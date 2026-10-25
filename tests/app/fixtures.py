"""Deterministic software-test worker. Never imports provider or reads credentials."""
import json,time,sys
from pathlib import Path
if __name__=='__main__':
 root=Path(sys.argv[sys.argv.index('--run-dir')+1]);inputs=json.loads((root/'inputs.json').read_text());assert inputs['schema_version']==1 and isinstance(inputs['resources'],list);spec=json.loads((root/'spec.json').read_text());mode=spec['resolved_config'].get('worker_scenario','complete')
 def event(name,**counts):
  with (root/'worker-events.jsonl').open('a') as f:f.write(json.dumps(dict(event_type=name,phase='prepare',counts=counts,error_code=None))+'\n')
 def attempt(state):
  with (root/'attempts.jsonl').open('a') as f:f.write(json.dumps({'attempt_id':'synthetic-attempt','state':state})+'\n')
 event('synthetic_started',in_flight_count=1)
 if mode in ('reserved','sent','completed'):attempt(mode);sys.exit(2)
 for _ in range(12):time.sleep(.02)
 event('synthetic_drained',in_flight_count=0)
 control=json.loads((root/'control.json').read_text()).get('action') if (root/'control.json').exists() else 'continue'
 state={'pause':'paused','cancel':'cancelled'}.get(control,'completed')
 (root/'worker-result.json').write_text(json.dumps(dict(state=state,blocking_items=[])))
