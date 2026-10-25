"""Breaks caught: a private engineering snapshot submitted as a course ZIP."""
import importlib,json,hashlib,tempfile,unittest,zipfile
from pathlib import Path

class CourseExportTests(unittest.TestCase):
 def module(self):
  try:return importlib.import_module('formal_src.course_export')
  except ModuleNotFoundError:self.fail('course-only export not implemented')
 def test_source_projection_excludes_data_secrets_envs_and_full_requests(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)
   for name,content in [('README.md','readme'),('formal_src/a.py','pass'),('web/index.html','<html></html>'),('.local-secrets/key.txt','NEVER-EXPORT-KEY'),('data/gold.jsonl','gold'),('artifacts/run/requests.jsonl','full payload'),('.venv-app/secret.py','env')]:
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
   files=self.module().collect_course_sources(root)
   self.assertIn('README.md',files);self.assertIn('formal_src/a.py',files)
   self.assertTrue(all(not x.startswith(('.local-secrets/','data/','artifacts/','.venv-app/')) for x in files))
 def test_missing_actual_course_documents_blocks_export_without_fake_material(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);run=root/'run';run.mkdir()
   result=self.module().export_bundle(root,run,root/'exports')
   self.assertFalse(result['exported']);self.assertIn('course_documents_missing',result['blocking_items'])
   self.assertFalse(list(root.rglob('*.zip')))
 def test_missing_portable_guides_block_before_any_export(self):
  from formal_src.course_export import export_bundle
  for missing in ('docs/COURSE_README.md','docs/APP_GUIDE.md'):
   with self.subTest(missing=missing),tempfile.TemporaryDirectory() as td:
    root=Path(td)
    for name in ['course/报告.pdf','course/项目摘要.md','course/submission.json','course/AI使用声明.md','docs/COURSE_README.md','docs/APP_GUIDE.md']:
     if name==missing:continue
     p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('Synthetic document fixture')
    result=export_bundle(root,root/'run',root/'exports')
    self.assertEqual(result['blocking_items'],['course_documents_missing'])
    self.assertIn(missing,result['missing']);self.assertFalse((root/'exports').exists())
 def test_known_credential_in_allowed_source_is_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'README.md').write_text('unexpected LIVE-TEST-KEY-VALUE')
   with self.assertRaises(ValueError):self.module().collect_course_sources(root,credential_values=['LIVE-TEST-KEY-VALUE'])

 def test_forged_audit_boolean_without_provenance_cannot_export(self):
  from uuid import uuid4
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);course=root/'course';course.mkdir();run=root/'run';run.mkdir()
   (root/'README.md').write_text('Synthetic package test, no experiment claim')
   (course/'报告.pdf').write_bytes(b'%PDF-synthetic-test-only')
   (course/'项目摘要.md').write_text('Synthetic export fixture')
   (course/'submission.json').write_text(json.dumps({'leader_id':'1','leader_name':'One','project_name':'Synthetic','members':[{'id':'1','name':'One'},{'id':'2','name':'Two'}]}))
   for member in ['1_One','2_Two']:(course/f'贡献声明_{member}.md').write_text('Synthetic contribution fixture')
   prediction=b'{"task_id":"synthetic-fixture"}\n';(run/'predictions.jsonl').write_bytes(prediction);output=hashlib.sha256(prediction).hexdigest()
   rid=str(uuid4());(run/'state.json').write_text(json.dumps({'kind':'experiment','purpose':'formal','state':'completed'}))
   (run/'sealed.json').write_text(json.dumps({'synthetic':False,'prediction_sha256':output}))
   summary=dict(schema_version=1,run_id=rid,input_sha='0'*64,output_sha=output,evaluation_sha='0'*64,partitions={},denominators={},metrics={},main_comparisons=[],engineering={},audit_complete=True,answer_tables=['predictions.jsonl'],exports=[])
   (run/'summary.json').write_text(json.dumps(summary))
   (course/'AI使用声明.md').write_text('Synthetic test AI statement')
   (root/'docs').mkdir();(root/'docs/COURSE_README.md').write_text('Synthetic course README')
   (root/'docs/APP_GUIDE.md').write_text('Synthetic app guide')
   result=self.module().export_bundle(root,run,root/'exports')
   self.assertFalse(result['exported']);self.assertIn('formal_results_not_complete_and_reviewed',result['blocking_items'])
