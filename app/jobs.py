"""Durable single-slot job persistence; workers never receive credentials in argv."""
import contextlib,hashlib,json,os,subprocess,threading,time
from . import fsutil
from pathlib import Path
from datetime import datetime,timezone
from uuid import uuid4
from collections import OrderedDict
from bisect import bisect_right
from formal_src.contracts import RunSnapshot,RunEvent,uuid

def now(): return datetime.now(timezone.utc).isoformat()
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def read(path,default=None): return json.loads(fsutil.read_text(path)) if path.exists() else default
def atomic(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name(path.name+'.'+str(uuid4())+'.tmp')
 with tmp.open('w',encoding='utf-8') as f: json.dump(value,f,allow_nan=False);f.flush();os.fsync(f.fileno())
 fsutil.replace(tmp,path)
 fsutil.sync_directory(path.parent)
class JobStore:
 def __init__(self,workspace):
  self.root=Path(workspace);self.root.mkdir(parents=True,exist_ok=True);self.runs=self.root/'runs';self.runs.mkdir(exist_ok=True);self._guard=threading.RLock();self._local=threading.local();self._event_cache=OrderedDict()
 @contextlib.contextmanager
 def lock(self):
  with self._guard:
   if getattr(self._local,'locked',False):
    yield
    return
   with (self.root/'jobs.lock').open('a') as f:
    fsutil.acquire(f);self._local.locked=True
    try: yield
    finally:
     self._local.locked=False;fsutil.release(f)
 def directory(self,run_id): uuid(run_id);return self.runs/run_id
 def get(self,run_id): return RunSnapshot.from_dict(read(self.directory(run_id)/'state.json'))
 def update(self,run_id,**values):
  with self.lock():
   d=self.get(run_id).to_dict();d.update(values,updated_at=now());d=RunSnapshot.from_dict(d);atomic(self.directory(run_id)/'state.json',d);return d
 def events(self,run_id,after_seq=0):
  if type(after_seq)!=int or after_seq<0: raise ValueError('Invalid event cursor')
  with self._guard:
   cached=self._event_history(run_id)
   return [dict(event) for event in cached['rows'][bisect_right(cached['seqs'],after_seq):]]
 def _event_history(self,run_id):
  # Only complete lines are committed. Cache offsets, not a new on-disk format.
  p=self.directory(run_id)/'events.jsonl'
  if not p.exists():
   self._event_cache.pop(run_id,None)
   return {'rows':[],'seqs':[],'offset':0,'size':0}
  with p.open('rb') as source:
   st=os.fstat(source.fileno());identity=(st.st_dev,st.st_ino)
   cached=self._event_cache.get(run_id)
   if cached is None or cached['identity']!=identity or st.st_size<cached['size'] or (st.st_size==cached['size'] and st.st_mtime_ns!=cached['mtime']):
    cached={'rows':[],'seqs':[],'offset':0,'size':0,'identity':identity}
   source.seek(cached['offset']);chunk=source.read(st.st_size-cached['offset']);end=chunk.rfind(b'\n')+1
   rows=[RunEvent.from_dict(json.loads(line)) for line in chunk[:end].splitlines() if line.strip()]
   previous=cached['seqs'][-1] if cached['seqs'] else 0
   for row in rows:
    if row['seq']<=previous:raise ValueError('Invalid event sequence')
    previous=row['seq']
   cached['rows'].extend(rows);cached['seqs'].extend(row['seq'] for row in rows)
   cached.update(offset=cached['offset']+end,size=st.st_size,mtime=st.st_mtime_ns)
  self._event_cache[run_id]=cached;self._event_cache.move_to_end(run_id)
  while len(self._event_cache)>4:self._event_cache.popitem(last=False)
  return cached
 def emit(self,run_id,event):
  with self.lock():
   s=self.get(run_id);history=self._event_history(run_id)
   if history['offset']!=history['size']:raise ValueError('Incomplete event journal')
   e=RunEvent.from_dict(dict(schema_version=1,run_id=run_id,seq=(history['seqs'][-1] if history['seqs'] else 0)+1,time=now(),phase=event.get('phase',s['phase']),event_type=event['event_type'],counts=event.get('counts',{}),error_code=event.get('error_code')))
   with (self.directory(run_id)/'events.jsonl').open('a') as f: f.write(json.dumps(e)+'\n');f.flush();os.fsync(f.fileno())
   counts={k:v for k,v in e['counts'].items() if k in s};self.update(run_id,last_event_seq=e['seq'],phase=e['phase'],**counts)
 def register(self,request,spec,blocking):
  with self.lock():
   for p in self.runs.glob('*/spec.json'):
    if read(p)['client_request_id']==request['client_request_id']: return self.get(p.parent.name),False
   active=[read(p) for p in self.runs.glob('*/state.json') if read(p)['state'] in ('queued','running','pause_requested','cancel_requested','validating')]
   if active: blocking=list(blocking)+['execution_slot_busy']
   rid=spec['run_id'];t=now();d=dict(schema_version=1,run_id=rid,kind=request['kind'],purpose=spec['purpose'],state='blocked' if blocking else 'queued',phase='prepare',profile_hash=digest(spec['resolved_config']),logical_generation_count=spec['logical_generation_count'],completed_logical_count=0,in_flight_count=0,attempt_count=0,failure_count=0,abstention_count=0,usage={'cost':None},last_event_seq=0,available_actions=['inspect'] if blocking else ['cancel'],created_at=t,updated_at=t,blocking_items=blocking,audit_complete=False)
   atomic(self.directory(rid)/'spec.json',spec);atomic(self.directory(rid)/'state.json',RunSnapshot.from_dict(d))
   atomic(self.directory(rid)/'integrity.json',{'spec_sha':digest(spec)})
   return self.get(rid),True
 def start(self,run_id,command,secret=None,*,expected_updated_at=None,inputs=None):
  directory=self.directory(run_id)
  with self.lock():
   current=self.get(run_id)
   if expected_updated_at is not None and current['updated_at']!=expected_updated_at:return current
   if current['state'] not in ('queued','blocked','paused','interrupted','failed'):return current
   previous=read(directory/'process.json',{})
   if self._alive(previous.get('pid'),previous.get('fingerprint')):return current
   for path in self.runs.glob('*/state.json'):
    other=read(path)
    if other['run_id']!=run_id and other['state'] in ('queued','running','pause_requested','cancel_requested','validating'):
     return self.update(run_id,state='blocked',blocking_items=['execution_slot_busy'])
   if inputs is not None:
    target=directory/'inputs.json'
    if target.exists() and digest(read(target))!=digest(inputs):return self.update(run_id,state='blocked',blocking_items=['inference_projection_changed'])
    if not target.exists():atomic(target,inputs)
   atomic(directory/'control.json',{'action':'continue'})
   env=os.environ.copy();env['PYTHONUTF8']='1'
   if secret: env['FORMAL_API_KEY']=secret
   else: env.pop('FORMAL_API_KEY',None)
   # A prior worker-result must never mark a resumed process complete.
   for name in ('worker-result.json','heartbeat.json'):
    (directory/name).unlink(missing_ok=True)
   consumed=len(self.events(run_id))
   try:
    process=subprocess.Popen(list(command)+['--run-dir',str(directory)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
   except OSError as exc:
    from formal_src.diagnostics import record_failure
    record_failure(directory,exc,'worker_start')
    return self.update(run_id,state='blocked',blocking_items=['kernel_start_failed'])
   atomic(directory/'process.json',{'pid':process.pid,'started_at':now(),'fingerprint':self._fingerprint(process.pid)})
   self.update(run_id,state='running',available_actions=['pause','cancel'],blocking_items=[])
  self._monitor(run_id,process.pid,process,consumed)
 def _monitor(self,run_id,pid,process=None,consumed=None):
  directory=self.directory(run_id);fingerprint=read(directory/'process.json',{}).get('fingerprint')
  def monitor():
   # Per-run monitor lock allows a restarted HTTP service to adopt its live worker.
   with (directory/'monitor.lock').open('a') as lock:
    try: fsutil.acquire(lock,blocking=False)
    except BlockingIOError:
     if process is None:return
     fsutil.acquire(lock)
    offset=len(self.events(run_id)) if consumed is None else consumed
    try:
     alive=True
     while True:
      p=directory/'worker-events.jsonl'
      raw=fsutil.read_text(p) if p.exists() else ''
      lines=raw.splitlines() if raw.endswith('\n') else raw.splitlines()[:-1]
      for line in lines[offset:]:self.emit(run_id,json.loads(line));offset+=1
      atomic(directory/'heartbeat.json',{'pid':pid,'time':now()})
      # After observing exit, drain once more: the worker may have appended its
      # final event between our previous read and poll(). No writer remains now.
      if not alive:break
      alive=process.poll() is None if process is not None else self._alive(pid,fingerprint)
      if alive:time.sleep(.03)
     result=read(directory/'worker-result.json',{})
     state=result.get('state','interrupted')
     if state not in ('completed','paused','cancelled','blocked','failed','interrupted'):state='failed'
     snapshot=self.get(run_id)
     if state in ('paused','cancelled','completed') and snapshot['in_flight_count']:state='interrupted'
     checks=read(directory/'integrity.json',{})
     checks['outputs']={name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in ('predictions.jsonl','tasks.jsonl','retrieval.jsonl','summary.json','results.csv','attempts.jsonl','inputs.json') if (directory/name).is_file()}
     atomic(directory/'integrity.json',checks)
     self.update(run_id,state=state,available_actions=['resume','inspect'] if state in ('paused','interrupted','blocked','failed') else ['inspect'],blocking_items=result.get('blocking_items',[]))
    except Exception as exc:
     from formal_src.diagnostics import record_failure
     record_failure(directory,exc,'worker_monitor')
     self.update(run_id,state='failed',blocking_items=['invalid_worker_output'])
  threading.Thread(target=monitor,daemon=True).start()
 @staticmethod
 def _windows_fingerprint(pid):
  # One handle binds liveness and creation time to the same process instance.
  import ctypes
  from ctypes import wintypes
  kernel=ctypes.WinDLL('kernel32',use_last_error=True)
  kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
  kernel.OpenProcess.restype=wintypes.HANDLE
  kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
  kernel.WaitForSingleObject.restype=wintypes.DWORD
  kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
  kernel.GetProcessTimes.restype=wintypes.BOOL
  kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
  handle=kernel.OpenProcess(0x1000|0x100000,False,int(pid))
  if not handle:return None
  try:
   created,exited,system,user=(wintypes.FILETIME() for _ in range(4))
   if not kernel.GetProcessTimes(handle,ctypes.byref(created),ctypes.byref(exited),ctypes.byref(system),ctypes.byref(user)):return None
   if kernel.WaitForSingleObject(handle,0)!=258:return None
   return f'windows:{pid}:{(created.dwHighDateTime<<32)|created.dwLowDateTime}'
  finally:kernel.CloseHandle(handle)
 @staticmethod
 def _fingerprint(pid):
  if not pid:return None
  if os.name=='nt':return JobStore._windows_fingerprint(pid)
  try:
   value=subprocess.check_output(['ps','-p',str(pid),'-o','lstart='],stderr=subprocess.DEVNULL,timeout=2).strip()
   return hashlib.sha256(value).hexdigest() if value else None
  except (subprocess.SubprocessError,OSError):return None
 @staticmethod
 def _alive(pid,fingerprint=None):
  try:
   if not pid:return False
   if os.name=='nt':
    current=JobStore._windows_fingerprint(pid)
    return current is not None and (fingerprint is None or current==fingerprint)
   os.kill(pid,0);return fingerprint is None or JobStore._fingerprint(pid)==fingerprint
  except ProcessLookupError:return False
 def recover(self):
  for p in self.runs.glob('*/state.json'):
   s=read(p)
   if s['state'] not in ('running','queued','pause_requested','cancel_requested','validating'):continue
   proc=read(p.parent/'process.json',{});pid=proc.get('pid')
   # A worker may finish while the service is down; replay its durable result.
   if self._alive(pid,proc.get('fingerprint')) or (p.parent/'worker-result.json').is_file():self._monitor(s['run_id'],pid)
   else:self.update(s['run_id'],state='interrupted',available_actions=['resume','inspect'])
 def resume_checks(self,run_id):
  d=self.directory(run_id);spec=read(d/'spec.json');checks=read(d/'integrity.json',{})
  if digest(spec)!=checks.get('spec_sha'):return ['saved_spec_changed']
  from formal_src.budget import peek_latest
  try:latest=peek_latest(d/'attempts.jsonl')
  except ValueError:return ['attempt_ledger_invalid']
  from formal_src.recovery import receipt_for
  try:
   if any(r.get('state') in ('sent','unknown') and not receipt_for(d,r) for r in latest.values()):return ['unknown_attempt_requires_inspection']
  except ValueError:return ['recovery_identity_changed']
  for path,sha in checks.get('outputs',{}).items():
   target=(d/path).resolve()
   if not target.is_relative_to(d.resolve()) or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=sha:return ['saved_output_changed']
  return []
