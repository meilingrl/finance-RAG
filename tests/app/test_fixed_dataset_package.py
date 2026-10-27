"""Packaging must use registered byte bindings and deduplicate without reshaping QA."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from tests.app.test_datasets import encoded, fixture


class FixedDatasetPackageTests(unittest.TestCase):
    def prepare(self, root):
        _, _, payloads = fixture(root)
        registry = root / 'artifacts/formal-v1/resources'
        registry.mkdir(parents=True)
        for role, rid in [('formal', 'formal-main-course'), ('demo', 'development-reviewed-v1')]:
            base = root / 'source' / role
            for name, body in payloads.items():
                if name.startswith(role + '/'):
                    path = base / name[len(role) + 1:]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(body)
            manifest = json.loads((base / 'resources.json').read_bytes())
            (registry / (rid + '.json')).write_bytes(encoded({'root': str(base), 'manifest': manifest}))
        documents = root / 'docs/delivery/fixed-dataset'
        documents.mkdir(parents=True)
        for name in ('ATTRIBUTION.md', 'APACHE-2.0.txt', 'CC-BY-4.0.txt'):
            (documents / name).write_text('test license ' + name)
        return payloads

    def test_packaging_uses_registered_main_set_and_one_object_per_sha(self):
        from scripts.package_fixed_dataset import package_fixed_dataset
        from app.datasets import ensure_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            payloads = self.prepare(root)
            (root / 'source/formal/credentials.key').write_text('must stay local')
            (root / 'source/formal/resources.json').write_text('{"unqualified":"candidate"}')
            result = package_fixed_dataset(root, root / 'artifacts/release')
            archive = Path(result['archive_path'])
            with zipfile.ZipFile(archive) as source:
                bundle = json.loads(source.read('bundle.json'))
                members = source.namelist()
                model_sha = hashlib.sha256(b'fixed-model-bytes').hexdigest()
                self.assertEqual(members.count('objects/' + model_sha), 1)
                self.assertNotIn('credentials.key', json.dumps(bundle))
                self.assertEqual(len(members), len({x['sha256'] for x in bundle['files']}) + 1)
            (root / 'configs/fixed_dataset.json').write_bytes(encoded(result['config']))
            installed = ensure_fixed_dataset(root, archive_path=archive)
            self.assertTrue(installed['ready'])
            target = root / 'data/fixed-course-v1'
            for name in ('formal/inputs/inference_questions.jsonl', 'formal/main-only/evaluation/gold.jsonl', 'formal/models/encoder.bin', 'demo/inference_questions.jsonl'):
                self.assertEqual((target / name).read_bytes(), payloads[name])
            self.assertEqual(json.loads((target / 'formal/resources.json').read_text())['resource_set_id'], 'formal-main-course')

    def test_changed_registered_source_prevents_release(self):
        from scripts.package_fixed_dataset import package_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.prepare(root)
            (root / 'source/formal/inputs/inference_questions.jsonl').write_text('rewritten')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                package_fixed_dataset(root, root / 'artifacts/release')
            self.assertFalse((root / 'artifacts/release').exists())

    def test_existing_release_is_never_rebuilt_in_place(self):
        from scripts.package_fixed_dataset import package_fixed_dataset
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.prepare(root)
            output = root / 'artifacts/release'
            output.mkdir()
            (output / 'keep').write_text('previous release')
            with self.assertRaises(ValueError):
                package_fixed_dataset(root, output)
            self.assertEqual((output / 'keep').read_text(), 'previous release')

    def test_optional_parts_reassemble_exactly_the_unchanged_zip(self):
        from scripts.package_fixed_dataset import package_fixed_dataset
        from app.datasets import ensure_fixed_dataset
        from unittest.mock import patch
        import io
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.prepare(root)
            whole = package_fixed_dataset(root, root / 'artifacts/whole')
            split = package_fixed_dataset(root, root / 'artifacts/split', part_bytes=256)
            self.assertNotIn('archive_url', split['config'])
            self.assertEqual(split['config']['archive_sha256'], whole['config']['archive_sha256'])
            paths = [Path(path) for path in split['part_paths']]
            self.assertGreater(len(paths), 1)
            self.assertEqual(b''.join(path.read_bytes() for path in paths), Path(whole['archive_path']).read_bytes())
            self.assertTrue(all(path.stat().st_size <= 256 for path in paths))
            (root / 'configs/fixed_dataset.json').write_bytes(encoded(split['config']))
            content = {part['archive_url']: path.read_bytes() for part, path in zip(split['config']['archive_parts'], paths)}
            with patch('app.datasets.urlopen', side_effect=lambda request, timeout: io.BytesIO(content[request.full_url])):
                installed = ensure_fixed_dataset(root)
            self.assertTrue(installed['ready'])

    def test_invalid_part_size_does_not_create_release(self):
        from scripts.package_fixed_dataset import package_fixed_dataset
        for size in (0, -1, True):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                self.prepare(root)
                output = root / 'artifacts/split'
                with self.assertRaises(ValueError):
                    package_fixed_dataset(root, output, part_bytes=size)
                self.assertFalse(output.exists())
