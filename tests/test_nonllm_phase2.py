import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ragpilot.common import digest, file_sha, read_json, write_json, write_jsonl
from ragpilot.evidence import build_units, normalize_page
from ragpilot.regions import propose_regions, spatial_lines
from ragpilot.rules import extract
from ragpilot.pipeline import make_fixture, run_offline, freeze_nonllm, validate_freeze, DEFAULT_CONFIG

class RegionTests(unittest.TestCase):
    def block(self,id,text,y,words=None):
        return {'uuid':id,'text':text,'order':y,'bbox':[10,y,490,y+20],**({'words':words} if words else {})}
    def test_word_serialization_lossless_with_tabs(self):
        b=self.block('b','Revenue 2019 2018',20,{'word_list':['Revenue','2019','2018'],'bbox_list':[[10,20,80,40],[300,20,350,40],[400,20,450,40]]})
        lines,status=spatial_lines(b)
        self.assertEqual(status,'lossless_official_words');self.assertEqual(lines[0]['text'],'Revenue\t2019\t2018')
        self.assertEqual(lines[0]['word_indices'],[0,1,2])
    def test_unreconciled_words_fall_back_without_dropping_text(self):
        b=self.block('b','Revenue 2019 2018',20,{'word_list':['Revenue'],'bbox_list':[[10,20,80,40]]})
        lines,status=spatial_lines(b);self.assertEqual(lines[0]['text'],b['text'])
        self.assertEqual(status,'official_block_fallback')
    def test_table_rows_are_not_split(self):
        blocks=[self.block('heading','Cash and revenue (in millions)',10),self.block('header','2019 2018',45),
                self.block('row1','Revenue 5 6 7 8',75),self.block('row2','Cash 9 10 11 12',105),
                self.block('prose','This paragraph explains the company business and operating policy in some detail with no figures.',250)]
        page={'pages':[{'bbox':[0,0,500,500],'blocks':blocks}]}
        units=build_units({'doc_id':'d','source_report':'r','page':1},page,segmentation='blind_blocks')
        self.assertGreaterEqual(len(units),2)
        table=next(u for u in units if 'row1' in u['word_block_refs'])
        self.assertTrue({'heading','header','row1','row2'}<=set(table['word_block_refs']))
        self.assertEqual({b for u in units for b in u['word_block_refs']},{b['uuid'] for b in blocks})
    def test_region_builder_rejects_gold(self):
        with self.assertRaises(ValueError):propose_regions({'answer':'5'})
    def test_region_constructor_has_no_question_input(self):
        import inspect
        self.assertEqual(list(inspect.signature(propose_regions).parameters),['page'])
    def test_unknown_segmentation_rejected(self):
        with self.assertRaises(ValueError):build_units({'doc_id':'d','source_report':'r','page':1},{'pages':[{'bbox':[0,0,500,500],'blocks':[self.block('b','text',20)]}]},segmentation='random')

class RuleTests(unittest.TestCase):
    def result(self,q,text):return extract(q,[('e1',text)])[0]
    def test_year_column_selection(self):
        r=self.result('What was net revenue in 2018?','(in millions)\n2019\t2018\nNet revenue\t5\t6')
        self.assertEqual((r['value'],r['scale']),('6','million'))
    def test_wrong_year_abstains(self):
        self.assertIsNone(self.result('What was net revenue in 2017?','2019\t2018\nNet revenue\t5\t6'))
    def test_duplicate_year_headers_abstain(self):
        self.assertIsNone(self.result('What was revenue in 2018?','2019\t2018\t2019\t2018\nRevenue\t5\t6\t7\t8'))
    def test_different_equally_good_values_abstain(self):
        self.assertIsNone(self.result('What was revenue in 2019?','Revenue in 2019: 5\nRevenue in 2019: 6'))
    def test_single_year_prose(self):
        r=self.result('What was net revenue in 2019?','Net revenue in 2019 was $5 million.')
        self.assertEqual(r['value'],'$5 million')
    def test_respectively_year_value(self):
        r=self.result('What was net revenue in 2018?','Net revenue was $5 million and $6 million in 2019 and 2018, respectively.')
        self.assertEqual(r['value'],'$6 million')
    def test_multiple_year_question_abstains(self):
        self.assertIsNone(self.result('What was revenue in 2019 and 2018?','2019\t2018\nRevenue\t5\t6'))
    def test_no_keyword_no_answer(self):
        self.assertIsNone(self.result('What was profit in 2019?','Revenue in 2019: 5'))

class IsolationTests(unittest.TestCase):
    def test_development_does_not_open_evaluation_gold(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);make_fixture(root/'data')
            gold=root/'data'/'evaluation_gold.jsonl';dev=root/'dev.jsonl';gold.rename(dev)
            m=read_json(root/'data'/'data_manifest.json');m.pop('evaluation_hash')
            m['evaluation_sources']={'development':{'path':str(dev),'sha256':file_sha(dev)},'pilot_evaluation':{'path':str(root/'never-open'),'sha256':'invalid'}}
            write_json(root/'data'/'data_manifest.json',m)
            result=run_offline(root/'data',root/'run',split='development')
            self.assertEqual(result['status'],'offline_complete')
    def test_freeze_hash_guards_source_and_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);make_fixture(root/'data');run_offline(root/'data',root/'dev',split='development')
            freeze_nonllm(root/'data',[root/'dev'],[DEFAULT_CONFIG],root/'freeze.json')
            validate_freeze(root/'freeze.json',root/'data',DEFAULT_CONFIG)
            with self.assertRaises(ValueError):validate_freeze(root/'freeze.json',root/'data',{**DEFAULT_CONFIG,'k':1})
            with patch('ragpilot.pipeline.source_fingerprint',return_value={'tampered':'bytes'}):
                with self.assertRaises(ValueError):validate_freeze(root/'freeze.json',root/'data',DEFAULT_CONFIG)
    def test_freeze_rejects_evaluation_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);make_fixture(root/'data');run_offline(root/'data',root/'run')
            with self.assertRaises(ValueError):freeze_nonllm(root/'data',[root/'run'],[DEFAULT_CONFIG],root/'freeze.json')

