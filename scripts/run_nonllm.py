#!/usr/bin/env python3
"""Reproduce the bounded non-generative runs; no downloads or LLM/API calls.

Input preparation and independent mapping review are separate. This command
runs three BM25 development checks and MiniLM k=1/2, freezes source and allowed
k=2 configurations using development evidence coverage, then runs both held-out
retrievers exactly once. Existing output paths are never overwritten.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ragpilot.common import write_json
from ragpilot.pipeline import DEFAULT_CONFIG,run_offline,freeze_nonllm

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',required=True);p.add_argument('--output',required=True);p.add_argument('--model-path',required=True)
    p.add_argument('--development-only',action='store_true',help='Stop after development runs, before freeze or evaluation')
    p.add_argument('--freeze-and-evaluate',action='store_true',help='Use existing completed development runs')
    args=p.parse_args();out=Path(args.output)
    import socket
    def blocked(*args,**kwargs):
        raise RuntimeError('Outbound networking disabled for non-generative local workflow')
    socket.socket.connect=blocked
    socket.create_connection=blocked
    if args.development_only and args.freeze_and_evaluate:p.error('Choose one stage')
    configs={'bm25-finance-k2':dict(DEFAULT_CONFIG),'bm25-finance-k1':{**DEFAULT_CONFIG,'k':1},
             'bm25-simple-k2':{**DEFAULT_CONFIG,'tokenizer':'simple'},
             'minilm-k2':{'retriever':'sentence_transformers','k':2,'model_path':args.model_path},
             'minilm-k1':{'retriever':'sentence_transformers','k':1,'model_path':args.model_path}}
    dev=[out/'development'/name for name in configs]
    if not args.freeze_and_evaluate:
        for (name,config),path in zip(configs.items(),dev):
            print(json.dumps({'stage':'development','name':name,'result':run_offline(args.data,path,config,split='development')},ensure_ascii=False),flush=True)
    if args.development_only:return
    allowed=[configs['bm25-finance-k2'],configs['minilm-k2']]
    freeze=freeze_nonllm(args.data,dev,allowed,out/'pre-evaluation-freeze.json')
    print(json.dumps({'stage':'frozen','time':freeze['frozen_at'],'default_extension_config':freeze['default_extension_config']}),flush=True)
    for name in ('bm25-finance-k2','minilm-k2'):
        run=run_offline(args.data,out/'evaluation'/name,configs[name],split='pilot_evaluation',freeze_path=out/'pre-evaluation-freeze.json')
        print(json.dumps({'stage':'evaluation','name':name,'result':run}),flush=True)
    write_json(out/'workflow.json',{'status':'completed_non_generative_only','development_runs':[str(p) for p in dev],
              'evaluation_runs':[str(out/'evaluation'/name) for name in ('bm25-finance-k2','minilm-k2')],
              'freeze_path':str(out/'pre-evaluation-freeze.json'),'generative_model_calls':0,'network_mode':'outbound_socket_connections_blocked'})
if __name__=='__main__':main()
