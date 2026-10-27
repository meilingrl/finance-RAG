"""Read-only capacity estimate from saved live calls; never certifies throughput."""
import json,math,statistics
from pathlib import Path

def estimate(rows,remaining_attempts,token_limit=100_000_000,target_cny=50):
 valid=[r for r in rows if isinstance(r.get('usage',{}).get('input_tokens'),(int,float)) and isinstance(r.get('usage',{}).get('output_tokens'),(int,float))]
 times=[r['elapsed_ms']/1000 for r in rows if r.get('elapsed_ms',0)>0]
 mean=statistics.mean([r['usage']['input_tokens']+r['usage']['output_tokens'] for r in valid]) if valid else None
 tokens=math.ceil(mean*6600) if mean is not None else None
 coverage=sorted({r.get('model_role','primary')+':'+r['condition'] for r in rows})
 return {'schema_version':1,'basis':'historical_development_samples_not_full_plan_benchmark','logical_generations':6600,'primary_generations':6150,'secondary_generations':450,'remaining_attempts':remaining_attempts,'attempt_budget_sufficient':remaining_attempts>=6600,'sample_count':len(rows),'sample_with_usage':len(valid),'failed_sample_count':sum(r['status'] in ('failed','parse_error','truncated','api_error') for r in rows),'coverage':coverage,'mean_sample_tokens':mean,'estimate_tokens':tokens,'estimate_tokens_with_20pct_headroom':math.ceil(tokens*1.2) if tokens is not None else None,'token_limit':token_limit,'target_cny':target_cny,'estimated_cost_cny':None,'blended_cny_per_million_needed_for_target':target_cny/(tokens/1e6) if tokens else None,'mean_call_seconds':statistics.mean(times) if times else None,'serial_hours_proxy':statistics.mean(times)*6600/3600 if times else None,'four_concurrent_hours_ideal_proxy':statistics.mean(times)*6600/3600/4 if times else None,'throughput_validated':False,'missing_evidence':['representative_formal_inputs_including_full_page','secondary_model_usage_and_latency','measured_concurrency_and_rate_limits','channel_price_for_monetary_estimate']}

def from_workspace(workspace,remaining_attempts):
 rows=[];seen=set();sources=[]
 for path in sorted(Path(workspace).glob('runs/*/predictions.jsonl')):
  spec=json.loads((path.parent/'spec.json').read_text())
  if spec.get('purpose')=='software_test':continue
  seal=path.parent/'sealed.json'
  if not seal.exists() or json.loads(seal.read_text()).get('synthetic') is not False:continue
  selected=0
  for line in path.read_text().splitlines():
   r=json.loads(line)
   if r.get('provenance')!='live':continue
   refs=tuple(r.get('attempt_refs',[]))
   if not refs or refs in seen:continue
   seen.add(refs);rows.append(r);selected+=1
  if selected:sources.append({'run_id':path.parent.name,'calls':selected})
 result=estimate(rows,remaining_attempts);result['sources']=sources
 return result
