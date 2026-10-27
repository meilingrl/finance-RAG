"""Bounded Responses/Chat transport; diagnostics omit URLs, bodies and credentials."""
import json
import time
import urllib.request
import urllib.error
import urllib.parse
import http.client
from scripts.llm_execution import APIError, NoRedirect

USER_AGENT = 'rag-pilot/1.0'

class TransportFailure(APIError):
    def __init__(self, code, diagnostics):
        super().__init__(code)
        self.diagnostics = diagnostics

def post_json(endpoint, payload, key, timeout):
    started = time.monotonic()
    info = {'bytes_received': 0, 'frames_received': 0, 'stream_state': 'not_started'}
    def failure(code, **extra):
        return TransportFailure(code, {**info, **extra, 'elapsed_ms': int((time.monotonic()-started)*1000)})
    url = urllib.parse.urlsplit(endpoint)
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname == '127.0.0.1'):
        raise failure('insecure_transport_rejected')
    request = urllib.request.Request(endpoint, json.dumps(payload, allow_nan=False).encode('utf-8'),
        {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'User-Agent': USER_AGENT})
    def decode(raw):
        return json.loads(raw.replace(key, '[REDACTED]') if key else raw,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    stream_kind = None
    chat = {'object': 'chat.completion', 'choices': {}}
    def completed_chat():
        choices = list(chat['choices'].values())
        if not choices or any(not c['finish_reason'] for c in choices):
            raise failure('stream_done_without_final_response', stream_state='done_without_final')
        return {**chat, 'choices': sorted(choices, key=lambda c: c['index'])}
    def consume_chat(event):
        nonlocal stream_kind
        if stream_kind not in (None, 'chat'):
            raise failure('response_schema_error')
        stream_kind = 'chat'
        info['last_event'] = 'chat.chunk'
        for name in ('id', 'model', 'created'):
            if name in event:
                if name in chat and chat[name] != event[name]:
                    raise failure('response_schema_error')
                chat[name] = event[name]
        if event.get('usage') is not None:
            chat['usage'] = event['usage']
        if not isinstance(event['choices'], list):
            raise failure('response_schema_error')
        for part in event['choices']:
            if not isinstance(part, dict) or type(part.get('index')) is not int or part['index'] < 0:
                raise failure('response_schema_error')
            choice = chat['choices'].setdefault(part['index'], {'index': part['index'], 'finish_reason': None,
                      'message': {'role': 'assistant', 'content': '', 'refusal': None}})
            delta = part.get('delta', {})
            if not isinstance(delta, dict):
                raise failure('response_schema_error')
            for name in ('content', 'refusal'):
                value = delta.get(name)
                if value is not None:
                    if not isinstance(value, str) or choice['finish_reason'] is not None and value:
                        raise failure('response_schema_error')
                    choice['message'][name] = (choice['message'][name] or '') + value
            if part.get('finish_reason') is not None:
                if not isinstance(part['finish_reason'], str) or choice['finish_reason'] is not None:
                    raise failure('response_schema_error')
                choice['finish_reason'] = part['finish_reason']
    def consume(frame):
        nonlocal stream_kind
        info['frames_received'] += 1
        content = '\n'.join(frame)
        if content == '[DONE]':
            if stream_kind == 'chat':
                return completed_chat()
            raise failure('stream_done_without_final_response', stream_state='done_without_final')
        event = decode(content)
        if not isinstance(event, dict):
            raise failure('response_schema_error')
        kind = event.get('type')
        if 'choices' in event:
            consume_chat(event)
            return None
        if stream_kind == 'chat' and kind != 'error':
            raise failure('response_schema_error')
        stream_kind = 'responses'
        info['last_event'] = kind if kind in ('response.created', 'response.in_progress', 'response.output_text.delta', 'response.completed', 'response.failed', 'response.incomplete', 'error') else 'other'
        if kind in ('response.completed', 'response.failed', 'response.incomplete'):
            if not isinstance(event.get('response'), dict):
                raise failure('response_schema_error')
            return event['response']
        if kind == 'error':
            raise failure('response_stream_error', stream_state='error_event')
        return None
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as reply:
            info['http_status'] = reply.status
            if 'text/event-stream' in reply.headers.get('Content-Type', '').lower():
                frame, pending = [], b''
                info['stream_state'] = 'receiving'
                while True:
                    chunk = reply.read1(65536)
                    if not chunk:
                        break
                    info['bytes_received'] += len(chunk)
                    if info['bytes_received'] > 2_000_000:
                        raise failure('stream_size_limit')
                    if time.monotonic()-started > timeout:
                        raise failure('stream_deadline_exceeded')
                    pending += chunk
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        text = line.decode('utf-8').rstrip('\r')
                        if text.startswith('data:'):
                            frame.append(text[5:].lstrip())
                        elif not text and frame:
                            result = consume(frame); frame = []
                            if result is not None:
                                return result
                # Accept a complete final event even if the peer omits the last blank line.
                if pending:
                    text = pending.decode('utf-8').rstrip('\r')
                    if text.startswith('data:'):
                        frame.append(text[5:].lstrip())
                if frame:
                    result = consume(frame)
                    if result is not None:
                        return result
                raise failure('stream_eof_without_final_response', stream_state='eof_without_final')
            info['stream_state'] = 'json_body'
            body = reply.read(2_000_001)
            info['bytes_received'] = len(body)
        if len(body) > 2_000_000:
            raise failure('response_too_large')
        result = decode(body.decode('utf-8'))
        if not isinstance(result, dict):
            raise failure('response_schema_error')
        return result
    except TransportFailure:
        raise
    except urllib.error.HTTPError as exc:
        raise failure('http_' + str(exc.code), http_status=exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as exc:
        extra = {'cause_type': type(exc).__name__}
        if isinstance(exc, urllib.error.URLError):
            extra['reason_type'] = type(exc.reason).__name__
        raise failure('transport_' + type(exc).__name__, **extra) from None
