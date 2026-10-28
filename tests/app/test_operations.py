import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from fastapi.testclient import TestClient
from app.api import create_app
from app.service import ApplicationService


class OperationTests(unittest.TestCase):
    def test_lost_response_refresh_and_duplicate_submission_execute_only_once(self):
        with tempfile.TemporaryDirectory() as td:
            service=ApplicationService(td,Path(td)/'work');client=TestClient(create_app(service))
            release=threading.Event();entered=threading.Event();calls=[]
            def prepare(*args):
                calls.append(args);entered.set();release.wait(3)
                return {'ready':True,'request':{'kind':'experiment'}}
            op=str(uuid4());headers={'x-session-id':service.session_id,'x-operation-id':op}
            with patch.object(service,'prepare_experiment',side_effect=prepare):
                try:
                    first=client.post('/api/v1/experiments/prepare',headers=headers,json={'model_profile_id':'formal-v1'})
                    self.assertEqual(first.status_code,202)
                    self.assertTrue(entered.wait(1))
                    duplicate=client.post('/api/v1/experiments/prepare',headers=headers,json={'model_profile_id':'formal-v1'})
                    self.assertEqual(duplicate.json()['operation_id'],op)
                    status=client.get('/api/v1/operations/'+op).json()
                    self.assertEqual(status['state'],'running')
                    changed=client.post('/api/v1/experiments/prepare',headers=headers,json={'model_profile_id':'different'})
                    self.assertEqual(changed.status_code,409)
                finally:release.set()
                for _ in range(100):
                    status=client.get('/api/v1/operations/'+op).json()
                    if status.get('state')=='completed':break
                    time.sleep(.01)
                self.assertTrue(status['result']['ready']);self.assertEqual(len(calls),1)
                restarted=TestClient(create_app(ApplicationService(td,Path(td)/'work')))
                self.assertEqual(restarted.get('/api/v1/operations/'+op).status_code,404)

    def test_existing_synchronous_route_keeps_its_response(self):
        with tempfile.TemporaryDirectory() as td:
            service=ApplicationService(td,Path(td)/'work');client=TestClient(create_app(service))
            with patch.object(service,'prepare_experiment',return_value={'ready':False,'state':'configuration_required'}):
                result=client.post('/api/v1/experiments/prepare',headers={'x-session-id':service.session_id},json={})
            self.assertEqual(result.status_code,200);self.assertEqual(result.json()['state'],'configuration_required')

