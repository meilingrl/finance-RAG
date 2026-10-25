"""Pinned encoder bytes must never be silently replaced or accepted unverified."""
import hashlib,importlib,io,tempfile,unittest
from pathlib import Path
class ProvisionTests(unittest.TestCase):
 def module(self):
  try:return importlib.import_module('formal_src.provisioning')
  except ModuleNotFoundError:self.fail('portable encoder provisioner missing')
 def manifest(self):
  data=b'public test bytes';return {'repo_id':'sentence-transformers/all-MiniLM-L6-v2','revision':'1'*40,'total_bytes':len(data),'files':[{'file':'modules.json','size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'url':'https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/'+'1'*40+'/modules.json'}]},data
 def test_download_verify_and_cached_reuse(self):
  with tempfile.TemporaryDirectory() as td:
   m,data=self.manifest();root=Path(td);calls=[]
   report=self.module().provision(root,m,opener=lambda url,timeout:(calls.append(url) or io.BytesIO(data)))
   self.assertEqual(report['bytes'],len(data));self.assertEqual(len(calls),1)
   self.module().provision(root,m,opener=lambda *a,**k:self.fail('cached files must not redownload'))
   (root/'modules.json').write_bytes(b'corrupt')
   with self.assertRaises(ValueError):self.module().provision(root,m)
 def test_wrong_hash_unsafe_url_or_paths_never_become_accepted_asset(self):
  for change in ['hash','path','url']:
   with self.subTest(change=change),tempfile.TemporaryDirectory() as td:
    m,data=self.manifest()
    if change=='hash':m['files'][0]['sha256']='0'*64
    if change=='path':m['files'][0]['file']='../escape'
    if change=='url':m['files'][0]['url']='https://example.invalid/foreign'
    with self.assertRaises(ValueError):self.module().provision(Path(td),m,opener=lambda *a,**k:io.BytesIO(data))
    self.assertFalse((Path(td)/'modules.json').exists())
 def test_cli_blocks_reviewed_bundle_before_provisioning(self):
  import json
  from unittest.mock import patch
  from scripts import provision_formal_encoder as cli,prepare_formal
  for protection in ('human_scope_review','application_registry'):
   with self.subTest(protection=protection),tempfile.TemporaryDirectory() as td:
    root=Path(td);output=root/'candidate';output.mkdir()
    (root/'formal_config').mkdir();(root/'formal_config/minilm-files.json').write_text('{}')
    manifest={'resources':[],'qualification':{},'human_scope_review':protection=='human_scope_review'}
    (output/'resources.json').write_text(json.dumps(manifest))
    if protection=='application_registry':
     registry=root/'artifacts/formal-v1/resources';registry.mkdir(parents=True)
     (registry/'synthetic.json').write_text(json.dumps({'root':str(output),'reviews':{'reviews':[{'approved':True}]}}))
    with patch.object(cli,'ROOT',root),patch.object(prepare_formal,'ROOT',root),patch.object(cli,'provision',side_effect=AssertionError('must not provision')):
     with self.assertRaisesRegex(ValueError,'immutable'):cli.main(['--output',str(output)])
    self.assertFalse((output/'models').exists())
