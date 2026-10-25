"""TAT-DQA import with report-disjoint sampling and explicit gold isolation."""
from __future__ import annotations
import random
from collections import Counter, defaultdict
from pathlib import Path
from .common import digest, file_sha, read_json, write_json, write_jsonl, safe_identifier, unique, portable_path
from .evidence import build_units, normalize_page, render_units

OFFICIAL_SITE = "https://nextplusplus.github.io/TAT-DQA/"

def load_records(path):
    value = read_json(path)
    if isinstance(value, dict) and "doc" in value:
        value = [value]
    if not isinstance(value, list):
        raise ValueError("QA input must be an official record list, or one exact official record")
    return value

def prepare(qa_inputs, docs_dir, output, seed=42, dev_docs=4, eval_docs=12, max_questions=3, render=False, regions_file=None):
    """Import only paired single-page span/non-comparison records; no answer-based selection.

    qa_inputs maps original split labels to local files. Official train is preferred
    for development and official dev for pilot evaluation; other split handling is
    recorded explicitly, never silently labelled an official held-out benchmark.
    """
    if dev_docs < 1 or eval_docs < 0 or dev_docs > 4 or eval_docs > 12 or not 1 <= max_questions <= 3:
        raise ValueError("Pilot limits: 1–4 development docs, 0–12 evaluation docs, 1–3 questions each")
    output = Path(output)
    if (output / "data_manifest.json").exists():
        raise ValueError("Frozen output already exists; choose a new output directory")
    docs_dir = Path(docs_dir)
    excluded = []
    candidates = []
    provenance = []
    region_map = read_json(regions_file) if regions_file else {}
    seen_docs, seen_questions = set(), set()
    total_questions = 0
    for split, path in sorted(qa_inputs.items()):
        provenance.append({"kind": "qa", "path": portable_path(path), "sha256": file_sha(path), "original_split": split})
        for item in load_records(path):
            doc = item["doc"]
            doc_id = safe_identifier(doc["uid"])
            source = doc.get("source")
            if not source:
                raise ValueError("Source report identity is required to prevent split leakage")
            if doc_id in seen_docs:
                raise ValueError("Duplicate document identity across QA inputs")
            seen_docs.add(doc_id)
            qs = item.get("questions", [])
            total_questions += len(qs)
            eligible = []
            for q in qs:
                qid = str(q["uid"])
                if qid in seen_questions:
                    raise ValueError("Duplicate question identity")
                seen_questions.add(qid)
                reason = None
                if q.get("answer_type") != "span":
                    reason = "answer_type_not_span"
                elif q.get("req_comparison") is not False:
                    reason = "comparison_or_missing_flag"
                elif not isinstance(q.get("question"), str) or not q["question"].strip():
                    reason = "missing_question"
                if reason:
                    excluded.append({"doc_id": doc_id, "question_id": qid, "reason": reason})
                else:
                    eligible.append(q)
            page_path = docs_dir / f"{doc_id}.json"
            pdf_path = docs_dir / f"{doc_id}.pdf"
            if not eligible:
                excluded.append({"doc_id": doc_id, "reason": "no_eligible_questions"})
                continue
            if not page_path.is_file() or not pdf_path.is_file():
                excluded.append({"doc_id": doc_id, "reason": "missing_page_json_or_pdf_pair"})
                continue
            raw = read_json(page_path)
            try:
                page = normalize_page(raw)
            except (KeyError, TypeError, ValueError) as exc:
                excluded.append({"doc_id": doc_id, "reason": "invalid_or_multipage_converted_input", "detail": str(exc)})
                continue
            # Optional dependency only for paired PDF structure validation.
            try:
                from pypdf import PdfReader
            except ImportError:
                try:
                    import fitz
                    with fitz.open(pdf_path) as pdf:
                        pdf_count = len(pdf)
                except ImportError as exc:
                    raise RuntimeError("Real import requires local pypdf or PyMuPDF for PDF single-page validation") from exc
            else:
                pdf_count = len(PdfReader(pdf_path).pages)
            if pdf_count != 1:
                excluded.append({"doc_id": doc_id, "reason": "pdf_not_single_page"})
                continue
            candidates.append({"doc_id": doc_id, "source_report": source, "page": int(doc.get("page", 1)),
                               "original_split": split, "eligible": eligible, "raw_page": raw,
                               "page_path": page_path, "pdf_path": pdf_path})
    rng = random.Random(seed)
    candidates.sort(key=lambda d: d["doc_id"])
    rng.shuffle(candidates)
    train_available = any(d["original_split"] == "train" for d in candidates)
    development_pool = [d for d in candidates if d["original_split"] == "train"] if train_available else list(candidates)
    selected_dev = development_pool[:dev_docs]
    reports = {d["source_report"] for d in selected_dev}
    dev_ids = {d["doc_id"] for d in selected_dev}
    evaluation_pool = [d for d in candidates if d["doc_id"] not in dev_ids and d["source_report"] not in reports
                       and (not train_available or d["original_split"] in {"dev", "test"})]
    selected_eval = evaluation_pool[:eval_docs]
    questions, gold, units, chosen = [], [], [], []
    for role, documents in [("development", selected_dev), ("pilot_evaluation", selected_eval)]:
        for d in documents:
            doc = {k: d[k] for k in ("doc_id", "source_report", "page", "original_split")}
            selected = list(d["eligible"])
            rng.shuffle(selected)
            selected = selected[:max_questions]
            evidence = build_units(doc, d["raw_page"], regions=region_map.get(d["doc_id"]))
            if render:
                render_units(evidence, d["pdf_path"], output / "images")
            units.extend(evidence)
            for q in selected:
                question = {"doc_id": d["doc_id"], "source_report": d["source_report"], "split": role,
                            "original_split": d["original_split"], "question_id": str(q["uid"]),
                            "question": q["question"], "answer_type": q["answer_type"]}
                questions.append(question)
                mappings = q.get("block_mapping") or []
                support = sorted({str(key) for mapping in mappings for key in mapping})
                gold.append({"question_id": str(q["uid"]), "doc_id": d["doc_id"], "split": role,
                             "answer": q.get("answer"), "scale": q.get("scale", ""),
                             "derivation": q.get("derivation"), "facts": q.get("facts"), "block_mapping": mappings,
                             "support_block_ids": support, "mapping_verified": False, "support_complete": False,
                             "mapping_status": "original_offsets_unverified", "offset_convention": "zero_based_python_character_slice_end_exclusive"})
            chosen.append({**doc, "split": role, "question_ids": [str(q["uid"]) for q in selected],
                           "candidate_unit_count": len(evidence), "whole_page": all(e["whole_page"] for e in evidence),
                           "source_files": [{"path": portable_path(d[k]), "sha256": file_sha(d[k])} for k in ("page_path", "pdf_path")]})
    unique(questions, "question_id")
    unique(units, "evidence_id")
    if not questions:
        raise ValueError("No eligible paired questions. Nothing has been frozen.")
    manifest = {"schema_version": 1, "dataset": "TAT-DQA", "source_site": OFFICIAL_SITE,
                "license": "CC BY 4.0 (publisher-stated; preserve attribution)", "seed": seed,
                "sample_status": "infrastructure_subset_not_full_pilot", "qa_sources": provenance,
                "filter": {"answer_type": "span", "req_comparison": False, "page_count": 1, "max_questions_per_doc": max_questions},
                "requested": {"development_docs": dev_docs, "pilot_evaluation_docs": eval_docs},
                "candidates": {"documents_with_valid_pairs": len(candidates), "questions_seen": total_questions},
                "excluded": excluded, "exclusion_counts": dict(Counter(x["reason"] for x in excluded)),
                "split_policy": "official_train_for_development_and_dev_or_test_for_evaluation" if train_available else "report_disjoint_partition_within_available_official_split",
                "selected_documents": chosen, "actual": {"documents": len(chosen), "questions": len(questions),
                    "development_documents": len(selected_dev), "evaluation_documents": len(selected_eval)},
                "majority_single_candidate": sum(d["candidate_unit_count"] == 1 for d in chosen) > len(chosen)/2,
                "gold_mapping_verified": False, "visual_review_status": "pending", "model_calls": 0,
                "inference_hash": digest(questions), "evidence_hash": digest(units), "evaluation_hash": digest(gold)}
    write_jsonl(output / "inference_questions.jsonl", questions)
    write_jsonl(output / "evaluation_gold.jsonl", gold)
    write_jsonl(output / "evidence_units.jsonl", units)
    write_json(output / "data_manifest.json", manifest)
    return manifest

