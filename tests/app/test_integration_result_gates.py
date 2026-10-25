import unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
from app.service import ApplicationService
from tests.acceptance.test_verification import VerificationTests
class ResultGatesTests(unittest.TestCase):
 def test_demo_summary_rejects_tampered_predictions(self):
  from formal_src.verification import VerificationBlocked
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);_,rd=VerificationTests().source(root)
   service=ApplicationService(Path(__file__).resolve().parents[2],root)
   (rd/'predictions.jsonl').write_text('tampered')
   with patch.object(service.jobs,'get',return_value={'kind':'demo'}):
    with self.assertRaises(VerificationBlocked):service.get_results(rd.name)
 def test_historical_attempts_are_reconciled_from_append_only_events(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);service=ApplicationService(Path(__file__).resolve().parents[2],root)
   from uuid import uuid4
   rid=str(uuid4());rd=service.jobs.directory(rid);rd.mkdir()
   rows=[{'attempt_id':'a','state':'reserved'},{'attempt_id':'a','state':'sent'},{'attempt_id':'a','state':'completed','error_code':'api_error'},{'attempt_id':'b','state':'unknown'}]
   (rd/'attempts.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
   with patch.object(service.jobs,'get',return_value={'run_id':rid,'attempt_count':0,'failure_count':0}):state=service.get_run(rid)
   self.assertEqual(state['attempt_count'],2);self.assertEqual(state['failure_count'],1)
 def test_probe_readiness_requires_answered_ready_and_parse_errors_are_known(self):
  from formal_src.budget import latest
  for answer in ('WRONG',None):
   with self.subTest(answer=answer),tempfile.TemporaryDirectory() as td:
    raw={'id':'probe','model':'gpt-6.1-sol','status':'completed','usage':{'input_tokens':10,'output_tokens':10},'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':json.dumps({'answer':answer,'scale':'','citations':['operational-probe'],'abstained':answer is None})}]}]}
    service=ApplicationService(Path(__file__).resolve().parents[2],Path(td),provider_transport=lambda *a:raw)
    result=service.check_provider({'endpoint':'https://example.invalid/v1/responses','model':'gpt-6.1-sol'},service.set_credential('synthetic-key'),'generation')
    self.assertFalse(result['ready']);self.assertEqual(next(iter(latest(Path(td)/'budget.jsonl').values()))['state'],'completed')
