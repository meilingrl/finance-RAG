"""Predeclared audit sampling and accounting; never invents a human review."""
from __future__ import annotations
import math
from collections import Counter
import random


def build_audit_plan(main_questions: list[dict], diagnostic_questions: list[dict],
                     regions: list[dict], *, seed=20261023) -> dict:
    rng = random.Random(seed)
    populations = []
    for records, count in [(main_questions,30),(diagnostic_questions,15)]:
        ids = []
        for row in records:
            if set(row) != {'question_id','source_id'} or not all(isinstance(v,str) and v for v in row.values()):
                raise ValueError('Audit sampling accepts only question and source identities')
            ids.append(row['question_id'])
        if len(set(ids)) != len(ids) or len(ids) < count:
            raise ValueError('Insufficient or duplicate audit population')
        populations.append(sorted(rng.sample(sorted(ids),count)))
    if set(populations[0]) & set(populations[1]):
        raise ValueError('Main and diagnostic audit questions overlap')
    answer_items = []
    for partition, ids, retrievers in [('main',populations[0],['bm25','minilm']),
                                       ('diagnostic',populations[1],['bm25'])]:
        for q in ids:
            for retriever in retrievers:
                for condition in ['E2','E3','E4']:
                    answer_items.append({'item_id':f'{partition}|{q}|{retriever}|{condition}|1',
                                         'partition':partition,'question_id':q,'retriever':retriever,
                                         'condition':condition,'repeat':1})
    relation_items = []
    seen_regions = set()
    for region in regions:
        if (set(region) != {'evidence_id','relation_ids'} or not isinstance(region['evidence_id'],str)
                or not isinstance(region['relation_ids'],list) or not all(isinstance(x,str) and x for x in region['relation_ids'])):
            raise ValueError('Relation sampling must be question/answer blind')
        if region['evidence_id'] in seen_regions or len(set(region['relation_ids'])) != len(region['relation_ids']):
            raise ValueError('Duplicate region or relation')
        seen_regions.add(region['evidence_id'])
    selected = rng.sample(sorted(regions,key=lambda r:r['evidence_id']),min(30,len(regions)))
    for region in selected:
        relation_ids = rng.sample(sorted(region['relation_ids']),min(5,len(region['relation_ids'])))
        if not relation_ids:
            relation_items.append({'item_id':f'region|{region["evidence_id"]}|no_relations',
                                   'evidence_id':region['evidence_id'],'relation_id':None,'kind':'coverage_only'})
        for relation_id in sorted(relation_ids):
            relation_items.append({'item_id':f'relation|{region["evidence_id"]}|{relation_id}',
                                   'evidence_id':region['evidence_id'],'relation_id':relation_id,'kind':'relation'})
    # The 25% second-review rule applies to semantic answer items. Relations
    # have their separate blinded review, with every conflict adjudicated.
    second = sorted(rng.sample([r['item_id'] for r in answer_items],math.ceil(len(answer_items)*.25)))
    return {'schema_version':1,'seed':seed,'main_question_ids':populations[0],
            'diagnostic_question_ids':populations[1],'answer_items':answer_items,
            'relation_items':relation_items,'second_review_ids':second,
            'blocking_items':[] if len(selected)==30 else ['fewer than 30 eligible relation regions'],
            'audit_complete':False}


def audit_status(plan: dict, reviews: list[dict]) -> dict:
    items = {r['item_id'] for r in plan['answer_items']+plan['relation_items']}
    if len(items) != len(plan['answer_items'])+len(plan['relation_items']):
        raise ValueError('Duplicate audit item')
    indexed = {}
    verdicts = {'supported','partially_supported','unsupported','undetermined'}
    for review in reviews:
        item,stage = review.get('item_id'),review.get('stage')
        if item not in items or stage not in {'initial','second','resolution'}:
            raise ValueError('Unknown review item or stage')
        if (not isinstance(review.get('reviewer_id'),str) or not review['reviewer_id'].strip()
                or review.get('verdict') not in verdicts or (item,stage) in indexed
                or not isinstance(review.get('reason'),str) or not review['reason'].strip()):
            raise ValueError('Invalid or duplicate human review')
        if stage == 'resolution' and not review.get('reason'):
            raise ValueError('Adjudication requires a reason')
        indexed[item,stage]=review
    for (item, stage), resolution in indexed.items():
        if stage != 'resolution':
            continue
        first = indexed.get((item, 'initial'))
        second = indexed.get((item, 'second'))
        if first is None or second is None or first['verdict'] == second['verdict']:
            raise ValueError('Resolution requires an actual disagreement between two reviews')
        if resolution['reviewer_id'] in {first['reviewer_id'], second['reviewer_id']}:
            raise ValueError('Dispute adjudication must be independent')
    missing_initial = 0
    missing_second = 0
    disputes = 0
    disagreements = 0
    resolved = 0
    final_verdicts = Counter()
    mandatory_second = set(plan['second_review_ids'])
    for item in sorted(items):
        first=indexed.get((item,'initial'))
        second=indexed.get((item,'second'))
        resolution=indexed.get((item,'resolution'))
        if first is None:
            missing_initial+=1
            continue
        if first['verdict'] != 'supported':
            mandatory_second.add(item)
        if second and first['reviewer_id']==second['reviewer_id']:
            raise ValueError('Second review must be independent')
        if item in mandatory_second and second is None:
            missing_second+=1
            continue
        if second and first['verdict'] != second['verdict']:
            disagreements += 1
            if resolution is None:
                disputes+=1
                continue
            elif resolution['reviewer_id'] in {first['reviewer_id'],second['reviewer_id']}:
                raise ValueError('Dispute adjudication must be independent')
            else:
                resolved += 1
        final_verdicts[(resolution or second or first)['verdict']] += 1
    return {'audit_complete':not (missing_initial or missing_second or disputes or plan.get('blocking_items')),
            'missing_initial':missing_initial,'missing_second':missing_second,
            'unresolved_disputes':disputes,'review_count':len(reviews),
            'disagreement_count':disagreements,'resolved_disputes':resolved,
            'final_verdict_counts':dict(final_verdicts),
            'blocking_items':list(plan.get('blocking_items',[]))}
