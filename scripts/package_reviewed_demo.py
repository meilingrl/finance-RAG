"""Package explicit, hash-bound development reviews; never generate approval.

Usage: python scripts/package_reviewed_demo.py --review FILE --output DIRECTORY
The review file must be produced by actual input-only review, not by this tool.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from formal_src.provider import digest
from formal_src.runner import input_projection, reviewed_assets, condition_content


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package(root, output, decision):
    if not decision.get('reviews') or not decision.get('structures'):
        raise ValueError('Explicit review records and reviewed structures required')
    root=Path(root).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('Output must not exist; preserve prior packages')
    source=root/'data/processed/nonllm-pilot-v5'
    units=[json.loads(l) for l in (source/'evidence_units.jsonl').read_text().splitlines()]
    reviews={r['evidence_id']:r for r in decision['reviews']}
    structures={r['evidence_id']:r for r in decision['structures']}
    if len(reviews)!=len(decision['reviews']) or len(structures)!=len(decision['structures']):raise ValueError('Duplicate review records')
    selected=[u for u in units if u['evidence_id'] in reviews]
    if {u['evidence_id'] for u in selected}!=set(reviews) or set(reviews)!=set(structures):raise ValueError('Review coverage mismatch')
    docs={u['doc_id'] for u in selected}
    if any(u['doc_id'] in docs and u['evidence_id'] not in reviews for u in units):raise ValueError('Review every unit of each development document')
    for u in selected:
        r=reviews[u['evidence_id']];s=structures[u['evidence_id']]
        if (r.get('unit_sha')!=digest(u) or s.get('evidence_sha')!=digest(u) or r.get('structure_sha')!=digest(s)
            or r.get('verified') is not True or r.get('scope_matches') is not True or r.get('qa_exposed') is not False
            or not r.get('reviewer') or not r.get('reviewed_at')):raise ValueError('Invalid or stale review')
        image=(root/u['image_path']).resolve()
        if not image.is_relative_to(source) or sha(image)!=r.get('image_sha'):raise ValueError('Reviewed image changed')
    # Questions are copied only after reviews have been provided and validated.
    questions=[json.loads(l) for l in (source/'inference_questions.jsonl').read_text().splitlines()]
    development={q['doc_id'] for q in questions if q.get('split')=='development'}
    if not docs<=development:raise ValueError('Only development documents may be packaged')
    questions=[q for q in questions if q.get('split')=='development' and q['doc_id'] in docs]
    output.mkdir(parents=True)
    resources=[]
    def bind(path,kind):resources.append(dict(kind=kind,path=str(path.relative_to(output)),sha256=sha(path)))
    def write(name,value,kind):
        path=output/name
        path.write_text((''.join(json.dumps(v,ensure_ascii=False)+'\n' for v in value) if name.endswith('.jsonl') else json.dumps(value,ensure_ascii=False,indent=2)+'\n'))
        bind(path,kind)
    final_units=[];final_reviews=[];final_structures=[]
    for u in selected:
        target=output/'images'/Path(u['image_path']).name;target.parent.mkdir(exist_ok=True)
        shutil.copyfile(root/u['image_path'],target);bind(target,'input')
        new={**u,'image_path':str(target.relative_to(output))};final_units.append(new)
        s={**structures[u['evidence_id']],'evidence_sha':digest(new)};final_structures.append(s)
        final_reviews.append({**reviews[u['evidence_id']],'source_unit_sha':digest(u),'unit_sha':digest(new),'image_path':new['image_path'],'structure_sha':digest(s)})
    write('inference_questions.jsonl',questions,'input')
    write('evidence_units.jsonl',final_units,'input')
    write('approved_structures.jsonl',final_structures,'approved_description')
    write('scope_reviews.json',{'reviews':final_reviews},'approved_description')
    # Copy the existing pinned encoder; no network download and no mutable symlink.
    model_root=root/'data/formal-v1'
    model_manifest=json.loads((model_root/'resources.json').read_text())
    for item in model_manifest['resources']:
        if item['kind']!='model':continue
        src=(model_root/item['path']).resolve()
        if not src.is_relative_to(model_root) or sha(src)!=item['sha256']:raise ValueError('Pinned model changed')
        dst=output/item['path'];dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst);bind(dst,'model')
    projection={'root':str(output),'resources':resources}
    data,bindings=input_projection(projection)
    for doc in docs:
        evidence=[u for u in final_units if u['doc_id']==doc]
        for condition in ('E3','E4'):
            assets=reviewed_assets(evidence,final_units,data,bindings,condition)
            condition_content(condition,'Input-only packaging check',evidence,assets)
    manifest=dict(schema_version=1,resource_set_id='development-reviewed-v1',purpose='demo',qualification={},resources=resources)
    write('review-source.json',decision,'review')
    approvals={'reviews':[dict(asset_sha256=r['sha256'],reviewer=decision['reviewer'],reviewer_kind=decision['reviewer_kind'],reviewed_at=decision['reviewed_at'],approved=True,qa_exposed=False) for r in resources if r['kind']=='approved_description']}
    write('review-import.json',approvals,'review')
    manifest['review_import']='review-import.json'
    (output/'resources.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--review',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();package(ROOT,args.output,json.loads(args.review.read_text()))