def prepare_inputs(documents_path, question_paths, output, gold_sources, render=True):
    """Prepare inputs without opening QA originals or any gold file.

    gold_sources is a split -> {path, sha256} manifest copied from the data
    curator. The gold bytes are read only by the scoring phase of run_offline.
    """
    from .common import read_jsonl, reject_gold
    from .regions import REGION_POLICY
    output=Path(output)
    if (output/'data_manifest.json').exists():
        raise ValueError('Frozen output already exists; choose a new output directory')
    documents=read_jsonl(documents_path);questions=[];units=[];chosen=[]
    for path in question_paths:
        questions.extend(read_jsonl(path))
    for q in questions:reject_gold(q)
    unique(questions,'question_id');unique(documents,'doc_id')
    reports={}
    for d in documents:
        reject_gold(d)
        if d['source_report'] in reports and reports[d['source_report']]!=d['split']:
            raise ValueError('Source report crosses development/evaluation boundary')
        reports[d['source_report']]=d['split']
        raw_path=d.get('ocr_path') or d.get('json_path') or d.get('page_path')
        pdf_path=d['pdf_path']
        if not raw_path:raise ValueError('Input document needs ocr_path')
        doc={k:d[k] for k in ('doc_id','source_report','page')}
        evidence=build_units(doc,read_json(raw_path),segmentation='blind_blocks')
        if render:render_units(evidence,pdf_path,output/'images')
        units.extend(evidence)
        chosen.append({**doc,'split':d['split'],'original_split':d['original_split'],
                       'question_ids':[q['question_id'] for q in questions if q['doc_id']==d['doc_id']],
                       'candidate_unit_count':len(evidence),'whole_page':all(u['whole_page'] for u in evidence),
                       'source_files':[{'path':portable_path(p),'sha256':file_sha(p)} for p in (raw_path,pdf_path)]})
    ids={d['doc_id'] for d in documents}
    if any(q['doc_id'] not in ids for q in questions):raise ValueError('Question refers to an unknown document')
    unique(units,'evidence_id')
    manifest={'schema_version':2,'dataset':'TAT-DQA','source_site':OFFICIAL_SITE,
              'license':'CC BY 4.0 (publisher-stated; preserve attribution)','seed':42,
              'sample_status':'bounded_nonllm_pilot','selected_documents':chosen,
              'region_policy':REGION_POLICY,'region_policy_hash':digest(REGION_POLICY),
              'actual':{'documents':len(documents),'questions':len(questions),
                        'development_documents':sum(d['split']=='development' for d in documents),
                        'evaluation_documents':sum(d['split']=='pilot_evaluation' for d in documents)},
              'majority_single_candidate':sum(d['candidate_unit_count']==1 for d in chosen)>len(chosen)/2,
              'inference_hash':digest(questions),'evidence_hash':digest(units),
              'evaluation_sources':gold_sources,
              'gold_opened_during_preparation':False,'visual_review_status':'pending'}
    write_jsonl(output/'inference_questions.jsonl',questions)
    write_jsonl(output/'evidence_units.jsonl',units)
    write_json(output/'data_manifest.json',manifest)
    return manifest
