import sys
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from summarize_multimodal import aggregate, paired_changes, summarize
from multimodal_jobs import prepare_multimodal_job
from llm_execution import prepare_job, execute_job
from ragpilot.common import read_json, read_jsonl, write_json, write_jsonl, digest, file_sha
from ragpilot.pipeline import make_fixture, run_offline
from test_llm_execution import responses_reply


class MatchedDenominators(unittest.TestCase):
    def test_abstention_stays_in_denominator_and_groups_use_identical_ids(self):
        rows = [{'question_id': 'a', 'group': 'E2', 'status': 'answered', 'exact_match': 1,
                 'answer_only_exact_match': 1},
                {'question_id': 'b', 'group': 'E2', 'status': 'abstention', 'exact_match': None,
                 'answer_only_exact_match': None}]
        result = aggregate(rows, {'a', 'b'}, ['E2'])[0]
        self.assertEqual(result['questions'], 2)
        self.assertEqual(result['answered'], 1)
        self.assertEqual(result['strict_correct'], 1)
        self.assertEqual(result['strict_accuracy_all'], 0.5)
        with self.assertRaises(ValueError): aggregate(rows, {'a', 'b', 'c'}, ['E2'])
        with self.assertRaises(ValueError): aggregate(rows + rows[:1], {'a', 'b'}, ['E2'])

    def test_net_difference_is_paired_gains_minus_losses(self):
        rows = []
        for q, a, b in [('a', 0, 1), ('b', 1, 0), ('c', 0, 1), ('d', 1, 1)]:
            for group, score in [('E2', a), ('E3', b)]:
                rows.append({'question_id': q, 'group': group, 'exact_match': score,
                             'answer_only_exact_match': score, 'status': 'answered'})
        result = paired_changes(rows, {'a', 'b', 'c', 'd'}, 'E2', 'E3')
        self.assertEqual(result['strict'], {'gained': 2, 'lost': 1, 'both_correct': 1, 'both_incorrect': 0, 'net': 1})

    def test_end_to_end_keeps_service_failure_separate_from_common_model_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data = root / 'data'; source = root / 'source'; base = root / 'base'
            phase4 = root / 'llm-phase4'; ledger = root / 'llm-phase3/budget.jsonl'
            make_fixture(data)
            image = root / 'image.png'
            image.write_bytes(b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', 100, 80))
            units = read_jsonl(data / 'evidence_units.jsonl')
            for u in units:
                u.update(image_path=str(image), image_sha256=file_sha(image))
            write_jsonl(data / 'evidence_units.jsonl', units)
            manifest = read_json(data / 'data_manifest.json'); manifest['evidence_hash'] = digest(units)
            write_json(data / 'data_manifest.json', manifest)
            run_offline(data, source, split='development')
            config = {'endpoint': 'https://example.com/v1/responses', 'wire_api': 'responses',
                      'model': 'gpt-6-luna', 'api_key_env': 'OPENAI_API_KEY', 'max_completion_tokens': 512,
                      'reasoning_effort': 'none', 'reserve_input_tokens': 32768,
                      'input_usd_per_million': None, 'output_usd_per_million': None, 'timeout_seconds': 30}
            prepare_job(source, data, base, config)
            execute_job(base, 'test-secret', ledger, None, transport=lambda *args: responses_reply())
            original = {d['evidence_id']: d for d in read_jsonl(source / 'descriptions.jsonl')}
            reviews = []
            for u in units:
                d = original[u['evidence_id']]
                reviews.append({'evidence_id': u['evidence_id'], 'content_hash': u['content_hash'],
                                'image_sha256': u['image_sha256'], 'verified': True, 'status': 'pass',
                                'question_exposure': False, 'answer_gold_exposure': False,
                                'evaluation_metrics_exposure': False, 'text': d['text'], 'original_text': d['text'],
                                'original_text_hash': hashlib.sha256(d['text'].encode()).hexdigest(),
                                'source_kind': 'coordinate_template_v1_anchored_legend'})
            overlay = root / 'overlay.jsonl'; write_jsonl(overlay, reviews)
            for group in ('E2', 'E3', 'E4'):
                folder = phase4 / (group.lower() + '-evaluation-v1')
                prepare_multimodal_job(base, overlay if group != 'E2' else None, folder, group)
                def transport(endpoint, payload, key, timeout):
                    content = payload['input'][0]['content']
                    shared = content if isinstance(content, str) else content[0]['text']
                    if group == 'E3' and 'profit' in shared:
                        return {'status': 'failed', 'output': [], 'model': 'gpt-6-luna', 'error': {'code': 'upstream_error'}}
                    return responses_reply()
                execute_job(folder, 'test-secret', ledger, None, transport=transport)
            result = summarize(phase4, data / 'evaluation_gold.jsonl')
            self.assertEqual(len(result['matched_comparisons']['all']['question_ids']), 2)
            self.assertEqual(len(result['model_usable_matched_comparisons']['all']['question_ids']), 1)
            self.assertEqual(result['technical_failures_excluded_from_model_only_subset'][0]['statuses']['E3'], 'api_error')
            self.assertTrue((phase4 / 'all-scored-results.csv').exists())

