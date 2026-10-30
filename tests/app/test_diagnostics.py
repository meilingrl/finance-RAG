import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]


class DiagnosticsTests(unittest.TestCase):
    def test_worker_failure_records_code_location_without_exception_message_or_secret(self):
        from tests.formal.test_runner import demo_fixture
        from formal_src.runner import run
        with tempfile.TemporaryDirectory() as td:
            spec,rd=demo_fixture(Path(td))
            with patch('formal_src.runner._run_owned',side_effect=RuntimeError('Bearer secret-example-key')):
                result=run(spec,lambda e:None,lambda:'continue',run_dir=rd)
            self.assertEqual(result['state'],'failed')
            path=rd/'diagnostics.json'
            self.assertTrue(path.is_file())
            raw=path.read_text();data=json.loads(raw)
            self.assertEqual(data['exception_type'],'RuntimeError')
            self.assertTrue(data['frames']);self.assertNotIn('secret-example-key',raw)
            self.assertLess(len(raw.encode()),16384)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_cli_starts_with_utf8_disabled_in_parent(self):
        import os
        result=subprocess.run([sys.executable,'-X','utf8=0','scripts/course_entry.py','--help'],
                              cwd=ROOT,env=dict(os.environ,PYTHONUTF8='0'),
                              capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Financial RAG',result.stdout)

    def test_launcher_import_failure_leaves_a_safe_diagnostic(self):
        with tempfile.TemporaryDirectory() as td:
            code="""
import builtins,runpy,sys
original=builtins.__import__
def importing(name,*args,**kwargs):
 if name=='formal_src.runner':raise ImportError('secret-import-detail')
 return original(name,*args,**kwargs)
builtins.__import__=importing
sys.argv=['run_formal.py','--run-dir',sys.argv[1]]
runpy.run_path('scripts/run_formal.py',run_name='__main__')
"""
            result=subprocess.run([sys.executable,'-c',code,td],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(result.returncode,1)
            raw=(Path(td)/'diagnostics.json').read_text()
            self.assertEqual(json.loads(raw)['exception_type'],'ImportError')
            self.assertNotIn('secret-import-detail',raw)

    def test_diagnostic_endpoint_does_not_follow_symlinks(self):
        from app.api import create_app
        from app.service import ApplicationService
        from fastapi.testclient import TestClient
        from uuid import uuid4
        with tempfile.TemporaryDirectory() as td:
            service=ApplicationService(ROOT,Path(td)/'work');rid=str(uuid4())
            directory=service.jobs.directory(rid);directory.mkdir()
            private=Path(td)/'private.json';private.write_text('{"secret":"never-expose"}')
            (directory/'diagnostics.json').symlink_to(private)
            response=TestClient(create_app(service)).get('/api/v1/runs/'+rid+'/diagnostics')
            self.assertNotIn('never-expose',response.text)
