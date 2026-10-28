"""Regression checks for recoverable setup errors and append-only event reads."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.service import ApplicationService
from app.datasets import FixedDatasetError
from app.jobs import JobStore, now
from uuid import uuid4


class SetupErrorsTests(unittest.TestCase):
    def test_conflict_is_actionable_without_suggesting_network_retry(self):
        with tempfile.TemporaryDirectory() as td:
            service = ApplicationService(td, Path(td)/'work')
            with patch('app.datasets.ensure_fixed_dataset', side_effect=FixedDatasetError('Existing fixed dataset conflict; preserved: SHA256 mismatch')):
                service.begin_fixed_data_setup()
                service._data_thread.join()
            result = service.data_status()
            self.assertEqual(result['state'], 'conflict')
            self.assertFalse(result['retryable'])
            self.assertIn('校验', result['message'])

    def test_permission_error_has_distinct_action(self):
        with tempfile.TemporaryDirectory() as td:
            service = ApplicationService(td, Path(td)/'work')
            with patch('app.datasets.ensure_fixed_dataset', side_effect=PermissionError('private path')):
                service.begin_fixed_data_setup()
                service._data_thread.join()
            result = service.data_status()
            self.assertEqual(result['error_code'], 'fixed_data_permission_denied')
            self.assertNotIn('private path', json.dumps(result))


class EventReadsTests(unittest.TestCase):
    def test_poll_ignores_incomplete_tail_and_returns_it_when_committed(self):
        with tempfile.TemporaryDirectory() as td:
            store=JobStore(td); rid=str(uuid4()); directory=store.directory(rid);directory.mkdir()
            def row(seq):
                return json.dumps(dict(schema_version=1,run_id=rid,seq=seq,time=now(),phase='prepare',event_type='progress',counts={},error_code=None))
            path=directory/'events.jsonl';path.write_text(row(1)+'\n'+row(2)[:30])
            self.assertEqual([e['seq'] for e in store.events(rid)], [1])
            with path.open('a') as file:file.write(row(2)[30:]+'\n')
            self.assertEqual([e['seq'] for e in store.events(rid,1)], [2])
            self.assertEqual(store.events(rid,2), [])
            path.write_text(row(1)+'\n')
            self.assertEqual([e['seq'] for e in store.events(rid)], [1])

