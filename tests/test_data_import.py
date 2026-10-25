import json
import tempfile
import unittest
from pathlib import Path
from ragpilot.common import read_jsonl
from ragpilot.data import prepare

class ImportTests(unittest.TestCase):
    def make_input(self, root, split, docid, report):
        from pypdf import PdfWriter
        p=root/'documents';p.mkdir(exist_ok=True)
        w=PdfWriter();w.add_blank_page(width=200,height=200)
        with (p/f'{docid}.pdf').open('wb') as f: w.write(f)
        (p/f'{docid}.json').write_text(json.dumps({'pages':[{'bbox':[0,0,200,200],'blocks':[{'uuid':docid+'b','bbox':[1,1,100,20],'text':'Revenue: $5','order':0}]}]}))
        q={'uid':docid+'q','question':'What is revenue?','answer':['$5'],'answer_type':'span','req_comparison':False,'scale':'','block_mapping':[{docid+'b':[9,10]}]}
        return {'doc':{'uid':docid,'page':1,'source':report},'questions':[q]}

    def setUp(self):
        try: import pypdf
        except ImportError: self.skipTest('optional pypdf not installed for real-import fixture')

    def test_report_disjoint_and_gold_separate(self):
        with tempfile.TemporaryDirectory() as path:
            r=Path(path)
            train=self.make_input(r,'train','train1','same-report')
            same=self.make_input(r,'dev','dev1','same-report')
            other=self.make_input(r,'dev','dev2','different-report')
            (r/'train.json').write_text(json.dumps([train]));(r/'dev.json').write_text(json.dumps([same,other]))
            manifest=prepare({'train':r/'train.json','dev':r/'dev.json'},r/'documents',r/'out',dev_docs=1,eval_docs=1)
            self.assertEqual(manifest['actual']['documents'],2)
            selected={d['doc_id'] for d in manifest['selected_documents']}
            self.assertEqual(selected,{'train1','dev2'})
            qs=read_jsonl(r/'out'/'inference_questions.jsonl')
            self.assertFalse(any('answer' in q or 'block_mapping' in q for q in qs))
            self.assertTrue(all(g['mapping_verified'] is False for g in read_jsonl(r/'out'/'evaluation_gold.jsonl')))
            with self.assertRaises(ValueError): prepare({'train':r/'train.json'},r/'documents',r/'out',dev_docs=1,eval_docs=0)

    def test_missing_comparison_flag_is_excluded(self):
        with tempfile.TemporaryDirectory() as path:
            r=Path(path);record=self.make_input(r,'train','d','r');del record['questions'][0]['req_comparison']
            (r/'train.json').write_text(json.dumps([record]))
            with self.assertRaises(ValueError): prepare({'train':r/'train.json'},r/'documents',r/'out',dev_docs=1,eval_docs=0)
