"""Breaks caught: automatic checks masquerading as completed human review."""
import importlib
import unittest

class AuditTests(unittest.TestCase):
    def module(self):
        try:return importlib.import_module('formal_src.audit')
        except ModuleNotFoundError:self.fail('manual audit planning not implemented')

    def test_prespecified_sampling_is_blind_and_does_not_approve_itself(self):
        main=[{'question_id':f'm{i}','source_id':f's{i%40}'} for i in range(200)]
        diagnostic=[{'question_id':f'd{i}','source_id':f's{i%40}'} for i in range(50)]
        regions=[{'evidence_id':f'e{i}','relation_ids':[f'e{i}-r{j}' for j in range(8)]} for i in range(40)]
        module=self.module()
        plan=module.build_audit_plan(main,diagnostic,regions)
        self.assertEqual(len(plan['main_question_ids']),30)
        self.assertEqual(len(plan['diagnostic_question_ids']),15)
        self.assertEqual(len(plan['answer_items']),225)
        self.assertEqual(len(plan['relation_items']),150)
        self.assertEqual(len(plan['second_review_ids']),57)
        status=module.audit_status(plan,[])
        self.assertFalse(status['audit_complete'])
        self.assertEqual(status['missing_initial'],375)
        self.assertEqual(plan,module.build_audit_plan(main,diagnostic,regions))
        main[0]['answer']='gold must not enter blind sampling'
        with self.assertRaises(ValueError):module.build_audit_plan(main,diagnostic,regions)

    def test_independent_second_review_and_disputes_are_required(self):
        module=self.module()
        plan={'answer_items':[{'item_id':'a'}],'relation_items':[], 'second_review_ids':['a'], 'blocking_items':[]}
        first={'item_id':'a','reviewer_id':'one','verdict':'supported','stage':'initial','reason':'Compared cited evidence'}
        initial_status=module.audit_status(plan,[first])
        self.assertFalse(initial_status['audit_complete'])
        self.assertEqual(initial_status['final_verdict_counts'],{})
        duplicate={**first,'stage':'second'}
        with self.assertRaises(ValueError):module.audit_status(plan,[first,duplicate])
        disagree={**first,'reviewer_id':'two','stage':'second','verdict':'unsupported'}
        disputed_status=module.audit_status(plan,[first,disagree])
        self.assertFalse(disputed_status['audit_complete'])
        self.assertEqual(disputed_status['final_verdict_counts'],{})
        resolution={**first,'reviewer_id':'three','stage':'resolution','verdict':'supported','reason':'Compared original evidence'}
        status=module.audit_status(plan,[first,disagree,resolution])
        self.assertTrue(status['audit_complete'])
        self.assertEqual(status['disagreement_count'],1)
        self.assertEqual(status['resolved_disputes'],1)
        self.assertEqual(status['final_verdict_counts'],{'supported':1})

    def test_empty_structure_regions_still_require_a_human_coverage_review(self):
        main=[{'question_id':f'm{i}','source_id':'s'} for i in range(200)]
        diagnostic=[{'question_id':f'd{i}','source_id':'s'} for i in range(50)]
        regions=[{'evidence_id':f'e{i}','relation_ids':[]} for i in range(30)]
        plan=self.module().build_audit_plan(main,diagnostic,regions)
        self.assertEqual(len(plan['relation_items']),30)
        self.assertTrue(all(r['kind']=='coverage_only' for r in plan['relation_items']))
        self.assertFalse(self.module().audit_status(plan,[])['audit_complete'])

    def test_human_verdict_without_reason_is_not_a_completed_review(self):
        plan={'answer_items':[{'item_id':'a'}],'relation_items':[],'second_review_ids':[],'blocking_items':[]}
        with self.assertRaises(ValueError):
            self.module().audit_status(plan,[{'item_id':'a','reviewer_id':'one','verdict':'supported','stage':'initial'}])

    def test_unsolicited_resolution_cannot_replace_an_agreed_verdict(self):
        plan={'answer_items':[{'item_id':'a'}],'relation_items':[],'second_review_ids':[],'blocking_items':[]}
        initial={'item_id':'a','reviewer_id':'one','verdict':'supported','stage':'initial','reason':'Evidence supports answer'}
        override={**initial,'verdict':'unsupported','stage':'resolution'}
        with self.assertRaises(ValueError):self.module().audit_status(plan,[initial,override])
        second={**initial,'reviewer_id':'two','stage':'second'}
        override['reviewer_id']='three'
        with self.assertRaises(ValueError):self.module().audit_status(plan,[initial,second,override])

if __name__=='__main__':unittest.main()
