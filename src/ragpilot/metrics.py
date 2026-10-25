"""Transparent *custom, non-official* financial span-answer evaluation.

This is not the TAT-DQA scorer. EM compares casefolded NFKC token sequences;
F1 is multiset token overlap. Currency, signs, numeric punctuation, percent
markers, and scale labels are retained. Lists are ordered answer spans, not
alternative gold answers. Scale-sensitive EM/F1 are zero on scale mismatch;
answer-only versions are also reported. Numeric Decimal equality is a separate
diagnostic, without multiplying/dividing by percent or thousand/million scales.

Accuracy is conditional on usable answered records. Abstentions, rejections,
API/parse/general errors, truncated inputs and unexecuted groups are reported
separately with denominators and coverage, never silently scored as wrong.
Whole-page results are also reported separately from local-evidence results.
Citation existence is checked; semantic citation support is not inferred.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import math
import re
import unicodedata

from .retrieval import finance_tokenize


PROTOCOL = {
    "name": "custom_financial_span_metrics_v1", "official": False,
    "official_scorer": "not_integrated",
    "em": "ordered normalized spans; currency/sign/% retained; scale must match",
    "f1": "multiset finance-token overlap; multiplied by exact canonical scale match",
    "numeric": "exact Decimal value; no scale conversion; value and unit checks separate",
    "answer_denominator": "answered records only; statuses and coverage reported separately",
    "correct_per_executed": "operational result: correct answers / executed attempts; abstentions, rejections, errors and truncated inputs remain separately counted",
    "retrieval_denominator": "executed retrieval with verified nonempty support-block mapping",
    "complete_support_denominator": "same, additionally support_complete is true",
    "citation_support": "not assessed; existence is not semantic support",
}
_STATUS_ALIASES = {
    "ok": "answered", "success": "answered", "completed": "answered",
    "abstained": "abstention", "refused": "rejection", "refusal": "rejection",
    "rejected": "rejection", "not_run": "not_executed", "skipped": "not_executed",
    "unavailable": "not_executed",
}
_STATUSES = ("answered", "abstention", "rejection", "api_error", "parse_error",
             "error", "truncated", "not_executed")
_SCALES = {"none": "", "thousands": "thousand", "millions": "million",
           "billions": "billion", "%": "percent", "percentage": "percent",
           "percentages": "percent"}


def normalize_scale(scale: object) -> str:
    text = "" if scale is None else unicodedata.normalize("NFKC", str(scale)).strip().casefold()
    return _SCALES.get(text, text)


def _spans(answer: object) -> list[str]:
    def finite(value: object) -> bool:
        return not ((isinstance(value, float) and not math.isfinite(value))
                    or (isinstance(value, Decimal) and not value.is_finite()))

    if isinstance(answer, (str, int, float, Decimal)) and not isinstance(answer, bool):
        if not finite(answer):
            raise ValueError("Numeric answers must be finite")
        return [str(answer)]
    if isinstance(answer, list) and all(isinstance(s, (str, int, float, Decimal))
                                        and not isinstance(s, bool) for s in answer):
        if not all(finite(span) for span in answer):
            raise ValueError("Numeric answers must be finite")
        return [str(span) for span in answer]
    raise ValueError("Answers must be strings, finite scalar numbers, or lists of spans")


def normalize_answer(answer: object) -> list[str]:
    """Preserve span boundaries and canonicalize accounting negatives to '-'."""
    pattern = (r"\(\s*((?:[$€£¥]|USD|EUR|GBP|JPY)?\s*"
               r"(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)\s*%?"
               r"(?:\s*(?:thousands?|millions?|billions?|percent(?:ages?)?))?)\s*\)")
    spans = [re.sub(pattern, lambda match: "-" + re.sub(r"^([$€£¥]|USD|EUR|GBP|JPY)\s+", r"\1", match[1].strip(), flags=re.IGNORECASE),
                    unicodedata.normalize("NFKC", span), flags=re.IGNORECASE)
             for span in _spans(answer)]
    return [" ".join(finance_tokenize(span)) for span in spans]


def answer_scores(prediction: object, gold: object, predicted_scale: object = "",
                  gold_scale: object = "") -> dict:
    """Custom EM/F1; never call these official TAT-DQA metrics."""
    pred, truth = normalize_answer(prediction), normalize_answer(gold)
    pred_tokens = [token for span in pred for token in span.split()]
    gold_tokens = [token for span in truth for token in span.split()]
    overlap = sum((Counter(pred_tokens) & Counter(gold_tokens)).values())
    if not pred_tokens or not gold_tokens:
        f1 = float(pred_tokens == gold_tokens)
    else:
        f1 = 2 * overlap / (len(pred_tokens) + len(gold_tokens))
    answer_em = float(pred == truth)
    scale_label_match = normalize_scale(predicted_scale) == normalize_scale(gold_scale)
    numeric = numeric_comparison(prediction, gold, predicted_scale, gold_scale)
    scale_match = scale_label_match and not numeric["prediction_scale_conflict"] and not numeric["gold_scale_conflict"]
    return {"answer_only_exact_match": answer_em, "answer_only_token_f1": f1,
            "scale_label_match": scale_label_match, "scale_match": scale_match, "exact_match": answer_em * scale_match,
            "token_f1": f1 * scale_match}


def _number(answer: object) -> dict | None:
    spans = _spans(answer)
    if len(spans) != 1:
        return None
    text = unicodedata.normalize("NFKC", spans[0]).strip().replace("−", "-")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    pattern = (r"(?P<sign_before>[+-]?)\s*(?P<currency>[$€£¥]|USD|EUR|GBP|JPY)?\s*(?P<sign_after>[+-]?)\s*"
               r"(?P<value>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)"
               r"\s*(?P<percent>%)?(?:\s*(?P<scale>thousands?|millions?|billions?|percent(?:ages?)?))?")
    match = re.fullmatch(pattern, text, flags=re.IGNORECASE)
    if match is None:
        return None
    sign = match["sign_before"] or match["sign_after"]
    if (match["sign_before"] and match["sign_after"]) or (negative and sign):
        return None
    try:
        value = Decimal(("-" if negative else sign) + match["value"].replace(",", ""))
    except InvalidOperation:
        return None
    return {"value": value, "percent": bool(match["percent"]),
            "currency": (match["currency"] or "").casefold(),
            "inline_scale": normalize_scale(match["scale"])}


def numeric_comparison(prediction: object, gold: object, predicted_scale: object = "",
                       gold_scale: object = "") -> dict:
    """5 and 5% may match numeric_value_match but cannot match percent_match."""
    pred, truth = _number(prediction), _number(gold)
    result = {"gold_numeric": truth is not None, "prediction_numeric": pred is not None,
              "numeric_value_match": None, "percent_match": None,
              "currency_match": None, "numeric_with_units_match": None,
              "prediction_scale_conflict": False, "gold_scale_conflict": False}
    if truth is None:
        return result
    if pred is None:
        result.update(numeric_value_match=False, numeric_with_units_match=False)
        return result
    pred_field, gold_field = normalize_scale(predicted_scale), normalize_scale(gold_scale)
    pred_inline = pred["inline_scale"] or ("percent" if pred["percent"] else "")
    gold_inline = truth["inline_scale"] or ("percent" if truth["percent"] else "")
    pred_effective, gold_effective = pred_field or pred_inline, gold_field or gold_inline
    pred_conflict = bool(pred_field and pred["inline_scale"] and pred_field != pred["inline_scale"])
    gold_conflict = bool(gold_field and truth["inline_scale"] and gold_field != truth["inline_scale"])
    pred_conflict = pred_conflict or (pred["percent"] and pred_effective not in {"", "percent"})
    gold_conflict = gold_conflict or (truth["percent"] and gold_effective not in {"", "percent"})
    scale_match = pred_effective == gold_effective
    value_match = pred["value"] == truth["value"]
    percent_match = ((pred["percent"] or pred_effective == "percent")
                     == (truth["percent"] or gold_effective == "percent"))
    currency_match = pred["currency"] == truth["currency"]
    result.update(numeric_value_match=value_match, percent_match=percent_match,
                  currency_match=currency_match,
                  prediction_scale_conflict=pred_conflict, gold_scale_conflict=gold_conflict,
                  numeric_with_units_match=value_match and percent_match and currency_match and scale_match
                  and not pred_conflict and not gold_conflict)
    return result


def _ratio(numerator: int | float, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def _status(prediction: dict) -> str:
    status = prediction.get("status")
    if status is None:
        status = "error" if prediction.get("error") else (
            "answered" if prediction.get("parsed_answer") is not None else "not_executed")
    status = _STATUS_ALIASES.get(status, status)
    if status not in _STATUSES:
        raise ValueError(f"Unknown prediction status: {status}")
    if prediction.get("truncated") and status == "answered":
        return "truncated"
    if status == "answered" and prediction.get("error"):
        raise ValueError("Answered prediction must not contain an error")
    return status


def _block_ids(unit: dict) -> set[str]:
    refs = unit.get("word_block_refs", [])
    if not isinstance(refs, list):
        raise ValueError("word_block_refs must be a list")
    ids = set()
    for ref in refs:
        if isinstance(ref, dict):
            ref = ref.get("block_id", ref.get("id"))
        if ref is None or isinstance(ref, (list, dict, bool)):
            raise ValueError("word_block_refs must contain block IDs or {block_id: ID} records")
        ids.add(str(ref))
    return ids


def _unique_index(records: list[dict], field: str, label: str) -> dict:
    index = {}
    for record in records:
        key = record.get(field)
        if not isinstance(key, str) or not key:
            raise ValueError(f"{label} requires a nonempty {field}")
        if key in index:
            raise ValueError(f"Duplicate {field} in {label}: {key}")
        index[key] = record
    return index


def _answer_row(question_id: str, group: str, prediction: dict, gold: dict,
                units: dict, document_whole_page: dict) -> dict:
    status = _status(prediction)
    doc_id = gold["doc_id"]
    selected_ids = prediction.get("selected_ids", [])
    if not isinstance(selected_ids, list) or not all(isinstance(eid, str) for eid in selected_ids):
        raise ValueError("Prediction selected_ids must be a list of evidence IDs")
    scope = "whole_page" if (document_whole_page.get(doc_id, False)
                             or any(units.get(eid, {}).get("whole_page", False)
                                    for eid in selected_ids)) else "local"
    row = {"question_id": question_id, "doc_id": doc_id, "group": group,
           "split": gold.get("split", "unspecified"),
           "status": status, "evidence_scope": scope,
           "prediction": prediction.get("parsed_answer"), "gold_answer": gold["answer"],
           "predicted_scale": prediction.get("scale", ""), "gold_scale": gold.get("scale", ""),
           "exact_match": None, "token_f1": None, "answer_only_exact_match": None,
           "answer_only_token_f1": None, "scale_match": None, "scale_label_match": None,
           "numeric_value_match": None, "numeric_with_units_match": None,
           "gold_numeric": False, "prediction_numeric": False,
           "percent_match": None, "currency_match": None,
           "prediction_scale_conflict": False, "gold_scale_conflict": False,
           "citation_count": 0, "valid_citation_count": 0, "citations_outside_selected": [],
           "citation_support": "not_assessed", "exclusion_reason": None}
    if status != "answered":
        row["exclusion_reason"] = status
        return row
    answer = prediction.get("parsed_answer")
    try:
        normalized = normalize_answer(answer)
        if not normalized or not any(normalized):
            raise ValueError("Answered prediction is empty")
        row.update(answer_scores(answer, gold["answer"], prediction.get("scale"), gold.get("scale")))
        row.update(numeric_comparison(answer, gold["answer"], prediction.get("scale"), gold.get("scale")))
    except ValueError as exc:
        row.update(status="parse_error", exclusion_reason=str(exc), prediction=repr(answer))
        return row
    citations = prediction.get("citations", [])
    if not isinstance(citations, list) or not all(isinstance(c, str) for c in citations):
        row.update(status="parse_error", exclusion_reason="citations must be a list of evidence IDs")
        for field in ("exact_match", "token_f1", "answer_only_exact_match", "answer_only_token_f1",
                      "scale_match", "numeric_value_match", "numeric_with_units_match"):
            row[field] = None
        return row
    citations = list(dict.fromkeys(citations))
    row["citation_count"] = len(citations)
    row["valid_citation_count"] = sum(c in units and units[c].get("doc_id") == doc_id for c in citations)
    row["citations_outside_selected"] = [c for c in citations if c not in selected_ids]
    return row


def _summarize_answers(rows: list[dict]) -> dict:
    counts = Counter(row["status"] for row in rows)
    answered = [row for row in rows if row["status"] == "answered"]
    executed = len(rows) - counts["not_executed"]
    result = {"expected_questions": len(rows), "executed_questions": executed,
              "answered_questions": len(answered),
              "status": "not_executed" if executed == 0 else "evaluated",
              "status_counts": {status: counts[status] for status in _STATUSES},
              "correct_per_executed": _ratio(sum(row["exact_match"] for row in answered), executed),
              "answer_coverage_executed": _ratio(len(answered), executed),
              "answer_coverage_expected": _ratio(len(answered), len(rows))}
    for metric in ("exact_match", "token_f1", "answer_only_exact_match", "answer_only_token_f1", "scale_match"):
        result[metric] = _ratio(sum(row[metric] for row in answered), len(answered))
    numeric = [row for row in answered if row["gold_numeric"]]
    for metric in ("numeric_value_match", "numeric_with_units_match"):
        result[metric] = _ratio(sum(bool(row[metric]) for row in numeric), len(numeric))
    result["citation_existence"] = _ratio(sum(row["valid_citation_count"] for row in answered),
                                          sum(row["citation_count"] for row in answered))
    result["answer_with_citation_coverage"] = _ratio(sum(row["citation_count"] > 0 for row in answered), len(answered))
    result["citation_support"] = {"value": None, "denominator": 0, "status": "not_assessed"}
    result["scale_conflict_counts"] = {
        "prediction": sum(row["prediction_scale_conflict"] for row in answered),
        "gold": sum(row["gold_scale_conflict"] for row in answered)}
    return result


def _retrieval_row(record: dict, gold: dict, units: dict) -> dict:
    selected = record.get("selected_ids", [])
    if not isinstance(selected, list) or not all(isinstance(eid, str) for eid in selected):
        raise ValueError("Retrieval selected_ids must be a list of evidence IDs")
    status = record.get("status", "ok")
    doc_id = gold["doc_id"]
    scope = "whole_page" if any(units.get(eid, {}).get("whole_page", False)
                                 for eid in record.get("candidate_ids", selected)) else "local"
    row = {"question_id": gold["question_id"], "doc_id": doc_id,
           "split": gold.get("split", "unspecified"),
           "retriever": record.get("retriever", "unknown"),
           "config_hash": record.get("config_hash", "unspecified"), "status": status,
           "evidence_scope": scope, "selected_ids": selected,
           "any_support_hit": None, "complete_support_coverage": None,
           "mapping_verified": gold.get("mapping_verified") is True,
           "support_complete": gold.get("support_complete") is True,
           "exclusion_reason": None, "invalid_selected_ids": [], "unrepresented_support_block_ids": []}
    if status not in {"ok", "success", "no_candidates"}:
        row["exclusion_reason"] = "retrieval_" + status
        return row
    if record.get("doc_id", doc_id) != doc_id:
        row["exclusion_reason"] = "document_mismatch"
        return row
    row["invalid_selected_ids"] = [eid for eid in selected if eid not in units or units[eid].get("doc_id") != doc_id]
    if row["invalid_selected_ids"]:
        row["exclusion_reason"] = "invalid_selected_evidence"
        return row
    if not row["mapping_verified"]:
        row["exclusion_reason"] = "unverified_support_mapping"
        return row
    support = gold.get("support_block_ids", [])
    if not isinstance(support, list):
        raise ValueError("support_block_ids must be a list")
    support = {str(block) for block in support}
    if not support:
        row["exclusion_reason"] = "empty_verified_support"
        return row
    selected_blocks = set().union(*(_block_ids(units[eid]) for eid in selected)) if selected else set()
    document_units = [unit for unit in units.values() if unit.get("doc_id") == doc_id]
    represented = set().union(*(_block_ids(unit) for unit in document_units)) if document_units else set()
    row["unrepresented_support_block_ids"] = sorted(support - represented)
    row["any_support_hit"] = bool(support & selected_blocks)
    if row["support_complete"]:
        complete = gold.get("complete_support_block_ids", list(support))
        if not isinstance(complete, list) or not complete:
            raise ValueError("Complete support requires nonempty complete_support_block_ids")
        complete = {str(block) for block in complete}
        if not support <= complete:
            raise ValueError("Complete support must include answer-bearing support blocks")
        row["unrepresented_support_block_ids"] = sorted((support | complete) - represented)
        row["complete_support_coverage"] = complete <= selected_blocks
    return row


def _summarize_retrieval(rows: list[dict]) -> dict:
    result = {"records": len(rows), "status_counts": dict(Counter(row["status"] for row in rows)),
              "exclusion_counts": dict(Counter(row["exclusion_reason"] for row in rows if row["exclusion_reason"]))}
    for metric in ("any_support_hit", "complete_support_coverage"):
        usable = [row for row in rows if row[metric] is not None]
        result[metric] = _ratio(sum(row[metric] for row in usable), len(usable))
    return result


def evaluate(predictions: list[dict], evaluation_records: list[dict],
             retrieval_records: list[dict], evidence_units: list[dict]) -> dict:
    """Return aggregate metrics plus result_rows and retrieval_rows for export.

    Gold fields: question_id/doc_id/answer/scale/support_block_ids/
    mapping_verified/support_complete. Prediction fields: question_id/group/
    status/parsed_answer/scale/citations/selected_ids. One final prediction per
    question/group is required; keep retry logs separately. E1-E4 are always
    represented, even when no run exists. No input object is modified.
    """
    golds = _unique_index(evaluation_records, "question_id", "evaluation records")
    units = _unique_index(evidence_units, "evidence_id", "evidence units")
    for gold in golds.values():
        if not isinstance(gold.get("doc_id"), str) or not gold["doc_id"] or "answer" not in gold:
            raise ValueError("Every evaluation record requires doc_id and answer")
        if not any(normalize_answer(gold["answer"])):
            raise ValueError("Empty gold answers are outside this span-extraction pilot")
    groups = {"E1", "E2", "E3", "E4"}
    prediction_index = {}
    for prediction in predictions:
        question_id, group = prediction.get("question_id"), prediction.get("group")
        if question_id not in golds or not isinstance(group, str) or not group:
            raise ValueError("Prediction requires a known question_id and nonempty group")
        key = (question_id, group)
        if key in prediction_index:
            raise ValueError("Duplicate final prediction for question/group; resolve retries before evaluation")
        prediction_index[key] = prediction
        groups.add(group)
    whole_page = {doc_id: any(u.get("whole_page", False) for u in units.values() if u.get("doc_id") == doc_id)
                  for doc_id in {gold["doc_id"] for gold in golds.values()}}
    rows = [_answer_row(qid, group, prediction_index.get((qid, group), {"status": "not_executed"}),
                        golds[qid], units, whole_page)
            for group in sorted(groups) for qid in sorted(golds)]
    answer_metrics = {}
    for group in sorted(groups):
        group_rows = [row for row in rows if row["group"] == group]
        summary = _summarize_answers(group_rows)
        summary["by_evidence_scope"] = {scope: _summarize_answers([row for row in group_rows if row["evidence_scope"] == scope])
                                         for scope in ("local", "whole_page")}
        summary["by_split"] = {split: _summarize_answers([row for row in group_rows if row["split"] == split])
                               for split in sorted({row["split"] for row in group_rows})}
        answer_metrics[group] = summary
    retrieval_rows, seen, by_config = [], set(), defaultdict(list)
    for record in retrieval_records:
        question_id = record.get("question_id")
        if question_id not in golds:
            raise ValueError("Retrieval record has unknown question_id")
        row = _retrieval_row(record, golds[question_id], units)
        key = (question_id, row["retriever"], row["config_hash"])
        if key in seen:
            raise ValueError("Duplicate retrieval record for question/retriever/config")
        seen.add(key)
        retrieval_rows.append(row)
        by_config[(row["retriever"], row["config_hash"])].append(row)
    retrieval_metrics = {}
    for (retriever, config_hash), group_rows in sorted(by_config.items()):
        summary = _summarize_retrieval(group_rows)
        summary.update(retriever=retriever, config_hash=config_hash)
        summary["by_evidence_scope"] = {scope: _summarize_retrieval([row for row in group_rows if row["evidence_scope"] == scope])
                                         for scope in ("local", "whole_page")}
        summary["by_split"] = {split: _summarize_retrieval([row for row in group_rows if row["split"] == split])
                               for split in sorted({row["split"] for row in group_rows})}
        retrieval_metrics[f"{retriever}:{config_hash}"] = summary
    return {"protocol": dict(PROTOCOL), "evaluation_questions": len(golds),
            "answer_metrics": answer_metrics, "retrieval_metrics": retrieval_metrics,
            "result_rows": rows, "retrieval_rows": retrieval_rows}
