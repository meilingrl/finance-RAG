"""Breaks caught: freezing an unready recipe or mutating conditions under one identity."""
import importlib,json,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
class LifecycleTests(unittest.TestCase):
 def module(self):
  try:return importlib.import_module('formal_src.profile_lifecycle')
  except ModuleNotFoundError:self.fail('formal profile lifecycle missing')
 def draft(self):return json.loads((ROOT/'formal_config/formal-v1.json').read_text())
 def options(self):return dict(models={'primary':'gpt-6.1-sol','secondary':'gpt-6-astra'},provider={'endpoint':'https://api.pinaic.com/v1/responses','max_output_tokens':2048,'reserve_input_tokens':131072,'reasoning_effort':'low','stream':True,'timeout_seconds':60},prompt_sha='1'*64,resource_manifest_sha='2'*64,code_sha='3'*64,audit_plan_sha256='4'*64,readiness={'cohorts_verified':True,'models_verified':True,'resources_verified':True,'human_reviews_complete':True})
 def test_unready_profile_cannot_be_frozen(self):
  options=self.options();options['readiness']['human_reviews_complete']=False
  with self.assertRaises(ValueError):self.module().freeze_formal_profile(self.draft(),**options)
 def test_freeze_requires_preregistered_audit_identity(self):
  options=self.options();options['audit_plan_sha256']=None
  with self.assertRaises(ValueError):self.module().freeze_formal_profile(self.draft(),**options)
 def test_frozen_identity_binds_models_recipe_code_and_resource(self):
  module=self.module();profile=module.freeze_formal_profile(self.draft(),**self.options())
  self.assertTrue(module.validate_frozen_profile(profile,profile['freeze_sha']))
  altered={**profile,'repeats':1}
  with self.assertRaises(ValueError):module.validate_frozen_profile(altered,profile['freeze_sha'])
  altered={**profile,'code_sha':'9'*64}
  with self.assertRaises(ValueError):module.validate_frozen_profile(altered,profile['freeze_sha'])
  self.assertIsNone(self.draft()['freeze_sha'])

if __name__=='__main__':unittest.main()
