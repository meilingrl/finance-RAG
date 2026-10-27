"""Content-addressed local registration; never modifies registered assets."""
import hashlib,json
from pathlib import Path
from formal_src.contracts import ResourceReport,identifier,relative,safe_json,timestamp
from .jobs import atomic,read,digest
class ResourceRegistry:
 def __init__(self,workspace):self.root=Path(workspace)/'resources';self.root.mkdir(parents=True,exist_ok=True)
 def register(self,root_path,review_policy=None):
  root=Path(root_path).resolve()
  if not root.is_dir():raise ValueError('Resource directory missing')
  if (root/'resources.json').exists():m=safe_json(read(root/'resources.json'))
  elif root.name=='nonllm-pilot-v5' and (root/'data_manifest.json').exists():
   m=dict(schema_version=1,resource_set_id='legacy-demo-'+hashlib.sha256(str(root).encode()).hexdigest()[:12],purpose='demo',qualification={},resources=[dict(kind='input',path=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in root.glob('*.json*') if p.is_file() and p.name in ('inference_questions.jsonl','evidence_units.jsonl')])
  else:raise ValueError('resources.json required')
  if review_policy is not None:
   if review_policy!='automatic_course' or m['purpose']!='formal':raise ValueError('Invalid coursework resource policy')
   m={**m,'resource_set_id':m['resource_set_id']+'-course','review_policy':'automatic_course'}
  identifier(m['resource_set_id'])
  if m.get('schema_version')!=1 or m.get('purpose') not in ('demo','formal') or not isinstance(m.get('resources'),list):raise ValueError('Invalid resource manifest')
  for r in m['resources']:
   relative(r['path'])
   if not (root/r['path']).resolve().is_relative_to(root):raise ValueError('Resource path escapes root')
   if r.get('kind') not in ('input','model','pdf','ocr','evaluation','review','legacy','approved_description'):raise ValueError('Invalid resource kind')
   if len(r.get('sha256',''))!=64:raise ValueError('Invalid resource SHA')
  previous=self.record(m['resource_set_id'])
  record={'root':str(root),'manifest':m}
  if previous and previous['root']==str(root) and previous['manifest']==m:
   for key in ('reviews','audit_reviews'):
    if key in previous:record[key]=previous[key]
  bundled=None
  if m.get('review_import'):
   if m['purpose']!='demo':raise ValueError('Bundled AI reviews are for demo packages only')
   relative(m['review_import']);path=(root/m['review_import']).resolve()
   matches=[r for r in m['resources'] if r['kind']=='review' and r['path']==m['review_import']]
   if len(matches)!=1 or not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=matches[0]['sha256']:raise ValueError('Bundled review binding invalid')
   bundled=read(path)
  atomic(self.root/(m['resource_set_id']+'.json'),record)
  if bundled is not None:return self.import_reviews(m['resource_set_id'],bundled)
  return self.check(m['resource_set_id'])
 def record(self,rid):identifier(rid);r=read(self.root/(rid+'.json'));return r
 def check(self,rid):
  record=self.record(rid)
  if not record: return ResourceReport.from_dict(dict(schema_version=1,resource_set_id=rid,resources=[],ready_for=[],blocking_items=['resources_missing']))
  from formal_src.course_policy import automatic
  m=record['manifest'];course=automatic(m);root=Path(record['root']);items=[];blocks=[]
  for r in m['resources']:
   p=(root/r['path']).resolve();actual=hashlib.sha256(p.read_bytes()).hexdigest() if p.is_relative_to(root) and p.is_file() else None
   status='ready' if actual==r['sha256'] else 'missing' if actual is None else 'sha_mismatch'
   items.append(dict(r,registered_root=str(root),actual_sha256=actual,status=status))
   if status!='ready':blocks.append('resource_'+status)
  kinds={r['kind'] for r in items if r['status']=='ready'}
  if 'input' not in kinds:blocks.append('input_missing')
  ready=['demo','prepare'] if not blocks else []
  if m['purpose']=='formal':
   if 'model' not in kinds:blocks.append('model_missing')
   if not course and not record.get('reviews'):blocks.append('reviews_missing')
   if course:
    from .course_readiness import qualify_course_resources
    checks=qualify_course_resources(record,verified_resources=items)
    blocks+=checks['blocking_items']
   else:
    q=m.get('qualification',{})
    for k in ('source_disjoint','cohorts_validated','inputs_complete','development_passed','validation_passed','compatibility_passed','throughput_passed','budget_validated'):
     if q.get(k) is not True:blocks.append('qualification_'+k)
    # Human-mode qualification attestations remain bound to evidence files.
    evidence=q.get('evidence_sha256')
    if not evidence or not any(i['sha256']==evidence and i['status']=='ready' for i in items):blocks.append('qualification_evidence_missing')
   if not blocks:ready+=['experiment','freeze']
  else:blocks.append('demo_only_resources')
  evaluation=[item for item in items if item['kind']=='evaluation']
  if evaluation and all(item['status']=='ready' for item in evaluation):ready.append('verify')
  return ResourceReport.from_dict(dict(schema_version=1,resource_set_id=rid,resources=items,ready_for=ready,blocking_items=sorted(set(blocks))))
 def import_reviews(self,rid,manifest):
  m=safe_json(manifest);r=self.record(rid)
  if not r:raise ValueError('Resource set missing')
  if 'audit_reviews' in m:
   binding=m['audit_reviews'];relative(binding['path'])
   path=(Path(r['root'])/binding['path']).resolve()
   if (binding.get('generation_manifest_sha256')!=digest(r['manifest']) or path.name!='audit_reviews.json'
       or not path.is_relative_to(Path(r['root']).resolve()) or not path.is_file()
       or hashlib.sha256(path.read_bytes()).hexdigest()!=binding.get('sha256')):raise ValueError('Invalid post-generation audit binding')
   r['audit_reviews']=binding;atomic(self.root/(rid+'.json'),r);return self.check(rid)
  reviews=m.get('reviews',[])
  if not reviews:raise ValueError('Review records required')
  known={i['sha256'] for i in r['manifest']['resources']}
  for v in reviews:
   if r['manifest']['purpose']=='formal' and v.get('reviewer_kind')=='ai':raise ValueError('AI demo review cannot approve formal materials')
   timestamp(v.get('reviewed_at'))
   if not v.get('reviewer') or not v.get('reviewed_at') or v.get('asset_sha256') not in known or v.get('approved') is not True or v.get('qa_exposed') is not False:raise ValueError('Invalid blind review record')
  r['reviews']=m;atomic(self.root/(rid+'.json'),r);return self.check(rid)

 def inference_projection(self,rid):
  record=self.record(rid)
  if not record:raise ValueError('Resource set missing')
  report=self.check(rid)
  resources=[]
  approved={r['asset_sha256'] for r in record.get('reviews',{}).get('reviews',[]) if r.get('approved') is True and r.get('qa_exposed') is False}
  for item in report['resources']:
   if item['kind'] not in ('input','model','approved_description'):continue
   if item['status']!='ready':raise ValueError('Inference resource changed')
   if item['kind']=='approved_description' and item['sha256'] not in approved and record['manifest'].get('review_policy')!='automatic_course':raise ValueError('Description lacks blind approval')
   resources.append({k:item[k] for k in ('kind','path','sha256')})
  return dict(schema_version=1,resource_set_id=rid,root=record['root'],resources=resources,**({'review_policy':'automatic_course'} if record['manifest'].get('review_policy')=='automatic_course' else {}))
