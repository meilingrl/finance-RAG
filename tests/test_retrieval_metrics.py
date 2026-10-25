"""Small, offline unit tests. Encoder tests use a fake, never a real model."""

from copy import deepcopy
from decimal import Decimal
import json
import math
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from ragpilot.metrics import answer_scores, evaluate, numeric_comparison
from ragpilot.retrieval import retrieve, tokenize


def question(qid="q1", doc_id="d1", text="revenue"):
    return {"question_id": qid, "doc_id": doc_id, "question": text}


def unit(eid, text, doc_id="d1", blocks=None, whole_page=False):
    return {"evidence_id": eid, "doc_id": doc_id, "text": text,
            "word_block_refs": blocks or [], "whole_page": whole_page}


def gold(qid="q1", answer="5", scale="", verified=True, complete=True, support=None):
    return {"question_id": qid, "doc_id": "d1", "answer": answer, "scale": scale,
            "support_block_ids": ["b1"] if support is None else support,
            "mapping_verified": verified, "support_complete": complete}


def prediction(qid="q1", answer="5", status="answered", scale="", group="E1"):
    return {"question_id": qid, "group": group, "status": status,
            "parsed_answer": answer, "scale": scale, "selected_ids": ["e1"], "citations": ["e1"]}


class RetrievalTests(unittest.TestCase):
    def test_finance_tokenizer_keeps_financial_forms(self):
        tokens = tokenize("Non‑GAAP 10-K U.S. −1,234.50 5 % $42.50", "finance")
        self.assertEqual(tokens, ["non-gaap", "10-k", "u.s", "-1,234.50", "5%", "$42.50"])
        self.assertEqual(tokenize("Non-GAAP -5.0%", "simple"), ["non", "gaap", "5", "0"])

    def test_within_document_only_stable_tie_break_and_no_padding(self):
        units = [unit("z", "revenue"), unit("outside", "revenue revenue", "other"), unit("a", "revenue")]
        result = retrieve(question(), units, {"k": 2})
        self.assertEqual(result["candidate_ids"], ["a", "z"])
        self.assertEqual(result["selected_ids"], ["a", "z"])
        self.assertEqual(result["config_hash"], retrieve(question(), list(reversed(units)), {"k": 2})["config_hash"])
        one = retrieve(question(), units[:1], {"k": 2})
        self.assertEqual(one["actual_k"], 1)
        self.assertEqual(one["requested_k"], 2)

    def test_bm25_formula_and_ranking(self):
        result = retrieve(question(), [unit("a", "revenue"), unit("b", "cost")], {"k": 1})
        self.assertEqual(result["selected_ids"], ["a"])
        self.assertAlmostEqual(result["scores"]["a"], math.log(2))
        self.assertEqual(result["candidate_scores"]["b"], 0.0)
        self.assertGreaterEqual(result["timing"]["index_seconds"], 0)
        self.assertGreaterEqual(result["elapsed_seconds"], 0)

    def test_configuration_changes_hash(self):
        units = [unit("a", "5% revenue")]
        a = retrieve(question(), units, {"k": 1})
        self.assertNotEqual(a["config_hash"], retrieve(question(), units, {"k": 2})["config_hash"])
        self.assertNotEqual(a["config_hash"], retrieve(question(), units, {"k": 1, "tokenizer": "simple"})["config_hash"])

    def test_invalid_config_and_gold_inputs_rejected(self):
        for config in ({"k": 3}, {"k": True}, {"b": 1.1}, {"k1": 0}, {"k1": float("nan")},
                       {"tokenizer": "unknown"}, {"typo": 1}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                retrieve(question(), [], config)
        contaminated = question()
        contaminated["answer"] = "5"
        with self.assertRaisesRegex(ValueError, "gold"):
            retrieve(contaminated, [], {})
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            retrieve(question(), [unit("a", "x"), unit("a", "y")], {})

    def test_empty_candidates_and_empty_text(self):
        empty = retrieve(question(), [], {})
        self.assertEqual(empty["status"], "no_candidates")
        self.assertEqual(empty["selected_ids"], [])
        result = retrieve(question(), [unit("a", "")], {})
        self.assertEqual(result["scores"], {"a": 0.0})

    def test_local_adapter_missing_path_is_not_a_model_run(self):
        result = retrieve(question(), [unit("a", "revenue")], {"retriever": "sentence_transformers"})
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["selected_ids"], [])
        self.assertIn("already-existing local", result["error"]["message"])

    def test_mock_local_adapter_safety_flags_fingerprint_pooling_truncation(self):
        calls = []

        class FakeModel:
            def __init__(self, path, **kwargs):
                calls.append((path, kwargs))
                self.tokenizer = types.SimpleNamespace(truncation_side="right")

            def encode(self, texts, **kwargs):
                calls.append((texts, kwargs))
                return [[1, 0], [0.9, 0.1], [0, 1]]

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "1_Pooling").mkdir()
            (path / "modules.json").write_text(json.dumps([
                {"idx": 0, "name": "0", "path": "", "type": "sentence_transformers.models.Transformer"},
                {"idx": 1, "name": "1", "path": "1_Pooling", "type": "sentence_transformers.models.Pooling"}]))
            (path / "1_Pooling" / "config.json").write_text('{"pooling_mode_mean_tokens":true}')
            (path / "weights.safetensors").write_bytes(b"fake test bytes, not model weights")
            config = {"retriever": "sentence_transformers", "model_path": temp, "max_seq_length": 128}
            with patch.dict("sys.modules", {"sentence_transformers": types.SimpleNamespace(SentenceTransformer=FakeModel)}):
                result = retrieve(question(), [unit("a", "revenue"), unit("b", "cost")], config)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["selected_ids"], ["a", "b"])
                self.assertEqual(calls[0][1], {"device": "cpu", "local_files_only": True, "trust_remote_code": False})
                self.assertEqual(calls[1][1]["prompt"], "")
                self.assertEqual(result["config"]["truncation"]["max_seq_length"], 128)
                self.assertEqual(result["config"]["pooling"], [{"pooling_mode_mean_tokens": True}])
                (path / "weights.safetensors").write_bytes(b"changed test bytes")
                changed = retrieve(question(), [unit("a", "revenue"), unit("b", "cost")], config)
                self.assertNotEqual(result["config_hash"], changed["config_hash"])
                self.assertNotEqual(result["config"]["model_content_sha256"], changed["config"]["model_content_sha256"])


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.units = [unit("e1", "Revenue was 5", blocks=["b1"]), unit("e2", "Cost was 2", blocks=["b2"])]

    def retrieval(self, qid="q1", selected=None, config_hash="hash"):
        return {"question_id": qid, "doc_id": "d1", "retriever": "bm25", "status": "ok",
                "config_hash": config_hash, "candidate_ids": ["e1", "e2"],
                "selected_ids": ["e1"] if selected is None else selected}

    def test_percent_scale_and_numeric_comparison_are_distinct(self):
        self.assertEqual(answer_scores("5", "5%")["exact_match"], 0)
        self.assertEqual(answer_scores("5", "5%")["token_f1"], 0)
        self.assertEqual(answer_scores("5", "5", "thousand", "million")["exact_match"], 0)
        self.assertEqual(answer_scores("5", "5", "millions", "million")["exact_match"], 1)
        numeric = numeric_comparison("5", "5%")
        self.assertTrue(numeric["numeric_value_match"])
        self.assertFalse(numeric["percent_match"])
        self.assertFalse(numeric["numeric_with_units_match"])
        self.assertTrue(numeric_comparison("1,000.00", "1000")["numeric_value_match"])
        self.assertFalse(numeric_comparison("1", "1000", "thousand", "")["numeric_value_match"])

    def test_currency_signs_dates_and_multiset_f1(self):
        self.assertEqual(answer_scores("$5", "5")["exact_match"], 0)
        self.assertEqual(answer_scores("-5", "5")["exact_match"], 0)
        self.assertEqual(answer_scores("2020-01-01", "2020-01-02")["exact_match"], 0)
        self.assertTrue(numeric_comparison("(5)", "-5")["numeric_value_match"])
        self.assertEqual(answer_scores("(5)", "5")["exact_match"], 0)
        self.assertEqual(answer_scores("(5)", "-5")["exact_match"], 1)
        self.assertEqual(answer_scores("loss ($5)", "loss $5")["exact_match"], 0)
        self.assertEqual(answer_scores("(.5)", ".5")["exact_match"], 0)
        self.assertEqual(answer_scores("($ 5)", "$ 5")["exact_match"], 0)
        self.assertFalse(numeric_comparison("(-5)", "-5")["prediction_numeric"])
        self.assertAlmostEqual(answer_scores("income income", "income cost")["token_f1"], 0.5)
        self.assertEqual(answer_scores(["a", "b"], ["b", "a"])["exact_match"], 0)
        self.assertEqual(answer_scores(["a", "b"], ["b", "a"])["token_f1"], 1)

    def test_nonfinite_answers_rejected(self):
        for answer in (float("nan"), float("inf"), Decimal("NaN"), [float("-inf")]):
            with self.subTest(answer=answer), self.assertRaisesRegex(ValueError, "finite"):
                answer_scores(answer, "5")
        result = evaluate([prediction(answer=float("nan"))], [gold()], [], self.units)
        self.assertEqual(result["answer_metrics"]["E1"]["status_counts"]["parse_error"], 1)
        json.dumps(result, allow_nan=False)

    def test_inline_numeric_scale_and_conflict(self):
        result = numeric_comparison("$5 million", "$5", "million", "million")
        self.assertTrue(result["numeric_with_units_match"])
        conflict = numeric_comparison("$5 million", "$5", "thousand", "million")
        self.assertTrue(conflict["prediction_scale_conflict"])
        self.assertFalse(conflict["numeric_with_units_match"])
        self.assertTrue(numeric_comparison("$-5", "-$5")["numeric_with_units_match"])
        self.assertTrue(numeric_comparison("5%", "5", "", "percent")["numeric_with_units_match"])
        for answer, scale in (("5%", "million"), ("5 million", "thousand")):
            with self.subTest(answer=answer):
                same_conflict = numeric_comparison(answer, answer, scale, scale)
                self.assertFalse(same_conflict["numeric_with_units_match"])
                self.assertTrue(same_conflict["prediction_scale_conflict"])
                self.assertTrue(same_conflict["gold_scale_conflict"])
                self.assertEqual(answer_scores(answer, answer, scale, scale)["exact_match"], 0)

    def test_development_and_evaluation_splits_remain_visible(self):
        dev, evaluation = gold("q1"), gold("q2")
        dev["split"], evaluation["split"] = "development", "pilot_evaluation"
        result = evaluate([prediction(), prediction("q2", answer="wrong")], [dev, evaluation],
                          [self.retrieval(), self.retrieval("q2", selected=["e2"])], self.units)
        splits = result["answer_metrics"]["E1"]["by_split"]
        self.assertEqual(splits["development"]["exact_match"]["value"], 1)
        self.assertEqual(splits["pilot_evaluation"]["exact_match"]["value"], 0)
        retrieval_splits = result["retrieval_metrics"]["bm25:hash"]["by_split"]
        self.assertEqual(retrieval_splits["development"]["any_support_hit"]["value"], 1)
        self.assertEqual(retrieval_splits["pilot_evaluation"]["any_support_hit"]["value"], 0)
        self.assertEqual({row["split"] for row in result["result_rows"]}, {"development", "pilot_evaluation"})

    def test_unexecuted_models_are_null_not_zero(self):
        result = evaluate([prediction()], [gold()], [self.retrieval()], self.units)
        self.assertFalse(result["protocol"]["official"])
        self.assertEqual(result["answer_metrics"]["E1"]["exact_match"]["value"], 1)
        for group in ("E2", "E3", "E4"):
            summary = result["answer_metrics"][group]
            self.assertEqual(summary["status"], "not_executed")
            self.assertIsNone(summary["exact_match"]["value"])
            self.assertEqual(summary["exact_match"]["denominator"], 0)

    def test_status_denominators_remain_separate(self):
        statuses = ["answered", "abstained", "rejected", "api_error", "parse_error", "error", "not_run", "truncated"]
        truths = [gold(f"q{i}") for i in range(len(statuses))]
        predictions = [prediction(f"q{i}", status=status) for i, status in enumerate(statuses)]
        summary = evaluate(predictions, truths, [], self.units)["answer_metrics"]["E1"]
        self.assertEqual(summary["exact_match"], {"numerator": 1.0, "denominator": 1, "value": 1.0})
        self.assertEqual(summary["executed_questions"], 7)
        self.assertEqual(summary["status_counts"]["abstention"], 1)
        self.assertEqual(summary["status_counts"]["rejection"], 1)
        self.assertEqual(summary["status_counts"]["api_error"], 1)
        self.assertEqual(summary["answer_coverage_executed"]["value"], 1 / 7)
        self.assertEqual(summary["correct_per_executed"], {"numerator": 1.0, "denominator": 7, "value": 1 / 7})

    def test_all_abstaining_rule_attempts_have_operational_zero(self):
        result = evaluate([prediction(status="abstained")], [gold()], [], self.units)
        self.assertEqual(result["answer_metrics"]["E1"]["correct_per_executed"],
                         {"numerator": 0, "denominator": 1, "value": 0})
        self.assertIsNone(result["answer_metrics"]["E2"]["correct_per_executed"]["value"])

    def test_verified_support_and_complete_coverage_denominators(self):
        truths = [gold("q1", support=["b1", "b2"]), gold("q2", complete=False),
                  gold("q3", verified=False), gold("q4", verified="true")]
        records = [self.retrieval(f"q{i}") for i in range(1, 5)]
        result = evaluate([], truths, records, self.units)
        metrics = result["retrieval_metrics"]["bm25:hash"]
        self.assertEqual(metrics["any_support_hit"], {"numerator": 2, "denominator": 2, "value": 1})
        self.assertEqual(metrics["complete_support_coverage"], {"numerator": 0, "denominator": 1, "value": 0})
        self.assertEqual(metrics["exclusion_counts"]["unverified_support_mapping"], 2)

    def test_missing_support_units_not_silently_dropped(self):
        result = evaluate([], [gold(support=["unrepresented"])], [self.retrieval()], self.units)
        self.assertEqual(result["retrieval_rows"][0]["unrepresented_support_block_ids"], ["unrepresented"])
        self.assertEqual(result["retrieval_metrics"]["bm25:hash"]["any_support_hit"]["value"], 0)

    def test_different_retrieval_configs_not_merged(self):
        result = evaluate([], [gold()], [self.retrieval(config_hash="k1"), self.retrieval(config_hash="k2")], self.units)
        self.assertEqual(len(result["retrieval_metrics"]), 2)

    def test_citations_only_check_existence_not_support(self):
        pred = prediction()
        pred["citations"] = ["e1", "missing", "foreign", "e1"]
        units = self.units + [unit("foreign", "5", "different-document")]
        result = evaluate([pred], [gold()], [], units)
        summary = result["answer_metrics"]["E1"]
        self.assertEqual(summary["citation_existence"], {"numerator": 1, "denominator": 3, "value": 1 / 3})
        self.assertEqual(summary["citation_support"]["status"], "not_assessed")

    def test_whole_page_separate_and_inputs_unmodified(self):
        units = deepcopy(self.units)
        units[0]["whole_page"] = True
        inputs = [[prediction()], [gold()], [self.retrieval()], units]
        before = deepcopy(inputs)
        result = evaluate(*inputs)
        self.assertEqual(inputs, before)
        scopes = result["answer_metrics"]["E1"]["by_evidence_scope"]
        self.assertEqual(scopes["whole_page"]["exact_match"]["denominator"], 1)
        self.assertEqual(scopes["local"]["exact_match"]["denominator"], 0)
        json.dumps(result, allow_nan=False)

    def test_duplicate_predictions_and_unknown_questions_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate final"):
            evaluate([prediction(), prediction()], [gold()], [], self.units)
        with self.assertRaisesRegex(ValueError, "known question_id"):
            evaluate([prediction("unknown")], [gold()], [], self.units)

    def test_empty_answer_is_parse_error_not_correct(self):
        result = evaluate([prediction(answer="")], [gold()], [], self.units)
        summary = result["answer_metrics"]["E1"]
        self.assertEqual(summary["status_counts"]["parse_error"], 1)
        self.assertIsNone(summary["exact_match"]["value"])


if __name__ == "__main__":
    unittest.main()
