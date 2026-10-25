"""Seal question-blind reviewed E3/E4 additions to existing frozen E2 jobs.

Never opens gold or model predictions. The unchanged execution runner handles
the API, shared 200-attempt ledger, model identity and response validation.
"""
from __future__ import annotations
import argparse
import base64
import copy
import hashlib
import json
import struct
from pathlib import Path

from llm_execution import load_job
from ragpilot.common import digest, file_sha, now, read_jsonl, unique, write_json, write_jsonl
from ragpilot.evidence import descriptions

RESERVATION = 131072
IMAGE_ALLOWANCE = 32768  # Operational allowance, not a provider-specific tokenizer proof.
COHORT = 'phase4-20261023-v1'


def augment_request(request, units, reviews, group):
    if group not in ('E3', 'E4') or request['group'] != 'E2':
        raise ValueError('Only frozen E2 -> E3/E4 augmentation is supported')
    payload = request['payload']
    if digest(payload) != request['request_hash']:
        raise ValueError('Base request hash changed')
    if ('messages' in payload or len(payload.get('input', [])) != 1
            or payload['input'][0].get('role') != 'user'
            or not isinstance(payload['input'][0].get('content'), str)):
        raise ValueError('Only the original text Responses payload is supported')
    shared = payload['input'][0]['content']
    if digest(shared) != request['shared_text_hash']:
        raise ValueError('Base shared text hash changed')
    blocked = []
    for eid in request['selected_ids']:
        unit, review = units[eid], reviews.get(eid, {})
        if (review.get('verified') is not True or review.get('content_hash') != unit['content_hash']
                or any(review.get(k) is not False for k in ('question_exposure', 'answer_gold_exposure',
                                                           'evaluation_metrics_exposure'))):
            blocked.append({'evidence_id': eid, 'reason': 'missing_failed_stale_or_exposed_review'})
        elif group == 'E3' and (review.get('image_sha256') != unit.get('image_sha256')
                                or review.get('status') != 'pass'):
            blocked.append({'evidence_id': eid, 'reason': 'image_scope_not_approved'})
        elif group == 'E4' and (not isinstance(review.get('text'), str) or not review['text'].strip()):
            blocked.append({'evidence_id': eid, 'reason': 'missing_description'})
    if blocked:
        return None, blocked
    row = copy.deepcopy(request)
    row['group'] = group
    parts = [{'type': 'input_text', 'text': shared}]
    image_refs, description_refs, image_hashes, review_hashes = [], [], {}, {}
    image_count = 0
    for eid in request['selected_ids']:
        unit, review = units[eid], reviews[eid]
        review_hashes[eid] = digest(review)
        if group == 'E3':
            path = Path(unit['image_path'])
            blob = path.read_bytes()
            if hashlib.sha256(blob).hexdigest() != unit['image_sha256']:
                raise ValueError('Image changed after blind review')
            if len(blob) < 24 or blob[:8] != b'\x89PNG\r\n\x1a\n' or blob[12:16] != b'IHDR':
                raise ValueError('Only the original PNG crops are supported')
            width, height = struct.unpack('>II', blob[16:24])
            if not 0 < width <= 2048 or not 0 < height <= 2048:
                raise ValueError('Image dimensions outside reviewed preflight; never silently resize')
            image_refs.append(str(path)); image_hashes[str(path)] = unit['image_sha256']
            parts.extend([{'type': 'input_text', 'text': f'Evidence image: {eid}'},
                          {'type': 'input_image', 'detail': 'high',
                           'image_url': 'data:image/png;base64,' + base64.b64encode(blob).decode('ascii')}])
            image_count += 1
        else:
            description_refs.append(eid)
            parts.append({'type': 'input_text', 'text': f'Structural description for {eid}:\n{review["text"]}'})
    row['payload']['input'][0]['content'] = parts
    # Ignore base64 wire size when allowing image tokens. Check actual usage in the runner.
    text_payload = copy.deepcopy(row['payload'])
    text_payload['input'][0]['content'] = [p for p in parts if p['type'] == 'input_text']
    text_bound = len(json.dumps(text_payload, ensure_ascii=False).encode()) + 2048
    allowance = text_bound + image_count * IMAGE_ALLOWANCE
    if allowance > RESERVATION:
        raise ValueError('Input exceeds operational token allowance; no silent truncation')
    if len(json.dumps(row['payload']).encode()) > 20_000_000:
        raise ValueError('Payload too large')
    row.update(request_hash=digest(row['payload']), input_token_byte_bound=text_bound,
               input_preflight={'text_byte_bound': text_bound, 'image_allowance': image_count * IMAGE_ALLOWANCE,
                                'total_allowance': allowance, 'image_tokenizer_verified': False},
               image_refs=image_refs, image_sha256=image_hashes, description_refs=description_refs,
               blind_review_hashes=review_hashes)
    return row, []


