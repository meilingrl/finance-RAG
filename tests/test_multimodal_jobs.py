"""Evidence scope, blinding and shared-prompt boundaries for E3/E4."""
import base64
import copy
import hashlib
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from multimodal_jobs import augment_request, prepare_multimodal_job
from ragpilot.common import digest, file_sha
from ragpilot.common import read_jsonl, write_jsonl
from ragpilot.pipeline import make_fixture, run_offline
from llm_execution import prepare_job, execute_job, load_job
from test_llm_execution import responses_reply


class MultimodalBoundaries(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        image = self.root / 'crop.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', 100, 80))
        self.unit = {'evidence_id': 'u1', 'content_hash': 'content', 'image_path': str(image),
                     'image_sha256': file_sha(image), 'text': 'Revenue\t10', 'whole_page': False}
        self.request = {'question_id': 'q1', 'doc_id': 'd1', 'group': 'E2', 'selected_ids': ['u1'],
                        'retrieval_freeze_hash': 'frozen', 'shared_text_hash': digest('Question\nRevenue\t10'),
                        'diagnostic_partition': 'local_regions', 'payload': {'model': 'same-model',
                        'store': False, 'instructions': 'same instructions', 'max_output_tokens': 2048,
                        'input': [{'role': 'user', 'content': 'Question\nRevenue\t10'}],
                        'text': {'format': {'type': 'json_schema'}}}}
        self.request['request_hash'] = digest(self.request['payload'])
        self.review = {'evidence_id': 'u1', 'content_hash': 'content', 'image_sha256': self.unit['image_sha256'],
                       'verified': True, 'question_exposure': False, 'answer_gold_exposure': False,
                       'evaluation_metrics_exposure': False, 'status': 'pass', 'text': 'One block.'}

    def test_image_uses_exact_bytes_and_same_prompt_without_mutation(self):
        before = copy.deepcopy(self.request)
        row, blocked = augment_request(self.request, {'u1': self.unit}, {'u1': self.review}, 'E3')
        self.assertEqual(blocked, [])
        self.assertEqual(self.request, before)
        self.assertEqual(row['payload']['instructions'], before['payload']['instructions'])
        self.assertEqual(row['payload']['input'][0]['content'][0]['text'], before['payload']['input'][0]['content'])
        parts = row['payload']['input'][0]['content']
        self.assertEqual(parts[1]['text'], 'Evidence image: u1')
        self.assertEqual(base64.b64decode(parts[2]['image_url'].split(',')[1]), Path(self.unit['image_path']).read_bytes())
        self.assertEqual(row['shared_text_hash'], before['shared_text_hash'])
        self.assertEqual(row['selected_ids'], ['u1'])

    def test_unreviewed_mismatched_or_exposed_evidence_is_never_sent(self):
        for changes in ({'verified': False}, {'content_hash': 'stale'}, {'question_exposure': True},
                        {'answer_gold_exposure': True}, {'evaluation_metrics_exposure': True},
                        {'image_sha256': 'stale'}):
            row, blocked = augment_request(self.request, {'u1': self.unit},
                                           {'u1': {**self.review, **changes}}, 'E3')
            self.assertIsNone(row)
            self.assertTrue(blocked)
        row, blocked = augment_request(self.request, {'u1': self.unit}, {}, 'E3')
        self.assertIsNone(row); self.assertTrue(blocked)

    def test_changed_image_bytes_fail_instead_of_using_stale_review(self):
        Path(self.unit['image_path']).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            augment_request(self.request, {'u1': self.unit}, {'u1': self.review}, 'E3')

    def test_description_attachment_keeps_shared_text_and_no_images(self):
        row, blocked = augment_request(self.request, {'u1': self.unit}, {'u1': self.review}, 'E4')
        parts = row['payload']['input'][0]['content']
        self.assertEqual(parts[0]['text'], self.request['payload']['input'][0]['content'])
        self.assertEqual(parts[1]['text'], 'Structural description for u1:\nOne block.')
        self.assertTrue(all(p['type'] == 'input_text' for p in parts))
        self.assertEqual(row['description_refs'], ['u1'])
        self.assertEqual(blocked, [])

    def test_payload_tampering_and_non_responses_protocol_rejected(self):
        self.request['payload']['model'] = 'changed'
        with self.assertRaises(ValueError):
            augment_request(self.request, {'u1': self.unit}, {'u1': self.review}, 'E4')

    def test_description_must_be_nonempty_and_blinding_explicit(self):
        for changes in ({'text': ''}, {'text': None}, {'question_exposure': None}):
            row, blocked = augment_request(self.request, {'u1': self.unit},
                                           {'u1': {**self.review, **changes}}, 'E4')
            self.assertIsNone(row); self.assertTrue(blocked)


