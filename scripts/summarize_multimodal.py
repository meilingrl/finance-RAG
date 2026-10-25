"""Score completed, sealed runs, then compare exactly matched question sets.

This is post-inference analysis only. It never sends requests or changes gold.
"""
from collections import Counter
import argparse
import csv
import json
from pathlib import Path

from llm_execution import load_job, score_job
from ragpilot.common import digest, file_sha, now, read_json, read_jsonl, write_json


def indexed(rows):
    found = {}
    for row in rows:
        key = (row['question_id'], row['group'])
        if key in found:
            raise ValueError('Duplicate scored question/group')
        found[key] = row
    return found


def aggregate(rows, ids, groups):
    found = indexed(rows)
    output = []
    for group in groups:
        if any((q, group) not in found for q in ids):
            raise ValueError('Unequal paired denominator')
        chosen = [found[(q, group)] for q in sorted(ids)]
        strict = sum(r.get('exact_match') == 1 for r in chosen)
        answer = sum(r.get('answer_only_exact_match') == 1 for r in chosen)
        output.append({'group': group, 'questions': len(chosen),
                       'answered': sum(r['status'] == 'answered' for r in chosen),
                       'strict_correct': strict, 'answer_only_correct': answer,
                       'strict_accuracy_all': strict / len(chosen) if chosen else None,
                       'answer_only_accuracy_all': answer / len(chosen) if chosen else None,
                       'status_counts': dict(Counter(r['status'] for r in chosen))})
    return output


def paired_changes(rows, ids, reference, treatment):
    found = indexed(rows)
    output = {'reference': reference, 'treatment': treatment, 'questions': len(ids)}
    for field, label in [('exact_match', 'strict'), ('answer_only_exact_match', 'answer_only')]:
        pairs = [(found[(q, reference)].get(field) == 1, found[(q, treatment)].get(field) == 1) for q in ids]
        gained = sum(not a and b for a, b in pairs)
        lost = sum(a and not b for a, b in pairs)
        output[label] = {'gained': gained, 'lost': lost,
                         'both_correct': sum(a and b for a, b in pairs),
                         'both_incorrect': sum(not a and not b for a, b in pairs), 'net': gained - lost}
    return output


