import unittest,tempfile,json,hashlib,sys,time
from pathlib import Path
from uuid import uuid4
from fastapi.testclient import TestClient
from app.service import ApplicationService
from app.api import create_app

def request(rid='demo',kind='prepare'):
 return dict(schema_version=1,client_request_id=str(uuid4()),kind=kind,profile_id='formal-v1',resource_set_id=rid,source_run_id=None,demo_input=None)
def setup(root):
 data=root/'data';data.mkdir();(data/'input.json').write_text('{}');(data/'resources.json').write_text(json.dumps(dict(schema_version=1,resource_set_id='demo',purpose='demo',resources=[dict(kind='input',path='input.json',sha256=hashlib.sha256(b'{}').hexdigest())])))
 s=ApplicationService(root,root/'w',worker_command=[sys.executable,str(Path(__file__).with_name('fixtures.py'))]);s.register_resources(data);return s
class ServiceTests(unittest.TestCase):
 def test_profiles_and_secrets(self):
  with tempfile.TemporaryDirectory() as td:
   s=setup(Path(td));key='TEST-SECRET-NOT-PERSISTED';ref=s.set_credential(key)
   with self.assertRaises(ValueError):s.save_settings({'api_key':key})
   with self.assertRaises(ValueError):s.save_profile('formal-v1',{'model':'x'})
   clone=s.clone_profile('formal-v1',{'model':'custom'});self.assertEqual(s.save_profile(clone['profile_id'],{'model':'next'})['model'],'next')
   self.assertNotIn(ref,ApplicationService(Path(td),Path(td)/'w').credentials)
   self.assertTrue(all(key not in p.read_text() for p in (Path(td)/'w').rglob('*') if p.is_file()))
 def test_background_idempotency_pause_and_events(self):
  with tempfile.TemporaryDirectory() as td:
   s=setup(Path(td));q=request();r=s.start_run(q);rid=r['run_id'];self.assertEqual(s.start_run(q)['run_id'],rid)
   self.assertEqual(s.control_run(rid,'pause')['state'],'pause_requested')
   for _ in range(100):
    r=s.get_run(rid)
    if r['state']=='paused':break
    time.sleep(.02)
   self.assertEqual(r['state'],'paused');self.assertEqual(r['in_flight_count'],0)
   e=s.list_events(rid);self.assertEqual([x['seq'] for x in e],[1,2]);self.assertEqual(len(s.list_events(rid,1)),1)
 def test_http_local_session(self):
  with tempfile.TemporaryDirectory() as td:
   s=setup(Path(td));client=TestClient(create_app(s));q=request()
   self.assertEqual(client.post('/api/v1/runs',json={'request':q}).status_code,403)
   h={'x-session-id':s.session_id,'origin':'https://attacker.invalid'}
   self.assertEqual(client.post('/api/v1/runs',headers=h,json={'request':q}).status_code,403)
   h={'x-session-id':s.session_id};r=client.post('/api/v1/runs',headers=h,json={'request':q});self.assertEqual(r.status_code,202)
   rid=r.json()['run_id'];self.assertEqual(client.get('/api/v1/runs/'+rid).status_code,200)
   # Let subprocess finish before TemporaryDirectory removes its files.
   for _ in range(100):
    if s.get_run(rid)['state']=='completed':break
    time.sleep(.02)
 def test_crash_attempt_boundaries(self):
  for scenario,blocked in [('reserved',False),('sent',True),('completed',False)]:
   with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as td:
    s=setup(Path(td));c=s.clone_profile('formal-v1',{'worker_scenario':scenario});q=request();q['profile_id']=c['profile_id'];r=s.start_run(q);rid=r['run_id']
    for _ in range(100):
     if s.get_run(rid)['state']=='interrupted':break
     time.sleep(.02)
    self.assertEqual(s.get_run(rid)['state'],'interrupted');self.assertEqual(bool(s.jobs.resume_checks(rid)),blocked)
 def test_http_crash_and_resume_integrity(self):
  for scenario in ('reserved','sent','completed'):
   with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as td:
    s=setup(Path(td));c=s.clone_profile('formal-v1',{'worker_scenario':scenario});q=request();q['profile_id']=c['profile_id'];client=TestClient(create_app(s));h={'x-session-id':s.session_id}
    r=client.post('/api/v1/runs',headers=h,json={'request':q}).json();rid=r['run_id']
    for _ in range(100):
     if s.get_run(rid)['state']=='interrupted':break
     time.sleep(.02)
    result=client.post('/api/v1/runs/'+rid+'/control',headers=h,json={'action':'resume'})
    self.assertEqual(result.status_code,200)
    if scenario=='sent':self.assertIn('unknown_attempt_requires_inspection',result.json()['blocking_items'])
    else:
     for _ in range(100):
      if s.get_run(rid)['state']=='interrupted':break
      time.sleep(.02)
 def test_resume_checks_actual_resource_and_output(self):
  from app.jobs import atomic
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);s=setup(root);c=s.clone_profile('formal-v1',{'worker_scenario':'reserved'});q=request();q['profile_id']=c['profile_id'];rid=s.start_run(q)['run_id']
   for _ in range(100):
    if s.get_run(rid)['state']=='interrupted':break
    time.sleep(.02)
   (root/'data/input.json').write_text('tampered')
   self.assertIn('resource_sha_mismatch',s.control_run(rid,'resume')['blocking_items'])
 def test_credential_not_in_process_argv_or_disk(self):
  import subprocess
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);s=setup(root);key='secret-value-987654321';ref=s.set_credential(key);rid=s.start_run(request(),ref)['run_id']
   pid=json.loads((s.jobs.directory(rid)/'process.json').read_text())['pid'];args=subprocess.check_output(['ps','-p',str(pid),'-o','args='],text=True);self.assertNotIn(key,args)
   for _ in range(100):
    if s.get_run(rid)['state']=='completed':break
    time.sleep(.02)
   self.assertTrue(all(key not in p.read_text() for p in (root/'w').rglob('*') if p.is_file()))
 def test_preflight_budget_counts_unique_reservations(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);s=setup(root);self.assertEqual(s.preflight(request())['budget_remaining'],8000)
   ledger=root/'w/budget.jsonl';record=dict(attempt_id='a',run_id=str(uuid4()),task_id='synthetic',request_hash='0'*64,state='reserved',category='retry_demo');ledger.write_text(json.dumps(record)+'\n'+json.dumps(dict(record,state='sent'))+'\n')
   self.assertEqual(s.preflight(request())['budget_remaining'],7999)
   ledger.write_text('broken\n');self.assertIn('budget_check_failed',s.preflight(request())['blocking_items'])