class SealedMultimodalJobs(unittest.TestCase):
    def test_prepare_execute_and_overlay_tampering_boundary_without_gold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data = root / 'data'; source = root / 'source'; base = root / 'base'
            make_fixture(data); run_offline(data, source, split='development')
            config = {'endpoint': 'https://example.com/v1/responses', 'wire_api': 'responses',
                      'model': 'gpt-6-luna', 'api_key_env': 'OPENAI_API_KEY', 'max_completion_tokens': 512,
                      'reasoning_effort': 'none', 'reserve_input_tokens': 32768,
                      'input_usd_per_million': None, 'output_usd_per_million': None, 'timeout_seconds': 30}
            prepare_job(source, data, base, config, limit=1)
            originals = read_jsonl(source / 'descriptions.jsonl')
            reviews = [{'evidence_id': d['evidence_id'], 'content_hash': d['evidence_content_hash'],
                        'verified': True, 'question_exposure': False, 'answer_gold_exposure': False,
                        'evaluation_metrics_exposure': False, 'original_text': d['text'],
                        'original_text_hash': hashlib.sha256(d['text'].encode()).hexdigest(),
                        'text': d['text'], 'source_kind': 'coordinate_template_v1_anchored_legend'} for d in originals]
            overlay = root / 'review.jsonl'; write_jsonl(overlay, reviews)
            frozen_hash = file_sha(source / 'evidence_units.jsonl')
            original_read = Path.read_text
            def no_gold(path, *args, **kwargs):
                if 'gold' in path.name:
                    raise AssertionError('Gold opened during preparation or inference')
                return original_read(path, *args, **kwargs)
            with patch.object(Path, 'read_text', no_gold):
                result = prepare_multimodal_job(base, overlay, root / 'e4', 'E4')
                self.assertEqual(result['question_count'], 1)
                self.assertEqual(result['exclusion_count'], 0)
                summary = execute_job(root / 'e4', 'test-secret', root / 'ledger.jsonl', None,
                                      transport=lambda *args: responses_reply())
                self.assertEqual(summary['status'], 'completed')
                # Contemporary text control gets an explicit audit-only replicate
                # marker; model-visible text and instructions remain byte-identical.
                control = prepare_multimodal_job(base, None, root / 'e2', 'E2')
                self.assertEqual(control['question_count'], 1)
                old = read_jsonl(base / 'requests.jsonl')[0]['payload']
                new = read_jsonl(root / 'e2' / 'requests.jsonl')[0]['payload']
                self.assertEqual({k: v for k, v in new.items() if k != 'metadata'}, old)
                self.assertEqual(new['metadata']['experiment_cohort'], 'phase4-20261023-v1')
            self.assertEqual(file_sha(source / 'evidence_units.jsonl'), frozen_hash)
            reviews[0]['text'] = 'changed after seal'; write_jsonl(overlay, reviews)
            with self.assertRaises(ValueError): load_job(root / 'e4')
            # Replacing both the unsealed old description file and overlay cannot
            # relabel an arbitrary narrative as the original geometry template.
            originals[0]['text'] = 'changed template'
            write_jsonl(source / 'descriptions.jsonl', originals)
            reviews[0]['original_text'] = 'changed template'
            reviews[0]['original_text_hash'] = hashlib.sha256(b'changed template').hexdigest()
            write_jsonl(overlay, reviews)
            with self.assertRaises(ValueError):
                prepare_multimodal_job(base, overlay, root / 'bad-e4', 'E4')

    def test_excluded_evaluation_questions_remain_explicit_instead_of_being_approved(self):
        # A missing review blocks the whole selected set, never a reduced top-k.
        case = MultimodalBoundaries(); case.setUp()
        self.addCleanup(case.doCleanups)
        request = copy.deepcopy(case.request); request['selected_ids'].append('u2')
        row, reasons = augment_request(request, {'u1': case.unit, 'u2': {**case.unit, 'evidence_id': 'u2'}},
                                       {'u1': case.review}, 'E3')
        self.assertIsNone(row)
        self.assertEqual([r['evidence_id'] for r in reasons], ['u2'])

