import unittest
class ProviderTests(unittest.TestCase):
 def test_provider_exists(self):
  import importlib.util
  self.assertIsNotNone(importlib.util.find_spec('formal_src.provider'))
 def test_explicit_provider_and_guards(self):
  from formal_src.provider import build_payload, validate_response, ProviderError
  p=build_payload({'model':'gpt-6.1-sol','max_output_tokens':100,'reserve_input_tokens':5000},'question',[{'evidence_id':'e','text':'3 years'}])
  self.assertEqual(p['model'],'gpt-6.1-sol'); self.assertIn('years',p['instructions'])
  with self.assertRaises(ProviderError): validate_response({'model':'wrong'},'gpt-6.1-sol',['e'],100,5000)
 def test_usage_schema_refusal_and_truncation(self):
  from formal_src.provider import validate_response,ProviderError
  base={'model':'gpt-6.1-sol','id':'r','status':'completed','usage':{'input_tokens':10,'output_tokens':10},'output':[{'type':'message','role':'assistant','content':[{'type':'refusal','refusal':'No'}]}]}
  self.assertEqual(validate_response(base,'gpt-6.1-sol',['e'],100,1000)['status'],'rejection')
  base['status']='incomplete';base['incomplete_details']={'reason':'max_output_tokens'}
  self.assertEqual(validate_response(base,'gpt-6.1-sol',['e'],100,1000)['status'],'truncated')
  base['usage']['input_tokens']=1001
  self.assertEqual(validate_response(base,'gpt-6.1-sol',['e'],100,1000)['status'],'truncated')
 def test_gold_rejected_and_config_validated_before_sending(self):
  from formal_src.provider import build_payload,ProviderError
  with self.assertRaises(ProviderError):build_payload({'model':'m','max_output_tokens':10,'reserve_input_tokens':10000},'q',[{'evidence_id':'e','text':'x','gold':'sentinel'}])
 def test_image_wire_bytes_do_not_consume_text_token_allowance(self):
  import base64
  from formal_src.provider import build_payload,ProviderError
  image='data:image/png;base64,'+base64.b64encode(b'\x89PNG\r\n\x1a\n'+b'x'*200000).decode()
  content=[{'type':'input_text','text':'Question and exact shared OCR evidence'},{'type':'input_image','image_url':image,'detail':'high'}]
  config={'model':'gpt-6.1-sol','max_output_tokens':2048,'reserve_input_tokens':131072}
  payload=build_payload(config,'q',[],content=content)
  self.assertEqual(payload['input'][0]['content'][1],content[1])
  with self.assertRaisesRegex(ProviderError,'wire_budget_exceeded'):build_payload({**config,'max_request_bytes':100000},'q',[],content=content)
  with self.assertRaisesRegex(ProviderError,'context_budget_exceeded'):build_payload(config,'q',[],content=[{'type':'input_text','text':'z'*131073},content[1]])

 def test_answer_does_not_require_usage_and_usage_is_not_accounted(self):
  import json
  from formal_src.provider import validate_response
  raw={'model':'gpt-6.1-sol','id':'r','status':'completed','output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':json.dumps({'answer':'3 years','scale':'','citations':['e'],'abstained':False})}]}]}
  for usage in (None,{},'unsupported',{'input_tokens':-1,'output_tokens':999999}):
   with self.subTest(usage=usage):
    result=validate_response({**raw,'usage':usage},'gpt-6.1-sol',['e'],100,1000)
    self.assertEqual(result['status'],'answered');self.assertEqual(result['parsed_answer'],'3 years');self.assertEqual(result['usage'],{})
