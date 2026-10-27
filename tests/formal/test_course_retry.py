import json,tempfile,unittest
from pathlib import Path
from tests.formal.test_runner import demo_fixture,fake_response
from formal_src.runner import run
from formal_src.provider import APIError

class CourseRetryTests(unittest.TestCase):
 def fixture(self,root):
  spec,rd=demo_fixture(root);spec['resolved_config']['review_policy']='automatic_course';(rd/'spec.json').write_text(json.dumps(spec));return spec,rd
 def test_timeout_retries_twice_and_saves_success(self):
  with tempfile.TemporaryDirectory() as td:
   spec,rd=self.fixture(Path(td));calls=[]
   def transport(endpoint,payload,key,timeout):
    calls.append(1)
    if len(calls)<3:raise APIError('transport_TimeoutError')
    return fake_response(payload)
   result=run(spec,lambda e:None,lambda:'continue',transport=transport,run_dir=rd)
   self.assertEqual(result['state'],'completed',result);self.assertEqual(len(calls),3)
   p=json.loads((rd/'predictions.jsonl').read_text().splitlines()[-1]);self.assertEqual(p['status'],'answered');self.assertEqual(len(p['attempt_refs']),3)
   logs=[json.loads(l) for l in (rd/'request-errors.jsonl').read_text().splitlines()];self.assertEqual(len(logs),2);self.assertIn('elapsed_ms',logs[0])
 def test_exhausted_request_is_saved_and_does_not_block(self):
  with tempfile.TemporaryDirectory() as td:
   spec,rd=self.fixture(Path(td));calls=[]
   def transport(*args):calls.append(1);raise APIError('stream_ended_without_final_response')
   result=run(spec,lambda e:None,lambda:'continue',transport=transport,run_dir=rd)
   self.assertEqual(result['state'],'completed',result);self.assertEqual(len(calls),3)
   p=json.loads((rd/'predictions.jsonl').read_text().splitlines()[-1]);self.assertEqual(p['status'],'failed')
   again=run(spec,lambda e:None,lambda:'continue',transport=lambda *a:self.fail('completed task resent'),run_dir=rd);self.assertEqual(again['state'],'completed')

 def test_pause_between_attempts_resumes_remaining_retries_only(self):
  with tempfile.TemporaryDirectory() as td:
   spec,rd=self.fixture(Path(td));control={'action':'continue'};calls=[]
   def transport(*args):
    calls.append(1);control['action']='pause';raise APIError('http_502')
   first=run(spec,lambda e:None,control,transport=transport,run_dir=rd)
   self.assertEqual(first['state'],'paused',first)
   def resumed(endpoint,payload,key,timeout):
    calls.append(1)
    if len(calls)==2:raise APIError('http_502')
    return fake_response(payload)
   second=run(spec,lambda e:None,{'action':'continue'},transport=resumed,run_dir=rd)
   self.assertEqual(second['state'],'completed',second);self.assertEqual(len(calls),3)
   p=json.loads((rd/'predictions.jsonl').read_text().splitlines()[-1]);self.assertEqual(len(p['attempt_refs']),3)
 def test_authentication_error_blocks_without_retry(self):
  with tempfile.TemporaryDirectory() as td:
   spec,rd=self.fixture(Path(td));calls=[]
   def transport(*args):calls.append(1);raise APIError('http_401')
   result=run(spec,lambda e:None,lambda:'continue',transport=transport,run_dir=rd)
   self.assertEqual(result['state'],'blocked',result);self.assertEqual(len(calls),1)

 def test_exhausted_generation_continues_to_next_task(self):
  from unittest.mock import patch
  from formal_src.runner import demo_tasks
  with tempfile.TemporaryDirectory() as td:
   spec,rd=self.fixture(Path(td));calls=[]
   def two_tasks(spec,questions):
    qs,tasks=demo_tasks(spec,questions);task=tasks[-1]
    return qs,[task,{**task,'task_id':task['task_id'][:-1]+'2','repeat':2}]
   def transport(endpoint,payload,key,timeout):
    calls.append(1)
    if len(calls)<=3:raise APIError('http_502')
    return fake_response(payload)
   with patch('formal_src.runner.demo_tasks',side_effect=two_tasks):
    result=run(spec,lambda e:None,lambda:'continue',transport=transport,run_dir=rd)
   self.assertEqual(result['state'],'completed',result);self.assertEqual(len(calls),4)
   rows=[json.loads(l) for l in (rd/'predictions.jsonl').read_text().splitlines()]
   self.assertEqual(len(rows),2);self.assertEqual(rows[0]['status'],'failed');self.assertEqual(rows[1]['status'],'answered')
 def test_manual_resume_does_not_retrip_on_historical_network_errors(self):
  from formal_src.budget import append
  with tempfile.TemporaryDirectory() as td:
   spec,rd=self.fixture(Path(td))
   for i in range(100):
    append(rd/'attempts.jsonl',{'attempt_id':str(i),'task_id':'historical-'+str(i),'state':'completed','error_code':'transport_TimeoutError' if i<9 else None,'parse_status':'failed' if i<9 else 'answered'})
   calls=[]
   def transport(endpoint,payload,key,timeout):calls.append(1);return fake_response(payload)
   result=run(spec,lambda e:None,lambda:'continue',transport=transport,run_dir=rd)
   self.assertEqual(result['state'],'completed',result);self.assertEqual(len(calls),1)
   from formal_src.budget import latest
   self.assertEqual(sum(bool(r.get('error_code')) for r in latest(rd/'attempts.jsonl').values()),9)
