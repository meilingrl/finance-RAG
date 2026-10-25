"""The new API runner must preserve the frozen experiment and bound spending."""
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from llm_execution import prepare_job, execute_job, score_job, parse_response, post_json
from ragpilot.common import read_json, read_jsonl, write_json, write_jsonl
from ragpilot.pipeline import make_fixture, run_offline


def response(answer='$5 million', abstained=False, citations=None, **extra):
    return {
        'id': 'chatcmpl-software-test', 'object': 'chat.completion', 'created': 1,
        'model': 'gpt-6-luna', 'system_fingerprint': 'software-test-only',
        'choices': [{'index': 0, 'finish_reason': 'stop', 'logprobs': None,
                     'message': {'role': 'assistant', 'refusal': None,
                                 'content': json.dumps({'answer': answer, 'scale': '' if abstained else 'million',
                                                        'citations': citations or [], 'abstained': abstained})}}],
        'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120},
        **extra,
    }


def responses_reply():
    return {'id': 'resp-software-test', 'object': 'response', 'created_at': 1,
            'model': 'gpt-6-luna', 'status': 'completed', 'error': None,
            'incomplete_details': None, 'output': [
                {'id': 'rs-software-test', 'type': 'reasoning', 'summary': []},
                {'id': 'msg-software-test', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                 'content': [{'type': 'output_text', 'annotations': [],
                              'text': '{"answer":"$5 million","scale":"million","citations":["fixture-a:p1:u000"],"abstained":false}'}]}],
            'usage': {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120,
                      'input_tokens_details': {'cached_tokens': 0},
                      'output_tokens_details': {'reasoning_tokens': 0}}}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data, self.source, self.job = [self.root / x for x in ('data', 'source', 'job')]
        make_fixture(self.data)
        run_offline(self.data, self.source, split='development')
        self.config = {
            'endpoint': 'https://api.openai.com/v1/chat/completions', 'model': 'gpt-6-luna',
            'api_key_env': 'OPENAI_API_KEY', 'max_completion_tokens': 512,
            'reasoning_effort': 'none', 'reserve_input_tokens': 32768,
            'input_usd_per_million': 0.10, 'output_usd_per_million': 0.50,
            'timeout_seconds': 30,
        }
        self.ledger = self.root / 'budget.jsonl'

    def prepare(self, **kwargs):
        return prepare_job(self.source, self.data, self.job, self.config, **kwargs)

    def transport(self, endpoint, payload, key, timeout):
        user = payload['messages'][1]['content']
        if 'profit' in user:
            return response(None, True)
        return response(citations=['fixture-a:p1:u000'])

    def test_prepare_and_execute_never_open_gold_and_keep_shared_text(self):
        # Opening gold in either stage leaks evaluation data into inference.
        original = Path.read_text
        def guarded(path, *args, **kwargs):
            if 'gold' in path.name:
                raise AssertionError('Gold was opened before scoring')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', guarded):
            manifest = self.prepare(limit=1)
            prepared = read_jsonl(self.job / 'requests.jsonl')[0]
            self.assertEqual(manifest['question_count'], 1)
            text = prepared['payload']['messages'][1]['content']
            self.assertIn('What was revenue in 2024?', text)
            self.assertIn('Revenue in 2024: $5 million', text)
            self.assertNotIn('gold', prepared['payload'])
            result = execute_job(self.job, 'test-secret', self.ledger, 1, transport=self.transport)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(read_jsonl(self.job / 'predictions.jsonl')[0]['status'], 'answered')

    def test_changed_frozen_retrieval_rejected_before_network(self):
        # A changed retrieved evidence set must never be silently accepted.
        rows = read_jsonl(self.source / 'retrieval.jsonl')
        rows[0]['selected_ids'] = []
        write_jsonl(self.source / 'retrieval.jsonl', rows)
        with self.assertRaisesRegex(ValueError, 'retrieval'):
            self.prepare()

    def test_execute_revalidates_inputs_and_job(self):
        self.prepare()
        rows = read_jsonl(self.job / 'requests.jsonl')
        rows[0]['payload']['messages'][1]['content'] = 'Changed evidence'
        write_jsonl(self.job / 'requests.jsonl', rows)
        with self.assertRaises(ValueError):
            execute_job(self.job, 'test-secret', self.ledger, 1, transport=self.transport)
        self.assertFalse(self.ledger.exists())

    def test_changed_saved_prediction_is_rejected_before_resume(self):
        self.prepare()
        execute_job(self.job, 'test-secret', self.ledger, 1, max_calls=1, transport=self.transport)
        predictions = read_jsonl(self.job / 'predictions.jsonl')
        predictions[0]['parsed_answer'] = 'edited answer'
        write_jsonl(self.job / 'predictions.jsonl', predictions)
        with self.assertRaises(ValueError):
            execute_job(self.job, 'test-secret', self.ledger, 1, transport=self.transport)
        self.assertEqual(len(read_jsonl(self.ledger)), 1)

    def test_missing_key_and_zero_budget_cannot_send(self):
        self.prepare()
        for key, budget in [('', 1), ('test-secret', 0)]:
            with self.assertRaises(ValueError):
                execute_job(self.job, key, self.ledger, budget, transport=self.transport)
        self.assertFalse(self.ledger.exists())

    def test_global_budget_survives_restart_and_prevents_second_request(self):
        self.prepare()
        first = execute_job(self.job, 'test-secret', self.ledger, 1, max_calls=1, transport=self.transport)
        second = execute_job(self.job, 'test-secret', self.ledger, 1, max_calls=1, transport=self.transport)
        self.assertEqual(first['status'], 'budget_stopped')
        self.assertEqual(second['status'], 'budget_stopped')
        self.assertEqual(len(read_jsonl(self.ledger)), 1)
        self.assertEqual(len(read_jsonl(self.job / 'predictions.jsonl')), 1)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            score_job(self.job, self.root / 'scores')

    def test_interrupted_attempt_is_not_automatically_sent_again(self):
        self.prepare(limit=1)
        def interrupted(*args):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            execute_job(self.job, 'test-secret', self.ledger, 1, transport=interrupted)
        result = execute_job(self.job, 'test-secret', self.ledger, 1, transport=self.transport)
        self.assertEqual(len(read_jsonl(self.ledger)), 1)
        self.assertEqual(result['status'], 'completed_with_errors')
        self.assertEqual(read_jsonl(self.job / 'predictions.jsonl')[0]['error'], 'previous_attempt_outcome_unknown_not_resent')

    def test_preparing_another_job_does_not_send_identical_request_again(self):
        self.prepare(limit=1)
        execute_job(self.job, 'test-secret', self.ledger, 1, transport=self.transport)
        second = self.root / 'second-job'
        prepare_job(self.source, self.data, second, self.config, limit=1)
        result = execute_job(second, 'test-secret', self.ledger, 1, transport=self.transport)
        self.assertEqual(len(read_jsonl(self.ledger)), 1)
        self.assertEqual(result['status'], 'completed_with_errors')

    def test_api_error_is_retained_without_retry_or_secret(self):
        self.prepare(limit=1)
        def failed(*args):
            raise RuntimeError('provider reflected test-secret in an error')
        execute_job(self.job, 'test-secret', self.ledger, 1, transport=failed)
        self.assertEqual(len(read_jsonl(self.ledger)), 1)
        rows = read_jsonl(self.job / 'predictions.jsonl')
        self.assertEqual(rows[0]['status'], 'api_error')
        self.assertNotIn('test-secret', (self.job / 'predictions.jsonl').read_text())

    def test_api_model_and_usage_errors_keep_resume_blocked_until_inspection(self):
        def authentication_error(*args):
            raise RuntimeError('authentication failed')
        def wrong_model(*args):
            return response(citations=['fixture-a:p1:u000'], model='another-model')
        def usage_overrun(*args):
            return response(citations=['fixture-a:p1:u000'], usage={
                'prompt_tokens': 40000, 'completion_tokens': 20, 'total_tokens': 40020})
        for i, transport in enumerate((authentication_error, wrong_model, usage_overrun)):
            job, ledger = self.root / f'paused-{i}', self.root / f'ledger-{i}.jsonl'
            prepare_job(self.source, self.data, job, self.config)
            execute_job(job, 'test-secret', ledger, 1, transport=transport)
            with self.assertRaisesRegex(ValueError, 'inspection'):
                execute_job(job, 'test-secret', ledger, 1, transport=self.transport)
            self.assertEqual(len(read_jsonl(ledger)), 1)

    def test_successful_resume_does_not_duplicate_requests_and_scores_same_baseline(self):
        self.prepare()
        execute_job(self.job, 'test-secret', self.ledger, 1, transport=self.transport)
        execute_job(self.job, 'test-secret', self.ledger, 1, transport=self.transport)
        self.assertEqual(len(read_jsonl(self.ledger)), 2)
        summary = score_job(self.job, self.root / 'scores')
        self.assertEqual(summary['answer_metrics']['E2']['correct_per_executed']['numerator'], 1)
        self.assertEqual(summary['answer_metrics']['E2']['correct_per_executed']['denominator'], 2)
        self.assertEqual(summary['answer_metrics']['E2']['status_counts']['abstention'], 1)
        self.assertEqual(summary['answer_metrics']['E3']['status'], 'not_executed')

    def test_expensive_request_is_not_sent_even_with_call_slots_left(self):
        self.prepare(limit=1)
        result = execute_job(self.job, 'test-secret', self.ledger, 0.000001, transport=self.transport)
        self.assertEqual(result['status'], 'budget_stopped')
        self.assertEqual(len(read_jsonl(self.ledger)), 0)
        self.assertFalse((self.job / 'predictions.jsonl').exists())

    def test_responses_api_uses_correct_wire_format_and_records_unknown_provider_cost(self):
        self.config.update(wire_api='responses', endpoint='https://api.pinaic.com/v1/responses',
                           input_usd_per_million=None, output_usd_per_million=None)
        self.prepare(limit=1)
        payload=read_jsonl(self.job/'requests.jsonl')[0]['payload']
        self.assertNotIn('messages',payload)
        self.assertNotIn('response_format',payload)
        self.assertFalse(payload['store'])
        self.assertEqual(payload['reasoning'],{'effort':'none'})
        self.assertIn('What was revenue in 2024?',payload['input'][0]['content'])
        result=execute_job(self.job,'test-secret',self.ledger,None,
                           transport=lambda *args:responses_reply())
        self.assertEqual(result['status'],'completed')
        prediction=read_jsonl(self.job/'predictions.jsonl')[0]
        self.assertEqual(prediction['parsed_answer'],'$5 million')
        self.assertEqual(prediction['usage']['input_tokens'],100)
        self.assertIsNone(prediction['cost_estimate']['amount'])
        self.assertIsNone(read_jsonl(self.ledger)[0]['reserved_usd'])

    def test_unknown_provider_price_cannot_be_claimed_as_a_dollar_budget(self):
        self.config.update(wire_api='responses', endpoint='https://api.pinaic.com/v1/responses',
                           input_usd_per_million=None, output_usd_per_million=None)
        self.prepare(limit=1)
        with self.assertRaisesRegex(ValueError,'price'):
            execute_job(self.job,'test-secret',self.ledger,1,transport=self.transport)
        self.assertFalse(self.ledger.exists())

    def test_failed_response_stops_before_sending_the_next_question(self):
        self.config.update(wire_api='responses',endpoint='https://api.pinaic.com/v1/responses',
                           input_usd_per_million=None,output_usd_per_million=None)
        self.prepare()
        def failed(*args):
            raw=responses_reply();raw.update(status='failed',error={'code':'server_error'})
            return raw
        result=execute_job(self.job,'test-secret',self.ledger,None,transport=failed)
        self.assertEqual(result['status'],'incomplete_requires_inspection')
        self.assertEqual(len(read_jsonl(self.ledger)),1)


