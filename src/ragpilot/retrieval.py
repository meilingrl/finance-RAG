"""Within-document retrieval; no answers, gold mappings, or descriptions enter it.

The default is a standard-library BM25 Okapi implementation with positive IDF
log(1 + (N-df+0.5)/(df+0.5)), k1=1.5, b=0.75. Repeated query terms contribute
repeatedly. Ties are broken by evidence_id, independent of input ordering.

Finance tokenization uses NFKC, casefolding, Unicode-dash normalization, and no
stemming/stopword removal. It keeps signs, currency symbols, decimal/thousands
separators, %, abbreviations and internal hyphens (5%, -1,234.50, non-GAAP,
10-K, U.S.). It does not interpret accounting parentheses or change units.
Simple tokenization is Unicode \\w+ after the same normalization; punctuation,
percent and signs are lost deliberately for the small tokenizer diagnostic.

The optional sentence-transformers adapter is lazy, CPU-only and local-only.
Calling it explicitly can load a local model; importing this module cannot.
No installation, download, remote API, training, or model run is performed by
the offline BM25 path. The adapter has not been validated on a real model.
API reference: https://sbert.net/docs/package_reference/sentence_transformer/model.html
"""

from __future__ import annotations

from collections import Counter
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import time
import unicodedata


_MODEL_CACHE = {}
_EMBEDDING_CACHE = {}

TOKENIZER_VERSION = "finance-nfkc-v1"
_DASHES = str.maketrans({c: "-" for c in "‐‑‒–—−"})
_FINANCE_TOKEN = re.compile(
    r"(?:[+\-]?[$€£¥]?|[$€£¥][+\-]?)(?:[^\W_]+(?:[.,&'/-][^\W_]+)*|\.\d+)%?"
    r"|[%$€£¥]", re.UNICODE
)
_GOLD_FIELDS = frozenset({
    "answer", "answers", "gold_answer", "scale", "derivation", "facts",
    "support_block_ids", "complete_support_block_ids", "mapping_verified", "support_complete", "gold",
})


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_DASHES).casefold()


def tokenize(text: str, mode: str = "finance") -> list[str]:
    """Deterministic tokenizer. In finance mode, '5 %' is equivalent to '5%'."""
    if not isinstance(text, str):
        raise ValueError("Tokenization requires a string")
    text = _normalize(text)
    if mode == "simple":
        return re.findall(r"\w+", text, flags=re.UNICODE)
    if mode != "finance":
        raise ValueError("tokenizer must be 'finance' or 'simple'")
    text = re.sub(r"(?<=\d)\s+%", "%", text)
    return _FINANCE_TOKEN.findall(text)


def finance_tokenize(text: str) -> list[str]:
    return tokenize(text, "finance")


def simple_tokenize(text: str) -> list[str]:
    return tokenize(text, "simple")


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _config(config: dict) -> dict:
    retriever = config.get("retriever", "bm25")
    if retriever not in {"bm25", "sentence_transformers"}:
        raise ValueError("retriever must be bm25 or sentence_transformers")
    k = config.get("k", 2)
    if isinstance(k, bool) or not isinstance(k, int) or k not in (1, 2):
        raise ValueError("Only the planned k=1 and k=2 checks are supported")
    effective = {"retriever": retriever, "k": k, "scope": "within_document",
                 "tie_break": "evidence_id_ascending", "implementation_version": "1"}
    if retriever == "bm25":
        allowed = {"retriever", "k", "tokenizer", "k1", "b"}
        mode = config.get("tokenizer", "finance")
        tokenize("", mode)
        k1, b = config.get("k1", 1.5), config.get("b", 0.75)
        if (isinstance(k1, bool) or not isinstance(k1, (int, float))
                or not math.isfinite(k1) or k1 <= 0):
            raise ValueError("k1 must be a finite positive number")
        if (isinstance(b, bool) or not isinstance(b, (int, float))
                or not math.isfinite(b) or not 0 <= b <= 1):
            raise ValueError("b must be between 0 and 1")
        effective.update(tokenizer=mode, tokenizer_version=TOKENIZER_VERSION,
                         k1=float(k1), b=float(b), idf="log1p_positive_robertson",
                         query_term_frequency="count", stopwords=False, stemming=False)
    else:
        allowed = {"retriever", "k", "model_path", "max_seq_length", "batch_size"}
        for name, default in (("max_seq_length", 256), ("batch_size", 8)):
            value = config.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
            effective[name] = value
        effective.update(model_path=str(config.get("model_path", "")), device="cpu",
                         local_files_only=True, trust_remote_code=False,
                         similarity="cosine", normalize_embeddings=True,
                         prompt="", adapter_real_model_validation="runtime_logged_per_result")
    unknown = set(config) - allowed
    if unknown:
        raise ValueError("Unsupported retrieval configuration keys: " + ", ".join(sorted(unknown)))
    return effective


