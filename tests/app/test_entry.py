"""Breaks caught: the UI launches a different path or exposes unsealed/private files."""
import importlib,json,tempfile,time,unittest,hashlib,subprocess,sys
from pathlib import Path
from uuid import uuid4
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[2]

class EntryTests(unittest.TestCase):
 def test_cli_project_root_precedes_tests_pythonpath(self):
  import os
  env={**os.environ,'PYTHONPATH':'tests:src:scripts:.'}
  result=subprocess.run([sys.executable,str(ROOT/'scripts/course_entry.py'),'--help'],cwd=ROOT,env=env,capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr)
 def entry(self):
  try:return importlib.import_module('scripts.course_entry')
  except ModuleNotFoundError:self.fail('single application entry not implemented')
 def resource(self,root):
  root.mkdir();q={'question_id':'q','doc_id':'d','question':'What was revenue in 2019?','split':'development'}
  unit={'evidence_id':'u','doc_id':'d','text':'Revenue in 2019 was $5 million.','region':[0,0,100,20]}
  bindings=[]
  for name,record in [('inference_questions.jsonl',q),('evidence_units.jsonl',unit)]:
   p=root/name;p.write_text(json.dumps(record)+'\n');bindings.append({'kind':'input','path':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
  (root/'resources.json').write_text(json.dumps({'schema_version':1,'resource_set_id':'demo','purpose':'demo','resources':bindings}))
  return q
 def test_browser_configuration_starts_same_real_worker_and_persists_result(self):
  with tempfile.TemporaryDirectory() as td:
   temp=Path(td);q=self.resource(temp/'data');app=self.entry().build_app(ROOT,temp/'work');client=TestClient(app)
   caps=client.get('/api/v1/capabilities').json();headers={'x-session-id':caps['session_id']}
   self.assertEqual(client.get('/').status_code,200)
   registered=client.post('/api/v1/resources/register',headers=headers,json={'root_path':str(temp/'data')});self.assertEqual(registered.status_code,200)
   examples=client.get('/api/v1/resources/demo/examples').json();self.assertEqual(examples[0]['question_id'],'q')
   req=dict(schema_version=1,client_request_id=str(uuid4()),kind='demo',profile_id='formal-v1',resource_set_id='demo',source_run_id=None,demo_input=dict(doc_id='d',question=q['question'],retriever='bm25',groups=['E1']))
   started=client.post('/api/v1/runs',headers=headers,json={'request':req}).json();rid=started['run_id']
   for _ in range(200):
    state=client.get('/api/v1/runs/'+rid).json()
    if state['state'] in ('completed','failed','blocked'):break
    time.sleep(.02)
   self.assertEqual(state['state'],'completed',state)
   records=client.get(f'/api/v1/runs/{rid}/files/predictions.jsonl');self.assertEqual(records.status_code,200)
   self.assertEqual(json.loads(records.text.splitlines()[0])['provenance'],'local')
   reopened=TestClient(self.entry().build_app(ROOT,temp/'work'))
   self.assertEqual(reopened.get('/api/v1/runs/'+rid).json()['state'],'completed')
   self.assertEqual(reopened.get(f'/api/v1/runs/{rid}/files/spec.json').status_code,403)
 def test_source_files_and_unsealed_predictions_cannot_be_downloaded(self):
  with tempfile.TemporaryDirectory() as td:
   app=self.entry().build_app(ROOT,Path(td)/'work');client=TestClient(app);rid=str(uuid4())
   directory=Path(td)/'work/runs'/rid;directory.mkdir();(directory/'predictions.jsonl').write_text('{}\n')
   self.assertEqual(client.get(f'/api/v1/runs/{rid}/files/predictions.jsonl').status_code,409)
   self.assertEqual(client.get(f'/api/v1/runs/{rid}/files/scored-answers.jsonl').status_code,409)
   self.assertEqual(client.get(f'/api/v1/runs/{rid}/files/inputs.json').status_code,403)

class EntryAdditionalTests(unittest.TestCase):
 entry=EntryTests.entry
 resource=EntryTests.resource
 def test_readonly_defaults_and_run_bound_evidence(self):
  with tempfile.TemporaryDirectory() as td:
   temp=Path(td);self.resource(temp/'data');app=self.entry().build_app(ROOT,temp/'work');client=TestClient(app)
   self.assertEqual(client.get('/api/v1/demo-defaults').json()['legacy_root'],str(ROOT/'data/processed/nonllm-pilot-v5'))
   self.assertEqual(client.get('/api/v1/runs/'+str(uuid4())+'/evidence').status_code,409)
 def test_cli_http_share_real_local_worker_semantics(self):
  with tempfile.TemporaryDirectory() as td:
   temp=Path(td);q=self.resource(temp/'data');work=temp/'work';app=self.entry().build_app(ROOT,work);client=TestClient(app)
   h={'x-session-id':client.get('/api/v1/capabilities').json()['session_id']}
   client.post('/api/v1/resources/register',headers=h,json={'root_path':str(temp/'data')})
   request=dict(schema_version=1,client_request_id=str(uuid4()),kind='demo',profile_id='formal-v1',resource_set_id='demo',source_run_id=None,demo_input=dict(doc_id='d',question=q['question'],retriever='bm25',groups=['E1']))
   http=client.post('/api/v1/runs',headers=h,json={'request':request}).json()
   for _ in range(200):
    http=client.get('/api/v1/runs/'+http['run_id']).json()
    if http['state']=='completed':break
    time.sleep(.02)
   request['client_request_id']=str(uuid4());path=temp/'request.json';path.write_text(json.dumps(request))
   process=subprocess.run([sys.executable,str(ROOT/'scripts/course_entry.py'),'--workspace',str(work),'execute','--request',str(path)],capture_output=True,text=True,timeout=15)
   self.assertEqual(process.returncode,0,process.stderr);cli=json.loads(process.stdout)
   def output(rid,name):return json.loads((work/'runs'/rid/name).read_text().splitlines()[0])
   a=output(http['run_id'],'predictions.jsonl');b=output(cli['run_id'],'predictions.jsonl')
   for key in ('run_id','elapsed_ms'):a.pop(key);b.pop(key)
   self.assertEqual(a,b)
   a=output(http['run_id'],'spec.json');b=output(cli['run_id'],'spec.json')
   for key in ('run_id','client_request_id','output_relative_path'):a.pop(key);b.pop(key)
   self.assertEqual(a,b)
   for rid in (http['run_id'],cli['run_id']):
    self.assertEqual(output(rid,'summary.json')['metrics'],{})
   self.assertEqual(client.get('/api/v1/runs/'+http['run_id']+'/evidence').json()[0]['evidence_id'],'u')

class EntrySealAndPreparedTests(EntryAdditionalTests):
 def test_tampered_sealed_outputs_cannot_be_downloaded(self):
  from tests.formal.test_runner import demo_fixture,fake_response
  from formal_src.runner import run
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);spec,rd=demo_fixture(root)
   run(spec,lambda e:None,lambda:'continue',transport=lambda endpoint,payload,key,timeout:fake_response(payload),run_dir=rd)
   client=TestClient(self.entry().build_app(ROOT,root))
   (rd/'predictions.jsonl').write_text('modified')
   self.assertEqual(client.get(f'/api/v1/runs/{rd.name}/files/predictions.jsonl').status_code,409)
 def test_prepared_resources_expose_development_examples_only(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);data=root/'data';self.resource(data)
   m=json.loads((data/'resources.json').read_text());m['purpose']='formal'
   qpath=data/'inference_questions.jsonl';rows=qpath.read_text();qpath.write_text(rows+json.dumps({'question_id':'hidden','doc_id':'test-doc','split':'main','question':'not for demo'})+'\n')
   for b in m['resources']:
    if b['path']=='inference_questions.jsonl':b['sha256']=hashlib.sha256(qpath.read_bytes()).hexdigest()
   (data/'resources.json').write_text(json.dumps(m));app=self.entry().build_app(ROOT,root/'work');app.state.service.register_resources(data)
   client=TestClient(app);response=client.get('/api/v1/resources/demo/examples')
   self.assertEqual(response.status_code,200);self.assertEqual([r['question_id'] for r in response.json()],['q'])
   self.assertEqual(client.get('/api/v1/resources/demo/evidence/test-doc').status_code,403)

if __name__=='__main__':unittest.main()
