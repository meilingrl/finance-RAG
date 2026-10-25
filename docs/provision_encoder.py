"""Fetch only the pinned official MiniLM files, checking every byte count/hash.

This opt-in provisioning command uses the network for public downloads only.
The pilot retrieval adapter does not call this script or download anything.
Existing mismatched files are not replaced. No model code is executed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True,
                        help="Empty or already verified directory outside the deliverable tree")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "artifacts/encoder-provisioning/model-manifest.json").read_text())
    destination = args.model_dir.expanduser().resolve()
    if destination.is_relative_to(root):
        raise ValueError("Keep model weights outside the deliverable tree")
    expected_names = {entry["file"] for entry in manifest["files"]}
    if destination.exists():
        extras = {p.relative_to(destination).as_posix() for p in destination.rglob("*")
                  if p.is_file()} - expected_names
        if extras:
            raise ValueError(f"Unexpected files in model directory: {sorted(extras)}")
    destination.mkdir(parents=True, exist_ok=True)
    prefix = ("https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/"
              + manifest["revision"] + "/")
    for entry in manifest["files"]:
        relative = Path(entry["file"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe manifest path")
        url = entry["url"]
        if url != prefix + relative.as_posix():
            raise ValueError("Manifest URL is not the pinned official model repository")
        path = destination / relative
        if path.is_symlink() or not path.resolve().is_relative_to(destination):
            raise ValueError("Model paths must remain inside the destination")
        if path.exists():
            if path.stat().st_size != entry["size"] or digest(path) != entry["sha256"]:
                raise ValueError(f"Existing file has a different hash: {relative}")
            print(f"Verified existing {relative}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".part")
        if temporary.exists():
            raise ValueError(f"Partial file already exists; inspect it before retrying: {temporary}")
        try:
            with urllib.request.urlopen(url, timeout=180) as response, temporary.open("xb") as output:
                copied = 0
                while chunk := response.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > entry["size"]:
                        raise ValueError(f"Download exceeded expected size: {relative}")
                    output.write(chunk)
            if temporary.stat().st_size != entry["size"] or digest(temporary) != entry["sha256"]:
                raise ValueError(f"Downloaded bytes did not match the manifest: {relative}")
            temporary.rename(path)
        except Exception:
            # Leave the partial file for inspection. Never try an unverified mirror.
            raise
        print(f"Downloaded and verified {relative}")
    print(f"Ready: {destination} ({manifest['total_bytes']} bytes)")


if __name__ == "__main__":
    main()
