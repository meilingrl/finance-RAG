"""Hash-only result-stage provenance. No evaluation assets or provider imports."""
import hashlib,json
from pathlib import Path


def write_result_proof(run_dir, *, source_run_id=None, source_spec=None):
    rd=Path(run_dir)
    def read(name):return json.loads((rd/name).read_text())
    def sha(name):return hashlib.sha256((rd/name).read_bytes()).hexdigest()
    def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    spec=read('spec.json');seal=read('sealed.json');summary=read('summary.json')
    proof={'schema_version':1,'source_run_id':source_run_id or rd.name,
           'source_spec_sha256':digest(source_spec or spec),'request_spec_sha256':digest(spec),
           'prediction_sha256':seal['prediction_sha256'],'seal_sha256':sha('sealed.json'),
           'evaluation_sha':summary['evaluation_sha'],
           'files':{name:sha(name) for name in ('summary.json','results.json','results.csv','retrieval.jsonl','scored-answers.jsonl','audit-status.json') if (rd/name).is_file()}}
    temporary=rd/'result-proof.json.proof.tmp'
    temporary.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    temporary.replace(rd/'result-proof.json')
    return proof
