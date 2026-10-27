"""Pure deterministic compilation; never reads QA, invokes models or freezes profiles."""
import hashlib
import json
from pathlib import Path
from .contracts import ContractError, identifier, safe_json

CANONICAL_PROFILE = json.loads((Path(__file__).resolve().parent.parent/'formal_config/formal-v1.json').read_text())

def content_sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def validate_profile(profile):
    safe_json(profile)
    if profile != CANONICAL_PROFILE:
        raise ContractError('Formal v1 fields are immutable; use a separately registered custom profile')
    if profile['status'] != 'draft': raise ContractError('Unverified profile cannot be frozen')
    return profile

def compile_run_plan(profile: dict, cohorts: dict, *, execution_scope='full') -> dict:
    validate_profile(profile)
    if execution_scope not in ('full','main_only'):raise ContractError('Unknown execution scope')
    safe_json(cohorts)
    required={'main':200,'diagnostic':50,'s50':50} if execution_scope=='full' else {'main':200}
    if not isinstance(cohorts,dict) or set(cohorts)-set(required)-{'development','validation','diagnostic','s50'} or set(required)-set(cohorts): raise ContractError('Missing or unknown cohorts')
    groups={}
    for name,items in cohorts.items():
        size=required.get(name,50)
        if execution_scope=='main_only' and name in ('diagnostic','s50') and items==[]:size=0
        if not isinstance(items,list) or len(items)!=size: raise ContractError('Incorrect cohort size')
        for item in items: identifier(item)
        if len(set(items))!=size: raise ContractError('Duplicate question IDs')
        groups[name]=sorted(items)
    main,diagnostic,s50=(set(groups.get(k,[])) for k in ('main','diagnostic','s50'))
    if main & diagnostic or (s50 and not s50 < main): raise ContractError('Cohort overlap or invalid S50 subset')
    disjoint=[set(groups[k]) for k in ('main','diagnostic','development','validation') if k in groups]
    for i,left in enumerate(disjoint):
        if any(left & right for right in disjoint[i+1:]): raise ContractError('Question roles must be disjoint')
    units=[]; expectations=[]
    def add(partition,condition,retriever,role,repeats,k=2):
        expectations.append(dict(partition=partition,condition=condition,retriever=retriever,model_role=role,question_count=len(groups[partition]),repeats=repeats,unit_count=len(groups[partition])*repeats,k=k))
        for q in groups[partition]:
            for repeat in range(1,repeats+1):
                task_id='|'.join((partition,q,retriever,condition,role,str(repeat)))
                units.append(dict(task_id=task_id,question_id=q,partition=partition,retriever=retriever,condition=condition,model_role=role,repeat=repeat,k=k,work_type='local_baseline' if role=='local' else 'generation'))
    for partition in (('main','diagnostic') if execution_scope=='full' else ('main',)):
        for retriever in ('bm25','minilm'):
            for condition in ('E1','E1_legacy'): add(partition,condition,retriever,'local',1)
            for condition in ('E2','E3','E4'): add(partition,condition,retriever,'primary',3)
    if execution_scope=='full':
        for condition in ('D','L','G','I','C0','C1','E2_no_association'): add('s50',condition,'bm25','primary',3)
        for retriever in ('bm25','minilm'):
            for k in (1,4): add('s50',f'E2_k{k}',retriever,'primary',3,k)
        for condition in ('E2','E3','E4'): add('s50','M2_'+condition,'bm25','secondary',3)
    retrieval_work=[]
    for partition in (('development','validation','main') if execution_scope=='full' else ()):
        for retriever,variants in [('bm25',('finance','simple')),('minilm',('first_segment','windowed'))]:
            for variant in variants:
                for k in (1,2,4):
                    retrieval_work.append(dict(partition=partition,retriever=retriever,variant=variant,k=k,question_ids=groups.get(partition,[]),bound=partition in groups))
    generation=sum(u['work_type']=='generation' for u in units)
    local=len(units)-generation
    expected_counts=(6600,1000,7600) if execution_scope=='full' else (3600,800,4400)
    if (generation,local,len({u['task_id'] for u in units}))!=expected_counts: raise ContractError('Incomplete formal plan')
    return dict(**({'execution_scope':'main_only'} if execution_scope=='main_only' else {}),schema_version=1,profile_id=profile['profile_id'],profile_hash=content_sha(profile),cohort_hash=content_sha(groups),status='draft',executable=False,blocking_items=['Profile, models, resources and human reviews are not frozen or verified'],units=units,logical_generation_count=generation,local_baseline_count=local,condition_expectations=expectations,retrieval_work=retrieval_work,actual_request_reuse=[],budgets=profile['budgets'].copy())
