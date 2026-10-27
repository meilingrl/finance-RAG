"""Unified inference worker. Reads only the allowlisted inputs projection.

Qualified multimodal resource adapters are intentionally gated, never inferred
from legacy page coordinates. Evaluation is a separate post-seal operation.
"""
import json,os,time,hashlib,fcntl
from pathlib import Path
from .contracts import ResolvedRunSpec,AnswerRecord,RunResults,safe_json
from .provider import build_payload,generate,digest,ProviderError,APIError,validate_config
from .budget import Budget,BudgetError,append,latest
from .retrieval import retrieve
from .baseline import extract
from .course_policy import readiness_valid
from .recovery import commit_prediction,repair_prediction,save_receipt,settle_receipts,restored_result
from .evidence import build_associations,expand_once

class RunnerBlocked(ValueError):pass

TRANSIENT_HTTP = {'http_429','http_500','http_502','http_503','http_504'}
PROVIDER_FAILURES = {'model_mismatch','response_not_completed','provider_validation_failed','model_not_adopted','provider_limits_missing','provider_endpoint_invalid','inference_sensitive_field','wire_budget_invalid','wire_budget_exceeded','context_budget_exceeded','capability_missing','capability_unsupported'}
def fatal_provider_failure(code):
 return bool(code and (code in PROVIDER_FAILURES or code.startswith('http_') and code not in TRANSIENT_HTTP))

def atomic(path,value):
 path=Path(path);tmp=path.with_name(path.name+'.tmp')
 with tmp.open('w') as f:json.dump(value,f,sort_keys=True,allow_nan=False);f.flush();os.fsync(f.fileno())
 os.replace(tmp,path)
 fd=os.open(str(path.parent),os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def rows(path):return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]

def load_inputs(run_dir):
 projection=json.loads((run_dir/'inputs.json').read_text());root=Path(projection['root']).resolve();loaded={}
 for binding in projection['resources']:
  if binding['kind'] not in {'input','model','approved_description'}:raise RunnerBlocked('inference_projection_invalid')
  p=(root/binding['path']).resolve()
  if not p.is_relative_to(root) or not p.is_file() or sha(p)!=binding['sha256']:raise RunnerBlocked('inference_asset_changed')
  if p.name in {'inference_questions.jsonl','evidence_units.jsonl'}:
   loaded[p.name]=rows(p)
   for record in loaded[p.name]:safe_json(record)
 if set(loaded)!={'inference_questions.jsonl','evidence_units.jsonl'}:raise RunnerBlocked('qualified_input_adapter_missing')
 return loaded['inference_questions.jsonl'],loaded['evidence_units.jsonl']

def condition_content(condition,question,units,assets):
 safe_json(units);safe_json(assets)
 condition={'M2_E2':'E2','M2_E3':'E3','M2_E4':'E4'}.get(condition,condition)
 def text(value):return {'type':'input_text','text':json.dumps(value,ensure_ascii=False,sort_keys=True)}
 evidence=[{'evidence_id':u['evidence_id'],'text':u['text']} for u in units]
 if condition in {'E2','E2_k1','E2_k4','E2_no_association'}:return None
 if condition=='C0':return [text({'question':question})]
 base=[text({'question':question})]
 if condition=='D':return base+[text({'evidence':evidence}),text({'evidence':evidence})]
 if assets.get('verified') is not True or assets.get('evidence_sha')!=digest(units):raise RunnerBlocked('qualified_same_range_adapter_missing')
 if condition=='C1':
  pages=assets.get('full_page_units')
  if not isinstance(pages,list) or not pages or {u['doc_id'] for u in pages}!={u['doc_id'] for u in units}:raise RunnerBlocked('qualified_full_page_binding_missing')
  return base+[text({'evidence':[{'evidence_id':u['evidence_id'],'text':u['text']} for u in pages]})]
 if condition in {'E3','I'}:
  images=assets.get('images',[])
  if [i['evidence_id'] for i in images]!=[u['evidence_id'] for u in units]:raise RunnerBlocked('qualified_image_binding_missing')
  if condition=='E3':base.append(text({'evidence':evidence}))
  for image in images:
   url=image.get('url','')
   if not url.startswith(('data:image/png;base64,','data:image/jpeg;base64,')):raise RunnerBlocked('local_image_binding_required')
   base.extend([text({'evidence_id':image['evidence_id']}),{'type':'input_image','image_url':url,'detail':'high'}])
  return base
 if condition=='G':
  if not isinstance(assets.get('legacy_geometry'),str):raise RunnerBlocked('reviewed_legacy_geometry_missing')
  return base+[text({'evidence':evidence}),text({'legacy_geometry':assets['legacy_geometry']})]
 if condition in {'E4','L'}:
  records=assets.get('structures',[]);indexed={r['evidence_id']:r for r in records}
  if set(indexed)!={u['evidence_id'] for u in units}:raise RunnerBlocked('reviewed_structure_binding_missing')
  nodes={n['node_id']:n for r in records for n in r['nodes']}
  if len(nodes)!=sum(len(r['nodes']) for r in records):raise RunnerBlocked('duplicate_structure_node')
  projected=[]
  for unit in units:
   record=indexed[unit['evidence_id']]
   if record['evidence_sha']!=digest(unit) or any(' '.join(n['text'].split()) not in ' '.join(unit['text'].split()) for n in record['nodes']):raise RunnerBlocked('structure_evidence_mismatch')
   item={'evidence_id':unit['evidence_id'],'nodes':[{'node_id':n['node_id'],'text':n['text']} for n in record['nodes']]}
   if condition=='E4':
    if any(r['source'] not in nodes or r['target'] not in nodes for r in record['relations']):raise RunnerBlocked('structure_reference_outside_evidence')
    item['relations']=record['relations']
   projected.append(item)
  return base+[text({'evidence':evidence}),text({'structure':projected})]
 raise RunnerBlocked('condition_not_supported')