def prepare_multimodal_job(base_folder, review_path, output, group):
    if group not in ('E2', 'E3', 'E4'):
        raise ValueError('Unsupported group')
    base_folder, out = map(lambda p: Path(p).resolve(), (base_folder, output))
    review_path = Path(review_path).resolve() if review_path is not None else None
    if out.exists():
        raise ValueError('Choose a new output directory')
    base, requests = load_job(base_folder)
    if base['group'] != 'E2' or base['config'].get('wire_api') != 'responses':
        raise ValueError('Base must be the frozen E2 Responses job')
    if out.is_relative_to(Path(base['source_run'])) or out.is_relative_to(Path(base['data_dir'])):
        raise ValueError('Output must be outside frozen inputs')
    unit_rows = read_jsonl(Path(base['source_run']) / 'evidence_units.jsonl')
    units = {u['evidence_id']: u for u in unit_rows}
    if group != 'E2' and review_path is None:
        raise ValueError('E3/E4 requires blind review')
    review_rows = read_jsonl(review_path) if group != 'E2' else []
    unique(review_rows, 'evidence_id')
    reviews = {r['evidence_id']: r for r in review_rows}
    description_path = Path(base['source_run']) / 'descriptions.jsonl'
    if group == 'E4':
        originals = {d['evidence_id']: d for d in read_jsonl(description_path)}
        canonical = {d['evidence_id']: d for d in descriptions(unit_rows)}
        for eid in {e for r in requests for e in r['selected_ids']}:
            review = reviews.get(eid)
            if review and review.get('verified') is True:
                original = originals[eid]['text']
                if (original != canonical[eid]['text']
                        or originals[eid]['evidence_content_hash'] != units[eid]['content_hash']
                        or originals[eid]['source_kind'] != canonical[eid]['source_kind']
                        or review.get('original_text') != original
                        or review.get('original_text_hash') != hashlib.sha256(original.encode()).hexdigest()
                        or review.get('source_kind') != 'coordinate_template_v1_anchored_legend'):
                    raise ValueError('Description is not bound to the original frozen template')
    augmented, excluded = [], []
    for request in requests:
        row, reasons = ((copy.deepcopy(request), []) if group == 'E2'
                        else augment_request(request, units, reviews, group))
        if row is None:
            excluded.append({'question_id': request['question_id'], 'group': group,
                             'diagnostic_partition': request['diagnostic_partition'], 'reasons': reasons})
        else:
            # Identifies this predeclared contemporary replicate in the durable
            # ledger without changing any model-visible instructions or evidence.
            if 'metadata' in row['payload']:
                raise ValueError('Base payload already has cohort metadata')
            row['payload']['metadata'] = {'experiment_cohort': COHORT}
            row['request_hash'] = digest(row['payload'])
            row['experiment_cohort'] = COHORT
            augmented.append(row)
    if not augmented:
        raise ValueError('No fully reviewed eligible questions')
    identity_files = [base_folder / 'job.json', base_folder / 'requests.jsonl', Path(__file__).resolve()]
    if group != 'E2':
        identity_files.append(review_path)
    if group == 'E4':
        identity_files.append(description_path)
    job = copy.deepcopy(base)
    job.pop('job_hash')
    job.update(created_at=now(), group=group, question_count=len(augmented),
               original_question_count=len(requests), base_job=str(base_folder), base_job_hash=base['job_hash'],
               review_path=str(review_path) if group != 'E2' else None,
               experiment_cohort=COHORT, exclusion_count=len(excluded), exclusion_hash=digest(excluded),
               request_list_hash=digest(augmented), multimodal_builder_sha256=file_sha(__file__),
               limitations=['Same frozen retrieval, shared text, model, instructions and answer schema as E2',
                            'Image/description approval is question-blind, not proof of semantic support',
                            'Excluded cases are reported; comparisons must use matched denominators',
                            'Whole-page diagnostics are separate from local-region evidence',
                            'E2 is a predeclared contemporary replicate, not a replacement of historical E2',
                            'Image allowance is operational; actual provider usage is checked after each call'])
    job['config']['reserve_input_tokens'] = RESERVATION
    job['source_files'].update({str(p): file_sha(p) for p in identity_files})
    out.mkdir(parents=True)
    write_jsonl(out / 'requests.jsonl', augmented)
    write_jsonl(out / 'exclusions.jsonl', excluded)
    job['source_files'][str(out / 'exclusions.jsonl')] = file_sha(out / 'exclusions.jsonl')
    job['job_hash'] = digest(job)
    write_json(out / 'job.json', job)
    load_job(out)
    return {k: job[k] for k in ('group', 'split', 'question_count', 'exclusion_count', 'job_hash')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-job', required=True)
    parser.add_argument('--review')
    parser.add_argument('--output', required=True)
    parser.add_argument('--group', choices=('E2', 'E3', 'E4'), required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_multimodal_job(args.base_job, args.review, args.output, args.group), indent=2))


if __name__ == '__main__':
    main()
