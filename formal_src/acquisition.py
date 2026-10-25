"""Curator-only fixed-seed sampling; never imported by inference workers."""
from collections import Counter
import random
import re
from .data_protocol import source_key
SEED=20261023

def select_questions(records, sources, *, target, role, existing=(), predicate=None):
    grouped={s:[] for s in sources}
    for record in records:
        d=record['doc'];s=source_key(d['source'])
        if s not in grouped:continue
        for q in record['questions']:
            if q.get('answer_type')=='span' and q.get('req_comparison') is False and q.get('question','').strip():
                if predicate is None or predicate(q,d):
                    grouped[s].append({'question_id':str(q['uid']),'question':q['question'],'doc_id':d['uid'],'source_id':s,'source_report':d['source'],'page':int(d.get('page',1)),'split':role,'answer_type':'span'})
    rng=random.Random(SEED)
    for s in sources:
        grouped[s].sort(key=lambda x:x['question_id']);rng.shuffle(grouped[s])
    used={x['question_id'] for x in existing};pages=Counter(x['doc_id'] for x in existing);counts=Counter(x['source_id'] for x in existing);selected=[]
    # Cover each source once before filling from the fixed shuffled source order.
    while len(selected)<target:
        progress=False
        for s in sources:
            while grouped[s]:
                row=grouped[s].pop()
                if row['question_id'] in used or pages[row['doc_id']]>=3 or counts[s]>=20:continue
                selected.append(row);used.add(row['question_id']);pages[row['doc_id']]+=1;counts[s]+=1;progress=True;break
            if len(selected)>=target:break
        if not progress:break
    return selected

def diagnostic_category(question, units):
    """Conservative candidates only; original structural dependence needs human review."""
    text=question['question'].casefold()
    if re.search(r'\b(?:footnote|endnote|annotation)\b|\bnote\s*\([a-z0-9]+\)',text):return 'footnote'
    return None

def evaluation_records(records,selected):
    wanted={r['question_id']:r for r in selected};out=[]
    for record in records:
        for q in record['questions']:
            if str(q['uid']) not in wanted:continue
            row=wanted[str(q['uid'])]
            out.append({**q,'question_id':str(q['uid']),'doc_id':row['doc_id'],'split':row['split'],'mapping_verified':False,'support_complete':False})
    return out

def audit_identity(rows):
    """Mechanical duplicates do not establish human company-alias identity."""
    groups={}
    for row in rows:
        for field in ('pdf_sha256','ocr_sha256'):
            if row.get(field):groups.setdefault((field,row[field]),[]).append(row)
    duplicates=[]
    for group in groups.values():
        if len({('test' if r['split'] in {'main','diagnostic'} else r['split']) for r in group})>1:
            ids=sorted({r['doc_id'] for r in group})
            if ids not in duplicates:duplicates.append(ids)
    return {'cross_role_duplicate_assets':duplicates,'human_alias_audit_complete':False,'source_key_rule':'lowercase_report_stem_strip_terminal_year','pdf_ocr_sha_checked':True}


def diagnostic_queue(records, sources, main, *, documents):
    """Uncertified question-only reviewer queue, bounded jointly with main QA.

    Hints are lexical metadata, never category certification. No structure generation.
    """
    rows=select_questions(records,sources,target=3_000,role='diagnostic',existing=main)
    result=[]
    for row in rows:
        doc=documents.get(row['doc_id'],{})
        text=row['question'].casefold()
        hints=[]
        if re.search(r'\b(?:note|footnote|annotation|explain|description)\b',text):hints.append('possible_annotation_dependency')
        if re.search(r'\b(?:year|period|category|segment|column|header|heading)\b',text):hints.append('possible_header_dependency')
        result.append({**row,'provisional_hints':hints,'certified_category':None,
                       'qualification_status':'human_qualification_pending',
                       'reviewer_qa_exposed':True,'eligible_for_description_blind_review':False,
                       'available_input_bindings':{k:doc[k] for k in ('pdf_path','ocr_path') if k in doc},
                       'input_assets_available':bool(doc.get('pdf_path') and doc.get('ocr_path'))})
    return result
