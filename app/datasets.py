"""Install the hash-pinned course dataset without changing existing local assets.

The release ZIP stores each SHA256 object once. Its file inventory is hash-pinned
by configuration; archive paths are never passed to extractall().
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from http.client import HTTPException, InvalidURL
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import ssl
import stat
import tempfile
import threading
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile

CHUNK_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_DOWNLOAD_WORKERS = 8
MAX_PART_ATTEMPTS = 5


class FixedDatasetError(ValueError):
    """A fixed data release could not be verified or safely installed."""


def _relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value or '\x00' in value:
        raise FixedDatasetError('Unsafe dataset path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('', '.', '..') for part in value.split('/')):
        raise FixedDatasetError('Unsafe dataset path')
    return path


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(CHUNK_BYTES), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _https_url(value):
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme == 'https' and bool(parsed.netloc) and not parsed.username and not parsed.password


def _json(content):
    try:
        value = json.loads(content)
    except (ValueError, UnicodeError) as error:
        raise FixedDatasetError('Invalid dataset JSON') from error
    if not isinstance(value, dict):
        raise FixedDatasetError('Invalid dataset object')
    return value


def _config(project_root):
    try:
        config = _json((project_root / 'configs/fixed_dataset.json').read_bytes())
    except OSError as error:
        raise FixedDatasetError('Fixed dataset configuration is missing') from error
    if (config.get('schema_version') != 1 or config.get('bundle_id') != 'fixed-course-v1'
            or config.get('install_dir') != 'data/fixed-course-v1'
            or config.get('formal_root') != 'formal' or config.get('demo_root') != 'demo'
            or not _valid_sha(config.get('archive_sha256')) or not _valid_sha(config.get('manifest_sha256'))
            or type(config.get('archive_bytes')) is not int or config['archive_bytes'] <= 0):
        raise FixedDatasetError('Invalid fixed dataset configuration')
    parts = config.get('archive_parts')
    if parts is not None:
        if not isinstance(parts, list) or not parts:
            raise FixedDatasetError('Dataset archive_parts must be a nonempty list')
        for part in parts:
            if (not isinstance(part, dict) or not _https_url(part.get('archive_url'))
                    or not _valid_sha(part.get('sha256')) or type(part.get('bytes')) is not int or part['bytes'] <= 0):
                raise FixedDatasetError('Invalid dataset archive part URL, bytes or SHA256')
        if sum(part['bytes'] for part in parts) != config['archive_bytes']:
            raise FixedDatasetError('Dataset part sizes must sum to archive_bytes')
    if parts is None and not _https_url(config.get('archive_url')):
        raise FixedDatasetError('Dataset release URL must use HTTPS')
    return config


def _bundle(content, config):
    if len(content) > MAX_MANIFEST_BYTES or hashlib.sha256(content).hexdigest() != config['manifest_sha256']:
        raise FixedDatasetError('Dataset manifest SHA256 mismatch')
    bundle = _json(content)
    if (bundle.get('schema_version') != 1 or bundle.get('bundle_id') != config['bundle_id']
            or bundle.get('formal_root') != 'formal' or bundle.get('demo_root') != 'demo'
            or not isinstance(bundle.get('files'), list) or not bundle['files']):
        raise FixedDatasetError('Invalid dataset bundle manifest')
    seen = set()
    for item in bundle['files']:
        if not isinstance(item, dict):
            raise FixedDatasetError('Invalid dataset inventory entry')
        path = str(_relative(item.get('path')))
        if (path in seen or path == 'bundle.json' or path.split('/')[0] not in ('formal', 'demo', 'LICENSES')
                or not _valid_sha(item.get('sha256')) or type(item.get('size')) is not int or item['size'] < 0):
            raise FixedDatasetError('Invalid or duplicate dataset inventory entry')
        seen.add(path)
    if not {'formal/resources.json', 'demo/resources.json'} <= seen:
        raise FixedDatasetError('Fixed resource manifests missing')
    return bundle


def _no_links(root, path):
    relative = path.relative_to(root)
    current = root
    if current.is_symlink():
        raise FixedDatasetError('Dataset symlinks are not allowed')
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise FixedDatasetError('Dataset symlinks are not allowed')


def _verify_tree(target, bundle):
    expected = {item['path']: item for item in bundle['files']}
    for name, item in expected.items():
        path = target / name
        _no_links(target, path)
        if not path.is_file() or path.stat().st_size != item['size'] or _sha(path) != item['sha256']:
            raise FixedDatasetError('Dataset asset missing or SHA256 mismatch: ' + name)
    for role in ('formal', 'demo'):
        manifest = _json((target / role / 'resources.json').read_bytes())
        if (manifest.get('schema_version') != 1 or not isinstance(manifest.get('resources'), list)
                or manifest.get('purpose') != ('formal' if role == 'formal' else 'demo')):
            raise FixedDatasetError('Invalid fixed resource manifest')
        if role == 'formal' and (manifest.get('execution_scope') != 'main_only' or manifest.get('review_policy') != 'automatic_course'):
            raise FixedDatasetError('Formal release must use the frozen automatic course main set')
        bindings = set()
        for entry in manifest['resources']:
            relative = str(_relative(entry.get('path')))
            name = role + '/' + relative
            item = expected.get(name)
            if name in bindings or not item or item['sha256'] != entry.get('sha256'):
                raise FixedDatasetError('Resource binding differs from fixed inventory')
            bindings.add(name)
        inventory = {name for name in expected if name.startswith(role + '/') and name != role + '/resources.json'}
        if inventory != bindings:
            raise FixedDatasetError('Resource inventory does not match its manifest')


def _result(project_root, config, **status):
    base = config['install_dir']
    result = dict(bundle_id=config['bundle_id'], formal_root=str(project_root / base / config['formal_root']),
                demo_root=str(project_root / base / config['demo_root']),
                formal_relative_root=base + '/' + config['formal_root'], demo_relative_root=base + '/' + config['demo_root'],
                installed=False, ready=False)
    result.update(status)
    return result


def inspect_fixed_dataset(project_root) -> dict:
    """Read-only full integrity check. Missing/corrupt data returns ready=False."""
    root = Path(project_root).resolve()
    try:
        config = _config(root)
    except FixedDatasetError as error:
        return dict(bundle_id='fixed-course-v1', formal_root=str(root / 'data/fixed-course-v1/formal'),
                    demo_root=str(root / 'data/fixed-course-v1/demo'), installed=False, ready=False,
                    state='configuration_error', error=str(error))
    target = root / config['install_dir']
    try:
        _no_links(root, target)
        if not target.exists():
            return _result(root, config, state='missing')
        if not target.is_dir():
            raise FixedDatasetError('Dataset target is not a directory')
        _no_links(target, target / 'bundle.json')
        content = (target / 'bundle.json').read_bytes()
        bundle = _bundle(content, config)
        _verify_tree(target, bundle)
    except (FixedDatasetError, OSError) as error:
        return _result(root, config, state='conflict', error=str(error))
    return _result(root, config, state='ready', ready=True)


def _notify(progress, stage, **details):
    if progress:
        progress(dict(stage=stage, **details))


def _download(config, destination, progress):
    request = Request(config['archive_url'], headers={'User-Agent': 'finance-RAG-fixed-dataset/1'})
    downloaded = 0
    _notify(progress, 'download', downloaded_bytes=0, total_bytes=config['archive_bytes'])
    try:
        with urlopen(request, timeout=60) as response, destination.open('xb') as output:
            final_url = getattr(response, 'url', config['archive_url'])
            if urlparse(final_url).scheme != 'https':
                raise FixedDatasetError('Dataset download redirected to an insecure URL')
            while chunk := response.read(CHUNK_BYTES):
                downloaded += len(chunk)
                if downloaded > config['archive_bytes']:
                    raise FixedDatasetError('Downloaded dataset exceeds expected archive size')
                output.write(chunk)
                _notify(progress, 'download', downloaded_bytes=downloaded, total_bytes=config['archive_bytes'])
    except (OSError, URLError) as error:
        raise FixedDatasetError('Fixed dataset download failed; retry or use --archive with the release ZIP') from error


def _download_parts(config, destination, progress):
    """Verify concurrent chunks independently and assemble in manifest order."""
    parts = config['archive_parts']
    lock = threading.Lock()
    cancelled = threading.Event()
    downloaded = 0
    high_water = [0] * len(parts)
    _notify(progress, 'download', downloaded_bytes=0, total_bytes=config['archive_bytes'], total_parts=len(parts))

    def download_one(index, part):
        nonlocal downloaded
        if cancelled.is_set():
            raise FixedDatasetError('Dataset part download cancelled')
        path = destination.parent / f'archive-part-{index:06d}'
        request = Request(part['archive_url'], headers={'User-Agent': 'finance-RAG-fixed-dataset/1'})
        for attempt in range(MAX_PART_ATTEMPTS):
            if cancelled.is_set():
                raise FixedDatasetError('Dataset part download cancelled')
            size = 0
            digest = hashlib.sha256()
            try:
                with urlopen(request, timeout=60) as response, path.open('xb') as output:
                    if not _https_url(getattr(response, 'url', part['archive_url'])):
                        raise FixedDatasetError('Dataset part redirected to an insecure URL')
                    while chunk := response.read(64 * 1024):
                        if cancelled.is_set():
                            raise FixedDatasetError('Dataset part download cancelled')
                        size += len(chunk)
                        if size > part['bytes']:
                            raise FixedDatasetError(f'Dataset part {index} exceeds expected byte count')
                        digest.update(chunk)
                        output.write(chunk)
                        with lock:
                            # Count archive byte positions once, even when a
                            # failed connection retransfers part of a chunk.
                            downloaded += max(0, size - high_water[index])
                            high_water[index] = max(high_water[index], size)
                            _notify(progress, 'download', downloaded_bytes=downloaded,
                                    total_bytes=config['archive_bytes'], total_parts=len(parts))
                if size != part['bytes']:
                    raise FixedDatasetError(f'Dataset part {index} byte count mismatch')
                if digest.hexdigest() != part['sha256']:
                    raise FixedDatasetError(f'Dataset part {index} SHA256 mismatch')
                return path
            except (OSError, URLError, HTTPException, EOFError) as error:
                if isinstance(error, HTTPError):
                    retryable = error.code == 429 or 500 <= error.code <= 599
                    error.close()
                else:
                    retryable = (not isinstance(error, InvalidURL)
                                 and isinstance(error, (URLError, TimeoutError, ConnectionError,
                                                        HTTPException, EOFError, ssl.SSLEOFError)))
                path.unlink(missing_ok=True)
                if not retryable or attempt + 1 == MAX_PART_ATTEMPTS or cancelled.is_set():
                    raise FixedDatasetError(f'Fixed dataset part {index} download failed; retry or use --archive with the release ZIP') from error
                sleep(0.5 * 2 ** attempt)

    paths = {}
    with ThreadPoolExecutor(max_workers=min(MAX_DOWNLOAD_WORKERS, len(parts)), thread_name_prefix='fixed-data-part') as pool:
        futures = {pool.submit(download_one, index, part): index for index, part in enumerate(parts)}
        try:
            for future in as_completed(futures):
                paths[futures[future]] = future.result()
        except BaseException:
            cancelled.set()
            for future in futures:
                future.cancel()
            raise
    with destination.open('xb') as output:
        for index in range(len(parts)):
            with paths[index].open('rb') as source:
                shutil.copyfileobj(source, output, CHUNK_BYTES)
            paths[index].unlink()


def _archive_inventory(archive, config):
    infos = {}
    for info in archive.infolist():
        name = str(_relative(info.filename))
        mode = stat.S_IFMT(info.external_attr >> 16)
        if name in infos or info.is_dir() or mode not in (0, stat.S_IFREG):
            raise FixedDatasetError('ZIP contains duplicate paths, directories or links')
        if info.flag_bits & 1:
            raise FixedDatasetError('Encrypted dataset archives are not supported')
        infos[name] = info
    manifest = infos.get('bundle.json')
    if not manifest or manifest.file_size > MAX_MANIFEST_BYTES:
        raise FixedDatasetError('ZIP bundle manifest missing or too large')
    content = archive.read(manifest)
    bundle = _bundle(content, config)
    objects = {}
    for item in bundle['files']:
        name = 'objects/' + item['sha256']
        if name in objects and objects[name] != item['size']:
            raise FixedDatasetError('Inconsistent content-addressed file size')
        objects[name] = item['size']
    if set(infos) != {'bundle.json'} | set(objects):
        raise FixedDatasetError('ZIP contains files outside the fixed whitelist')
    if any(infos[name].file_size != size for name, size in objects.items()):
        raise FixedDatasetError('ZIP object size differs from fixed inventory')
    return bundle, content


def ensure_fixed_dataset(project_root, *, archive_path=None, progress=None) -> dict:
    """Verify or atomically install the pinned public release (or an offline ZIP).

    A conflicting existing target is preserved and reported. `installed` is true
    only when this call created the complete target. Paths derive from the caller's
    project root; neither the ZIP nor configuration contains an author's path.
    """
    root = Path(project_root).resolve()
    config = _config(root)
    status = inspect_fixed_dataset(root)
    if status['ready']:
        _notify(progress, 'ready', bundle_id=config['bundle_id'], installed=False)
        return status
    if status['state'] != 'missing':
        raise FixedDatasetError('Existing fixed dataset conflict; preserved: ' + status.get('error', 'invalid installation'))
    target = root / config['install_dir']
    _no_links(root, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.fixed-course-v1-', dir=target.parent) as temporary:
        temporary = Path(temporary)
        archive_file = Path(archive_path).expanduser().resolve() if archive_path is not None else temporary / 'download.zip'
        if archive_path is None:
            if config.get('archive_parts'):
                _download_parts(config, archive_file, progress)
            else:
                _download(config, archive_file, progress)
        _notify(progress, 'verify_archive')
        if not archive_file.is_file() or _sha(archive_file) != config['archive_sha256']:
            raise FixedDatasetError('Fixed dataset archive SHA256 mismatch')
        if archive_file.stat().st_size != config['archive_bytes']:
            raise FixedDatasetError('Fixed dataset archive size mismatch')
        staged = temporary / 'installation'
        staged.mkdir()
        try:
            with zipfile.ZipFile(archive_file) as archive:
                bundle, content = _archive_inventory(archive, config)
                (staged / 'bundle.json').write_bytes(content)
                copied = {}
                for index, item in enumerate(bundle['files'], 1):
                    destination = staged / item['path']
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if item['sha256'] in copied:
                        shutil.copyfile(copied[item['sha256']], destination)
                    else:
                        digest = hashlib.sha256()
                        size = 0
                        with archive.open('objects/' + item['sha256']) as source, destination.open('xb') as output:
                            while chunk := source.read(CHUNK_BYTES):
                                size += len(chunk)
                                if size > item['size']:
                                    raise FixedDatasetError('ZIP object exceeds fixed inventory size')
                                digest.update(chunk)
                                output.write(chunk)
                        if size != item['size'] or digest.hexdigest() != item['sha256']:
                            raise FixedDatasetError('Dataset object SHA256 mismatch: ' + item['path'])
                        copied[item['sha256']] = destination
                    _notify(progress, 'install', completed_files=index, total_files=len(bundle['files']))
            _verify_tree(staged, bundle)
            _no_links(root, target)
            if target.exists():
                concurrent = inspect_fixed_dataset(root)
                if concurrent['ready']:
                    return concurrent
                raise FixedDatasetError('Existing fixed dataset conflict; preserved')
            os.rename(staged, target)
        except (zipfile.BadZipFile, RuntimeError, KeyError, OSError) as error:
            raise FixedDatasetError('Fixed dataset ZIP could not be safely installed: ' + str(error)) from error
    status = inspect_fixed_dataset(root)
    if not status['ready']:
        raise FixedDatasetError('Installed fixed dataset failed final integrity verification')
    status['installed'] = True
    _notify(progress, 'ready', bundle_id=config['bundle_id'], installed=True)
    return status
