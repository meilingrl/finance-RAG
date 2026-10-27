"""Download the pinned local encoder into an unqualified formal candidate bundle."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from formal_src.provisioning import provision


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/formal-v1')
    args = parser.parse_args(argv)
    output = args.output.resolve()
    from scripts.prepare_formal import ensure_candidate_mutable
    ensure_candidate_mutable(output)
    manifest_path = output / 'resources.json'
    resources = json.loads(manifest_path.read_text())
    model_manifest = json.loads((ROOT / 'configs/formal/minilm-files.json').read_text())
    model_root = output / 'models/all-MiniLM-L6-v2'
    report = provision(model_root, model_manifest)
    bindings = []
    for entry in model_manifest['files']:
        path = model_root / entry['file']
        bindings.append({'kind': 'model', 'path': path.relative_to(output).as_posix(),
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    existing = [r for r in resources['resources'] if r['kind'] != 'model']
    resources['resources'] = existing + bindings
    resources['model_status'] = 'files_verified_runtime_pending'
    temporary = manifest_path.with_suffix('.json.provisioning.tmp')
    temporary.write_text(json.dumps(resources, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(manifest_path)
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
