"""Single local entry: browser and CLI share ApplicationService."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

if __name__ == '__main__':
    try:
        import fcntl
    except ImportError:
        print('当前运行器需要 Unix 文件锁（fcntl）。原生 Windows 不受支持；请在 macOS、Linux 或 WSL 中安装 Python 3.12 后运行。',file=sys.stderr)
        raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [str(ROOT)] + [entry for entry in sys.path if entry != str(ROOT)]
from app.api import create_app
from app.service import ApplicationService
from formal_src.contracts import identifier
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from app.workspace import resolve_workspace


def build_app(project_root=ROOT, workspace=None):
    root = Path(project_root).resolve()
    service = ApplicationService(root, resolve_workspace(root, workspace))
    app = create_app(service)
    app.state.service = service

    @app.get('/')
    def index():
        return FileResponse(root / 'web/index.html')

    @app.get('/help')
    def help_page():
        return FileResponse(root / 'docs/guides/APP_GUIDE.md',media_type='text/plain; charset=utf-8')

    @app.get('/web/{name}')
    def static(name: str):
        if name not in {'app.js', 'state.js', 'styles.css'}:
            raise HTTPException(404)
        return FileResponse(root / 'web' / name)

    @app.get('/api/v1/demo-defaults')
    def demo_defaults():
        return {'legacy_root': str(root / 'data/processed/nonllm-pilot-v5'),
                'reviewed_root': str(root / 'data/fixed-course-v1/demo'),
                'formal_root': str(root / 'data/fixed-course-v1/formal'),
                'candidate_root': str(root / 'data/formal-v1'),
                'workspace': str(service.workspace)}

    def inputs(rid, filename):
        record = service.resources.record(rid)
        if not record or record['manifest']['purpose'] not in ('demo', 'formal'):
            raise HTTPException(403, 'Development demo resources only')
        base = Path(record['root']).resolve()
        matches = [v for v in record['manifest']['resources'] if v['kind'] == 'input' and Path(v['path']).name == filename]
        if len(matches) != 1:
            raise HTTPException(404)
        path = (base / matches[0]['path']).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise HTTPException(404)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != matches[0]['sha256']:
            raise HTTPException(409, 'Resource identity changed')
        return [json.loads(line) for line in data.decode().splitlines() if line.strip()]

    @app.get('/api/v1/resources/{rid}/examples')
    def examples(rid: str):
        fields = ('question_id', 'doc_id', 'question', 'split', 'source_report')
        return [{k: row[k] for k in fields if k in row} for row in inputs(rid, 'inference_questions.jsonl') if row.get('split') == 'development']

    @app.get('/api/v1/resources/{rid}/evidence/{doc_id}')
    def evidence(rid: str, doc_id: str):
        # A document is visible only if it has an allowlisted development question.
        identifier(doc_id)
        if doc_id not in {q['doc_id'] for q in examples(rid)}:
            raise HTTPException(403)
        fields = ('evidence_id', 'doc_id', 'text', 'page', 'page_number', 'region', 'bbox')
        return [{**{k: row[k] for k in fields if k in row},
                 **({'image_url': f'/api/v1/resources/{rid}/crop/{doc_id}/{row["evidence_id"]}'} if row.get('image_path') else {})}
                for row in inputs(rid, 'evidence_units.jsonl') if row.get('doc_id') == doc_id]

    @app.get('/api/v1/resources/{rid}/crop/{doc_id}/{evidence_id}')
    def crop(rid: str, doc_id: str, evidence_id: str):
        # Evidence IDs are manifest keys (including colons), never file paths.
        identifier(doc_id)
        if doc_id not in {q['doc_id'] for q in examples(rid)}:
            raise HTTPException(403, 'Development documents only')
        units = [r for r in inputs(rid, 'evidence_units.jsonl') if r['doc_id'] == doc_id and r['evidence_id'] == evidence_id]
        if len(units) != 1 or not units[0].get('image_path'):
            raise HTTPException(404)
        record = service.resources.record(rid)
        base = Path(record['root']).resolve()
        image = (base / units[0]['image_path']).resolve()
        bindings = [b for b in record['manifest']['resources'] if b['kind'] == 'input' and (base / b['path']).resolve() == image]
        if len(bindings) != 1 or not image.is_relative_to(base) or image.suffix.lower() not in ('.png', '.jpg', '.jpeg') or not image.is_file():
            raise HTTPException(404)
        if hashlib.sha256(image.read_bytes()).hexdigest() != bindings[0]['sha256']:
            raise HTTPException(409, 'Resource identity changed')
        return FileResponse(image)

    def sealed(directory):
        from formal_src.verification import check_seal, VerificationBlocked
        try:
            check_seal(directory)
        except (VerificationBlocked, OSError, ValueError, KeyError, TypeError):
            raise HTTPException(409, 'Sealed output identity changed or unavailable')

    @app.get('/api/v1/runs/{rid}/evidence')
    def run_evidence(rid: str):
        directory = service.jobs.directory(rid)
        sealed(directory)
        spec = json.loads((directory / 'spec.json').read_text())
        if spec['kind'] == 'verify':
            spec = json.loads((service.jobs.directory(spec['source_run_id']) / 'spec.json').read_text())
        if spec['kind'] != 'demo':
            raise HTTPException(403, 'Development demos only')
        return evidence(spec['resource_set_id'], spec['demo_input']['doc_id'])

    @app.get('/api/v1/runs/{rid}/origin')
    def origin(rid: str):
        spec = json.loads((service.jobs.directory(rid) / 'spec.json').read_text())
        return {key: spec[key] for key in ('profile_id', 'resource_set_id', 'kind')}

    @app.get('/api/v1/runs/{rid}/files/{name}')
    def files(rid: str, name: str):
        if name not in {'predictions.jsonl', 'retrieval.jsonl', 'summary.json', 'results.json', 'results.csv', 'scored-answers.jsonl', 'audit-status.json'}:
            raise HTTPException(403)
        directory = service.jobs.directory(rid)
        sealed(directory)
        if name != 'predictions.jsonl':
            from formal_src.verification import check_result_proof, VerificationBlocked
            try:
                proof=check_result_proof(directory, required=True)
                if name not in proof['files']:
                    raise VerificationBlocked('derived_file_not_bound')
            except (VerificationBlocked, OSError, ValueError, KeyError, TypeError):
                raise HTTPException(409, 'Derived results need an intact verification proof')
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404)
        return Response(path.read_bytes(), media_type='application/json' if name.endswith('.json') else 'text/plain; charset=utf-8')

    return app


def parser():
    result = argparse.ArgumentParser(description='Financial RAG local course application')
    result.add_argument('--workspace', type=Path)
    sub = result.add_subparsers(dest='command')
    serve = sub.add_parser('serve')
    serve.add_argument('--port', type=int, default=8765)
    serve.add_argument('--no-browser', action='store_true', help='Do not open the system browser')
    execute = sub.add_parser('execute')
    execute.add_argument('--request', required=True, type=Path)
    verify = sub.add_parser('verify')
    verify.add_argument('--source-run', required=True)
    verify.add_argument('--profile-id')
    verify.add_argument('--resource-set-id')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command in (None, 'serve'):
        import uvicorn
        port=getattr(args,'port',8765)
        url=f'http://127.0.0.1:{port}'
        print(f'Financial RAG: {url}',flush=True)
        if not getattr(args,'no_browser',False):
            import threading,webbrowser,urllib.request
            def open_when_ready():
                for _ in range(40):
                    try:
                        with urllib.request.urlopen(url+'/api/v1/capabilities',timeout=.5) as response:
                            if response.status==200:
                                webbrowser.open(url);return
                    except OSError:time.sleep(.25)
            threading.Thread(target=open_when_ready,daemon=True).start()
        application=build_app(ROOT,args.workspace)
        if (ROOT/'configs/fixed_dataset.json').is_file():application.state.service.begin_fixed_data_setup()
        uvicorn.run(application,host='127.0.0.1',port=port)
        return 0
    service = ApplicationService(ROOT, resolve_workspace(ROOT,args.workspace))
    if args.command == 'execute':
        request = json.loads(args.request.read_text())
    else:
        source = json.loads((service.jobs.directory(args.source_run) / 'spec.json').read_text())
        request = dict(schema_version=1, client_request_id=str(uuid4()), kind='verify', profile_id=args.profile_id or source['profile_id'], resource_set_id=args.resource_set_id or source['resource_set_id'], source_run_id=args.source_run, demo_input=None)
    key = os.environ.get('FORMAL_API_KEY') if args.command == 'execute' else None
    ref = service.set_credential(key) if key else None
    state = service.start_run(request, ref)
    while state['state'] in {'queued', 'validating', 'running', 'pause_requested', 'cancel_requested'}:
        time.sleep(.1)
        state = service.get_run(state['run_id'])
    print(json.dumps(state, ensure_ascii=False))
    return 0 if state['state'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
