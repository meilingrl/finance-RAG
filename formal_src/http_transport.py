"""Guarded Responses HTTP/SSE transport with an explicit application identity.

Ported from the frozen pilot transport: parsing, limits, redirect policy and
credential redaction are preserved. Only the explicit User-Agent is added.
The current gateway rejects urllib's implicit identity before inference.
"""
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from scripts.llm_execution import APIError, NoRedirect

USER_AGENT = 'rag-pilot/1.0'

def post_json(endpoint, payload, key, timeout):
    url = urllib.parse.urlsplit(endpoint)
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname == '127.0.0.1'):
        raise APIError('insecure_transport_rejected')
    request = urllib.request.Request(endpoint, json.dumps(payload, allow_nan=False).encode('utf-8'),
                                     {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                                      'User-Agent': USER_AGENT})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as reply:
            if 'text/event-stream' in reply.headers.get('Content-Type', ''):
                size, frame, pending = 0, [], b''
                deadline = time.monotonic() + timeout
                while True:
                    chunk = reply.read1(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > 2_000_000 or time.monotonic() > deadline:
                        raise APIError('stream_size_or_time_limit')
                    pending += chunk
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        text = line.decode('utf-8').rstrip('\r')
                        if text.startswith('data:'):
                            frame.append(text[5:].lstrip())
                        elif not text and frame:
                            content = '\n'.join(frame).replace(key, '[REDACTED]'); frame = []
                            if content == '[DONE]':
                                raise APIError('stream_ended_without_final_response')
                            event = json.loads(content)
                            if event.get('type') in ('response.completed', 'response.failed', 'response.incomplete'):
                                return event['response']
                            if event.get('type') == 'error':
                                raise APIError('response_stream_error')
                raise APIError('stream_ended_without_final_response')
            body = reply.read(2_000_001)
        if len(body) > 2_000_000:
            raise APIError('response_too_large')
        # Do not retain credentials even if a provider reflects one in a response.
        raw = body.decode('utf-8').replace(key, '[REDACTED]')
        return json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except urllib.error.HTTPError as exc:
        # Error bodies, headers, URLs and exception strings are deliberately not logged.
        raise APIError('http_' + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise APIError('transport_' + type(exc).__name__) from None


