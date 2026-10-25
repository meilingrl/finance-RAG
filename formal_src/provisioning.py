"""Opt-in public MiniLM download with pinned revision, sizes and SHA256s."""
from __future__ import annotations
import hashlib
from pathlib import Path
import re
import urllib.request


def provision(destination, manifest, *, opener=urllib.request.urlopen):
    destination = Path(destination).resolve()
    revision = manifest['revision']
    if manifest['repo_id'] != 'sentence-transformers/all-MiniLM-L6-v2' or not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Pinned official MiniLM repository required')
    prefix = 'https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/' + revision + '/'
    entries = manifest['files']
    names = [entry['file'] for entry in entries]
    if len(names) != len(set(names)) or sum(entry['size'] for entry in entries) != manifest['total_bytes']:
        raise ValueError('Inconsistent model manifest')
    if destination.exists() and any(p.relative_to(destination).as_posix() not in names for p in destination.rglob('*') if p.is_file()):
        raise ValueError('Unexpected existing model files; preserve and inspect')
    for entry in entries:
        relative = Path(entry['file'])
        if relative.is_absolute() or '..' in relative.parts or entry['url'] != prefix + relative.as_posix():
            raise ValueError('Unsafe model binding')
        if not re.fullmatch('[0-9a-f]{64}', entry['sha256']) or type(entry['size']) is not int or entry['size'] < 1:
            raise ValueError('Invalid pinned model identity')
        path = destination / relative
        if path.is_symlink() or not path.resolve().is_relative_to(destination):
            raise ValueError('Unsafe model destination')
    downloaded = 0
    for entry in entries:
        path = destination / entry['file']
        if path.exists():
            if path.stat().st_size != entry['size'] or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
                raise ValueError('Existing model identity differs; preserve and inspect')
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.part')
        # A failed request leaves its partial file for explicit inspection.
        with temporary.open('xb') as output, opener(entry['url'], timeout=180) as response:
            count = 0
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > entry['size']:
                    raise ValueError('Model download exceeded pinned size')
                output.write(chunk)
        if count != entry['size'] or hashlib.sha256(temporary.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Downloaded model identity differs')
        temporary.rename(path)
        downloaded += count
    return {'revision': revision, 'file_count': len(entries), 'bytes': manifest['total_bytes'],
            'downloaded_bytes': downloaded, 'verified': True}
