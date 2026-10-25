"""Breaks caught: re-scoring unsealed bytes or requiring model calls to verify."""
import importlib,json,tempfile,unittest,hashlib,socket
from pathlib import Path
from unittest.mock import patch

class VerificationTests(unittest.TestCase):
 def module(self):
  try:return importlib.import_module('formal_src.verification')
  except ModuleNotFoundError:self.fail('separate no-API verification adapter missing')
 def source(self,root):
  from tests.formal.test_runner import demo_fixture,fake_response
  from formal_src.runner import run
  spec,rd=demo_fixture(root)
  self.assertEqual(run(spec,lambda e:None,lambda:'continue',transport=lambda endpoint,payload,key,timeout:fake_response(payload),run_dir=rd)['state'],'completed')
  return spec,rd
 def test_demo_integrity_recheck_never_calls_network_or_claims_accuracy(self):
  with tempfile.TemporaryDirectory() as td:
   spec,rd=self.source(Path(td));before=(rd/'predictions.jsonl').read_bytes()
   with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
    result=self.module().verify_run(rd)
   self.assertEqual(result['state'],'completed')
   summary=json.loads((rd/'summary.json').read_text())
   self.assertEqual(summary['engineering']['verification_kind'],'integrity_only')
   self.assertEqual(summary['metrics'],{})
   self.assertEqual((rd/'predictions.jsonl').read_bytes(),before)
   self.assertFalse(summary['audit_complete'])
 def test_tampered_or_unsealed_prediction_is_rejected_before_evaluation_lookup(self):
  with tempfile.TemporaryDirectory() as td:
   spec,rd=self.source(Path(td));(rd/'predictions.jsonl').write_text('tampered')
   result=self.module().verify_run(rd)
   self.assertEqual(result['state'],'blocked');self.assertIn('prediction_seal_mismatch',result['blocking_items'])
  with tempfile.TemporaryDirectory() as td:
   rd=Path(td);(rd/'spec.json').write_text(json.dumps({'kind':'demo'}))
   self.assertEqual(self.module().verify_run(rd)['state'],'blocked')
 def test_task_input_and_summary_tampering_are_distinctly_rejected(self):
  for name,code in [('tasks.json','task_seal_mismatch'),('inputs.json','input_seal_mismatch')]:
   with self.subTest(name=name),tempfile.TemporaryDirectory() as td:
    spec,rd=self.source(Path(td));(rd/name).write_text('{}')
    result=self.module().verify_run(rd)
    self.assertEqual(result['blocking_items'],[code])
 def test_verify_job_preserves_original_run_and_gets_own_summary_identity(self):
  from uuid import uuid4
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);spec,source=self.source(root)
   before={p.name:p.read_bytes() for p in source.iterdir() if p.is_file()}
   target=root/'runs'/str(uuid4());target.mkdir()
   request={**spec,'kind':'verify','run_id':target.name,'source_run_id':source.name,'demo_input':None}
   (target/'spec.json').write_text(json.dumps(request))
   result=self.module().verify_run(target)
   self.assertEqual(result['state'],'completed',result)
   self.assertEqual(json.loads((target/'summary.json').read_text())['run_id'],target.name)
   self.assertEqual(before,{p.name:p.read_bytes() for p in source.iterdir() if p.is_file()})
   self.module().check_seal(target)
 def test_full_synthetic_predictions_rescore_with_frozen_denominators(self):
  from tests.formal.test_runner import full_fixture,fake_response,SyntheticEncoder
  from formal_src.runner import run
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);spec,rd=full_fixture(root)
   questions=[json.loads(l) for l in (root/'inference_questions.jsonl').read_text().splitlines()]
   gold=root/'gold.jsonl';gold.write_text(''.join(json.dumps({'question_id':q['question_id'],'source_id':q['source_id'],'answer':'3 years','scale':''})+'\n' for q in questions))
   manifest={'resources':[{'kind':'evaluation','path':'gold.jsonl','sha256':hashlib.sha256(gold.read_bytes()).hexdigest()}]}
   from formal_src.provider import digest
   spec['resource_sha']=digest(manifest);(rd/'spec.json').write_text(json.dumps(spec))
   registry=root/'resources';registry.mkdir();(registry/'x.json').write_text(json.dumps({'root':str(root),'manifest':manifest}))
   with patch('formal_src.retrieval.LocalEncoder',SyntheticEncoder):
    result=run(spec,lambda e:None,lambda:'continue',transport=lambda endpoint,payload,key,timeout:fake_response(payload),run_dir=rd)
   self.assertEqual(result['state'],'completed')
   with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
    result=self.module().verify_run(rd)
   self.assertEqual(result['state'],'completed',result)
   summary=json.loads((rd/'summary.json').read_text())
   self.assertEqual(summary['metrics']['main|bm25|E2|primary']['question_count'],200)
   self.assertEqual(summary['metrics']['diagnostic|bm25|E2|primary']['question_count'],50)
   self.assertEqual(len(summary['main_comparisons']),4)
   rows=[json.loads(line) for line in (rd/'scored-answers.jsonl').read_text().splitlines()]
   self.assertEqual(len(rows),len(json.loads((rd/'tasks.json').read_text())))
   self.assertTrue({'task_id','question_id','partition','repeat','uem','em','f1','numeric'} <= rows[0].keys())
   self.assertTrue((rd/'results.csv').read_text().startswith('task_id,'))
   from scripts.course_entry import build_app
   from fastapi.testclient import TestClient
   client=TestClient(build_app(Path(__file__).resolve().parents[2],root))
   response=client.get(f'/api/v1/runs/{rd.name}/files/scored-answers.jsonl')
   self.assertEqual(response.status_code,200,response.text)
   service=client.app.state.service
   # The view index retains the strict RunResults contract and identifies its data.
   with patch.object(service.jobs,'get',return_value={}):
    self.assertEqual(service.get_results(rd.name,'answers').to_dict()['answer_tables'],['scored-answers.jsonl'])
    self.assertEqual(service.get_results(rd.name,'audit').to_dict()['answer_tables'],['audit-status.json'])
    self.assertEqual(service.get_results(rd.name,'retrieval').to_dict()['answer_tables'],['retrieval.jsonl'])
   from uuid import uuid4
   before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in rd.iterdir() if p.is_file()}
   target=root/'runs'/str(uuid4());target.mkdir()
   (target/'spec.json').write_text(json.dumps({**spec,'kind':'verify','run_id':target.name,'source_run_id':rd.name,'demo_input':None}))
   self.assertEqual(self.module().verify_run(target)['state'],'completed')
   self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in rd.iterdir() if p.is_file()})
   self.module().check_seal(target)
   self.assertEqual((target/'scored-answers.jsonl').read_bytes(),(rd/'scored-answers.jsonl').read_bytes())
   (rd/'scored-answers.jsonl').write_text('tampered')
   self.assertEqual(client.get(f'/api/v1/runs/{rd.name}/files/scored-answers.jsonl').status_code,409)
   self.assertTrue(summary['engineering']['synthetic'])
   self.assertFalse(summary['audit_complete'])

if __name__=='__main__':unittest.main()
