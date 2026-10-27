"""Bounded Responses transport; diagnostics never retain URLs, bodies or credentials."""
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
    def consume(frame):
        info['frames_received'] += 1
        content = '\n'.join(frame)
        if content == '[DONE]':
            raise failure('stream_done_without_final_response', stream_state='done_without_final')
        event = decode(content)
        if not isinstance(event, dict):
            raise failure('response_schema_error')
        kind = event.get('type')
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
            if 'text/event-stream' in reply.headers.get('Content-Type', ''):
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
