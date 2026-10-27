"""Explicit coursework scope: automatic checks are not human approval."""
COURSE='automatic_course'

def automatic(value):
 policy=value.get('review_policy','human_reviewed')
 if policy not in ('human_reviewed',COURSE):raise ValueError('Unknown review policy')
 return policy==COURSE

def readiness_valid(profile):
 required={'cohorts_verified','models_verified','resources_verified','human_reviews_complete'}
 value=profile.get('readiness',{})
 return set(value)==required and all(value[k] is True for k in required-{'human_reviews_complete'}) and value['human_reviews_complete'] is (False if automatic(profile) else True)

def course_assets(evidence,all_units,data,bindings,condition,*,_structure_validated=False):
 import base64
 from .provider import digest
 from .structure import project_structure
 normalized=condition.removeprefix('M2_')
 result={'verified':True,'human_reviewed':False,'evidence_sha':digest(evidence),'full_page_units':[u for u in all_units if u['doc_id'] in {e['doc_id'] for e in evidence}],'images':[],'structures':[]}
 if normalized in {'E3','I'}:
  for unit in evidence:
   binding=bindings.get(unit.get('image_path'))
   if not binding or binding['kind']!='input' or binding['sha256']!=unit.get('image_sha256'):raise ValueError('course_image_binding_missing')
   content=binding['absolute'].read_bytes();mime='image/png' if content.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if content.startswith(b'\xff\xd8') else None
   if not mime:raise ValueError('unsupported_crop_format')
   result['images'].append({'evidence_id':unit['evidence_id'],'url':'data:'+mime+';base64,'+base64.b64encode(content).decode()})
 if normalized in {'E4','L'}:
  records=data.get('draft_structures.jsonl',data.get('approved_structures.jsonl',[]));units={u['evidence_id']:u for u in all_units}
  if not _structure_validated:
   if len({r['evidence_id'] for r in records})!=len(records):raise ValueError('duplicate_structure')
   for r in records:
    if r['evidence_id'] not in units or r.get('evidence_sha')!=digest(units[r['evidence_id']]):raise ValueError('course_structure_identity_changed')
  if not {u['evidence_id'] for u in evidence}<={r['evidence_id'] for r in records}:raise ValueError('course_structure_missing')
  result['structures']=project_structure(records,[u['evidence_id'] for u in evidence])
 if normalized=='G':
  import json
  from src.ragpilot.evidence import descriptions
  scoped=[];index=[]
  for u in evidence:
   for block in u.get('blocks',[]):
    if ' '.join(block.get('text','').split()) not in ' '.join(u['text'].split()):raise ValueError('legacy_index_text_outside_evidence')
    index.append({'evidence_id':u['evidence_id'],'block_id':block['block_id'],'text':block['text']})
   scoped.append({**u,'content_hash':u.get('content_hash',digest(u))})
  result['legacy_geometry']=json.dumps({'index':index,'descriptions':[{'evidence_id':r['evidence_id'],'text':r['text']} for r in descriptions(scoped)]},sort_keys=True)
 return result


class CourseAssetBuilder:
 """One worker owns immutable loaded inputs; validate global structure identity once."""
 def __init__(self,units,data,bindings):
  self.units=units;self.data=data;self.bindings=bindings;self.structure_validated=False
 def __call__(self,evidence,condition):
  if condition.removeprefix('M2_') in {'E4','L'} and not self.structure_validated:
   course_assets([],self.units,self.data,self.bindings,'E4')
   self.structure_validated=True
  return course_assets(evidence,self.units,self.data,self.bindings,condition,_structure_validated=self.structure_validated)
