#!/usr/bin/env python3
"""Run only predeclared held-out non-generative configs, with networking blocked."""
import argparse,json,socket,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ragpilot.common import read_json,write_json
from ragpilot.pipeline import run_offline
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--data',required=True);p.add_argument('--freeze',required=True);p.add_argument('--output',required=True)
a=p.parse_args()
def blocked(*args,**kwargs):raise RuntimeError('Outbound networking disabled for frozen local evaluation')
socket.socket.connect=blocked;socket.create_connection=blocked
runs=[]
for config in read_json(a.freeze)['allowed_configs']:
    name='bm25-finance-k2' if config['retriever']=='bm25' else 'minilm-k2'
    result=run_offline(a.data,Path(a.output)/name,config,split='pilot_evaluation',freeze_path=a.freeze)
    runs.append({'name':name,'result':result});print(json.dumps(runs[-1]),flush=True)
write_json(Path(a.output)/'evaluation_workflow.json',{'runs':runs,'network_mode':'outbound_socket_connections_blocked','generative_model_calls':0})
