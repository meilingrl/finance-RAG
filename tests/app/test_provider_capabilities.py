import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app.service import ApplicationService
from app.provider_checks import provider_ready,provider_values
from tests.app.test_provider_parallel import SETTINGS,response
ROOT=Path(__file__).resolve().parents[2]

class ProviderCapabilityTests(unittest.TestCase):
 def test_real_image_probe_is_required_and_counted(self):
  with tempfile.TemporaryDirectory() as td:
   calls=[]
   def transport(e,p,k,t):
    calls.append(p)
    image=any(part.get('type')=='input_image' for part in p['input'][0]['content'])
    return response(p,'123456' if image else 'READY')
   s=ApplicationService(ROOT,td,provider_transport=transport)
   with patch('app.provider_checks.make_image_probe',return_value=('data:image/png;base64,c3ludGhldGlj','123456')):
    r=s.check_provider({**SETTINGS,'require_vision':True},s.set_credential('synthetic-secret'),'compatibility')
   self.assertTrue(r['ready'],r);self.assertTrue(r['vision']['ready'])
   self.assertEqual(r['sent_attempts'],6)
   profile={'provider':r['effective_provider'],'models':{'primary':SETTINGS['model']},'concurrency':4}
   self.assertTrue(provider_ready(td,profile,require_vision=True))
   self.assertNotIn('123456',json.dumps(calls))

 def test_text_success_does_not_claim_image_support_or_run_parallel_after_failure(self):
  with tempfile.TemporaryDirectory() as td:
   s=ApplicationService(ROOT,td,provider_transport=lambda e,p,k,t:response(p))
   with patch('app.provider_checks.make_image_probe',return_value=('data:image/png;base64,c3ludGhldGlj','123456')):
    r=s.check_provider({**SETTINGS,'require_vision':True},s.set_credential('synthetic-secret'),'compatibility')
   self.assertTrue(r['single']['ready']);self.assertFalse(r['vision']['ready']);self.assertFalse(r['ready'])
   self.assertEqual(r['sent_attempts'],2)

 def test_official_alias_is_scoped_to_requested_model(self):
  values=provider_values({**SETTINGS,'endpoint':'https://api.deepseek.com/responses','model':'deepseek-flash'})
  self.assertIsInstance(values['model_aliases'],dict)
  self.assertIn('deepseek-flash',values['model_aliases'])
  self.assertNotIn('deepseek-v4-pro',values['model_aliases'])
