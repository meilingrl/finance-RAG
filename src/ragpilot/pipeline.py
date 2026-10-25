"""Offline orchestration: no API client, paid service or generation execution path."""
from __future__ import annotations
import csv
import sys
from pathlib import Path
from .common import digest, now, read_json, read_jsonl, write_json, write_jsonl, reject_gold, safe_event, unique, file_sha
from .evidence import descriptions
from .inference import freeze_retrieval, run_e1, prepare_requests
from .rules import RULES
from .retrieval import retrieve
from .metrics import evaluate

DEFAULT_CONFIG = {"retriever": "bm25", "tokenizer": "finance", "k": 2, "k1": 1.5, "b": 0.75}

def run_offline(data_dir, output_dir, config=None, run_kind="real_data_infrastructure", split=None, freeze_path=None):
    data_dir, out = Path(data_dir), Path(output_dir)
    if (out / "run.json").exists():
        raise ValueError("Run directory already frozen; choose a new output directory")
    supplied_config = config or {}
    defaults = DEFAULT_CONFIG if supplied_config.get("retriever", "bm25") == "bm25" else {"retriever": "sentence_transformers", "k": 2}
    config = {**defaults, **supplied_config}
    if freeze_path:
        validate_freeze(freeze_path, data_dir, config)
    questions = read_jsonl(data_dir / "inference_questions.jsonl")
    units = read_jsonl(data_dir / "evidence_units.jsonl")
    manifest = read_json(data_dir / "data_manifest.json")
    if manifest.get("dataset", "").startswith("SYNTHETIC_"):
        run_kind = "synthetic_software_fixture"
    if digest(questions) != manifest["inference_hash"] or digest(units) != manifest["evidence_hash"]:
        raise ValueError("Processed inputs changed after manifest freeze; prepare a new version")
    if split:
        questions = [q for q in questions if q["split"] == split]
    if not questions:
        raise ValueError("No questions for requested split")
    unique(questions, "question_id")
    for question in questions:
        reject_gold(question)
    for unit in units:
        reject_gold(unit)
    out.mkdir(parents=True, exist_ok=True)
    config_hash = digest(config)
    safe_event(out / "events.jsonl", "offline_started", config_hash=config_hash, run_kind=run_kind)
    desc = descriptions(units)
    retrieval, predictions, requests = [], [], []
    for question in questions:
        found = retrieve(question, units, config)
        if found.get("status") not in (None, "ok", "success"):
            retrieval.append(found)
            safe_event(out / "events.jsonl", "retrieval_unavailable", status=found.get("status"), error_code="retrieval_unavailable")
            continue
        frozen = freeze_retrieval(question, units, found)
        retrieval.append(frozen)
        predictions.append(run_e1(question, units, frozen))
        prepared = prepare_requests(question, units, frozen, desc)
        requests.extend(prepared)
        predictions.extend({k: v for k, v in r.items() if k != "request_payload"} for r in prepared)
    # Gold is opened only after inference and request creation have completed.
    if manifest.get("evaluation_sources"):
        if not split:
            raise ValueError("Isolated-gold data requires an explicit development/evaluation split")
        source=manifest["evaluation_sources"][split]
        if split=="pilot_evaluation" and not freeze_path:
            raise ValueError("Pilot evaluation requires --freeze created before opening evaluation gold")
        if file_sha(source["path"])!=source["sha256"]:
            raise ValueError("Isolated gold changed after curator freeze")
        evaluation=read_jsonl(source["path"])
    else:
        evaluation = read_jsonl(data_dir / "evaluation_gold.jsonl")
        if digest(evaluation) != manifest["evaluation_hash"]:
            raise ValueError("Evaluation gold changed after manifest freeze")
    wanted = {q["question_id"] for q in questions}
    evaluation = [r for r in evaluation if r["question_id"] in wanted]
    scores = evaluate(predictions, evaluation, retrieval, units)
    scores["run_kind"] = run_kind
    scores["model_execution"] = {"calls": 0, "actual_api_spend": 0, "currency": "USD", "basis": "no_model_requests_sent", "future_request_cost": "unknown"}
    scores["limitations"] = ["Custom metrics are not official TAT-DQA scores", "No LLM group executed", "Pretrained encoder only runs if explicit local model is provided", "Whole-page diagnostics are not evidence of local retrieval discrimination", "Automatic descriptions are not visually verified"]
    scores["candidate_counts"] = {d: sum(u["doc_id"] == d for u in units) for d in sorted({q["doc_id"] for q in questions})}
    scores["retrieval_timing"]={"total_elapsed_seconds":sum(r['elapsed_seconds'] for r in retrieval),
                                "queries":len(retrieval),"successful_queries":sum(r.get('status')=='ok' for r in retrieval)}
    if config['retriever']=='sentence_transformers':
        per_unit={};queries=[];selected_truncated=[]
        for r in retrieval:
            timing=r.get('timing',{});counts=timing.get('input_token_counts');flags=timing.get('input_truncated')
            if counts is None or flags is None:continue
            queries.append({'question_id':r['question_id'],'tokens':counts[0],'truncated':flags[0]})
            for eid,n,flag in zip(r['candidate_ids'],counts[1:],flags[1:]):per_unit[eid]={'tokens':n,'truncated':flag}
            selected_truncated.extend(eid for eid in r['selected_ids'] if per_unit[eid]['truncated'])
        scores['encoder_diagnostics']={'per_evidence_unit':per_unit,'queries':queries,
              'truncated_unit_count':sum(x['truncated'] for x in per_unit.values()),'unit_count':len(per_unit),
              'selected_truncated_occurrences':len(selected_truncated),'selected_occurrences':sum(len(r['selected_ids']) for r in retrieval),
              'effect':'Only the first max_seq_length tokens influenced each truncated embedding; E1/shared evidence retains full text.',
              'real_encoder_successful_queries':sum(r.get('status')=='ok' for r in retrieval)}
    scores["majority_single_candidate"] = sum(v == 1 for v in scores["candidate_counts"].values()) > len(scores["candidate_counts"])/2
    write_json(out / "data_manifest.json", manifest)
    write_json(out / "frozen_config.json", {"config": config, "config_hash": config_hash, "selection_status": "frozen_development_record" if freeze_path else "development_candidate_or_legacy_default", "rules_development_validation": RULES["development_validation"], "rules": RULES, "rules_hash": digest(RULES), "freeze_path": str(freeze_path) if freeze_path else None})
    write_jsonl(out / "evidence_units.jsonl", units)
    write_jsonl(out / "descriptions.jsonl", desc)
    write_jsonl(out / "retrieval.jsonl", retrieval)
    write_jsonl(out / "predictions.jsonl", predictions)
    write_jsonl(out / "prepared_requests.jsonl", requests)
    write_json(out / "metrics.json", scores)
    rows = scores.get("result_rows", [])
    with (out / "results.csv").open("w", newline="", encoding="utf-8") as f:
        fields = sorted({key for row in rows for key in row})
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (out / "cases.md").write_text("# Operational case record\n\nNo model failure analysis has been performed. No invented examples or error categories are presented as experimental findings. Inspect predictions.jsonl for actual E1 rule coverage; E2–E4 are not run. Student-authored analysis remains pending.\n", encoding="utf-8")
    completed = {"status": "offline_complete" if len(predictions) == len(questions)*4 else "offline_partial_retriever_unavailable",
                 "started_and_finished_scope": "non_generative_baselines", "completed_at": now(), "python": sys.version.split()[0],
                 "run_kind": run_kind, "selected_split": split or "combined_infrastructure_smoke_not_held_out_evaluation", "question_count": len(questions), "document_count": len({q["doc_id"] for q in questions}),
                 "config_hash": config_hash, "config": config, "model_calls": 0,
                 "prepared_request_count": len(requests), "retrieval_hash": digest(retrieval),
                 "prediction_hash": digest(predictions), "input_manifest_hash": digest(manifest), "source_fingerprint": source_fingerprint()}
    write_json(out / "run.json", completed)
    safe_event(out / "events.jsonl", "offline_finished", status=completed["status"], count=len(questions), run_kind=run_kind)
    return completed

