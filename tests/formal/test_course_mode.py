import unittest,tempfile,json
from pathlib import Path
from tests.formal.test_profile_lifecycle import LifecycleTests

class CourseModeTests(unittest.TestCase):
 def test_freeze_keeps_human_review_false(self):
  from formal_src.profile_lifecycle import freeze_formal_profile,validate_frozen_profile
  fixture=LifecycleTests();options=fixture.options();options['readiness']['human_reviews_complete']=False;options['audit_plan_sha256']=None
  profile=freeze_formal_profile({**fixture.draft(),'review_policy':'automatic_course'},**options)
  self.assertTrue(validate_frozen_profile(profile));self.assertFalse(profile['readiness']['human_reviews_complete'])
 def test_course_assets_need_bindings_not_human_signature(self):
  from formal_src.runner import reviewed_assets
  from formal_src.provider import digest
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'crop.png';p.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
   import hashlib
   sha=hashlib.sha256(p.read_bytes()).hexdigest()
   unit={'evidence_id':'e','doc_id':'d','text':'Revenue 120','image_path':'crop.png','image_sha256':sha}
   bindings={'crop.png':{'kind':'input','sha256':sha,'absolute':p}}
   data={'review_policy':'automatic_course'}
   result=reviewed_assets([unit],[unit],data,bindings,'E3')
   self.assertEqual(len(result['images']),1);self.assertFalse(result['human_reviewed'])
   unit['image_sha256']='0'*64
   with self.assertRaises(ValueError):reviewed_assets([unit],[unit],data,bindings,'E3')
 def test_course_registration_retains_real_missing_file_checks(self):
  from app.resources import ResourceRegistry
  import hashlib
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'input').write_text('data');sha=hashlib.sha256(b'data').hexdigest()
   manifest={'schema_version':1,'resource_set_id':'formal','purpose':'formal','resources':[{'kind':'input','path':'input','sha256':sha}]}
   (root/'resources.json').write_text(json.dumps(manifest));registry=ResourceRegistry(root/'w')
   report=registry.register(root,'automatic_course');self.assertNotIn('reviews_missing',report['blocking_items']);self.assertIn('model_missing',report['blocking_items'])
   self.assertEqual(json.loads((root/'resources.json').read_text()),manifest)
   self.assertEqual(registry.inference_projection('formal-course')['review_policy'],'automatic_course')
   (root/'input').write_text('changed');self.assertIn('resource_sha_mismatch',registry.check('formal-course')['blocking_items'])
 def test_course_structure_without_review_still_binds_original_unit(self):
  from formal_src.runner import reviewed_assets
  from formal_src.provider import digest
  unit={'evidence_id':'e','doc_id':'d','text':'Revenue 120'}
  structure={'evidence_id':'e','evidence_sha':digest(unit),'nodes':[],'relations':[],'verified':False}
  data={'review_policy':'automatic_course','draft_structures.jsonl':[structure]}
  result=reviewed_assets([unit],[unit],data,{},'E4');self.assertFalse(result['structures'][0]['verified'])
  structure['evidence_sha']='0'*64
  with self.assertRaises(ValueError):reviewed_assets([unit],[unit],data,{},'E4')
 def test_complete_course_matrix_scores_without_human_audit(self):
  from tests.formal.test_runner import full_fixture,fake_response,SyntheticEncoder
  from formal_src.runner import run
  from formal_src.provider import digest
  from formal_src.verification import verify_run
  from unittest.mock import patch
  import hashlib
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);spec,rd=full_fixture(root)
   projection=json.loads((rd/'inputs.json').read_text());projection['review_policy']='automatic_course';spec['resolved_config']['review_policy']='automatic_course'
   unit=json.loads((root/'evidence_units.jsonl').read_text());unit.update(image_path='crop.png',image_sha256=hashlib.sha256((root/'crop.png').read_bytes()).hexdigest())
   (root/'evidence_units.jsonl').write_text(json.dumps(unit)+'\n')
   structure=json.loads((root/'approved_structures.jsonl').read_text());structure.update(evidence_sha=digest(unit),verified=False)
   (root/'approved_structures.jsonl').write_text(json.dumps(structure)+'\n')
   projection['resources']=[b for b in projection['resources'] if b['path']!='scope_reviews.json']
   for b in projection['resources']:b['sha256']=hashlib.sha256((root/b['path']).read_bytes()).hexdigest()
   (rd/'inputs.json').write_text(json.dumps(projection))
   questions=[json.loads(l) for l in (root/'inference_questions.jsonl').read_text().splitlines()]
   gold=root/'gold.jsonl';gold.write_text(''.join(json.dumps({'question_id':q['question_id'],'source_id':q['source_id'],'answer':'3 years','scale':''})+'\n' for q in questions))
   manifest={'review_policy':'automatic_course','resources':[{'kind':'evaluation','path':'gold.jsonl','sha256':hashlib.sha256(gold.read_bytes()).hexdigest()}]}
   spec['resource_sha']=digest(manifest);(rd/'spec.json').write_text(json.dumps(spec));(root/'resources').mkdir();(root/'resources/x.json').write_text(json.dumps({'root':str(root),'manifest':manifest}))
   with patch('formal_src.retrieval.LocalEncoder',SyntheticEncoder):result=run(spec,lambda e:None,lambda:'continue',transport=lambda endpoint,payload,key,timeout:fake_response(payload),run_dir=rd)
   self.assertEqual(result['state'],'completed',result)
   result=verify_run(rd);self.assertEqual(result['state'],'completed',result)
   summary=json.loads((rd/'summary.json').read_text());self.assertFalse(summary['audit_complete']);self.assertEqual(summary['engineering']['audit_status'],'not_performed_course_scope');self.assertTrue(summary['metrics'])
