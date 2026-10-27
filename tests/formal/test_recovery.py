import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from tests.formal.test_runner import demo_fixture,fake_response
from formal_src.runner import run

class RecoveryTests(unittest.TestCase):
 def test_prediction_pair_recovers_after_first_file_commit(self):
  from formal_src import recovery
  with tempfile.TemporaryDirectory() as d:
   rd=Path(d);original=recovery._append_bytes
   def interrupted(path,data):
    if path.name=='prediction-checksums.jsonl':raise OSError('simulated disk interruption')
    original(path,data)
   with patch.object(recovery,'_append_bytes',side_effect=interrupted):
    with self.assertRaises(OSError):recovery.commit_prediction(rd,{'task_id':'t'},'sha')
   recovery.repair_prediction(rd)
   self.assertEqual(json.loads((rd/'predictions.jsonl').read_text()),{'task_id':'t'})
   self.assertEqual(json.loads((rd/'prediction-checksums.jsonl').read_text())['sha256'],'sha')
   recovery.repair_prediction(rd)
   self.assertEqual(len((rd/'predictions.jsonl').read_text().splitlines()),1)
 def test_transaction_rejects_unrelated_edit(self):
  from formal_src import recovery
  with tempfile.TemporaryDirectory() as d:
   rd=Path(d);original=recovery._append_bytes
   def interrupted(path,data):
    if path.name=='prediction-checksums.jsonl':raise OSError('stop')
    original(path,data)
   with patch.object(recovery,'_append_bytes',side_effect=interrupted):
    with self.assertRaises(OSError):recovery.commit_prediction(rd,{'task_id':'t'},'sha')
   (rd/'predictions.jsonl').write_text('tampered')
   with self.assertRaises(ValueError):recovery.repair_prediction(rd)
 def test_receipt_restores_answer_without_second_call(self):
  import formal_src.runner as kernel
  with tempfile.TemporaryDirectory() as d:
   spec,rd=demo_fixture(Path(d));calls=[];original=kernel.append
   def interrupted(path,row):
    if path.name=='attempts.jsonl' and row['state']=='completed':raise OSError('crash after response')
    original(path,row)
   def transport(endpoint,payload,key,timeout):calls.append(1);return fake_response(payload)
   with patch.object(kernel,'append',side_effect=interrupted):run(spec,lambda e:None,lambda:'continue',transport=transport,run_dir=rd)
   result=run(spec,lambda e:None,lambda:'continue',transport=transport,run_dir=rd)
   self.assertEqual(result['state'],'completed',result);self.assertEqual(len(calls),1)
 def test_unknown_requires_explicit_audited_failure_and_keeps_budget(self):
  from formal_src.recovery import issues,resolve_failure
  from formal_src.provider import APIError
  from formal_src.budget import latest
  with tempfile.TemporaryDirectory() as d:
   spec,rd=demo_fixture(Path(d));calls=[]
   def uncertain(*args):calls.append(1);raise APIError('transport_TimeoutError')
   first=run(spec,lambda e:None,lambda:'continue',transport=uncertain,run_dir=rd)
   self.assertIn('unknown_attempt_requires_inspection',first['blocking_items'])
   items=issues(rd);self.assertEqual(len(items),1)
   with self.assertRaises(ValueError):resolve_failure(rd,items[0]['attempt_id'],'',True)
   resolve_failure(rd,items[0]['attempt_id'],'operator: response unavailable, record failure without retry',True)
   result=run(spec,lambda e:None,lambda:'continue',transport=uncertain,run_dir=rd)
   self.assertEqual(result['state'],'completed',result);self.assertEqual(len(calls),1)
   attempts=latest(rd.parent.parent/'budget.jsonl');self.assertEqual(len(attempts),1)
   prediction=[json.loads(l) for l in (rd/'predictions.jsonl').read_text().splitlines() if json.loads(l)['condition']=='E2'][0]
   self.assertEqual(prediction['status'],'failed');self.assertIsNone(prediction['answer'])
 def test_partial_attempt_append_is_completed_once_from_journal(self):
  from formal_src import budget
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'attempts.jsonl'
   def torn(path,data):
    with path.open('ab') as f:f.write(data[:len(data)//2])
    raise OSError('power loss')
   row={'attempt_id':'a','state':'sent'}
   with patch.object(budget,'_append_bytes',side_effect=torn):
    with self.assertRaises(OSError):budget.append(p,row)
   self.assertEqual(budget.peek_latest(p),{'a':row})
   self.assertEqual(budget.latest(p),{'a':row})
   self.assertEqual(len(p.read_text().splitlines()),1)
 def test_receipt_identity_change_refuses_recovery(self):
  from formal_src.recovery import resolve_failure,issues
  from formal_src.provider import APIError
  with tempfile.TemporaryDirectory() as d:
   spec,rd=demo_fixture(Path(d))
   def uncertain(*args):raise APIError('transport_TimeoutError')
   run(spec,lambda e:None,lambda:'continue',transport=uncertain,run_dir=rd)
   item=issues(rd)[0];resolve_failure(rd,item['attempt_id'],'reviewer: unresolved request, preserve failure',True)
   (rd/'inputs.json').write_text('{}')
   result=run(spec,lambda e:None,lambda:'continue',transport=uncertain,run_dir=rd)
   self.assertIn('recovery_identity_changed',result['blocking_items'])
 def test_process_exit_mid_prediction_recovers_once(self):
  import subprocess,sys
  from formal_src.recovery import repair_prediction,prediction_rows
  with tempfile.TemporaryDirectory() as d:
   code="""import os,sys
from pathlib import Path
from formal_src import recovery
original=recovery._append_bytes
def crash(path,data):
 if path.name=='prediction-checksums.jsonl':os._exit(77)
 original(path,data)
recovery._append_bytes=crash
recovery.commit_prediction(Path(sys.argv[1]),{'task_id':'crash'},'sha')
"""
   result=subprocess.run([sys.executable,'-c',code,d]);self.assertEqual(result.returncode,77)
   rd=Path(d);self.assertEqual(prediction_rows(rd),[{'task_id':'crash'}])
   repair_prediction(rd);repair_prediction(rd)
   self.assertEqual(len((rd/'predictions.jsonl').read_text().splitlines()),1)
   self.assertEqual(len((rd/'prediction-checksums.jsonl').read_text().splitlines()),1)
 def test_saved_response_corruption_is_rejected(self):
  from formal_src.recovery import save_receipt,receipt_for
  from formal_src.budget import append
  with tempfile.TemporaryDirectory() as d:
   rd=Path(d);(rd/'spec.json').write_text('{}');(rd/'inputs.json').write_text('{}')
   row=dict(attempt_id='a',run_id='r',task_id='t',request_hash='h',category='demo',state='sent')
   append(rd/'attempts.jsonl',row);save_receipt(rd,row,{'answer':1},None)
   path=next((rd/'recovery/receipts').glob('*.json'));value=json.loads(path.read_text());value['payload']['parsed']['answer']=2;path.write_text(json.dumps(value))
   with self.assertRaisesRegex(ValueError,'recovery_checksum_mismatch'):receipt_for(rd,row)