def summarize(root, reviewed_gold):
    root = Path(root).resolve()
    if (root / 'RESULTS.json').exists():
        raise ValueError('Summary exists; preserve it instead of overwriting')
    folders = {g: root / (g.lower() + '-evaluation-v1') for g in ('E2', 'E3', 'E4')}
    jobs, requests = {}, {}
    for group, folder in folders.items():
        jobs[group], requests[group] = load_job(folder)
        run = read_json(folder / 'run.json')
        if (run['pending'] or run['status'] not in ('completed', 'completed_with_errors')
                or digest(read_jsonl(folder / 'predictions.jsonl')) != run['prediction_hash']):
            raise ValueError('All three sealed runs must finish before comparison')
    reference = {r['question_id']: r for r in requests['E2']}
    for group in ('E3', 'E4'):
        if (jobs[group]['source_run'] != jobs['E2']['source_run']
                or jobs[group]['base_job_hash'] != jobs['E2']['base_job_hash']
                or jobs[group]['experiment_cohort'] != jobs['E2']['experiment_cohort']
                or jobs[group]['config'] != jobs['E2']['config']):
            raise ValueError('Comparison configuration differs')
        for request in requests[group]:
            base = reference[request['question_id']]
            if any(request[k] != base[k] for k in ('selected_ids', 'retrieval_freeze_hash', 'shared_text_hash')):
                raise ValueError('Shared evidence differs')
            extra, original = request['payload'], base['payload']
            if {k: v for k, v in extra.items() if k != 'input'} != {k: v for k, v in original.items() if k != 'input'}:
                raise ValueError('Model, instructions, schema or settings differ')
            if extra['input'][0]['content'][0] != {'type': 'input_text', 'text': original['input'][0]['content']}:
                raise ValueError('Model-visible common text changed')
    rows = []
    scores = {}
    for group, folder in folders.items():
        scores[group] = score_job(folder, root / (folder.name + '-scores'), reviewed_gold)
        rows += [r for r in scores[group]['result_rows'] if r['group'] == group or (group == 'E2' and r['group'] == 'E1')]
    ids = {g: {r['question_id'] for r in requests[g]} for g in requests}
    common = set.intersection(*ids.values())
    local = {r['question_id'] for r in rows if r['group'] == 'E2' and r['evidence_scope'] == 'local'} & common
    whole = common - local
    found = indexed(rows)
    usable = {q for q in common if all(found[(q, g)]['status'] in ('answered', 'abstention')
                                      for g in ('E2', 'E3', 'E4'))}
    matched = {}
    for name, subset in [('all', common), ('local', local), ('whole_page_diagnostic', whole)]:
        matched[name] = {'question_ids': sorted(subset),
                         'results': aggregate(rows, subset, ['E1', 'E2', 'E3', 'E4']),
                         'paired_changes': [paired_changes(rows, subset, 'E2', g) for g in ('E3', 'E4')]}
    model_usable = {}
    for name, subset in [('all', usable), ('local', usable & local), ('whole_page_diagnostic', usable & whole)]:
        model_usable[name] = {'question_ids': sorted(subset),
                              'results': aggregate(rows, subset, ['E1', 'E2', 'E3', 'E4']),
                              'paired_changes': [paired_changes(rows, subset, 'E2', g) for g in ('E3', 'E4')]}
    historical = score_job(jobs['E2']['base_job'], root / 'historical-e2-rescored', reviewed_gold)
    old_rows = [{**r, 'group': 'E2_historical'} for r in historical['result_rows'] if r['group'] == 'E2']
    current_by_id = {r['question_id']: r for r in rows if r['group'] == 'E2'}
    for row in old_rows:
        current = current_by_id[row['question_id']]
        if any(row[k] != current[k] for k in ('doc_id', 'gold_answer', 'gold_scale')):
            raise ValueError('Historical and current scoring truth differs')
    usage = {'input_tokens': 0, 'output_tokens': 0, 'responses_with_usage': 0}
    records = []
    for folder in folders.values():
        records += read_jsonl(folder / 'predictions.jsonl')
    for record in records:
        if record.get('usage'):
            usage['input_tokens'] += record['usage'].get('input_tokens', 0)
            usage['output_tokens'] += record['usage'].get('output_tokens', 0)
            usage['responses_with_usage'] += 1
    result = {'time': now(), 'status': 'completed_frozen_bm25_multimodal_pilot',
              'cohort': jobs['E2']['experiment_cohort'], 'retriever': 'BM25 finance k2',
              'model_reported': jobs['E2']['config']['model'], 'sample_expanded': False,
              'official_tatdqa_metrics': False, 'denominator': 'All questions in each stated subset; abstentions and technical errors separately counted',
              'shared_controls_verified': True,
              'group_populations': {g: {'original_questions': jobs[g]['original_question_count'],
                                        'eligible_questions': len(ids[g]), 'exclusions': jobs[g]['exclusion_count']} for g in jobs},
              'matched_comparisons': matched,
              'model_usable_matched_comparisons': model_usable,
              'technical_failures_excluded_from_model_only_subset': [
                  {'question_id': q, 'statuses': {g: found[(q, g)]['status'] for g in ('E2', 'E3', 'E4')}}
                  for q in sorted(common - usable)],
              'text_description_all36': aggregate(rows, ids['E2'] & ids['E4'], ['E1', 'E2', 'E4']),
              'historical_text_replication36': {'results': aggregate(rows + old_rows, ids['E2'], ['E2_historical', 'E2']),
                                                'changes': paired_changes(rows + old_rows, ids['E2'], 'E2_historical', 'E2')},
              'usage_main_phase4': usage, 'main_phase4_attempts': len(records),
              'total_attempts_in_shared_ledger': len(read_jsonl(root.parent / 'llm-phase3/budget.jsonl')),
              'provider_cost_usd': None,
              'source_hashes': {str(folder / 'run.json'): file_sha(folder / 'run.json') for folder in folders.values()},
              'summary_script_sha256': file_sha(__file__), 'reviewed_gold_sha256': file_sha(reviewed_gold),
              'limitations': ['Single draw per condition on 12 report pages; questions within a report are dependent',
                              'No model version attestation beyond third-party reported identifier',
                              'No semantic citation validation or official TAT-DQA scorer',
                              'E4 legend repeats existing text and adds metadata; gain cannot isolate geometry from repetition',
                              'BCE3 image cases excluded by blind scope review; whole-page6 diagnostic separated',
                              'MiniLM visual groups are outside this bounded default-retriever follow-up']}
    for row in rows:
        row['in_all_group_common_subset'] = row['question_id'] in common
        row['in_model_usable_common_subset'] = row['question_id'] in usable
    with (root / 'all-scored-results.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=sorted({k for row in rows for k in row}))
        writer.writeheader(); writer.writerows(rows)
    write_json(root / 'RESULTS.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True); parser.add_argument('--reviewed-gold', required=True)
    args = parser.parse_args()
    result = summarize(args.root, args.reviewed_gold)
    print(json.dumps({k: result[k] for k in ('status', 'group_populations', 'matched_comparisons')}, ensure_ascii=False, indent=2))