class ParseTests(unittest.TestCase):
    def test_scale_is_a_magnitude_field_not_a_place_for_years_or_explanations(self):
        raw=response(citations=['e1'])
        content=json.loads(raw['choices'][0]['message']['content'])
        content['scale']='years'
        raw['choices'][0]['message']['content']=json.dumps(content)
        self.assertEqual(parse_response(raw,['e1'])['status'],'parse_error')

    def test_invalid_json_wrong_types_or_unknown_citations_do_not_become_answers(self):
        invalid = [response(citations=['other-document']), response(answer=5, citations=['e1'])]
        broken = response(); broken['choices'][0]['message']['content'] = 'not JSON'
        invalid.append(broken)
        for raw in invalid:
            self.assertEqual(parse_response(raw, ['e1'])['status'], 'parse_error')

    def test_refusal_and_output_truncation_are_separate_from_incorrect_answers(self):
        raw = response(citations=['e1'])
        raw['choices'][0]['finish_reason'] = 'length'
        self.assertEqual(parse_response(raw, ['e1'])['status'], 'truncated')
        raw = response(); raw['choices'][0]['message']['refusal'] = 'Cannot comply'
        self.assertEqual(parse_response(raw, ['e1'])['status'], 'rejection')

    def test_responses_parser_skips_reasoning_and_retains_truncation_and_refusal(self):
        raw=responses_reply()
        self.assertEqual(parse_response(raw,['fixture-a:p1:u000'])['status'],'answered')
        raw['status']='incomplete';raw['incomplete_details']={'reason':'max_output_tokens'}
        self.assertEqual(parse_response(raw,['fixture-a:p1:u000'])['status'],'truncated')
        raw=responses_reply()
        raw['output'][1]['content']=[{'type':'refusal','refusal':'Cannot comply'}]
        self.assertEqual(parse_response(raw,['fixture-a:p1:u000'])['status'],'rejection')