def demo_tasks(spec,questions):
 demo=spec['demo_input'];matches=[q for q in questions if q['doc_id']==demo['doc_id'] and q.get('split')=='development']
 if not matches:raise RunnerBlocked('development_document_required')
 exact=[q for q in matches if q['question']==demo['question']]
 q={**(exact or matches)[0],'question':demo['question']}
 if not exact:q['question_id']='demo-question'
 tasks=[]
 for c in demo['groups']:
  role='local' if c=='E1' else 'primary'
  tasks.append(dict(task_id=f"demo|{q['question_id']}|{demo['retriever']}|{c}|{role}|1",question_id=q['question_id'],partition='demo',retriever=demo['retriever'],condition=c,model_role=role,repeat=1,k=2,work_type='local_baseline' if role=='local' else 'generation'))
 return [q],tasks

def run(spec,emit,control,transport=None,*,run_dir=None):
 """Own one run before snapshots/writes. Callable control is sampled per task.

 run_dir is trusted internal context, never inferred from cwd or spec paths.
 Legacy control dictionaries may still bind their absolute run_dir.
 """
 binding=run_dir if run_dir is not None else control.get('run_dir') if isinstance(control,dict) else None
 if binding is None or not Path(binding).is_absolute():raise RunnerBlocked('absolute_run_directory_required')
 rd=Path(binding).resolve();rd.mkdir(parents=True,exist_ok=True)
 with (rd/'worker.lock').open('a+') as ownership:
  try:fcntl.flock(ownership,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return {'state':'blocked','blocking_items':['run_already_running']}
  try:
   return _run_owned(spec,emit,control,transport,rd)
  except Exception:
   value={'state':'failed','blocking_items':['worker_initialization_failed']};atomic(rd/'worker-result.json',value);return value
  finally:fcntl.flock(ownership,fcntl.LOCK_UN)


def input_package(rd):
 return input_projection(json.loads((rd/'inputs.json').read_text()))

def input_projection(projection):
 root=Path(projection['root']).resolve();bindings={};data={'review_policy':projection.get('review_policy','human_reviewed')}
 for b in projection['resources']:
  if b['kind'] not in {'input','model','approved_description'}:raise RunnerBlocked('inference_projection_invalid')
  p=(root/b['path']).resolve()
  if not p.is_relative_to(root) or not p.is_file() or sha(p)!=b['sha256']:raise RunnerBlocked('inference_asset_changed')
  if b['path'] in bindings:raise RunnerBlocked('duplicate_input_binding')
  bindings[b['path']]={**b,'absolute':p}
  if p.name in {'inference_questions.jsonl','evidence_units.jsonl','approved_structures.jsonl','draft_structures.jsonl'}:
   if p.name in data:raise RunnerBlocked('duplicate_input_role')
   data[p.name]=rows(p)
   for record in data[p.name]:safe_json(record)
  elif p.name in {'cohorts.json','scope_reviews.json'}:
   if p.name in data:raise RunnerBlocked('duplicate_input_role')
   data[p.name]=json.loads(p.read_text());safe_json(data[p.name])
  if p.name in {'scope_reviews.json','approved_structures.jsonl'} and b['kind']!='approved_description':raise RunnerBlocked('unapproved_description_binding')
 if not all(k in data for k in ('inference_questions.jsonl','evidence_units.jsonl')):raise RunnerBlocked('inference_inputs_missing')
 return data,bindings

def reviewed_assets(evidence,all_units,data,bindings,condition,asset_builder=None):
 import base64
 from .course_policy import automatic,course_assets
 if automatic(data):return asset_builder(evidence,condition) if asset_builder else course_assets(evidence,all_units,data,bindings,condition)
 assets={'verified':True,'evidence_sha':digest(evidence),'full_page_units':[u for u in all_units if u['doc_id'] in {x['doc_id'] for x in evidence}]}
 normalized=condition.removeprefix('M2_')
 if normalized in {'E2','E2_k1','E2_k4','E2_no_association','D','C0','C1'}:return assets
 reviews=data.get('scope_reviews.json',{}).get('reviews',[]);approved={r['evidence_id']:r for r in reviews}
 if len(approved)!=len(reviews):raise RunnerBlocked('duplicate_scope_review')
 structures={r['evidence_id']:r for r in data.get('approved_structures.jsonl',[])}
 if len(structures)!=len(data.get('approved_structures.jsonl',[])):raise RunnerBlocked('duplicate_approved_structure')
 assets['images']=[];assets['structures']=[]
 for u in evidence:
  review=approved.get(u['evidence_id'])
  if not review or review.get('unit_sha')!=digest(u) or review.get('qa_exposed') is not False or review.get('verified') is not True or review.get('scope_matches') is not True or not review.get('reviewer') or not review.get('reviewed_at'):raise RunnerBlocked('approved_blind_scope_review_missing')
  if normalized in {'E3','I'}:
   binding=bindings.get(review.get('image_path'))
   if not binding or binding['kind']!='input' or binding['sha256']!=review.get('image_sha'):raise RunnerBlocked('approved_same_range_image_missing')
   image=binding['absolute'].read_bytes();mime='image/png' if image.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if image.startswith(b'\xff\xd8') else None
   if mime is None:raise RunnerBlocked('unsupported_crop_format')
   assets['images'].append({'evidence_id':u['evidence_id'],'url':'data:'+mime+';base64,'+base64.b64encode(image).decode()})
  if normalized in {'E4','L'}:
   structure=structures.get(u['evidence_id'])
   if not structure or digest(structure)!=review.get('structure_sha') or structure.get('evidence_sha')!=digest(u):raise RunnerBlocked('approved_structure_sha_missing')
   assets['structures'].append(structure)
 if normalized in {'E4','L'}:
  from .structure import project_structure
  for record in structures.values():
   review=approved.get(record['evidence_id']);unit=next((u for u in all_units if u['evidence_id']==record['evidence_id']),None)
   if unit is None or not review or review.get('unit_sha')!=digest(unit) or review.get('structure_sha')!=digest(record) or record.get('evidence_sha')!=digest(unit) or review.get('qa_exposed') is not False or review.get('verified') is not True or review.get('scope_matches') is not True or not review.get('reviewer') or not review.get('reviewed_at'):raise RunnerBlocked('approved_structure_sha_missing')
  try:assets['structures']=project_structure(list(structures.values()),[u['evidence_id'] for u in evidence])
  except ValueError:raise RunnerBlocked('approved_structure_projection_invalid') from None
 if normalized=='G':
  from src.ragpilot.evidence import descriptions
  scoped=[];index=[]
  for u in evidence:
   for block in u.get('blocks',[]):
    if ' '.join(block.get('text','').split()) not in ' '.join(u['text'].split()):raise RunnerBlocked('legacy_index_text_outside_evidence')
    index.append({'evidence_id':u['evidence_id'],'block_id':block['block_id'],'text':block['text']})
   scoped.append({**u,'content_hash':u.get('content_hash',digest(u))})
  assets['legacy_geometry']=json.dumps({'index':index,'descriptions':[{'evidence_id':r['evidence_id'],'text':r['text']} for r in descriptions(scoped)]},sort_keys=True)

 return assets

def current_code_sha():
 """Identical recursive source map to ApplicationService._code_sha."""
 root=Path(__file__).resolve().parents[1]
 return digest({str(p.relative_to(root)):sha(p) for folder in ('formal_src','src','scripts') for p in sorted((root/folder).rglob('*.py'))})

def projected_model_root(bindings):
 roots={b['absolute'].parent for b in bindings.values() if b['kind']=='model' and b['absolute'].name=='modules.json'}
 if len(roots)!=1:raise RunnerBlocked('registered_local_model_missing' if not roots else 'ambiguous_local_model_binding')
 root=next(iter(roots));registered={b['absolute'] for b in bindings.values() if b['kind']=='model'}
 files={p.resolve() for p in root.rglob('*') if p.is_file()}
 if not files or not files<=registered or any(not p.is_relative_to(root) for p in files):raise RunnerBlocked('local_model_projection_incomplete')
 return str(root)

def formal_tasks(spec,data):
 from .run_plan import compile_run_plan,CANONICAL_PROFILE
 cohorts=data.get('cohorts.json')
 if cohorts is None:raise RunnerBlocked('cohort_binding_missing')
 expected=compile_run_plan(CANONICAL_PROFILE,cohorts,execution_scope=spec['plan'].get('execution_scope','full'))
 for key in ('units','cohort_hash','logical_generation_count','local_baseline_count','condition_expectations','retrieval_work'):
  if spec['plan'].get(key)!=expected[key]:raise RunnerBlocked('formal_recipe_identity_mismatch')
 if spec['logical_generation_count']!=expected['logical_generation_count']:raise RunnerBlocked('formal_logical_count_mismatch')
 if spec['purpose']!='software_test':
  config=spec['resolved_config']
  from .profile_lifecycle import validate_frozen_profile,validate_models
  from .provider import INSTRUCTIONS
  try:validate_frozen_profile(config,spec['freeze_sha'])
  except ValueError:raise RunnerBlocked('frozen_generation_protocol_invalid') from None
  try:validate_models(config.get('models'),{t['model_role'] for t in expected['units'] if t['work_type']=='generation'})
  except ValueError as exc:raise RunnerBlocked(str(exc)) from None
  if config['prompt_sha']!=hashlib.sha256(INSTRUCTIONS.encode()).hexdigest():raise RunnerBlocked('prompt_identity_mismatch')
  if spec['code_sha']!=current_code_sha():raise RunnerBlocked('actual_code_identity_mismatch')
  if config.get('freeze_sha')!=spec['freeze_sha'] or digest({**config,'freeze_sha':None})!=spec['freeze_sha']:raise RunnerBlocked('freeze_identity_mismatch')
  allowed={'status','models','provider','prompt_sha','resource_manifest_sha','freeze_sha','readiness','code_sha','concurrency','audit_plan_sha256','review_policy'}
  if set(config)-set(CANONICAL_PROFILE)-{'code_sha','concurrency','audit_plan_sha256','review_policy'}:raise RunnerBlocked('formal_profile_identity_mismatch')
  if any(config.get(k)!=v for k,v in CANONICAL_PROFILE.items() if k not in allowed):raise RunnerBlocked('formal_profile_identity_mismatch')
  if config.get('code_sha')!=spec['code_sha'] or config.get('resource_manifest_sha')!=spec['resource_sha']:raise RunnerBlocked('formal_snapshot_identity_mismatch')
  if not spec['freeze_sha'] or config.get('status')!='frozen' or not spec['plan'].get('executable') or not readiness_valid(config):raise RunnerBlocked('qualified_frozen_spec_required')
  for key in ('cohort_sizes','retrievers','core_conditions','auxiliary_conditions','k','repeats','seed','metrics','budgets'):
   if config.get(key)!=CANONICAL_PROFILE[key]:raise RunnerBlocked('formal_profile_identity_mismatch')
  if spec['plan'].get('profile_hash')!=expected['profile_hash']:raise RunnerBlocked('formal_profile_identity_mismatch')
 indexed={q['question_id']:q for q in data['inference_questions.jsonl']}
 if len(indexed)!=len(data['inference_questions.jsonl']):raise RunnerBlocked('duplicate_question_identity')
 for split,ids in cohorts.items():
  if split=='s50':continue
  for qid in ids:
   if qid not in indexed or indexed[qid].get('split')!=split or not indexed[qid].get('source_id'):raise RunnerBlocked('cohort_input_identity_mismatch')
 return expected['units']

def _run_owned(spec,emit,control,transport,rd):
 import threading,random,concurrent.futures,subprocess,sys
 from collections import Counter,deque
 spec=ResolvedRunSpec.from_dict(spec).to_dict()
 from .course_policy import automatic
 from .retry_policy import code_for,retryable
 course_retry=automatic(spec['resolved_config'])
 if transport is not None and spec['purpose']!='software_test':raise RunnerBlocked('injected_transport_requires_software_test')
 pending=latest(rd/'attempts.jsonl');counts={'attempt_count':len(pending),'in_flight_count':0,'failure_count':sum(bool(r.get('error_code')) or r.get('parse_status') in {'failed','parse_error','truncated'} for r in pending.values()),'completed_logical_count':0,'completed_local_count':0,'reuse_count':0,'retry_count':0};guard=threading.Lock();technical=deque((bool(r.get('error_code')) or r.get('parse_status') in {'failed','parse_error','truncated'} for r in pending.values() if r['state']=='completed'),maxlen=100)
 def event(kind,phase='generation'):
  with guard:emit({'event_type':kind,'phase':phase,'counts':dict(counts),'error_code':None})
 def finish(state,blocking=()):
  event('worker_stopped');value={'state':state,'blocking_items':list(blocking)};atomic(rd/'worker-result.json',value);return value
 def action():
  result=control() if callable(control) else json.loads((rd/'control.json').read_text()).get('action') if (rd/'control.json').exists() else control.get('action','continue')
  if result not in {'continue','pause','cancel',None}:raise RunnerBlocked('invalid_control_action')
  return result
 try:
  repair_prediction(rd);settle_receipts(rd);pending=latest(rd/'attempts.jsonl')
  technical.clear();technical.extend(bool(r.get('error_code')) or r.get('parse_status') in {'failed','parse_error','truncated'} for r in pending.values() if r['state']=='completed')
  # Explicitly restarting a course worker opens a fresh health window; the durable ledger stays intact.
  if course_retry:technical.clear()
  counts['failure_count']=sum(bool(r.get('error_code')) or r.get('parse_status') in {'failed','parse_error','truncated'} for r in pending.values())
  for attempt in pending.values():
   if fatal_provider_failure(attempt.get('error_code')) and not (course_retry and retryable(attempt.get('error_code'))):raise RunnerBlocked(attempt['error_code'])
  if any(r['state'] in {'sent','unknown'} for r in pending.values()):raise RunnerBlocked('unknown_attempt_requires_inspection')
  if spec['purpose']!='software_test' and spec['kind'] in {'demo','experiment'} and spec['code_sha']!=current_code_sha():raise RunnerBlocked('actual_code_identity_mismatch')
  if spec['kind']=='verify':
   event('evaluation_started','scoring')
   result=evaluate_subprocess(rd);return finish(result['state'],result.get('blocking_items',[]))
  if spec['kind']=='prepare':
   output=spec['resolved_config'].get('prepare_output')
   if not output or not Path(output).is_absolute():raise RunnerBlocked('prepare_output_binding_missing')
   script=Path(__file__).resolve().parents[1]/'scripts/prepare_formal.py'
   if not script.exists():raise RunnerBlocked('curator_subprocess_missing')
   # Building existing acquired inputs only; acquisition is separately authorized.
   if not (Path(output)/'inputs/documents.jsonl').exists():raise RunnerBlocked('prepare_source_documents_missing')
   event('curator_started','prepare')
   proc=subprocess.run([sys.executable,str(script),'--output',output,'--build-inputs'],env={k:v for k,v in os.environ.items() if k!='FORMAL_API_KEY'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   return finish('completed' if proc.returncode==0 else 'blocked',[] if proc.returncode==0 else ['curator_build_failed'])
  data,bindings=input_package(rd)
  if spec['kind']=='experiment' and data.get('review_policy','human_reviewed')!=spec['resolved_config'].get('review_policy','human_reviewed'):raise RunnerBlocked('review_policy_identity_mismatch')
  questions=data['inference_questions.jsonl'];units=data['evidence_units.jsonl']
  if spec['kind']=='demo':questions,tasks=demo_tasks(spec,questions)
  elif spec['kind']=='experiment':tasks=formal_tasks(spec,data)
  else:raise RunnerBlocked('unsupported_run_kind')
  concurrency=1 if spec['kind']=='demo' else spec['resolved_config'].get('concurrency',1)
  if type(concurrency) is not int or not 1<=concurrency<=32:raise RunnerBlocked('verified_concurrency_invalid')
  task_sha=digest(tasks);input_sha=sha(rd/'inputs.json');identity={'spec_sha':digest(spec),'task_sha':task_sha,'input_sha':input_sha}
  if (rd/'execution-identity.json').exists() and json.loads((rd/'execution-identity.json').read_text())!=identity:raise RunnerBlocked('execution_identity_changed')
  atomic(rd/'execution-identity.json',identity)
  if (rd/'tasks.json').exists() and json.loads((rd/'tasks.json').read_text())!=tasks:raise RunnerBlocked('task_output_changed')
  atomic(rd/'tasks.json',tasks)
  prior=rows(rd/'predictions.jsonl') if (rd/'predictions.jsonl').exists() else [];done={r['task_id']:AnswerRecord.from_dict(r).to_dict() for r in prior};task_ids={t['task_id'] for t in tasks}
  if len(done)!=len(prior) or set(done)-task_ids:raise RunnerBlocked('prediction_identity_mismatch')
  checks=rows(rd/'prediction-checksums.jsonl') if (rd/'prediction-checksums.jsonl').exists() else []
  if len(checks)!=len(prior) or any(check!={'task_id':record['task_id'],'sha256':digest(record)} for check,record in zip(checks,prior)):raise RunnerBlocked('prediction_output_changed')
  if (rd/'sealed.json').exists():
   sealed=json.loads((rd/'sealed.json').read_text())
   if sealed.get('prediction_sha256')!=sha(rd/'predictions.jsonl') or sealed.get('input_sha256')!=input_sha:raise RunnerBlocked('sealed_output_changed')
  qindex={q['question_id']:q for q in questions};byid={u['evidence_id']:u for u in units}
  if len(byid)!=len(units):raise RunnerBlocked('duplicate_evidence_identity')
  edges=build_associations(units);retrieval_cache={};prepared={};encoders={}
  from .preparation import PreparationCache
  from .course_policy import CourseAssetBuilder
  checkpoints=PreparationCache(rd,{'code_sha':spec['code_sha'],'input_sha':input_sha,'config_sha':digest(spec['resolved_config'])})
  asset_builder=CourseAssetBuilder(units,data,bindings)
  bydoc={}
  for unit in units:bydoc.setdefault(unit['doc_id'],[]).append(unit)
  counts['completed_logical_count']=sum(r['model_role']!='local' for r in done.values());counts['completed_local_count']=len(done)-counts['completed_logical_count']
  event('preparing_0_of_'+str(len(tasks)),'prepare')
  # Prepare/freeze all common evidence and every variant before the first send.
  def retrieve_cached(q,retriever,k,variant=None):
   cfg={'retriever':retriever,'k':k,**spec['resolved_config'].get('retrieval',{}).get(retriever,{})}
   if variant:cfg['variant']=variant;cfg['tokenizer']=variant if retriever=='bm25' else cfg.get('tokenizer','finance')
   key=(q['question_id'],digest(cfg))
   if key not in retrieval_cache:
    def build_retrieval():
     encoder=None
     if retriever=='minilm':
      from .retrieval import LocalEncoder
      path=cfg.get('model_path','') if spec['purpose']=='software_test' else projected_model_root(bindings)
      encoders.setdefault(path,None)
      if encoders[path] is None:encoders[path]=LocalEncoder(path)
      encoder=encoders[path]
     result=retrieve(q,bydoc.get(q['doc_id'],[]),cfg,encoder=encoder)
     append(rd/'retrieval.jsonl',result);return result
    retrieval_cache[key]=checkpoints.get(['retrieval',q,cfg],build_retrieval)
   return retrieval_cache[key]
  for task_index,task in enumerate(tasks):
   # Pause is honored during preparation, before another potentially expensive item.
   if not callable(control):
    requested=action()
    if requested in {'pause','cancel'}:return finish('paused' if requested=='pause' else 'cancelled')
   q=qindex[task['question_id']]
   def build_context():
    r=retrieve_cached(q,task['retriever'],task['k']);selected=r['selected_ids'] if task['condition']=='E2_no_association' else expand_once(r['selected_ids'],bydoc.get(q['doc_id'],[]),edges);evidence=[byid[e] for e in selected]
    context={'q':q,'evidence':evidence,'evidence_sha':digest(evidence)}
    if task['work_type']=='generation':
     assets=reviewed_assets(evidence,units,data,bindings,task['condition'],asset_builder);content=condition_content(task['condition'],q['question'],evidence,assets)
     config={**(spec['resolved_config'].get('provider') or {}),'model':spec['resolved_config'].get('models',{}).get(task['model_role'])};validate_config(config)
     payload=build_payload(config,q['question'],evidence,content=content);context.update(config=config,payload=payload,request_hash=digest(payload),selected_ids=selected)
     if task['condition']=='C1':context['selected_ids']=[u['evidence_id'] for u in assets['full_page_units']]
     if task['condition']=='C0':context['selected_ids']=[]
    else:context.update(request_hash=digest({'question':q,'evidence':evidence,'model':task['condition']}))
    return context
   key=['context',task['question_id'],task['retriever'],task['condition'],task['model_role'],task['k']]
   context={**checkpoints.get(key,build_context),'task':task};prepared[task['task_id']]=context
   if task['task_id'] in done:
    record=done[task['task_id']]
    if record['evidence_sha']!=context['evidence_sha'] or record['request_hash']!=context['request_hash'] or record['run_id']!=spec['run_id']:raise RunnerBlocked('prediction_content_identity_changed')
   if task_index==0 or (task_index+1)%10==0 or task_index+1==len(tasks):event('preparing_'+str(task_index+1)+'_of_'+str(len(tasks)),'prepare')
  # Registered local diagnostics are independent of answer outcomes.
  if spec['kind']=='experiment':
   for work in spec['plan']['retrieval_work']:
    for qid in work['question_ids']:retrieve_cached(qindex[qid],work['retriever'],work['k'],work['variant'])
  atomic(rd/'evidence-freeze.json',{'input_sha':input_sha,'tasks':{tid:{'ids':[u['evidence_id'] for u in c['evidence']],'sha':c['evidence_sha']} for tid,c in prepared.items()}})
  event('concurrency_'+str(concurrency),'generation');cache={}
  def reuse_key(c):return (c['config']['endpoint'],c['request_hash'],c['task']['repeat'])
  for tid,record in done.items():
   if record['provenance']!='local':cache.setdefault(reuse_key(prepared[tid]),record)
  def save(context,result,refs=(),usage=None,provenance='live',source=None,elapsed=0):
   t=context['task'];q=context['q'];model=context.get('config',{}).get('model') or ('legacy-e1' if t['condition']=='E1_legacy' else 'formal-e1-v1')
   record=AnswerRecord.from_dict(dict(schema_version=1,run_id=spec['run_id'],question_id=q['question_id'],source_id=q.get('source_id',q['doc_id']),retriever=t['retriever'],model=model,model_role=t['model_role'],condition=t['condition'],repeat=t['repeat'],task_id=t['task_id'],evidence_sha=context['evidence_sha'],status=result['status'],answer=result['answer'],scale=result.get('scale') or '',citations=result['citations'],request_hash=context['request_hash'],attempt_refs=list(refs),elapsed_ms=elapsed,usage=usage or {'cost_usd':None},provenance=provenance,reuse_source_task_id=source)).to_dict()
   commit_prediction(rd,record,digest(record));done[t['task_id']]=record
   with guard:
    counts['completed_local_count' if t['model_role']=='local' else 'completed_logical_count']+=1
    counts['reuse_count']+=provenance=='same_round_reuse'
   event('logical_unit_completed');return record
  budget=Budget(rd.parent.parent/'budget.jsonl');dispatch_state={'action':None,'unknown':False,'rolling':False,'fatal':None,'peak':0}
  class DispatchStopped(Exception):pass
  def permission():
   requested=action()
   with guard:
    if requested in {'pause','cancel'}:dispatch_state['action']=requested
    if len(technical)==100 and sum(technical)>5:dispatch_state['rolling']=True;dispatch_state['action']='pause'
    if dispatch_state['fatal']:return 'fatal'
    if dispatch_state['unknown']:return 'unknown'
    return dispatch_state['action']
  def retry_pending(c):
   related=[a for a in pending.values() if a['task_id']==c['task']['task_id'] and a['state']!='reserved']
   return course_retry and 0<len(related)<3 and related[-1]['state']=='completed' and retryable(related[-1].get('error_code'))
  def execute(c):
   related=[a for a in pending.values() if a['task_id']==c['task']['task_id'] and a['state']!='reserved'] if course_retry else []
   if any(a['request_hash']!=c['request_hash'] for a in related):raise RunnerBlocked('recovery_request_changed')
   refs=[a['attempt_id'] for a in related];started=time.monotonic();parsed=None
   for retry in range(len(refs),3 if course_retry else 2):
    if course_retry and retry:
     event('retry_wait_'+str(retry))
     deadline=time.monotonic()+(0 if transport else 2**retry)
     while time.monotonic()<deadline:
      if permission():raise DispatchStopped()
      time.sleep(min(.2,max(0,deadline-time.monotonic())))
    if permission():
     if not refs or course_retry:raise DispatchStopped()
     parsed={'status':'failed','parsed_answer':None,'scale':'','citations':[],'usage':{'cost_usd':None}};break
    category='retry_demo' if retry or spec['kind']=='demo' and spec['purpose']!='development' else 'formal' if spec['kind']=='experiment' else 'development_validation'
    reservation=budget.reserve(spec['run_id'],c['task']['task_id'],c['request_hash'],category);append(rd/'attempts.jsonl',reservation);refs.append(reservation['attempt_id'])
    with guard:counts['attempt_count']+=1;counts['retry_count']+=bool(retry)
    event('attempt_reserved')
    if permission():
     if retry==0 or course_retry:raise DispatchStopped()
     parsed={'status':'failed','parsed_answer':None,'scale':'','citations':[],'usage':{'cost_usd':None}};break
    with guard:
     counts['in_flight_count']+=1;dispatch_state['peak']=max(dispatch_state['peak'],counts['in_flight_count'])
    sent=budget.transition(reservation,'sent');append(rd/'attempts.jsonl',sent);event('attempt_sent')
    parsed=None;error=None;unknown=False;diagnostics={};request_started=time.monotonic()
    try:
     parsed=generate(c['config'],c['payload'],os.environ.get('FORMAL_API_KEY',''),c['selected_ids'],transport)
    except APIError as exc:
     error=code_for(exc) if course_retry else str(exc) if str(exc).startswith('http_') else 'transport_outcome_unknown'
     unknown=not course_retry and error=='transport_outcome_unknown'
     from .http_transport import TransportFailure
     diagnostics=dict(exc.diagnostics) if isinstance(exc,TransportFailure) else {}
     diagnostics['exception_type']=type(exc).__name__
    except ProviderError as exc:
     error=str(exc) if str(exc) in PROVIDER_FAILURES else 'provider_validation_failed'
     diagnostics={'exception_type':type(exc).__name__}
    except Exception as exc:
     error=code_for(exc) if course_retry else 'transport_outcome_unknown';unknown=not course_retry
     diagnostics={'exception_type':type(exc).__name__}
    if course_retry and not error and parsed and parsed['status'] in {'parse_error','truncated','api_error'}:error=parsed['status']
    if error:
     from datetime import datetime,timezone
     append(rd/'request-errors.jsonl',{'time':datetime.now(timezone.utc).isoformat(),'attempt_id':reservation['attempt_id'],'task_id':c['task']['task_id'],'error_code':error,'elapsed_ms':int((time.monotonic()-request_started)*1000),'attempt_number':retry+1,'retryable':retryable(error),'diagnostics':diagnostics})
     with guard:emit({'event_type':'request_error','phase':'generation','counts':dict(counts),'error_code':error})
    if not unknown:save_receipt(rd,sent,parsed,error)
    if fatal_provider_failure(error) and not (course_retry and retryable(error)):
     with guard:dispatch_state['fatal']=error
    failed=error is not None or parsed and parsed['status'] in {'parse_error','truncated','api_error'}
    outcome={**budget.transition(reservation,'unknown' if unknown else 'completed'),'error_code':error,'response_id':parsed.get('response_id') if parsed else None,'usage':parsed.get('usage') if parsed else None,'parse_status':parsed.get('status') if parsed else 'failed',**({'remote_outcome':'unknown' if error.startswith('transport_') or error.startswith('stream_') else 'response_received','local_disposition':'retry_or_record_failure'} if course_retry and error and retryable(error) else {})}
    append(rd/'attempts.jsonl',outcome)
    with guard:
     counts['in_flight_count']-=1;counts['failure_count']+=bool(failed);technical.append(bool(failed))
     if unknown:dispatch_state['unknown']=True
    event('attempt_finished')
    if unknown:raise RunnerBlocked('unknown_attempt_requires_inspection')
    if course_retry and retryable(error) and retry<2:continue
    if not course_retry and error in {'http_429','http_500','http_502','http_503','http_504'} and retry==0:continue
    if error:parsed={'status':'failed','parsed_answer':None,'scale':'','citations':[],'usage':{'cost_usd':None}}
    break
   result={'status':{'rejection':'refused'}.get(parsed['status'],parsed['status']),'answer':parsed['parsed_answer'],'scale':parsed.get('scale') or '', 'citations':parsed['citations']}
   return result,refs,{**parsed['usage'],'cost_usd':None},int((time.monotonic()-started)*1000)
  ordered=list(tasks)
  if spec['kind']=='experiment':
   rng=random.Random(20261023);ordered=[]
   for repeat in (1,2,3):
    group=sorted([t for t in tasks if t['repeat']==repeat],key=lambda t:t['task_id']);rng.shuffle(group);ordered.extend(group)
  atomic(rd/'dispatch-order.json',[t['task_id'] for t in ordered])
  stop=None;errors=[]
  with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
   futures={};inflight_keys={}
   def drain_one():
    ready,_=concurrent.futures.wait(futures,return_when=concurrent.futures.FIRST_COMPLETED)
    for future in ready:
     c=futures.pop(future);key=reuse_key(c);inflight_keys.pop(key,None)
     try:
      result,refs,usage,elapsed=future.result();record=save(c,result,refs,usage,elapsed=elapsed);cache[key]=record
     except DispatchStopped:pass
     except (RunnerBlocked,BudgetError) as exc:errors.append(str(exc))
   for task in ordered:
    if task['task_id'] in done:continue
    with guard:blocked_dispatch=dispatch_state['fatal'] or dispatch_state['unknown']
    if errors or blocked_dispatch:break
    requested=action()
    if requested in {'pause','cancel'}:stop=requested;break
    with guard:rolling=len(technical)==100 and sum(technical)>5
    if rolling:stop='pause';errors.append('rolling_failure_threshold');break
    c=prepared[task['task_id']]
    retry_saved=retry_pending(c)
    restored=None if retry_saved else restored_result(rd,pending,task['task_id'],c['request_hash'])
    if restored:
     result,refs,usage,elapsed=restored;record=save(c,result,refs,usage,elapsed=elapsed);cache[reuse_key(c)]=record;continue
    if not retry_saved and any(r.get('task_id')==task['task_id'] and r['state']=='completed' for r in pending.values()):errors.append('completed_attempt_prediction_missing');break
    if task['work_type']=='local_baseline':
     result=extract(c['q']['question'],[(u['evidence_id'],u['text']) for u in c['evidence']],legacy=task['condition']=='E1_legacy');save(c,result,provenance='local');continue
    if not transport and not os.environ.get('FORMAL_API_KEY'):errors.append('credential_required');break
    key=reuse_key(c)
    if key in inflight_keys:
     while key in inflight_keys:drain_one()
    with guard:blocked_dispatch=dispatch_state['fatal'] or dispatch_state['unknown']
    if blocked_dispatch:break
    if key in cache:
     r=cache[key];save(c,r,r['attempt_refs'],{'cost_usd':None},'same_round_reuse',r['task_id']);continue
    while len(futures)>=concurrency:drain_one()
    if errors:break
    requested=permission()
    if requested:
     stop=dispatch_state['action'];break
    future=pool.submit(execute,c);futures[future]=c;inflight_keys[key]=future
   while futures:drain_one()
  stop=dispatch_state['action'] or stop
  if dispatch_state['rolling'] and 'rolling_failure_threshold' not in errors:errors.append('rolling_failure_threshold')
  if dispatch_state['unknown'] and 'unknown_attempt_requires_inspection' not in errors:errors.append('unknown_attempt_requires_inspection')
  if dispatch_state['fatal'] and dispatch_state['fatal'] not in errors:errors.append(dispatch_state['fatal'])
  if dispatch_state['fatal']:return finish('blocked',errors)
  if errors:return finish('paused' if stop=='pause' and not dispatch_state['unknown'] else 'blocked',errors)
  if stop:return finish('paused' if stop=='pause' else 'cancelled')
  if set(done)!=task_ids:raise RunnerBlocked('incomplete_predictions')
  output_sha=sha(rd/'predictions.jsonl');parts=Counter(t['partition'] for t in tasks)
  summary=RunResults.from_dict(dict(schema_version=1,run_id=spec['run_id'],input_sha=input_sha,output_sha=output_sha,evaluation_sha='0'*64,partitions=dict(parts),denominators={'logical_generation_count':sum(t['work_type']=='generation' for t in tasks),'local_baseline_count':sum(t['work_type']=='local_baseline' for t in tasks)},metrics={},main_comparisons=[],engineering={'purpose':spec['purpose'],'synthetic':transport is not None,'attempt_count':counts['attempt_count'],'failure_count':counts['failure_count'],'reuse_count':counts['reuse_count'],'concurrency':concurrency,'evaluation_status':'not_evaluated','cost_usd':None},audit_complete=False,answer_tables=['predictions.jsonl'],exports=[])).to_dict()
  atomic(rd/'summary.json',summary);atomic(rd/'results.json',summary)
  atomic(rd/'sealed.json',{'schema_version':1,'run_id':spec['run_id'],'prediction_sha256':output_sha,'input_sha256':input_sha,'task_sha256':sha(rd/'tasks.json'),'purpose':spec['purpose'],'synthetic':transport is not None,'completed_tasks':len(tasks)})
  from .provenance import write_result_proof
  write_result_proof(rd)
  event('inference_sealed')
  if spec['kind']=='experiment' and spec['purpose']!='software_test':
   event('evaluation_started','scoring')
   result=evaluate_subprocess(rd);return finish(result['state'],result.get('blocking_items',[]))
  return finish('completed')
 except (RunnerBlocked,BudgetError,ProviderError,ValueError) as exc:return finish('blocked',[str(exc)])
 except Exception:return finish('failed',['worker_internal_error'])

def evaluate_subprocess(rd):
 import subprocess,sys
 module=Path(__file__).with_name('verification.py')
 if not module.exists():return {'state':'blocked','blocking_items':['evaluation_pending']}
 env={k:v for k,v in os.environ.items() if k!='FORMAL_API_KEY'}
 proc=subprocess.run([sys.executable,'-m','formal_src.verification','--run-dir',str(rd)],cwd=Path(__file__).resolve().parents[1],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 if proc.returncode:return {'state':'blocked','blocking_items':['evaluation_pending']}
 return {'state':'completed','blocking_items':[]}
