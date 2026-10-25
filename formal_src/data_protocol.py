"""Formal input-only cohort identity checks and frozen auxiliary selection."""
from __future__ import annotations
from collections import Counter, defaultdict
import random
import re
from pathlib import Path
from .contracts import safe_json
from src.ragpilot.common import reject_gold


def source_key(report:str)->str:
    return re.sub(r'[_-](?:19|20)\d{2}$','',Path(report).stem.casefold())


def validate_cohorts(cohorts:dict, *, exposed_sources=(), source_pools:dict|None=None)->dict:
    sizes={'development':50,'validation':50,'main':200,'diagnostic':50}
    if set(cohorts)!=set(sizes):raise ValueError('Missing or unknown cohort role')
    sources={};question_ids=set();doc_sources={};page_counts=Counter();source_counts=Counter()
    for role,size in sizes.items():
        records=cohorts[role]
        if not isinstance(records,list) or len(records)!=size:raise ValueError('Incorrect formal cohort size')
        sources[role]=set()
        for row in records:
            safe_json(row);reject_gold(row)
            for key in ('question_id','doc_id','source_id','question'):
                if not isinstance(row.get(key),str) or not row[key].strip():raise ValueError('Input identity and original question required')
            q,doc,source=(row[k] for k in ('question_id','doc_id','source_id'))
            if q in question_ids:raise ValueError('Question belongs to multiple roles')
            question_ids.add(q)
            if source in exposed_sources:raise ValueError('Previously exposed source is excluded')
            if doc in doc_sources and doc_sources[doc]!=source:raise ValueError('Page assigned to conflicting source reports')
            doc_sources[doc]=source
            page_counts[doc]+=1
            source_counts[('test' if role in {'main','diagnostic'} else role,source)]+=1
            sources[role].add(source)
    if any(count>3 for count in page_counts.values()) or any(count>20 for count in source_counts.values()):
        raise ValueError('Page or source question quota exceeded')
    if sources['development'] & (sources['validation']|sources['main']|sources['diagnostic']) or sources['validation'] & (sources['main']|sources['diagnostic']):
        raise ValueError('Source report leakage across roles')
    if not sources['diagnostic']<=sources['main'] or not 20<=len(sources['main'])<=50:
        raise ValueError('Diagnostic sources or main source coverage invalid')
    if source_pools is not None:
        for role in sizes:
            pool=source_pools['test' if role in {'main','diagnostic'} else role]
            if not sources[role]<=set(pool):raise ValueError('Source is outside prespecified partition')
    return {'counts':dict(sizes),'source_counts':{role:len(ids) for role,ids in sources.items()},
            'source_pools_checked':source_pools is not None,
            'input_scope_verified':False,'human_reviews_complete':False}


def select_auxiliary(main_questions:list[dict], *, seed=20261023)->list[dict]:
    if len(main_questions)!=200:raise ValueError('S50 is drawn from fixed main200')
    grouped=defaultdict(list)
    for row in main_questions:
        safe_json(row);reject_gold(row)
        grouped[row['source_id']].append(row)
    if len({r['question_id'] for r in main_questions})!=200 or not 20<=len(grouped)<=50:
        raise ValueError('Invalid main identity or source coverage')
    rng=random.Random(seed);selected=[];counts=Counter()
    for source in sorted(grouped):
        candidates=sorted(grouped[source],key=lambda r:r['question_id']);rng.shuffle(candidates)
        selected.append(candidates[0]);counts[source]+=1
    used={r['question_id'] for r in selected}
    remaining=sorted([r for r in main_questions if r['question_id'] not in used],key=lambda r:r['question_id'])
    rng.shuffle(remaining)
    for row in remaining:
        if len(selected)==50:break
        if counts[row['source_id']]<4:
            selected.append(row);counts[row['source_id']]+=1
    if len(selected)!=50:raise ValueError('Insufficient quota to select S50')
    return selected
