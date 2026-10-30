"""Portable regressions, also runnable on native Windows without real API/data."""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class WindowsCompatibilityTests(unittest.TestCase):
    def test_lock_excludes_another_process_then_releases(self):
        from app import fsutil
        code = """
import sys
from app import fsutil
with open(sys.argv[1], 'a+b') as handle:
 try: fsutil.acquire(handle, blocking=False)
 except BlockingIOError: sys.exit(23)
 fsutil.release(handle)
"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'lock'
            with path.open('a+b') as handle:
                fsutil.acquire(handle)
                blocked = subprocess.run([sys.executable, '-c', code, str(path)], cwd=ROOT, timeout=10)
                self.assertEqual(blocked.returncode, 23)
                fsutil.release(handle)
                free = subprocess.run([sys.executable, '-c', code, str(path)], cwd=ROOT, timeout=10)
                self.assertEqual(free.returncode, 0)

    def test_concurrent_processes_preserve_all_budget_records(self):
        from formal_src.budget import records
        code = """
import sys
from formal_src.budget import append
for i in range(8): append(sys.argv[1], {'worker':sys.argv[2], 'item':i})
"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'budget.jsonl'
            children = [subprocess.Popen([sys.executable, '-c', code, str(path), str(i)], cwd=ROOT) for i in range(4)]
            try:
                for child in children: self.assertEqual(child.wait(timeout=20), 0)
                rows = records(path)
                self.assertEqual(len(rows), 32)
                self.assertEqual(len({(r['worker'], r['item']) for r in rows}), 32)
            finally:
                for child in children:
                    if child.poll() is None: child.kill(); child.wait()

    def test_atomic_state_and_journal_roundtrip_non_ascii(self):
        from app.jobs import atomic, read
        from formal_src.budget import append, records
        with tempfile.TemporaryDirectory(prefix='中文-') as td:
            path = Path(td) / '状态.json'
            atomic(path, {'text': '金融资料 €'})
            self.assertEqual(read(path), {'text': '金融资料 €'})
            ledger = Path(td) / '账本.jsonl'
            append(ledger, {'text': '中文'})
            self.assertEqual(records(ledger), [{'text': '中文'}])

    def test_replace_retries_windows_sharing_violation(self):
        from app import fsutil
        real_replace = os.replace
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td)/'src', Path(td)/'dst'
            src.write_bytes(b'new'); dst.write_bytes(b'old')
            calls = []
            def replace(a, b):
                calls.append(1)
                if len(calls) == 1:
                    error = PermissionError(errno.EACCES, 'sharing'); error.winerror = 32
                    raise error
                return real_replace(a, b)
            with patch.object(fsutil.os, 'name', 'nt'), patch.object(fsutil.os, 'replace', replace):
                fsutil.replace(src, dst)
            self.assertEqual(dst.read_bytes(), b'new')

    def test_windows_crt_read_error_without_winerror_is_retried(self):
        from app import fsutil
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'; path.write_text('中文', encoding='utf-8')
            original = type(path).read_text
            calls = []
            def reading(target, **kwargs):
                calls.append(1)
                if len(calls) == 1: raise PermissionError(errno.EACCES, 'CRT sharing error')
                return original(target, **kwargs)
            with patch.object(fsutil.os, 'name', 'nt'), patch.object(type(path), 'read_text', reading):
                self.assertEqual(fsutil.read_text(path), '中文')

    def test_windows_persistent_permission_error_is_bounded(self):
        from app import fsutil
        calls = []
        def denied(*args):
            calls.append(1)
            raise PermissionError(errno.EACCES, 'denied')
        with patch.object(fsutil.os, 'name', 'nt'), patch.object(fsutil.os, 'replace', denied):
            with self.assertRaises(PermissionError): fsutil.replace('a', 'b', attempts=3)
        self.assertEqual(len(calls), 3)

    def test_runner_pause_survives_control_file_sharing_conflict(self):
        from app import fsutil
        from app.jobs import atomic
        from formal_src.runner import run
        from tests.formal.test_runner import demo_fixture
        with tempfile.TemporaryDirectory() as td:
            spec, rd = demo_fixture(Path(td)); atomic(rd/'control.json', {'action':'pause'})
            original = type(rd).read_text; failed = []
            def reading(path, *args, **kwargs):
                if path.name == 'control.json' and not failed:
                    failed.append(True); raise PermissionError(errno.EACCES, 'sharing')
                return original(path, *args, **kwargs)
            with patch.object(type(rd), 'read_text', reading), patch.object(fsutil, '_sharing_error', return_value=True):
                result = run(spec, lambda event: None, {'run_dir':str(rd)}, run_dir=rd)
            self.assertTrue(failed)
            self.assertEqual(result['state'], 'paused')

    def test_fixture_pause_survives_control_file_sharing_conflict(self):
        import runpy
        from app import fsutil
        from app.jobs import atomic, read
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            atomic(rd/'inputs.json', {'schema_version':1, 'resources':[]})
            atomic(rd/'spec.json', {'resolved_config':{}})
            atomic(rd/'control.json', {'action':'pause'})
            original = type(rd).read_text; failed = []
            def reading(path, *args, **kwargs):
                if path.name == 'control.json' and not failed:
                    failed.append(True); raise PermissionError(errno.EACCES, 'sharing')
                return original(path, *args, **kwargs)
            with patch.object(type(rd), 'read_text', reading), patch.object(fsutil, '_sharing_error', return_value=True), patch.object(sys, 'argv', ['fixtures.py','--run-dir',str(rd)]):
                runpy.run_path(str(ROOT/'tests/app/fixtures.py'), run_name='__main__')
            self.assertTrue(failed)
            self.assertEqual(read(rd/'worker-result.json')['state'], 'paused')

    def test_posix_permission_failure_is_not_retried(self):
        from app import fsutil
        calls = []
        def denied(*args):
            calls.append(1)
            raise PermissionError(errno.EACCES, 'denied')
        with patch.object(fsutil.os, 'name', 'posix'), patch.object(fsutil.os, 'replace', denied):
            with self.assertRaises(PermissionError): fsutil.replace('a', 'b')
        self.assertEqual(len(calls), 1)

    def test_process_identity_rejects_mismatch_and_exited_child(self):
        from app.jobs import JobStore
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            identity = JobStore._fingerprint(child.pid)
            self.assertIsNotNone(identity)
            self.assertTrue(JobStore._alive(child.pid, identity))
            self.assertFalse(JobStore._alive(child.pid, 'windows:wrong:identity'))
            child.terminate(); child.wait(timeout=10)
            self.assertFalse(JobStore._alive(child.pid, identity))
        finally:
            if child.poll() is None: child.kill(); child.wait()

    def test_windows_process_creation_time_and_64_bit_handle(self):
        import ctypes
        from unittest.mock import Mock
        from app.jobs import JobStore
        kernel = Mock()
        handle = 0x100000001
        kernel.OpenProcess.return_value = handle
        kernel.WaitForSingleObject.return_value = 258
        kernel.CloseHandle.return_value = 1
        def times(h, created, exited, system, user):
            self.assertEqual(h, handle)
            created._obj.dwHighDateTime = 2
            created._obj.dwLowDateTime = 3
            return 1
        kernel.GetProcessTimes.side_effect = times
        with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), patch('app.jobs.os.name', 'nt'):
            first = JobStore._fingerprint(42)
            self.assertEqual(first, 'windows:42:8589934595')
            self.assertTrue(JobStore._alive(42, first))
            self.assertFalse(JobStore._alive(42, 'windows:42:999'))
            kernel.WaitForSingleObject.return_value = 0
            self.assertFalse(JobStore._alive(42, first))
        self.assertEqual(kernel.CloseHandle.call_count, 4)
        self.assertEqual(kernel.OpenProcess.restype, ctypes.c_void_p)

    def test_root_model_manifest_and_empty_pooling_path(self):
        from app.course_readiness import _Qualification
        from collections import defaultdict
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            q = _Qualification.__new__(_Qualification)
            q.errors = defaultdict(set); q.bindings = {}
            for name in ('modules.json', 'config.json', 'tokenizer.json', 'model.safetensors'):
                p = root/name
                p.write_text('[{"type":"sentence_transformers.models.Pooling","path":""}]' if name == 'modules.json' else '{}', encoding='utf-8')
                q.bindings[name] = {'path':name, 'kind':'model', 'absolute':p}
            q.model()
            self.assertFalse(q.errors)

    def test_diagnostics_without_nofollow_fails_closed(self):
        from app.api import create_app
        from app.service import ApplicationService
        from fastapi.testclient import TestClient
        from uuid import uuid4
        with tempfile.TemporaryDirectory() as td:
            service = ApplicationService(ROOT, Path(td)/'work')
            rid = str(uuid4()); directory = service.jobs.directory(rid); directory.mkdir()
            (directory/'diagnostics.json').write_text('{"stage":"worker"}', encoding='utf-8')
            original = getattr(os, 'O_NOFOLLOW', None)
            try:
                if original is not None: del os.O_NOFOLLOW
                response = TestClient(create_app(service)).get('/api/v1/runs/'+rid+'/diagnostics')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {})
            finally:
                if original is not None: os.O_NOFOLLOW = original

    def test_importing_launcher_does_not_reexec_or_change_utf8_environment(self):
        code = """
import os, runpy
os.execv=lambda *args: (_ for _ in ()).throw(AssertionError('import re-executed interpreter'))
runpy.run_path('scripts/course_entry.py', run_name='imported_course_entry')
assert os.environ.get('PYTHONUTF8') == '0'
"""
        env = dict(os.environ, PYTHONUTF8='0')
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
