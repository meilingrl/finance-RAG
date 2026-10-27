"""Preparation may reuse only proven capabilities and must keep service keys local."""
import contextlib
import http.server
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.provider_checks import provider_ready
from app.service import ApplicationService
from formal_src.provider import APIError, build_payload
from tests.app.test_provider_parallel import SETTINGS, response

ROOT = Path(__file__).resolve().parents[2]


def profile(settings, provider=None):
    return {'provider': provider if provider is not None else {
                key: value for key, value in settings.items() if key not in ('model', 'concurrency')},
            'models': {'primary': settings['model']}, 'concurrency': settings.get('concurrency', 1)}


@contextlib.contextmanager
def redirecting_catalog():
    """Only a loopback test server; following the redirect would expose its key."""
    class Handler(http.server.BaseHTTPRequestHandler):
        paths = []

        def log_message(self, *args):
            pass

        def do_GET(self):
            type(self).paths.append(self.path)
            if self.path == '/models':
                self.send_response(302)
                self.send_header('Location', '/redirected-models')
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"data":[{"id":"test-model"}]}')

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port), Handler.paths
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class OnboardingSafetyTests(unittest.TestCase):
    def test_text_only_proof_cannot_claim_image_support(self):
        with tempfile.TemporaryDirectory() as td:
            calls = []

            def transport(endpoint, payload, key, timeout):
                calls.append(payload)
                return response(payload)

            service = ApplicationService(ROOT, td, provider_transport=transport)
            settings = {**SETTINGS, 'concurrency': 1}
            report = service.check_provider(settings, service.set_credential('synthetic-review-key'), 'compatibility')
            self.assertTrue(report['ready'])
            self.assertEqual(len(calls), 1)
            original = profile(settings, report['effective_provider'])
            self.assertTrue(provider_ready(td, original))
            self.assertFalse(provider_ready(td, original, require_vision=True))

    def test_negotiated_json_mode_does_not_certify_the_original_schema_profile(self):
        with tempfile.TemporaryDirectory() as td:
            def transport(endpoint, payload, key, timeout):
                if payload['text']['format']['type'] == 'json_schema':
                    raise APIError('http_400')
                return response(payload)

            service = ApplicationService(ROOT, td, provider_transport=transport)
            settings = {**SETTINGS, 'concurrency': 1}
            report = service.check_provider(settings, service.set_credential('synthetic-review-key'), 'compatibility')
            self.assertTrue(report['ready'])
            self.assertEqual(report['effective_provider']['response_format'], 'json_object')
            original = profile(settings)
            self.assertEqual(build_payload({**original['provider'], 'model': settings['model']}, 'q', [])['text']['format']['type'], 'json_schema')
            self.assertFalse(provider_ready(td, original))
            self.assertTrue(provider_ready(td, profile(settings, report['effective_provider'])))

    def test_complete_chat_base_url_is_sent_as_chat_without_an_added_responses_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            seen = []

            def transport(endpoint, payload, key, timeout):
                seen.append((endpoint, payload))
                return response(payload)

            service = ApplicationService(ROOT, td, provider_transport=transport)
            settings = {key: value for key, value in SETTINGS.items() if key != 'endpoint'}
            settings['base_url'] = 'https://example.invalid/v1/chat/completions'
            report = service.check_provider(settings, service.set_credential('synthetic-review-key'), 'generation')
            self.assertTrue(report['ready'])
            self.assertEqual(seen[0][0], 'https://example.invalid/v1/chat/completions')
            self.assertIn('messages', seen[0][1])
            self.assertNotIn('input', seen[0][1])

    def test_catalog_url_preserves_the_normalized_endpoint_origin_and_api_prefix(self):
        cases = [
            ('endpoint', 'https://example.invalid', 'https://example.invalid/models'),
            ('endpoint', 'https://example.invalid/v1', 'https://example.invalid/v1/models'),
            ('endpoint', 'https://example.invalid/v1/chat/completions', 'https://example.invalid/v1/models'),
            ('base_url', 'https://example.invalid:9443/v1/responses', 'https://example.invalid:9443/v1/models'),
        ]
        for field, endpoint, expected in cases:
            with self.subTest(endpoint=endpoint), tempfile.TemporaryDirectory() as td:
                seen = []

                def catalog(url, key, timeout):
                    seen.append(url)
                    return {'data': [{'id': SETTINGS['model']}]}

                service = ApplicationService(ROOT, td, catalog_transport=catalog)
                settings = {key: value for key, value in SETTINGS.items() if key != 'endpoint'}
                settings[field] = endpoint
                result = service.check_provider(settings, service.set_credential('synthetic-review-key'), 'catalog')
                self.assertTrue(result['ready'])
                self.assertEqual(seen, [expected])

    def test_catalog_redirect_is_rejected_before_a_second_request(self):
        with tempfile.TemporaryDirectory() as td, redirecting_catalog() as (base, paths):
            service = ApplicationService(ROOT, td)
            settings = {**SETTINGS, 'endpoint': base + '/responses'}
            try:
                result = service.check_provider(settings, service.set_credential('synthetic-review-key'), 'catalog')
            except APIError:
                pass
            else:
                self.assertFalse(result['ready'])
            self.assertEqual(paths, ['/models'])

    def test_restarted_session_cannot_prepare_ready_using_an_old_credential_reference(self):
        with tempfile.TemporaryDirectory() as td:
            def transport(endpoint, payload, key, timeout):
                image = any(part.get('type') == 'input_image' for part in payload['input'][0]['content'])
                return response(payload, '123456' if image else 'READY')

            service = ApplicationService(ROOT, td, provider_transport=transport)
            settings = {**SETTINGS, 'concurrency': 1}
            adopted = service.clone_profile('formal-v1', {
                'models': {'primary': SETTINGS['model']},
                'provider': profile(settings)['provider'], 'concurrency': 1})
            old_reference = service.set_credential('synthetic-review-key')
            with patch('app.provider_checks.make_image_probe', return_value=('data:image/png;base64,c3ludGhldGlj', '123456')):
                report = service.check_provider({**settings, 'require_vision': True}, old_reference, 'compatibility')
            self.assertTrue(report['ready'])
            restarted = ApplicationService(ROOT, td)
            self.assertTrue(provider_ready(td, adopted, require_vision=True))
            # All independent data/freeze/preflight prerequisites are ready. A
            # persisted opaque reference still cannot replace a current key.
            with patch.object(restarted, 'check_resources', return_value={'ready_for': ['freeze'], 'blocking_items': []}), \
                 patch.object(restarted, '_compiled_plan', return_value={'units': [
                     {'work_type': 'generation', 'model_role': 'primary', 'condition': 'E3'}]}), \
                 patch.object(restarted, 'freeze_profile', return_value={'frozen': True, 'freeze_sha': 'a' * 64}), \
                 patch.object(restarted, 'preflight', return_value={'ready': True, 'blocking_items': [], 'estimated_logical_generations': 3600}):
                result = restarted.prepare_experiment(adopted['profile_id'], 'synthetic-formal', old_reference)
            self.assertFalse(result['ready'])
            self.assertIn('credential_required', result['blocking_items'])

    def test_a_valid_frozen_profile_with_no_reasoning_effort_remains_exportable(self):
        from tests.formal.test_profile_lifecycle import LifecycleTests
        from formal_src.profile_lifecycle import freeze_formal_profile
        from formal_src.course_export import runtime_configuration

        helper = LifecycleTests()
        options = helper.options()
        options['provider']['reasoning_effort'] = None
        frozen = freeze_formal_profile(helper.draft(), **options)
        result = runtime_configuration({'run_id': 'synthetic-review', 'resolved_config': frozen,
            'code_sha': frozen['code_sha'], 'freeze_sha': frozen['freeze_sha'],
            'resource_sha': frozen['resource_manifest_sha']})
        self.assertEqual(result['resolved_config'], frozen)
        self.assertIsNone(result['model_provider_identity']['provider']['reasoning_effort'])

class PublicConfigurationErrorTests(unittest.TestCase):
    def test_invalid_generation_limits_return_an_actionable_public_message(self):
        from fastapi.testclient import TestClient
        from app.api import create_app
        with tempfile.TemporaryDirectory() as td:
            service=ApplicationService(ROOT,td)
            client=TestClient(create_app(service))
            result=client.post('/api/v1/profiles/formal-v1/clone',headers={'x-session-id':service.session_id},json={'models':{'primary':'deepseek-flash','secondary':None},'provider':{'endpoint':'https://api.deepseek.com','max_output_tokens':0,'reserve_input_tokens':10000}})
            self.assertEqual(result.status_code,400)
            self.assertEqual(result.json()['code'],'provider_limits_missing')
            self.assertIn('正整数',result.json()['message'])
