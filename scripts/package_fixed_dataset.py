"""Build an immutable, content-addressed fixed dataset release from registrations.

No original data or resource registration is modified. The full candidate
resources.json in the author's data directory is deliberately never used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.datasets import CHUNK_BYTES, _relative, _sha, _verify_tree

BUNDLE_ID = 'fixed-course-v1'
ARCHIVE_NAME = 'fixed-course-data-v1.zip'
DEFAULT_URL = 'https://github.com/meilingrl/finance-RAG/releases/download/fixed-data-v1/' + ARCHIVE_NAME


def _encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def _allowed(path):
    relative = _relative(path)
    forbidden = {'.DS_Store', '__MACOSX', '.env', 'config.llm.local.json', '.local-secrets', 'credentials', 'secrets'}
    if any(part in forbidden or part.startswith('.env.') for part in relative.parts) or relative.suffix in ('.key', '.pem'):
        raise ValueError('Private or platform file cannot be packaged: ' + path)
    return relative


def _entry(name):
    info = zipfile.ZipInfo(name, date_time=(2026, 10, 27, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _split_archive(archive_path, part_bytes, archive_url):
    parts = []
    folder = archive_path.parent / 'parts'
    folder.mkdir()
    with archive_path.open('rb') as source:
        while chunk := source.read(min(CHUNK_BYTES, part_bytes)):
            index = len(parts)
            path = folder / (archive_path.name + f'.part{index:04d}')
            digest = hashlib.sha256()
            size = 0
            with path.open('xb') as output:
                while chunk:
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    if size == part_bytes:
                        break
                    chunk = source.read(min(CHUNK_BYTES, part_bytes - size))
            parts.append(dict(archive_url=archive_url + f'.part{index:04d}', bytes=size, sha256=digest.hexdigest()))
    return parts


def package_fixed_dataset(project_root, output_dir, *, archive_url=DEFAULT_URL, part_bytes=None):
    """Create a new release directory and return its archive/config/verification."""
    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    if part_bytes is not None and (type(part_bytes) is not int or part_bytes <= 0):
        raise ValueError('part_bytes must be a positive integer')
    if output.exists():
        raise ValueError('Release output must not exist; preserve prior releases')
    output.parent.mkdir(parents=True, exist_ok=True)
    files = []
    objects = {}
    snapshots = {}
    with tempfile.TemporaryDirectory(prefix='.fixed-release-', dir=output.parent) as temporary:
        temporary = Path(temporary)
        tree = temporary / 'verification'
        tree.mkdir()

        def bind(name, source, expected_sha=None):
            _allowed(name)
            target = tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(source, bytes):
                target.write_bytes(source)
            else:
                source = Path(source)
                if not source.is_file() or source.is_symlink():
                    raise ValueError('Registered regular file missing: ' + name)
                shutil.copyfile(source, target)
            sha = _sha(target)
            if expected_sha is not None and sha != expected_sha:
                raise ValueError('Registered resource SHA256 mismatch: ' + name)
            files.append(dict(path=name, sha256=sha, size=target.stat().st_size))
            objects.setdefault(sha, target)

        for role, resource_id in [('formal', 'formal-main-course'), ('demo', 'development-reviewed-v1')]:
            record_path = root / 'artifacts/formal-v1/resources' / (resource_id + '.json')
            record = json.loads(record_path.read_bytes())
            manifest = record['manifest']
            if manifest.get('resource_set_id') != resource_id or manifest.get('purpose') != ('formal' if role == 'formal' else 'demo'):
                raise ValueError('Unexpected registered fixed resource set')
            if role == 'formal' and (manifest.get('execution_scope') != 'main_only' or manifest.get('review_policy') != 'automatic_course'):
                raise ValueError('Registered formal course main set required')
            source_root = Path(record['root']).resolve()
            seen = set()
            for resource in manifest['resources']:
                relative = _allowed(resource['path'])
                if str(relative) in seen:
                    raise ValueError('Duplicate registered resource path')
                seen.add(str(relative))
                source = source_root / str(relative)
                if not source.resolve().is_relative_to(source_root):
                    raise ValueError('Registered resource escapes its root')
                bind(role + '/' + str(relative), source, resource['sha256'])
            manifest_bytes = _encoded(manifest)
            bind(role + '/resources.json', manifest_bytes)
            snapshots[role] = dict(resource_set_id=resource_id, manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                                   resource_count=len(manifest['resources']))

        for name in ('ATTRIBUTION.md', 'APACHE-2.0.txt', 'CC-BY-4.0.txt'):
            bind('LICENSES/' + name, root / 'docs/delivery/fixed-dataset' / name)
        bundle = dict(schema_version=1, bundle_id=BUNDLE_ID, formal_root='formal', demo_root='demo',
                      source_manifests=snapshots, files=sorted(files, key=lambda x: x['path']))
        bundle_bytes = _encoded(bundle)
        (tree / 'bundle.json').write_bytes(bundle_bytes)
        _verify_tree(tree, bundle)
        release = temporary / 'release'
        release.mkdir()
        archive_path = release / ARCHIVE_NAME
        with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            archive.writestr(_entry('bundle.json'), bundle_bytes)
            for sha, source in sorted(objects.items()):
                with archive.open(_entry('objects/' + sha), 'w') as destination, source.open('rb') as content:
                    shutil.copyfileobj(content, destination, CHUNK_BYTES)
        archive_sha = _sha(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise ValueError('Release ZIP CRC verification failed')
        config = dict(schema_version=1, bundle_id=BUNDLE_ID, archive_url=archive_url,
                      archive_sha256=archive_sha, archive_bytes=archive_path.stat().st_size,
                      manifest_sha256=hashlib.sha256(bundle_bytes).hexdigest(), install_dir='data/fixed-course-v1',
                      formal_root='formal', demo_root='demo')
        if part_bytes is not None:
            config['archive_parts'] = _split_archive(archive_path, part_bytes, archive_url)
            config.pop('archive_url')
        verification = dict(bundle_id=BUNDLE_ID, file_count=len(files), unique_object_count=len(objects),
                            materialized_bytes=sum(x['size'] for x in files),
                            unique_object_bytes=sum(p.stat().st_size for p in objects.values()),
                            archive_bytes=config['archive_bytes'], archive_sha256=archive_sha,
                            manifest_sha256=config['manifest_sha256'], all_source_sha256_verified=True,
                            zip_crc_verified=True, original_assets_modified=False)
        (release / 'bundle.json').write_bytes(bundle_bytes)
        (release / 'fixed_dataset.json').write_bytes(_encoded(config))
        (release / 'verification.json').write_bytes(_encoded(verification))
        (release / (ARCHIVE_NAME + '.sha256')).write_text(archive_sha + '  ' + ARCHIVE_NAME + '\n')
        shutil.copytree(root / 'docs/delivery/fixed-dataset', release / 'LICENSES')
        (release / 'RELEASE.md').write_text(
            '# Fixed course data v1\n\n'
            'Use `python scripts/setup_data.py --archive PATH/TO/' + ARCHIVE_NAME + '` for offline installation. '
            'The public installer uses the same ZIP and SHA256 from `configs/fixed_dataset.json`.\n\n'
            'This directory is a prepared release. Publication is a separate operation. '
            'The bundle preserves registered main-only course and reviewed development resource bytes. '
            'See `bundle.json`, `verification.json` and `LICENSES/ATTRIBUTION.md`.\n')
        os.rename(release, output)
    result = dict(archive_path=str(output / ARCHIVE_NAME), config=config, verification=verification)
    if config.get('archive_parts'):
        result['part_paths'] = [str(output / 'parts' / (ARCHIVE_NAME + f'.part{index:04d}'))
                                for index in range(len(config['archive_parts']))]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/dataset-release-20261027')
    parser.add_argument('--archive-url', default=DEFAULT_URL)
    parser.add_argument('--part-bytes', type=int, help='Also produce ordered release parts of at most this many bytes')
    args = parser.parse_args(argv)
    print(json.dumps(package_fixed_dataset(args.project_root, args.output, archive_url=args.archive_url,
                                          part_bytes=args.part_bytes), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
