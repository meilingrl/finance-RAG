import unittest,tempfile,json
from pathlib import Path
class RunnerTests(unittest.TestCase):
 def test_fatal_provider_stops_full_plan_and_never_seals(self):
  from formal_src.runner import run
  from formal_src.provider import ProviderError
  from scripts.llm_execution import APIError
  from unittest.mock import patch
  for concurrency in (1,4):
   for code in ('http_401','http_403','http_400','http_404','model_mismatch','usage_invalid'):
    with self.subTest(concurrency=concurrency,code=code),tempfile.TemporaryDirectory() as d:
     spec,rd=full_fixture(Path(d));spec['resolved_config']['concurrency']=concurrency
     (rd/'spec.json').write_text(json.dumps(spec));calls=[]
     def transport(*args):
      calls.append(1)
      if code.startswith('http_'):raise APIError(code)
      raise ProviderError(code)
     with patch('formal_src.retrieval.LocalEncoder',SyntheticEncoder):
      result=run(spec,lambda e:None,lambda:'continue',transport=transport,run_dir=rd)
     self.assertEqual(result['state'],'blocked',result)
     self.assertIn(code,result['blocking_items']);self.assertLessEqual(len(calls),concurrency)
     self.assertFalse((rd/'sealed.json').exists());self.assertFalse((rd/'results.json').exists())
     rows=[json.loads(l) for l in (rd/'predictions.jsonl').read_text().splitlines()]
     self.assertTrue(any(r['status']=='failed' for r in rows))
     again=run(spec,lambda e:None,lambda:'continue',transport=lambda *a:self.fail('fatal resume sent'),run_dir=rd)
     self.assertEqual(again['state'],'blocked')
 def test_fatal_observation_drains_already_sent_peers_without_new_dispatch(self):
  import threading
  from unittest.mock import patch
  from formal_src.runner import run
  from formal_src.provider import APIError
  with tempfile.TemporaryDirectory() as d:
   spec,rd=full_fixture(Path(d));spec['resolved_config']['concurrency']=4
   (rd/'spec.json').write_text(json.dumps(spec));barrier=threading.Barrier(3);observed=threading.Event();calls=[];lock=threading.Lock()
   def transport(endpoint,payload,key,timeout):
    with lock:
     self.assertFalse(observed.is_set(),'fresh dispatch after fatal observation');calls.append(payload);number=len(calls)
    barrier.wait(5)
    if number==1:raise APIError('http_401')
    self.assertTrue(observed.wait(5));return fake_response(payload)
   def event(value):
    if value['event_type']=='attempt_finished':observed.set()
   with patch('formal_src.retrieval.LocalEncoder',SyntheticEncoder):
    result=run(spec,event,lambda:'continue',transport=transport,run_dir=rd)
   self.assertEqual(result['state'],'blocked');self.assertEqual(len(calls),3)
   rows=[json.loads(l) for l in (rd/'predictions.jsonl').read_text().splitlines()]
   self.assertEqual(sum(r['provenance']=='live' for r in rows),3)
   self.assertFalse((rd/'sealed.json').exists())
 def test_runner_exists(self):
  import importlib.util
  self.assertIsNotNone(importlib.util.find_spec('formal_src.runner'))
 def test_budget_cap_and_unknown(self):
  from formal_src.budget import Budget, BudgetError
  with tempfile.TemporaryDirectory() as d:
   b=Budget(Path(d)/'budget.jsonl',caps={'formal':1,'development_validation':1,'retry_demo':1},total=1)
   a=b.reserve('r','t','0'*64,'formal'); b.transition(a,'sent')
   with self.assertRaises(BudgetError): b.reserve('r','u','0'*64,'retry_demo')
   b.transition(a,'unknown')
   self.assertEqual(json.loads((Path(d)/'budget.jsonl').read_text().splitlines()[-1])['state'],'unknown')
 def test_demo_seals_and_resume_never_resends(self):
  from formal_src.runner import run
  from uuid import uuid4
  from formal_src.provider import digest
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);rd=root/'runs'/str(uuid4());rd.mkdir(parents=True)
   qs=[{'question_id':'q','doc_id':'doc','question':'How long?','split':'development'}];us=[{'evidence_id':'e','doc_id':'doc','text':'compensation expensed over 3 years'}]
   resources=[]
   import hashlib
   for name,rows in [('inference_questions.jsonl',qs),('evidence_units.jsonl',us)]:
    p=root/name;p.write_text(''.join(json.dumps(r)+'\n' for r in rows));resources.append({'kind':'input','path':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
   (rd/'inputs.json').write_text(json.dumps({'root':str(root),'resources':resources}))
   spec={'schema_version':1,'client_request_id':str(uuid4()),'run_id':rd.name,'kind':'demo','purpose':'software_test','profile_id':'custom-demo','resource_set_id':'x','source_run_id':None,'demo_input':{'doc_id':'doc','question':'How long?','retriever':'bm25','groups':['E1','E2']},'resolved_config':{'models':{'primary':'gpt-6.1-sol'},'provider':{'endpoint':'https://example.com/v1/responses','max_output_tokens':100,'reserve_input_tokens':10000}},'code_sha':'0'*64,'resource_sha':'0'*64,'freeze_sha':None,'plan':{},'logical_generation_count':0,'output_relative_path':'runs/x'}
   (rd/'spec.json').write_text(json.dumps(spec));calls=[]
   def transport(endpoint,payload,key,timeout):
    calls.append(payload);return {'id':'response-test','model':payload['model'],'status':'completed','usage':{'input_tokens':10,'output_tokens':20},'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':json.dumps({'answer':'3 years','scale':'','citations':['e'],'abstained':False})}]}]}
   result=run(spec,lambda e:None,{'run_dir':str(rd)},transport=transport)
   self.assertEqual(result['state'],'completed');self.assertEqual(len(calls),1);self.assertTrue((rd/'sealed.json').exists())
   run(spec,lambda e:None,{'run_dir':str(rd)},transport=transport);self.assertEqual(len(calls),1)
 def test_payload_conditions_require_qualified_assets(self):
  from formal_src.runner import condition_content,RunnerBlocked
  units=[{'evidence_id':'e','text':'SECRET_OCR'}]
  for c in ['E3','E4','L','G','I','C1','M2_E3','M2_E4']:
   with self.subTest(c=c),self.assertRaises(RunnerBlocked):condition_content(c,'q',units,{})
 def test_8001st_reservation_is_rejected(self):
  from formal_src.budget import Budget,BudgetError
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'budget.jsonl'
   p.write_text(''.join(json.dumps({'attempt_id':str(i),'category':'formal' if i<6600 else 'development_validation' if i<7100 else 'retry_demo','state':'completed'})+'\n' for i in range(8000)))
   with self.assertRaises(BudgetError):Budget(p).reserve('r','t','0'*64,'retry_demo')
   self.assertEqual(len(p.read_text().splitlines()),8000)
 def test_unknown_sent_blocks_even_without_inputs(self):
  from formal_src.runner import run
  from uuid import uuid4
  with tempfile.TemporaryDirectory() as d:
   rd=Path(d);(rd/'attempts.jsonl').write_text(json.dumps({'attempt_id':'a','state':'sent'})+'\n')
   spec={'schema_version':1,'client_request_id':str(uuid4()),'run_id':str(uuid4()),'kind':'prepare','purpose':'software_test','profile_id':'custom','resource_set_id':'x','source_run_id':None,'demo_input':None,'resolved_config':{},'code_sha':'0'*64,'resource_sha':'0'*64,'freeze_sha':None,'plan':{},'logical_generation_count':0,'output_relative_path':'runs/x'}
   result=run(spec,lambda e:None,{'run_dir':d},transport=lambda *a:self.fail('sent again'))
   self.assertEqual(result['blocking_items'],['unknown_attempt_requires_inspection']);self.assertFalse((rd/'sealed.json').exists())
 def test_process_contention_never_exceeds_cap(self):
  import subprocess,sys
  from formal_src.budget import latest
  with tempfile.TemporaryDirectory() as d:
   path=str(Path(d)/'budget.jsonl')
   script="from formal_src.budget import Budget,BudgetError; import sys\ntry: Budget(sys.argv[1],caps={'formal':1},total=1).reserve('r','t','0'*64,'formal')\nexcept BudgetError:pass\n"
   procs=[subprocess.Popen([sys.executable,'-c',script,path]) for _ in range(4)]
   for p in procs:self.assertEqual(p.wait(),0)
   self.assertEqual(len(latest(path)),1)
 def test_all_pure_condition_payloads_and_no_ocr_leak(self):
  from formal_src.runner import condition_content
  from formal_src.provider import digest
  units=[{'evidence_id':'e','doc_id':'doc','text':'OCR_ONLY 3 years'}]
  assets={'verified':True,'evidence_sha':digest(units),'images':[{'evidence_id':'e','url':'data:image/png;base64,AA=='}],'structures':[{'evidence_id':'e','evidence_sha':digest(units[0]),'nodes':[{'node_id':'N1.C1','text':'3 years'}],'relations':[]}],'legacy_geometry':'above N1','full_page_units':units}
  for c in ['E2','E2_k1','E2_k4','E2_no_association','M2_E2','E3','M2_E3','E4','M2_E4','D','L','G','I','C0','C1']:
   with self.subTest(c=c):
    content=condition_content(c,'QUESTION_ONCE',units,assets)
    if content is not None:
     text=json.dumps(content);self.assertEqual(text.count('QUESTION_ONCE'),1)
     if c in {'C0','I'}:self.assertNotIn('OCR_ONLY',text)
     if c=='D':self.assertEqual(text.count('OCR_ONLY'),2)
     if c=='L':self.assertNotIn('relations',text)
 def test_structure_rejects_text_outside_evidence(self):
  from formal_src.runner import condition_content,RunnerBlocked
  from formal_src.provider import digest
  units=[{'evidence_id':'e','text':'three'}]
  assets={'verified':True,'evidence_sha':digest(units),'structures':[{'evidence_id':'e','evidence_sha':digest(units[0]),'nodes':[{'node_id':'n','text':'invented answer'}],'relations':[]}]}
  with self.assertRaises(RunnerBlocked):condition_content('E4','q',units,assets)

 def test_run_ownership_prevents_duplicate_transport(self):
  import multiprocessing
  from formal_src.runner import run
  ctx=multiprocessing.get_context('fork')
  with tempfile.TemporaryDirectory() as d:
   spec,rd=demo_fixture(Path(d));entered=ctx.Event();release=ctx.Event();results=ctx.Queue()
   def execute():
    def transport(endpoint,payload,key,timeout):
     with (rd/'external-calls.jsonl').open('a') as f:f.write('sent\n')
     return fake_response(payload)
    import formal_src.runner as kernel
    from unittest.mock import patch
    original=kernel.retrieve
    def held_retrieval(*args,**kwargs):
     entered.set();release.wait(5);return original(*args,**kwargs)
    with patch.object(kernel,'retrieve',held_retrieval):
     results.put(run(spec,lambda e:None,{'run_dir':str(rd)},transport=transport))
   first=ctx.Process(target=execute);second=ctx.Process(target=execute)
   first.start();self.assertTrue(entered.wait(5));second.start();second.join(2)
   try:
    self.assertFalse(second.is_alive(),'losing worker must return immediately')
    loser=results.get(timeout=2);self.assertEqual(loser,{'state':'blocked','blocking_items':['run_already_running']})
    self.assertFalse((rd/'worker-result.json').exists(),'loser must not write owner result')
    import subprocess,sys
    cli=subprocess.run([sys.executable,'scripts/run_formal.py','--run-dir',str(rd)],capture_output=True,timeout=3)
    self.assertEqual(cli.returncode,0)
    self.assertFalse((rd/'worker-result.json').exists(),'losing CLI must not write owner result')
   finally:
    release.set();first.join(5);second.join(5)
   self.assertEqual(results.get(timeout=2)['state'],'completed')
   self.assertEqual((rd/'external-calls.jsonl').read_text().splitlines(),['sent'])
   self.assertEqual(json.loads((rd/'worker-result.json').read_text())['state'],'completed')

 def test_callable_controls_and_explicit_directory(self):
  from formal_src.runner import run
  for action,state in [('pause','paused'),('cancel','cancelled')]:
   with self.subTest(action=action),tempfile.TemporaryDirectory() as d:
    spec,rd=demo_fixture(Path(d));calls=[]
    result=run(spec,lambda e:None,lambda:action,transport=lambda *a:calls.append(a),run_dir=rd)
    self.assertEqual(result['state'],state);self.assertEqual(calls,[])
  with tempfile.TemporaryDirectory() as d:
   spec,rd=demo_fixture(Path(d));actions=iter(['continue','pause']);calls=[]
   result=run(spec,lambda e:None,lambda:next(actions),transport=lambda *a:calls.append(a),run_dir=rd)
   self.assertEqual(result['state'],'paused');self.assertEqual(calls,[])
   self.assertEqual(len((rd/'predictions.jsonl').read_text().splitlines()),1)

