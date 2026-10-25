"""Paired source-cluster bootstrap over question-level repeated-run averages."""
from __future__ import annotations
from collections import defaultdict
import math
import random


def _quantile(values, probability):
    position = (len(values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def paired_cluster_interval(rows: list[dict], *, iterations: int = 10000,
                            seed: int = 20261023) -> dict:
    if type(iterations) is not int or iterations < 1 or type(seed) is not int or not rows:
        raise ValueError('Positive iterations, integer seed, and paired observations are required')
    questions = defaultdict(list)
    sources = {}
    seen = set()
    for row in rows:
        q, source, repeat = (row.get(k) for k in ('question_id', 'source_id', 'repeat'))
        value = row.get('difference')
        if (not isinstance(q, str) or not q or not isinstance(source, str) or not source
                or type(repeat) is not int or repeat < 1
                or type(value) not in (int, float) or not math.isfinite(value) or not -1 <= value <= 1):
            raise ValueError('Invalid paired observation')
        if (q, repeat) in seen or (q in sources and sources[q] != source):
            raise ValueError('Duplicate round or conflicting source for question')
        seen.add((q, repeat)); sources[q] = source
        questions[q].append(float(value))
    clusters = defaultdict(list)
    for q, values in sorted(questions.items()):
        clusters[sources[q]].append(sum(values) / len(values))
    names = sorted(clusters)
    source_sums = {s: sum(clusters[s]) for s in names}
    rng = random.Random(seed)
    draws = []
    for _ in range(iterations):
        sample = rng.choices(names, k=len(names))
        draws.append(sum(source_sums[s] for s in sample) / sum(len(clusters[s]) for s in sample))
    draws.sort()
    effect = sum(source_sums.values()) / len(questions)
    return {'effect': effect, 'question_count': len(questions), 'source_count': len(names),
            'source_macro_effect': sum(source_sums[s] / len(clusters[s]) for s in names) / len(names),
            'ci_95': [_quantile(draws, .025), _quantile(draws, .975)],
            'ci_9875': [_quantile(draws, .00625), _quantile(draws, .99375)],
            'iterations': iterations, 'seed': seed, 'resampling_unit': 'source_cluster',
            'round_aggregation': 'question_mean', 'approximate': True}


def main_comparisons(scored_rows: list[dict], *, iterations=10000, seed=20261023) -> dict:
    """P1 answerer, P2 retriever, P3 image, P4 structure; main cohort only."""
    by_condition = defaultdict(dict)
    source_by_question = {}
    for row in scored_rows:
        if row['partition'] != 'main':
            continue
        expected_role = 'local' if row['condition'] == 'E1' else 'primary'
        if row.get('model_role', expected_role) != expected_role:
            continue
        condition = (row['retriever'], row['condition'])
        identity = (row['question_id'], row['repeat'])
        if identity in by_condition[condition]:
            raise ValueError('Duplicate question/round within condition')
        by_condition[condition][identity] = row['uem']
        q = row['question_id']
        if source_by_question.setdefault(q, row['source_id']) != row['source_id']:
            raise ValueError('Conflicting paired source')
    definitions = [('P1', ('bm25','E2'), ('bm25','E1')),
                   ('P2', ('minilm','E2'), ('bm25','E2')),
                   ('P3', ('bm25','E3'), ('bm25','E2')),
                   ('P4', ('bm25','E4'), ('bm25','E2'))]
    results = {}
    for name, positive, negative in definitions:
        a, b = by_condition.get(positive, {}), by_condition.get(negative, {})
        if not a or not b:
            results[name] = {'status':'not_available','reason':'required main condition absent'}
            continue
        if {q for q,_ in a} != {q for q,_ in b}:
            raise ValueError('Compared question populations differ')
        for scores, condition in [(a, positive[1]), (b, negative[1])]:
            rounds = defaultdict(set)
            for q, repeat in scores:
                rounds[q].add(repeat)
            required = {1} if condition == 'E1' else {1, 2, 3}
            if any(value != required for value in rounds.values()):
                raise ValueError('Compared condition has incomplete or extra rounds')
        pairs = []
        for (q, repeat), value in sorted(a.items()):
            b_key = (q, 1 if negative[1] == 'E1' else repeat)
            if b_key not in b:
                raise ValueError('Missing paired round')
            pairs.append({'question_id':q, 'source_id':source_by_question[q],
                          'repeat':repeat, 'difference':value-b[b_key]})
        results[name] = {'status':'complete', 'positive':list(positive), 'negative':list(negative),
                         **paired_cluster_interval(pairs,iterations=iterations,seed=seed)}
    return results
