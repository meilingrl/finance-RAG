"""Strict JSON contracts. Validation never includes input values in errors.

from_dict validates a defensive JSON copy; to_dict returns another copy.
Instances are mappings for compatibility with application/runner dict interfaces.
Consumers must revalidate after mutation at each trust boundary.
"""
import copy
import json
import re
from datetime import datetime
from uuid import UUID

SCHEMA_VERSION = 1
KINDS = ('prepare', 'demo', 'experiment', 'verify')
STATES = ('queued', 'validating', 'blocked', 'running', 'pause_requested', 'paused', 'cancel_requested', 'cancelled', 'interrupted', 'completed', 'failed')
PHASES = ('prepare', 'review', 'freeze', 'retrieval', 'generation', 'scoring', 'statistics', 'audit')
RETRIEVERS = ('bm25', 'minilm')
CONDITIONS = ('E1', 'E1_legacy', 'E2', 'E3', 'E4', 'D', 'L', 'G', 'I', 'C0', 'C1', 'E2_k1', 'E2_k4', 'E2_no_association', 'M2_E2', 'M2_E3', 'M2_E4')
PURPOSES = ('formal', 'custom', 'demo', 'development', 'verification', 'software_test')

class ContractError(ValueError):
    def __init__(self, message='Invalid contract', code='invalid_contract', blocking_items=None):
        super().__init__(message)
        self.code, self.message = code, message
        self.blocking_items = list(blocking_items or [])
    def to_dict(self):
        return dict(code=self.code, message=self.message, retryable=False, blocking_items=self.blocking_items)

def fail(message='Invalid contract'): raise ContractError(message)
def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',value) or '..' in value: fail('Invalid identifier')
def uuid(value):
    if not isinstance(value,str): fail('Invalid UUID')
    try:
        if str(UUID(value)) != value.lower(): fail('Invalid UUID')
    except (ValueError, AttributeError): fail('Invalid UUID')
def integer(value):
    if type(value) is not int or value < 0: fail('Invalid nonnegative integer')
def timestamp(value):
    if not isinstance(value,str): fail('Invalid UTC timestamp')
    try:
        date=datetime.fromisoformat(value.replace('Z','+00:00'))
        if date.utcoffset() is None or date.utcoffset().total_seconds()!=0: fail('Invalid UTC timestamp')
    except ValueError: fail('Invalid UTC timestamp')
def relative(value):
    if not isinstance(value,str) or not value or '\\' in value or value.startswith('/') or ':' in value or any(p in ('','..','.') for p in value.split('/')): fail('Invalid relative path')
def fields(data, required, optional=()):
    if not isinstance(data,dict) or set(data)-set(required)-set(optional) or set(required)-set(data): fail('Missing or unknown fields')
