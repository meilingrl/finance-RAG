"""Reuse only the exact same question, provider and complete model request."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from reuse_llm_responses import seed_identical_responses
from llm_execution import prepare_job,execute_job
from ragpilot.pipeline import make_fixture,run_offline
from ragpilot.common import read_jsonl,write_jsonl
from test_llm_execution import responses_reply


class ReuseTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.data=self.root/'data';self.source=self.root/'source'
        make_fixture(self.data);run_offline(self.data,self.source,split='development')
        self.config={'endpoint':'https://api.pinaic.com/v1/responses','wire_api':'responses',
                     'model':'gpt-6-luna','api_key_env':'OPENAI_API_KEY','max_completion_tokens':512,
                     'reasoning_effort':'none','reserve_input_tokens':32768,
                     'input_usd_per_million':None,'output_usd_per_million':None,'timeout_seconds':30}
        self.first,self.second,self.ledger=[self.root/x for x in ('first','second','ledger.jsonl')]
        prepare_job(self.source,self.data,self.first,self.config,limit=1)
        execute_job(self.first,'test-secret',self.ledger,None,transport=lambda *args:responses_reply())

    def test_exact_reuse_keeps_response_provenance_and_avoids_another_call(self):
        prepare_job(self.source,self.data,self.second,self.config,limit=1)
        result=seed_identical_responses(self.first,self.second)
        self.assertEqual(result['reused'],1)
        execute_job(self.second,'test-secret',self.ledger,None,
                    transport=lambda *args: self.fail('Exact matching response must not be sent again'))
        self.assertEqual(len(read_jsonl(self.ledger)),1)
        record=read_jsonl(self.second/'predictions.jsonl')[0]
        self.assertEqual(record['parsed_answer'],'$5 million')
        self.assertIsNone(record['usage'])
        self.assertEqual(record['reused_source_usage']['input_tokens'],100)
        self.assertEqual(record['reuse']['source_job'],str(self.first.resolve()))

    def test_different_model_or_provider_never_reuses_an_answer(self):
        for name,change in [('model',{'model':'another-model'}),
                            ('provider',{'endpoint':'https://example.com/v1/responses'})]:
            dest=self.root/name
            prepare_job(self.source,self.data,dest,{**self.config,**change},limit=1)
            self.assertEqual(seed_identical_responses(self.first,dest)['reused'],0)
            self.assertFalse((dest/'predictions.jsonl').exists())

    def test_changed_source_prediction_cannot_be_used_as_a_cached_answer(self):
        prepare_job(self.source,self.data,self.second,self.config,limit=1)
        rows=read_jsonl(self.first/'predictions.jsonl');rows[0]['parsed_answer']='edited'
        write_jsonl(self.first/'predictions.jsonl',rows)
        with self.assertRaises(ValueError):seed_identical_responses(self.first,self.second)
        self.assertFalse((self.second/'predictions.jsonl').exists())
