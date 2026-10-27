"""Configured model identity stays frozen while model names remain user-selectable."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.formal import test_profile_lifecycle as lifecycle
from app.service import ApplicationService
from app.jobs import atomic
from tests.app.test_provider_parallel import response

ROOT=Path(__file__).resolve().parents[2]

class ConfiguredModelTests(unittest.TestCase):
 def test_custom_names_and_optional_secondary_are_frozen_and_tamper_checked(self):
  helper=lifecycle.LifecycleTests();module=helper.module()
  for secondary in (None,'deepseek-v4-pro'):
   options=helper.options();options['models']={'primary':'deepseek-flash','secondary':secondary}
   frozen=module.freeze_formal_profile(helper.draft(),**options)
   self.assertTrue(module.validate_frozen_profile(frozen))
   options['models']['primary']='modified-after-freeze'
   self.assertEqual(frozen['models']['primary'],'deepseek-flash')
   altered=copy.deepcopy(frozen);altered['models']['primary']='modified-without-refreezing'
   with self.assertRaises(ValueError):module.validate_frozen_profile(altered)

 def test_bad_model_configuration_is_rejected(self):
  helper=lifecycle.LifecycleTests()
  for models in ({'primary':'','secondary':None},{'primary':'  ','secondary':None},
                 {'primary':12,'secondary':None},{'primary':'valid','secondary':12},
                 {'primary':'valid','secondary':'   '},['valid']):
   with self.subTest(models=models),self.assertRaises(ValueError):
    helper.module().freeze_formal_profile(helper.draft(),**{**helper.options(),'models':models})

 def test_freezing_again_uses_saved_models_and_preserves_old_snapshot(self):
  helper=lifecycle.LifecycleTests()
  with tempfile.TemporaryDirectory() as td:
   service=ApplicationService(ROOT,td,provider_transport=lambda e,p,k,t:response(p))
   provider=helper.options()['provider'];models={'primary':'deepseek-flash','secondary':None}
   profile=service.clone_profile('formal-v1',{'provider':provider,'models':models,'concurrency':4})
   manifest={'review_policy':'automatic_course','execution_scope':'main_only'}
   plan={'units':[{'work_type':'generation','model_role':'primary'}]}
   key=service.set_credential('synthetic-secret')
   def check():
    settings={**provider,'model':models['primary'],'concurrency':4}
    self.assertTrue(service.check_provider(settings,key,'compatibility')['ready'])
   with patch.object(service,'check_resources',return_value={'ready_for':['freeze']}),patch.object(service.resources,'record',return_value={'manifest':manifest}),patch.object(service,'_compiled_plan',return_value=plan):
    check();result=service.freeze_profile('formal-v1','synthetic',profile['profile_id'])
    self.assertTrue(result['frozen'],result)
    old=service._profile('formal-v1');run=Path(td)/'runs'/'historical'/'spec.json'
    atomic(run,{'resolved_config':old});before=run.read_bytes()
    models['primary']='another-configured-model'
    service.save_profile(profile['profile_id'],{'models':models})
    blocked=service.freeze_profile('formal-v1','synthetic',profile['profile_id'])
    self.assertFalse(blocked['frozen']);self.assertIn('concurrency_not_verified',blocked['blocking_items'])
    check();result=service.freeze_profile('formal-v1','synthetic',profile['profile_id'])
    self.assertTrue(result['frozen'],result)
    self.assertEqual(service._profile('formal-v1')['models']['primary'],models['primary'])
    self.assertNotEqual(old['freeze_sha'],result['freeze_sha'])
    archive=Path(td)/'profiles'/'history'/(old['freeze_sha']+'.json')
    self.assertEqual(json.loads(archive.read_text()),old)
    self.assertEqual(run.read_bytes(),before)

 def test_full_plan_requires_secondary_but_main_only_does_not(self):
  helper=lifecycle.LifecycleTests()
  with tempfile.TemporaryDirectory() as td:
   service=ApplicationService(ROOT,td)
   profile=service.clone_profile('formal-v1',{'models':{'primary':'deepseek-flash','secondary':None},'provider':helper.options()['provider']})
   plan={'units':[{'work_type':'generation','model_role':'secondary'}]}
   with patch.object(service,'check_resources',return_value={'ready_for':['freeze']}),patch.object(service.resources,'record',return_value={'manifest':{'review_policy':'automatic_course'}}),patch.object(service,'_compiled_plan',return_value=plan),patch.object(service,'parallel_verified',return_value=True):
    result=service.freeze_profile('formal-v1','synthetic',profile['profile_id'])
   self.assertFalse(result['frozen']);self.assertIn('formal_model_missing_secondary',result['blocking_items'])

 def test_real_worker_validation_accepts_configured_names(self):
  from tests.formal.test_runner import production_fixture
  from formal_src.runner import formal_tasks,input_package
  from formal_src.provider import digest
  with tempfile.TemporaryDirectory() as td:
   spec,rd,_=production_fixture(Path(td));config=spec['resolved_config']
   config['models']={'primary':'deepseek-flash','secondary':'deepseek-v4-pro'}
   config['freeze_sha']=None;config['freeze_sha']=digest(config);spec['freeze_sha']=config['freeze_sha']
   self.assertTrue(formal_tasks(spec,input_package(rd)[0]))

 def test_concurrent_refreezes_preserve_every_returned_identity(self):
  from concurrent.futures import ThreadPoolExecutor
  import time
  helper=lifecycle.LifecycleTests()
  with tempfile.TemporaryDirectory() as td:
   service=ApplicationService(ROOT,td)
   profiles=[service.clone_profile('formal-v1',{'models':{'primary':name,'secondary':None},'provider':helper.options()['provider']}) for name in ('model-a','model-b','model-c')]
   manifest={'review_policy':'automatic_course','execution_scope':'main_only'}
   plan={'units':[{'work_type':'generation','model_role':'primary'}]}
   code=service._code_sha()
   def slow_identity():time.sleep(.03);return code
   with patch.object(service,'check_resources',return_value={'ready_for':['freeze']}),patch.object(service.resources,'record',return_value={'manifest':manifest}),patch.object(service,'_compiled_plan',return_value=plan),patch.object(service,'parallel_verified',return_value=True),patch.object(service,'_code_sha',side_effect=slow_identity):
    self.assertTrue(service.freeze_profile('formal-v1','synthetic',profiles[0]['profile_id'])['frozen'])
    with ThreadPoolExecutor(max_workers=2) as pool:
     results=list(pool.map(lambda p:service.freeze_profile('formal-v1','synthetic',p['profile_id']),profiles[1:]))
   identities={service._profile('formal-v1')['freeze_sha']}
   identities.update(p.stem for p in (Path(td)/'profiles'/'history').glob('*.json'))
   self.assertTrue(all(r['frozen'] and r['freeze_sha'] in identities for r in results),results)

 def test_refreeze_checks_active_states_without_using_recent_list(self):
  helper=lifecycle.LifecycleTests()
  with tempfile.TemporaryDirectory() as td:
   service=ApplicationService(ROOT,td)
   profile=service.clone_profile('formal-v1',{'models':{'primary':'deepseek-flash','secondary':None},'provider':helper.options()['provider']})
   atomic(Path(td)/'runs'/'older-active'/'state.json',{'kind':'experiment','state':'validating'})
   with patch.object(service,'check_resources',return_value={'ready_for':['freeze']}),patch.object(service.resources,'record',return_value={'manifest':{'review_policy':'automatic_course'}}),patch.object(service,'_compiled_plan',return_value={'units':[{'work_type':'generation','model_role':'primary'}]}),patch.object(service,'parallel_verified',return_value=True),patch.object(service,'find_runs',return_value={'runs':[]}):
    result=service.freeze_profile('formal-v1','synthetic',profile['profile_id'])
   self.assertFalse(result['frozen']);self.assertIn('execution_slot_busy',result['blocking_items'])

 def test_resume_rechecks_model_snapshot_inside_launch_lock(self):
  from tests.app.test_service import setup,request
  with tempfile.TemporaryDirectory() as td:
   s=setup(Path(td));profile=s.clone_profile('formal-v1',{'models':{'primary':'model-a','secondary':None}})
   q=request();q.update(kind='experiment',profile_id=profile['profile_id'])
   ready={'blocking_items':[],'estimated_logical_generations':6600}
   key=s.set_credential('synthetic-secret')
   with patch.object(s,'preflight',return_value=ready),patch.object(s.jobs,'start'):
    rid=s.start_run(q,key)['run_id']
   s.jobs.update(rid,state='paused')
   def change_before_launch():
    current=s._profile(profile['profile_id']);current['models']['primary']='model-b'
    atomic(Path(td)/'w'/'profiles'/(profile['profile_id']+'.json'),current)
    return s.worker_command
   with patch.object(s,'_preflight',return_value={'blocking_items':[]}),patch.object(s,'check_resources',return_value={'ready_for':['experiment']}),patch.object(s,'_worker',side_effect=change_before_launch),patch.object(s.jobs,'start') as start:
    result=s.control_run(rid,'resume')
   self.assertEqual(result['state'],'blocked')
   self.assertIn('profile_changed',result['blocking_items'])
   start.assert_not_called()
