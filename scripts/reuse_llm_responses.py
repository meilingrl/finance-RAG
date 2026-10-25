"""Seed a paired run with unchanged responses only for byte-equivalent requests.

This performs no inference and opens no gold. Different retrieval metadata is
retained for the destination; usage is not counted again for reused responses.
"""
import argparse
from pathlib import Path
from llm_execution import load_job,append_durable
from ragpilot.common import digest,file_sha,now,read_json,read_jsonl,write_json


def seed_identical_responses(source_job,destination_job):
    source,destination=Path(source_job).resolve(),Path(destination_job).resolve()
    if (destination/'predictions.jsonl').exists() or (destination/'reuse_manifest.json').exists():
        raise ValueError('Destination already contains responses; do not overwrite or seed twice')
    old,old_requests=load_job(source);new,new_requests=load_job(destination)
    run=read_json(source/'run.json')
    if run['status'] not in ('completed','completed_with_errors') or run['pending']:
        raise ValueError('Only a completed source job may supply reusable responses')
    predictions=read_jsonl(source/'predictions.jsonl')
    if digest(predictions)!=run['prediction_hash'] or run['job_hash']!=old['job_hash']:
        raise ValueError('Source predictions changed')
    source_requests={r['question_id']:r for r in old_requests}
    by_question={r['question_id']:r for r in predictions}
    for record in predictions:
        if record['record_hash']!=digest({k:v for k,v in record.items() if k!='record_hash'}):
            raise ValueError('Source prediction record changed')
    reused=[]
    for request in new_requests:
        previous=by_question.get(request['question_id']);original=source_requests.get(request['question_id'])
        if (not previous or not original or previous['status'] not in ('answered','abstained')
                or old['config']['endpoint']!=new['config']['endpoint']
                or original['payload']!=request['payload']
                or previous['request_hash']!=request['request_hash']):
            continue
        record={**previous,**{k:v for k,v in request.items() if k not in ('payload','input_token_byte_bound')}}
        record.update(usage=None,reused_source_usage=previous.get('usage'),elapsed_seconds=0,
                      cost_estimate={'amount':0,'currency':None,'basis':'identical_request_response_reused_no_new_call'},
                      reuse={'source_job':str(source),'source_job_hash':old['job_hash'],
                             'source_record_hash':previous['record_hash'],'request_payload_equal':True})
        record.pop('record_hash',None);record['record_hash']=digest(record)
        append_durable(destination/'predictions.jsonl',record)
        reused.append(request['question_id'])
    manifest={'created_at':now(),'source_job':str(source),'source_prediction_hash':run['prediction_hash'],
              'destination_job_hash':new['job_hash'],'helper_sha256':file_sha(__file__),
              'reused':len(reused),'question_ids':reused,'api_calls':0,'gold_opened':False}
    write_json(destination/'reuse_manifest.json',manifest)
    return manifest


if __name__=='__main__':
    import json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-job',required=True);parser.add_argument('--destination-job',required=True)
    args=parser.parse_args()
    print(json.dumps(seed_identical_responses(args.source_job,args.destination_job),indent=2))
