import unittest,tempfile,json,hashlib
from pathlib import Path
from unittest.mock import patch
from app.service import ApplicationService

ROOT=Path(__file__).resolve().parents[2]
class AdapterTests(unittest.TestCase):
 def test_capabilities_lists_persisted_profiles(self):
  with tempfile.TemporaryDirectory() as td:
   s=ApplicationService(ROOT,td);p=s.clone_profile('formal-v1',{'model':'chosen'})
   self.assertIn(p['profile_id'],[p['profile_id'] for p in s.capabilities()['profiles']])
 def test_code_sha_covers_inference_dependencies(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'src').mkdir();p=root/'src/inference.py';p.write_text('one')
   s=ApplicationService(root,root/'w');before=s._code_sha();p.write_text('two');self.assertNotEqual(before,s._code_sha())
 def test_catalog_transport_no_generation_or_budget(self):
  with tempfile.TemporaryDirectory() as td:
   s=ApplicationService(ROOT,td,catalog_transport=lambda endpoint,key,timeout:{'data':[{'id':'gpt-6.1-sol'}]})
   ref=s.set_credential('test-hidden-value');result=s.check_provider({'base_url':'https://example.invalid/v1','model':'gpt-6.1-sol'},ref,'catalog')
   self.assertTrue(result['ready']);self.assertEqual(result['sent_attempts'],0);self.assertFalse((Path(td)/'budget.jsonl').exists())
 def test_freeze_requires_explicit_profile_and_real_resources(self):
  with tempfile.TemporaryDirectory() as td:
   s=ApplicationService(ROOT,td);p=s.clone_profile('formal-v1',{})
   result=s.freeze_profile('formal-v1','missing',p['profile_id']);self.assertFalse(result['frozen']);self.assertIn('resources_missing',result['blocking_items'])
 def test_export_calls_allowlist_adapter_with_known_credentials(self):
  with tempfile.TemporaryDirectory() as td:
   s=ApplicationService(ROOT,td);s.set_credential('test-hidden-value')
   with patch.object(s.jobs,'get',return_value={}),patch('formal_src.course_export.export_bundle',return_value={'exported':False}) as export:
    s.export_course_bundle('2ad974ac-5914-4f8e-b459-7331da0447a2');self.assertEqual(export.call_args.kwargs['credential_values'],('test-hidden-value',))
 def test_frozen_experiment_binds_full_compiled_plan(self):
  from uuid import uuid4
  from app.jobs import atomic,read,digest
  from formal_src.run_plan import CANONICAL_PROFILE,compile_run_plan
  with tempfile.TemporaryDirectory() as td:
   s=ApplicationService(ROOT,td);cohorts={'main':['m'+str(i) for i in range(200)],'diagnostic':['d'+str(i) for i in range(50)],'s50':['m'+str(i) for i in range(50)]}
   frozen={**CANONICAL_PROFILE,'status':'frozen','freeze_sha':'a'*64};atomic(Path(td)/'profiles/formal-v1.json',frozen)
   q=dict(schema_version=1,client_request_id=str(uuid4()),kind='experiment',profile_id='formal-v1',resource_set_id='r',source_run_id=None,demo_input=None)
   with patch.object(s,'_compiled_plan',return_value=compile_run_plan(CANONICAL_PROFILE,cohorts)),patch.object(s,'preflight',return_value={'blocking_items':['fixture_block'],'estimated_logical_generations':6600}),patch.object(s.resources,'record',return_value={'manifest':{}}):
    run=s.start_run(q);spec=read(s.jobs.directory(run['run_id'])/'spec.json');self.assertEqual(len(spec['plan']['units']),7600);self.assertTrue(spec['plan']['executable']);self.assertEqual(spec['freeze_sha'],'a'*64)
 def test_results_calls_seal_validator_before_exposing_experiment(self):
  from uuid import uuid4
  with tempfile.TemporaryDirectory() as td:
   s=ApplicationService(ROOT,td);rid=str(uuid4())
   with patch.object(s.jobs,'get',return_value={'kind':'experiment'}),patch('formal_src.verification.check_seal',side_effect=ValueError('changed')):
    with self.assertRaisesRegex(ValueError,'changed'):s.get_results(rid,'answers')
 def test_preflight_rejects_frozen_runtime_tampering(self):
  from uuid import uuid4
  from app.jobs import atomic
  from formal_src.run_plan import CANONICAL_PROFILE
  with tempfile.TemporaryDirectory() as td:
   s=ApplicationService(ROOT,td);atomic(Path(td)/'profiles/formal-v1.json',{**CANONICAL_PROFILE,'status':'frozen','freeze_sha':'a'*64})
   q=dict(schema_version=1,client_request_id=str(uuid4()),kind='experiment',profile_id='formal-v1',resource_set_id='r',source_run_id=None,demo_input=None)
   with patch.object(s,'check_resources',return_value={'ready_for':['experiment'],'blocking_items':[]}),patch.object(s.resources,'record',return_value={'manifest':{}}):
    self.assertIn('frozen_profile_invalid',s.preflight(q)['blocking_items'])
 def test_demo_sealed_source_can_integrity_verify_without_evaluation(self):
  from uuid import uuid4
  from app.jobs import atomic
  from tests.app.test_service import setup,request
  with tempfile.TemporaryDirectory() as td:
   s=setup(Path(td));source=str(uuid4());atomic(s.jobs.directory(source)/'sealed.json',{'purpose':'demo'});atomic(s.jobs.directory(source)/'spec.json',{'kind':'demo'})
   q=request();q.update(kind='verify',source_run_id=source)
   self.assertTrue(s.preflight(q)['ready'])
 def test_prepare_binds_registered_root_and_blocks_approved_bundle(self):
  from tests.app.test_service import setup,request
  from app.jobs import read
  with tempfile.TemporaryDirectory() as td:
   s=setup(Path(td));q=request();q['kind']='prepare'
   with patch.object(s.jobs,'start'):
    run=s.start_run(q)
   self.assertEqual(read(s.jobs.directory(run['run_id'])/'spec.json')['resolved_config']['prepare_output'],str((Path(td)/'data').resolve()))
 def test_prepare_rejects_bundle_with_imported_reviews(self):
  from tests.app.test_service import setup,request
  from app.jobs import atomic
  with tempfile.TemporaryDirectory() as td:
   s=setup(Path(td));record=s.resources.record('demo');record['reviews']={'reviews':[{'approved':True}]};atomic(s.resources.root/'demo.json',record)
   self.assertIn('approved_resources_immutable',s.preflight(request())['blocking_items'])
 def test_operational_generation_consumes_shared_development_budget(self):
  from formal_src.budget import latest
  with tempfile.TemporaryDirectory() as td:
   raw={'id':'probe-response','model':'gpt-6.1-sol','status':'completed','usage':{'input_tokens':10,'output_tokens':10},'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':json.dumps({'answer':'READY','scale':'','citations':['operational-probe'],'abstained':False})}]}]}
   s=ApplicationService(ROOT,td,provider_transport=lambda *args:raw);ref=s.set_credential('hidden-operation-key')
   result=s.check_provider({'endpoint':'https://example.invalid/v1/responses','model':'gpt-6.1-sol'},ref,'generation')
   self.assertEqual(result['sent_attempts'],1);self.assertEqual(result['purpose'],'operational_probe');records=list(latest(Path(td)/'budget.jsonl').values());self.assertEqual(len(records),1);self.assertEqual(records[0]['category'],'development_validation');self.assertEqual(records[0]['state'],'completed');self.assertTrue(result['ready'])