class ResumeIntegrityTests(unittest.TestCase):
 def wait(self,s,rid,states):
  for _ in range(200):
   result=s.get_run(rid)
   if result['state'] in states:return result
   time.sleep(.01)
  self.fail('worker did not reach expected state')
 def test_resume_rechecks_budget_and_profile_readiness(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);s=setup(root);(root/'w/budget.jsonl').write_text('broken\n');rid=s.start_run(request())['run_id']
   result=s.control_run(rid,'resume')
   self.assertEqual(result['state'],'blocked');self.assertIn('budget_check_failed',result['blocking_items'])
 def test_first_launch_after_missing_credential_builds_input_projection(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);s=setup(root);q=request();q.update(kind='demo',demo_input=dict(doc_id='d',question='What?',retriever='bm25',groups=['E2']))
   run=s.start_run(q);rid=run['run_id'];self.assertEqual(run['state'],'blocked')
   s.set_credential('test-session-key');result=s.control_run(rid,'resume')
   self.assertTrue((s.jobs.directory(rid)/'inputs.json').exists())
   self.wait(s,rid,{'completed','failed','interrupted'})
 def test_concurrent_resume_launches_exactly_one_real_worker(self):
  import concurrent.futures,threading
  from unittest.mock import patch
  from app import jobs
  with tempfile.TemporaryDirectory() as td:
   s=setup(Path(td));rid=s.start_run(request())['run_id'];s.control_run(rid,'pause');self.wait(s,rid,{'paused'})
   barrier=threading.Barrier(2);original_check=s.jobs.resume_checks;original_popen=jobs.subprocess.Popen;spawned=[]
   def checked(identity):
    out=original_check(identity);barrier.wait(timeout=3);return out
   def spawn(*args,**kw):
    child=original_popen(*args,**kw);spawned.append(child.pid);return child
   with patch.object(s.jobs,'resume_checks',side_effect=checked),patch('app.jobs.subprocess.Popen',side_effect=spawn):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
     results=list(pool.map(lambda _:s.control_run(rid,'resume'),range(2)))
   self.assertEqual(len(spawned),1)
   self.wait(s,rid,{'completed','failed','interrupted'})
 def test_resume_http_preserves_budget_profile_and_seal_gates(self):
  from app.jobs import atomic
  cases=[('budget','budget_check_failed'),('experiment','profile_not_frozen'),('verify','source_not_sealed'),('exhausted','retry_demo_budget_exhausted')]
  for case,code in cases:
   with self.subTest(case=case),tempfile.TemporaryDirectory() as td:
    root=Path(td);s=setup(root);q=request()
    if case=='budget':(root/'w/budget.jsonl').write_text('broken\n')
    if case=='experiment':q['kind']='experiment';s.set_credential('synthetic-key')
    if case=='verify':q.update(kind='verify',source_run_id=str(uuid4()))
    if case=='exhausted':
     q.update(kind='demo',demo_input=dict(doc_id='d',question='What?',retriever='bm25',groups=['E2']));s.set_credential('synthetic-key')
     (root/'w/budget.jsonl').write_text(''.join(json.dumps(dict(attempt_id=str(i),run_id=str(uuid4()),task_id='synthetic',request_hash='0'*64,state='reserved',category='retry_demo'))+'\n' for i in range(900)))
    rid=s.start_run(q)['run_id'];client=TestClient(create_app(s));result=client.post('/api/v1/runs/'+rid+'/control',headers={'x-session-id':s.session_id},json={'action':'resume'})
    self.assertEqual(result.status_code,200);self.assertEqual(result.json()['state'],'blocked');self.assertIn(code,result.json()['blocking_items']);self.assertFalse((s.jobs.directory(rid)/'process.json').exists())
 def test_true_partial_resume_budgets_only_remaining_generations(self):
  from app.jobs import atomic,read
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);s=setup(root);manifest=json.loads((root/'data/resources.json').read_text());sha=manifest['resources'][0]['sha256'];manifest.update(purpose='formal',qualification={k:True for k in ('source_disjoint','cohorts_validated','inputs_complete','development_passed','validation_passed','compatibility_passed','throughput_passed','budget_validated')});manifest['qualification']['evidence_sha256']=sha;manifest['resources'].append(dict(kind='model',path='input.json',sha256=sha));(root/'data/resources.json').write_text(json.dumps(manifest));s.register_resources(root/'data');s.import_reviews('demo',{'reviews':[dict(reviewer='independent',reviewed_at='2026-10-24T00:00:00Z',asset_sha256=sha,approved=True,qa_exposed=False)]})
   profile=s.clone_profile('formal-v1',{'worker_scenario':'reserved'});profile['status']='frozen';atomic(root/'w/profiles'/(profile['profile_id']+'.json'),profile);q=request();q.update(kind='experiment',profile_id=profile['profile_id']);ref=s.set_credential('synthetic-key');rid=s.start_run(q,ref)['run_id'];self.wait(s,rid,{'interrupted'})
   s.jobs.update(rid,completed_logical_count=6500)
   (root/'w/budget.jsonl').write_text(''.join(json.dumps(dict(attempt_id=str(i),run_id=rid,task_id='synthetic',request_hash='0'*64,state='reserved',category='formal'))+'\n' for i in range(6500)))
   self.assertIn('budget_exhausted',s.preflight(q)['blocking_items'])
   result=s.control_run(rid,'resume');self.assertNotIn('budget_exhausted',result['blocking_items']);self.assertNotEqual(result['state'],'blocked');self.wait(s,rid,{'interrupted'})
 def test_overlapping_http_resumes_have_one_owner_after_fast_worker_exits(self):
  import concurrent.futures,threading
  from unittest.mock import patch
  from app import jobs
  with tempfile.TemporaryDirectory() as td:
   s=setup(Path(td));rid=s.start_run(request())['run_id'];s.control_run(rid,'pause');self.wait(s,rid,{'paused'})
   barrier=threading.Barrier(2);guard=threading.Lock();original_check=s.jobs.resume_checks;original_popen=jobs.subprocess.Popen;spawned=[];calls=[]
   def checked(identity):
    out=original_check(identity)
    with guard:ordinal=len(calls);calls.append(identity)
    barrier.wait(timeout=3)
    if ordinal:time.sleep(.5)
    return out
   def spawn(*args,**kw):
    child=original_popen(*args,**kw);spawned.append(child.pid);return child
   def resume(_):
    with TestClient(create_app(s)) as client:return client.post('/api/v1/runs/'+rid+'/control',headers={'x-session-id':s.session_id},json={'action':'resume'})
   with patch.object(s.jobs,'resume_checks',side_effect=checked),patch('app.jobs.subprocess.Popen',side_effect=spawn):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(resume,range(2)))
   self.assertTrue(all(result.status_code==200 for result in results));self.assertEqual(len(spawned),1);self.assertEqual(self.wait(s,rid,{'completed','failed','interrupted'})['state'],'completed')
