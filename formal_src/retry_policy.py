"""Bounded coursework retries; error diagnostics never contain response text or secrets."""
import re
HTTP_RETRY={'http_408','http_429','http_500','http_502','http_503','http_504'}
TRANSPORT_RETRY={'transport_outcome_unknown','stream_ended_without_final_response','stream_eof_without_final_response','stream_done_without_final_response','stream_deadline_exceeded','response_stream_error','response_not_completed','response_schema_error','parse_error','truncated'}
EXCEPTION_TYPES={'TimeoutError','ConnectionError','ConnectionResetError','ConnectionAbortedError','BrokenPipeError','URLError','OSError','JSONDecodeError','UnicodeDecodeError','IncompleteRead','RemoteDisconnected','SSLError','ValueError','HTTPException'}
def code_for(exc):
 value=str(exc)
 if re.fullmatch(r'http_[1-5][0-9]{2}',value):return value
 if value in TRANSPORT_RETRY|{'stream_size_limit','response_too_large','insecure_transport_rejected'}:return value
 if value.startswith('transport_') and value[10:] in EXCEPTION_TYPES:return value
 return 'transport_'+type(exc).__name__ if type(exc).__name__ in EXCEPTION_TYPES else 'response_schema_error'
def retryable(code):
 return code in HTTP_RETRY|TRANSPORT_RETRY or bool(code and code.startswith('transport_') and code[10:] in EXCEPTION_TYPES)