def _bm25(question: str, candidates: list[dict], config: dict) -> tuple[list[float], dict]:
    started = time.perf_counter()
    documents = [Counter(tokenize(unit["text"], config["tokenizer"])) for unit in candidates]
    lengths = [sum(document.values()) for document in documents]
    average = sum(lengths) / len(lengths) if lengths else 0.0
    document_frequency = Counter(token for document in documents for token in document)
    indexed = time.perf_counter()
    query = Counter(tokenize(question, config["tokenizer"]))
    scores = []
    for document, length in zip(documents, lengths):
        score = 0.0
        for term, count in query.items():
            tf = document.get(term, 0)
            if not tf:
                continue
            df = document_frequency[term]
            idf = math.log1p((len(documents) - df + 0.5) / (df + 0.5))
            norm = 1 - config["b"] + config["b"] * length / average
            score += count * idf * tf * (config["k1"] + 1) / (tf + config["k1"] * norm)
        scores.append(score)
    return scores, {"index_seconds": indexed - started,
                    "query_seconds": time.perf_counter() - indexed}


def _local_model_metadata(model_path: str) -> tuple[Path, dict]:
    """Fingerprint names and bytes, not timestamps. Never resolve a Hub model ID."""
    if not model_path:
        raise ValueError("Set model_path to an already-existing local SentenceTransformer directory")
    path = Path(model_path).expanduser()
    if not path.is_dir():
        raise ValueError("model_path is not an existing local directory; downloads are disabled")
    path = path.resolve()
    digest, files, total = hashlib.sha256(), [], 0
    for file in sorted(path.rglob("*")):
        if file.is_symlink() and not file.resolve().is_relative_to(path):
            raise ValueError("Model directory contains a symlink outside the model directory")
        if not file.is_file():
            continue
        relative = file.relative_to(path).as_posix()
        size = file.stat().st_size
        digest.update(json.dumps([relative, size], separators=(",", ":")).encode() + b"\0")
        with file.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        total += size
        files.append(relative)
    if "modules.json" not in files:
        raise ValueError("Local model must include modules.json; automatic model construction is disabled")
    modules = json.loads((path / "modules.json").read_text(encoding="utf-8"))
    if not isinstance(modules, list) or not modules:
        raise ValueError("modules.json must contain a nonempty module list")
    pooling = []
    for module in modules:
        if not isinstance(module, dict) or not str(module.get("type", "")).startswith("sentence_transformers.models."):
            raise ValueError("Only built-in sentence_transformers.models modules are supported")
        module_path = (path / str(module.get("path", ""))).resolve()
        if not module_path.is_relative_to(path):
            raise ValueError("modules.json contains a path outside the local model directory")
        if str(module["type"]).endswith(".Pooling"):
            config_path = module_path / "config.json"
            pooling.append(json.loads(config_path.read_text(encoding="utf-8")))
    if not pooling:
        raise ValueError("A saved Pooling module is required so the pooling policy can be recorded")
    return path, {"model_content_sha256": digest.hexdigest(), "model_files": files,
                  "model_bytes": total, "pooling": pooling, "model_modules": modules}