class SupportScopeTests(unittest.TestCase):
    def test_header_only_is_not_answer_support(self):
        from ragpilot.metrics import evaluate
        units=[{'evidence_id':'header','doc_id':'d','word_block_refs':['h']},{'evidence_id':'row','doc_id':'d','word_block_refs':['r']}]
        gold=[{'question_id':'q','doc_id':'d','answer':'5','scale':'','support_block_ids':['r'],'complete_support_block_ids':['r','h'],'mapping_verified':True,'support_complete':True}]
        r={'question_id':'q','doc_id':'d','retriever':'bm25','config_hash':'cfg','selected_ids':['header'],'candidate_ids':['header','row']}
        metric=evaluate([],gold,[r],units)['retrieval_rows'][0]
        self.assertFalse(metric['any_support_hit']);self.assertFalse(metric['complete_support_coverage'])
        r['selected_ids']=['row'];metric=evaluate([],gold,[r],units)['retrieval_rows'][0]
        self.assertTrue(metric['any_support_hit']);self.assertFalse(metric['complete_support_coverage'])
        r['selected_ids']=['row','header'];metric=evaluate([],gold,[r],units)['retrieval_rows'][0]
        self.assertTrue(metric['complete_support_coverage'])

class AdditionalRuleRegression(unittest.TestCase):
    def test_accounting_negative_small_value_is_preserved(self):
        r=extract('What was profit in 2019?',[('e','2019\t2018\nProfit\t(7)\t5')])[0]
        self.assertEqual(r['value'],'(7)')
    def test_named_column_uses_column_heading_not_first_number(self):
        text='The amortized cost and fair value of investments as of June 30, 2019 are as follows:\nCost\tFair Value\n(in thousands)\nDue after five years\t41,612\t41,756'
        r=extract('What was fair value of investments due after five years as of June 30, 2019?',[('e',text)])[0]
        self.assertEqual(r['value'],'41,756');self.assertEqual(r['scale'],'thousand')
    def test_extra_change_columns_require_explicit_header(self):
        text='Years ended December 31,\t2019\t2018\nIncrease/(Decrease)\n2019 vs. 2018\nCost of services\t$ 10,655\t$ 10,859\t$ (204)\t(1.9)%'
        r=extract('How much was cost of services in 2019?',[('e',text)])[0]
        self.assertEqual(r['value'],'$10,655')
        self.assertIsNone(extract('How much was cost of services in 2019?',[('e',text.replace('Increase/(Decrease)','').replace('2019 vs. 2018',''))])[0])

class YearSectionRegression(unittest.TestCase):
    def test_named_column_does_not_reuse_prior_year(self):
        text='2019\nCost\tFair value\nCash\t1\t2\n2018\nBonds\t3\t4'
        self.assertIsNone(extract('What was fair value of bonds in 2019?',[('e',text)])[0])
    def test_multicolumn_pages_fall_back_without_question_input(self):
        blocks=[]
        for i,(x,y) in enumerate([(10,40),(10,100),(270,40),(270,100)]):
            blocks.append({'uuid':str(i),'text':'A long ordinary paragraph with more than fifteen words that describes only generic financial information without providing any question specific guidance.','bbox':[x,y,x+200,y+55],'order':i})
        units=build_units({'doc_id':'d','source_report':'r','page':1},{'pages':[{'bbox':[0,0,500,500],'blocks':blocks}]},segmentation='blind_blocks')
        self.assertEqual(len(units),1);self.assertTrue(units[0]['whole_page'])

class ProposalAndRotationRegression(unittest.TestCase):
    def test_overlapping_proposals_do_not_launder_exposure(self):
        page={'pages':[{'bbox':[0,0,500,500],'blocks':[{'uuid':'b','text':'Revenue','bbox':[10,10,100,40],'order':1}]}]}
        regions=[{'region':[10,10,100,40],'source':'manual','question_exposure':exposed} for exposed in (True,False)]
        with self.assertRaises(ValueError):build_units({'doc_id':'d','source_report':'r','page':1},page,regions=regions,segmentation='blind_blocks')
    def test_vertical_sidebar_keeps_original_order(self):
        b={'text':'FINANCIAL STATEMENTS','bbox':[10,10,30,200],'words':{'word_list':['FINANCIAL','STATEMENTS'],'bbox_list':[[10,110,30,200],[10,10,30,100]]}}
        lines,status=spatial_lines(b);self.assertEqual(lines[0]['text'],'FINANCIAL STATEMENTS')
        self.assertEqual(status,'rotated_or_vertical_block_official_order')