def safe_json(data):
    try: json.dumps(data,allow_nan=False)
    except (TypeError, ValueError): fail('Expected finite JSON values')
    def walk(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if not isinstance(key,str): fail('Expected string keys')
                normalized=key.lower().replace('-','_')
                if normalized in {'api_key','apikey','authorization','credential','credentials','credential_ref','secret','password','token','gold','gold_answer','gold_path','ground_truth','answer_key'}: fail('Sensitive fields are forbidden')
                walk(item)
        elif isinstance(value,list):
            for item in value: walk(item)
        elif value is not None and type(value) not in (str, int, float, bool):
            fail('Expected JSON node types')
    walk(data)
    return copy.deepcopy(data)

class Contract(dict):
    required=()
    optional=()
    @classmethod
    def from_dict(cls,data):
        fields(data,cls.required,cls.optional)
        value=safe_json(data)
        if 'schema_version' in value and (type(value['schema_version']) is not int or value['schema_version']!=1): fail('Unsupported schema version')
        for key in ('run_id','source_run_id','client_request_id'):
            if key in value and (key != 'source_run_id' or value[key] is not None): uuid(value[key])
        for key in ('profile_id','resource_set_id','doc_id','question_id','source_id'):
            if key in value: identifier(value[key])
        for key in ('time','created_at','updated_at','started_at','completed_at'):
            if key in value and (key in cls.required or value[key] is not None): timestamp(value[key])
        for key, choices in [('kind',KINDS),('state',STATES),('phase',PHASES),('purpose',PURPOSES)]:
            if key in value and value[key] not in choices: fail('Invalid enum')
        for key in ('profile_hash','request_hash','code_sha','resource_sha','evidence_sha','input_sha','output_sha','evaluation_sha'):
            if key in value and (not isinstance(value[key],str) or not re.fullmatch(r'[0-9a-f]{64}',value[key])): fail('Invalid SHA256')
        if 'freeze_sha' in value and value['freeze_sha'] is not None and (not isinstance(value['freeze_sha'],str) or not re.fullmatch(r'[0-9a-f]{64}',value['freeze_sha'])): fail('Invalid freeze SHA256')
        cls.validate(value)
        return cls(value)
    @classmethod
    def validate(cls,data): pass
    def to_dict(self): return copy.deepcopy(dict(self))

class RunRequest(Contract):
    required=('schema_version','client_request_id','kind','profile_id','resource_set_id','source_run_id','demo_input')
    @classmethod
    def validate(cls,d):
        if (d['kind']=='verify') != (d['source_run_id'] is not None): fail('Source run is required only for verify')
        if d['kind']=='demo':
            v=d['demo_input']; fields(v,('doc_id','question','retriever','groups'))
            identifier(v['doc_id'])
            if not isinstance(v['question'],str) or not v['question'].strip(): fail('Demo question is required')
            if v['retriever'] not in RETRIEVERS or not isinstance(v['groups'],list) or not v['groups'] or any(not isinstance(g,str) or g not in ('E1','E2','E3','E4') for g in v['groups']) or len(set(v['groups']))!=len(v['groups']): fail('Invalid demo groups or retriever')
        elif d['demo_input'] is not None: fail('Demo input is allowed only for demo')
        identifier(d['profile_id']); identifier(d['resource_set_id'])

def validate_run_request(data): return RunRequest.from_dict(data).to_dict()

class ResolvedRunSpec(Contract):
    required=RunRequest.required+('run_id','purpose','resolved_config','code_sha','resource_sha','freeze_sha','plan','logical_generation_count','output_relative_path')
    @classmethod
    def validate(cls,d):
        RunRequest.from_dict({k:d[k] for k in RunRequest.required})
        integer(d['logical_generation_count']); relative(d['output_relative_path'])
        if not isinstance(d['resolved_config'],dict) or not isinstance(d['plan'],dict): fail('Invalid resolved configuration or plan')

class RunSnapshot(Contract):
    required=('schema_version','run_id','kind','purpose','state','phase','profile_hash','logical_generation_count','completed_logical_count','in_flight_count','attempt_count','failure_count','abstention_count','usage','last_event_seq','available_actions','created_at','updated_at','blocking_items')
    optional=('started_at','completed_at','audit_complete','error')
    @classmethod
    def validate(cls,d):
        for key in ('logical_generation_count','completed_logical_count','in_flight_count','attempt_count','failure_count','abstention_count','last_event_seq'): integer(d[key])
        if d['completed_logical_count']>d['logical_generation_count']: fail('Progress exceeds plan')
        if not isinstance(d['available_actions'],list) or any(a not in ('pause','resume','cancel','inspect','verify','export') for a in d['available_actions']): fail('Invalid action')
        if not isinstance(d['usage'],dict) or not isinstance(d['blocking_items'],list): fail('Invalid snapshot metadata')
        if 'audit_complete' in d and type(d['audit_complete']) is not bool: fail('Invalid audit status')

class RunEvent(Contract):
    required=('schema_version','run_id','seq','time','phase','event_type','counts','error_code')
    @classmethod
    def validate(cls,d):
        integer(d['seq'])
        if d['seq']<1: fail('Event sequence starts at one')
        identifier(d['event_type'])
        if d['error_code'] is not None: identifier(d['error_code'])
        allowed={'logical_generation_count','completed_logical_count','in_flight_count','attempt_count','failure_count','abstention_count','local_baseline_count','completed_local_count','reuse_count','retry_count'}
        if not isinstance(d['counts'],dict) or set(d['counts'])-allowed: fail('Invalid event counts')
        for v in d['counts'].values(): integer(v)

class AnswerRecord(Contract):
    required=('schema_version','run_id','question_id','source_id','retriever','model','model_role','condition','repeat','task_id','evidence_sha','status','answer','scale','citations','request_hash','attempt_refs','elapsed_ms','usage','provenance','reuse_source_task_id')
    @classmethod
    def validate(cls,d):
        if d['retriever'] not in RETRIEVERS or d['condition'] not in CONDITIONS or d['model_role'] not in ('primary','secondary','local'): fail('Invalid answer experiment identity')
        if not isinstance(d['model'],str) or not d['model'].strip() or len(d['model']) > 256 or any(ord(c)<32 for c in d['model']): fail('Invalid model identity')
        if not isinstance(d['task_id'],str): fail('Invalid task identity')
        parts=d['task_id'].split('|')
        if len(parts)!=6: fail('Invalid task identity')
        for part in parts[:5]: identifier(part)
        if parts[0] not in ('main','diagnostic','s50','development','validation','demo','custom') or parts[2] not in RETRIEVERS or parts[3] not in CONDITIONS or parts[4] not in ('primary','secondary','local') or parts[5] not in ('1','2','3'): fail('Invalid task identity')
        integer(d['repeat']); integer(d['elapsed_ms'])
        if d['repeat'] not in (1,2,3): fail('Invalid repeat')
        if d['status'] not in ('answered','abstained','refused','failed','parse_error','truncated'): fail('Invalid answer status')
        if d['provenance'] not in ('live','same_round_reuse','local'): fail('Invalid answer provenance')
        if (d['provenance']=='same_round_reuse') != (d['reuse_source_task_id'] is not None): fail('Invalid reuse reference')
        if not isinstance(d['citations'],list) or not isinstance(d['attempt_refs'],list) or not isinstance(d['usage'],dict): fail('Invalid answer metadata')

class RunResults(Contract):
    required=('schema_version','run_id','input_sha','output_sha','evaluation_sha','partitions','denominators','metrics','main_comparisons','engineering','audit_complete','answer_tables','exports')
    @classmethod
    def validate(cls,d):
        if type(d['audit_complete']) is not bool: fail('Invalid audit status')
        for key in ('partitions','denominators','metrics','engineering'):
            if not isinstance(d[key],dict): fail('Invalid results object')
        for key in ('main_comparisons','answer_tables','exports'):
            if not isinstance(d[key],list): fail('Invalid results list')
        for path in d['answer_tables']+d['exports']: relative(path)

class ResourceReport(Contract):
    required=('schema_version','resource_set_id','resources','ready_for','blocking_items')

class PreflightReport(Contract):
    required=('schema_version','ready','blocking_items','profile_hash','estimated_logical_generations','estimated_additional_attempts','budget_remaining','price_status')
    @classmethod
    def validate(cls,d):
        if type(d['ready']) is not bool: fail('Invalid readiness')
        for key in ('estimated_logical_generations','estimated_additional_attempts','budget_remaining'): integer(d[key])
