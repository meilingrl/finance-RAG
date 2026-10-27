import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from app.service import ApplicationService
from tests.app.test_service import setup, request

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = dict(endpoint='https://example.invalid/responses', model='test-model',
                max_output_tokens=2048, reserve_input_tokens=131072,
                timeout_seconds=120, stream=True, reasoning_effort='low')

def response(payload, answer='READY'):
    return dict(id='synthetic', model=payload['model'], status='completed', output=[
        dict(type='message', role='assistant', content=[dict(type='output_text', text=json.dumps(
            dict(answer=answer, scale='', citations=['operational-probe'], abstained=False)))])])

class ParallelChecks(unittest.TestCase):
    def test_five_requests_really_overlap_and_proof_binds_configuration(self):
        with tempfile.TemporaryDirectory() as td:
            calls=[]; lock=threading.Lock(); barrier=threading.Barrier(4)
            def transport(endpoint, payload, key, timeout):
                with lock:
                    calls.append(payload); n=len(calls)
                if n>1: barrier.wait(timeout=3)
                return response(payload)
            s=ApplicationService(ROOT, td, provider_transport=transport)
            result=s.check_provider(SETTINGS,s.set_credential('synthetic-secret'),'compatibility')
            self.assertTrue(result['ready'],result)
            self.assertEqual(result['sent_attempts'],5)
            self.assertEqual(result['parallel']['success_count'],4)
            self.assertTrue(s.parallel_verified(dict(provider=SETTINGS,models={'primary':'test-model'})))
            changed={**SETTINGS,'endpoint':'https://other.invalid/responses'}
            self.assertFalse(s.parallel_verified(dict(provider=changed,models={'primary':'test-model'})))
            self.assertFalse(s.parallel_verified(dict(provider=SETTINGS,models={'primary':'other-model'})))
            self.assertNotIn('synthetic-secret',''.join(p.read_text() for p in Path(td).rglob('*.json')))

    def test_parallel_failure_does_not_erase_single_success_or_keep_old_proof(self):
        with tempfile.TemporaryDirectory() as td:
            s=ApplicationService(ROOT, td,provider_transport=lambda e,p,k,t:response(p))
            key=s.set_credential('synthetic-secret')
            self.assertTrue(s.check_provider(SETTINGS,key,'compatibility')['ready'])
            calls=[]
            def transport(e,p,k,t):
                calls.append(1)
                return response(p,'WRONG' if len(calls)>1 else 'READY')
            s.provider_transport=transport
            result=s.check_provider(SETTINGS,key,'compatibility')
            self.assertTrue(result['single']['ready'])
            self.assertFalse(result['parallel']['ready'])
            self.assertFalse(s.parallel_verified(dict(provider=SETTINGS,models={'primary':'test-model'})))

    def test_failed_single_skips_parallel(self):
        with tempfile.TemporaryDirectory() as td:
            s=ApplicationService(ROOT,td,provider_transport=lambda e,p,k,t:response(p,'WRONG'))
            r=s.check_provider(SETTINGS,s.set_credential('synthetic-secret'),'compatibility')
            self.assertEqual(r['sent_attempts'],1)
            self.assertFalse(r['parallel']['ready'])

    def test_demo_resolves_to_serial_without_mutating_saved_profile(self):
        with tempfile.TemporaryDirectory() as td:
            s=setup(Path(td));p=s.clone_profile('formal-v1',{'concurrency':4})
            q=request();q.update(kind='demo',profile_id=p['profile_id'],demo_input=dict(doc_id='d',question='What?',retriever='bm25',groups=['E1']))
            with patch.object(s.jobs,'start'):
                r=s.start_run(q)
            spec=json.loads((s.jobs.directory(r['run_id'])/'spec.json').read_text())
            self.assertEqual(spec['resolved_config']['concurrency'],1)
            self.assertEqual(s._profile(p['profile_id'])['concurrency'],4)

    def test_formal_preflight_requires_current_parallel_proof(self):
        with tempfile.TemporaryDirectory() as td:
            s=setup(Path(td));s.worker_command=None;q=request();q['kind']='experiment'
            self.assertIn('concurrency_not_verified',s.preflight(q)['blocking_items'])

    def test_custom_parallelism_is_measured_and_bound(self):
        for n in (1,8,32):
            with self.subTest(n=n),tempfile.TemporaryDirectory() as td:
                calls=[];barrier=threading.Barrier(n)
                def transport(e,p,k,t):
                    calls.append(1)
                    if len(calls)>1:barrier.wait(timeout=5)
                    return response(p)
                s=ApplicationService(ROOT,td,provider_transport=transport)
                settings={**SETTINGS,'concurrency':n}
                r=s.check_provider(settings,s.set_credential('synthetic-secret'),'compatibility')
                self.assertTrue(r['ready'],r)
                self.assertEqual(r['parallel']['concurrency'],n)
                self.assertEqual(r['sent_attempts'],1 if n==1 else n+1)
                profile=dict(provider=SETTINGS,models={'primary':'test-model'},concurrency=n)
                self.assertTrue(s.parallel_verified(profile))
                profile['concurrency']=2
                self.assertFalse(s.parallel_verified(profile))

    def test_invalid_parallelism_sends_no_requests(self):
        for n in (0,33,True,2.5,'8'):
            with self.subTest(n=n),tempfile.TemporaryDirectory() as td:
                s=ApplicationService(ROOT,td,provider_transport=lambda *a:self.fail('must not send'))
                with self.assertRaises(ValueError):
                    s.check_provider({**SETTINGS,'concurrency':n},s.set_credential('synthetic-secret'),'compatibility')

    def test_http_check_persists_proof_and_counts_actual_attempts(self):
        from fastapi.testclient import TestClient
        from app.api import create_app
        from formal_src.budget import latest
        with tempfile.TemporaryDirectory() as td:
            s=ApplicationService(ROOT,td,provider_transport=lambda e,p,k,t:response(p))
            client=TestClient(create_app(s));key=s.set_credential('synthetic-secret')
            r=client.post('/api/v1/provider/check',headers={'x-session-id':s.session_id},json=dict(
                settings={**SETTINGS,'concurrency':8},credential_ref=key,level='compatibility'))
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['sent_attempts'],9)
            ledger=list(latest(Path(td)/'budget.jsonl').values())
            self.assertEqual(len(ledger),9)
            self.assertTrue(all(row['state']=='completed' for row in ledger))
            restarted=ApplicationService(ROOT,td)
            self.assertTrue(restarted.parallel_verified(dict(provider=SETTINGS,models={'primary':'test-model'},concurrency=8)))

    def test_worker_demo_ignores_large_formal_parallelism(self):
        from formal_src.runner import run
        from tests.formal.test_runner import full_fixture, fake_response, SyntheticEncoder
        with tempfile.TemporaryDirectory() as td:
            spec,rd=full_fixture(Path(td))
            spec.update(kind='demo',demo_input=dict(doc_id='doc',question='How long?',retriever='bm25',groups=['E1','E2','E3','E4']))
            spec['resolved_config']['concurrency']=32
            events=[]
            with patch('formal_src.retrieval.LocalEncoder',SyntheticEncoder):
                result=run(spec,events.append,lambda:'continue',transport=lambda e,p,k,t:fake_response(p),run_dir=rd)
            self.assertEqual(result['state'],'completed',result)
            self.assertTrue(any(e['event_type']=='concurrency_1' for e in events))
            self.assertLessEqual(max(e['counts']['in_flight_count'] for e in events),1)
            self.assertEqual(json.loads((rd/'summary.json').read_text())['engineering']['concurrency'],1)

    def test_serial_demo_can_resume_with_saved_parallel_profile(self):
        with tempfile.TemporaryDirectory() as td:
            s=setup(Path(td));p=s.clone_profile('formal-v1',{'concurrency':8})
            q=request();q.update(kind='demo',profile_id=p['profile_id'],demo_input=dict(doc_id='d',question='What?',retriever='bm25',groups=['E2']))
            r=s.start_run(q)
            self.assertEqual(r['state'],'blocked')
            s.set_credential('synthetic-secret')
            with patch.object(s.jobs,'start') as start:
                resumed=s.control_run(r['run_id'],'resume')
            self.assertNotIn('profile_changed',resumed['blocking_items'])
            start.assert_called_once()
