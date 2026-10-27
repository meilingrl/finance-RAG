"""Automatic fixed-resource setup and preparation of a new course experiment."""
import threading
from uuid import uuid4
from .jobs import atomic

class CourseWorkflow:
 def _init_course_workflow(self):
  self._data_lock=threading.RLock();self._data_thread=None
  self._provider_credentials={}
  self._data_state={'state':'not_started','ready':False,'message':'正在检查固定实验资料'}
 def data_status(self):
  with self._data_lock:return dict(self._data_state)
 def begin_fixed_data_setup(self,archive_path=None):
  with self._data_lock:
   if self._data_thread and self._data_thread.is_alive():return dict(self._data_state)
   if self._data_state.get('ready'):return dict(self._data_state)
   self._data_state={'state':'preparing','ready':False,'stage':'inspect','message':'正在准备固定实验资料'}
   def progress(value):
    with self._data_lock:self._data_state.update(value)
   def prepare():
    try:
     from .datasets import ensure_fixed_dataset
     result=ensure_fixed_dataset(self.project_root,archive_path=archive_path,progress=progress)
     formal=self.resources.register(result['formal_root']);demo=self.resources.register(result['demo_root'])
     ready='freeze' in formal['ready_for'] and 'demo' in demo['ready_for']
     with self._data_lock:self._data_state={**result,'state':'ready' if ready else 'invalid','ready':ready,
      'formal_resource_set_id':formal['resource_set_id'],'demo_resource_set_id':demo['resource_set_id'],
      'blocking_items':formal['blocking_items'] if not ready else [],'message':'固定实验资料已校验并登记' if ready else '固定资料校验未通过'}
    except Exception as exc:
     # Installer errors contain only fixed paths/verification messages, never provider credentials.
     from .datasets import FixedDatasetError
     detail=str(exc) if isinstance(exc,FixedDatasetError) else type(exc).__name__
     with self._data_lock:self._data_state={'state':'failed','ready':False,'blocking_items':['fixed_data_unavailable'],
       'message':'固定资料准备未完成，请检查网络后重试；已有资料保留。','detail':detail}
   self._data_thread=threading.Thread(target=prepare,daemon=True,name='fixed-course-data')
   self._data_thread.start();return dict(self._data_state)
 def prepare_experiment(self,model_profile_id,resource_set_id=None,credential_ref=None):
  """Validate data and service, refresh the runtime snapshot, then preflight it."""
  if credential_ref not in self.credentials:return {'ready':False,'state':'configuration_required','blocking_items':['credential_required']}
  if not resource_set_id or resource_set_id in ('formal-v1','formal-v1-course'):
   status=self.data_status()
   if not status.get('ready'):
    self.begin_fixed_data_setup()
    return {'ready':False,'state':'preparing_data','blocking_items':['fixed_data_preparing'],'data':self.data_status()}
   resource_set_id=status['formal_resource_set_id']
  resource=self.check_resources(resource_set_id)
  if 'freeze' not in resource['ready_for']:
   return {'ready':False,'state':'data_invalid','blocking_items':resource['blocking_items'],'resource_set_id':resource_set_id}
  profile=self._profile(model_profile_id)
  if model_profile_id=='formal-v1' or profile.get('status')=='frozen':
   profile=self.clone_profile(model_profile_id,{})
   model_profile_id=profile['profile_id']
  plan=self._compiled_plan(resource_set_id)
  roles={t['model_role'] for t in plan['units'] if t['work_type']=='generation'}
  from formal_src.profile_lifecycle import validate_models
  try:validate_models(profile.get('models'),roles)
  except ValueError as exc:return {'ready':False,'state':'configuration_required','blocking_items':[str(exc)]}
  from .provider_checks import provider_ready,identity
  require_vision=any(t['condition'] in ('E3','I','M2_E3') for t in plan['units'])
  secondary=profile['models'].get('secondary') if 'secondary' in roles else None
  fingerprint=identity({**profile['provider'],'model':profile['models']['primary'],'concurrency':profile.get('concurrency',4)})
  if self._provider_credentials.get(fingerprint)!=credential_ref or not provider_ready(self.workspace,profile,require_vision=require_vision,secondary=secondary):
   report=self.check_provider({**profile['provider'],'model':profile['models']['primary'],
      'concurrency':profile.get('concurrency',4),'require_vision':require_vision,'secondary_model':secondary},credential_ref,'compatibility')
   if not report['ready']:
    return {'ready':False,'state':'provider_invalid','blocking_items':report['blocking_items'],'provider_check':report,'resource_set_id':resource_set_id}
   # Any negotiated representation is recorded before freeze; no model substitution.
   if report.get('effective_provider'):
    profile=self.save_profile(model_profile_id,{'provider':report['effective_provider']})
  frozen=self.freeze_profile('formal-v1',resource_set_id,model_profile_id)
  if not frozen['frozen']:return {'ready':False,'state':'freeze_blocked',**frozen,'resource_set_id':resource_set_id}
  request={'schema_version':1,'client_request_id':str(uuid4()),'kind':'experiment','profile_id':'formal-v1',
    'resource_set_id':resource_set_id,'source_run_id':None,'demo_input':None}
  report=dict(self.preflight(request))
  return {**report,'state':'ready' if report['ready'] else 'blocked','resource_set_id':resource_set_id,
    'model_profile_id':model_profile_id,'request':request,'freeze_sha':frozen['freeze_sha'],
    'attempt_limit':report['estimated_logical_generations']*3,'budget_scope':'run'}
