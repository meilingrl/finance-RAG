"""The shared CLI/HTTP application facade. No experiment algorithm lives here."""
import json,sys
from pathlib import Path
from uuid import uuid4
from formal_src.contracts import RunRequest,ResolvedRunSpec,PreflightReport,RunResults,safe_json,identifier,ContractError
from .jobs import JobStore,atomic,read,digest
from . import fsutil
from .resources import ResourceRegistry
from .onboarding import CourseWorkflow


def catalog_urls(endpoint):
 """Model-list URLs derived from a complete model-service endpoint.

 The application request path ends in ``/responses`` or ``/chat/completions``, so
 the model catalog normally lives one level up. Users may also paste a base URL
 or a versioned path; both are handled instead of rejected.
 """
 from formal_src.provider import normalize_endpoint
 from urllib.parse import urlsplit,urlunsplit
 complete=normalize_endpoint(endpoint)[0]
 url=urlsplit(complete);path=url.path.rstrip('/')
 suffix='/chat/completions' if path.endswith('/chat/completions') else '/responses'
 if path.endswith(suffix):base=path[:-len(suffix)].rstrip('/')
 else:base=path.rstrip('/')
 candidates=[]
 def add(value):
  candidate=urlunsplit((url.scheme,url.netloc,value,'',''))
  if candidate not in candidates:candidates.append(candidate)
 add(base+'/models')
 if not base.endswith('/v1'):add(base+'/v1/models')
 return candidates


