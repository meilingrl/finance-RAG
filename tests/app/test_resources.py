import unittest,tempfile,json,hashlib
from pathlib import Path
class ResourceTests(unittest.TestCase):
 def test_hash_and_traversal(self):
  from app.resources import ResourceRegistry
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)/'data';root.mkdir();(root/'input.json').write_text('{}')
   m=dict(schema_version=1,resource_set_id='demo',purpose='demo',resources=[dict(kind='input',path='input.json',sha256=hashlib.sha256(b'{}').hexdigest())])
   (root/'resources.json').write_text(json.dumps(m));r=ResourceRegistry(Path(td)/'w');self.assertIn('demo',r.register(root)['ready_for'])
   (root/'input.json').write_text('changed');self.assertIn('resource_sha_mismatch',r.check('demo')['blocking_items'])
   m['resources'][0]['path']='../outside';(root/'resources.json').write_text(json.dumps(m))
   with self.assertRaises(ValueError):r.register(root)
 def test_formal_requires_all_gates(self):
  from app.resources import ResourceRegistry
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'resources.json').write_text(json.dumps(dict(schema_version=1,resource_set_id='formal',purpose='formal',resources=[])))
   r=ResourceRegistry(p/'w').register(p);self.assertIn('model_missing',r['blocking_items']);self.assertIn('reviews_missing',r['blocking_items']);self.assertNotIn('experiment',r['ready_for'])
 def test_legacy_adapter_is_external_demo_only(self):
  from app.resources import ResourceRegistry
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)/'nonllm-pilot-v5';root.mkdir();(root/'data_manifest.json').write_text('{}');(root/'inference_questions.jsonl').write_text('{}');before=list(root.iterdir())
   registry=ResourceRegistry(Path(td)/'w');report=registry.register(root);self.assertIn('demo',report['ready_for']);self.assertIn('demo_only_resources',report['blocking_items']);self.assertEqual(list(root.iterdir()),before)
 def test_review_exposure_rejected(self):
  from app.resources import ResourceRegistry
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);sha=hashlib.sha256(b'{}').hexdigest();(root/'input.json').write_text('{}');(root/'resources.json').write_text(json.dumps(dict(schema_version=1,resource_set_id='formal',purpose='formal',resources=[dict(kind='input',path='input.json',sha256=sha)])))
   r=ResourceRegistry(root/'w');r.register(root)
   with self.assertRaises(ValueError):r.import_reviews('formal',{'reviews':[dict(reviewer='alice',reviewed_at='2026-10-24T00:00:00Z',asset_sha256=sha,approved=True,qa_exposed=True)]})
 def test_inference_projection_excludes_evaluation(self):
  from app.resources import ResourceRegistry
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);items=[]
   for name,kind in [('questions.jsonl','input'),('private-evaluation.jsonl','evaluation'),('model.bin','model')]:
    (root/name).write_text('{}');items.append(dict(kind=kind,path=name,sha256=hashlib.sha256(b'{}').hexdigest()))
   (root/'resources.json').write_text(json.dumps(dict(schema_version=1,resource_set_id='mixed',purpose='formal',resources=items)))
   r=ResourceRegistry(root/'w');r.register(root);projection=r.inference_projection('mixed');self.assertEqual({v['kind'] for v in projection['resources']},{'input','model'});self.assertNotIn('private-evaluation',json.dumps(projection))

class EvaluationIntegrityTests(unittest.TestCase):
 def test_one_good_evaluation_does_not_hide_missing_or_changed_other_binding(self):
  import tempfile,json,hashlib
  from pathlib import Path
  from app.resources import ResourceRegistry
  for bad in ('missing','changed'):
   with self.subTest(bad=bad),tempfile.TemporaryDirectory() as td:
    root=Path(td);data=root/'data';data.mkdir();(data/'input').write_text('input');(data/'evaluation').write_text('good')
    if bad=='changed':(data/'bad-evaluation').write_text('changed')
    manifest={'schema_version':1,'resource_set_id':'demo','purpose':'demo','resources':[{'kind':'input','path':'input','sha256':hashlib.sha256(b'input').hexdigest()},{'kind':'evaluation','path':'evaluation','sha256':hashlib.sha256(b'good').hexdigest()},{'kind':'evaluation','path':'bad-evaluation','sha256':hashlib.sha256(b'expected').hexdigest()}]}
    (data/'resources.json').write_text(json.dumps(manifest));result=ResourceRegistry(root/'w').register(data)
    self.assertNotIn('verify',result['ready_for'])
