import unittest,tempfile
from pathlib import Path
class JobsTest(unittest.TestCase):
 def test_missing_worker_blocks(self):
  from app.service import ApplicationService
  from uuid import uuid4
  with tempfile.TemporaryDirectory() as d:
   s=ApplicationService(project_root=Path(d),workspace=Path(d)/'work')
   q=dict(schema_version=1,client_request_id=str(uuid4()),kind='prepare',profile_id='formal-v1',resource_set_id='missing',source_run_id=None,demo_input=None)
   r=s.start_run(q,None)
   self.assertEqual(r['state'],'blocked');self.assertIn('kernel_unavailable',r['blocking_items'])
   self.assertEqual(s.start_run(q,None)['run_id'],r['run_id'])
 def test_single_slot_and_completed_output_hash(self):
  import sys,time,json,hashlib
  from tests.app.test_service import setup,request
  from app.jobs import atomic,read
  with tempfile.TemporaryDirectory() as td:
   s=setup(Path(td));first=s.start_run(request());second=s.start_run(request())
   self.assertIn('execution_slot_busy',second['blocking_items'])
   for _ in range(100):
    if s.get_run(first['run_id'])['state']=='completed':break
    time.sleep(.02)
   d=s.jobs.directory(first['run_id']);(d/'prediction.json').write_text('{}');checks=read(d/'integrity.json');checks['outputs']={'prediction.json':hashlib.sha256(b'{}').hexdigest()};atomic(d/'integrity.json',checks)
   self.assertEqual(s.jobs.resume_checks(first['run_id']),[]);(d/'prediction.json').write_text('tampered');self.assertIn('saved_output_changed',s.jobs.resume_checks(first['run_id']))
 def test_recovery_marks_dead_worker_interrupted(self):
  from app.service import ApplicationService
  from app.jobs import atomic
  from tests.app.test_service import request
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);s=ApplicationService(root,root/'w');r=s.start_run(request());rid=r['run_id'];s.jobs.update(rid,state='running');atomic(s.jobs.directory(rid)/'process.json',{'pid':99999999})
   reopened=ApplicationService(root,root/'w');self.assertEqual(reopened.get_run(rid)['state'],'interrupted')
 def test_recycled_pid_is_not_adopted_as_worker(self):
  import os
  from app.jobs import JobStore
  self.assertFalse(JobStore._alive(os.getpid(),'not-the-original-process'))
  self.assertTrue(JobStore._alive(os.getpid(),JobStore._fingerprint(os.getpid())))

 def test_recovery_replays_saved_result_after_worker_has_exited(self):
  import time,json,hashlib
  from app.service import ApplicationService
  from app.jobs import atomic,read
  from tests.app.test_service import request
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);s=ApplicationService(root,root/'w');run=s.start_run(request());rid=run['run_id']
   directory=s.jobs.directory(rid)
   s.jobs.update(rid,state='running',in_flight_count=1)
   atomic(directory/'process.json',{'pid':None})
   (directory/'worker-events.jsonl').write_text(json.dumps({'event_type':'finished','counts':{'in_flight_count':0}})+'\n')
   (directory/'predictions.jsonl').write_bytes(b'synthetic-result\n')
   atomic(directory/'worker-result.json',{'state':'completed'})
   reopened=ApplicationService(root,root/'w')
   deadline=time.monotonic()+2
   while reopened.get_run(rid)['state']=='running' and time.monotonic()<deadline:time.sleep(.01)
   self.assertEqual(reopened.get_run(rid)['state'],'completed')
   self.assertEqual(reopened.get_run(rid)['in_flight_count'],0)
   self.assertEqual(len(reopened.jobs.events(rid)),1)
   self.assertEqual(read(directory/'integrity.json')['outputs']['predictions.jsonl'],hashlib.sha256(b'synthetic-result\n').hexdigest())
