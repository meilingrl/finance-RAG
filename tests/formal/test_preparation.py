import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from tests.formal.test_runner import demo_fixture,fake_response
from formal_src.runner import run

class PreparationTests(unittest.TestCase):
 def test_preparation_pause_and_resume_uses_checkpoint(self):
  with tempfile.TemporaryDirectory() as td:
   spec,rd=demo_fixture(Path(td));events=[]
   def emit(e):
    events.append(e)
    if e['event_type'].startswith('preparing_1_of_'):(rd/'control.json').write_text('{"action":"pause"}')
   result=run(spec,emit,{'run_dir':str(rd)},transport=lambda *a:self.fail('sent before preparation completed'))
   self.assertEqual(result['state'],'paused');self.assertTrue(list((rd/'preparation-cache').glob('*.json')))
   (rd/'control.json').write_text('{"action":"continue"}')
   with patch('formal_src.runner.retrieve',side_effect=AssertionError('recomputed saved retrieval')):
    result=run(spec,events.append,{'run_dir':str(rd)},transport=lambda endpoint,payload,key,timeout:fake_response(payload))
   self.assertEqual(result['state'],'completed',result)
   self.assertTrue(any(e['phase']=='prepare' for e in events))
 def test_corrupt_cache_stops_before_send(self):
  with tempfile.TemporaryDirectory() as td:
   spec,rd=demo_fixture(Path(td))
   def emit(e):
    if e['event_type'].startswith('preparing_1_of_'):(rd/'control.json').write_text('{"action":"pause"}')
   run(spec,emit,{'run_dir':str(rd)},transport=lambda *a:self.fail('sent'))
   p=next((rd/'preparation-cache').glob('*.json'));v=json.loads(p.read_text());v['sha256']='0'*64;p.write_text(json.dumps(v))
   (rd/'control.json').write_text('{"action":"continue"}')
   result=run(spec,lambda e:None,{'run_dir':str(rd)},transport=lambda *a:self.fail('sent'))
   self.assertEqual(result['state'],'blocked');self.assertIn('preparation_checkpoint_changed',result['blocking_items'])

 def test_parallel_probe_saves_results_before_continuing(self):
  from tests.formal.test_runner import full_fixture,SyntheticEncoder
  import time
  with tempfile.TemporaryDirectory() as td:
   spec,rd=full_fixture(Path(td));spec['resolved_config']['concurrency']=4;events=[]
   def emit(e):
    events.append(e)
    if e['event_type']=='parallel_probe_passed':(rd/'control.json').write_text('{"action":"pause"}')
   def transport(endpoint,payload,key,timeout):time.sleep(.03);return fake_response(payload)
   with patch('formal_src.retrieval.LocalEncoder',SyntheticEncoder):result=run(spec,emit,{'run_dir':str(rd)},transport=transport)
   self.assertEqual(result['state'],'paused',result)
   proof=json.loads((rd/'parallel-validation.json').read_text());self.assertTrue(proof['passed']);self.assertEqual(proof['concurrency'],4)
   self.assertGreaterEqual(proof['peak_in_flight'],2);self.assertTrue((rd/'predictions.jsonl').exists())

 def test_association_expansion_only_rechecks_current_document(self):
  import hashlib
  from formal_src.evidence import expand_once
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);spec,rd=demo_fixture(root);p=root/'evidence_units.jsonl'
   with p.open('a') as f:f.write(json.dumps({'evidence_id':'other','doc_id':'other-doc','text':'unrelated'})+'\n')
   projection=json.loads((rd/'inputs.json').read_text())
   for binding in projection['resources']:
    if binding['path']==p.name:binding['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
   (rd/'inputs.json').write_text(json.dumps(projection))
   def scoped(seeds,units,edges):
    self.assertEqual({u['doc_id'] for u in units},{'doc'})
    return expand_once(seeds,units,edges)
   with patch('formal_src.runner.expand_once',scoped):result=run(spec,lambda e:None,lambda:'continue',transport=lambda endpoint,payload,key,timeout:fake_response(payload),run_dir=rd)
   self.assertEqual(result['state'],'completed',result)
