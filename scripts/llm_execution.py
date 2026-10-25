"""E2 API execution alongside, never inside, the frozen non-LLM implementation.

Preparation and execution never open gold. Scoring is a separate command.
The shared ledger reserves the full conservative cost BEFORE every network call;
reservations are not refunded, including timeouts and interrupted processes.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ragpilot.common import digest, file_sha, now, read_json, read_jsonl, write_json, write_jsonl
from ragpilot.inference import SYSTEM, common_text, selected_units
from ragpilot.metrics import evaluate
from ragpilot.pipeline import source_fingerprint

ANSWER_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {'answer': {'type': ['string', 'null']},
                   'scale': {'type': 'string', 'enum': ['', 'thousand', 'million', 'billion', 'percent']},
                   'citations': {'type': 'array', 'items': {'type': 'string'}},
                   'abstained': {'type': 'boolean'}},
    'required': ['answer', 'scale', 'citations', 'abstained'],
}
SPAN_GUIDANCE = (
    '\nAnswer format: copy the minimal extractive answer span from the supplied evidence, '
    'without a full sentence, explanatory phrase, or expanded title. Preserve the original '
    'numeric form and currency/percent signs; do not convert or calculate values. '
    'For a numeric answer omit contextual units such as years or shares from the answer. '
    'For a section identifier return the identifier, not its full heading. '
    'The scale field is only a magnitude: empty string, thousand, million, billion, or percent; '
    'use the empty string for ordinary years, dates, counts, section identifiers and text answers. '
    'Never put an explanation or "not applicable" in scale. Cite the supplied evidence IDs.'
)
CONFIG_KEYS = {'endpoint', 'model', 'api_key_env', 'max_completion_tokens', 'reasoning_effort',
               'reserve_input_tokens', 'input_usd_per_million', 'output_usd_per_million', 'timeout_seconds'}


def validate_config(config):
    if set(config) - {'wire_api', 'stream'} != CONFIG_KEYS:
        raise ValueError('Configuration must contain exactly the documented public fields; no credentials')
    url = urllib.parse.urlsplit(config['endpoint'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('API endpoint must be HTTPS without credentials, query or fragment')
    wire = config.get('wire_api', 'chat_completions')
    if wire not in ('chat_completions', 'responses') or not url.path.endswith('/' + ('responses' if wire == 'responses' else 'chat/completions')):
        raise ValueError('Endpoint path must match the configured API protocol')
    if type(config.get('stream', False)) is not bool or (config.get('stream') and wire != 'responses'):
        raise ValueError('Streaming is supported only for Responses')
    for name in ('model', 'api_key_env'):
        if not isinstance(config[name], str) or not re.fullmatch(r'[A-Za-z0-9_.:-]+', config[name]):
            raise ValueError('Model and key environment variable must be public identifiers')
    if config['reasoning_effort'] not in ('none', 'low', 'medium', 'high'):
        raise ValueError('Unsupported reasoning effort')
    for name in ('max_completion_tokens', 'reserve_input_tokens', 'timeout_seconds'):
        if type(config[name]) is not int or config[name] <= 0:
            raise ValueError('Token limits and timeout must be positive integers')
    if config['reserve_input_tokens'] > 272000:
        raise ValueError('This budget calculation supports short-context pricing only')
    prices = [config[name] for name in ('input_usd_per_million', 'output_usd_per_million')]
    if prices == [None, None]:
        return  # Third-party billing is unknown; never substitute official prices.
    for name in ('input_usd_per_million', 'output_usd_per_million'):
        value = config[name]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('Explicit positive token prices are required')


def validate_source(source, data):
    source, data = Path(source), Path(data)
    run = read_json(source / 'run.json')
    manifest = read_json(data / 'data_manifest.json')
    units = read_jsonl(source / 'evidence_units.jsonl')
    retrieval = read_jsonl(source / 'retrieval.jsonl')
    baseline = read_jsonl(source / 'predictions.jsonl')
    questions = read_jsonl(data / 'inference_questions.jsonl')
    if run['status'] != 'offline_complete' or run['source_fingerprint'] != source_fingerprint():
        raise ValueError('Frozen non-LLM source or completed run changed')
    if digest(manifest) != run['input_manifest_hash'] or read_json(source / 'data_manifest.json') != manifest:
        raise ValueError('Frozen data manifest changed')
    if digest(questions) != manifest['inference_hash'] or digest(units) != manifest['evidence_hash']:
        raise ValueError('Frozen inference questions or evidence changed')
    if digest(read_jsonl(data / 'evidence_units.jsonl')) != manifest['evidence_hash']:
        raise ValueError('Processed evidence differs from frozen run')
    if digest(retrieval) != run['retrieval_hash']:
        raise ValueError('Frozen retrieval changed')
    if digest(baseline) != run['prediction_hash']:
        raise ValueError('Frozen baseline predictions changed')
    questions = [q for q in questions if q['split'] == run['selected_split']]
    if len(questions) != run['question_count'] or {q['question_id'] for q in questions} != {r['question_id'] for r in retrieval}:
        raise ValueError('Question population differs from frozen retrieval')
    return run, manifest, questions, units, retrieval, baseline


def prepare_job(source_run, data_dir, output, config, limit=None):
    """Reuse the old E2 shared text; produce a new, sealed job without any API call."""
    validate_config(config)
    source, data, out = Path(source_run).resolve(), Path(data_dir).resolve(), Path(output).resolve()
    if out.exists() or out.is_relative_to(source) or out.is_relative_to(data):
        raise ValueError('Choose a new output directory outside frozen inputs and runs')
    run, manifest, questions, units, retrieval, baseline = validate_source(source, data)
    if limit is not None:
        if run['selected_split'] != 'development' or type(limit) is not int or not 1 <= limit <= len(questions):
            raise ValueError('Only development smoke tests may limit questions; evaluation keeps the full sample')
        questions = questions[:limit]
    prepared = read_jsonl(source / 'prepared_requests.jsonl')
    prepared = {r['question_id']: r for r in prepared if r['group'] == 'E2'}
    found = {r['question_id']: r for r in retrieval}
    requests = []
    for q in questions:
        frozen = found[q['question_id']]
        chosen = selected_units(q, units, frozen)
        old = prepared[q['question_id']]
        shared = common_text(q, chosen)
        if (digest(old['request_payload']) != old['request_hash'] or old['shared_text_hash'] != digest(shared)
                or old['request_payload']['shared_text'] != shared
                or old['request_payload']['system'] != SYSTEM
                or old['selected_ids'] != frozen['selected_ids']
                or old['retrieval_freeze_hash'] != frozen['freeze_hash']):
            raise ValueError('Original E2 request differs from frozen evidence or prompt')
        system = SYSTEM + SPAN_GUIDANCE
        payload = {
            'model': config['model'], 'store': False,
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': shared}],
            'max_completion_tokens': config['max_completion_tokens'],
            'reasoning_effort': config['reasoning_effort'],
            'response_format': {'type': 'json_schema', 'json_schema': {
                'name': 'evidence_answer', 'strict': True, 'schema': ANSWER_SCHEMA}},
        }
        if config.get('wire_api') == 'responses':
            payload = {'model': config['model'], 'store': False, 'stream': config.get('stream', False),
                       'instructions': system, 'input': [{'role': 'user', 'content': shared}],
                       'max_output_tokens': config['max_completion_tokens'],
                       'reasoning': {'effort': config['reasoning_effort']},
                       'text': {'format': {'type': 'json_schema', 'name': 'evidence_answer',
                                           'strict': True, 'schema': ANSWER_SCHEMA}}}
        # Text-only byte bound plus generous framing/schema allowance, not a tokenizer estimate.
        byte_bound = len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) + 2048
        if byte_bound > config['reserve_input_tokens']:
            raise ValueError('Input exceeds conservative token reservation; no silent truncation')
        requests.append({'question_id': q['question_id'], 'doc_id': q['doc_id'], 'group': 'E2',
                         'selected_ids': frozen['selected_ids'], 'retrieval_freeze_hash': frozen['freeze_hash'],
                         'shared_text_hash': digest(shared), 'diagnostic_partition': old['diagnostic_partition'],
                         'payload': payload, 'request_hash': digest(payload), 'input_token_byte_bound': byte_bound})
    identity_files = [source / name for name in ('run.json', 'data_manifest.json', 'evidence_units.jsonl',
                      'retrieval.jsonl', 'predictions.jsonl', 'prepared_requests.jsonl')]
    identity_files += [data / name for name in ('data_manifest.json', 'inference_questions.jsonl', 'evidence_units.jsonl')]
    job = {'schema_version': 1, 'created_at': now(), 'group': 'E2', 'source_run': str(source), 'data_dir': str(data),
           'split': run['selected_split'], 'question_count': len(questions), 'config': config,
           'request_list_hash': digest(requests), 'runner_sha256': file_sha(__file__),
           'source_files': {str(p): file_sha(p) for p in identity_files},
           'source_dataset': manifest['dataset'], 'model_calls_at_preparation': 0,
           'limitations': ['Document-internal retrieval; same frozen E1 baseline',
                           'E3 image scope and E4 description verification are still blocked',
                           'No model quality claim before real API execution and separate scoring']}
    job['job_hash'] = digest(job)
    out.mkdir(parents=True)
    write_jsonl(out / 'requests.jsonl', requests)
    write_json(out / 'job.json', job)
    return job


def load_job(folder):
    folder = Path(folder)
    job = read_json(folder / 'job.json')
    if job['job_hash'] != digest({k: v for k, v in job.items() if k != 'job_hash'}):
        raise ValueError('Job manifest changed after preparation')
    validate_config(job['config'])
    if file_sha(__file__) != job['runner_sha256']:
        raise ValueError('Runner changed; prepare a new job before execution')
    for path, expected in job['source_files'].items():
        if file_sha(path) != expected:
            raise ValueError('Source artifacts changed after preparation')
    validate_source(job['source_run'], job['data_dir'])
    requests = read_jsonl(folder / 'requests.jsonl')
    if digest(requests) != job['request_list_hash']:
        raise ValueError('Prepared requests changed after preparation')
    # Recheck selected image bytes through the frozen source validator on each invocation.
    _, _, questions, units, retrieval, _ = validate_source(job['source_run'], job['data_dir'])
    by_id = {q['question_id']: q for q in questions}
    for r in retrieval:
        selected_units(by_id[r['question_id']], units, r)
    return job, requests


def parse_response(raw, selected_ids):
    result = {'status': 'parse_error', 'parsed_answer': None, 'scale': None,
              'citations': [], 'truncated': False, 'error': 'invalid_response_schema'}
    try:
        if 'output' in raw:
            if raw.get('status') == 'incomplete' and (raw.get('incomplete_details') or {}).get('reason') == 'max_output_tokens':
                return {**result, 'status': 'truncated', 'truncated': True, 'error': 'output_token_limit'}
            if raw.get('status') != 'completed':
                return {**result, 'status': 'api_error', 'error': 'response_not_completed'}
            parts = [c for item in raw['output'] if item.get('type') == 'message' and item.get('role') == 'assistant'
                     for c in item.get('content', [])]
            if any(c.get('type') == 'refusal' for c in parts):
                return {**result, 'status': 'rejection', 'error': None}
            text = ''.join(c['text'] for c in parts if c.get('type') == 'output_text')
            raw = {'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]}
        if len(raw['choices']) != 1:
            return result
        choice = raw['choices'][0]
        if choice.get('finish_reason') == 'length':
            return {**result, 'status': 'truncated', 'truncated': True, 'error': 'output_token_limit'}
        if choice['message'].get('refusal') or choice.get('finish_reason') == 'content_filter':
            return {**result, 'status': 'rejection', 'error': None}
        if choice.get('finish_reason') != 'stop':
            return result
        value = json.loads(choice['message']['content'])
        if not isinstance(value, dict) or set(value) != {'answer', 'scale', 'citations', 'abstained'}:
            return result
        if (type(value['abstained']) is not bool or not isinstance(value['scale'], str)
                or value['scale'] not in ('', 'thousand', 'million', 'billion', 'percent')
                or not isinstance(value['citations'], list)
                or not all(isinstance(c, str) and c in selected_ids for c in value['citations'])):
            return result
        if value['abstained']:
            if value['answer'] is not None or value['citations'] or value['scale']:
                return result
            return {**result, 'status': 'abstained', 'scale': '', 'error': None}
        if not isinstance(value['answer'], str) or not value['answer'].strip() or not value['citations']:
            return result
        return {**result, 'status': 'answered', 'parsed_answer': value['answer'], 'scale': value['scale'],
                'citations': value['citations'], 'error': None}
    except (KeyError, TypeError, ValueError, IndexError):
        return result


class APIError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise APIError('redirect_rejected')


def post_json(endpoint, payload, key, timeout):
    url = urllib.parse.urlsplit(endpoint)
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname == '127.0.0.1'):
        raise APIError('insecure_transport_rejected')
    request = urllib.request.Request(endpoint, json.dumps(payload, allow_nan=False).encode('utf-8'),
                                     {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as reply:
            if 'text/event-stream' in reply.headers.get('Content-Type', ''):
                size, frame, pending = 0, [], b''
                deadline = time.monotonic() + timeout
                while True:
                    chunk = reply.read1(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > 2_000_000 or time.monotonic() > deadline:
                        raise APIError('stream_size_or_time_limit')
                    pending += chunk
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        text = line.decode('utf-8').rstrip('\r')
                        if text.startswith('data:'):
                            frame.append(text[5:].lstrip())
                        elif not text and frame:
                            content = '\n'.join(frame).replace(key, '[REDACTED]'); frame = []
                            if content == '[DONE]':
                                raise APIError('stream_ended_without_final_response')
                            event = json.loads(content)
                            if event.get('type') in ('response.completed', 'response.failed', 'response.incomplete'):
                                return event['response']
                            if event.get('type') == 'error':
                                raise APIError('response_stream_error')
                raise APIError('stream_ended_without_final_response')
            body = reply.read(2_000_001)
        if len(body) > 2_000_000:
            raise APIError('response_too_large')
        # Do not retain credentials even if a provider reflects one in a response.
        raw = body.decode('utf-8').replace(key, '[REDACTED]')
        return json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except urllib.error.HTTPError as exc:
        # Error bodies, headers, URLs and exception strings are deliberately not logged.
        raise APIError('http_' + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise APIError('transport_' + type(exc).__name__) from None


def append_durable(path, record):
    with Path(path).open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def reserve_call(ledger, job, request, max_cost, max_calls):
    ledger = Path(ledger)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    priced = job['config']['input_usd_per_million'] is not None
    cost = ((Decimal(str(job['config']['reserve_input_tokens'])) * Decimal(str(job['config']['input_usd_per_million']))
            + Decimal(str(job['config']['max_completion_tokens'])) * Decimal(str(job['config']['output_usd_per_million']))) / 1_000_000
            if priced else None)
    request_key = digest([job['config']['endpoint'], request['question_id'], request['request_hash']])
    with ledger.open('a+', encoding='utf-8') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        records = [json.loads(line) for line in stream if line.strip()]
        if any(r['request_key'] == request_key for r in records):
            return 'previous_attempt', cost
        if max_cost is not None and any(r['reserved_usd'] is None for r in records):
            raise ValueError('Prior provider price is unknown; cannot enforce a USD limit')
        spent = sum((Decimal(str(r['reserved_usd'] or 0)) for r in records), Decimal(0))
        if len(records) >= max_calls or (max_cost is not None and spent + cost > Decimal(str(max_cost))):
            return 'budget_stopped', cost
        record = {'time': now(), 'job_hash': job['job_hash'], 'request_key': request_key,
                  'question_id': request['question_id'], 'reserved_usd': float(cost) if cost is not None else None,
                  'accounting': 'conservative_reservation_not_refunded' if priced else 'unpriced_provider_call_count_only',
                  'model': job['config']['model'], 'endpoint': job['config']['endpoint']}
        stream.seek(0, 2)
        stream.write(json.dumps(record) + '\n'); stream.flush(); os.fsync(stream.fileno())
    return 'reserved', cost


def execute_job(folder, api_key, ledger, max_cost_usd, max_calls=200, transport=post_json,
                resume_after_inspection=False):
    if not isinstance(api_key, str) or not api_key.strip() or '\n' in api_key or '\r' in api_key:
        raise ValueError('A local API key is required; do not put it in configuration or chat')
    if type(max_calls) is not int or not 1 <= max_calls <= 200:
        raise ValueError('Call budget must be between 1 and 200')
    if max_cost_usd is not None and (type(max_cost_usd) not in (int, float) or not math.isfinite(max_cost_usd) or max_cost_usd <= 0):
        raise ValueError('An explicitly authorized positive USD budget is required')
    folder = Path(folder)
    job, requests = load_job(folder)
    if max_cost_usd is not None and job['config']['input_usd_per_million'] is None:
        raise ValueError('Provider price is unknown; a USD cap would be misleading')
    with (folder / '.execution.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        destination = folder / 'predictions.jsonl'
        predictions = read_jsonl(destination) if destination.exists() else []
        completed = {r['question_id'] for r in predictions}
        if len(completed) != len(predictions) or not completed <= {r['question_id'] for r in requests}:
            raise ValueError('Duplicate or unknown saved predictions')
        expected = {r['question_id']: r for r in requests}
        for record in predictions:
            if (record.get('record_hash') != digest({k: v for k, v in record.items() if k != 'record_hash'})
                    or record.get('request_hash') != expected[record['question_id']]['request_hash']):
                raise ValueError('Saved prediction changed before resume')
        if (len(predictions) < len(requests) and not resume_after_inspection
                and any(r['status'] in ('api_error', 'error') for r in predictions)):
            raise ValueError('Previous API, model or usage error requires inspection before sending more requests')
        stopped = False
        for request in requests:
            if request['question_id'] in completed:
                continue
            reservation, cost = reserve_call(ledger, job, request, max_cost_usd, max_calls)
            if reservation == 'budget_stopped':
                stopped = True; break
            record = {k: v for k, v in request.items() if k not in ('payload', 'input_token_byte_bound')}
            record.update(model_id=job['config']['model'], model_version_or_reported_name=None,
                          raw_response=None, usage=None, retry_count=0, elapsed_seconds=None,
                          api_response=None, status='api_error', parsed_answer=None, scale=None,
                          citations=[], truncated=False, error='previous_attempt_outcome_unknown_not_resent',
                          cost_estimate={'amount': float(cost) if cost is not None else None,
                                         'currency': 'USD' if cost is not None else None,
                                         'basis': 'conservative_reservation' if cost is not None else 'unknown_provider_pricing'},
                          eligible_main_comparison=False)
            if reservation == 'previous_attempt':
                stopped = True
            if reservation == 'reserved':
                started = time.monotonic()
                try:
                    raw = transport(job['config']['endpoint'], request['payload'], api_key, job['config']['timeout_seconds'])
                    raw = json.loads(json.dumps(raw, allow_nan=False).replace(api_key, '[REDACTED]'))
                    record.update(parse_response(raw, request['selected_ids']))
                    if record['status'] == 'api_error':
                        stopped = True
                    raw_text = (''.join(c.get('text', '') for item in raw.get('output', []) if item.get('type') == 'message'
                                       for c in item.get('content', []) if c.get('type') == 'output_text')
                                if 'output' in raw else raw.get('choices', [{}])[0].get('message', {}).get('content'))
                    record.update(api_response=raw, model_version_or_reported_name=raw.get('model'),
                                  usage=raw.get('usage'), raw_response=raw_text)
                    if raw.get('model') != job['config']['model']:
                        record.update(status='error', error='returned_model_mismatch', eligible_main_comparison=False)
                        stopped = True
                    usage = raw.get('usage') or {}
                    in_tokens = usage.get('prompt_tokens', usage.get('input_tokens'))
                    out_tokens = usage.get('completion_tokens', usage.get('output_tokens'))
                    if (type(in_tokens) is int and type(out_tokens) is int
                            and (in_tokens > job['config']['reserve_input_tokens']
                                 or out_tokens > job['config']['max_completion_tokens'])):
                        record.update(status='error', error='reported_usage_exceeds_reservation')
                        stopped = True
                    record['eligible_main_comparison'] = record['status'] in ('answered', 'abstained')
                except Exception as exc:
                    code = str(exc) if isinstance(exc, APIError) else 'transport_' + type(exc).__name__
                    record.update(status='api_error', error=code)
                    # Authentication/schema failures and ambiguous timeouts require inspection.
                    stopped = True
                record['elapsed_seconds'] = time.monotonic() - started
            record['record_hash'] = digest(record)
            append_durable(destination, record)
            predictions.append(record)
            if stopped:
                break
        pending = len(requests) - len(predictions)
        errors = sum(r['status'] not in ('answered', 'abstained') for r in predictions)
        status = ('budget_stopped' if stopped and pending and not errors else 'incomplete_requires_inspection') if pending else ('completed_with_errors' if errors else 'completed')
        summary = {'status': status, 'updated_at': now(), 'job_hash': job['job_hash'],
                   'question_count': len(requests), 'recorded_predictions': len(predictions), 'pending': pending,
                   'status_counts': dict(Counter(r['status'] for r in predictions)),
                   'prediction_hash': digest(predictions), 'api_key_logged': False,
                   'cost_limit_usd': max_cost_usd,
                   'gold_opened_during_execution': False, 'ledger_path': str(Path(ledger).resolve())}
        write_json(folder / 'run.json', summary)
        return summary


def score_job(folder, output, reviewed_gold=None):
    folder, out = Path(folder), Path(output)
    if out.exists():
        raise ValueError('Scoring output exists; choose a new directory')
    job, requests = load_job(folder)
    run = read_json(folder / 'run.json')
    if run['pending'] or run['status'] not in ('completed', 'completed_with_errors'):
        raise ValueError('Cannot score an incomplete job as a full comparison')
    predictions = read_jsonl(folder / 'predictions.jsonl')
    if digest(predictions) != run['prediction_hash'] or run['job_hash'] != job['job_hash']:
        raise ValueError('Saved predictions changed before scoring')
    for record in predictions:
        if record['record_hash'] != digest({k: v for k, v in record.items() if k != 'record_hash'}):
            raise ValueError('Prediction record changed')
    source = Path(job['source_run'])
    source_run, manifest, _, units, retrieval, baseline = validate_source(source, job['data_dir'])
    # First access to answer files occurs here, after immutable predictions exist.
    if manifest.get('evaluation_sources'):
        gold_source = manifest['evaluation_sources'][job['split']]
        if file_sha(gold_source['path']) != gold_source['sha256']:
            raise ValueError('Original gold bytes changed')
        gold_path = Path(gold_source['path'])
    else:
        gold_path = Path(job['data_dir']) / 'evaluation_gold.jsonl'
    original = read_jsonl(gold_path)
    if not manifest.get('evaluation_sources') and digest(original) != manifest['evaluation_hash']:
        raise ValueError('Original fixture gold changed')
    gold = read_jsonl(reviewed_gold) if reviewed_gold else original
    originals = {g['question_id']: g for g in original}
    if {g['question_id'] for g in gold} != set(originals):
        raise ValueError('Reviewed gold changed question population')
    for g in gold:
        if any(g.get(k) != originals[g['question_id']].get(k) for k in ('doc_id', 'split', 'answer', 'scale')):
            raise ValueError('Reviewed mappings may not change answers or units')
    ids = {r['question_id'] for r in requests}
    gold = [g for g in gold if g['question_id'] in ids]
    paired = [r for r in baseline if r['group'] == 'E1' and r['question_id'] in ids] + predictions
    scores = evaluate(paired, gold, [r for r in retrieval if r['question_id'] in ids], units)
    scores['execution_provenance'] = {'job_hash': job['job_hash'], 'source_run': str(source),
                                    'prediction_hash': run['prediction_hash'], 'original_e1_unchanged': True,
                                    'gold_read_only_after_execution': True, 'official_tatdqa_metrics': False}
    write_json(out / 'metrics.json', scores)
    rows = scores['result_rows']
    with (out / 'results.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=sorted({k for row in rows for k in row}))
        writer.writeheader(); writer.writerows(rows)
    return scores


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare', help='Offline E2 preparation; no key or network required')
    prepare.add_argument('--source-run', required=True); prepare.add_argument('--data', required=True)
    prepare.add_argument('--config', required=True); prepare.add_argument('--output', required=True)
    prepare.add_argument('--limit', type=int)
    execute = sub.add_parser('execute', help='Send real paid requests; requires explicit USD cap')
    execute.add_argument('--job', required=True); execute.add_argument('--key-file')
    cost = execute.add_mutually_exclusive_group(required=True)
    cost.add_argument('--max-cost-usd', type=float)
    cost.add_argument('--no-cost-limit', action='store_true', help='Only when the user explicitly authorized no dollar limit')
    execute.add_argument('--max-calls', type=int, default=200)
    execute.add_argument('--resume-after-inspection', action='store_true',
                         help='After inspecting a stopped run, continue only unsent requests; errors are never resent')
    score = sub.add_parser('score', help='Read gold only after completed inference')
    score.add_argument('--job', required=True); score.add_argument('--output', required=True)
    score.add_argument('--reviewed-gold')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            job = prepare_job(args.source_run, args.data, args.output, read_json(args.config), args.limit)
            result = {k: job[k] for k in ('job_hash', 'split', 'question_count', 'model_calls_at_preparation')}
        elif args.command == 'execute':
            config = read_json(Path(args.job) / 'job.json')['config']
            key = Path(args.key_file).expanduser().read_text().strip() if args.key_file else os.environ.get(config['api_key_env'], '')
            ledger = Path(__file__).resolve().parents[1] / 'artifacts/llm-phase3/budget.jsonl'
            result = execute_job(args.job, key, ledger, args.max_cost_usd, args.max_calls,
                                 resume_after_inspection=args.resume_after_inspection)
        else:
            scores = score_job(args.job, args.output, args.reviewed_gold)
            result = scores['answer_metrics']
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        # No exception body: provider/OS errors can contain credentials or private URLs.
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__,
                          'message': 'Check public configuration, frozen files, local key and budget; no request was retried.'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
