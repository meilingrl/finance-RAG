import copy
import tempfile
import unittest
from pathlib import Path
from ragpilot.common import digest, file_sha, reject_gold
from ragpilot.evidence import build_units, descriptions, close_region, normalize_page
from ragpilot.inference import freeze_retrieval, selected_units, run_e1, prepare_requests
from ragpilot.retrieval import retrieve

class Boundaries(unittest.TestCase):
    def setUp(self):
        self.page = {"pages": [{"bbox": [0,0,500,500], "blocks": [
            {"uuid":"b1","bbox":[10,10,200,40],"order":1,"text":"Revenue in 2024: $5 million"},
            {"uuid":"b2","bbox":[10,45,200,80],"order":2,"text":"Cash in 2024: $8 million"}]}]}
        self.units = build_units({"doc_id":"d1","source_report":"r1","page":1},self.page)
        self.q = {"question_id":"q1","doc_id":"d1","question":"What was revenue in 2024?"}
        self.frozen = freeze_retrieval(self.q,self.units,retrieve(self.q,self.units,{"retriever":"bm25","k":2}))

    def test_gold_rejected_recursively(self):
        with self.assertRaises(ValueError): reject_gold({"x":[{"answer":"secret"}]})
        with self.assertRaises(ValueError): build_units({"doc_id":"d","answer":"secret"},self.page)
        with self.assertRaises(ValueError): descriptions([{**self.units[0],"facts":["secret"]}])

    def test_geometry_region_closure_includes_extra_text(self):
        blocks = normalize_page(self.page)["blocks"]
        region, selected = close_region([15,25,150,50],blocks)
        self.assertEqual(region,[10.0,10.0,200.0,80.0])
        self.assertEqual([b["block_id"] for b in selected],["b1","b2"])

    def test_whole_page_diagnostic_and_no_semantic_descriptions(self):
        self.assertTrue(self.units[0]["whole_page"])
        d = descriptions(self.units)[0]
        self.assertFalse(d["question_exposure"])
        self.assertNotIn("revenue",d["text"].lower())
        self.assertIn("above",d["text"])
        self.assertFalse(d["verified"])

    def test_retrieval_mutation_rejected(self):
        changed = copy.deepcopy(self.units); changed[0]["text"] += "changed"
        with self.assertRaises(ValueError): selected_units(self.q,changed,self.frozen)
        changed = copy.deepcopy(self.frozen);changed["selected_ids"] = []
        with self.assertRaises(ValueError): selected_units(self.q,self.units,changed)
        with self.assertRaises(ValueError): selected_units({**self.q,"question":"different"},self.units,self.frozen)

    def test_image_byte_mutation_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"test.png";p.write_bytes(b"original")
            units=copy.deepcopy(self.units);units[0]["image_path"]=str(p);units[0]["image_sha256"]=file_sha(p)
            frozen=freeze_retrieval(self.q,units,retrieve(self.q,units,{"retriever":"bm25","k":2}))
            p.write_bytes(b"changed")
            with self.assertRaises(ValueError): prepare_requests(self.q,units,frozen,descriptions(units))

    def test_e1_unique_value_and_abstention(self):
        record=run_e1(self.q,self.units,self.frozen)
        self.assertEqual(record["parsed_answer"],"$5 million")
        self.assertEqual(record["status"],"answered")
        q={**self.q,"question":"What was profit in 2024?"}
        frozen=freeze_retrieval(q,self.units,retrieve(q,self.units,{"retriever":"bm25","k":2}))
        self.assertEqual(run_e1(q,self.units,frozen)["status"],"abstained")

    def test_no_year_mismatch(self):
        q={**self.q,"question":"What was revenue in 2023?"}
        frozen=freeze_retrieval(q,self.units,retrieve(q,self.units,{"retriever":"bm25","k":2}))
        self.assertEqual(run_e1(q,self.units,frozen)["status"],"abstained")

    def test_request_pairing_and_disabled_calls(self):
        records=prepare_requests(self.q,self.units,self.frozen,descriptions(self.units),max_context_chars=1)
        self.assertEqual({r["group"] for r in records},{"E2","E3","E4"})
        self.assertEqual(len({r["shared_text_hash"] for r in records}),1)
        self.assertEqual(len({r["prompt_hash"] for r in records}),1)
        self.assertTrue(all(r["enabled"] is False and r["status"] == "not_run" for r in records))
        self.assertTrue(all(r["raw_response"] is None for r in records))
        self.assertTrue(all(r["context_status"] == "over_character_budget" and not r["truncated"] for r in records))
        self.assertEqual(records[0]["request_payload"]["image_refs"],[])
        self.assertEqual(records[0]["request_payload"]["description_attachments"],[])
        self.assertEqual(records[1]["request_payload"]["description_attachments"],[])

    def test_exposed_description_diagnostic_only(self):
        desc=descriptions(self.units);desc[0]["question_exposure"]=True
        e4=prepare_requests(self.q,self.units,self.frozen,desc)[2]
        self.assertIn("description_question_exposure_diagnostic_only",e4["blockers"])
        self.assertFalse(e4["eligible_main_comparison"])

    def test_region_proposal_must_be_question_blind(self):
        with self.assertRaises(ValueError): build_units({"doc_id":"d","source_report":"r","page":1},self.page,regions=[{"region":[0,0,500,500],"source":"manual","question_exposure":True}])

