"""Breaks caught: report leakage and silently overfull page/source quotas."""
import importlib
import unittest

class DataProtocolTests(unittest.TestCase):
    def module(self):
        try:return importlib.import_module('formal_src.data_protocol')
        except ModuleNotFoundError:self.fail('formal data protocol is not implemented')
    def records(self):
        result={}
        for role,count in [('development',50),('validation',50),('main',200),('diagnostic',50)]:
            result[role]=[{'question_id':f'{role}-{i}','doc_id':f'{role}-page-{i}',
                          'source_id':f'test-{i%40}' if role in {'main','diagnostic'} else f'{role}-source-{i%30}',
                          'question':'An original question?'} for i in range(count)]
        return result
    def test_source_isolation_and_s50_is_frozen_main_subset(self):
        module=self.module();records=self.records()
        out=module.validate_cohorts(records)
        self.assertEqual(out['counts'],{'development':50,'validation':50,'main':200,'diagnostic':50})
        self.assertEqual(out['source_counts']['main'],40)
        subset=module.select_auxiliary(records['main'])
        self.assertEqual(len(subset),50)
        self.assertEqual(len({r['source_id'] for r in subset}),40)
        records['validation'][0]['source_id']=records['main'][0]['source_id']
        with self.assertRaises(ValueError):module.validate_cohorts(records)
    def test_duplicate_question_page_overflow_and_exposed_source_rejected(self):
        module=self.module()
        records=self.records();records['diagnostic'][0]['question_id']=records['main'][0]['question_id']
        with self.assertRaises(ValueError):module.validate_cohorts(records)
        records=self.records()
        for row in records['main'][:4]:row['doc_id']='same-page'
        with self.assertRaises(ValueError):module.validate_cohorts(records)
        records=self.records()
        with self.assertRaises(ValueError):module.validate_cohorts(records,exposed_sources={'test-0'})
    def test_input_records_never_contain_gold_fields(self):
        records=self.records();records['main'][0]['answer']='secret'
        with self.assertRaises(ValueError):self.module().validate_cohorts(records)

if __name__=='__main__':unittest.main()
