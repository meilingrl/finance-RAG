"""Configuration-bound, request-counted text, vision and parallel service probes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from threading import Barrier
from formal_src.provider import build_probe_payload,digest,normalize_config,PROVIDER_FIELDS
from .jobs import atomic,read
from .probe_images import make_image_probe

def concurrency(settings):
 value=settings.get('concurrency',4)
 if type(value) is not int or not 1<=value<=32:raise ValueError('concurrency_invalid')
 return value

def provider_values(settings):
 value=normalize_config({**settings,'max_output_tokens':settings.get('max_output_tokens',2048),
  'reserve_input_tokens':settings.get('reserve_input_tokens',32768)})
 if isinstance(value.get('model_aliases'),list):value['model_aliases']={settings['model']:value['model_aliases']} if value['model_aliases'] else {}
 if isinstance(value.get('model_aliases'),dict):value['model_aliases']={key:aliases for key,aliases in value['model_aliases'].items() if aliases}
 return {key:value[key] for key in PROVIDER_FIELDS if key in value}

def identity(settings):
 public=provider_values(settings)
 return digest(dict(provider=public,model=settings.get('model'),concurrency=concurrency(settings),
  payload=build_probe_payload({**public,'model':settings.get('model')})))

def proof_path(workspace,fingerprint):return Path(workspace)/'provider-checks'/(fingerprint+'.json')

def provider_ready(workspace,profile,*,require_vision=False,secondary=None):
 try:
  settings={**profile.get('provider',{}),'model':profile.get('models',{}).get('primary'),'concurrency':profile.get('concurrency',4)}
  fingerprint=identity(settings);r=read(proof_path(workspace,fingerprint),{})
  if r.get('configuration_hash')!=fingerprint or r.get('ready') is not True or r.get('parallel',{}).get('success_count')!=concurrency(settings):return False
  if require_vision and not (r.get('vision',{}).get('ready') is True and r['vision'].get('required') is True and r['vision'].get('sent_attempts',0)>0):return False
  if secondary:
   second=r.get('secondary',{})
   if second.get('model')!=secondary or second.get('ready') is not True:return False
   if require_vision and not (second.get('vision',{}).get('ready') is True and second['vision'].get('required') is True and second['vision'].get('sent_attempts',0)>0):return False
  return True
 except (ValueError,TypeError,KeyError,OSError):return False

def parallel_verified(workspace,profile):return provider_ready(workspace,profile)

def check_compatibility(service,settings,credential_ref):
 count=concurrency(settings);fingerprint=identity(settings);path=proof_path(service.workspace,fingerprint)
 atomic(path,dict(configuration_hash=fingerprint,ready=False))
 effective={**settings}
 def probe(values=None):
  try:return service.check_provider(values or effective,credential_ref,'generation')
  except ValueError as exc:
   code=str(exc)
   if not code.replace('_','').isalnum() or len(code)>80:code='provider_configuration_invalid'
   return dict(ready=False,blocking_items=[code],sent_attempts=0)
 single=probe();negotiation_attempts=0
 if not single['ready'] and effective.get('response_format','auto')=='auto' and set(single['blocking_items']) & {'http_400','http_422'}:
  negotiation_attempts+=single['sent_attempts'];effective['response_format']='json_object';single=probe()
 require_vision=settings.get('require_vision',False);vision={'ready':not require_vision,'sent_attempts':0,'blocking_items':[],'required':require_vision}
 if single['ready'] and require_vision:
  url,marker=make_image_probe();vision={**probe({**effective,'_probe_image':url,'_probe_marker':marker}),'required':True}
  if not vision['ready']:vision['blocking_items']=['vision_'+code for code in vision['blocking_items']]
 peers=[]
 if single['ready'] and vision['ready'] and count>1:
  barrier=Barrier(count)
  def parallel_probe(_):barrier.wait(timeout=10);return probe()
  with ThreadPoolExecutor(max_workers=count) as pool:peers=list(pool.map(parallel_probe,range(count)))
 success=int(single['ready'] and vision['ready']) if count==1 else sum(p['ready'] for p in peers)
 parallel={'ready':success==count,'concurrency':count,'success_count':success,
  'blocking_items':sorted({c for p in peers for c in p['blocking_items']}) if peers else ([] if success==count else ['single_check_failed' if not single['ready'] else 'vision_check_failed']),
  'sent_attempts':sum(p['sent_attempts'] for p in peers)}
 secondary={'ready':True,'sent_attempts':0,'blocking_items':[]};second_model=settings.get('secondary_model')
 if second_model and single['ready'] and vision['ready'] and parallel['ready']:
  second=probe({**effective,'model':second_model});secondary={**second,'model':second_model}
  if second['ready'] and require_vision:
   url,marker=make_image_probe();second_image=probe({**effective,'model':second_model,'_probe_image':url,'_probe_marker':marker})
   secondary.update(ready=second_image['ready'],vision={**second_image,'required':True},sent_attempts=second['sent_attempts']+second_image['sent_attempts'],blocking_items=second_image['blocking_items'])
  secondary['blocking_items']=['secondary_'+code for code in secondary['blocking_items']]
 report={'ready':single['ready'] and vision['ready'] and parallel['ready'] and secondary['ready'],
  'single':single,'vision':vision,'parallel':parallel,'secondary':secondary,
  'sent_attempts':negotiation_attempts+single['sent_attempts']+vision['sent_attempts']+parallel['sent_attempts']+secondary['sent_attempts'],
  'blocking_items':sorted(set(single['blocking_items']+vision['blocking_items']+parallel['blocking_items']+secondary['blocking_items'])),
  'configuration_hash':fingerprint,'level':'compatibility','purpose':'operational_probe','checked_at':datetime.now(timezone.utc).isoformat(),
  'effective_provider':provider_values(effective)}
 actual=identity({**report['effective_provider'],'model':settings['model'],'concurrency':count})
 atomic(path,{**report,'ready':False,'negotiated':True} if actual!=fingerprint else report)
 if actual!=fingerprint:atomic(proof_path(service.workspace,actual),{**report,'configuration_hash':actual})
 if report['ready']:service._provider_credentials[actual]=credential_ref
 return report