if __name__ == '__main__': unittest.main()

class FreezeAndAdapterRegression(unittest.TestCase):
    def test_selected_must_be_subset_of_complete_candidates(self):
        case=Boundaries();case.setUp()
        bad=retrieve(case.q,case.units,{"retriever":"bm25","k":2});bad["candidate_ids"]=[]
        with self.assertRaises(ValueError): freeze_retrieval(case.q,case.units,bad)

    def test_optional_encoder_unavailable_pipeline_is_logged(self):
        from ragpilot.pipeline import make_fixture, run_offline
        from ragpilot.common import read_jsonl
        with tempfile.TemporaryDirectory() as path:
            root=Path(path);make_fixture(root/'input')
            run=run_offline(root/'input',root/'output',{"retriever":"sentence_transformers","model_path":str(root/'absent-model')})
            self.assertEqual(run["status"],"offline_partial_retriever_unavailable")
            self.assertEqual(run["run_kind"],"synthetic_software_fixture")
            self.assertEqual(run["model_calls"],0)
            self.assertTrue(all(r["status"]=="unavailable" for r in read_jsonl(root/'output'/'retrieval.jsonl')))

    def test_original_question_data_never_in_description_interface(self):
        import inspect
        self.assertEqual(list(inspect.signature(descriptions).parameters),["units"])

class RenderingRegression(unittest.TestCase):
    def test_cropbox_offset_is_recorded_and_rendered(self):
        try:
            import fitz
        except ImportError:
            self.skipTest('optional PyMuPDF not installed')
        from ragpilot.evidence import render_units
        with tempfile.TemporaryDirectory() as path:
            root=Path(path);pdf=fitz.open();page=pdf.new_page(width=300,height=400)
            page.set_cropbox(fitz.Rect(20,30,280,370));pdf.save(root/'crop.pdf');pdf.close()
            raw={'pages':[{'bbox':[0,0,520,680],'blocks':[{'uuid':'b','bbox':[10,10,100,50],'text':'Test','order':1}]}]}
            units=build_units({'doc_id':'d','source_report':'r','page':1},raw)
            render_units(units,root/'crop.pdf',root/'images')
            self.assertEqual(units[0]['render']['pdf_cropbox'],[20,30,280,370])
            self.assertEqual(units[0]['render']['pdf_clip'],[0,0,260,340])
            self.assertEqual(units[0]['render']['coordinate_origin'],'cropbox_relative_page.rect')
            self.assertEqual(units[0]['image_sha256'],file_sha(units[0]['image_path']))
            self.assertFalse(units[0]['visual_verified'])
