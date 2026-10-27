"""Course qualification checks use files, not manifest attestations."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


class CourseReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = {'schema_version': 1, 'resource_set_id': 'course', 'purpose': 'formal',
                         'review_policy': 'automatic_course', 'execution_scope': 'main_only',
                         'resources': [], 'qualification': {}}
        self.questions = [{'question_id': f'q{i}', 'doc_id': f'd{i}', 'source_id': f's{i // 5}',
                           'source_report': f's{i // 5}_2020.pdf', 'split': 'main',
                           'question': 'What was revenue?', 'page': 1} for i in range(200)]
        self.units = [{'evidence_id': f'e{i}', 'doc_id': f'd{i}', 'source_id': f's{i // 5}',
                       'source_report': f's{i // 5}_2020.pdf', 'text': 'Revenue 120',
                       'image_path': 'inputs/crop.png', 'image_sha256': hashlib.sha256(b'\x89PNG\r\n\x1a\nfixture').hexdigest()}
                      for i in range(200)]
        self.structures = [{'evidence_id': u['evidence_id'], 'evidence_sha': _digest(u),
                            'nodes': [{'node_id': f'N{i}', 'text': 'Revenue'}], 'relations': [],
                            'question_exposure': False, 'verified': False} for i, u in enumerate(self.units)]
        self.write('inputs/inference_questions.jsonl', self.questions)
        self.write('inputs/evidence_units.jsonl', self.units)
        self.write('inputs/draft_structures.jsonl', self.structures)
        self.write('inputs/cohorts.json', {'main': [q['question_id'] for q in self.questions], 'diagnostic': []})
        self.write('inputs/crop.png', b'\x89PNG\r\n\x1a\nfixture')
        self.write('evaluation/gold.jsonl', [{'question_id': q['question_id'], 'source_id': q['source_id'],
                                             'answer': 'SECRET-GOLD-CONTENT'} for q in self.questions], 'evaluation')
        self.write('models/minilm/modules.json', [{'path': '', 'type': 'sentence_transformers.models.Transformer'},
                                                   {'path': '1_Pooling', 'type': 'sentence_transformers.models.Pooling'}], 'model')
        for name in ('config.json', 'tokenizer.json', '1_Pooling/config.json'):
            self.write('models/minilm/' + name, {}, 'model')
        self.write('models/minilm/model.safetensors', b'local-model-weights', 'model')

    def write(self, path, value, kind='input'):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            content = value
        elif path.endswith('.jsonl'):
            content = ''.join(json.dumps(row) + '\n' for row in value).encode()
        else:
            content = json.dumps(value).encode()
        target.write_bytes(content)
        binding = {'kind': kind, 'path': path, 'sha256': hashlib.sha256(content).hexdigest()}
        self.manifest['resources'] = [r for r in self.manifest['resources'] if r['path'] != path] + [binding]

    def qualify(self, **kwargs):
        self.assertIsNotNone(importlib.util.find_spec('app.course_readiness'), 'Course qualification module required')
        from app.course_readiness import qualify_course_resources
        return qualify_course_resources({'root': str(self.root), 'manifest': self.manifest}, **kwargs)

    def test_valid_main_derives_gates_without_attestations_and_never_exposes_gold(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.qualify()
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['qualification'], {'source_disjoint': True, 'cohorts_validated': True, 'inputs_complete': True})
        self.assertEqual(result['evidence']['counts']['main'], 200)
        self.assertEqual(result['evidence']['source_counts']['main'], 40)
        self.assertNotIn('SECRET-GOLD-CONTENT', json.dumps(result))
        self.assertNotIn('compatibility_passed', result['qualification'])
        self.assertEqual(result['evidence_sha256'], _digest(result['evidence']))
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_duplicate_question_and_wrong_main_count_block_even_with_true_attestations(self):
        self.manifest['qualification'] = {'cohorts_validated': True, 'inputs_complete': True, 'source_disjoint': True}
        self.questions[1] = dict(self.questions[0])
        self.write('inputs/inference_questions.jsonl', self.questions)
        result = self.qualify()
        self.assertIn('course_question_id_duplicate', result['blocking_items'])
        self.assertFalse(result['qualification']['cohorts_validated'])

    def test_main_source_coverage_is_checked(self):
        self.questions[-1]['source_id'] = 'unexpected-source'
        self.write('inputs/inference_questions.jsonl', self.questions)
        self.assertIn('course_main_source_count', self.qualify()['blocking_items'])

    def test_nested_answer_and_credentials_in_inference_are_redacted(self):
        self.questions[0]['extra'] = {'answer': 'SECRET-ANSWER', 'api_key': 'SECRET-KEY'}
        self.write('inputs/inference_questions.jsonl', self.questions)
        result = self.qualify()
        self.assertIn('course_inference_sensitive_fields', result['blocking_items'])
        self.assertNotIn('SECRET-', json.dumps(result))

    def test_question_requires_document_evidence(self):
        self.units.pop()
        self.write('inputs/evidence_units.jsonl', self.units)
        self.assertIn('course_question_evidence_missing', self.qualify()['blocking_items'])

    def test_image_binding_requires_registered_file_and_matching_unit_hash(self):
        self.units[0]['image_sha256'] = '0' * 64
        self.write('inputs/evidence_units.jsonl', self.units)
        self.assertIn('course_image_binding_invalid', self.qualify()['blocking_items'])

    def test_structure_requires_original_unit_hash_and_valid_node_references(self):
        self.structures[0]['evidence_sha'] = '0' * 64
        self.structures[1]['relations'] = [{'source': 'N1', 'target': 'unknown'}]
        self.write('inputs/draft_structures.jsonl', self.structures)
        result = self.qualify()
        self.assertIn('course_structure_binding_invalid', result['blocking_items'])
        self.assertIn('course_structure_invalid', result['blocking_items'])

    def test_evaluation_identity_matches_main_exactly_and_contains_no_output_answers(self):
        self.write('evaluation/gold.jsonl', [{'question_id': 'wrong', 'answer': 'SECRET-GOLD-CONTENT'}], 'evaluation')
        result = self.qualify()
        self.assertIn('course_evaluation_identity_mismatch', result['blocking_items'])
        self.assertNotIn('SECRET-GOLD-CONTENT', json.dumps(result))

    def test_model_metadata_without_weights_blocks(self):
        self.manifest['resources'] = [r for r in self.manifest['resources'] if not r['path'].endswith('.safetensors')]
        self.assertIn('course_model_binding_incomplete', self.qualify()['blocking_items'])

    def test_verified_resources_are_a_binding_not_an_unchecked_override(self):
        verified = [dict(r, status='ready') for r in self.manifest['resources']]
        verified[0]['sha256'] = '0' * 64
        result = self.qualify(verified_resources=verified)
        self.assertIn('course_resource_unverified', result['blocking_items'])

    def test_human_review_policy_does_not_gain_automatic_qualification(self):
        self.manifest['review_policy'] = 'human_reviewed'
        result = self.qualify()
        self.assertFalse(result['applicable'])
        self.assertFalse(result['passed'])
        self.assertEqual(result['qualification'], {})

    def test_auxiliary_qids_are_not_a_second_role_but_must_be_main_subset(self):
        self.write('inputs/cohorts.json', {'main': [q['question_id'] for q in self.questions],
                                         's50': [q['question_id'] for q in self.questions[:49]] + ['outside']})
        self.assertIn('course_s50_invalid', self.qualify()['blocking_items'])

    def test_development_and_validation_if_present_must_have_50_disjoint_sources(self):
        extra = [{'question_id': f'dev{i}', 'doc_id': f'devdoc{i}', 'source_id': 's0',
                  'source_report': 's0_2020.pdf', 'question': 'Revenue?', 'split': 'development'} for i in range(50)]
        self.questions += extra
        self.write('inputs/inference_questions.jsonl', self.questions)
        self.write('inputs/cohorts.json', {'main': [f'q{i}' for i in range(200)],
                                         'development': [q['question_id'] for q in extra]})
        self.assertIn('course_source_overlap', self.qualify()['blocking_items'])

    def test_source_report_is_the_source_identity_when_source_id_is_absent(self):
        for q in self.questions:
            q.pop('source_id')
        self.write('inputs/inference_questions.jsonl', self.questions)
        self.assertTrue(self.qualify()['passed'])

    def test_normalized_answer_key_cannot_enter_blind_inputs(self):
        self.questions[0]['annotation'] = {'Answer': 'SECRET-ANSWER'}
        self.write('inputs/inference_questions.jsonl', self.questions)
        self.assertIn('course_inference_sensitive_fields', self.qualify()['blocking_items'])

    def test_malformed_optional_documents_return_a_block_instead_of_crashing(self):
        self.write('inputs/documents.jsonl', [{'doc_id': 'd0', 'pdf_path': ['invalid']}])
        self.assertIn('course_document_asset_missing', self.qualify()['blocking_items'])

    def test_pdf_content_aliases_cannot_cross_main_and_development(self):
        dev = [{'question_id': f'dev{i}', 'doc_id': f'devdoc{i}', 'source_id': 'unseen-source',
                'source_report': 'unseen-source_2020.pdf', 'question': 'Revenue?', 'split': 'development'} for i in range(50)]
        self.write('inputs/inference_questions.jsonl', self.questions + dev)
        self.write('inputs/cohorts.json', {'main': [f'q{i}' for i in range(200)],
                                         'development': [q['question_id'] for q in dev]})
        self.write('inputs/source.pdf', b'%PDF-1.4 shared-source-content', 'pdf')
        self.write('inputs/documents.jsonl', [{'doc_id': q['doc_id'], 'source_id': q['source_id'],
                                              'pdf_path': 'inputs/source.pdf'} for q in self.questions + dev])
        self.assertIn('course_source_content_overlap', self.qualify()['blocking_items'])

    def test_main_count_failure_is_independent_of_declared_manifest_gate(self):
        self.questions.pop()
        self.write('inputs/inference_questions.jsonl', self.questions)
        self.assertIn('course_main_cohort_invalid', self.qualify()['blocking_items'])

    def test_unit_source_report_cannot_contradict_question_document(self):
        self.units[0]['source_report'] = 'different-company_2020.pdf'
        self.write('inputs/evidence_units.jsonl', self.units)
        self.assertIn('course_evidence_source_mismatch', self.qualify()['blocking_items'])

    def test_malformed_asset_references_fail_closed_without_raw_values(self):
        self.units[0]['image_path'] = ['SECRET-REFERENCE']
        self.write('inputs/evidence_units.jsonl', self.units)
        result = self.qualify()
        self.assertFalse(result['passed'])
        self.assertIn('course_image_binding_invalid', result['blocking_items'])
        self.assertNotIn('SECRET-REFERENCE', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
