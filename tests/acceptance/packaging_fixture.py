"""Full synthetic packaging provenance fixture, never research evidence.

The provider dependency is replaced in-process; all tasks, AnswerRecords,
sealing, evaluation and audit adapters run normally. False synthetic wire flag
reflects the production transport interface exercised by this boundary fixture,
not a claim about actual data, providers or human participation.
"""
import json,hashlib,os
from unittest.mock import patch
from tests.formal.test_runner import production_fixture,SyntheticEncoder,fake_response
from formal_src.provider import digest,validate_response
from formal_src.audit import build_audit_plan
from formal_src.runner import run

def build(root):
 spec,rd,_=production_fixture(root)
 questions=[json.loads(l) for l in (root/'inference_questions.jsonl').read_text().splitlines()]
 tasks=spec['plan']['units']
 plan=build_audit_plan([{'question_id':q['question_id'],'source_id':q['source_id']} for q in questions if q['split']=='main'],[{'question_id':q['question_id'],'source_id':q['source_id']} for q in questions if q['split']=='diagnostic'],[{'evidence_id':f'synthetic-region-{i}','relation_ids':[]} for i in range(30)])
 plan['cohort_sha256']=digest(sorted({(t['partition'],t['question_id']) for t in tasks if t['partition'] in {'main','diagnostic'}}))
 (root/'audit_plan.json').write_text(json.dumps(plan))
 (root/'gold.jsonl').write_text(''.join(json.dumps({'question_id':q['question_id'],'source_id':q['source_id'],'answer':'3 years','scale':''})+'\n' for q in questions))
 reviews=[{'item_id':item['item_id'],'stage':'initial','reviewer_id':'synthetic-reviewer-1','verdict':'supported','reason':'Synthetic test record'} for item in plan['answer_items']+plan['relation_items']]
 reviews += [{'item_id':i,'stage':'second','reviewer_id':'synthetic-reviewer-2','verdict':'supported','reason':'Synthetic second test record'} for i in plan['second_review_ids']]
 (root/'audit_reviews.json').write_text(json.dumps(reviews))
 projection=json.loads((rd/'inputs.json').read_text())
 manifest={'resources':projection['resources']+[{'kind':kind,'path':name,'sha256':hashlib.sha256((root/name).read_bytes()).hexdigest()} for kind,name in [('review','audit_plan.json'),('evaluation','gold.jsonl')]]}
 spec['resource_sha']=digest(manifest)
 config=spec['resolved_config'];config['resource_manifest_sha']=spec['resource_sha'];config['audit_plan_sha256']=digest(plan);config['freeze_sha']=digest({**config,'freeze_sha':None});spec['freeze_sha']=config['freeze_sha']
 (rd/'spec.json').write_text(json.dumps(spec))
 (root/'resources').mkdir();(root/'resources/x.json').write_text(json.dumps({'root':str(root),'manifest':manifest,'audit_reviews':{'path':'audit_reviews.json','sha256':hashlib.sha256((root/'audit_reviews.json').read_bytes()).hexdigest(),'generation_manifest_sha256':spec['resource_sha']}}))
 def provider(config,payload,key,selected_ids,transport):
  return validate_response(fake_response(payload),config['model'],selected_ids,config['max_output_tokens'],config['reserve_input_tokens'])
 with patch.dict(os.environ,{'FORMAL_API_KEY':'synthetic-test-only'}),patch('formal_src.retrieval.LocalEncoder',SyntheticEncoder),patch('formal_src.runner.generate',provider):
  outcome=run(spec,lambda e:None,lambda:'continue',run_dir=rd)
 if outcome['state']!='completed':raise AssertionError(outcome)
 (rd/'state.json').write_text(json.dumps({'kind':'experiment','purpose':'formal','state':'completed'}))
 return spec,rd
