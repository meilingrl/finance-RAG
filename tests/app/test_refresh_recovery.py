import tempfile,time,unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app.api import create_app
from tests.app.test_service import setup,request

class RefreshRecoveryTests(unittest.TestCase):
 def test_lost_start_response_can_find_original_without_launching_again(self):
  with tempfile.TemporaryDirectory() as td:
   s=setup(Path(td));q=request();run=s.start_run(q)
   client=TestClient(create_app(s))
   result=client.get('/api/v1/runs',params={'client_request_id':q['client_request_id']})
   self.assertEqual(result.status_code,200)
   self.assertEqual(result.json()['run_id'],run['run_id'])
   self.assertEqual(len(list(s.jobs.runs.glob('*/spec.json'))),1)
   for _ in range(200):
    if s.get_run(run['run_id'])['state']=='completed':break
    time.sleep(.01)
   self.assertEqual(client.get('/api/v1/runs').json()['runs'][0]['run_id'],run['run_id'])
 def test_unknown_submission_is_not_automatically_created(self):
  with tempfile.TemporaryDirectory() as td:
   s=setup(Path(td));client=TestClient(create_app(s))
   self.assertIsNone(client.get('/api/v1/runs',params={'client_request_id':request()['client_request_id']}).json()['run_id'])
   self.assertEqual(list(s.jobs.runs.glob('*/spec.json')),[])
 def test_service_process_exit_does_not_kill_worker_and_restart_adopts_it(self):
  import subprocess,sys,json
  from app.service import ApplicationService
  with tempfile.TemporaryDirectory() as td:
   code="from pathlib import Path; from tests.app.test_service import setup,request; import sys,os; s=setup(Path(sys.argv[1])); r=s.start_run(request()); print(r['run_id'],flush=True); os._exit(0)"
   rid=subprocess.check_output([sys.executable,'-c',code,td],text=True).strip()
   reopened=ApplicationService(Path(td),Path(td)/'w')
   for _ in range(300):
    state=reopened.get_run(rid)
    if state['state']=='completed':break
    time.sleep(.01)
   self.assertEqual(state['state'],'completed')
   self.assertEqual(state['in_flight_count'],0)
   self.assertEqual(len(list(reopened.jobs.runs.glob('*/spec.json'))),1)
