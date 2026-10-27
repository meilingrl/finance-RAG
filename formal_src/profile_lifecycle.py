"""Freeze runtime identities without modifying the shipped research recipe."""
from __future__ import annotations
from copy import deepcopy
import re
from .contracts import safe_json
from .course_policy import automatic,readiness_valid
from .run_plan import CANONICAL_PROFILE, content_sha

DYNAMIC={'status','models','provider','prompt_sha','resource_manifest_sha','freeze_sha','readiness','code_sha','concurrency','audit_plan_sha256'}


def validate_recipe(profile):
    safe_json(profile);automatic(profile)
    if set(profile)-set(CANONICAL_PROFILE)-{'code_sha','concurrency','audit_plan_sha256','review_policy'}:
        raise ValueError('Unknown formal runtime field')
    if set(CANONICAL_PROFILE)-set(profile):raise ValueError('Incomplete research recipe')
    for key,value in CANONICAL_PROFILE.items():
        if key not in DYNAMIC and profile[key]!=value:
            raise ValueError('Frozen research recipe cannot be changed')


def freeze_formal_profile(draft:dict, *, models:dict, provider:dict, prompt_sha:str,
                          resource_manifest_sha:str, code_sha:str, readiness:dict,
                          concurrency:int=4,audit_plan_sha256:str|None=None)->dict:
    validate_recipe(draft)
    if draft['status']!='draft':raise ValueError('Already frozen; create a new version')
    profile=deepcopy(draft)
    profile.update(models=deepcopy(models),provider=deepcopy(provider),prompt_sha=prompt_sha,
                   resource_manifest_sha=resource_manifest_sha,code_sha=code_sha,
                   readiness=deepcopy(readiness),concurrency=concurrency,audit_plan_sha256=audit_plan_sha256,status='frozen',freeze_sha=None)
    profile['freeze_sha']=content_sha(profile)
    validate_frozen_profile(profile,profile['freeze_sha'])
    return profile


def validate_frozen_profile(profile:dict,expected_freeze_sha:str|None=None)->bool:
    validate_recipe(profile)
    if profile['status']!='frozen':raise ValueError('Formal profile is not frozen')
    if not readiness_valid(profile):
        raise ValueError('Formal quality and readiness checks are incomplete')
    for key in ('prompt_sha','resource_manifest_sha','code_sha','freeze_sha','audit_plan_sha256'):
        if key=='audit_plan_sha256' and automatic(profile) and profile.get(key) is None:continue
        if not isinstance(profile.get(key),str) or not re.fullmatch('[0-9a-f]{64}',profile[key]):
            raise ValueError('Actual runtime identity is required')
    if profile['models']!={'primary':'gpt-6.1-sol','secondary':'gpt-6-astra'}:
        raise ValueError('Model configuration differs from the declared formal recipe')
    provider=profile['provider']
    if (not isinstance(provider,dict) or provider.get('max_output_tokens')!=2048
            or provider.get('reasoning_effort')!='low' or provider.get('stream') is not True):
        raise ValueError('Generation protocol differs from declared formal protocol')
    from .provider import validate_config
    validate_config({**provider,'model':profile['models']['primary']})
    if type(profile.get('concurrency')) is not int or profile['concurrency'] not in (1,2,4):
        raise ValueError('Unsupported verified concurrency')
    claimed=profile['freeze_sha'];unsealed={**profile,'freeze_sha':None}
    if content_sha(unsealed)!=claimed or expected_freeze_sha is not None and claimed!=expected_freeze_sha:
        raise ValueError('Frozen runtime identity changed')
    return True
