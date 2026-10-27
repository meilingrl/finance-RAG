"""Fixed data installation keeps the frozen bytes and rejects untrusted archives."""
import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def fixture(root, *, extra_members=(), changed_object=False):
    """Use hand-authored frozen questions, gold and bindings, without production helpers."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    payloads = {
        'formal/inputs/inference_questions.jsonl': b'{"question_id":"q-fixed","question":"Revenue?","split":"main"}\n',
        'formal/main-only/evaluation/gold.jsonl': b'{"question_id":"q-fixed","answer":"42"}\n',
        'formal/models/encoder.bin': b'fixed-model-bytes',
        'formal/main-only/qualification.json': b'{"question_count":1}\n',
        'demo/inference_questions.jsonl': b'{"question_id":"dev-fixed","split":"development"}\n',
        'demo/models/encoder.bin': b'fixed-model-bytes',
    }
    for role, rid in [('formal', 'formal-main-course'), ('demo', 'development-reviewed-v1')]:
        items = []
        for name, body in list(payloads.items()):
            if not name.startswith(role + '/'):
                continue
            relative = name[len(role) + 1:]
            kind = 'model' if relative.startswith('models/') else 'evaluation' if '/evaluation/' in relative else 'review' if relative.endswith('qualification.json') else 'input'
            items.append({'kind': kind, 'path': relative, 'sha256': hashlib.sha256(body).hexdigest()})
        manifest = {'schema_version': 1, 'resource_set_id': rid, 'purpose': 'formal' if role == 'formal' else 'demo', 'resources': items, 'qualification': {}}
        if role == 'formal':
            manifest.update(execution_scope='main_only', review_policy='automatic_course')
        payloads[role + '/resources.json'] = encoded(manifest)
    payloads['LICENSES/ATTRIBUTION.md'] = b'TAT-DQA CC BY 4.0; MiniLM Apache-2.0\n'
    files = [{'path': p, 'sha256': hashlib.sha256(b).hexdigest(), 'size': len(b)} for p, b in sorted(payloads.items())]
    bundle = {'schema_version': 1, 'bundle_id': 'fixed-course-v1', 'formal_root': 'formal', 'demo_root': 'demo', 'files': files}
    bundle_bytes = encoded(bundle)
    archive = root / 'offline.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr('bundle.json', bundle_bytes)
        seen = set()
        for name, body in payloads.items():
            sha = hashlib.sha256(body).hexdigest()
            if sha in seen:
                continue
            seen.add(sha)
            output.writestr('objects/' + sha, b'changed' if changed_object and name.endswith('gold.jsonl') else body)
        for name, body in extra_members:
            output.writestr(name, body)
    config = {'schema_version': 1, 'bundle_id': 'fixed-course-v1', 'archive_url': 'https://example.invalid/fixed.zip', 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'archive_bytes': archive.stat().st_size, 'manifest_sha256': hashlib.sha256(bundle_bytes).hexdigest(), 'install_dir': 'data/fixed-course-v1', 'formal_root': 'formal', 'demo_root': 'demo'}
    (root / 'configs').mkdir(exist_ok=True)
    (root / 'configs/fixed_dataset.json').write_bytes(encoded(config))
    return archive, config, payloads


class FixedDatasetTests(unittest.TestCase):
    def test_offline_install_preserves_question_gold_model_and_is_idempotent(self):
        from app.datasets import ensure_fixed_dataset, inspect_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive, _, payloads = fixture(root)
            before = inspect_fixed_dataset(root)
            self.assertFalse(before['ready'])
            self.assertFalse((root / 'data').exists(), 'read-only inspection must not create folders')
            first = ensure_fixed_dataset(root, archive_path=archive)
            self.assertTrue(first['ready'])
            self.assertTrue(first['installed'])
            self.assertEqual(first['formal_root'], str(root.resolve() / 'data/fixed-course-v1/formal'))
            for name, body in payloads.items():
                self.assertEqual((root / 'data/fixed-course-v1' / name).read_bytes(), body)
            archive.unlink()
            second = ensure_fixed_dataset(root)
            self.assertTrue(second['ready'])
            self.assertFalse(second['installed'])

    def test_same_bundle_installs_under_another_machine_root(self):
        from app.datasets import ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            archive, config, _ = fixture(one)
            destination = Path(two)
            (destination / 'configs').mkdir()
            (destination / 'configs/fixed_dataset.json').write_bytes(encoded(config))
            result = ensure_fixed_dataset(destination, archive_path=archive)
            self.assertEqual(result['demo_root'], str(destination.resolve() / 'data/fixed-course-v1/demo'))
            self.assertNotIn(one, (destination / 'data/fixed-course-v1/bundle.json').read_text())

    def test_download_stream_is_verified_before_installation(self):
        from app.datasets import ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            archive, config, _ = fixture(td)
            response = io.BytesIO(archive.read_bytes())
            response.headers = {'Content-Length': str(config['archive_bytes'])}
            progress = []
            with patch('app.datasets.urlopen', return_value=response):
                result = ensure_fixed_dataset(td, progress=progress.append)
            self.assertTrue(result['ready'])
            self.assertTrue(any(row['stage'] == 'download' and row['downloaded_bytes'] == config['archive_bytes'] for row in progress))
            self.assertEqual(progress[-1]['stage'], 'ready')

    def test_wrong_archive_hash_leaves_no_install(self):
        from app.datasets import FixedDatasetError, ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            archive, _, _ = fixture(td)
            archive.write_bytes(archive.read_bytes() + b'corruption')
            with self.assertRaisesRegex(FixedDatasetError, 'SHA256'):
                ensure_fixed_dataset(td, archive_path=archive)
            self.assertFalse((Path(td) / 'data/fixed-course-v1').exists())

    def test_changed_resource_with_valid_archive_hash_is_rejected(self):
        from app.datasets import FixedDatasetError, ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            archive, _, _ = fixture(td, changed_object=True)
            with self.assertRaises(FixedDatasetError):
                ensure_fixed_dataset(td, archive_path=archive)
            self.assertFalse((Path(td) / 'data/fixed-course-v1').exists())

    def test_archive_rejects_escape_absolute_windows_paths_and_symlinks(self):
        from app.datasets import FixedDatasetError, ensure_fixed_dataset
        symlink = zipfile.ZipInfo('symlink')
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        for member in ('../escaped', '/absolute', 'C:/windows', 'x\\..\\escaped', symlink):
            with self.subTest(member=member), tempfile.TemporaryDirectory() as td:
                archive, _, _ = fixture(td, extra_members=[(member, b'outside')])
                with self.assertRaises(FixedDatasetError):
                    ensure_fixed_dataset(td, archive_path=archive)
                self.assertFalse((Path(td) / 'data/fixed-course-v1').exists())

    def test_existing_conflict_is_reported_and_never_overwritten(self):
        from app.datasets import FixedDatasetError, ensure_fixed_dataset, inspect_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive, _, _ = fixture(root)
            target = root / 'data/fixed-course-v1'
            target.mkdir(parents=True)
            (target / 'keep.txt').write_text('user work')
            with self.assertRaisesRegex(FixedDatasetError, 'conflict'):
                ensure_fixed_dataset(root, archive_path=archive)
            self.assertEqual((target / 'keep.txt').read_text(), 'user work')
            self.assertFalse(inspect_fixed_dataset(root)['ready'])

    def test_inspection_detects_modified_assets_and_cannot_repair_by_overwriting(self):
        from app.datasets import FixedDatasetError, ensure_fixed_dataset, inspect_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            archive, _, _ = fixture(td)
            ensure_fixed_dataset(td, archive_path=archive)
            question = Path(td) / 'data/fixed-course-v1/formal/inputs/inference_questions.jsonl'
            question.write_text('changed')
            self.assertFalse(inspect_fixed_dataset(td)['ready'])
            with self.assertRaises(FixedDatasetError):
                ensure_fixed_dataset(td, archive_path=archive)
            self.assertEqual(question.read_text(), 'changed')

    def test_symlinked_data_directory_is_not_followed(self):
        from app.datasets import FixedDatasetError, ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as external:
            archive, _, _ = fixture(td)
            (Path(td) / 'data').symlink_to(external, target_is_directory=True)
            with self.assertRaises(FixedDatasetError):
                ensure_fixed_dataset(td, archive_path=archive)
            self.assertEqual(list(Path(external).iterdir()), [])

class FixedDatasetFailureTests(unittest.TestCase):
    def test_incomplete_download_is_removed_and_retry_can_install(self):
        from app.datasets import FixedDatasetError, ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            archive, _, _ = fixture(td)
            response = io.BytesIO(archive.read_bytes()[:50])
            with patch('app.datasets.urlopen', return_value=response), self.assertRaises(FixedDatasetError):
                ensure_fixed_dataset(td)
            data = Path(td) / 'data'
            self.assertEqual(list(data.iterdir()), [])
            self.assertTrue(ensure_fixed_dataset(td, archive_path=archive)['ready'])

    def test_network_failure_has_actionable_error_and_preserves_existing_data(self):
        from urllib.error import URLError
        from app.datasets import FixedDatasetError, ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            fixture(td)
            original = Path(td) / 'data/original'
            original.mkdir(parents=True)
            (original / 'keep').write_text('frozen original')
            with patch('app.datasets.urlopen', side_effect=URLError('unavailable')), self.assertRaisesRegex(FixedDatasetError, 'retry or use --archive'):
                ensure_fixed_dataset(td)
            self.assertEqual((original / 'keep').read_text(), 'frozen original')
            self.assertEqual(list((Path(td) / 'data').iterdir()), [original])

    def test_replaced_installed_manifest_symlink_is_invalid(self):
        from app.datasets import ensure_fixed_dataset, inspect_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            archive, _, _ = fixture(td)
            ensure_fixed_dataset(td, archive_path=archive)
            manifest = Path(td) / 'data/fixed-course-v1/bundle.json'
            original = Path(td) / 'external-manifest.json'
            original.write_bytes(manifest.read_bytes())
            manifest.unlink()
            manifest.symlink_to(original)
            self.assertFalse(inspect_fixed_dataset(td)['ready'])

    def test_metadata_sha_cannot_be_changed_with_only_new_archive_sha(self):
        from app.datasets import FixedDatasetError, ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            archive, config, _ = fixture(td)
            with zipfile.ZipFile(archive) as source:
                entries = [(x, source.read(x)) for x in source.namelist()]
            with zipfile.ZipFile(archive, 'w') as output:
                for name, body in entries:
                    output.writestr(name, body + b' ' if name == 'bundle.json' else body)
            config.update(archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), archive_bytes=archive.stat().st_size)
            (Path(td) / 'configs/fixed_dataset.json').write_bytes(encoded(config))
            with self.assertRaisesRegex(FixedDatasetError, 'manifest SHA256'):
                ensure_fixed_dataset(td, archive_path=archive)

    def test_expected_object_cannot_be_a_zip_symlink(self):
        from app.datasets import FixedDatasetError, ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            archive, config, _ = fixture(td)
            with zipfile.ZipFile(archive) as source:
                entries = [(x, source.read(x)) for x in source.namelist()]
            with zipfile.ZipFile(archive, 'w') as output:
                for index, (name, body) in enumerate(entries):
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = ((stat.S_IFLNK if index == 1 else stat.S_IFREG) | 0o644) << 16
                    output.writestr(info, body)
            config.update(archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), archive_bytes=archive.stat().st_size)
            (Path(td) / 'configs/fixed_dataset.json').write_bytes(encoded(config))
            with self.assertRaisesRegex(FixedDatasetError, 'links'):
                ensure_fixed_dataset(td, archive_path=archive)

    def test_cli_offline_install_and_read_only_check(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as td:
            archive, _, _ = fixture(td)
            script = Path(__file__).resolve().parents[2] / 'scripts/setup_data.py'
            before = subprocess.run([sys.executable, str(script), '--project-root', td, '--check'], capture_output=True, text=True)
            self.assertEqual(before.returncode, 1)
            self.assertFalse((Path(td) / 'data').exists())
            install = subprocess.run([sys.executable, str(script), '--project-root', td, '--archive', str(archive)], capture_output=True, text=True)
            self.assertEqual(install.returncode, 0, install.stderr)
            self.assertTrue(json.loads(install.stdout)['ready'])
            archive.unlink()
            after = subprocess.run([sys.executable, str(script), '--project-root', td, '--check'], capture_output=True, text=True)
            self.assertEqual(after.returncode, 0, after.stderr)


def multipart_fixture(root, part_bytes=256):
    archive, config, payloads = fixture(root)
    content = archive.read_bytes()
    chunks = [content[i:i + part_bytes] for i in range(0, len(content), part_bytes)]
    config['archive_parts'] = [
        {'archive_url': f'https://example.invalid/fixed.zip.part{index:04d}', 'bytes': len(body),
         'sha256': hashlib.sha256(body).hexdigest()}
        for index, body in enumerate(chunks)
    ]
    (Path(root) / 'configs/fixed_dataset.json').write_bytes(encoded(config))
    return archive, config, payloads, chunks


class FixedDatasetMultipartTests(unittest.TestCase):
    def test_parts_complete_out_of_order_but_assemble_the_same_frozen_zip(self):
        import threading
        from app.datasets import ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            archive, config, payloads, chunks = multipart_fixture(td, part_bytes=2000)
            later_finished = threading.Event()
            class PartResponse(io.BytesIO):
                def __init__(self, index):
                    super().__init__(chunks[index]); self.index = index
                    self.url = config['archive_parts'][index]['archive_url']
                def read(self, size=-1):
                    if self.index == 0:
                        if not later_finished.wait(2):
                            raise OSError('Parts were downloaded sequentially')
                    result = super().read(size)
                    if self.index == 1 and not result:
                        later_finished.set()
                    return result
            def open_part(request, timeout):
                index = int(request.full_url.rsplit('part', 1)[1])
                return PartResponse(index)
            archive.unlink()
            progress = []
            with patch('app.datasets.urlopen', side_effect=open_part):
                status = ensure_fixed_dataset(td, progress=progress.append)
            self.assertTrue(status['ready'])
            for name in ('formal/inputs/inference_questions.jsonl', 'formal/main-only/evaluation/gold.jsonl'):
                self.assertEqual((Path(td) / 'data/fixed-course-v1' / name).read_bytes(), payloads[name])
            downloads = [item for item in progress if item['stage'] == 'download']
            self.assertEqual(downloads[-1]['downloaded_bytes'], config['archive_bytes'])
            self.assertEqual(sorted(item['downloaded_bytes'] for item in downloads), [item['downloaded_bytes'] for item in downloads])

    def test_downloader_limits_parallel_part_requests_to_eight(self):
        import threading
        from app.datasets import ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            _, config, _, chunks = multipart_fixture(td, part_bytes=128)
            lock = threading.Lock()
            release = threading.Event()
            activity = {'active': 0, 'peak': 0}
            class PartResponse(io.BytesIO):
                def __init__(self, index):
                    super().__init__(chunks[index])
                    self.url = config['archive_parts'][index]['archive_url']
                def read(self, size=-1):
                    if not release.wait(2):
                        raise OSError('Expected eight parallel requests')
                    return super().read(size)
                def __exit__(self, *args):
                    with lock:
                        activity['active'] -= 1
                    return super().__exit__(*args)
            def open_part(request, timeout):
                index = int(request.full_url.rsplit('part', 1)[1])
                with lock:
                    activity['active'] += 1
                    activity['peak'] = max(activity['peak'], activity['active'])
                    if activity['active'] == 8:
                        release.set()
                return PartResponse(index)
            with patch('app.datasets.urlopen', side_effect=open_part):
                self.assertTrue(ensure_fixed_dataset(td)['ready'])
            self.assertEqual(activity['peak'], 8)
            self.assertEqual(activity['active'], 0)

    def test_corrupt_part_fails_its_sha_and_cleans_temporary_files(self):
        from app.datasets import FixedDatasetError, ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            _, config, _, chunks = multipart_fixture(td)
            original = Path(td) / 'data/original.txt'
            original.parent.mkdir()
            original.write_text('keep original dataset')
            def open_part(request, timeout):
                index = int(request.full_url.rsplit('part', 1)[1])
                body = chunks[index]
                return io.BytesIO((b'X' + body[1:]) if index == 1 else body)
            with patch('app.datasets.urlopen', side_effect=open_part), self.assertRaisesRegex(FixedDatasetError, 'part.*SHA256'):
                ensure_fixed_dataset(td)
            self.assertEqual(original.read_text(), 'keep original dataset')
            self.assertEqual(list(original.parent.iterdir()), [original])

    def test_each_part_must_match_its_declared_byte_count(self):
        from app.datasets import FixedDatasetError, ensure_fixed_dataset
        for extra in (b'', b'extra'):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as td:
                _, _, _, chunks = multipart_fixture(td)
                def open_part(request, timeout):
                    index = int(request.full_url.rsplit('part', 1)[1])
                    return io.BytesIO((chunks[index][:-1] if not extra else chunks[index] + extra) if index == 0 else chunks[index])
                with patch('app.datasets.urlopen', side_effect=open_part), self.assertRaises(FixedDatasetError):
                    ensure_fixed_dataset(td)
                self.assertEqual(list((Path(td) / 'data').iterdir()), [])

    def test_part_configuration_requires_https_sha_and_exact_total_size(self):
        from app.datasets import FixedDatasetError, ensure_fixed_dataset
        mutations = [('archive_url', 'http://example.invalid/part'), ('sha256', 'bad'), ('bytes', 0), ('bytes', 1)]
        for key, value in mutations:
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as td:
                archive, config, _, _ = multipart_fixture(td)
                config['archive_parts'][0][key] = value
                (Path(td) / 'configs/fixed_dataset.json').write_bytes(encoded(config))
                with self.assertRaises(FixedDatasetError):
                    ensure_fixed_dataset(td, archive_path=archive)
                self.assertFalse((Path(td) / 'data').exists())

    def test_offline_zip_works_with_part_config_without_network(self):
        from app.datasets import ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            archive, _, _, _ = multipart_fixture(td)
            with patch('app.datasets.urlopen', side_effect=AssertionError('Offline install must not download parts')):
                self.assertTrue(ensure_fixed_dataset(td, archive_path=archive)['ready'])

    def test_parts_only_config_does_not_require_or_use_whole_zip_url(self):
        from app.datasets import ensure_fixed_dataset
        for unused_url in (None, 'unused-not-a-url'):
            with self.subTest(unused_url=unused_url), tempfile.TemporaryDirectory() as td:
                _, config, _, chunks = multipart_fixture(td)
                if unused_url is None:
                    config.pop('archive_url')
                else:
                    config['archive_url'] = unused_url
                (Path(td) / 'configs/fixed_dataset.json').write_bytes(encoded(config))
                def open_part(request, timeout):
                    return io.BytesIO(chunks[int(request.full_url.rsplit('part', 1)[1])])
                with patch('app.datasets.urlopen', side_effect=open_part):
                    self.assertTrue(ensure_fixed_dataset(td)['ready'])

    def test_network_part_failure_cleans_all_parts_and_preserves_original_data(self):
        from urllib.error import URLError
        from app.datasets import FixedDatasetError, ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            _, _, _, chunks = multipart_fixture(td)
            data = Path(td) / 'data'
            data.mkdir()
            (data / 'keep').write_text('original private data')
            def open_part(request, timeout):
                index = int(request.full_url.rsplit('part', 1)[1])
                if index == 2:
                    raise URLError('sensitive detail must not be displayed')
                return io.BytesIO(chunks[index])
            with patch('app.datasets.urlopen', side_effect=open_part), patch('app.datasets.sleep', return_value=None), self.assertRaises(FixedDatasetError) as caught:
                ensure_fixed_dataset(td)
            self.assertNotIn('sensitive detail', str(caught.exception))
            self.assertEqual([p.name for p in data.iterdir()], ['keep'])
            self.assertEqual((data / 'keep').read_text(), 'original private data')

    def test_valid_parts_still_require_the_pinned_overall_archive_sha(self):
        from app.datasets import FixedDatasetError, ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            _, config, _, chunks = multipart_fixture(td)
            config['archive_sha256'] = '0' * 64
            (Path(td) / 'configs/fixed_dataset.json').write_bytes(encoded(config))
            def open_part(request, timeout):
                return io.BytesIO(chunks[int(request.full_url.rsplit('part', 1)[1])])
            with patch('app.datasets.urlopen', side_effect=open_part), self.assertRaisesRegex(FixedDatasetError, 'archive SHA256'):
                ensure_fixed_dataset(td)
            self.assertEqual(list((Path(td) / 'data').iterdir()), [])

class FixedDatasetPartRetryTests(unittest.TestCase):
    def test_transient_network_errors_retry_the_same_part_up_to_success(self):
        from urllib.error import URLError
        from http.client import RemoteDisconnected
        from app.datasets import ensure_fixed_dataset
        failures = [URLError('temporary'), TimeoutError(), ConnectionResetError(), RemoteDisconnected()]
        with tempfile.TemporaryDirectory() as td:
            _, _, _, chunks = multipart_fixture(td)
            attempts = 0
            delays = []
            def open_part(request, timeout):
                nonlocal attempts
                index = int(request.full_url.rsplit('part', 1)[1])
                if index == 0:
                    attempts += 1
                    if attempts <= len(failures):
                        raise failures[attempts - 1]
                return io.BytesIO(chunks[index])
            with patch('app.datasets.urlopen', side_effect=open_part), patch('app.datasets.sleep', side_effect=delays.append, create=True):
                self.assertTrue(ensure_fixed_dataset(td)['ready'])
            self.assertEqual(attempts, 5)
            self.assertEqual(delays, [0.5, 1.0, 2.0, 4.0])

    def test_http_429_and_5xx_are_retried(self):
        from urllib.error import HTTPError
        from app.datasets import ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            _, _, _, chunks = multipart_fixture(td)
            statuses = [429, 500, 503, 504]
            attempts = 0
            def open_part(request, timeout):
                nonlocal attempts
                index = int(request.full_url.rsplit('part', 1)[1])
                if index == 0:
                    attempts += 1
                    if attempts <= len(statuses):
                        raise HTTPError(request.full_url, statuses[attempts - 1], 'temporary', {}, None)
                return io.BytesIO(chunks[index])
            with patch('app.datasets.urlopen', side_effect=open_part), patch('app.datasets.sleep', return_value=None, create=True):
                self.assertTrue(ensure_fixed_dataset(td)['ready'])
            self.assertEqual(attempts, 5)

    def test_partial_connection_failure_restarts_cleanly_without_double_counting_progress(self):
        from http.client import IncompleteRead
        from app.datasets import ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            _, config, _, chunks = multipart_fixture(td)
            attempts = 0
            progress = []
            class PartialResponse(io.BytesIO):
                def read(self, size=-1):
                    if self.tell():
                        raise IncompleteRead(b'', len(chunks[0]) - 80)
                    return super().read(80)
            def open_part(request, timeout):
                nonlocal attempts
                index = int(request.full_url.rsplit('part', 1)[1])
                if index == 0:
                    attempts += 1
                    if attempts == 1:
                        return PartialResponse(chunks[0])
                return io.BytesIO(chunks[index])
            with patch('app.datasets.urlopen', side_effect=open_part), patch('app.datasets.sleep', return_value=None, create=True):
                self.assertTrue(ensure_fixed_dataset(td, progress=progress.append)['ready'])
            self.assertEqual(attempts, 2)
            counts = [event['downloaded_bytes'] for event in progress if event['stage'] == 'download']
            self.assertEqual(counts[-1], config['archive_bytes'])
            self.assertLessEqual(max(counts), config['archive_bytes'])
            self.assertEqual(sorted(counts), counts)

    def test_exhausted_retries_clean_parts_and_keep_existing_assets(self):
        from urllib.error import URLError
        from app.datasets import FixedDatasetError, ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            _, _, _, chunks = multipart_fixture(td)
            data = Path(td) / 'data'
            data.mkdir()
            (data / 'keep').write_text('private original')
            attempts = 0
            delays = []
            def open_part(request, timeout):
                nonlocal attempts
                index = int(request.full_url.rsplit('part', 1)[1])
                if index == 0:
                    attempts += 1
                    raise URLError('do not expose credentials in upstream error')
                return io.BytesIO(chunks[index])
            with patch('app.datasets.urlopen', side_effect=open_part), patch('app.datasets.sleep', side_effect=delays.append, create=True), self.assertRaises(FixedDatasetError) as caught:
                ensure_fixed_dataset(td)
            self.assertEqual(attempts, 5)
            self.assertEqual(delays, [0.5, 1.0, 2.0, 4.0])
            self.assertNotIn('credentials', str(caught.exception))
            self.assertEqual([p.name for p in data.iterdir()], ['keep'])
            self.assertEqual((data / 'keep').read_text(), 'private original')

    def test_nonretryable_http_integrity_and_size_failures_stop_after_one_attempt(self):
        from http.client import InvalidURL
        from urllib.error import HTTPError
        from app.datasets import FixedDatasetError, ensure_fixed_dataset
        for failure in (400, 401, 403, 404, 'sha', 'short', 'long', 'redirect', 'invalid_url', 'file_permission'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as td:
                _, _, _, chunks = multipart_fixture(td)
                attempts = 0
                delays = []
                def open_part(request, timeout):
                    nonlocal attempts
                    index = int(request.full_url.rsplit('part', 1)[1])
                    body = chunks[index]
                    if index == 0:
                        attempts += 1
                        if isinstance(failure, int):
                            raise HTTPError(request.full_url, failure, 'permanent', {}, None)
                        if failure == 'invalid_url':
                            raise InvalidURL('invalid path')
                        if failure == 'file_permission':
                            raise PermissionError('cannot write part file')
                        body = b'X' + body[1:] if failure == 'sha' else body[:-1] if failure == 'short' else body + b'extra' if failure == 'long' else body
                    response = io.BytesIO(body)
                    if index == 0 and failure == 'redirect':
                        response.url = 'http://example.invalid/insecure'
                    return response
                with patch('app.datasets.urlopen', side_effect=open_part), patch('app.datasets.sleep', side_effect=delays.append, create=True), self.assertRaises(FixedDatasetError):
                    ensure_fixed_dataset(td)
                self.assertEqual(attempts, 1)
                self.assertEqual(delays, [])
                self.assertEqual(list((Path(td) / 'data').iterdir()), [])
