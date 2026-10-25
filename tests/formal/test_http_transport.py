"""Gateway UA compatibility must not weaken existing wire/security bounds."""
import contextlib,http.server,json,threading,unittest

@contextlib.contextmanager
def server():
 class Handler(http.server.BaseHTTPRequestHandler):
  seen=[]
  def log_message(self,*args):pass
  def do_POST(self):
   body=self.rfile.read(int(self.headers.get('Content-Length','0')))
   type(self).seen.append({'path':self.path,'agent':self.headers.get('User-Agent'),'body':body,'auth':self.headers.get('Authorization')})
   if self.path=='/redirect':
    self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{self.server.server_port}/target');self.end_headers();return
   if (self.headers.get('User-Agent') or '').startswith('Python-urllib'):
    self.send_response(502);self.end_headers();self.wfile.write(b'{"error":{"type":"upstream_error","message":"Upstream access forbidden"}}');return
   self.send_response(200);self.send_header('Content-Type','text/event-stream' if self.path in ['/sse','/done','/oversize'] else 'application/json');self.end_headers()
   try:
    if self.path=='/sse':self.wfile.write(b'event: response.completed\ndata: '+json.dumps({'type':'response.completed','response':{'id':'r','reflected':'test-secret'}}).encode()+b'\n\n')
    elif self.path=='/done':self.wfile.write(b'data: [DONE]\n\n')
    elif self.path=='/oversize':self.wfile.write(b'x'*2_000_001)
    else:self.wfile.write(b'{"ok":true,"reflected":"test-secret"}')
   except (BrokenPipeError,ConnectionResetError):pass
 httpd=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
 try:yield f'http://127.0.0.1:{httpd.server_port}',Handler
 finally:httpd.shutdown();httpd.server_close();thread.join()

class TransportTests(unittest.TestCase):
 def test_explicit_product_agent_preserves_exact_payload_and_redacts_reflected_key(self):
  from formal_src.provider import post_json
  with server() as (url,handler):
   payload={'model':'m','input':[{'role':'user','content':'synthetic only'}],'stream':True,'store':False}
   result=post_json(url+'/json',payload,'test-secret',2)
   self.assertEqual(handler.seen[0]['agent'],'rag-pilot/1.0')
   self.assertEqual(json.loads(handler.seen[0]['body']),payload)
   self.assertEqual(result['reflected'],'[REDACTED]')
   self.assertEqual(handler.seen[0]['auth'],'Bearer test-secret')
 def test_sse_completion_done_and_stream_size_guards(self):
  from formal_src.provider import post_json,APIError
  with server() as (url,handler):
   self.assertEqual(post_json(url+'/sse',{},'test-secret',2)['id'],'r')
   with self.assertRaisesRegex(APIError,'stream_ended_without_final_response'):post_json(url+'/done',{},'test-secret',2)
   with self.assertRaisesRegex(APIError,'stream_size_or_time_limit'):post_json(url+'/oversize',{},'test-secret',2)
 def test_redirects_remain_rejected_and_no_key_forwarded(self):
  from formal_src.provider import post_json,APIError
  with server() as (url,handler):
   with self.assertRaisesRegex(APIError,'redirect_rejected'):post_json(url+'/redirect',{},'test-secret',2)
   self.assertEqual([r['path'] for r in handler.seen],['/redirect'])