class TransportTests(unittest.TestCase):
    def test_sse_without_newlines_cannot_keep_extending_total_deadline(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                for _ in range(20):
                    try:self.wfile.write(b'x'*32);self.wfile.flush()
                    except (BrokenPipeError,ConnectionResetError):break
                    time.sleep(0.02)
        server=HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.handle_request,daemon=True);thread.start()
        started=time.monotonic()
        try:
            with self.assertRaisesRegex(RuntimeError,'stream_size_or_time_limit'):
                post_json(f'http://127.0.0.1:{server.server_port}/v1/responses',{},'test-secret',0.1)
            self.assertLess(time.monotonic()-started,0.3)
        finally:server.server_close();thread.join(timeout=2)

    def test_responses_stream_returns_completed_response_instead_of_treating_sse_as_json(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                events=[{'type':'response.created','response':{'status':'in_progress'}},
                        {'type':'response.output_text.delta','delta':'partial output'},
                        {'type':'response.completed','response':responses_reply()}]
                for event in events:
                    self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode())
        server=HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.handle_request,daemon=True);thread.start()
        try:
            raw=post_json(f'http://127.0.0.1:{server.server_port}/v1/responses',
                          {'model':'gpt-6-luna','stream':True},'test-secret',5)
            self.assertEqual(raw['status'],'completed')
            self.assertEqual(parse_response(raw,['fixture-a:p1:u000'])['parsed_answer'],'$5 million')
        finally:server.server_close();thread.join(timeout=5)

    def test_real_http_transport_preserves_request(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps(response(None, True)).encode())
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        try:
            result = post_json(f'http://127.0.0.1:{server.server_port}/v1/chat/completions',
                               {'model': 'gpt-6-luna', 'messages': []}, 'test-secret', 5)
            self.assertEqual(result['model'], 'gpt-6-luna')
            self.assertEqual(received, [{'model': 'gpt-6-luna', 'messages': []}])
        finally:
            server.server_close(); thread.join(timeout=5)

    def test_redirect_cannot_forward_authorization_to_another_endpoint(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.send_response(302)
                self.send_header('Location', 'http://127.0.0.1:1/credential-leak')
                self.end_headers()
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        try:
            with self.assertRaisesRegex(RuntimeError, 'redirect_rejected'):
                post_json(f'http://127.0.0.1:{server.server_port}/v1/chat/completions', {}, 'test-secret', 5)
        finally:
            server.server_close(); thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
