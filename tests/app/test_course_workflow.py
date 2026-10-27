"""Course preparation must derive readiness and isolate new run accounting."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from app.service import ApplicationService
from app.resources import ResourceRegistry
from formal_src.budget import Budget,BudgetError,latest
from tests.app.test_service import setup,request

ROOT=Path(__file__).resolve().parents[2]

class CourseWorkflowTests(unittest.TestCase):
 def test_budget_scopes_preserve_old_calls_without_blocking_new_run(self):
  with tempfile.TemporaryDirectory() as td:
   ledger=Path(td)/'budget.jsonl'
   for rid in ('first','second'):
    b=Budget(ledger,total=2,caps={'formal':2},run_id=rid)
    for i in range(2):b.reserve(rid,str(i),'0'*64,'formal')
    with self.assertRaises(BudgetError):b.reserve(rid,'overflow','0'*64,'formal')
   self.assertEqual(len(latest(ledger)),4)

 def test_new_course_preflight_ignores_previous_experiment_consumption(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);s=setup(root);p=s.clone_profile('formal-v1',{'review_policy':'automatic_course'})
   q=request();q.update(kind='experiment',profile_id=p['profile_id'])
   (s.workspace/'budget.jsonl').write_text(''.join(json.dumps(dict(attempt_id=str(i),run_id='previous',task_id=str(i),request_hash='0'*64,state='reserved',category='formal'))+'\n' for i in range(8000)))
   record=s.resources.record('demo');record['manifest'].update(review_policy='automatic_course',execution_scope='main_only')
   with patch.object(s.resources,'record',return_value=record):result=s.preflight(q)
   self.assertNotIn('budget_exhausted',result['blocking_items'])
   self.assertNotIn('category_budget_exceeded',result['blocking_items'])
   self.assertEqual(result['budget_remaining'],10800)

 def test_course_registration_derives_static_qualification(self):
  with tempfile.TemporaryDirectory() as td:
   s=setup(Path(td));r=s.resources;record=r.record('demo')
   record['manifest'].update(purpose='formal',review_policy='automatic_course',qualification={})
   from app.jobs import atomic
   atomic(r.root/'demo.json',record)
   passed={'applicable':True,'passed':True,'qualification':{'source_disjoint':True,'cohorts_validated':True,'inputs_complete':True},'blocking_items':[],'evidence':{'counts':{'main':200}},'evidence_sha256':'a'*64}
   with patch('app.course_readiness.qualify_course_resources',return_value=passed):result=r.check('demo')
   self.assertFalse(any(b.startswith('qualification_') for b in result['blocking_items']),result)

 def test_setup_api_exposes_progress_and_does_not_require_a_provider_key(self):
  from fastapi.testclient import TestClient
  from app.api import create_app
  with tempfile.TemporaryDirectory() as td:
   s=ApplicationService(ROOT,td);client=TestClient(create_app(s))
   with patch.object(s,'begin_fixed_data_setup',return_value={'state':'preparing','ready':False}):
    result=client.post('/api/v1/data/prepare',headers={'x-session-id':s.session_id},json={})
   self.assertEqual(result.status_code,200,result.text)
   self.assertEqual(result.json()['state'],'preparing')

 def test_prepare_endpoint_uses_saved_model_and_returns_ready_request(self):
  from fastapi.testclient import TestClient
  from app.api import create_app
  with tempfile.TemporaryDirectory() as td:
   s=ApplicationService(ROOT,td);client=TestClient(create_app(s))
   with patch.object(s,'prepare_experiment',return_value={'ready':True,'blocking_items':[],'resource_set_id':'fixed'}):
    result=client.post('/api/v1/experiments/prepare',headers={'x-session-id':s.session_id},json={'model_profile_id':'custom-selected','credential_ref':'opaque'})
   self.assertEqual(result.status_code,200,result.text)
   self.assertTrue(result.json()['ready'])

 @unittest.skipUnless((ROOT/'data/fixed-course-v1/formal/resources.json').is_file(),'fixed dataset is optional in a source-only checkout')
 def test_fixed_main_preparation_repairs_all_eleven_stale_gates_without_real_api(self):
  from tests.formal import test_profile_lifecycle as lifecycle
  from formal_src.profile_lifecycle import freeze_formal_profile
  from app.jobs import atomic
  from tests.app.test_provider_parallel import response
  helper=lifecycle.LifecycleTests()
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);calls=[]
   def transport(e,p,k,t):
    calls.append(p)
    image=any(part.get('type')=='input_image' for part in p['input'][0]['content'])
    return response(p,'123456' if image else 'READY')
   s=ApplicationService(ROOT,root,provider_transport=transport)
   old=helper.options();old['readiness']['human_reviews_complete']=False;old['audit_plan_sha256']=None
   previous=freeze_formal_profile({**helper.draft(),'review_policy':'automatic_course'},**old)
   atomic(root/'profiles/formal-v1.json',previous)
   (root/'budget.jsonl').write_text(''.join(json.dumps(dict(attempt_id=str(i),run_id='previous',task_id=str(i),request_hash='0'*64,state='reserved',category='formal'))+'\n' for i in range(8000)))
   resource=s.register_resources(ROOT/'data/fixed-course-v1/formal')
   self.assertIn('freeze',resource['ready_for'],resource['blocking_items'])
   profile=s.clone_profile('formal-v1',{'models':{'primary':'deepseek-flash','secondary':'deepseek-v4-pro'},'provider':{**helper.options()['provider'],'endpoint':'https://api.deepseek.com/responses'},'concurrency':2})
   with patch('app.provider_checks.make_image_probe',return_value=('data:image/png;base64,c3ludGhldGlj','123456')):
    result=s.prepare_experiment(profile['profile_id'],resource['resource_set_id'],s.set_credential('synthetic-secret'))
   self.assertTrue(result['ready'],result)
   self.assertEqual(result['blocking_items'],[])
   self.assertEqual(result['estimated_logical_generations'],3600)
   self.assertEqual(result['attempt_limit'],10800)
   self.assertEqual(s._profile('formal-v1')['models']['primary'],'deepseek-flash')
   self.assertEqual(len(calls),4)
   self.assertEqual(len(latest(root/'budget.jsonl')),8004)
   self.assertTrue((root/'profiles/history'/(previous['freeze_sha']+'.json')).is_file())