def make_fixture(destination):
    """Software-test-only artificial data. Never merged with TAT-DQA artifacts."""
    from .evidence import build_units
    dest = Path(destination)
    if (dest / "data_manifest.json").exists():
        raise ValueError("Fixture destination exists; choose a new directory")
    units = []
    for doc_id, text in [("fixture-a", "Revenue in 2024: $5 million\nCash in 2024: $8 million"), ("fixture-b", "Revenue in 2024: $99 million")]:
        page = {"pages": [{"bbox": [0,0,500,500], "blocks": [{"uuid": doc_id+"-block", "bbox": [10,10,490,100], "text": text, "order": 1}]}]}
        units.extend(build_units({"doc_id": doc_id, "source_report": "synthetic-software-fixture-"+doc_id, "page": 1}, page))
    questions = [{"question_id": "fixture-q1", "doc_id": "fixture-a", "source_report": "synthetic-software-fixture-fixture-a", "split": "development", "original_split": "synthetic_fixture", "question": "What was revenue in 2024?", "answer_type": "span"},
                 {"question_id": "fixture-q2", "doc_id": "fixture-a", "source_report": "synthetic-software-fixture-fixture-a", "split": "development", "original_split": "synthetic_fixture", "question": "What was profit in 2024?", "answer_type": "span"}]
    gold = [{"question_id": "fixture-q1", "doc_id": "fixture-a", "split": "development", "answer": "$5 million", "scale": "million", "support_block_ids": ["fixture-a-block"], "mapping_verified": True, "support_complete": True},
            {"question_id": "fixture-q2", "doc_id": "fixture-a", "split": "development", "answer": "$3 million", "scale": "million", "support_block_ids": [], "mapping_verified": False, "support_complete": False}]
    manifest = {"dataset": "SYNTHETIC_SOFTWARE_FIXTURE_NOT_EXPERIMENT_DATA", "seed": 42, "inference_hash": digest(questions), "evidence_hash": digest(units), "evaluation_hash": digest(gold), "actual": {"documents": 2, "questions": 2}, "selected_documents": [], "license": "self-created software test fixture"}
    write_jsonl(dest / "inference_questions.jsonl", questions)
    write_jsonl(dest / "evidence_units.jsonl", units)
    write_jsonl(dest / "evaluation_gold.jsonl", gold)
    write_json(dest / "data_manifest.json", manifest)
    return manifest


