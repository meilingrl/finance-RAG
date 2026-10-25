"""Preparation must preserve approved assets, stay portable and honor reserves."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts import prepare_formal as p
class PreparationSafety(unittest.TestCase):
 def test_protected_bundle_rejected_before_any_builder_or_curator_write(self):
  for qualification in [True,False]:
   with tempfile.TemporaryDirectory() as td:
    root=Path(td);m={'resources':[],'qualification':{'inputs_complete':qualification}}
    (root/'resources.json').write_text(json.dumps(m))
    if not qualification:(root/'frozen.json').write_text('{}')
    before={f.name:f.read_bytes() for f in root.iterdir()}
    for stage in [p.main,p.builder,p.prepare_diagnostic_queue]:
     with patch('urllib.request.urlopen',side_effect=AssertionError('network forbidden')),patch('scripts.phase2_acquire.urlopen',side_effect=AssertionError('network forbidden')):
      with self.assertRaisesRegex(ValueError,'immutable'):stage(root)
     self.assertEqual(before,{f.name:f.read_bytes() for f in root.iterdir()})
 def test_cached_metadata_external_root_has_root_relative_binding(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);target=root/'isolated_gold/official_metadata/tatdqa_dataset_train.json';target.parent.mkdir(parents=True);target.write_text('[]')
   data,binding=p.Fetcher(root).metadata('train')
   self.assertEqual(data,[]);self.assertEqual(binding['path'],'isolated_gold/official_metadata/tatdqa_dataset_train.json')
 def test_whole_source_failure_uses_next_ordered_reserve_without_qa_performance(self):
  records=[{'doc':{'uid':s,'source':s+'_2020.pdf','page':1},'questions':[{'uid':s,'question':'A synthetic value?','answer_type':'span','req_comparison':False}]} for s in ['a','b','c','d']]
  documents={}
  def acquire(ids):
   for doc in ids:
    if doc!='a':documents[doc]={'doc_id':doc}
  selected,log=p.select_qualified(records,['a','b','c','d'],target=2,role='main',acquire=acquire,documents=documents,source_target=2,max_sources=2)
  self.assertEqual({q['source_id'] for q in selected},{'b','c'})
  self.assertEqual(log[0]['replacement_source'],'c');self.assertEqual(log[0]['rejected_source'],'a')
