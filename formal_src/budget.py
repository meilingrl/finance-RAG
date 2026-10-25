"""Durable, nonrefundable shared attempt reservations, protected across processes."""
import fcntl,json,os,uuid
from pathlib import Path

class BudgetError(ValueError): pass

def append(path,record):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('a') as f:
  fcntl.flock(f,fcntl.LOCK_EX); f.write(json.dumps(record,sort_keys=True)+'\n');f.flush();os.fsync(f.fileno())

def latest(path):
 path=Path(path)
 if not path.exists():return {}
 records={}
 for line in path.read_text().splitlines():
  row=json.loads(line);records[row['attempt_id']]=row
 return records

class Budget:
 def __init__(self,path,*,caps=None,total=8000):
  self.path=Path(path);self.caps=caps or {'formal':6600,'development_validation':500,'retry_demo':900};self.total=total
 def reserve(self,run_id,task_id,request_hash,category):
  if category not in self.caps:raise BudgetError('invalid_budget_category')
  self.path.parent.mkdir(parents=True,exist_ok=True)
  with self.path.open('a+') as f:
   fcntl.flock(f,fcntl.LOCK_EX);f.seek(0)
   try:rows={r['attempt_id']:r for r in map(json.loads,f)}
   except (ValueError,KeyError):raise BudgetError('budget_check_failed') from None
   if len(rows)>=self.total or sum(r['category']==category for r in rows.values())>=self.caps[category]:raise BudgetError('attempt_budget_exhausted')
   row=dict(attempt_id=str(uuid.uuid4()),run_id=run_id,task_id=task_id,request_hash=request_hash,state='reserved',category=category)
   f.seek(0,2);f.write(json.dumps(row,sort_keys=True)+'\n');f.flush();os.fsync(f.fileno());return row
 def transition(self,reservation,state):
  if state not in {'sent','completed','unknown'}:raise BudgetError('invalid_attempt_state')
  row={**reservation,'state':state};append(self.path,row);return row
