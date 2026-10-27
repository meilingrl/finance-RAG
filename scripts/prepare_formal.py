#!/usr/bin/env python3
"""Bounded pinned acquisition, curator split, then separate question-blind builder."""
import argparse
import concurrent.futures
import re
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from scripts.phase2_acquire import Fetcher as LegacyFetcher,RemoteZip as LegacyRemoteZip,REVISION,dump,jsonl,sha
from formal_src.acquisition import select_questions,evaluation_records,diagnostic_category,audit_identity,diagnostic_queue
from formal_src.data_protocol import source_key,select_auxiliary

def load_protocol():
    return json.loads((ROOT/'configs/formal/source_protocol.json').read_text())

def readl(path):return [json.loads(x) for x in path.read_text().splitlines()]

def _local_binding(method,root):
    """Reuse frozen acquisition code with an instance-local path formatter only."""
    from types import FunctionType,MethodType
    fn=method.__func__
    namespace={**fn.__globals__,'portable':lambda path:str(Path(path).resolve().relative_to(root.resolve()))}
    adapted=FunctionType(fn.__code__,namespace,fn.__name__,fn.__defaults__,fn.__closure__)
    return MethodType(adapted,method.__self__)

class Fetcher(LegacyFetcher):
    def __init__(self,root):
        super().__init__(Path(root).resolve())
        self.metadata=_local_binding(self.metadata,self.root)

class RemoteZip(LegacyRemoteZip):
    def __init__(self,split,fetch):
        super().__init__(split,fetch)
        self.read=_local_binding(self.read,fetch.root)

def ensure_candidate_mutable(root):
    root=Path(root).resolve();manifest=root/'resources.json'
    if any((root/name).exists() for name in ('frozen.json','freeze.json','.reviewed.json')):
        raise ValueError('Qualified/frozen candidate bundle is immutable')
    if manifest.exists():
        m=json.loads(manifest.read_text())
        if (any(v is True for v in m.get('qualification',{}).values())
                or m.get('human_scope_review') is True
                or any(r['kind']=='approved_description' for r in m['resources'])):
            raise ValueError('Qualified/reviewed candidate bundle is immutable')
    # Imported review records live in the application registry, not in the asset.
    registry=ROOT/'artifacts/formal-v1/resources'
    for path in registry.glob('*.json') if registry.exists() else []:
        registered=json.loads(path.read_text())
        if Path(registered['root']).resolve()==root and registered.get('reviews'):
            raise ValueError('Reviewed candidate bundle is immutable')

def select_qualified(records,pool,*,target,role,acquire,documents,source_target=None,max_sources=50):
    active=list(pool[:source_target] if source_target else pool);reserve=iter(pool[len(active):]);eligible=list(records);log=[]
    while True:
        selected=select_questions(eligible,active,target=target,role=role)
        acquire(q['doc_id'] for q in selected)
        missing={q['doc_id'] for q in selected if q['doc_id'] not in documents}
        if missing:
            eligible=[r for r in eligible if r['doc']['uid'] not in missing]
            continue
        present={q['source_id'] for q in selected}
        absent=[s for s in active if s not in present]
        changed=False
        if source_target:
            for source in absent:
                replacement=next(reserve,None)
                if replacement is None:break
                active[active.index(source)]=replacement;changed=True
                log.append({'rejected_source':source,'replacement_source':replacement,'reason':'no_input_qualified_eligible_page'})
            if changed:continue
        if len(selected)>=target and (not source_target or len(present)>=source_target):return selected,log
        replacement=next(reserve,None) if source_target and len(active)<max_sources else None
        if replacement is None:return selected,log
        active.append(replacement);log.append({'rejected_source':None,'replacement_source':replacement,'reason':'insufficient_qualified_sample_capacity'})

