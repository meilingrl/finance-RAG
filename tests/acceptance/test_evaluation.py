"""Breaks caught: unit erasure, dropping failures, and scoring unfinished runs."""
import importlib
import unittest


class EvaluationTests(unittest.TestCase):
    def scorer(self):
        try:
            return importlib.import_module('formal_src.evaluation')
        except ModuleNotFoundError:
            self.fail('formal evaluation is not implemented')

    def test_currency_spacing_equivalent_but_missing_currency_is_wrong(self):
        score = self.scorer().uem_score
        gold = {'answer': '$4,143', 'scale': 'million'}
        self.assertEqual(score({'answer': '$ 4,143', 'scale': 'million', 'status': 'answered'}, gold)['uem'], 1)
        self.assertEqual(score({'answer': '4,143', 'scale': 'million', 'status': 'answered'}, gold)['uem'], 0)

    def test_percent_scale_conflict_and_semantic_words_cannot_be_erased(self):
        score = self.scorer().uem_score
        cases = [
            ({'answer':'5','scale':''}, {'answer':'5%','scale':''}, 0),
            ({'answer':'5 million','scale':'thousand'}, {'answer':'5','scale':'million'}, 0),
            ({'answer':'$-5','scale':''}, {'answer':'$5','scale':''}, 0),
            ({'answer':'the company operates','scale':''}, {'answer':'where the company operates','scale':''}, 0),
            ({'answer':'Software services.','scale':''}, {'answer':'software services','scale':''}, 1),
        ]
        for pred,gold,want in cases:
            with self.subTest(pred=pred):
                self.assertEqual(score({**pred,'status':'answered'}, gold)['uem'],want)

    def test_abstention_and_failure_stay_in_denominator(self):
        ev = self.scorer()
        gold=[{'question_id':'a','answer':'5','scale':'','source_id':'source-a'},
              {'question_id':'b','answer':'6','scale':'','source_id':'source-b'}]
        plan=[{'task_id':'t1','question_id':'a','partition':'main','condition':'E2','retriever':'bm25','model_role':'primary','repeat':1},
              {'task_id':'t2','question_id':'b','partition':'main','condition':'E2','retriever':'bm25','model_role':'primary','repeat':1}]
        records=[{**plan[0],'status':'answered','answer':'5','scale':''},
                 {**plan[1],'status':'abstained','answer':None,'scale':''}]
        result=ev.evaluate_predictions(records,gold,plan,sealed=True)
        metric=result['metrics']['main|bm25|E2|primary']
        self.assertEqual(metric['question_count'],2)
        self.assertEqual(metric['uem'],0.5)
        self.assertEqual(metric['status_counts']['abstained'],1)
        records[1]['status']='failed'
        result=ev.evaluate_predictions(records,gold,plan,sealed=True)
        self.assertEqual(result['metrics']['main|bm25|E2|primary']['uem'],0.5)
        self.assertFalse(result['audit_complete'])

    def test_incomplete_duplicate_or_unsealed_inputs_cannot_make_main_table(self):
        ev=self.scorer()
        plan=[{'task_id':'x','question_id':'a','partition':'main','condition':'E1','retriever':'bm25','model_role':'local','repeat':1}]
        gold=[{'question_id':'a','answer':'5','scale':'','source_id':'s'}]
        record={**plan[0],'status':'answered','answer':'5','scale':''}
        for records,sealed in [([],True),([record,record],True),([record],False)]:
            with self.subTest(records=len(records),sealed=sealed):
                with self.assertRaises(ValueError):ev.evaluate_predictions(records,gold,plan,sealed=sealed)

    def test_parallel_answer_only_f1_is_retained(self):
        result=self.scorer().uem_score({'answer':'software','scale':'','status':'answered'}, {'answer':'software services','scale':''})
        self.assertIn('answer_only_f1',result)
        self.assertAlmostEqual(result['answer_only_f1'],2/3)

if __name__=='__main__':unittest.main()
