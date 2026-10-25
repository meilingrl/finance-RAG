"""Neutral synthetic smoke test; never opens pilot questions or answer labels."""
import os
os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                  HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1",
                  CUDA_VISIBLE_DEVICES="", OMP_NUM_THREADS="2",
                  TOKENIZERS_PARALLELISM="false")
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import socket
import sys
import time

network_attempts = []
def no_network(*args, **kwargs):
    network_attempts.append(str(args[1:] if len(args) > 1 else args))
    raise RuntimeError("Network is forbidden during local encoder verification")
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.create_connection = no_network

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--model-path", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
MODEL = args.model_path.resolve()
SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))
from ragpilot.retrieval import retrieve, _local_model_metadata

started = time.perf_counter()
torch.set_num_threads(2)
model = SentenceTransformer(str(MODEL), device="cpu", local_files_only=True,
                            trust_remote_code=False,
                            model_kwargs={"use_safetensors": True})
sentences = ["A cat is resting on a couch.", "The cat sleeps on the sofa.",
             "A rocket launches into space.", "A cat is resting on a couch."]
vectors = model.encode(sentences, batch_size=8, show_progress_bar=False,
                       normalize_embeddings=True, convert_to_numpy=True, prompt="")
similarities = vectors @ vectors.T
assert vectors.shape == (4, 384)
assert np.isfinite(vectors).all()
assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-6)
assert np.allclose(vectors[0], vectors[3], atol=1e-6)
assert similarities[0, 1] > similarities[0, 2]
assert str(model.device) == "cpu"
assert torch.version.cuda is None
assert not any(x.suffix in {".bin", ".pkl", ".pickle", ".pt"} for x in MODEL.rglob("*"))

question = {"question_id": "synthetic-smoke", "doc_id": "synthetic",
            "question": sentences[0]}
units = [{"evidence_id": "cat", "doc_id": "synthetic", "text": sentences[1]},
         {"evidence_id": "rocket", "doc_id": "synthetic", "text": sentences[2]}]
result = retrieve(question, units, {"retriever": "sentence_transformers", "k": 2,
                                   "model_path": str(MODEL), "max_seq_length": 256,
                                   "batch_size": 8})
assert result["status"] == "ok", result
assert result["selected_ids"] == ["cat", "rocket"], result
assert all(np.isfinite(list(result["candidate_scores"].values())))
assert not network_attempts, network_attempts
_, metadata = _local_model_metadata(str(MODEL))
record = {
    "status": "passed", "verified_at_utc": datetime.now(timezone.utc).isoformat(), "test_data": "four neutral synthetic sentences only",
    "model_path": str(MODEL), "python": sys.executable,
    "python_version": sys.version, "device": str(model.device),
    "cuda_runtime": torch.version.cuda, "parameters": sum(p.numel() for p in model.parameters()),
    "max_seq_length": model.max_seq_length, "dimensions": 384,
    "shape": list(vectors.shape), "norms": np.linalg.norm(vectors, axis=1).tolist(),
    "similarities": similarities.tolist(), "numpy_dtype": str(vectors.dtype),
    "embedding_bytes_sha256": hashlib.sha256(vectors.tobytes()).hexdigest(),
    "python_socket_connect_attempts": network_attempts,
    "offline_flags": {k: os.environ[k] for k in ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
        "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK", "CUDA_VISIBLE_DEVICES"]},
    "direct_load_use_safetensors": True, "weight_files": ["model.safetensors"],
    "packages": {name: importlib.metadata.version(name) for name in ["sentence-transformers",
        "torch", "transformers", "safetensors", "huggingface-hub", "tokenizers", "numpy",
        "scipy", "scikit-learn"]},
    "adapter_source_sha256": hashlib.sha256((SOURCE/"ragpilot/retrieval.py").read_bytes()).hexdigest(),
    "adapter_result": result, "model_metadata": metadata,
    "elapsed_seconds": time.perf_counter() - started,
}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(record, indent=2)+"\n")
print(json.dumps(record, indent=2))