if __name__=='__main__':unittest.main()

class CompletePackagingTests(unittest.TestCase):
 def test_complete_synthetic_chain_exports_allowlisted_aggregates_and_atomic_failure(self):
  from tests.acceptance.packaging_fixture import build
  from formal_src.course_export import export_bundle
  from formal_src.verification import _atomic,_sha
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);spec,rd=build(root)
   course=root/'course';course.mkdir();(root/'README.md').write_text('PRIVATE-HISTORY-DO-NOT-SUBMIT')
   (root/'docs').mkdir();(root/'docs/COURSE_README.md').write_text('Synthetic portable course README: [guide](docs/APP_GUIDE.md)')
   (root/'docs/APP_GUIDE.md').write_text('Synthetic runnable app guide')
   (root/'docs/private-history.md').write_text('PRIVATE-DOC-DO-NOT-SUBMIT')
   project=Path(__file__).resolve().parents[2]
   (root/'docs/COURSE_SUBMISSION_MANIFEST.json').write_bytes((project/'docs/COURSE_SUBMISSION_MANIFEST.json').read_bytes())
   (root/'formal_src').mkdir()
   for filename in ('__init__.py','course_export.py','contracts.py'):
    (root/'formal_src'/filename).write_bytes((project/'formal_src'/filename).read_bytes())
   (course/'报告.pdf').write_bytes(b'%PDF-synthetic-only');(course/'项目摘要.md').write_text('Synthetic');(course/'AI使用声明.md').write_text('Synthetic AI test statement')
   (course/'submission.json').write_text(json.dumps({'leader_id':'1','leader_name':'One','project_name':'Synthetic','members':[{'id':'1','name':'One'},{'id':'2','name':'Two'}]}))
   for member in ['1_One','2_Two']:(course/f'贡献声明_{member}.md').write_text('Synthetic contribution fixture')
   summary=json.loads((rd/'summary.json').read_text());summary['engineering']['private_request']='NEVER-IN-ARCHIVE'
   _atomic(rd/'summary.json',summary)
   proof=json.loads((rd/'result-proof.json').read_text());proof['files']['summary.json']=_sha(rd/'summary.json');_atomic(rd/'result-proof.json',proof)
   with self.assertRaisesRegex(ValueError,'Credential in runtime provenance'):
    export_bundle(root,rd,root/'credential-rejected',credential_values=[spec['resolved_config']['provider']['endpoint']])
   self.assertFalse((root/'credential-rejected').exists())
   result=export_bundle(root,rd,root/'exports');self.assertTrue(result['exported'],result)
   with zipfile.ZipFile(result['archive']) as archive:
    self.assertEqual(archive.read('README.md'),(root/'docs/COURSE_README.md').read_bytes())
    self.assertEqual(archive.read('docs/APP_GUIDE.md'),(root/'docs/APP_GUIDE.md').read_bytes())
    self.assertNotIn('docs/private-history.md',archive.namelist())
    self.assertIn('AI使用声明.md',archive.namelist());self.assertNotIn('predictions.jsonl',archive.namelist());self.assertNotIn(b'NEVER-IN-ARCHIVE',archive.read('results/summary.json'))
    from formal_src.run_plan import content_sha,CANONICAL_PROFILE
    runtime=json.loads(archive.read('runtime/configuration.json'))
    self.assertEqual(runtime['resolved_config'],spec['resolved_config'])
    self.assertEqual(runtime['canonical_recipe_sha256'],content_sha(CANONICAL_PROFILE))
    self.assertEqual(runtime['runtime_profile_sha256'],content_sha(spec['resolved_config']))
    for field in ('code_sha','prompt_sha','resource_manifest_sha','freeze_sha'):
     self.assertEqual(runtime[field],spec['resolved_config'][field])
    self.assertEqual(runtime['model_provider_identity'],{'models':spec['resolved_config']['models'],'provider':spec['resolved_config']['provider']})
    resources=json.loads(archive.read('resources.json'))
    self.assertFalse(resources['model_resources']['included']);self.assertFalse(resources['dataset_resources']['included'])
    self.assertTrue(resources['model_resources']['public_download_manifest']['files'])
    self.assertEqual(resources['dataset_resources']['pointer_policy'],'content_sha_only_no_dataset_bytes')
    self.assertNotIn(str(root).encode(),archive.read('runtime/configuration.json'))
    self.assertNotIn(str(root).encode(),archive.read('resources.json'))
    extracted=root/'extracted';archive.extractall(extracted)
   import subprocess,sys,os
   child="""
from pathlib import Path
import json
import formal_src.course_export as exporter
root=Path.cwd().resolve()
assert Path(exporter.__file__).resolve()==root/'formal_src/course_export.py'
assert exporter.MANIFEST.resolve()==root/'docs/COURSE_SUBMISSION_MANIFEST.json'
assert exporter.MANIFEST.is_file()
assert (root/'docs/COURSE_README.md').is_file()
files=exporter.collect_course_sources(root)
assert 'formal_src/course_export.py' in files
assert 'docs/COURSE_README.md' in files
assert 'docs/COURSE_SUBMISSION_MANIFEST.json' in files
result=exporter.export_bundle(root,root/'absent-run',root/'exports')
assert result['exported'] is False and result['blocking_items']==['course_documents_missing'], result
assert 'course/报告.pdf' in result['missing'], result
print(json.dumps({'isolated_manifest':True,'blocked_for_missing_real_documents':True}))
"""
   checked=subprocess.run([sys.executable,'-c',child],cwd=extracted,env={**os.environ,'PYTHONPATH':str(extracted)},capture_output=True,text=True)
   self.assertEqual(checked.returncode,0,checked.stderr)
   self.assertEqual(json.loads(checked.stdout),{'isolated_manifest':True,'blocked_for_missing_real_documents':True})
   original=zipfile.ZipFile.writestr
   def fail(archive,name,*a,**kw):
    if name=='results/summary.json':raise OSError('synthetic archive failure')
    return original(archive,name,*a,**kw)
   with patch.object(zipfile.ZipFile,'writestr',fail):
    with self.assertRaises(OSError):export_bundle(root,rd,root/'failed')
   self.assertFalse(list((root/'failed').iterdir()))

class RuntimeConfigurationTests(unittest.TestCase):
 def test_unsupported_provider_runtime_fields_are_rejected_not_silently_dropped(self):
  from formal_src.course_export import runtime_configuration
  from formal_src.run_plan import CANONICAL_PROFILE
  config={**CANONICAL_PROFILE,'provider':{'endpoint':'https://example.invalid/v1/responses','extra_headers':{'Authorization':'private-value'}}}
  with self.assertRaisesRegex(ValueError,'Unexportable provider'):runtime_configuration({'resolved_config':config,'run_id':'synthetic','code_sha':'0'*64})
 def test_local_absolute_or_credential_provider_values_are_rejected(self):
  from formal_src.course_export import runtime_configuration
  from formal_src.run_plan import CANONICAL_PROFILE
  for endpoint in ('/tmp/local-output','https://user:secret@example.invalid/v1/responses','https://example.invalid/v1/responses?key=secret'):
   with self.subTest(endpoint=endpoint):
    config={**CANONICAL_PROFILE,'provider':{'endpoint':endpoint}}
    with self.assertRaises(ValueError):runtime_configuration({'resolved_config':config,'run_id':'synthetic','code_sha':'0'*64})
