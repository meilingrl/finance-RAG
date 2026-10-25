"""Small serialization, identity, validation and provenance primitives."""
from __future__ import annotations
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

GOLD_KEYS = frozenset({"answer", "answers", "derivation", "facts", "block_mapping", "gold", "support_block_ids", "complete_support_block_ids", "mapping_verified", "support_complete"})

def now():
    return datetime.now(timezone.utc).isoformat()

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]

def write_jsonl(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for value in values:
            f.write(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")

def reject_gold(value, location="inference"):
    """Defense in depth; provenance/gold never crosses the inference boundary."""
    if isinstance(value, dict):
        forbidden = GOLD_KEYS.intersection(value)
        if forbidden:
            raise ValueError(f"Forbidden evaluation keys in {location}: {sorted(forbidden)}")
        for key, item in value.items():
            reject_gold(item, f"{location}.{key}")
    elif isinstance(value, (list, tuple)):
        for item in value:
            reject_gold(item, location)

def bbox(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("Bounding boxes must contain [x0, y0, x1, y1]")
    result = [float(x) for x in value]
    if not all(math.isfinite(x) for x in result) or result[2] <= result[0] or result[3] <= result[1]:
        raise ValueError("Invalid or non-finite bounding box")
    return result

def contains(outer, inner, epsilon=1e-6):
    return outer[0] - epsilon <= inner[0] and outer[1] - epsilon <= inner[1] and outer[2] + epsilon >= inner[2] and outer[3] + epsilon >= inner[3]

def intersects(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

def safe_identifier(value):
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value) or value in {".", ".."}:
        raise ValueError(f"Unsafe file identifier: {value!r}")
    return value

def unique(records, key):
    ids = [record[key] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate {key}")

def safe_event(path, event, **fields):
    """Only callers' bounded metadata; never environment, headers, credentials or URLs."""
    allowed = {"status", "count", "elapsed_seconds", "error_code", "config_hash", "run_kind", "stage"}
    if set(fields) - allowed:
        raise ValueError("Unsupported log metadata")
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": now(), "event": event, **fields}, ensure_ascii=False) + "\n")


def portable_path(path):
    """Paths inside the project stay relocatable when commands run at its root."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)
