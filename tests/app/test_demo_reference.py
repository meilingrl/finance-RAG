"""Reference answers are post-seal display data, never inference inputs."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest


class DemoReferenceTests(unittest.TestCase):
    def test_unsealed_run_cannot_read_gold(self):
        from app.demo_reference import reference_for_run
        from formal_src.verification import VerificationBlocked
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            with self.assertRaisesRegex(VerificationBlocked,'source_not_sealed'):
                reference_for_run(root,root)

    def fixture(self, root, *, custom=False, conflict=False):
        from tests.formal.test_runner import demo_fixture, fake_response
        from formal_src.runner import run
        spec,rd=demo_fixture(root)
        if custom:spec['demo_input']['question']='A different question?'
        (rd/'spec.json').write_text(json.dumps(spec))
        result=run(spec,lambda e:None,lambda:'continue',transport=lambda _,p,*args:fake_response(p),run_dir=rd)
        self.assertEqual(result['state'],'completed')
        d=root/'data/phase2/development';d.mkdir(parents=True)
        def write(name,value):
            p=d/name;p.write_text(json.dumps(value)+('\n' if name.endswith('jsonl') else ''))
            return hashlib.sha256(p.read_bytes()).hexdigest()
        qsha=write('questions.jsonl',dict(question_id='q',doc_id='doc',split='development',question='How long?'))
        gold=dict(question_id='q',doc_id='doc',split='development',answer=['3'],scale='')
        gsha=write('gold.jsonl',gold)
        rsha=write('gold_semantic_verified.jsonl',{**gold,'annotation_conflict':conflict,'semantic_review':{'notes':'Source-column conflict' if conflict else 'Source agrees','reviewer':'test reviewer'}})
        write('gold_manifest.json',{'development':{'path':'data/phase2/development/gold.jsonl','sha256':gsha}})
        write('semantic_mapping_review.json',{'original_gold_sha256':gsha,'questions_sha256':qsha,'verified_gold_sha256':rsha})
        return rd,d

    def test_original_answer_numeric_hint_and_separate_scale(self):
        from app.demo_reference import reference_for_run
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);rd,_=self.fixture(root)
            report=reference_for_run(root,rd)
            self.assertTrue(report['available'])
            self.assertEqual(report['answer'],['3'])
            self.assertEqual(report['scale'],'')
            generated=[r for r in report['comparisons'] if r['condition']=='E2'][0]
            self.assertFalse(generated['text_match'])
            self.assertTrue(generated['numeric_match'])
            self.assertTrue(generated['scale_match'])
            self.assertEqual(generated['comparison'],'numeric_only')
            self.assertNotIn('gold.jsonl',(rd/'inputs.json').read_text())

    def test_custom_question_gets_no_unrelated_standard_answer(self):
        from app.demo_reference import reference_for_run
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);rd,_=self.fixture(root,custom=True)
            self.assertFalse(reference_for_run(root,rd)['available'])

    def test_annotation_conflict_is_not_a_wrong_answer_verdict(self):
        from app.demo_reference import reference_for_run
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);rd,_=self.fixture(root,conflict=True)
            report=reference_for_run(root,rd)
            self.assertTrue(report['annotation_conflict'])
            self.assertEqual(report['review_notes'],'Source-column conflict')
            self.assertTrue(all(r['comparison']=='annotation_conflict' for r in report['comparisons']))

    def test_tampered_gold_is_not_displayed(self):
        from app.demo_reference import reference_for_run
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);rd,d=self.fixture(root)
            (d/'gold.jsonl').write_text('{}\n')
            with self.assertRaisesRegex(ValueError,'reference_source_changed'):
                reference_for_run(root,rd)
