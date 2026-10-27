"""Local HTTP adapter. Mutations require a session header and local Origin."""
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse
from formal_src.contracts import ContractError
from urllib.parse import urlsplit

def create_app(service):
 app=FastAPI()
 @app.middleware('http')
 async def local(request,call_next):
  host=request.url.hostname
  if host not in ('localhost','127.0.0.1','::1','testserver'):return JSONResponse({'code':'local_only'},403)
  if request.method not in ('GET','HEAD','OPTIONS'):
   origin=request.headers.get('origin')
   if request.headers.get('x-session-id')!=service.session_id:return JSONResponse({'code':'invalid_session'},403)
   if origin:
    u=urlsplit(origin)
    if u.scheme not in ('http','https') or u.hostname not in ('localhost','127.0.0.1','::1','testserver') or u.netloc!=request.url.netloc:return JSONResponse({'code':'invalid_origin'},403)
  return await call_next(request)
 @app.exception_handler(Exception)
 async def failure(request,exc):return JSONResponse({'code':'request_failed','message':'Request could not be completed','retryable':False,'blocking_items':[]},400)
 @app.exception_handler(ValueError)
 async def invalid(request,exc):return JSONResponse(exc.to_dict() if isinstance(exc,ContractError) else {'code':'invalid_request','message':'Invalid request','retryable':False,'blocking_items':[]},400)
 @app.get('/api/v1/capabilities')
 def capabilities():return service.capabilities()
 @app.put('/api/v1/settings')
 def settings(values:dict):return service.save_settings(values)
 @app.put('/api/v1/profiles/{pid}')
 def profile(pid:str,values:dict):return service.save_profile(pid,values)
 @app.post('/api/v1/profiles/{pid}/clone')
 def clone(pid:str,values:dict):return service.clone_profile(pid,values)
 @app.post('/api/v1/credentials/session')
 def credential(values:dict):return {'credential_ref':service.set_credential(values.get('api_key'))}
 @app.post('/api/v1/provider/check')
 def provider(values:dict):return service.check_provider(values['settings'],values.get('credential_ref'),values['level'])
 @app.post('/api/v1/resources/register')
 def register(values:dict):return service.register_resources(values['root_path'],values.get('review_policy'))
 @app.get('/api/v1/resources/{rid}')
 def resource(rid:str):return service.check_resources(rid)
 @app.post('/api/v1/resources/{rid}/reviews')
 def reviews(rid:str,values:dict):return service.import_reviews(rid,values)
 @app.post('/api/v1/profiles/{pid}/freeze')
 def freeze(pid:str,values:dict):return service.freeze_profile(pid,values['resource_set_id'],values.get('model_profile_id'))
 @app.post('/api/v1/runs/preflight')
 def preflight(values:dict):return service.preflight(values)
 @app.get('/api/v1/runs')
 def runs(client_request_id:str|None=None):return service.find_runs(client_request_id)
 @app.post('/api/v1/runs',status_code=202)
 def start(values:dict):return service.start_run(values['request'],values.get('credential_ref'))
 @app.get('/api/v1/runs/{rid}')
 def run(rid:str):return service.get_run(rid)
 @app.get('/api/v1/runs/{rid}/events')
 def events(rid:str,after_seq:int=0):return service.list_events(rid,after_seq)
 @app.post('/api/v1/runs/{rid}/control')
 def control(rid:str,values:dict):return service.control_run(rid,values['action'])
 @app.get('/api/v1/runs/{rid}/results')
 def results(rid:str,view:str='summary'):return service.get_results(rid,view)
 @app.get('/api/v1/runs/{rid}/recovery')
 def recovery(rid:str):return service.recovery_status(rid)
 @app.post('/api/v1/runs/{rid}/recovery')
 def reconcile(rid:str,values:dict):return service.resolve_attempt(rid,values)
 @app.get('/api/v1/runs/{rid}/reference')
 def reference(rid:str):return service.get_demo_reference(rid)
 @app.post('/api/v1/runs/{rid}/export')
 def export(rid:str):return service.export_course_bundle(rid)
 return app
