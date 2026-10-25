import json
import unittest
from uuid import uuid4
try:
    from formal_src.contracts import RunRequest, RunSnapshot, RunEvent, ContractError
except ImportError:
    RunRequest = RunSnapshot = RunEvent = ContractError = None

class ContractTests(unittest.TestCase):
    def request(self, **changes):
        return dict(schema_version=1, client_request_id=str(uuid4()), kind='experiment', profile_id='formal-v1', resource_set_id='resources-1', source_run_id=None, demo_input=None, **changes)
    def test_contract_available(self):
        self.assertIsNotNone(RunRequest, 'RunRequest contract missing')
    def test_request_roundtrip_and_json(self):
        self.assertIsNotNone(RunRequest)
        data = self.request()
        self.assertEqual(json.loads(json.dumps(RunRequest.from_dict(data).to_dict())), data)
    def test_reject_unknown_sensitive_and_overrides(self):
        self.assertIsNotNone(RunRequest)
        for key in ['api_key', 'gold', 'output_path', 'k', 'repeats', 'model', 'prompt']:
            with self.subTest(key=key), self.assertRaises(ContractError):
                RunRequest.from_dict({**self.request(), key:'bad'})
    def test_kind_uuid_verify_demo_boundaries(self):
        self.assertIsNotNone(RunRequest)
        for changes in [dict(kind='other'), dict(kind='verify'), dict(kind='demo'), dict(client_request_id='../bad'), dict(resource_set_id='../secret'), dict(source_run_id='bad')]:
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                RunRequest.from_dict({**self.request(), **changes})
        data = {**self.request(), 'kind':'verify', 'source_run_id':str(uuid4())}
        self.assertEqual(RunRequest.from_dict(data)['kind'], 'verify')
    def test_errors_do_not_echo_secrets(self):
        self.assertIsNotNone(RunRequest)
        try:
            RunRequest.from_dict({**self.request(), 'api_key':'sk-secret'})
        except ContractError as exc:
            out = json.dumps(exc.to_dict())
            self.assertNotIn('sk-secret', out)
            self.assertEqual(set(exc.to_dict()), {'code','message','retryable','blocking_items'})
    def test_events_reject_content_and_invalid_time(self):
        self.assertIsNotNone(RunEvent)
        data=dict(schema_version=1,run_id=str(uuid4()),seq=1,time='2026-10-24T00:00:00Z',phase='generation',event_type='progress',counts={},error_code=None)
        self.assertEqual(RunEvent.from_dict(data).to_dict(),data)
        for changes in [dict(question='secret'),dict(time='yesterday'),dict(seq=True),dict(counts={'gold':1})]:
            with self.assertRaises(ContractError): RunEvent.from_dict({**data,**changes})
    def test_snapshot_states_and_output_contract_validation(self):
        from formal_src.contracts import STATES, PHASES, ResolvedRunSpec, AnswerRecord, RunResults
        data=dict(schema_version=1,run_id=str(uuid4()),kind='experiment',purpose='formal',state='queued',phase='prepare',profile_hash='a'*64,logical_generation_count=6600,completed_logical_count=0,in_flight_count=0,attempt_count=0,failure_count=0,abstention_count=0,usage={'cost':None},last_event_seq=0,available_actions=['cancel'],created_at='2026-10-24T00:00:00Z',updated_at='2026-10-24T00:00:00Z',blocking_items=[])
        for state in STATES:
            for phase in PHASES: json.dumps(RunSnapshot.from_dict({**data,'state':state,'phase':phase}).to_dict())
        for changes in [dict(state='done'),dict(phase='unknown'),dict(profile_hash='bad'),dict(logical_generation_count=True),dict(completed_logical_count=6601),dict(usage={'cost':float('nan')})]:
            with self.subTest(changes=changes),self.assertRaises(ContractError): RunSnapshot.from_dict({**data,**changes})
        answer=dict(schema_version=1,run_id=str(uuid4()),question_id='q1',source_id='report1',retriever='bm25',model='configured-model',model_role='primary',condition='E2',repeat=1,task_id='main|q1|bm25|E2|primary|1',evidence_sha='a'*64,status='answered',answer='42',scale=None,citations=['e1'],request_hash='b'*64,attempt_refs=['attempt-1'],elapsed_ms=12,usage={'cost':None},provenance='live',reuse_source_task_id=None)
        self.assertEqual(AnswerRecord.from_dict(answer)['answer'],'42')
        for changes in [dict(repeat=0),dict(request_hash='bad'),dict(provenance='same_round_reuse'),dict(evidence_sha=None)]:
            with self.assertRaises(ContractError): AnswerRecord.from_dict({**answer,**changes})
    def answer(self):
        return dict(schema_version=1,run_id=str(uuid4()),question_id='q1',source_id='report1',retriever='bm25',model='vendor/model-v1',model_role='primary',condition='E2',repeat=1,task_id='main|q1|bm25|E2|primary|1',evidence_sha='a'*64,status='answered',answer='42',scale=None,citations=['e1'],request_hash='b'*64,attempt_refs=['attempt-1'],elapsed_ms=12,usage={'cost':None},provenance='live',reuse_source_task_id=None)
    def test_required_identity_and_time_reject_null(self):
        from formal_src.contracts import AnswerRecord
        event=dict(schema_version=1,run_id=str(uuid4()),seq=1,time='2026-10-24T00:00:00Z',phase='generation',event_type='progress',counts={},error_code=None)
        snapshot=dict(schema_version=1,run_id=str(uuid4()),kind='experiment',purpose='formal',state='queued',phase='prepare',profile_hash='a'*64,logical_generation_count=6600,completed_logical_count=0,in_flight_count=0,attempt_count=0,failure_count=0,abstention_count=0,usage={},last_event_seq=0,available_actions=[],created_at='2026-10-24T00:00:00Z',updated_at='2026-10-24T00:00:00Z',blocking_items=[])
        for cls,data,keys in [(RunRequest,self.request(),('client_request_id','profile_id','resource_set_id')),(RunEvent,event,('run_id','time')),(RunSnapshot,snapshot,('run_id','created_at','updated_at')),(AnswerRecord,self.answer(),('run_id','question_id','source_id'))]:
            for key in keys:
                with self.subTest(cls=cls.__name__,key=key),self.assertRaises(ContractError): cls.from_dict({**data,key:None})
        self.assertIsNone(RunRequest.from_dict(self.request())['source_run_id'])
        self.assertIsNone(RunSnapshot.from_dict({**snapshot,'started_at':None,'completed_at':None})['started_at'])
    def test_malformed_demo_groups_produce_serializable_contract_error(self):
        for groups in [[{}],[[]],['E1',{}]]:
            data={**self.request(),'kind':'demo','demo_input':dict(doc_id='doc1',question='What?',retriever='bm25',groups=groups)}
            with self.subTest(groups=groups):
                with self.assertRaises(ContractError) as caught: RunRequest.from_dict(data)
                json.dumps(caught.exception.to_dict())
    def test_tuple_cannot_hide_sensitive_fields(self):
        from formal_src.contracts import AnswerRecord, safe_json
        with self.assertRaises(ContractError): safe_json({'nested':({'api_key':'synthetic-marker'},)})
        with self.assertRaises(ContractError): AnswerRecord.from_dict({**self.answer(),'usage':{'nested':({'gold':'synthetic-marker'},)}})
    def test_answer_task_and_model_require_usable_identity(self):
        from formal_src.contracts import AnswerRecord
        self.assertEqual(AnswerRecord.from_dict(self.answer())['model'],'vendor/model-v1')
        for key in ('task_id','model','question_id','source_id'):
            for value in (None,12,{},[],''):
                with self.subTest(key=key,value=value),self.assertRaises(ContractError): AnswerRecord.from_dict({**self.answer(),key:value})
        for value in ('../bad','main|q1','main|q1|bm25|E2|primary|x'):
            with self.assertRaises(ContractError): AnswerRecord.from_dict({**self.answer(),'task_id':value})