def source_fingerprint():
    root=Path(__file__).resolve().parent.parent
    return {str(p.relative_to(root)):file_sha(p) for p in sorted(root.rglob('*.py'))}

def freeze_nonllm(data_dir, development_runs, allowed_configs, output):
    """Seal developed code/configs before held-out scoring. Never reads eval gold."""
    out=Path(output)
    if out.exists():raise ValueError('Freeze record already exists')
    summaries=[]
    manifest=read_json(Path(data_dir)/'data_manifest.json')
    for path in development_runs:
        run=read_json(Path(path)/'run.json')
        if run['selected_split']!='development':raise ValueError('Only development runs may select configuration')
        if run['status']!='offline_complete':raise ValueError('Only completed development runs can be frozen')
        if run['input_manifest_hash']!=digest(manifest):raise ValueError('Development run used different frozen data')
        if run.get('source_fingerprint')!=source_fingerprint():raise ValueError('Development run used different source code')
        if manifest.get('dataset')=='TAT-DQA' and run['run_kind']=='synthetic_software_fixture':raise ValueError('Synthetic development cannot select real-data configuration')
        metrics=read_json(Path(path)/'metrics.json')
        summaries.append({'path':str(path),'run_hash':digest(run),'config':run['config'],
                          'retrieval_metrics':metrics['retrieval_metrics'],
                          'e1_correct_per_executed':metrics['answer_metrics']['E1']['correct_per_executed'],
                          'e1_coverage':metrics['answer_metrics']['E1']['answer_coverage_executed']})
    manifest=read_json(Path(data_dir)/'data_manifest.json')
    record={'frozen_at':now(),'data_manifest_hash':digest(manifest),
            'source_fingerprint':source_fingerprint(),'rules':RULES,'rules_hash':digest(RULES),
            'allowed_configs':allowed_configs,'development_runs':summaries,
            'selection_status':'retrieval_mapping_unverified_no_evidence_based_winner_selected',
            'default_extension_config':allowed_configs[0],
            'evaluation_gold_opened_for_selection':False, 'encoder_identities':encoder_identities(allowed_configs)}
    import statistics
    eligible=[]
    for summary in summaries:
        if summary['config'] not in allowed_configs:continue
        retrieval_metrics=list(summary['retrieval_metrics'].values())
        if len(retrieval_metrics)!=1:continue
        m=retrieval_metrics[0]
        complete=m['complete_support_coverage'];any_hit=m['any_support_hit']
        if not any_hit['denominator']:continue
        times=[r['elapsed_seconds'] for r in read_jsonl(Path(summary['path'])/'retrieval.jsonl') if r.get('status')=='ok']
        eligible.append({'config':summary['config'],'complete_support_coverage':complete,'any_support_hit':any_hit,
                         'median_retrieval_seconds':statistics.median(times) if times else float('inf')})
    if len(eligible)==len(allowed_configs) and len({e['any_support_hit']['denominator'] for e in eligible})==1:
        eligible.sort(key=lambda e:(-(e['complete_support_coverage']['value'] if e['complete_support_coverage']['value'] is not None else e['any_support_hit']['value']),-e['any_support_hit']['value'],e['median_retrieval_seconds']))
        record.update(selection_status='development_verified_support_then_latency',default_extension_config=eligible[0]['config'],
                      selection_rule='higher complete support when available, then any support, exact ties by measured median retrieval time; k=2 retained; no evaluation use',
                      selection_candidates=eligible)
    write_json(out,record)
    return record

