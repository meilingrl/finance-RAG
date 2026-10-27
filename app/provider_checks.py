"""Configuration-bound synthetic connectivity and parallel probes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from pathlib import Path
from formal_src.provider import build_payload, digest, validate_config
from .jobs import atomic, read


def concurrency(settings):
    value=settings.get('concurrency',4)
    if type(value) is not int or not 1<=value<=32:
        raise ValueError('concurrency_invalid')
    return value


def identity(settings):
    endpoint=settings.get('endpoint') or settings.get('base_url','').rstrip('/')+'/responses'
    config={**settings,'endpoint':endpoint,
            'max_output_tokens':settings.get('max_output_tokens',2048),
            'reserve_input_tokens':settings.get('reserve_input_tokens',32768)}
    validate_config(config)
    payload=build_payload(config,'What is the marker?',[
        {'evidence_id':'operational-probe','text':'The marker is READY.'}])
    return digest(dict(endpoint=endpoint,payload=payload,concurrency=concurrency(settings),
                       reserve_input_tokens=config['reserve_input_tokens'],
                       timeout_seconds=config.get('timeout_seconds',120)))


def proof_path(workspace, fingerprint):
    return Path(workspace)/'provider-checks'/(fingerprint+'.json')


def parallel_verified(workspace, profile):
    try:
        fingerprint=identity({**profile.get('provider',{}),
                              'model':profile.get('models',{}).get('primary'),
                              'concurrency':profile.get('concurrency',4)})
        result=read(proof_path(workspace,fingerprint),{})
        return (result.get('configuration_hash')==fingerprint and result.get('ready') is True
                and result.get('parallel',{}).get('success_count')==concurrency(profile))
    except (ValueError,TypeError,KeyError,OSError):
        return False


def check_compatibility(service, settings, credential_ref):
    count=concurrency(settings)
    fingerprint=identity(settings)
    # Invalidate previous success before starting another check of this configuration.
    path=proof_path(service.workspace,fingerprint)
    atomic(path,dict(configuration_hash=fingerprint,ready=False))
    def probe():
        try:
            return service.check_provider(settings,credential_ref,'generation')
        except ValueError:
            # Budget failures occur before dispatch, and must remain inspectable.
            return dict(ready=False,blocking_items=['budget_check_failed'],sent_attempts=0)
    single=probe()
    peers=[]
    if single['ready'] and count>1:
        barrier=Barrier(count)
        def parallel_probe():
            barrier.wait(timeout=10)
            return probe()
        with ThreadPoolExecutor(max_workers=count) as pool:
            peers=list(pool.map(lambda _:parallel_probe(),range(count)))
    success=int(single['ready']) if count==1 else sum(p['ready'] for p in peers)
    blocks=sorted({code for p in peers for code in p['blocking_items']})
    parallel=dict(ready=success==count,concurrency=count,success_count=success,
                  blocking_items=blocks if peers or single['ready'] and count==1 else ['single_check_failed'],
                  sent_attempts=sum(p['sent_attempts'] for p in peers))
    report=dict(ready=single['ready'] and parallel['ready'],single=single,parallel=parallel,
                sent_attempts=single['sent_attempts']+parallel['sent_attempts'],
                blocking_items=sorted(set(single['blocking_items']+parallel['blocking_items'])),
                configuration_hash=fingerprint,level='compatibility',purpose='operational_probe',
                checked_at=datetime.now(timezone.utc).isoformat())
    atomic(path,report)
    return report
