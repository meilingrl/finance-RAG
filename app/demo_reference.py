"""Post-seal development reference display. Never imported by inference workers."""
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

from formal_src.verification import check_seal


def _json(path):
    return json.loads(path.read_text())


def _checked_rows(path, expected):
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise ValueError('reference_source_changed')
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _text(value):
    return ' '.join(str(value).split()).casefold()


def _number(value):
    # A hint only: do not infer units, currency, percentages, or semantic correctness.
    tokens=re.findall(r'(?<![\w.])[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?![\w.])',str(value))
    if len(tokens)!=1:return None
    try:return Decimal(tokens[0].replace(',',''))
    except InvalidOperation:return None


def reference_for_run(project_root, run_dir):
    rd=Path(run_dir)
    _,_,predictions=check_seal(rd)  # Must precede even opening the reference directory.
    spec=_json(rd/'spec.json')
    absent={'available':False,'reason':'当前运行没有可匹配的开发集标准答案；自定义问题不套用原题答案。'}
    if spec['kind']!='demo':return absent
    directory=Path(project_root)/'data/phase2/development'
    if not (directory/'gold_manifest.json').is_file():return absent
    manifest=_json(directory/'gold_manifest.json')['development']
    metadata=_json(directory/'semantic_mapping_review.json')
    questions=_checked_rows(directory/'questions.jsonl',metadata['questions_sha256'])
    demo=spec['demo_input']
    matches=[q for q in questions if q.get('split')=='development' and q['doc_id']==demo['doc_id'] and q['question'].strip()==demo['question'].strip()]
    if len(matches)!=1:return absent
    question=matches[0]
    if not predictions or any(p['question_id']!=question['question_id'] for p in predictions):return absent
    if manifest['sha256']!=metadata['original_gold_sha256']:raise ValueError('reference_source_changed')
    original=_checked_rows(directory/'gold.jsonl',manifest['sha256'])
    rows=[r for r in original if r.get('split')=='development' and r['question_id']==question['question_id'] and r['doc_id']==question['doc_id']]
    if len(rows)!=1:return absent
    gold=rows[0]
    reviewed=_checked_rows(directory/'gold_semantic_verified.jsonl',metadata['verified_gold_sha256'])
    reviews=[r for r in reviewed if r['question_id']==question['question_id'] and r['doc_id']==question['doc_id']]
    review=reviews[0] if len(reviews)==1 else {}
    conflict=review.get('annotation_conflict',False)
    parts=gold['answer'] if isinstance(gold['answer'],list) else [gold['answer']]
    target='; '.join(str(v) for v in parts)
    comparisons=[]
    for row in predictions:
        text_match=_text(row.get('answer',''))==_text(target)
        scale_match=_text(row.get('scale',''))==_text(gold.get('scale',''))
        expected=_number(parts[0]) if len(parts)==1 else None
        numeric_match=expected is not None and expected==_number(row.get('answer',''))
        comparison=('annotation_conflict' if conflict else 'not_answered' if row['status']!='answered'
                    else 'literal_match' if text_match and scale_match else 'numeric_only' if numeric_match else 'different')
        comparisons.append(dict(task_id=row['task_id'],condition=row['condition'],answer=row.get('answer'),scale=row.get('scale',''),status=row['status'],text_match=text_match,scale_match=scale_match,numeric_match=numeric_match,comparison=comparison))
    return dict(available=True,question_id=question['question_id'],question=question['question'],answer=parts,
                scale=gold.get('scale',''),source='TAT-DQA 开发材料原始标注',source_sha256=manifest['sha256'],
                annotation_conflict=conflict,review_available=bool(review),
                review_notes=review.get('semantic_review',{}).get('notes',''),
                review_source=review.get('semantic_review',{}).get('reviewer',''),
                comparisons=comparisons,notice='仅作开发题对照，不是正式评分。文字差异不等于答错；数值提示不判断单位换算或语义正确性。')
