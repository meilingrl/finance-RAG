"""The shared CLI/HTTP application facade. No experiment algorithm lives here."""
import json,sys
from pathlib import Path
from uuid import uuid4
from formal_src.contracts import RunRequest,ResolvedRunSpec,PreflightReport,RunResults,safe_json,identifier,ContractError
from .jobs import JobStore,atomic,read,digest
from .resources import ResourceRegistry
class ApplicationService:
 def __init__(self,project_root,workspace,*,worker_command=None,catalog_transport=None,provider_transport=None):
  self.catalog_transport=catalog_transport;self.provider_transport=provider_transport
  self.project_root=Path(project_root).resolve();self.workspace=Path(workspace).resolve();self.jobs=JobStore(workspace);self.resources=ResourceRegistry(workspace);self.credentials={};self.session_id=str(uuid4());self.worker_command=worker_command;self.jobs.recover()
 def _profile(self,pid):
  identifier(pid);p=self.workspace/'profiles'/f'{pid}.json'
  if p.exists():return read(p)
  p=self.project_root/'formal_config'/f'{pid}.json'
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
  current.update(values);atomic(self.workspace/'profiles'/f'{profile_id}.json',current);return current
 def clone_profile(self,source_profile_id,values):
  source=self._profile(source_profile_id);values=self._clean(values)
  if set(values)&{'profile_id','purpose','status','source_profile_id'}:raise ValueError('Profile identity reserved')
  source.update(values,profile_id='custom-'+str(uuid4()),purpose='custom',status='draft',source_profile_id=source_profile_id);atomic(self.workspace/'profiles'/(source['profile_id']+'.json'),source);return source
 def set_credential(self,api_key):
  if not isinstance(api_key,str) or not api_key.strip():raise ValueError('Credential required')
  ref=str(uuid4());self.credentials[ref]=api_key;return ref
 def check_provider(self,settings,credential_ref,level):
  settings=self._clean(settings)
  if level not in ('catalog','generation'):raise ValueError('Invalid check level')
  key=self.credentials.get(credential_ref)
  if not key:return {'ready':False,'blocking_items':['credential_required'],'sent_attempts':0}
  from formal_src.provider import validate_config,build_payload,generate,digest as request_digest
  from urllib.parse import urlsplit
  endpoint=settings.get('endpoint') or settings.get('base_url','').rstrip('/')+'/responses'
  config={**settings,'endpoint':endpoint,'max_output_tokens':settings.get('max_output_tokens',2048),'reserve_input_tokens':settings.get('reserve_input_tokens',32768)}
  validate_config(config)
  if level=='catalog':
   catalog=endpoint.rsplit('/',1)[0]+'/models'
   if self.catalog_transport:raw=self.catalog_transport(catalog,key,settings.get('timeout_seconds',30))
   else:
    import urllib.request
    req=urllib.request.Request(catalog,headers={'Authorization':'Bearer '+key})
    with urllib.request.urlopen(req,timeout=settings.get('timeout_seconds',30)) as response:raw=json.load(response)
   ready=any(item.get('id')==config['model'] for item in raw.get('data',[]))
   return {'ready':ready,'blocking_items':[] if ready else ['model_not_in_catalog'],'sent_attempts':0,'level':'catalog'}
  from formal_src.budget import Budget
  payload=build_payload(config,'What is the marker?', [{'evidence_id':'operational-probe','text':'The marker is READY.'}])
  budget=Budget(self.workspace/'budget.jsonl');reservation=budget.reserve(str(uuid4()),'operational-synthetic-probe',request_digest(payload),'development_validation')
  budget.transition(reservation,'sent')
  try:
   result=generate(config,payload,key,['operational-probe'],transport=self.provider_transport)
   budget.transition(reservation,'completed')
  except Exception as exc:
   from formal_src.provider import ProviderError,APIError
   known=isinstance(exc,ProviderError) or isinstance(exc,APIError) and str(exc).startswith('http_')
   budget.transition(reservation,'completed' if known else 'unknown')
   return {'ready':False,'blocking_items':['provider_generation_failed'],'sent_attempts':1,'level':'generation','purpose':'operational_probe'}
  ready=result.get('status')=='answered' and result.get('parsed_answer')=='READY'
  return {'ready':ready,'blocking_items':[] if ready else ['operational_marker_not_answered'],'sent_attempts':1,'level':'generation','purpose':'operational_probe','model':config['model'],'response_id':result.get('response_id')}
 def register_resources(self,root_path):return self.resources.register(root_path)
 def check_resources(self,resource_set_id):return self.resources.check(resource_set_id)
 def import_reviews(self,resource_set_id,review_manifest):return self.resources.import_reviews(resource_set_id,review_manifest)
 def _compiled_plan(self,resource_set_id):
  from formal_src.run_plan import compile_run_plan,CANONICAL_PROFILE
  projection=self.resources.inference_projection(resource_set_id)
  bindings=[v for v in projection['resources'] if Path(v['path']).name=='cohorts.json']
  if len(bindings)!=1:raise ValueError('Cohort binding required')
  cohorts=read(Path(projection['root'])/bindings[0]['path'])
  return compile_run_plan(CANONICAL_PROFILE,cohorts)
 def freeze_profile(self,profile_id,resource_set_id,model_profile_id=None):
  draft=self._profile(profile_id);r=self.check_resources(resource_set_id);blocks=[]
  if 'freeze' not in r['ready_for']:blocks+=r['blocking_items'] or ['resources_not_ready']
  if profile_id!='formal-v1':blocks.append('formal_preset_required')
  if not model_profile_id or not model_profile_id.startswith('custom-'):blocks.append('model_profile_required')
  if blocks:return dict(frozen=False,blocking_items=sorted(set(blocks)))
  from formal_src.profile_lifecycle import freeze_formal_profile
  from formal_src.provider import INSTRUCTIONS
  import hashlib
  adopted=self._profile(model_profile_id)
  plan=self._compiled_plan(resource_set_id)
  record=self.resources.record(resource_set_id)
  from formal_src.verification import validate_audit_plan,_digest
  bindings=[b for b in record['manifest']['resources'] if Path(b['path']).name=='audit_plan.json' and b['kind']=='review']
  if len(bindings)!=1:return dict(frozen=False,blocking_items=['preregistered_audit_plan_required'])
  audit_plan=read(Path(record['root'])/bindings[0]['path'])
  audit_sha=_digest(audit_plan)
  try:validate_audit_plan(audit_plan,plan['units'],audit_sha)
  except (ValueError,KeyError,TypeError):return dict(frozen=False,blocking_items=['preregistered_audit_plan_invalid'])
  readiness={'cohorts_verified':True,'models_verified':True,'resources_verified':True,'human_reviews_complete':True}
  frozen=freeze_formal_profile(draft,models=adopted.get('models',{}),provider=adopted.get('provider',{}),prompt_sha=hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(),resource_manifest_sha=digest(record['manifest']),code_sha=self._code_sha(),readiness=readiness,concurrency=adopted.get('concurrency',4),audit_plan_sha256=audit_sha)
  atomic(self.workspace/'profiles'/f'{profile_id}.json',frozen)
  return dict(frozen=True,profile_hash=digest(frozen),freeze_sha=frozen['freeze_sha'],blocking_items=[])
 def _budget(self):
  path=self.workspace/'budget.jsonl';reserved={};categories={'formal':0,'development_validation':0,'retry_demo':0}
  try:
   for line in path.read_text().splitlines() if path.exists() else []:
    row=json.loads(line)
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
 def _preflight(self,request,remaining_generations=None):
  q=RunRequest.from_dict(request);p=self._profile(q['profile_id']);r=self.check_resources(q['resource_set_id']);blocks=[]
  if not self._worker():blocks.append('kernel_unavailable')
  integrity_verify=q['kind']=='verify' and read(self.jobs.directory(q['source_run_id'])/'spec.json',{}).get('kind')=='demo'
  if q['kind'] not in r['ready_for'] and not (integrity_verify and 'demo' in r['ready_for']):blocks+=r['blocking_items'] or ['resources_not_ready']
  if q['kind']=='prepare':
   record=self.resources.record(q['resource_set_id'])
   if p.get('status')=='frozen' or record and (record.get('reviews') or any(v is True for v in record['manifest'].get('qualification',{}).values())):blocks.append('approved_resources_immutable')
  if q['kind']=='experiment':blocks+=['profile_not_frozen'] if p.get('status')!='frozen' else []
  if q['kind']=='experiment' and p.get('status')=='frozen' and not self.worker_command:
   from formal_src.profile_lifecycle import validate_frozen_profile
   try:validate_frozen_profile(p)
   except ValueError:blocks.append('frozen_profile_invalid')
   if p.get('code_sha')!=self._code_sha():blocks.append('code_changed')
   record=self.resources.record(q['resource_set_id'])
   if p.get('resource_manifest_sha')!=digest(record['manifest'] if record else {}):blocks.append('resources_changed')
  if q['kind']=='verify':
   source=self.jobs.directory(q['source_run_id'])
   if not (source/'sealed.json').exists():blocks.append('source_not_sealed')
  remaining,categories,budget_blocks=self._budget();blocks+=budget_blocks
  logical=6600 if q['kind']=='experiment' else sum(g!='E1' for g in q['demo_input']['groups']) if q['kind']=='demo' else 0
  additional=logical if remaining_generations is None else remaining_generations
  if additional>remaining:blocks.append('budget_exhausted')
  if q['kind']=='demo' and additional and categories['retry_demo']+additional>900:blocks.append('retry_demo_budget_exhausted')
  if categories['development_validation']>500 or categories['retry_demo']>900:blocks.append('category_budget_exceeded')
  return PreflightReport.from_dict(dict(schema_version=1,ready=not blocks,blocking_items=sorted(set(blocks)),profile_hash=digest(p),estimated_logical_generations=logical,estimated_additional_attempts=additional,budget_remaining=remaining,price_status='unknown'))
 def start_run(self,request,credential_ref=None):
  q=RunRequest.from_dict(self._clean(request));pre=self.preflight(q);p=self._profile(q['profile_id']);rid=str(uuid4());record=self.resources.record(q['resource_set_id']);blocks=list(pre['blocking_items'])
  generation=q['kind']=='experiment' or q['kind']=='demo' and any(x!='E1' for x in q['demo_input']['groups'])
  if generation and credential_ref not in self.credentials:blocks.append('credential_required')
  if q['kind']=='prepare' and record:p={**p,'prepare_output':record['root']}
  plan={}
  if q['kind']=='experiment' and p.get('status')=='frozen' and not self.worker_command:
   try:
    plan=self._compiled_plan(q['resource_set_id']);plan.update(status='frozen',executable=True,blocking_items=[])
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
   latest={r['attempt_id']:r for r in (json.loads(line) for line in path.read_text().splitlines() if line.strip())}
   state=dict(state,attempt_count=len(latest),failure_count=sum(bool(r.get('error_code')) or r.get('parse_status') in {'failed','parse_error','truncated'} for r in latest.values()))
  return state
 def list_events(self,run_id,after_seq=0):return self.jobs.events(run_id,after_seq)
 def control_run(self,run_id,action):
  s=self.jobs.get(run_id);d=self.jobs.directory(run_id)
  if action not in ('pause','resume','cancel'):raise ValueError('Invalid control')
  if action=='resume':
   if s['state'] not in ('paused','interrupted','blocked'):raise ValueError('Run cannot resume')
   blocks=self.jobs.resume_checks(run_id);spec=read(d/'spec.json')
   request={key:spec[key] for key in RunRequest.required}
   started=(d/'process.json').exists()
   remaining=max(0,s['logical_generation_count']-s['completed_logical_count']) if started else None
   blocks+=self._preflight(request,remaining_generations=remaining)['blocking_items']
   if self._code_sha()!=spec['code_sha']:blocks.append('code_changed')
   record=self.resources.record(spec['resource_set_id'])
   if digest(record['manifest'] if record else {})!=spec['resource_sha']:blocks.append('resources_changed')
   report=self.check_resources(spec['resource_set_id'])
   integrity_verify=spec['kind']=='verify' and read(self.jobs.directory(spec['source_run_id'])/'spec.json',{}).get('kind')=='demo'
   blocks += report['blocking_items'] if spec['kind'] not in report['ready_for'] and not (integrity_verify and 'demo' in report['ready_for']) else []
   current_profile=self._profile(spec['profile_id'])
   if spec['kind']=='prepare' and record:current_profile={**current_profile,'prepare_output':record['root']}
   if digest(current_profile) != digest(spec['resolved_config']):blocks.append('profile_changed')
   if not self._worker():blocks.append('kernel_unavailable')
   needs_secret=spec['kind']=='experiment' or spec['kind']=='demo' and any(g!='E1' for g in spec['demo_input']['groups'])
   secret=next(reversed(self.credentials.values()),None) if needs_secret else None
   if needs_secret and not secret:blocks.append('credential_required')
   with self.jobs.lock():
    current=self.jobs.get(run_id)
    if current['updated_at']!=s['updated_at']:return current
    if blocks:return self.jobs.update(run_id,state='blocked',blocking_items=sorted(set(blocks)))
    try:projection=self.resources.inference_projection(spec['resource_set_id'])
    except ValueError:return self.jobs.update(run_id,state='blocked',blocking_items=['inference_projection_unavailable'])
    self.jobs.start(run_id,self._worker(),secret,expected_updated_at=s['updated_at'],inputs=projection)
    return self.jobs.get(run_id)
  if s['state'] in ('queued','running','pause_requested','cancel_requested'):
   atomic(d/'control.json',{'action':action});return self.jobs.update(run_id,state=action+'_requested')
  return s
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
