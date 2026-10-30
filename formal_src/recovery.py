"""Local recovery journals. Never query a provider or erase attempt history."""
import base64,hashlib,json,os
from pathlib import Path
from uuid import uuid4
from datetime import datetime,timezone
from app import fsutil


def atomic_bytes(path,data):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name(path.name+'.'+str(uuid4())+'.tmp')
 with tmp.open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
 fsutil.replace(tmp,path)
 fsutil.sync_directory(path.parent)


def _bytes(value):return json.dumps(value,sort_keys=True,allow_nan=False).encode()
def _digest(value):return hashlib.sha256(_bytes(value)).hexdigest()
def _json(path):return json.loads(Path(path).read_text())
def _rows(path):return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
def _key(attempt):return {k:attempt[k] for k in ('attempt_id','run_id','task_id','request_hash','category')}
def _identity(rd):return {name:hashlib.sha256((rd/name).read_bytes()).hexdigest() for name in ('spec.json','inputs.json')}
def _path(rd,kind,aid):return rd/'recovery'/kind/(hashlib.sha256(aid.encode()).hexdigest()+'.json')


def _append_bytes(path,data):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('ab') as f:f.write(data);f.flush();os.fsync(f.fileno())


def append_plan(path,data):
 before=path.read_bytes() if path.exists() else b''
 return {'size':len(before),'sha':hashlib.sha256(before).hexdigest(),'append':base64.b64encode(data).decode(),'append_sha':hashlib.sha256(data).hexdigest()}


def missing_suffix(path,plan):
 current=path.read_bytes() if path.exists() else b'';size=plan['size'];addition=base64.b64decode(plan['append'],validate=True)
 if hashlib.sha256(addition).hexdigest()!=plan['append_sha']:raise ValueError('journal_checksum_mismatch')
 if len(current)<size or hashlib.sha256(current[:size]).hexdigest()!=plan['sha'] or not addition.startswith(current[size:]):raise ValueError('journal_content_conflict')
 return addition[len(current)-size:]


def repair_prediction(rd):
 rd=Path(rd);journal=rd/'prediction-transaction.json'
 if not journal.exists():return
 txn=_json(journal)
 if set(txn)!= {'predictions.jsonl','prediction-checksums.jsonl'}:raise ValueError('prediction_transaction_invalid')
 missing={name:missing_suffix(rd/name,plan) for name,plan in txn.items()}
 for name,data in missing.items():
  if data:_append_bytes(rd/name,data)
 journal.unlink()


def commit_prediction(rd,record,checksum):
 rd=Path(rd);repair_prediction(rd);txn={}
 for name,row in [('predictions.jsonl',record),('prediction-checksums.jsonl',{'task_id':record['task_id'],'sha256':checksum})]:
  txn[name]=append_plan(rd/name,_bytes(row)+b'\n')
 atomic_bytes(rd/'prediction-transaction.json',_bytes(txn));repair_prediction(rd)


def _save_record(path,value):
 atomic_bytes(path,_bytes({'payload':value,'sha256':_digest(value)}))

def _read_record(path):
 envelope=_json(path);value=envelope['payload']
 if envelope['sha256']!=_digest(value):raise ValueError('recovery_checksum_mismatch')
 return value

def prediction_rows(rd):
 path=rd/'predictions.jsonl';data=path.read_bytes() if path.exists() else b''
 journal=rd/'prediction-transaction.json'
 if journal.exists():data+=missing_suffix(path,_json(journal)['predictions.jsonl'])
 return [json.loads(line) for line in data.splitlines() if line.strip()]

def save_receipt(rd,attempt,parsed,error):
 rd=Path(rd)
 value={'identity':_identity(rd),'attempt':dict(attempt),'parsed':parsed,'error':error,'kind':'response'}
 _save_record(_path(rd,'receipts',attempt['attempt_id']),value)


def receipt_for(rd,attempt):
 rd=Path(rd)
 for kind in ('receipts','decisions'):
  path=_path(rd,kind,attempt['attempt_id'])
  if not path.exists():continue
  value=_read_record(path)
  if value['identity']!=_identity(rd) or _key(value['attempt'])!=_key(attempt):raise ValueError('recovery_identity_changed')
  from .budget import records
  if value['attempt'] not in records(rd/'attempts.jsonl'):raise ValueError('recovery_attempt_changed')
  return value
 return None


def issues(rd):
 from .budget import peek_latest as latest
 rd=Path(rd);done={r['task_id'] for r in prediction_rows(rd)};pending=latest(rd/'attempts.jsonl');items=[]
 for r in pending.values():
  if r['state'] in ('sent','unknown') or r['state']=='completed' and r.get('task_id') not in done:
   saved=receipt_for(rd,r)
   items.append({'attempt_id':r['attempt_id'],'task_id':r.get('task_id'),'state':r['state'],'recoverable':bool(saved),'request_hash':r.get('request_hash')})
 return items


def resolve_failure(rd,attempt_id,note,confirmed):
 from .budget import peek_latest as latest
 rd=Path(rd)
 if confirmed is not True or not isinstance(note,str) or not 10<=len(note.strip())<=2000:raise ValueError('recovery_confirmation_required')
 if (rd/'sealed.json').exists():raise ValueError('sealed_run_immutable')
 r=latest(rd/'attempts.jsonl').get(attempt_id)
 if not r or not any(i['attempt_id']==attempt_id for i in issues(rd)):raise ValueError('recovery_attempt_not_pending')
 if receipt_for(rd,r):raise ValueError('saved_response_available_resume_instead')
 value={'identity':_identity(rd),'attempt':r,'parsed':{'status':'failed','parsed_answer':None,'scale':'','citations':[],'usage':{'cost_usd':None}},'error':'recovery_unconfirmed_failure','kind':'operator_failure','note':note.strip(),'time':datetime.now(timezone.utc).isoformat()}
 _save_record(_path(rd,'decisions',attempt_id),value)
 return {'recorded':True,'attempt_id':attempt_id,'action':'count_as_failed_without_resend'}


def settle_receipts(rd):
 from .budget import latest,append
 rd=Path(rd)
 for r in latest(rd/'attempts.jsonl').values():
  if r['state'] not in ('sent','unknown'):continue
  value=receipt_for(rd,r)
  if not value:continue
  parsed=value['parsed'];row={**r,'state':'completed','error_code':value['error'],'response_id':(parsed or {}).get('response_id'),'usage':(parsed or {}).get('usage'),'parse_status':(parsed or {}).get('status','failed'),'recovery':value['kind']}
  append(rd.parent.parent/'budget.jsonl',row);append(rd/'attempts.jsonl',row)


def restored_result(rd,attempts,task_id,request_hash):
 related=[r for r in attempts.values() if r.get('task_id')==task_id and r['state']!='reserved']
 if not related:return None
 r=related[-1]
 if r['request_hash']!=request_hash:raise ValueError('recovery_request_changed')
 value=receipt_for(Path(rd),r)
 if not value:return None
 parsed=value['parsed'] if not value['error'] else None
 if not parsed:parsed={'status':'failed','parsed_answer':None,'scale':'','citations':[],'usage':{'cost_usd':None}}
 result={'status':{'rejection':'refused'}.get(parsed['status'],parsed['status']),'answer':parsed['parsed_answer'],'scale':parsed.get('scale') or '','citations':parsed['citations']}
 return result,[r['attempt_id'] for r in related],{**parsed['usage'],'cost_usd':None},0
