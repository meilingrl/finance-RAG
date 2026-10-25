"""Offline verification: bind sealed inference bytes before opening evaluation assets.

This module deliberately imports neither the provider nor a credential reader. It
also runs as a separate process when invoked by the generation worker.
"""
from __future__ import annotations
import argparse
import csv
import io
from collections import Counter
import hashlib
import json
from pathlib import Path
from uuid import UUID

from .contracts import AnswerRecord, RunResults
from .evaluation import evaluate_predictions
from .statistics import main_comparisons


class VerificationBlocked(ValueError):
    pass


def _json(path):
    return json.loads(path.read_text())


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic(path, value):
    temporary = path.with_suffix(path.suffix + '.verification.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def check_result_proof(run_dir, *, required=False):
    rd = Path(run_dir)
    path = rd / 'result-proof.json'
    if not path.is_file():
        if required:
            raise VerificationBlocked('verified_result_proof_missing')
        return None
    proof = _json(path)
    if proof.get('schema_version') != 1 or not isinstance(proof.get('files'), dict):
        raise VerificationBlocked('result_proof_invalid')
    if not {'summary.json', 'results.json'}.issubset(proof['files']):
        raise VerificationBlocked('result_proof_invalid')
    for name, expected in proof['files'].items():
        if name not in {'summary.json','results.json','results.csv','retrieval.jsonl','scored-answers.jsonl','audit-status.json'} or not (rd/name).is_file() or _sha(rd/name) != expected:
            raise VerificationBlocked('result_seal_mismatch')
    if proof.get('prediction_sha256') != _sha(rd/'predictions.jsonl') or proof.get('seal_sha256') != _sha(rd/'sealed.json'):
        raise VerificationBlocked('result_source_mismatch')
    if proof.get('request_spec_sha256') != _digest(_json(rd/'spec.json')):
        raise VerificationBlocked('result_request_identity_mismatch')
    summary = _json(rd/'summary.json')
    if proof.get('evaluation_sha') != summary.get('evaluation_sha'):
        raise VerificationBlocked('result_evaluation_mismatch')
    return proof


def validate_audit_plan(plan, tasks, expected_sha):
    # The approved audit algorithm accepts arbitrary populations; this adapter
    # enforces the preregistered formal cohort and canonical review population.
    if _digest(plan) != expected_sha:
        raise VerificationBlocked('audit_plan_identity_mismatch')
    main = set(plan.get('main_question_ids', [])); diagnostic = set(plan.get('diagnostic_question_ids', []))
    if len(main) != 30 or len(diagnostic) != 15 or main & diagnostic:
        raise VerificationBlocked('audit_plan_population_invalid')
    known = {(t['partition'], t['question_id']) for t in tasks}
    if any(('main',q) not in known for q in main) or any(('diagnostic',q) not in known for q in diagnostic):
        raise VerificationBlocked('audit_cohort_identity_mismatch')
    expected = {(part,q,r,c,1) for part,ids,retrievers in [('main',main,['bm25','minilm']),('diagnostic',diagnostic,['bm25'])] for q in ids for r in retrievers for c in ['E2','E3','E4']}
    answers = plan.get('answer_items', [])
    actual = {(r['partition'],r['question_id'],r['retriever'],r['condition'],r['repeat']) for r in answers}
    relations = plan.get('relation_items', [])
    item_ids = {r['item_id'] for r in answers}
    second = plan.get('second_review_ids', [])
    if len(answers) != 225 or actual != expected or len(item_ids) != 225 or len(second) != 57 or len(set(second)) != 57 or not set(second) <= item_ids or len({r['evidence_id'] for r in relations}) != 30 or plan.get('blocking_items'):
        raise VerificationBlocked('audit_plan_population_invalid')
    cohort = sorted((t['partition'],t['question_id']) for t in tasks if t['partition'] in {'main','diagnostic'})
    # A repeated task matrix must bind the entire cohort identity, not just samples.
    if plan.get('cohort_sha256') != _digest(sorted(set(cohort))):
        raise VerificationBlocked('audit_cohort_identity_mismatch')
    return True


def check_seal(run_dir):
    """Check actual bytes and full task identity; safe for read-only API routes."""
    rd = Path(run_dir).resolve()
    if not (rd / 'sealed.json').is_file():
        raise VerificationBlocked('source_not_sealed')
    seal = _json(rd / 'sealed.json')
    for filename, field, code in [
        ('predictions.jsonl', 'prediction_sha256', 'prediction_seal_mismatch'),
        ('tasks.json', 'task_sha256', 'task_seal_mismatch'),
        ('inputs.json', 'input_sha256', 'input_seal_mismatch'),
    ]:
        if not (rd / filename).is_file() or seal.get(field) != _sha(rd / filename):
            raise VerificationBlocked(code)
    tasks, predictions = _json(rd / 'tasks.json'), _rows(rd / 'predictions.jsonl')
    task_index = {t['task_id']: t for t in tasks}
    received = {p['task_id'] for p in predictions}
    if (len(task_index) != len(tasks) or len(received) != len(predictions)
            or received != set(task_index) or seal.get('completed_tasks') != len(tasks)):
        raise VerificationBlocked('prediction_population_mismatch')
    for row in predictions:
        AnswerRecord.from_dict(row)
        task = task_index[row['task_id']]
        if any(row[k] != task[k] for k in ('question_id', 'retriever', 'condition', 'model_role', 'repeat')):
            raise VerificationBlocked('prediction_identity_mismatch')
    identity_path = rd / 'execution-identity.json'
    if identity_path.is_file():
        identity = _json(identity_path)
        if identity != {'spec_sha':_digest(_json(rd/'spec.json')), 'task_sha':_digest(tasks), 'input_sha':_sha(rd/'inputs.json')}:
            raise VerificationBlocked('execution_identity_mismatch')
    elif _json(rd/'spec.json').get('kind') != 'verify':
        raise VerificationBlocked('execution_identity_missing')
    check_result_proof(rd)
    return seal, tasks, predictions


def _evaluation_files(workspace, spec):
    record = _json(workspace / 'resources' / (spec['resource_set_id'] + '.json'))
    manifest_sha = hashlib.sha256(json.dumps(record['manifest'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if manifest_sha != spec['resource_sha']:
        raise VerificationBlocked('resource_manifest_identity_mismatch')
    root = Path(record['root']).resolve()
    files = {}
    bindings = []
    for binding in record['manifest']['resources']:
        if binding['kind'] not in ('evaluation', 'review'):
            continue
        path = (root / binding['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file() or _sha(path) != binding['sha256']:
            raise VerificationBlocked('evaluation_resource_changed')
        if path.name in files:
            raise VerificationBlocked('duplicate_evaluation_binding')
        files[path.name] = path
        bindings.append({k: binding[k] for k in ('kind', 'path', 'sha256')})
    post = record.get('audit_reviews')
    if post:
        path = (root/post['path']).resolve()
        if post.get('generation_manifest_sha256') != spec['resource_sha'] or path.name != 'audit_reviews.json' or not path.is_relative_to(root) or not path.is_file() or _sha(path) != post.get('sha256'):
            raise VerificationBlocked('audit_review_binding_changed')
        files['audit_reviews.json'] = path
        bindings.append({'kind':'post_generation_review','path':post['path'],'sha256':post['sha256']})
    digest = hashlib.sha256(json.dumps(sorted(bindings, key=lambda b: b['path']), sort_keys=True).encode()).hexdigest()
    return files, digest


def verify_run(run_dir):
    """Verify a sealed demo or score a complete experiment; never rerun inference."""
    rd = Path(run_dir).resolve()
    try:
        request_spec = _json(rd / 'spec.json')
        source = rd
        if request_spec['kind'] == 'verify':
            source_id = str(UUID(request_spec['source_run_id']))
            source = rd.parent / source_id
        seal, tasks, predictions = check_seal(source)
        spec = _json(source / 'spec.json')
        old = _json(source / 'summary.json') if (source / 'summary.json').exists() else {}
        engineering = dict(old.get('engineering', {}))
        engineering.update(purpose=spec['purpose'], synthetic=seal.get('synthetic') is True,
                           source_run_id=source.name, verification_kind='integrity_only',
                           evaluation_status='not_evaluated', cost_usd=None)
        parts = Counter(t['partition'] for t in tasks)
        summary = dict(schema_version=1, run_id=request_spec.get('run_id', rd.name),
                       input_sha=seal['input_sha256'], output_sha=seal['prediction_sha256'],
                       evaluation_sha='0' * 64, partitions=dict(parts),
                       denominators={'logical_generation_count': sum(t['work_type'] == 'generation' for t in tasks),
                                     'local_baseline_count': sum(t['work_type'] == 'local_baseline' for t in tasks)},
                       metrics={}, main_comparisons=[], engineering=engineering,
                       audit_complete=False, answer_tables=['predictions.jsonl'], exports=[])
        scored_rows = None
        audit = {'audit_complete':False,'status':'pending_human_review'}
        if spec['kind'] == 'experiment':
            files, evaluation_sha = _evaluation_files(source.parent.parent, spec)
            if 'gold.jsonl' not in files:
                raise VerificationBlocked('evaluation_gold_missing')
            gold = _rows(files['gold.jsonl'])
            task_index = {t['task_id']: t for t in tasks}
            merged = [{**task_index[p['task_id']], **p} for p in predictions]
            scored = evaluate_predictions(merged, gold, tasks, sealed=True)
            scored_rows = scored['rows']
            comparisons = main_comparisons(scored_rows)
            summary['answer_tables'] = ['scored-answers.jsonl','predictions.jsonl']
            summary['exports'] = ['results.csv','audit-status.json']
            summary.update(evaluation_sha=evaluation_sha, metrics=scored['metrics'],
                           main_comparisons=[dict(comparison_id=name, **value) for name, value in comparisons.items()])
            engineering.update(verification_kind='sealed_evaluation', evaluation_status='scored',
                               scoring_protocol=scored['evaluation_protocol'],
                               denominator=scored['denominator'])
            if 'audit_plan.json' in files and 'audit_reviews.json' in files:
                from .audit import audit_status
                plan = _json(files['audit_plan.json'])
                plan_sha = spec['resolved_config'].get('audit_plan_sha256')
                validate_audit_plan(plan, tasks, plan_sha)
                audit = audit_status(plan, _json(files['audit_reviews.json']))
                engineering['audit_provenance'] = {'plan_sha256':plan_sha, 'reviews_sha256':_sha(files['audit_reviews.json']), 'cohort_sha256':plan['cohort_sha256']}
                engineering['audit'] = audit
                summary['audit_complete'] = audit['audit_complete']
            else:
                engineering['audit_status'] = 'pending_human_review'
        # Unique physical attempt IDs avoid counting same-round answer reuse twice.
        attempts = _rows(source / 'attempts.jsonl') if (source / 'attempts.jsonl').exists() else []
        engineering['attempt_count'] = len({a['attempt_id'] for a in attempts})
        if source != rd:
            # Copy already sealed bytes only. The originating run remains untouched.
            for name in ('predictions.jsonl', 'tasks.json', 'inputs.json', 'sealed.json', 'retrieval.jsonl'):
                target = rd / name
                if not (source / name).is_file():continue
                data = (source / name).read_bytes()
                if target.exists() and target.read_bytes() != data:
                    raise VerificationBlocked('verification_output_changed')
                target.write_bytes(data)
        if scored_rows is not None:
            # Persist evaluator output verbatim; the application never opens gold.
            (rd/'scored-answers.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in scored_rows))
            columns = ['task_id','question_id','partition','retriever','condition','model_role','repeat','source_id','status','uem','em','f1','answer_only_em','answer_only_f1','numeric']
            buffer=io.StringIO();writer=csv.DictWriter(buffer,fieldnames=columns,extrasaction='ignore');writer.writeheader()
            writer.writerows({**row,'numeric':json.dumps(row['numeric'],ensure_ascii=False,sort_keys=True)} for row in scored_rows)
            (rd/'results.csv').write_text(buffer.getvalue())
        _atomic(rd/'audit-status.json',audit)
        if 'audit-status.json' not in summary['exports']:summary['exports'].append('audit-status.json')
        summary = RunResults.from_dict(summary).to_dict()
        _atomic(rd / 'summary.json', summary)
        _atomic(rd / 'results.json', summary)
        from .provenance import write_result_proof
        write_result_proof(rd,source_run_id=source.name,source_spec=spec)
        return {'state': 'completed', 'blocking_items': []}
    except VerificationBlocked as exc:
        return {'state': 'blocked', 'blocking_items': [str(exc)]}
    except (OSError, ValueError, KeyError, TypeError):
        return {'state': 'blocked', 'blocking_items': ['verification_inputs_invalid']}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', required=True)
    result = verify_run(Path(parser.parse_args(argv).run_dir))
    return 0 if result['state'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