def builder(root):
    root=root.resolve()
    ensure_candidate_mutable(root)
    # Only this explicit documents manifest and PDF/OCR are read. No questions or evaluation.
    from ragpilot.evidence import build_units,render_units
    from formal_src.evidence import build_associations
    from formal_src.structure import describe_units
    units=[]
    for doc in readl(root/'inputs/documents.jsonl'):
        raw=json.loads((root/doc['ocr_path']).read_text())
        us=build_units(doc,raw,segmentation='blind_blocks')
        render_units(us,root/doc['pdf_path'],root/'inputs/crops')
        for u in us:
            u['source_id']=doc['source_id']
            if u.get('image_path'):u['image_path']=str((ROOT/u['image_path']).resolve().relative_to(root))
        units.extend(us)
    jsonl(root/'inputs/evidence_units.jsonl',units)
    jsonl(root/'inputs/associations.jsonl',build_associations(units))
    jsonl(root/'inputs/draft_structures.jsonl',describe_units(units))
    jsonl(root/'review/blind_reviewpack.jsonl',[{'evidence_id':u['evidence_id'],'doc_id':u['doc_id'],'source_id':u['source_id'],'region':u['region'],'text':u['text'],'image_path':u['image_path'],'image_sha256':u['image_sha256'],'question_exposure':False,'visual_verified':False} for u in units])
    return len(units)

def prepare_diagnostic_queue(root):
    root=root.resolve();ensure_candidate_mutable(root);protocol=load_protocol()
    records=[]
    for split in ('train','dev'):
        records.extend(json.loads((root/f'isolated_gold/official_metadata/tatdqa_dataset_{split}.json').read_text()))
    questions=readl(root/'inputs/inference_questions.jsonl')
    main=[q for q in questions if q['split']=='main']
    sources=[s for s in protocol['source_pools']['test'] if s in {q['source_id'] for q in main}]
    documents={}
    for record in records:
        uid=record['doc']['uid'];bindings={}
        for kind,ext in [('pdf','pdf'),('ocr','json')]:
            path=root/f'inputs/{kind}/{uid}.{ext}'
            if path.exists():bindings[kind+'_path']=str(path.relative_to(root))
        if bindings:documents[uid]=bindings
    queue=diagnostic_queue(records,sources,main,documents=documents)
    queue_path=root/'review/diagnostic_qualification_queue.jsonl';jsonl(queue_path,queue)
    validation_sources={q['source_id'] for q in questions if q['split']=='validation'}
    absent=[]
    for source in protocol['source_pools']['validation']:
        if source in validation_sources:continue
        matching=[r for r in records if source_key(r['doc']['source'])==source]
        eligible=sum(q.get('answer_type')=='span' and q.get('req_comparison') is False and bool(q.get('question','').strip()) for r in matching for q in r['questions'])
        from ragpilot.evidence import normalize_page
        input_failures=[]
        for record in matching:
            uid=record['doc']['uid'];path=root/f'inputs/ocr/{uid}.json'
            if path.exists():
                try:normalize_page(json.loads(path.read_text()))
                except ValueError:input_failures.append({'doc_id':uid,'reason':'ocr_normalization_rejected_geometry'})
        reason='no_eligible_questions' if not eligible else ('all_source_pages_rejected_input_geometry' if len(input_failures)==len(matching) else 'not_selected_after_input_qualification_or_fixed_sampling')
        absent.append({'source_id':source,'official_metadata_documents':len(matching),'eligible_span_noncomparison_questions':eligible,'reason':reason,'input_failures':input_failures,'source_pool_unchanged':True})
    summary={'target':{'complex_header':25,'footnote':25},'certified':{'complex_header':0,'footnote':0},'queue_questions':len(queue),'queue_with_pdf_and_ocr':sum(q['input_assets_available'] for q in queue),'queue_sources':len({q['source_id'] for q in queue}),'shared_page_quota_max':max(Counter(q['doc_id'] for q in main+queue).values()),'shared_source_quota_max':max(Counter(q['source_id'] for q in main+queue).values()),'validation_pool_size':30,'validation_selected_sources':len(validation_sources),'validation_absent_sources':absent,'human_qualification_complete':False,'queue_is_fixed_bounded_review_worklist_not_certified_diagnostic_set':True}
    dump(root/'review/diagnostic_handoff_status.json',summary)
    manifest_path=root/'resources.json';manifest=json.loads(manifest_path.read_text())
    paths={x['path']:x for x in manifest['resources']}
    for path in [queue_path,root/'review/diagnostic_handoff_status.json']:
        rel=str(path.relative_to(root));paths[rel]={'kind':'review','path':rel,'sha256':sha(path.read_bytes())}
    manifest['resources']=list(paths.values());dump(manifest_path,manifest)
    dump(ROOT/'artifacts/formal-v1-preparation/diagnostic-handoff-summary.json',summary)
    print(json.dumps(summary),flush=True)

