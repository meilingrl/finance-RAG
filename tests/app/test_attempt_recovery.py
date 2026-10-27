import tempfile,unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app.api import create_app
from app.service import ApplicationService
from formal_src.runner import run
from formal_src.provider import APIError
from tests.formal.test_runner import demo_fixture

class AttemptRecoveryTests(unittest.TestCase):
 def fixture(self,root):
  spec,rd=demo_fixture(root);s=ApplicationService(root,root)
  q={k:spec[k] for k in ('schema_version','client_request_id','kind','profile_id','resource_set_id','source_run_id','demo_input')}
  (rd/'spec.json').unlink();s.jobs.register(q,spec,[])
  def unknown(*args):raise APIError('transport_TimeoutError')
  run(spec,lambda e:None,lambda:'continue',transport=unknown,run_dir=rd)
  s.jobs.update(rd.name,state='blocked',available_actions=['resume','inspect'])
  return s,rd
 def test_http_resolution_requires_session_confirmation_and_preserves_history(self):
  with tempfile.TemporaryDirectory() as td:
   s,rd=self.fixture(Path(td));client=TestClient(create_app(s));url='/api/v1/runs/'+rd.name+'/recovery'
   item=client.get(url).json()['items'][0];original=(rd/'attempts.jsonl').read_bytes()
   body={'attempt_id':item['attempt_id'],'note':'Operator checked request; result unavailable. Count as failure.','confirmed':True}
   self.assertEqual(client.post(url,json=body).status_code,403)
   headers={'x-session-id':s.session_id}
   self.assertEqual(client.post(url,headers=headers,json={**body,'confirmed':False}).status_code,400)
   self.assertEqual(client.post(url,headers=headers,json=body).status_code,200)
   self.assertEqual((rd/'attempts.jsonl').read_bytes(),original)
   self.assertTrue(client.get(url).json()['items'][0]['recoverable'])
   self.assertEqual(s.jobs.resume_checks(rd.name),[])
 def test_running_worker_is_not_reconciled(self):
  with tempfile.TemporaryDirectory() as td:
   s,rd=self.fixture(Path(td));s.jobs.update(rd.name,state='running')
   with self.assertRaises(ValueError):s.resolve_attempt(rd.name,{'attempt_id':'fake','note':'test operator note','confirmed':True})
 def test_status_reads_torn_attempt_from_committed_journal(self):
  from unittest.mock import patch
  from formal_src import budget
  with tempfile.TemporaryDirectory() as td:
   s,rd=self.fixture(Path(td));row=next(iter(budget.latest(rd/'attempts.jsonl').values()))
   def torn(path,data):
    with path.open('ab') as f:f.write(data[:len(data)//2])
    raise OSError('interrupted append')
   with patch.object(budget,'_append_bytes',side_effect=torn):
    with self.assertRaises(OSError):budget.append(rd/'attempts.jsonl',row)
   self.assertEqual(s.get_run(rd.name)['attempt_count'],1)

 def test_preflight_budget_reads_torn_reservation_without_refund(self):
  from unittest.mock import patch
  from formal_src import budget
  with tempfile.TemporaryDirectory() as td:
   s,rd=self.fixture(Path(td));path=Path(td)/'budget.jsonl'
   original=s._budget()[0]
   def torn(path,data):
    with path.open('ab') as f:f.write(data[:len(data)//2])
    raise OSError('interrupted reservation')
   with patch.object(budget,'_append_bytes',side_effect=torn):
    with self.assertRaises(OSError):budget.Budget(path).reserve(rd.name,'another','hash','retry_demo')
   remaining,categories,blocks=s._budget()
   self.assertEqual(blocks,[]);self.assertEqual(remaining,original-1)
