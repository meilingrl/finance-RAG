"""Prospective unit-aware scoring. Never modifies the frozen pilot scorer."""
from __future__ import annotations
from collections import Counter, defaultdict
from src.ragpilot.metrics import answer_scores, numeric_comparison

FINAL_STATUSES = {'answered', 'abstained', 'refused', 'failed', 'parse_error', 'truncated'}


def uem_score(prediction: dict, gold: dict) -> dict:
    """Strict normalized span/units; no synonym inference or unit conversion."""
    if 'answer' not in gold:
        raise ValueError('Gold answer is required')
    status = prediction.get('status')
    if status not in FINAL_STATUSES:
        raise ValueError('Only final executed states can be scored')
    answer = prediction.get('answer')
    usable = status == 'answered' and answer is not None
    candidate = answer if usable else ''
    old = answer_scores(candidate, gold['answer'], prediction.get('scale', ''), gold.get('scale', ''))
    numeric = numeric_comparison(candidate, gold['answer'], prediction.get('scale', ''), gold.get('scale', ''))
    matched = numeric['numeric_with_units_match'] if numeric['gold_numeric'] else old['exact_match']
    return {'uem': int(bool(usable and matched)), 'em': old['exact_match'] if usable else 0,
            'f1': old['token_f1'] if usable else 0,
            'answer_only_em': old['answer_only_exact_match'] if usable else 0,
            'answer_only_f1': old['answer_only_token_f1'] if usable else 0,
            'numeric': numeric, 'status': status}


def evaluate_predictions(predictions: list[dict], gold_records: list[dict],
                         planned_units: list[dict], *, sealed: bool = False) -> dict:
    """Pure full-plan evaluator; caller must verify on-disk seal before invoking."""
    if sealed is not True:
        raise ValueError('Predictions must be sealed before evaluation')
    def index(records, key):
        out = {}
        for row in records:
            identity = row.get(key)
            if not isinstance(identity, str) or not identity or identity in out:
                raise ValueError('Duplicate or missing record identity')
            out[identity] = row
        return out
    planned = index(planned_units, 'task_id')
    received = index(predictions, 'task_id')
    golds = index(gold_records, 'question_id')
    if not planned or set(planned) != set(received):
        raise ValueError('Incomplete or foreign predictions cannot make a main table')
    rows = []
    grouped = defaultdict(list)
    for task_id, task in planned.items():
        prediction = received[task_id]
        for key in ('question_id', 'partition', 'retriever', 'condition', 'model_role', 'repeat'):
            if prediction.get(key) != task.get(key):
                raise ValueError('Prediction metadata does not match frozen plan')
        gold = golds.get(task['question_id'])
        if gold is None or not isinstance(gold.get('source_id'), str) or not gold['source_id']:
            raise ValueError('Qualified gold with a source identity is required')
        if prediction.get('source_id', gold['source_id']) != gold['source_id']:
            raise ValueError('Prediction source differs from evaluation source')
        row = {**task, **uem_score(prediction, gold), 'source_id': gold['source_id']}
        rows.append(row)
        key = '|'.join(str(task[k]) for k in ('partition', 'retriever', 'condition', 'model_role'))
        grouped[key].append(row)
    metrics = {}
    for key, items in grouped.items():
        questions = defaultdict(list)
        for item in items:
            questions[item['question_id']].append(item)
        values = {}
        for metric in ('uem', 'em', 'f1', 'answer_only_em', 'answer_only_f1'):
            values[metric] = sum(sum(r[metric] for r in rs) / len(rs) for rs in questions.values()) / len(questions)
        metrics[key] = {**values, 'question_count': len(questions), 'prediction_count': len(items),
                        'source_count': len({r['source_id'] for r in items}),
                        'status_counts': dict(Counter(r['status'] for r in items))}
    return {'evaluation_protocol': 'financial_uem_v1', 'official_tatdqa': False,
            'metrics': metrics, 'rows': rows, 'audit_complete': False,
            'denominator': 'all planned questions; average rounds within question first'}