def main(root):
    root=Path(root).resolve();ensure_candidate_mutable(root)
    root.mkdir(parents=True,exist_ok=True)
    report=ROOT/'artifacts/formal-v1-preparation';report.mkdir(parents=True,exist_ok=True)
    fetch=Fetcher(root);failures=[];metadata=[];archives={};split_by_doc={}
    try:
        protocol=load_protocol()
        if protocol['seed']!=20261023:raise ValueError('unsupported_source_protocol_seed')
        pools=protocol['source_pools']
        exposed={source_key(s) for s in protocol['known_exposed_source_reports']}
        if any(set(pool)&exposed for pool in pools.values()):raise ValueError('exposed_source_in_pool')
        for split in ('train','dev'):
            rows,_=fetch.metadata(split);metadata.extend(rows)
            for r in rows:split_by_doc[r['doc']['uid']]=split
            archives[split]=RemoteZip(split,fetch)
        valid=[]
        for r in metadata:
            uid=r['doc']['uid'];split=split_by_doc[uid];names=archives[split].entries
            if all(f'{split}/{uid}.{ext}' in names for ext in ('json','pdf')) and sum(n.startswith(f'{split}/{uid}_') and n.endswith('.png') for n in names)==1:valid.append(r)
        bydoc={r['doc']['uid']:r for r in valid};documents={}
        def acquire(doc_ids):
            from ragpilot.evidence import normalize_page
            import fitz
            def one(uid):
                r=bydoc[uid];d=r['doc'];split=split_by_doc[uid]
                try:
                    bindings={}
                    for ext,kind in [('json','ocr'),('pdf','pdf')]:
                        path=root/f'inputs/{kind}/{uid}.{ext}';archives[split].read(f'{split}/{uid}.{ext}',path);bindings[kind+'_path']=str(path.relative_to(root))
                    page=normalize_page(json.loads((root/bindings['ocr_path']).read_text()))
                    with fitz.open(root/bindings['pdf_path']) as pdf:
                        if len(pdf)!=1:raise ValueError('not_single_pdf_page')
                        bounds=page['bbox'];sx=pdf[0].rect.width/(bounds[2]-bounds[0]);sy=pdf[0].rect.height/(bounds[3]-bounds[1])
                        if abs(sx/sy-1)>.005:raise ValueError('pdf_ocr_aspect_mismatch')
                    documents[uid]={'doc_id':uid,'source_report':d['source'],'source_id':source_key(d['source']),'page':int(d.get('page',1)),'original_split':split,'page_bbox':page['bbox'],**bindings}
                except Exception as e:failures.append({'doc_id':uid,'reason':type(e).__name__})
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(one,sorted(set(doc_ids)-documents.keys())))
        cohorts={};replacement_log=[]
        for role,target in [('development',50),('validation',50),('main',200)]:
            pool=pools['test'] if role=='main' else pools[role]
            cohorts[role],replacements=select_qualified(valid,pool,target=target,role=role,acquire=acquire,documents=documents,source_target=40 if role=='main' else None)
            replacement_log.extend(replacements)
        # Diagnostic candidates are never populated with ordinary span questions.
        test_sources=sorted({q['source_id'] for q in cohorts['main']});diagnostic=[]
        for category in ('complex_header','footnote'):
            candidates=select_questions(valid,test_sources,target=25,role='diagnostic',existing=cohorts['main']+diagnostic,predicate=lambda q,d: (bool(re.search(r'\b(?:header|heading|subcolumn|sub-column)\b',q['question'],re.I)) if category=='complex_header' else diagnostic_category(q,[])==category))
            acquire(x['doc_id'] for x in candidates)
            diagnostic.extend({**q,'diagnostic_category':category,'qualification_status':'human_qualification_pending'} for q in candidates if q['doc_id'] in documents)
        cohorts['diagnostic']=diagnostic;questions=sum(cohorts.values(),[])
        audit_rows=[]
        for q in questions:
            doc=documents[q['doc_id']]
            audit_rows.append({'doc_id':q['doc_id'],'source_id':q['source_id'],'split':q['split'],**{kind+'_sha256':sha((root/doc[kind+'_path']).read_bytes()) for kind in ('pdf','ocr')}})
        dump(root/'review/source_sha_audit.json',audit_identity(audit_rows))
        dump(root/'review/source_replacements.json',replacement_log)
        # Keep document projection question-blind.
        used={q['doc_id'] for q in questions};jsonl(root/'inputs/documents.jsonl',[documents[d] for d in sorted(used)])
        jsonl(root/'inputs/inference_questions.jsonl',questions)
        ids={k:[q['question_id'] for q in v] for k,v in cohorts.items()}
        ids['s50']=[q['question_id'] for q in select_auxiliary(cohorts['main'])] if len(cohorts['main'])==200 else []
        dump(root/'inputs/cohorts.json',ids)
        jsonl(root/'evaluation/gold.jsonl',evaluation_records(metadata,questions))
        jsonl(root/'review/diagnostic_qualification_queue.jsonl',[{**q,'reviewer_qa_exposed':True,'eligible_for_description_blind_review':False} for q in diagnostic])
        subprocess.run([sys.executable,str(Path(__file__).resolve()),'--output',str(root),'--build-inputs'],check=True,stdout=subprocess.DEVNULL)
        resources=[]
        for p in sorted(root.rglob('*')):
            if not p.is_file() or 'acquisition_cache' in p.parts or p.name=='resources.json':continue
            rel=p.relative_to(root)
            if rel.parts[0] in {'evaluation','isolated_gold'}:kind='evaluation'
            elif rel.parts[0]=='review':kind='review'
            elif 'pdf' in rel.parts:kind='pdf'
            elif 'ocr' in rel.parts:kind='ocr'
            else:kind='input'
            resources.append({'kind':kind,'path':str(rel),'sha256':sha(p.read_bytes())})
        qualification={k:False for k in ('source_disjoint','cohorts_validated','inputs_complete','development_passed','validation_passed','compatibility_passed','throughput_passed','budget_validated')}
        dump(root/'resources.json',{'schema_version':1,'resource_set_id':'formal-v1','purpose':'formal','resources':resources,'qualification':qualification,'model_status':'model_missing','human_scope_review':False,'source_alias_sha_audit':False,'publisher_revision':REVISION,'license':'CC BY 4.0','attribution':'TAT-DQA, NExT++ / TAT@NExT, Zhu et al., ACM MM 2022'})
        prepare_diagnostic_queue(root)
        summary={'counts':{k:len(v) for k,v in cohorts.items()},'source_counts':{k:len({q['source_id'] for q in v}) for k,v in cohorts.items()},'documents':len(used),'diagnostic_categories':dict(Counter(q['diagnostic_category'] for q in diagnostic)),'human_gates_complete':False,'formal_ready':False,'model_calls':0,'failures':failures,'prespecified_test_source_target':40,'prespecified_test_reserve':pools['test'][40:],'exposed_source_exclusion_count':len(exposed),'diagnostic_policy':'Conservative lexical/geometry candidates only; human qualification pending. No ordinary-question substitution.'}
    except Exception as e:
        summary={'formal_ready':False,'blocking_error':type(e).__name__,'detail_code':str(e) if isinstance(e,ValueError) else 'public_endpoint_or_preparation_failure','failures':failures,'model_calls':0}
    finally:
        previous=report/'network_requests.json';old=json.loads(previous.read_text()) if previous.exists() else []
        dump(previous,old+fetch.requests)
    summary['network_bytes_this_process']=sum(x['bytes'] for x in fetch.requests);summary['full_archive_downloads']=0
    dump(report/'summary.json',summary);print(json.dumps(summary),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='data/formal-v1');parser.add_argument('--build-inputs',action='store_true');parser.add_argument('--prepare-diagnostic-queue',action='store_true');args=parser.parse_args()
    path=Path(args.output);path=path if path.is_absolute() else ROOT/path
    if args.prepare_diagnostic_queue:prepare_diagnostic_queue(path)
    elif args.build_inputs:builder(path)
    else:main(path)
