"""SSE must assemble Chat deltas without accepting broken or mixed identities."""
import json
import unittest
from unittest.mock import MagicMock, patch
from formal_src.http_transport import post_json, TransportFailure
from scripts.llm_execution import parse_response


def chunk(content=None, finish=None, **extra):
    return {'id': 'synthetic-chat', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'synthetic-model',
            'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': finish}], **extra}


class StreamCompatibilityTests(unittest.TestCase):
    def send(self, events, *, raw_suffix=b''):
        body = b''.join(b'data: ' + json.dumps(e).encode() + b'\r\n\r\n' for e in events) + raw_suffix
        reply = MagicMock()
        reply.__enter__.return_value = reply
        reply.status = 200
        reply.headers = {'Content-Type': 'text/event-stream; charset=utf-8'}
        # Deliberately split inside JSON strings and line delimiters.
        reply.read1.side_effect = [body[i:i+17] for i in range(0, len(body), 17)] + [b'']
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = reply
            return post_json('https://example.invalid/chat/completions', {'messages': [], 'stream': True}, 'synthetic-key', 2)

    def test_chat_sse_combines_content_keeps_usage_and_ignores_reasoning(self):
        text = '{"answer":"READY","scale":"","citations":["e"],"abstained":false}'
        raw = self.send([chunk(None, choices=[{'index': 0, 'delta': {'role': 'assistant', 'reasoning_content': 'private reasoning'}, 'finish_reason': None}]),
                         chunk(text[:23]), chunk(text[23:]), chunk('', 'stop', usage={'prompt_tokens': 10, 'completion_tokens': 20})],
                        raw_suffix=b'data: [DONE]\r\n\r\n')
        self.assertEqual(raw['model'], 'synthetic-model')
        self.assertEqual(raw['usage'], {'prompt_tokens': 10, 'completion_tokens': 20})
        self.assertEqual(parse_response(raw, ['e'])['parsed_answer'], 'READY')
        self.assertNotIn('private reasoning', json.dumps(raw))

    def test_chat_sse_keeps_truncation_and_refusal(self):
        truncated = self.send([chunk('partial JSON', 'length')], raw_suffix=b'data: [DONE]\n\n')
        self.assertEqual(parse_response(truncated, ['e'])['status'], 'truncated')
        refused = self.send([chunk(None, 'stop', choices=[{'index': 0, 'delta': {'refusal': 'No'}, 'finish_reason': 'stop'}])], raw_suffix=b'data: [DONE]\n\n')
        self.assertEqual(parse_response(refused, ['e'])['status'], 'rejection')

    def test_chat_sse_done_without_finish_and_mixed_model_are_rejected(self):
        for events in ([chunk('unfinished')], [chunk('a'), chunk('b', 'stop', model='different-model')]):
            with self.subTest(events=events), self.assertRaises(TransportFailure):
                self.send(events, raw_suffix=b'data: [DONE]\n\n')

    def test_nonstream_response_content_filter_is_a_rejection(self):
        raw = {'id': 'synthetic', 'model': 'synthetic-model', 'status': 'incomplete',
               'incomplete_details': {'reason': 'content_filter'}, 'output': []}
        self.assertEqual(parse_response(raw, ['e'])['status'], 'rejection')

    def test_malformed_response_parts_return_parse_error_without_crashing(self):
        for output in ([None], [{'type': 'message', 'role': 'assistant', 'content': [None]}]):
            with self.subTest(output=output):
                self.assertEqual(parse_response({'status': 'completed', 'output': output}, ['e'])['status'], 'parse_error')

    def test_malformed_choice_and_incomplete_details_return_parse_error(self):
        for raw in ({'choices': [None]}, {'choices': [{'finish_reason': 'stop', 'message': None}]},
                    {'status': 'incomplete', 'incomplete_details': 'bad', 'output': []}):
            with self.subTest(raw=raw):
                self.assertEqual(parse_response(raw, ['e'])['status'], 'parse_error')

    def test_chat_json_content_parts_are_parsed_without_reasoning_text(self):
        raw = {'choices': [{'finish_reason': 'stop', 'message': {'content': [
            {'type': 'reasoning_text', 'text': 'not an answer'},
            {'type': 'text', 'text': '{"answer":"READY","scale":"","citations":["e"],"abstained":false}'}]}}]}
        self.assertEqual(parse_response(raw, ['e'])['parsed_answer'], 'READY')
