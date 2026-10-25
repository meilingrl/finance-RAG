import tempfile
import unittest
from collections import Counter
from pathlib import Path
from formal_src import acquisition as a

class AcquisitionTests(unittest.TestCase):
    def records(self):
        return [{'doc':{'uid':f'{s}-{p}','source':f'{s}_2020.pdf','page':p},'questions':[{'uid':f'{s}-{p}-{q}','question':'ordinary value?','answer_type':'span','req_comparison':False,'answer':'ORIGINAL','scale':'million'} for q in range(5)]} for s in ('alpha','beta') for p in range(10)]
    def test_selection_preserves_quota_and_is_deterministic(self):
        rows=a.select_questions(self.records(),['alpha','beta'],target=30,role='main')
        self.assertEqual(len(rows),30)
        self.assertEqual(rows,a.select_questions(self.records(),['alpha','beta'],target=30,role='main'))
        self.assertEqual({r['source_id'] for r in rows},{'alpha','beta'})
        self.assertLessEqual(max(Counter(r['doc_id'] for r in rows).values()),3)
        self.assertLessEqual(max(Counter(r['source_id'] for r in rows).values()),20)
        self.assertNotIn('answer',rows[0])
    def test_existing_rows_count_toward_shared_test_quota(self):
        main=a.select_questions(self.records(),['alpha','beta'],target=30,role='main')
        extra=a.select_questions(self.records(),['alpha','beta'],target=30,role='diagnostic',existing=main)
        allrows=main+extra
        self.assertEqual(len(allrows),40)
        self.assertLessEqual(max(Counter(r['doc_id'] for r in allrows).values()),3)
        self.assertFalse({r['question_id'] for r in main}&{r['question_id'] for r in extra})
    def test_diagnostic_never_substitutes_ordinary_questions(self):
        self.assertEqual(a.diagnostic_category({'question':'ordinary value?'},[]),None)
        self.assertEqual(a.diagnostic_category({'question':'What does footnote (a) explain?'},[]),'footnote')
    def test_gold_split_keeps_original_values(self):
        original=self.records()[0]
        inputs=a.select_questions([original],['alpha'],target=1,role='development')
        gold=a.evaluation_records([original],inputs)
        self.assertEqual(gold[0]['answer'],'ORIGINAL');self.assertEqual(gold[0]['scale'],'million')
        self.assertNotIn('answer',inputs[0])

    def test_blind_builder_uses_only_documents_and_ocr(self):
        import fitz
        import json
        from scripts.prepare_formal import builder,readl
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'inputs/pdf').mkdir(parents=True);(root/'inputs/ocr').mkdir()
            with fitz.open() as pdf:
                page=pdf.new_page(width=200,height=200);page.insert_text((20,30),'Revenue 12');pdf.save(root/'inputs/pdf/d.pdf')
            (root/'inputs/ocr/d.json').write_text(json.dumps({'pages':[{'bbox':[0,0,200,200],'blocks':[{'uuid':'b','bbox':[10,10,190,50],'text':'Revenue 12'}]}]}))
            doc={'doc_id':'d','source_report':'alpha_2020.pdf','source_id':'alpha','page':1,'pdf_path':'inputs/pdf/d.pdf','ocr_path':'inputs/ocr/d.json'}
            (root/'inputs/documents.jsonl').write_text(json.dumps(doc)+'\n')
            # There are deliberately no inference questions or evaluation assets.
            self.assertGreater(builder(root),0)
            units=readl(root/'inputs/evidence_units.jsonl')
            self.assertTrue((root/units[0]['image_path']).exists())
            self.assertFalse(units[0]['visual_verified'])
            self.assertNotIn('question_id',units[0])

    def test_duplicate_asset_cross_role_is_reported(self):
        audit=a.audit_identity([{'doc_id':'a','source_id':'one','split':'development','pdf_sha256':'same'},{'doc_id':'b','source_id':'two','split':'main','pdf_sha256':'same'}])
        self.assertEqual(audit['cross_role_duplicate_assets'],[['a','b']])
        self.assertFalse(audit['human_alias_audit_complete'])

    def test_packaged_source_protocol_needs_no_planning_artifact(self):
        from scripts.prepare_formal import load_protocol
        protocol=load_protocol()
        self.assertEqual(protocol['seed'],20261023)
        self.assertEqual({k:len(v) for k,v in protocol['source_pools'].items()},{'development':30,'validation':30,'test':90})
        self.assertEqual(len(protocol['known_exposed_source_reports']),20)
        self.assertNotIn('questions',protocol)

    def test_diagnostic_handoff_is_gold_free_unclassified_and_quota_bounded(self):
        records=self.records();main=a.select_questions(records,['alpha','beta'],target=30,role='main')
        queue=a.diagnostic_queue(records,['alpha','beta'],main,documents={})
        self.assertEqual(len(queue),10)
        self.assertTrue(all(x['qualification_status']=='human_qualification_pending' and x['certified_category'] is None for x in queue))
        self.assertTrue(all('answer' not in x and 'scale' not in x for x in queue))
        self.assertLessEqual(max(Counter(x['doc_id'] for x in main+queue).values()),3)
        self.assertLessEqual(max(Counter(x['source_id'] for x in main+queue).values()),20)
    def test_question_aware_curator_does_not_generate_structure(self):
        import ast
        tree=ast.parse(Path(a.__file__).read_text())
        imports=[n.module or '' for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        self.assertFalse(any('structure' in n for n in imports))
        self.assertIsNone(a.diagnostic_category({'question':'Which header applies?'},[]))
