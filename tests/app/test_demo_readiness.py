import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from app.service import ApplicationService
from app.resources import ResourceRegistry


class DemoReadinessTests(unittest.TestCase):
    def fixture(self, root):
        data = root / 'data'
        data.mkdir()
        items = []
        for name, rows in [('inference_questions.jsonl', [{'question_id':'q','doc_id':'doc','question':'How long?','split':'development'}]), ('evidence_units.jsonl', [{'evidence_id':'e','doc_id':'doc','text':'3 years','blocks':[]}])]:
            path = data / name
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            items.append(dict(kind='input',path=name,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        (data/'resources.json').write_text(json.dumps(dict(schema_version=1,resource_set_id='demo',purpose='demo',resources=items)))
        service = ApplicationService(Path(__file__).resolve().parents[2],root/'workspace')
        service.register_resources(data)
        request = dict(schema_version=1,client_request_id=str(uuid4()),kind='demo',profile_id='formal-v1',resource_set_id='demo',source_run_id=None,demo_input=dict(doc_id='doc',question='How long?',retriever='bm25',groups=['E1','E3','E4']))
        return service, request, data

    def test_missing_scope_review_blocks_preflight_without_creating_run(self):
        with tempfile.TemporaryDirectory() as td:
            service, request, _ = self.fixture(Path(td))
            report = service.preflight(request)
            self.assertFalse(report['ready'])
            self.assertIn('approved_blind_scope_review_missing',report['blocking_items'])
            self.assertEqual(list(service.jobs.runs.iterdir()),[])
            request['demo_input']['groups']=['E1','E2']
            self.assertTrue(service.preflight(request)['ready'])

    def test_missing_local_model_blocks_preflight(self):
        with tempfile.TemporaryDirectory() as td:
            service, request, _ = self.fixture(Path(td))
            request['demo_input'].update(retriever='minilm',groups=['E1'])
            self.assertIn('registered_local_model_missing',service.preflight(request)['blocking_items'])

    def test_reregister_and_restart_preserve_only_unchanged_approval(self):
        with tempfile.TemporaryDirectory() as td:
            service, _, data = self.fixture(Path(td))
            manifest=json.loads((data/'resources.json').read_text())
            approval={'reviews':[dict(asset_sha256=manifest['resources'][0]['sha256'],reviewer='test-reviewer',reviewed_at='2026-10-25T00:00:00Z',approved=True,qa_exposed=False)]}
            service.import_reviews('demo',approval)
            registry=ResourceRegistry(service.workspace)
            registry.register(data)
            self.assertEqual(registry.record('demo').get('reviews'),approval)
            manifest['resources'][0]['sha256']='0'*64
            (data/'resources.json').write_text(json.dumps(manifest))
            registry.register(data)
            self.assertNotIn('reviews',registry.record('demo'))

    def test_demo_packaged_review_is_hash_bound_and_restored(self):
        with tempfile.TemporaryDirectory() as td:
            service, _, data = self.fixture(Path(td))
            manifest=json.loads((data/'resources.json').read_text())
            approval={'reviews':[dict(asset_sha256=manifest['resources'][0]['sha256'],reviewer='test AI',reviewer_kind='ai',reviewed_at='2026-10-25T00:00:00Z',approved=True,qa_exposed=False)]}
            p=data/'review-import.json';p.write_text(json.dumps(approval))
            manifest['review_import']='review-import.json'
            manifest['resources'].append(dict(kind='review',path=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
            (data/'resources.json').write_text(json.dumps(manifest))
            service.register_resources(data)
            self.assertEqual(service.resources.record('demo').get('reviews'),approval)
            p.write_text('{}')
            with self.assertRaises(ValueError):service.register_resources(data)

    def test_reviewed_e3_e4_pass_and_changed_crop_is_rejected(self):
        from formal_src.provider import digest
        with tempfile.TemporaryDirectory() as td:
            service, request, data=self.fixture(Path(td))
            unit=json.loads((data/'evidence_units.jsonl').read_text())
            structure=dict(evidence_id='e',evidence_sha=digest(unit),nodes=[dict(node_id='N1',text='3 years')],relations=[])
            image=b'\x89PNG\r\n\x1a\nsynthetic fixture only'
            review=dict(evidence_id='e',unit_sha=digest(unit),image_path='crop.png',image_sha=hashlib.sha256(image).hexdigest(),structure_sha=digest(structure),qa_exposed=False,verified=True,scope_matches=True,reviewer='test-only',reviewed_at='2026-10-25T00:00:00Z')
            manifest=json.loads((data/'resources.json').read_text())
            for name,body,kind in [('crop.png',image,'input'),('scope_reviews.json',json.dumps({'reviews':[review]}).encode(),'approved_description'),('approved_structures.jsonl',(json.dumps(structure)+'\n').encode(),'approved_description')]:
                (data/name).write_bytes(body)
                manifest['resources'].append(dict(kind=kind,path=name,sha256=hashlib.sha256(body).hexdigest()))
            (data/'resources.json').write_text(json.dumps(manifest))
            service.register_resources(data)
            approval={'reviews':[dict(asset_sha256=r['sha256'],approved=True,qa_exposed=False,reviewer='test-only',reviewed_at='2026-10-25T00:00:00Z') for r in manifest['resources'] if r['kind']=='approved_description']}
            service.import_reviews('demo',approval)
            self.assertTrue(service.preflight(request)['ready'])
            (data/'crop.png').write_bytes(b'changed')
            report=service.preflight(request)
            self.assertFalse(report['ready'])
            self.assertIn('resource_sha_mismatch',report['blocking_items'])
