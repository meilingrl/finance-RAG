"""Durable, nonrefundable shared attempt reservations, protected across processes."""
import contextlib,fcntl,json,os,uuid
from pathlib import Path
from .recovery import atomic_bytes,append_plan,missing_suffix,_append_bytes

class BudgetError(ValueError): pass

@contextlib.contextmanager
def locked(path):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.with_name(path.name+'.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  recover_append(path)
  yield path

def recover_append(path):
 journal=path.with_name(path.name+'.pending')
 if not journal.exists():return
 try:remaining=missing_suffix(path,json.loads(journal.read_text()))
 except (ValueError,KeyError,TypeError):raise BudgetError('journal_content_conflict') from None
 if remaining:_append_bytes(path,remaining)
 journal.unlink()

def append_locked(path,record):
 journal=path.with_name(path.name+'.pending')
 atomic_bytes(journal,json.dumps(append_plan(path,(json.dumps(record,sort_keys=True)+'\n').encode())).encode())
 recover_append(path)

def append(path,record):
 with locked(path) as path:append_locked(path,record)

def records(path):
 """Read a committed journal logically without changing the on-disk snapshot."""
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.with_name(path.name+'.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_SH)
  data=path.read_bytes() if path.exists() else b'';journal=path.with_name(path.name+'.pending')
  if journal.exists():data+=missing_suffix(path,json.loads(journal.read_text()))
  return [json.loads(line) for line in data.splitlines() if line.strip()]

def peek_latest(path):return {row['attempt_id']:row for row in records(path)}

def latest(path):
 with locked(path) as path:
  return {row['attempt_id']:row for row in map(json.loads,path.read_text().splitlines())} if path.exists() else {}

class Budget:
 def __init__(self,path,*,caps=None,total=8000):
  self.path=Path(path);self.caps=caps or {'formal':6600,'development_validation':500,'retry_demo':900};self.total=total
 def reserve(self,run_id,task_id,request_hash,category):
  if category not in self.caps:raise BudgetError('invalid_budget_category')
  with locked(self.path) as path:
   try:rows={r['attempt_id']:r for r in map(json.loads,path.read_text().splitlines())} if path.exists() else {}
   except (ValueError,KeyError):raise BudgetError('budget_check_failed') from None
   if len(rows)>=self.total or sum(r['category']==category for r in rows.values())>=self.caps[category]:raise BudgetError('attempt_budget_exhausted')
   row=dict(attempt_id=str(uuid.uuid4()),run_id=run_id,task_id=task_id,request_hash=request_hash,state='reserved',category=category)
   append_locked(path,row);return row
 def transition(self,reservation,state):
  if state not in {'sent','completed','unknown'}:raise BudgetError('invalid_attempt_state')
  row={**reservation,'state':state};append(self.path,row);return row
