"""Provider boundaries retain configured identities, formats and original images."""
import copy
import json
import unittest

from formal_src import provider


def config(**extra):
    return dict(endpoint='https://example.invalid/v1/responses', model='requested-model',
                max_output_tokens=4096, reserve_input_tokens=32768, stream=False, **extra)


def answer(model='requested-model'):
    return {'id': 'synthetic-response', 'model': model, 'choices': [{
        'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant',
        'content': '{"answer":"READY","scale":"","citations":["e"],"abstained":false}'}}]}


class CompatibilityTests(unittest.TestCase):
    def test_base_and_complete_urls_resolve_without_duplicating_protocol_path(self):
        for endpoint, extra, expected in [
            ('https://example.invalid', {}, 'https://example.invalid/responses'),
            ('https://example.invalid/v1/', {}, 'https://example.invalid/v1/responses'),
            ('https://example.invalid/chat/completions/', {}, 'https://example.invalid/chat/completions'),
            ('https://example.invalid/v1', {'api_style': 'chat_completions'}, 'https://example.invalid/v1/chat/completions'),
        ]:
            seen = []
            def transport(url, payload, key, timeout):
                seen.append(url)
                return answer()
            cfg = {**config(), 'endpoint': endpoint, **extra}
            payload = provider.build_payload(cfg, 'What is the marker?', [{'evidence_id': 'e', 'text': 'READY'}])
            result = provider.generate(cfg, payload, 'synthetic-key', ['e'], transport)
            self.assertEqual(result['status'], 'answered')
            self.assertEqual(seen, [expected])

    def test_chat_omits_responses_only_store_flag(self):
        payload=provider.build_probe_payload({**config(),'endpoint':'https://api.deepseek.com/chat/completions','model':'deepseek-flash'})
        self.assertNotIn('store',payload)

    def test_chat_preserves_image_url_and_detail_with_protocol_specific_fields(self):
        content = [{'type': 'input_text', 'text': 'Read the image'},
                   {'type': 'input_image', 'image_url': 'data:image/png;base64,c3ludGhldGlj', 'detail': 'original'}]
        cfg = {**config(), 'endpoint': 'https://example.invalid/v1/chat/completions', 'reasoning_effort': 'max'}
        payload = provider.build_payload(cfg, 'q', [], content=content)
        self.assertNotIn('input', payload)
        self.assertEqual(payload['max_completion_tokens'], 4096)
        self.assertEqual(payload['reasoning_effort'], 'max')
        self.assertEqual(payload['messages'][1]['content'], [
            {'type': 'text', 'text': 'Read the image'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,c3ludGhldGlj', 'detail': 'original'}}])
        self.assertEqual(content[1]['type'], 'input_image')

    def test_json_object_carries_answer_schema_in_prompt(self):
        cfg = {**config(), 'response_format': 'json_object'}
        payload = provider.build_payload(cfg, 'q', [])
        self.assertEqual(payload['text']['format'], {'type': 'json_object'})
        self.assertIn('JSON', payload['instructions'])
        self.assertIn('abstained', payload['instructions'])

    def test_deepseek_chat_uses_documented_json_mode_and_max_tokens(self):
        cfg = {**config(), 'endpoint': 'https://api.deepseek.com/chat/completions', 'model': 'deepseek-flash'}
        payload = provider.build_payload(cfg, 'q', [])
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertEqual(payload['max_tokens'], 4096)
        self.assertNotIn('max_completion_tokens', payload)

    def test_deepseek_responses_keeps_schema_and_image_for_flash(self):
        cfg = {**config(), 'endpoint': 'https://api.deepseek.com/responses', 'model': 'deepseek-flash'}
        image = {'type': 'input_image', 'image_url': 'data:image/png;base64,c3ludGhldGlj', 'detail': 'high'}
        payload = provider.build_payload(cfg, 'q', [], content=[image])
        self.assertEqual(payload['input'][0]['content'], [image])
        self.assertEqual(payload['text']['format']['type'], 'json_schema')
        with self.assertRaisesRegex(provider.ProviderError, 'capability_unsupported'):
            provider.build_payload({**cfg, 'model': 'deepseek-v4-pro'}, 'q', [], content=[image])

    def test_explicit_unsupported_capabilities_fail_before_sending_or_losing_images(self):
        content = [{'type': 'input_image', 'image_url': 'data:image/png;base64,c3ludGhldGlj'}]
        for cfg, parts in [
            ({**config(), 'capabilities': {'vision': False}}, content),
            ({**config(), 'capabilities': {'json_schema': False}, 'response_format': 'json_schema'}, None),
            ({**config(), 'capabilities': {'stream': False}, 'stream': True}, None),
        ]:
            with self.subTest(cfg=cfg), self.assertRaisesRegex(provider.ProviderError, 'capability_unsupported'):
                provider.build_payload(cfg, 'q', [], content=parts)

    def test_declared_json_schema_unavailable_negotiates_json_object(self):
        cfg = {**config(), 'response_format': 'auto', 'capabilities': {'json_schema': False, 'json_object': True}}
        payload = provider.build_payload(cfg, 'q', [])
        self.assertEqual(payload['text']['format']['type'], 'json_object')

    def test_aliases_are_explicit_and_never_allow_an_unrelated_model(self):
        cfg = {**config(), 'model_aliases': {'requested-model': ['requested-model-2026-10-01']}}
        payload = provider.build_payload(cfg, 'q', [])
        result = provider.generate(cfg, payload, 'synthetic-key', ['e'], lambda *args: answer('requested-model-2026-10-01'))
        self.assertEqual(result['status'], 'answered')
        with self.assertRaisesRegex(provider.ProviderError, 'model_mismatch'):
            provider.generate(cfg, payload, 'synthetic-key', ['e'], lambda *args: answer('unrelated-model'))

    def test_documented_deepseek_alias_is_scoped_to_the_official_endpoint(self):
        cfg = {**config(), 'endpoint': 'https://api.deepseek.com/responses', 'model': 'deepseek-flash'}
        payload = provider.build_payload(cfg, 'q', [])
        result = provider.generate(cfg, payload, 'synthetic-key', ['e'], lambda *args: answer('deepseek-v4.1-flash-1017'))
        self.assertEqual(result['status'], 'answered')
        for changed in ({**cfg, 'endpoint': 'https://example.invalid/responses'}, {**cfg, 'model_aliases': []}):
            with self.subTest(changed=changed), self.assertRaisesRegex(provider.ProviderError, 'model_mismatch'):
                provider.generate(changed, payload, 'synthetic-key', ['e'], lambda *args: answer('deepseek-v4.1-flash-1017'))

    def test_normalization_preserves_the_original_snapshot(self):
        cfg = {**config(), 'endpoint': 'https://example.invalid/v1/', 'reasoning_effort': 'low'}
        saved = copy.deepcopy(cfg)
        normalized = provider.normalize_config(cfg)
        normalized['reasoning']['effort'] = 'high'
        self.assertEqual(cfg, saved)
        self.assertEqual(normalized['endpoint'], 'https://example.invalid/v1/responses')

    def test_malformed_returned_identity_is_rejected_as_provider_error(self):
        for model in ([], {}, None):
            with self.subTest(model=model), self.assertRaisesRegex(provider.ProviderError, 'model_mismatch'):
                provider.validate_response(answer(model), 'requested-model', ['e'], 100, 1000)

    def test_image_probe_never_includes_expected_answer_in_text(self):
        payload = provider.build_probe_payload(config(), image_url='data:image/png;base64,c3ludGhldGlj', nonce='SECRET_EXPECTED_MARKER')
        text = payload['input'][0]['content'][0]['text']
        self.assertNotIn('SECRET_EXPECTED_MARKER', text)
        self.assertIn('operational-probe', text)
        self.assertEqual(payload['input'][0]['content'][1]['type'], 'input_image')

    def test_invalid_booleans_reasoning_and_conflicting_protocols_are_rejected(self):
        for extra in ({'stream': 'false'}, {'reasoning_effort': []}, {'reasoning': {'effort': 3}},
                      {'api_style': 'chat_completions'}, {'capabilities': {'vision': 'yes'}}):
            with self.subTest(extra=extra), self.assertRaises(provider.ProviderError):
                provider.validate_config({**config(), **extra})

    def test_image_context_projection_applies_to_chat_encoding(self):
        cfg = {**config(), 'endpoint': 'https://example.invalid/chat/completions', 'reserve_input_tokens': 5000}
        image = 'data:image/png;base64,' + 'A' * 200000
        payload = provider.build_payload(cfg, 'q', [], content=[{'type': 'input_image', 'image_url': image, 'detail': 'high'}])
        self.assertEqual(payload['messages'][1]['content'][0]['image_url']['url'], image)


class ConfigurableFreezeTests(unittest.TestCase):
    def test_protocol_changes_are_frozen_and_later_tampering_is_rejected(self):
        from tests.formal.test_profile_lifecycle import LifecycleTests
        from formal_src.profile_lifecycle import freeze_formal_profile, validate_frozen_profile
        helper = LifecycleTests()
        options = helper.options()
        options['provider'].update(max_output_tokens=8192, reasoning_effort='max', stream=False)
        frozen = freeze_formal_profile(helper.draft(), **options)
        self.assertTrue(validate_frozen_profile(frozen))
        altered = copy.deepcopy(frozen)
        altered['provider']['max_output_tokens'] = 8193
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            validate_frozen_profile(altered)