def fake_response(payload):
 return {'id':'response-test','model':payload['model'],'status':'completed','usage':{'input_tokens':10,'output_tokens':20},'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':json.dumps({'answer':'3 years','scale':'','citations':['e'],'abstained':False})}]}]}

def demo_fixture(root):
 from uuid import uuid4
 import hashlib
 rd=root/'runs'/str(uuid4());rd.mkdir(parents=True);resources=[]
 for name,data in [('inference_questions.jsonl',{'question_id':'q','doc_id':'doc','question':'How long?','split':'development'}),('evidence_units.jsonl',{'evidence_id':'e','doc_id':'doc','text':'compensation expensed over 3 years'})]:
  path=root/name;path.write_text(json.dumps(data)+'\n');resources.append({'kind':'input','path':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
 (rd/'inputs.json').write_text(json.dumps({'root':str(root),'resources':resources}))
 spec={'schema_version':1,'client_request_id':str(uuid4()),'run_id':rd.name,'kind':'demo','purpose':'software_test','profile_id':'custom-demo','resource_set_id':'x','source_run_id':None,'demo_input':{'doc_id':'doc','question':'How long?','retriever':'bm25','groups':['E1','E2']},'resolved_config':{'models':{'primary':'gpt-6.1-sol'},'provider':{'endpoint':'https://example.com/v1/responses','max_output_tokens':100,'reserve_input_tokens':10000}},'code_sha':'0'*64,'resource_sha':'0'*64,'freeze_sha':None,'plan':{},'logical_generation_count':0,'output_relative_path':'runs/x'}
 (rd/'spec.json').write_text(json.dumps(spec));return spec,rd

class FormalExtensionTests(unittest.TestCase):
 def test_http_retry_counts_visible_before_terminal_failure(self):
  from formal_src.runner import run
  from formal_src.provider import APIError
  with tempfile.TemporaryDirectory() as d:
   spec,rd=demo_fixture(Path(d));events=[]
   def fail(*a):raise APIError('http_502')
   result=run(spec,events.append,lambda:'continue',transport=fail,run_dir=rd)
   self.assertEqual(max(e['counts'].get('attempt_count',0) for e in events),2)
   self.assertEqual(max(e['counts'].get('failure_count',0) for e in events),2)
   self.assertEqual(result['state'],'completed')
   self.assertEqual(json.loads((rd/'predictions.jsonl').read_text().splitlines()[-1])['status'],'failed')
 def test_full_compiler_workload_terminal_once_and_repeat_reuse(self):
  from formal_src.runner import run
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as d:
   spec,rd=full_fixture(Path(d));calls=[];active=[0,0]
   import threading,time
   lock=threading.Lock()
   def transport(endpoint,payload,key,timeout):
    with lock:active[0]+=1;active[1]=max(active);calls.append(payload)
    time.sleep(.002)
    with lock:active[0]-=1
    return fake_response(payload)
   with patch('formal_src.retrieval.LocalEncoder',SyntheticEncoder):
    result=run(spec,lambda e:None,lambda:'continue',transport=transport,run_dir=rd)
   self.assertEqual(result['state'],'completed',result)
   predictions=[json.loads(s) for s in (rd/'predictions.jsonl').read_text().splitlines()]
   self.assertEqual(len(predictions),7600);self.assertEqual(len({r['task_id'] for r in predictions}),7600)
   self.assertEqual(sum(r['model_role']!='local' for r in predictions),6600)
   self.assertEqual(sum(r['model_role']=='local' for r in predictions),1000)
   self.assertEqual({r['source_id'] for r in predictions},{'source-group'})
   self.assertTrue(1<active[1]<=4)
   self.assertEqual(json.loads((rd/'summary.json').read_text())['engineering']['attempt_count'],len(calls))
   self.assertTrue(any(r['provenance']=='same_round_reuse' for r in predictions))
   live_rounds={}
   for r in predictions:
    if r['provenance']=='live':live_rounds.setdefault(r['request_hash'],set()).add(r['repeat'])
   self.assertTrue(all(rounds=={1,2,3} for rounds in live_rounds.values()))
   dispatch=[next(t for t in spec['plan']['units'] if t['task_id']==tid)['repeat'] for tid in json.loads((rd/'dispatch-order.json').read_text())]
   self.assertEqual(dispatch,sorted(dispatch))
   byid={r['task_id']:r for r in predictions}
   for r in predictions:
    if r['reuse_source_task_id']:self.assertEqual(r['repeat'],byid[r['reuse_source_task_id']]['repeat'])
   self.assertTrue(json.loads((rd/'sealed.json').read_text())['synthetic'])
   self.assertEqual(json.loads((rd/'summary.json').read_text())['engineering']['concurrency'],4)

 def test_missing_review_and_tampered_recipe_send_nothing(self):
  from formal_src.runner import run
  from unittest.mock import patch
  for tamper in ('review','recipe'):
   with self.subTest(tamper=tamper),tempfile.TemporaryDirectory() as d:
    spec,rd=full_fixture(Path(d));projection=json.loads((rd/'inputs.json').read_text())
    if tamper=='review':
     projection['resources']=[b for b in projection['resources'] if Path(b['path']).name!='scope_reviews.json'];(rd/'inputs.json').write_text(json.dumps(projection))
    else:spec['plan']['units'].pop()
    calls=[]
    with patch('formal_src.retrieval.LocalEncoder',SyntheticEncoder):result=run(spec,lambda e:None,lambda:'continue',transport=lambda *a:calls.append(a),run_dir=rd)
    self.assertEqual(result['state'],'blocked');self.assertEqual(calls,[]);self.assertFalse((rd/'sealed.json').exists())

 def test_pause_resume_and_prediction_tamper(self):
  from formal_src.runner import run
  with tempfile.TemporaryDirectory() as d:
   spec,rd=demo_fixture(Path(d));control=iter(['continue','pause']);calls=[]
   result=run(spec,lambda e:None,lambda:next(control),transport=lambda *a:calls.append(a),run_dir=rd)
   self.assertEqual(result['state'],'paused');self.assertEqual(calls,[])
   result=run(spec,lambda e:None,lambda:'continue',transport=lambda endpoint,payload,key,timeout:(calls.append(payload) or fake_response(payload)),run_dir=rd)
   self.assertEqual(result['state'],'completed');self.assertEqual(len(calls),1)
   prediction=rd/'predictions.jsonl';content=prediction.read_text();prediction.write_text(content.replace('3 years','4 years'))
   result=run(spec,lambda e:None,lambda:'continue',transport=lambda *a:calls.append(a),run_dir=rd)
   self.assertEqual(result['state'],'blocked');self.assertEqual(len(calls),1)

 def test_unsealed_prediction_tamper_blocks_resume(self):
  from formal_src.runner import run
  with tempfile.TemporaryDirectory() as d:
   spec,rd=demo_fixture(Path(d));actions=iter(['continue','pause'])
   run(spec,lambda e:None,lambda:next(actions),transport=lambda *a:None,run_dir=rd)
   path=rd/'predictions.jsonl';records=[json.loads(s) for s in path.read_text().splitlines()];records[0]['answer']='tampered';path.write_text(''.join(json.dumps(r)+'\n' for r in records))
   calls=[];result=run(spec,lambda e:None,lambda:'continue',transport=lambda *a:calls.append(a),run_dir=rd)
   self.assertEqual(result['state'],'blocked');self.assertEqual(calls,[])

 def test_prepare_and_verify_use_keyless_separate_processes(self):
  from formal_src.runner import run
  from unittest.mock import patch
  import os
  for kind in ('prepare','verify'):
   with self.subTest(kind=kind),tempfile.TemporaryDirectory() as d:
    root=Path(d);spec,rd=demo_fixture(root);spec.update(kind=kind,demo_input=None)
    if kind=='verify':spec['source_run_id']=spec['run_id']
    else:
     output=root/'prepared';(output/'inputs').mkdir(parents=True);(output/'inputs/documents.jsonl').write_text('');spec['resolved_config']['prepare_output']=str(output)
    calls=[]
    def child(argv,**kwargs):
     calls.append((argv,kwargs));return type('Result',(),{'returncode':0})()
    exists=Path.exists
    def selected_exists(path):return True if path.name=='verification.py' else exists(path)
    with patch.dict(os.environ,{'FORMAL_API_KEY':'synthetic-secret'}),patch('subprocess.run',side_effect=child),patch('pathlib.Path.exists',selected_exists):
     result=run(spec,lambda e:None,lambda:'continue',run_dir=rd)
    self.assertEqual(result['state'],'completed',result);self.assertEqual(len(calls),1)
    self.assertNotIn('FORMAL_API_KEY',calls[0][1]['env'])
    self.assertIn('--run-dir' if kind=='verify' else '--build-inputs',calls[0][0])

 def test_rolling_threshold_survives_resume_and_unknown_drains(self):
  from formal_src.runner import run
  from formal_src.provider import APIError
  with tempfile.TemporaryDirectory() as d:
   spec,rd=demo_fixture(Path(d));attempts=[{'attempt_id':str(i),'task_id':'other','state':'completed','error_code':'http_502' if i<6 else None} for i in range(100)]
   (rd/'attempts.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in attempts));calls=[]
   result=run(spec,lambda e:None,lambda:'continue',transport=lambda *a:calls.append(a),run_dir=rd)
   self.assertEqual(result['state'],'paused');self.assertIn('rolling_failure_threshold',result['blocking_items']);self.assertEqual(calls,[])
  with tempfile.TemporaryDirectory() as d:
   spec,rd=demo_fixture(Path(d));events=[]
   def unknown(*a):raise APIError('transport_TimeoutError')
   result=run(spec,events.append,lambda:'continue',transport=unknown,run_dir=rd)
   self.assertEqual(result['blocking_items'],['unknown_attempt_requires_inspection'])
   self.assertEqual(events[-1]['counts']['attempt_count'],1);self.assertEqual(events[-1]['counts']['in_flight_count'],0)
   self.assertEqual(json.loads((rd/'attempts.jsonl').read_text().splitlines()[-1])['state'],'unknown')

class SyntheticEncoder:
 def __init__(self,*a):pass
 def tokenize(self,text):return text.split()
 def decode(self,tokens):return ' '.join(tokens)
 def encode(self,texts):return [[1.,0.] for t in texts]

def full_fixture(root):
 from formal_src.run_plan import CANONICAL_PROFILE,compile_run_plan
 from formal_src.provider import digest
 spec,rd=demo_fixture(root)
 cohorts={'development':[f'dev{i}' for i in range(50)],'validation':[f'val{i}' for i in range(50)],'main':[f'main{i}' for i in range(200)],'diagnostic':[f'diag{i}' for i in range(50)],'s50':[f'main{i}' for i in range(50)]}
 plan=compile_run_plan(CANONICAL_PROFILE,cohorts)
 questions=[{'question_id':q,'doc_id':'doc','source_id':'source-group','question':'How long?','split':split} for split,ids in cohorts.items() if split!='s50' for q in ids]
 unit={'evidence_id':'e','doc_id':'doc','text':'compensation expensed over 3 years','region':[0,0,10,10],'blocks':[]}
 image=b'\x89PNG\r\n\x1a\nsynthetic-only'
 structure={'evidence_id':'e','evidence_sha':digest(unit),'nodes':[{'node_id':'N1.C1','text':'3 years'}],'relations':[],'verified':False}
 import hashlib
 review={'evidence_id':'e','unit_sha':digest(unit),'image_sha':hashlib.sha256(image).hexdigest(),'image_path':'crop.png','structure_sha':digest(structure),'qa_exposed':False,'verified':True,'scope_matches':True,'reviewer':'synthetic-reviewer','reviewed_at':'2026-10-24T00:00:00Z'}
 resources=[]
 for name,data,kind in [('inference_questions.jsonl',questions,'input'),('evidence_units.jsonl',[unit],'input'),('cohorts.json',cohorts,'input'),('scope_reviews.json',{'reviews':[review]},'approved_description'),('approved_structures.jsonl',[structure],'approved_description'),('crop.png',image,'input')]:
  path=root/name
  if isinstance(data,bytes):path.write_bytes(data)
  elif name.endswith('.jsonl'):path.write_text(''.join(json.dumps(r)+'\n' for r in data))
  else:path.write_text(json.dumps(data))
  resources.append({'kind':kind,'path':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
 (rd/'inputs.json').write_text(json.dumps({'root':str(root),'resources':resources}))
 spec.update(kind='experiment',demo_input=None,plan=plan,logical_generation_count=6600)
 spec['resolved_config'].update(models={'primary':'gpt-6.1-sol','secondary':'gpt-6-astra'},concurrency=4)
 (rd/'spec.json').write_text(json.dumps(spec));return spec,rd

class FormalFixTests(unittest.TestCase):
 def test_projected_model_works_without_recipe_override(self):
  from formal_src.runner import run
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as d:
   spec,rd,model_root=production_fixture(Path(d));seen=[]
   class Encoder(SyntheticEncoder):
    def __init__(self,path):seen.append(path)
   with patch('formal_src.retrieval.LocalEncoder',Encoder):result=run(spec,lambda e:None,lambda:'pause',run_dir=rd)
   self.assertEqual(result['state'],'paused',result);self.assertEqual(seen,[str(model_root.resolve())])

 def test_worker_rejects_protocol_prompt_and_disk_identity(self):
  from formal_src.runner import formal_tasks,RunnerBlocked,input_package
  from formal_src.provider import digest
  for field in ('model','cap','reasoning','stream','prompt','disk'):
   with self.subTest(field=field),tempfile.TemporaryDirectory() as d:
    spec,rd,_=production_fixture(Path(d));config=spec['resolved_config']
    if field=='model':config['models']['primary']='WRONG'
    elif field=='cap':config['provider']['max_output_tokens']=17
    elif field=='reasoning':config['provider']['reasoning_effort']='high'
    elif field=='stream':config['provider']['stream']=False
    elif field=='prompt':config['prompt_sha']='0'*64
    else:config['code_sha']=spec['code_sha']='0'*64
    config['freeze_sha']=None;config['freeze_sha']=digest(config);spec['freeze_sha']=config['freeze_sha']
    with self.assertRaises(RunnerBlocked):formal_tasks(spec,input_package(rd)[0])

 def test_pause_and_cancel_before_retry_settle_known_attempt(self):
  from formal_src.runner import run
  from formal_src.provider import APIError
  for action,state in [('pause','paused'),('cancel','cancelled')]:
   with self.subTest(action=action),tempfile.TemporaryDirectory() as d:
    spec,rd=demo_fixture(Path(d));current=['continue'];calls=[]
    def transport(*args):calls.append(1);current[0]=action;raise APIError('http_502')
    result=run(spec,lambda e:None,lambda:current[0],transport=transport,run_dir=rd)
    self.assertEqual(result['state'],state,result);self.assertEqual(calls,[1])
    records=[json.loads(s) for s in (rd/'predictions.jsonl').read_text().splitlines()]
    self.assertEqual(records[-1]['status'],'failed')
    result=run(spec,lambda e:None,lambda:'continue',transport=lambda *a:calls.append(2),run_dir=rd)
    self.assertEqual(calls,[1]);self.assertEqual(result['state'],'completed')

 def test_pause_during_future_wait_stops_next_send(self):
  from formal_src.runner import run
  import threading
  for requested,state in [('pause','paused'),('cancel','cancelled')]:
   with self.subTest(action=requested),tempfile.TemporaryDirectory() as d:
    spec,rd=full_fixture(Path(d));spec.update(kind='demo',demo_input={'doc_id':'doc','question':'How long?','retriever':'bm25','groups':['E2','E3']});spec['resolved_config']['concurrency']=1
    current=['continue'];entered=threading.Event();sampled=threading.Event();calls=[];main_samples=[0]
    def control():
     if threading.current_thread() is threading.main_thread():
      main_samples[0]+=1
      if main_samples[0]>=2:sampled.set()
     return current[0]
    def transport(endpoint,payload,key,timeout):
     calls.append(payload);entered.set();self.assertTrue(sampled.wait(3));current[0]=requested;return fake_response(payload)
    result=run(spec,lambda e:None,control,transport=transport,run_dir=rd)
    self.assertEqual(result['state'],state,result);self.assertEqual(len(calls),1)

 def test_selected_structure_projection_and_legacy_geometry(self):
  from formal_src.runner import reviewed_assets,condition_content,RunnerBlocked
  from formal_src.provider import digest
  u={'evidence_id':'e','doc_id':'doc','text':'alpha beta','blocks':[{'block_id':'a','text':'alpha','bbox':[0,0,10,10]},{'block_id':'b','text':'beta','bbox':[0,20,10,30]}]}
  v={'evidence_id':'v','doc_id':'doc','text':'footnote','blocks':[]}
  records=[{'evidence_id':'e','evidence_sha':digest(u),'nodes':[{'node_id':'a','text':'alpha'}],'relations':[{'source':'a','target':'b','kind':'footnote'}]},{'evidence_id':'v','evidence_sha':digest(v),'nodes':[{'node_id':'b','text':'footnote'}],'relations':[]}]
  reviews=[{'evidence_id':unit['evidence_id'],'unit_sha':digest(unit),'structure_sha':digest(r),'qa_exposed':False,'verified':True,'scope_matches':True,'reviewer':'synthetic','reviewed_at':'2026-10-24T00:00:00Z'} for unit,r in zip([u,v],records)]
  data={'scope_reviews.json':{'reviews':reviews},'approved_structures.jsonl':records}
  assets=reviewed_assets([u],[u,v],data,{},'E4');self.assertEqual(assets['structures'][0]['relations'],[])
  self.assertNotIn('footnote',json.dumps(condition_content('E4','q',[u],assets)))
  assets=reviewed_assets([u,v],[u,v],data,{},'E4');self.assertEqual(len(assets['structures'][0]['relations']),1)
  legacy=reviewed_assets([u],[u,v],data,{},'G')['legacy_geometry'];self.assertIn('Block a is above block b.',legacy);self.assertNotIn('bbox',legacy)
  u['blocks'][0]['text']='UNSHARED_SECRET';reviews[0]['unit_sha']=digest(u)
  with self.assertRaises(RunnerBlocked):reviewed_assets([u],[u,v],data,{},'G')

 def test_unknown_peer_prevents_fresh_retry(self):
  from formal_src.runner import run
  from formal_src.provider import APIError
  import threading
  with tempfile.TemporaryDirectory() as d:
   spec,rd=full_fixture(Path(d));spec.update(kind='demo',demo_input={'doc_id':'doc','question':'How long?','retriever':'bm25','groups':['E2','E3']});spec['resolved_config']['concurrency']=2
   peer_finished=threading.Event();calls=[]
   def emit(event):
    if event['event_type']=='attempt_finished' and event['counts']['failure_count']==1:peer_finished.set()
   def transport(endpoint,payload,key,timeout):
    calls.append(payload)
    if any(c['type']=='input_image' for c in payload['input'][0]['content']):raise APIError('transport_TimeoutError')
    self.assertTrue(peer_finished.wait(3));raise APIError('http_502')
   result=run(spec,emit,lambda:'continue',transport=transport,run_dir=rd)
   self.assertEqual(result['state'],'blocked',result);self.assertEqual(len(calls),2)
   attempts=[json.loads(s) for s in (rd/'attempts.jsonl').read_text().splitlines()]
   self.assertEqual(len({a['attempt_id'] for a in attempts}),2)
   records=[json.loads(s) for s in (rd/'predictions.jsonl').read_text().splitlines()];self.assertEqual(records[0]['status'],'failed')

 def test_model_binding_ambiguity_and_unregistered_file_rejected(self):
  from formal_src.runner import projected_model_root,input_package,RunnerBlocked
  import hashlib
  for mode in ('ambiguous','unregistered'):
   with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
    root=Path(d);spec,rd,model=production_fixture(root);projection=json.loads((rd/'inputs.json').read_text())
    if mode=='ambiguous':
     p=root/'other/modules.json';p.parent.mkdir();p.write_text('[]');projection['resources'].append({'kind':'model','path':'other/modules.json','sha256':hashlib.sha256(p.read_bytes()).hexdigest()});(rd/'inputs.json').write_text(json.dumps(projection))
    else:(model/'unregistered.bin').write_bytes(b'synthetic')
    with self.assertRaises(RunnerBlocked):projected_model_root(input_package(rd)[1])


 def test_pause_while_budget_reservation_waits_prevents_physical_send(self):
  from formal_src.runner import run
  from formal_src.budget import Budget
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as d:
   spec,rd=demo_fixture(Path(d));spec['demo_input']['groups']=['E2'];current=['continue'];calls=[];reserve=Budget.reserve
   def held_reserve(self,*args):
    row=reserve(self,*args);current[0]='pause';return row
   with patch.object(Budget,'reserve',held_reserve):result=run(spec,lambda e:None,lambda:current[0],transport=lambda *a:(calls.append(a) or fake_response(a[1])),run_dir=rd)
   self.assertEqual(result['state'],'paused');self.assertEqual(calls,[])
   self.assertEqual(json.loads((rd/'attempts.jsonl').read_text().splitlines()[-1])['state'],'reserved')


 def test_live_demo_actual_source_identity_checked_before_dispatch(self):
  from formal_src.runner import run
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as d:
   spec,rd=demo_fixture(Path(d));spec['purpose']='demo'
   with patch('formal_src.runner.generate',side_effect=AssertionError('must not dispatch')):
    result=run(spec,lambda e:None,lambda:'continue',run_dir=rd)
   self.assertEqual(result['blocking_items'],['actual_code_identity_mismatch'])

def production_fixture(root):
 from formal_src.run_plan import CANONICAL_PROFILE
 from formal_src.profile_lifecycle import freeze_formal_profile
 from formal_src.provider import INSTRUCTIONS,digest
 import hashlib
 spec,rd=full_fixture(root);model=root/'model';model.mkdir();(model/'modules.json').write_text('[]');(model/'config.json').write_text('{}')
 projection=json.loads((rd/'inputs.json').read_text())
 for p in model.iterdir():projection['resources'].append({'kind':'model','path':str(p.relative_to(root)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
 (rd/'inputs.json').write_text(json.dumps(projection));project=Path(__file__).resolve().parents[2]
 code=digest({str(p.relative_to(project)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('formal_src','src','scripts') for p in sorted((project/folder).rglob('*.py'))})
 config=freeze_formal_profile(CANONICAL_PROFILE,models={'primary':'gpt-6.1-sol','secondary':'gpt-6-astra'},provider={'endpoint':'https://example.com/v1/responses','max_output_tokens':2048,'reserve_input_tokens':10000,'reasoning_effort':'low','stream':True},prompt_sha=hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(),resource_manifest_sha=spec['resource_sha'],code_sha=code,audit_plan_sha256='4'*64,readiness={k:True for k in CANONICAL_PROFILE['readiness']},concurrency=1)
 spec.update(purpose='formal',resolved_config=config,code_sha=code,freeze_sha=config['freeze_sha']);spec['plan']['executable']=True
 return spec,rd,model
