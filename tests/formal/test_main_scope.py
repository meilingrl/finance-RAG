import unittest
from formal_src.run_plan import compile_run_plan,CANONICAL_PROFILE
from formal_src.runner import formal_tasks,RunnerBlocked

class MainScopeTests(unittest.TestCase):
 def fixture(self):
  cohorts={'main':[f'q{i}' for i in range(200)],'diagnostic':[],'s50':[f'q{i}' for i in range(50)]}
  return cohorts
 def test_main_only_keeps_retrievers_repeats_and_excludes_auxiliaries(self):
  cohorts=self.fixture();plan=compile_run_plan(CANONICAL_PROFILE,cohorts,execution_scope='main_only')
  self.assertEqual(plan['logical_generation_count'],3600);self.assertEqual(plan['local_baseline_count'],800)
  self.assertEqual({t['partition'] for t in plan['units']},{'main'})
  self.assertEqual({t['retriever'] for t in plan['units']},{'bm25','minilm'})
  self.assertEqual({t['repeat'] for t in plan['units'] if t['work_type']=='generation'},{1,2,3})
  data={'cohorts.json':cohorts,'inference_questions.jsonl':[{'question_id':q,'split':'main','source_id':q} for q in cohorts['main']]}
  spec={'plan':plan,'logical_generation_count':3600,'purpose':'software_test'}
  self.assertEqual(formal_tasks(spec,data),plan['units'])
  spec['logical_generation_count']=6600
  with self.assertRaises(RunnerBlocked):formal_tasks(spec,data)
 def test_full_remains_strict_and_unknown_scope_fails(self):
  with self.assertRaises(ValueError):compile_run_plan(CANONICAL_PROFILE,self.fixture())
  with self.assertRaises(ValueError):compile_run_plan(CANONICAL_PROFILE,self.fixture(),execution_scope='anything')

 def test_main_only_completes_and_scores_all_four_comparisons(self):
  import tempfile,json,hashlib
  from pathlib import Path
  from unittest.mock import patch
  from tests.formal.test_runner import full_fixture,fake_response,SyntheticEncoder
  from formal_src.runner import run
  from formal_src.verification import verify_run
  from formal_src.provider import digest
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);spec,rd=full_fixture(root)
   cohorts=json.loads((root/'cohorts.json').read_text())
   spec['plan']=compile_run_plan(CANONICAL_PROFILE,cohorts,execution_scope='main_only');spec['logical_generation_count']=3600
   spec['resolved_config']['review_policy']='automatic_course'
   projection=json.loads((rd/'inputs.json').read_text());projection['review_policy']='automatic_course'
   unit=json.loads((root/'evidence_units.jsonl').read_text());unit.update(image_path='crop.png',image_sha256=hashlib.sha256((root/'crop.png').read_bytes()).hexdigest())
   (root/'evidence_units.jsonl').write_text(json.dumps(unit)+'\n')
   structure=json.loads((root/'approved_structures.jsonl').read_text());structure['evidence_sha']=digest(unit)
   (root/'approved_structures.jsonl').write_text(json.dumps(structure)+'\n')
   for b in projection['resources']:b['sha256']=hashlib.sha256((root/b['path']).read_bytes()).hexdigest()
   (rd/'inputs.json').write_text(json.dumps(projection))
   questions=[json.loads(l) for l in (root/'inference_questions.jsonl').read_text().splitlines()]
   gold=root/'gold.jsonl';gold.write_text(''.join(json.dumps({'question_id':q['question_id'],'source_id':q['source_id'],'answer':'3 years','scale':''})+'\n' for q in questions if q['split']=='main'))
   manifest={'review_policy':'automatic_course','execution_scope':'main_only','resources':[{'kind':'evaluation','path':'gold.jsonl','sha256':hashlib.sha256(gold.read_bytes()).hexdigest()}]}
   spec['resource_sha']=digest(manifest);(rd/'spec.json').write_text(json.dumps(spec));(root/'resources').mkdir();(root/'resources/x.json').write_text(json.dumps({'root':str(root),'manifest':manifest}))
   def without_usage(endpoint,payload,key,timeout):
    response=fake_response(payload);response.pop('usage',None);return response
   with patch('formal_src.retrieval.LocalEncoder',SyntheticEncoder):result=run(spec,lambda e:None,lambda:'continue',transport=without_usage,run_dir=rd)
   self.assertEqual(result['state'],'completed',result)
   result=verify_run(rd);self.assertEqual(result['state'],'completed',result)
   summary=json.loads((rd/'summary.json').read_text());self.assertEqual(summary['partitions'],{'main':4400});self.assertEqual(len(summary['main_comparisons']),4);self.assertFalse(summary['audit_complete'])