def validate_freeze(path,data_dir,config):
    freeze=read_json(path)
    if freeze['source_fingerprint']!=source_fingerprint():raise ValueError('Source code changed after development freeze')
    if freeze['data_manifest_hash']!=digest(read_json(Path(data_dir)/'data_manifest.json')):raise ValueError('Data manifest changed after development freeze')
    if config not in freeze['allowed_configs']:raise ValueError('Configuration was not declared before evaluation')
    if freeze['rules_hash']!=digest(RULES):raise ValueError('Extraction rules changed after development freeze')
    if freeze.get('encoder_identities',[])!=encoder_identities(freeze['allowed_configs']):raise ValueError('Encoder weights or dependency versions changed after freeze')

def score_frozen(run_dir,gold_path,output_dir):
    """Score immutable predictions with separately reviewed mappings; answers immutable."""
    root=Path(run_dir);out=Path(output_dir)
    if out.exists():raise ValueError('Scoring destination already exists')
    run=read_json(root/'run.json');predictions=read_jsonl(root/'predictions.jsonl');retrieval=read_jsonl(root/'retrieval.jsonl')
    if digest(predictions)!=run['prediction_hash'] or digest(retrieval)!=run['retrieval_hash']:
        raise ValueError('Frozen predictions or retrieval changed')
    units=read_jsonl(root/'evidence_units.jsonl');manifest=read_json(root/'data_manifest.json')
    if digest(manifest)!=run['input_manifest_hash']:raise ValueError('Frozen source manifest changed')
    if digest(units)!=manifest['evidence_hash']:raise ValueError('Frozen evidence changed')
    original_source=manifest['evaluation_sources'][run['selected_split']]
    if file_sha(original_source['path'])!=original_source['sha256']:raise ValueError('Original gold bytes changed')
    gold=read_jsonl(gold_path)
    original=read_jsonl(manifest['evaluation_sources'][run['selected_split']]['path'])
    original_by_id={g['question_id']:g for g in original}
    if {g['question_id'] for g in gold}!={g['question_id'] for g in original}:raise ValueError('Reviewed gold changed question population')
    for g in gold:
        if any(g.get(k)!=original_by_id[g['question_id']].get(k) for k in ('doc_id','split','answer','scale')):
            raise ValueError('Reviewed mappings may not change original answers, units or split')
    scores=evaluate(predictions,gold,retrieval,units)
    scores['rescoring']={'source_run':str(root),'prediction_hash':run['prediction_hash'],'retrieval_hash':run['retrieval_hash'],
                         'reviewed_gold_path':str(gold_path),'reviewed_gold_sha256':file_sha(gold_path),
                         'original_answers_preserved':True,'inference_rerun':False}
    write_json(out/'metrics.json',scores)
    rows=scores['result_rows']
    with (out/'results.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=sorted({k for r in rows for k in r}));writer.writeheader();writer.writerows(rows)
    write_json(out/'scoring_manifest.json',scores['rescoring'])
    return scores['rescoring']


def encoder_identities(configs):
    import importlib.metadata
    from .retrieval import _local_model_metadata
    result=[]
    for config in configs:
        if config['retriever']!='sentence_transformers':continue
        path,meta=_local_model_metadata(config['model_path'])
        versions={name:importlib.metadata.version(name) for name in ('sentence-transformers','transformers','torch','tokenizers','safetensors')}
        result.append({'path':str(path),'model_content_sha256':meta['model_content_sha256'],'dependency_versions':versions})
    return result