class ApplicationService(CourseWorkflow):
 def __init__(self,project_root,workspace,*,worker_command=None,catalog_transport=None,provider_transport=None):
  self.catalog_transport=catalog_transport;self.provider_transport=provider_transport
  self.project_root=Path(project_root).resolve();self.workspace=Path(workspace).resolve();self.jobs=JobStore(workspace);self.resources=ResourceRegistry(workspace);self.credentials={};self.session_id=str(uuid4());self.worker_command=worker_command;self.jobs.recover()
  self._init_course_workflow()
 def _profile(self,pid):
  identifier(pid);p=self.workspace/'profiles'/f'{pid}.json'
  if p.exists():return read(p)
  p=self.project_root/'configs/formal'/f'{pid}.json'
  if p.exists():return read(p)
  if pid=='formal-v1':return {'profile_id':pid,'status':'draft','purpose':'formal'}
  raise ValueError('Profile missing')
 def _worker(self):
  if self.worker_command:return self.worker_command
  p=self.project_root/'scripts/run_formal.py';return [sys.executable,str(p)] if p.is_file() else None
 def capabilities(self):return dict(schema_version=1,kinds=['prepare','demo','experiment','verify'],kernel_available=bool(self._worker()),session_id=self.session_id,workspace=str(self.workspace),settings=read(self.workspace/'settings.json',{}),profiles=[self._profile('formal-v1')]+[read(p) for p in sorted((self.workspace/'profiles').glob('custom-*.json'))])
 def _clean(self,values):
  values=safe_json(values)
  from urllib.parse import urlsplit
  def inspect(value):
   if isinstance(value,dict):
    for item in value.values():inspect(item)
   elif isinstance(value,list):
    for item in value:inspect(item)
   elif isinstance(value,str):
    if any(secret in value for secret in self.credentials.values()):raise ValueError('Sensitive value forbidden')
    if value.startswith(('http://','https://')):
     u=urlsplit(value)
     if u.username or u.password or u.query or u.fragment:raise ValueError('Sensitive URL forbidden')
  inspect(values);return values
 def save_settings(self,values):
  values=self._clean(values)
  if set(values)-{'base_url','model','secondary_model','price','output_directory','timeout_seconds'}:raise ValueError('Unknown settings field')
  if 'base_url' in values:
   from urllib.parse import urlsplit
   u=urlsplit(values['base_url'])
   if u.username or u.password or u.query or u.fragment:raise ValueError('Credentials forbidden in URL')
  selection={}
  if 'output_directory' in values:
   from .workspace import configure_workspace
   selection=configure_workspace(self.project_root,values['output_directory'],self.workspace)
  merged=read(self.workspace/'settings.json',{});merged.update(values)
  atomic(self.workspace/'settings.json',merged);return {**merged,**selection}
 def save_profile(self,profile_id,values):
  current=self._profile(profile_id);values=self._clean(values)
  if profile_id=='formal-v1' or current.get('status')=='frozen':raise ValueError('Immutable preset; clone to custom')
  if set(values)&{'profile_id','purpose','status','source_profile_id'}:raise ValueError('Profile identity immutable')
  values=self._normalize_model_values(current,values)
  current.update(values);atomic(self.workspace/'profiles'/f'{profile_id}.json',current);return current
 def clone_profile(self,source_profile_id,values):
  source=self._profile(source_profile_id);values=self._clean(values)
  if set(values)&{'profile_id','purpose','status','source_profile_id'}:raise ValueError('Profile identity reserved')
  values=self._normalize_model_values(source,values)
  source.update(values,profile_id='custom-'+str(uuid4()),purpose='custom',status='draft',source_profile_id=source_profile_id);atomic(self.workspace/'profiles'/(source['profile_id']+'.json'),source);return source
 def _normalize_model_values(self,current,values):
  if isinstance(values.get('provider'),dict):
   model=values.get('models',current.get('models',{})).get('primary')
   if model:
    from .provider_checks import provider_values
    return {**values,'provider':provider_values({**values['provider'],'model':model})}
  return values
 def set_credential(self,api_key):
  if not isinstance(api_key,str) or not api_key.strip():raise ValueError('Credential required')
  ref=str(uuid4());self.credentials[ref]=api_key;return ref
 def check_provider(self,settings,credential_ref,level):
  settings=self._clean(settings)
  if level=='compatibility':
   from .provider_checks import check_compatibility
   return check_compatibility(self,settings,credential_ref)
  if level not in ('catalog','generation'):raise ValueError('Invalid check level')
  key=self.credentials.get(credential_ref)
  if not key:return {'ready':False,'blocking_items':['credential_required'],'sent_attempts':0}
  from formal_src.provider import normalize_config,build_probe_payload,generate,digest as request_digest
  from urllib.parse import urlsplit
  config=normalize_config({**settings,'max_output_tokens':settings.get('max_output_tokens',2048),'reserve_input_tokens':settings.get('reserve_input_tokens',32768)})
  endpoint=config['endpoint']
  if level=='catalog':
   from urllib.parse import urlsplit,urlunsplit
   url=urlsplit(endpoint);suffix='/chat/completions' if config['api_style']=='chat_completions' else '/responses'
   base=url.path[:-len(suffix)]
   catalog=urlunsplit((url.scheme,url.netloc,base.rstrip('/')+'/models','',''))
   if self.catalog_transport:raw=self.catalog_transport(catalog,key,settings.get('timeout_seconds',30))
   else:
    import urllib.request
    req=urllib.request.Request(catalog,headers={'Authorization':'Bearer '+key})
    from scripts.llm_execution import NoRedirect
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=settings.get('timeout_seconds',30)) as response:raw=json.load(response)
   ready=any(item.get('id')==config['model'] for item in raw.get('data',[]))
   models=[item['id'] for item in raw.get('data',[]) if isinstance(item,dict) and isinstance(item.get('id'),str) and item['id']]
   return {'ready':ready,'blocking_items':[] if ready else ['model_not_in_catalog'],'sent_attempts':0,'level':'catalog','catalog_url':catalog,'models':models}
  from formal_src.budget import Budget
  payload=build_probe_payload(config,image_url=settings.get('_probe_image'),nonce=settings.get('_probe_marker','READY'))
  operation=str(uuid4());budget=Budget(self.workspace/'budget.jsonl',run_id=operation)
  reservation=budget.reserve(operation,'operational-synthetic-probe',request_digest(payload),'development_validation')
  budget.transition(reservation,'sent')
  try:
   result=generate(config,payload,key,['operational-probe'],transport=self.provider_transport)
   budget.transition(reservation,'completed')
  except Exception as exc:
   from formal_src.provider import ProviderError,APIError
   known=isinstance(exc,ProviderError) or isinstance(exc,APIError) and str(exc).startswith('http_')
   budget.transition(reservation,'completed' if known else 'unknown')
   code=str(exc) if known else 'transport_outcome_unknown'
   if not code.replace('_','').isalnum() or len(code)>80:code='provider_generation_failed'
   return {'ready':False,'blocking_items':[code],'sent_attempts':1,'level':'generation','purpose':'operational_probe'}
  ready=result.get('status')=='answered' and result.get('parsed_answer')==settings.get('_probe_marker','READY')
  return {'ready':ready,'blocking_items':[] if ready else ['operational_marker_not_answered'],'sent_attempts':1,'level':'generation','purpose':'operational_probe','model':config['model'],'response_id':result.get('response_id')}
 def available_models(self,endpoint,credential_ref):
  """List the service's model IDs so the form can fill in a valid name."""
  key=self.credentials.get(credential_ref)
  if not key:return {'models':[],'catalog_url':None,'blocking_items':['credential_required']}
  candidates=catalog_urls(endpoint)
  errors=[]
  for catalog in candidates:
   try:
    if self.catalog_transport:raw=self.catalog_transport(catalog,key,15)
    else:
     import urllib.request
     from scripts.llm_execution import NoRedirect
     request=urllib.request.Request(catalog,headers={'Authorization':'Bearer '+key,'User-Agent':'rag-pilot/1.0'})
     with urllib.request.build_opener(NoRedirect()).open(request,timeout=15) as response:raw=json.load(response)
   except Exception as exc:
    code=getattr(exc,'code',None)
    errors.append('http_'+str(code) if code else type(exc).__name__)
    continue
   rows=raw.get('data') if isinstance(raw,dict) else None
   if not isinstance(rows,list):errors.append('catalog_schema_error');continue
   models=[item['id'] for item in rows if isinstance(item,dict) and isinstance(item.get('id'),str) and item['id']]
   return {'models':models,'catalog_url':catalog,'blocking_items':[] if models else ['model_catalog_empty']}
  return {'models':[],'catalog_url':None,'blocking_items':['model_catalog_unavailable'],'attempts':len(candidates),'causes':errors}
 def parallel_verified(self,profile):
  from .provider_checks import parallel_verified
  return parallel_verified(self.workspace,profile)
 def register_resources(self,root_path,review_policy=None):return self.resources.register(root_path,review_policy)
 def check_resources(self,resource_set_id):return self.resources.check(resource_set_id)
 def import_reviews(self,resource_set_id,review_manifest):return self.resources.import_reviews(resource_set_id,review_manifest)
 def _compiled_plan(self,resource_set_id):
  from formal_src.run_plan import compile_run_plan,CANONICAL_PROFILE
  projection=self.resources.inference_projection(resource_set_id)
  bindings=[v for v in projection['resources'] if Path(v['path']).name=='cohorts.json']
  if len(bindings)!=1:raise ValueError('Cohort binding required')
  cohorts=read(Path(projection['root'])/bindings[0]['path'])
  return compile_run_plan(CANONICAL_PROFILE,cohorts,execution_scope=self.resources.record(resource_set_id)['manifest'].get('execution_scope','full'))
 def freeze_profile(self,profile_id,resource_set_id,model_profile_id=None):
  with self.jobs.lock():
   return self._freeze_profile(profile_id,resource_set_id,model_profile_id)
 def _freeze_profile(self,profile_id,resource_set_id,model_profile_id):
  draft=self._profile(profile_id);r=self.check_resources(resource_set_id);blocks=[]
  if 'freeze' not in r['ready_for']:blocks+=r['blocking_items'] or ['resources_not_ready']
  if profile_id!='formal-v1':blocks.append('formal_preset_required')
  if not model_profile_id or not model_profile_id.startswith('custom-'):blocks.append('model_profile_required')
  if blocks:return dict(frozen=False,blocking_items=sorted(set(blocks)))
  from formal_src.profile_lifecycle import freeze_formal_profile,validate_frozen_profile,validate_models
  from formal_src.provider import INSTRUCTIONS
  import hashlib
  adopted=self._profile(model_profile_id)
  plan=self._compiled_plan(resource_set_id)
  try:validate_models(adopted.get('models'),{t['model_role'] for t in plan['units'] if t['work_type']=='generation'})
  except ValueError as exc:return dict(frozen=False,blocking_items=[str(exc)])
  if not self.parallel_verified(adopted):return dict(frozen=False,blocking_items=['concurrency_not_verified'])
  from .provider_checks import provider_ready
  vision=any(t.get('condition') in ('E3','I','M2_E3') for t in plan['units'])
  secondary=adopted.get('models',{}).get('secondary') if any(t.get('model_role')=='secondary' for t in plan['units']) else None
  if not provider_ready(self.workspace,adopted,require_vision=vision,secondary=secondary):return dict(frozen=False,blocking_items=['provider_capabilities_unverified'])
  states=(read(path) for path in self.jobs.runs.glob('*/state.json'))
  if any(s.get('kind')=='experiment' and s.get('state') in ('queued','validating','running','pause_requested','cancel_requested') for s in states):
   return dict(frozen=False,blocking_items=['execution_slot_busy'])
  previous=draft if draft.get('status')=='frozen' else None
  if previous:validate_frozen_profile(previous)
  draft={**draft,'status':'draft'}
  record=self.resources.record(resource_set_id)
  from formal_src.course_policy import automatic
  course=automatic(record['manifest']);audit_sha=None
  draft={**draft,'review_policy':'automatic_course' if course else 'human_reviewed'}
  if not course:
   from formal_src.verification import validate_audit_plan,_digest
   bindings=[b for b in record['manifest']['resources'] if Path(b['path']).name=='audit_plan.json' and b['kind']=='review']
   if len(bindings)!=1:return dict(frozen=False,blocking_items=['preregistered_audit_plan_required'])
   audit_plan=read(Path(record['root'])/bindings[0]['path'])
   audit_sha=_digest(audit_plan)
   try:validate_audit_plan(audit_plan,plan['units'],audit_sha)
   except (ValueError,KeyError,TypeError):return dict(frozen=False,blocking_items=['preregistered_audit_plan_invalid'])
  readiness={'cohorts_verified':True,'models_verified':True,'resources_verified':True,'human_reviews_complete':not course}
  frozen=freeze_formal_profile(draft,models=adopted.get('models',{}),provider=adopted.get('provider',{}),prompt_sha=hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(),resource_manifest_sha=digest(record['manifest']),code_sha=self._code_sha(),readiness=readiness,concurrency=adopted.get('concurrency',4),audit_plan_sha256=audit_sha)
  if previous and previous['freeze_sha']!=frozen['freeze_sha']:
   atomic(self.workspace/'profiles'/'history'/(previous['freeze_sha']+'.json'),previous)
  atomic(self.workspace/'profiles'/f'{profile_id}.json',frozen)
  return dict(frozen=True,profile_hash=digest(frozen),freeze_sha=frozen['freeze_sha'],blocking_items=[])
 def _budget(self):
  path=self.workspace/'budget.jsonl';reserved={};categories={'formal':0,'development_validation':0,'retry_demo':0}
  try:
   from formal_src.budget import records
   for row in records(path):
    if set(('attempt_id','run_id','task_id','request_hash','state','category'))-set(row) or row['category'] not in categories or row['state'] not in ('reserved','sent','completed','unknown'):raise ValueError()
    if row['state']=='reserved':
     old=reserved.get(row['attempt_id'])
     if old is not None and old!=row['category']:raise ValueError()
     reserved[row['attempt_id']]=row['category']
   for category in reserved.values():categories[category]+=1
   return max(0,8000-len(reserved)),categories,[]
  except (ValueError,TypeError,KeyError,OSError):return 0,categories,['budget_check_failed']
 def preflight(self,request):
  return self._preflight(request)
 def _preflight(self,request,remaining_generations=None,run_id=None):
  q=RunRequest.from_dict(request);p=self._profile(q['profile_id']);r=self.check_resources(q['resource_set_id']);blocks=[]
  if q['kind']=='experiment' and not self.worker_command and not self.parallel_verified(p):blocks.append('concurrency_not_verified')
  if q['kind']=='experiment' and not self.worker_command and self.parallel_verified(p):
   from .provider_checks import provider_ready
   record=self.resources.record(q['resource_set_id']);scope=record['manifest'].get('execution_scope') if record else None
   secondary=p.get('models',{}).get('secondary') if scope!='main_only' else None
   if not provider_ready(self.workspace,p,require_vision=True,secondary=secondary):blocks.append('provider_capabilities_unverified')
  if not self._worker():blocks.append('kernel_unavailable')
  integrity_verify=q['kind']=='verify' and read(self.jobs.directory(q['source_run_id'])/'spec.json',{}).get('kind')=='demo'
  if q['kind'] not in r['ready_for'] and not (integrity_verify and 'demo' in r['ready_for']):blocks+=r['blocking_items'] or ['resources_not_ready']
  if q['kind']=='demo' and 'demo' in r['ready_for'] and not self.worker_command:
   from .demo_readiness import check_demo
   from formal_src.runner import RunnerBlocked
   try:blocks+=check_demo(self.resources.inference_projection(q['resource_set_id']),q,p)
   except RunnerBlocked as exc:blocks.append(str(exc))
   except (ValueError,KeyError,TypeError,OSError):blocks.append('demo_assets_invalid_or_unapproved')
  if q['kind']=='prepare':
   record=self.resources.record(q['resource_set_id'])
   if p.get('status')=='frozen' or record and (record.get('reviews') or any(v is True for v in record['manifest'].get('qualification',{}).values())):blocks.append('approved_resources_immutable')
  if q['kind']=='experiment':blocks+=['profile_not_frozen'] if p.get('status')!='frozen' else []
  if q['kind']=='experiment' and p.get('status')=='frozen' and not self.worker_command:
   from formal_src.profile_lifecycle import validate_frozen_profile,validate_models
   try:validate_frozen_profile(p)
   except ValueError:blocks.append('frozen_profile_invalid')
   if p.get('code_sha')!=self._code_sha():blocks.append('code_changed')
   record=self.resources.record(q['resource_set_id'])
   try:validate_models(p.get('models'),('primary',) if record and record['manifest'].get('execution_scope')=='main_only' else ('primary','secondary'))
   except ValueError as exc:blocks.append(str(exc))
   if p.get('resource_manifest_sha')!=digest(record['manifest'] if record else {}):blocks.append('resources_changed')
  if q['kind']=='verify':
   source=self.jobs.directory(q['source_run_id'])
   if not (source/'sealed.json').exists():blocks.append('source_not_sealed')
  remaining,categories,budget_blocks=self._budget();blocks+=budget_blocks
  record=self.resources.record(q['resource_set_id']);manifest=record['manifest'] if record else {}
  logical=(3600 if manifest.get('execution_scope')=='main_only' else 6600) if q['kind']=='experiment' else sum(g!='E1' for g in q['demo_input']['groups']) if q['kind']=='demo' else 0
  additional=logical if remaining_generations is None else remaining_generations
  from formal_src.course_policy import automatic
  course=automatic(manifest) or automatic(p)
  if course:
   used=0
   if run_id:
    from formal_src.budget import peek_latest
    used=sum(row.get('run_id')==run_id for row in peek_latest(self.workspace/'budget.jsonl').values())
   remaining=max(0,logical*3-used)
  if additional>remaining:blocks.append('budget_exhausted')
  if not course and q['kind']=='demo' and additional and categories['retry_demo']+additional>900:blocks.append('retry_demo_budget_exhausted')
  if not course and (categories['development_validation']>500 or categories['retry_demo']>900):blocks.append('category_budget_exceeded')
  return PreflightReport.from_dict(dict(schema_version=1,ready=not blocks,blocking_items=sorted(set(blocks)),profile_hash=digest(p),estimated_logical_generations=logical,estimated_additional_attempts=additional,budget_remaining=remaining,price_status='unknown'))
 def start_run(self,request,credential_ref=None):
  with self.jobs.lock():
   return self._start_run(request,credential_ref)
 def _start_run(self,request,credential_ref):
  q=RunRequest.from_dict(self._clean(request));pre=self.preflight(q);p=self._profile(q['profile_id']);rid=str(uuid4());record=self.resources.record(q['resource_set_id']);blocks=list(pre['blocking_items'])
  generation=q['kind']=='experiment' or q['kind']=='demo' and any(x!='E1' for x in q['demo_input']['groups'])
  if generation and credential_ref not in self.credentials:blocks.append('credential_required')
  if q['kind']=='demo':p={**p,'concurrency':1}
  if q['kind']=='prepare' and record:p={**p,'prepare_output':record['root']}
  plan={}
  if q['kind']=='experiment' and p.get('status')=='frozen' and not self.worker_command:
   try:
    plan=self._compiled_plan(q['resource_set_id']);plan.update(status='frozen',executable=True,blocking_items=[])
    from formal_src.course_policy import automatic
    if automatic(p):plan['accounting_policy']={'scope':'run','max_attempts_per_generation':3,'attempt_limit':plan['logical_generation_count']*3,'historical_caps_enforced':False}
   except ValueError:blocks.append('formal_plan_unavailable')
  spec=ResolvedRunSpec.from_dict(dict(q,run_id=rid,purpose='software_test' if self.worker_command else 'verification' if q['kind']=='verify' else 'demo' if q['kind']=='demo' else p.get('purpose','custom'),resolved_config=p,code_sha=self._code_sha(),resource_sha=digest(record['manifest'] if record else {}),freeze_sha=p.get('freeze_sha') if q['kind']=='experiment' else None,plan=plan,logical_generation_count=pre['estimated_logical_generations'],output_relative_path='runs/'+rid))
  s,new=self.jobs.register(q,spec,blocks)
  if new and s['state']=='queued':
   try:projection=self.resources.inference_projection(q['resource_set_id'])
   except ValueError:return self.jobs.update(rid,state='blocked',blocking_items=['inference_projection_unavailable'])
   self.jobs.start(rid,self._worker(),self.credentials.get(credential_ref),expected_updated_at=s['updated_at'],inputs=projection)
  return self.jobs.get(s['run_id'])
 def _code_sha(self):return digest({str(p.relative_to(self.project_root)):__import__('hashlib').sha256(p.read_bytes()).hexdigest() for folder in ('formal_src','src','scripts') for p in sorted((self.project_root/folder).rglob('*.py'))})
 def get_run(self,run_id):
  state=self.jobs.get(run_id)
  path=self.jobs.directory(run_id)/'attempts.jsonl'
  if path.is_file():
   from formal_src.budget import peek_latest
   latest=peek_latest(path)
   state=dict(state,attempt_count=len(latest),failure_count=sum(bool(r.get('error_code')) or r.get('parse_status') in {'failed','parse_error','truncated'} for r in latest.values()))
  return state
 def find_runs(self,client_request_id=None):
  from formal_src.contracts import uuid
  if client_request_id is not None:uuid(client_request_id)
  runs=[]
  for path in self.jobs.runs.glob('*/state.json'):
   try:
    state=self.jobs.get(path.parent.name)
    if client_request_id is not None:
     if read(path.parent/'spec.json')['client_request_id']==client_request_id:return {'run_id':state['run_id']}
    else:runs.append(state)
   except (ValueError,KeyError,TypeError):continue
  return {'run_id':None} if client_request_id is not None else {'runs':sorted(runs,key=lambda s:s['created_at'],reverse=True)[:100]}
 def list_events(self,run_id,after_seq=0):return self.jobs.events(run_id,after_seq)
 def control_run(self,run_id,action):
  s=self.jobs.get(run_id);d=self.jobs.directory(run_id)
  if action not in ('pause','resume','cancel'):raise ValueError('Invalid control')
  if action=='resume':
   if s['state'] not in ('paused','interrupted','blocked','failed'):raise ValueError('Run cannot resume')
   blocks=self.jobs.resume_checks(run_id);spec=read(d/'spec.json')
   request={key:spec[key] for key in RunRequest.required}
   started=(d/'process.json').exists()
   remaining=max(0,s['logical_generation_count']-s['completed_logical_count']) if started else None
   blocks+=self._preflight(request,remaining_generations=remaining,run_id=run_id)['blocking_items']
   if self._code_sha()!=spec['code_sha']:blocks.append('code_changed')
   record=self.resources.record(spec['resource_set_id'])
   if digest(record['manifest'] if record else {})!=spec['resource_sha']:blocks.append('resources_changed')
   report=self.check_resources(spec['resource_set_id'])
   integrity_verify=spec['kind']=='verify' and read(self.jobs.directory(spec['source_run_id'])/'spec.json',{}).get('kind')=='demo'
   blocks += report['blocking_items'] if spec['kind'] not in report['ready_for'] and not (integrity_verify and 'demo' in report['ready_for']) else []
   current_profile=self._profile(spec['profile_id'])
   if spec['kind']=='demo':current_profile={**current_profile,'concurrency':1}
   if spec['kind']=='prepare' and record:current_profile={**current_profile,'prepare_output':record['root']}
   if digest(current_profile) != digest(spec['resolved_config']):blocks.append('profile_changed')
   if not self._worker():blocks.append('kernel_unavailable')
   needs_secret=spec['kind']=='experiment' or spec['kind']=='demo' and any(g!='E1' for g in spec['demo_input']['groups'])
   secret=next(reversed(self.credentials.values()),None) if needs_secret else None
   if needs_secret and not secret:blocks.append('credential_required')
   with self.jobs.lock():
    current=self.jobs.get(run_id)
    if current['updated_at']!=s['updated_at']:return current
    launch_profile=self._profile(spec['profile_id'])
    if spec['kind']=='demo':launch_profile={**launch_profile,'concurrency':1}
    if spec['kind']=='prepare' and record:launch_profile={**launch_profile,'prepare_output':record['root']}
    if digest(launch_profile)!=digest(spec['resolved_config']):blocks.append('profile_changed')
    if blocks:return self.jobs.update(run_id,state='blocked',blocking_items=sorted(set(blocks)))
    try:projection=self.resources.inference_projection(spec['resource_set_id'])
    except ValueError:return self.jobs.update(run_id,state='blocked',blocking_items=['inference_projection_unavailable'])
    self.jobs.start(run_id,self._worker(),secret,expected_updated_at=s['updated_at'],inputs=projection)
    return self.jobs.get(run_id)
  if s['state'] in ('queued','running','pause_requested','cancel_requested'):
   atomic(d/'control.json',{'action':action});return self.jobs.update(run_id,state=action+'_requested')
  return s
 def recovery_status(self,run_id):
  from formal_src.recovery import issues
  state=self.jobs.get(run_id)
  if state['state'] not in ('blocked','interrupted','failed','paused'):return {'items':[]}
  return {'items':issues(self.jobs.directory(run_id))}
 def resolve_attempt(self,run_id,values):
  from formal_src.recovery import resolve_failure
  values=self._clean(values)
  if set(values)!={'attempt_id','note','confirmed'}:raise ValueError('Invalid recovery fields')
  with self.jobs.lock():
   state=self.jobs.get(run_id)
   if state['state'] not in ('blocked','interrupted','paused','failed'):raise ValueError('Run must be stopped')
   d=self.jobs.directory(run_id)
   with (d/'worker.lock').open('a+') as owner:
    try:fsutil.acquire(owner,blocking=False)
    except BlockingIOError:raise ValueError('Worker still running') from None
    result=resolve_failure(d,values['attempt_id'],values['note'],values['confirmed'])
   # The audit entry is durable; resuming is a separate explicit action.
   self.jobs.update(run_id,state='blocked',available_actions=['resume','inspect'])
   return result
 def get_demo_reference(self,run_id):
  from .demo_reference import reference_for_run
  if self.jobs.get(run_id)['state']!='completed':raise ValueError('Reference requires completed run')
  return reference_for_run(self.project_root,self.jobs.directory(run_id))
 def get_results(self,run_id,view='summary'):
  if view not in ('summary','answers','retrieval','audit'):raise ValueError('Invalid result view')
  s=self.jobs.get(run_id);d=self.jobs.directory(run_id)
  from formal_src.verification import check_seal,check_result_proof
  check_seal(d)
  proof=check_result_proof(d)
  r=read(d/'summary.json')
  if not r:raise ValueError('Results unavailable')
  if not proof:
   # Legacy inference seals prove bytes, not historical metrics/audit claims.
   r=dict(r,metrics={},main_comparisons=[],audit_complete=False,engineering={'verification_kind':'integrity_only','evaluation_status':'not_evaluated'})
  if view != 'summary':
   candidates={'answers':['scored-answers.jsonl','predictions.jsonl'],'retrieval':['retrieval.jsonl'],'audit':['audit-status.json']}[view]
   available=set(proof['files']) if proof else set()
   available.add('predictions.jsonl')
   selected=[name for name in candidates if name in available]
   if view=='answers' and 'scored-answers.jsonl' in selected:selected=['scored-answers.jsonl']
   r=dict(r,answer_tables=selected,exports=[name for name in r['exports'] if name in available])
  return RunResults.from_dict(r)
 def export_course_bundle(self,run_id):
  self.jobs.get(run_id)
  from formal_src.course_export import export_bundle
  return export_bundle(self.project_root,self.jobs.directory(run_id),self.workspace/'exports',credential_values=tuple(self.credentials.values()))