def _sentence_scores(question: str, candidates: list[dict], config: dict) -> tuple[list[float], dict]:
    started = time.perf_counter()
    path, metadata = _local_model_metadata(config["model_path"])
    config.update(metadata)
    config["model_path"] = str(path)
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError("Optional sentence-transformers dependency is unavailable; nothing was installed or downloaded") from exc
    try:
        config["sentence_transformers_version"] = importlib.metadata.version("sentence-transformers")
    except importlib.metadata.PackageNotFoundError:
        config["sentence_transformers_version"] = "unknown"
    # Cache only by immutable content fingerprint and exact sequence policy.
    cache_key=(str(path),metadata["model_content_sha256"],config["max_seq_length"])
    model_cached=cache_key in _MODEL_CACHE
    if not model_cached:
        model = SentenceTransformer(str(path), device="cpu", local_files_only=True,
                                    trust_remote_code=False)
        model.max_seq_length=config["max_seq_length"]
        _MODEL_CACHE[cache_key]=model
    model=_MODEL_CACHE[cache_key]
    config["truncation"]={"max_seq_length":model.max_seq_length,
                          "side":getattr(model.tokenizer,"truncation_side","unknown"),
                          "policy":"tokenizer_max_sequence_length_including_special_tokens",
                          "per_input_truncation_counts":"retrieval.timing.input_token_counts"}
    loaded=time.perf_counter()
    input_texts=[question]+[u["text"] for u in candidates]
    token_counts=None
    if callable(model.tokenizer):
        measured=model.tokenizer(input_texts,add_special_tokens=True,truncation=False,padding=False)
        token_counts=[len(tokens) for tokens in measured["input_ids"]]
    index_key=(cache_key,_hash([(u["evidence_id"],u["text"]) for u in candidates]))
    index_cached=index_key in _EMBEDDING_CACHE
    texts=[question] if index_cached else input_texts
    vectors=model.encode(texts,batch_size=config["batch_size"],show_progress_bar=False,
                         convert_to_numpy=True,normalize_embeddings=True,prompt="")
    vectors=[[float(value) for value in vector] for vector in vectors]
    if len(vectors)!=len(texts) or not vectors[0]:
        raise ValueError("Sentence encoder returned an invalid number of embeddings")
    if not index_cached:_EMBEDDING_CACHE[index_key]=vectors[1:]
    query,documents=vectors[0],_EMBEDDING_CACHE[index_key]
    scores=[]
    for vector in documents:
        if len(vector)!=len(query) or not all(math.isfinite(v) for v in query+vector):
            raise ValueError("Sentence encoder returned invalid embeddings")
        norm=math.sqrt(sum(v*v for v in query)*sum(v*v for v in vector))
        scores.append(sum(a*b for a,b in zip(query,vector))/norm if norm else 0.0)
    return scores,{"model_load_and_fingerprint_seconds":loaded-started,
                   "encode_and_similarity_seconds":time.perf_counter()-loaded,
                   "model_cache_hit":model_cached,"index_cache_hit":index_cached,
                   "input_token_counts":token_counts,
                   "input_truncated":[n>model.max_seq_length for n in token_counts] if token_counts is not None else None,
                   "token_count_order":["question"]+[u["evidence_id"] for u in candidates]}


def retrieve(question: dict, units: list[dict], config: dict) -> dict:
    """Rank only units in question['doc_id']; k is 1 or 2, never padded.

    Question fields: question_id, doc_id, question. Unit fields: evidence_id,
    doc_id, text; other input metadata is ignored. Known gold fields are rejected.
    Invalid inputs raise ValueError. Optional-model failures return an explicit
    unavailable record with no selected IDs or scores; they are not BM25 runs.
    `scores` maps selected IDs to scores, `candidate_scores` maps every candidate.
    """
    started = time.perf_counter()
    for record in [question] + list(units):
        if not isinstance(record, dict):
            raise ValueError("Question and evidence units must be dictionaries")
        if _GOLD_FIELDS.intersection(record):
            raise ValueError("Evaluation/gold fields are forbidden in retrieval inputs")
    for field in ("question_id", "doc_id", "question"):
        if not isinstance(question.get(field), str) or not question[field].strip():
            raise ValueError(f"Question requires a nonempty {field}")
    effective = _config(config)
    candidates = [u for u in units if u.get("doc_id") == question["doc_id"]]
    for unit in candidates:
        if not isinstance(unit.get("evidence_id"), str) or not unit["evidence_id"]:
            raise ValueError("Every candidate requires a nonempty evidence_id")
        if not isinstance(unit.get("text"), str):
            raise ValueError("Every candidate requires text")
    candidates.sort(key=lambda u: u["evidence_id"])
    ids = [u["evidence_id"] for u in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate evidence_id within the document")
    result = {"question_id": question["question_id"], "doc_id": question["doc_id"],
              "retriever": effective["retriever"], "candidate_ids": ids,
              "candidate_count": len(ids), "selected_ids": [], "scores": {},
              "candidate_scores": {}, "requested_k": effective["k"], "actual_k": 0,
              "config": effective, "status": "ok", "error": None, "timing": {}}
    if not candidates:
        result["status"] = "no_candidates"
    else:
        try:
            score_fn = _bm25 if effective["retriever"] == "bm25" else _sentence_scores
            scores, timing = score_fn(question["question"], candidates, effective)
            result["timing"] = timing
            by_id = dict(zip(ids, scores))
            selected = sorted(ids, key=lambda evidence_id: (-by_id[evidence_id], evidence_id))[:effective["k"]]
            result.update(selected_ids=selected, actual_k=len(selected), candidate_scores=by_id,
                          scores={evidence_id: by_id[evidence_id] for evidence_id in selected})
        except Exception as exc:
            if effective["retriever"] == "bm25":
                raise
            result.update(status="unavailable", error={"type": type(exc).__name__, "message": str(exc)})
    result["config_hash"] = _hash(effective)
    result["elapsed_seconds"] = time.perf_counter() - started
    return result
