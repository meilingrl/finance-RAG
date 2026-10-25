"""Synthetic identity regressions; no acquired data or network."""
import json,tempfile,unittest,hashlib
from pathlib import Path
from tests.acceptance.test_verification import VerificationTests

class ProvenanceTests(unittest.TestCase):
 source=VerificationTests.source
 def test_mutated_source_spec_is_rejected(self):
  from formal_src.verification import verify_run
  with tempfile.TemporaryDirectory() as td:
   spec,rd=self.source(Path(td));spec['resource_sha']='f'*64
   (rd/'spec.json').write_text(json.dumps(spec))
   self.assertEqual(verify_run(rd)['blocking_items'],['execution_identity_mismatch'])
 def test_derived_results_are_bound_after_verification(self):
  from formal_src.verification import verify_run,check_seal,VerificationBlocked
  with tempfile.TemporaryDirectory() as td:
   _,rd=self.source(Path(td));self.assertEqual(verify_run(rd)['state'],'completed')
   (rd/'summary.json').write_text('{}')
   with self.assertRaisesRegex(VerificationBlocked,'result_seal_mismatch'):check_seal(rd)
 def test_registry_identity_rejected_before_gold_open(self):
  from formal_src.verification import _evaluation_files,VerificationBlocked
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'resources').mkdir()
   (root/'resources/x.json').write_text(json.dumps({'root':td,'manifest':{'resources':[{'kind':'evaluation','path':'unread-gold.jsonl','sha256':'0'*64}]}}))
   with self.assertRaisesRegex(VerificationBlocked,'resource_manifest_identity_mismatch'):_evaluation_files(root,{'resource_set_id':'x','resource_sha':'1'*64})
 def test_empty_audit_plan_cannot_complete(self):
  from formal_src.verification import validate_audit_plan,VerificationBlocked
  with self.assertRaises(VerificationBlocked):validate_audit_plan({'answer_items':[],'relation_items':[],'second_review_ids':[]},[], '0'*64)
 def test_post_generation_audit_reviews_bind_without_mutating_generation_manifest(self):
  from app.resources import ResourceRegistry
  from formal_src.provider import digest
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);assets=root/'assets';assets.mkdir();(assets/'input.json').write_text('{}');(assets/'audit_reviews.json').write_text('[]')
   manifest={'schema_version':1,'resource_set_id':'synthetic','purpose':'demo','resources':[{'kind':'input','path':'input.json','sha256':hashlib.sha256(b'{}').hexdigest()}]}
   (assets/'resources.json').write_text(json.dumps(manifest));registry=ResourceRegistry(root);registry.register(assets)
   registry.import_reviews('synthetic',{'audit_reviews':{'path':'audit_reviews.json','sha256':hashlib.sha256(b'[]').hexdigest(),'generation_manifest_sha256':digest(manifest)}})
   self.assertEqual(registry.record('synthetic')['manifest'],manifest)
   self.assertIn('audit_reviews',registry.record('synthetic'))
 def test_verify_job_request_identity_is_bound_by_result_proof(self):
  from formal_src.verification import verify_run,check_seal,VerificationBlocked
  from uuid import uuid4
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);spec,source=self.source(root);rd=root/'runs'/str(uuid4());rd.mkdir()
   request={**spec,'kind':'verify','run_id':rd.name,'source_run_id':source.name,'demo_input':None}
   (rd/'spec.json').write_text(json.dumps(request));self.assertEqual(verify_run(rd)['state'],'completed')
   request['source_run_id']=str(uuid4());(rd/'spec.json').write_text(json.dumps(request))
   with self.assertRaisesRegex(VerificationBlocked,'result_request_identity_mismatch'):check_seal(rd)
 def test_manifest_identity_matches_registration_with_unicode_metadata(self):
  from app.jobs import digest
  from formal_src.verification import _evaluation_files
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'resources').mkdir();manifest={'title':'合成资源','resources':[]}
   (root/'resources/x.json').write_text(json.dumps({'root':td,'manifest':manifest}))
   files,_=_evaluation_files(root,{'resource_set_id':'x','resource_sha':digest(manifest)})
   self.assertEqual(files,{})
 def test_new_demo_writes_hash_only_result_proof_without_evaluation(self):
  from formal_src.verification import check_result_proof
  with tempfile.TemporaryDirectory() as td:
   _,rd=self.source(Path(td));proof=check_result_proof(rd,required=True)
   self.assertIn('summary.json',proof['files']);self.assertIn('retrieval.jsonl',proof['files'])
   self.assertEqual(proof['evaluation_sha'],'0'*64)
