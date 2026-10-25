"""Finite, text-only extractive rules. No model, gold, coordinates or identifiers.

The rules consume exactly the shared evidence text and question. Ambiguous
matches abstain. All outputs are copied from supplied text; no arithmetic.
"""
from __future__ import annotations
import re

RULES = {
    'version':'e1-text-rules-v2',
    'development_validation':'calibrated_on_4_official_train_documents_12_questions_only',
    'strategies':['unique_delimited_value','year_header_table_row','single_year_prose','respectively_year_value','named_column_table_row','duration','note_heading','lexical_sentence_span'],
    'label_min_overlap':2,
    'label_min_query_coverage':.5,
    'unique_best_margin':.15,
    'question_specific_exceptions':False,
    'table_extra_columns':'only leading year columns with explicit increase/decrease or vs header',
    'uses_coordinates':False,
}
STOP=set('what which was were is are the a an of in for to and at as by how much many did does do company total amount value with on from during per its it their this that ended ending year years fiscal december january february march april may june july august september october november percentage percent'.split())
ALIASES={'revenues':'revenue','receivables':'receivable','accounts':'account','expenses':'expense','costs':'cost','earnings':'earning','losses':'loss','shares':'share','dividends':'dividend','paid':'pay','payments':'payment','represents':'represent','recognized':'recognize','recognizes':'recognize','determines':'determine','expensed':'expense','investments':'investment','equivalents':'equivalent','maturities':'maturity','services':'service','balances':'balance','obligations':'obligation','adjustments':'adjustment','stockholders':'stockholder'}
NUM=re.compile(r"(?<![\w.])(?:[$€£]\s*)?[-+]?\(?\d+(?:,\d{3})*(?:\.\d+)?\)?(?:\s*%|\s+(?:thousand|million|billion|percent))?(?!\w|[.]\d)",re.I)
YEAR=re.compile(r'\b(?:19|20)\d{2}\b')
SCALE=re.compile(r'\b(thousands?|millions?|billions?|percent)\b|%',re.I)
MONTHS=r'January|February|March|April|May|June|July|August|September|October|November|December'
DATE=re.compile(r'\b(?:'+MONTHS+r')\s+\d{1,2},?\s+(?:19|20)\d{2}\b',re.I)

def terms(text):
    return {ALIASES.get(t,t) for t in re.findall(r'[a-z]+',text.lower()) if t not in STOP}

def lexical_score(question_terms,label):
    label_terms=terms(label);overlap=question_terms & label_terms
    if not question_terms or not overlap:return 0
    if len(overlap)<min(RULES['label_min_overlap'],len(question_terms)):return 0
    coverage=len(overlap)/len(question_terms)
    if coverage<RULES['label_min_query_coverage']:return 0
    return coverage+.2*len(overlap)/max(1,len(label_terms))

def value_scale(value,context):
    found=SCALE.search(value)
    if found:return 'percent' if found.group()=='%' else found.group().lower().rstrip('s')
    # Table/prose scale is inherited only from one explicit unit declaration.
    declarations=[]
    for text in re.findall(r'\([^)]{0,120}\)',context):
        match=re.search(r'\bin\s+(thousands?|millions?|billions?)\b',text,re.I)
        if match:declarations.append(match.group(1).lower().rstrip('s'))
    return declarations[0] if len(set(declarations))==1 else ''

def numbers(text,exclude_years=True):
    vals=[]
    for m in NUM.finditer(text):
        value=m.group().strip()
        if exclude_years and YEAR.fullmatch(value):continue
        vals.append((value,m.start(),m.end()))
    return vals

def candidates(question,text):
    qterms=terms(question);years=YEAR.findall(question)
    if len(set(years))>1:return []
    year=years[0] if years else None
    found=[]
    rows=text.splitlines();header_years=[];column_headers=[];prefix=[];active_years=[]
    for row in rows:
        cells=[c.strip() for c in row.split('\t') if c.strip()]
        row_years=YEAR.findall(row)
        if len(cells)<2 and row_years and not re.search(r'\bvs\.?',row,re.I):
            active_years=row_years
            header_years=[]
            column_headers=[]
        # Headers can contain dates, but never a financial metric label.
        if len(row_years)>=2 and len(cells)>=2 and all(len(terms(c))<=1 for c in cells):
            header_years=row_years;active_years=row_years
            continue
        if len(cells)>=2 and all(not numbers(c,exclude_years=False) and len(c.split())<=6 for c in cells):
            column_headers=cells
        if len(cells)<2:
            prefix.append(row);continue
        label=cells[0];score=lexical_score(qterms,label)
        vals=[];pending_currency=''
        for cell in cells[1:]:
            clean=cell.strip()
            if clean in {'$','€','£'}:pending_currency=clean;continue
            trailing_currency=clean[-1] if clean and clean[-1] in '$€£' else ''
            if trailing_currency:clean=clean[:-1].strip()
            matches=numbers(clean)
            if len(matches)==1 and not terms(clean):
                value=matches[0][0]
                if pending_currency and not any(c in value for c in '$€£'):value=pending_currency+value
                vals.append(re.sub(r'([$€£])\s+',r'\1',value));pending_currency=trailing_currency
            elif clean in {'—','–','-','―','n/a'}:vals.append(None);pending_currency=''
            else:vals=[];break
        if not vals:
            prefix.append(row);continue
        change_cols=bool(re.search(r'increase|decrease|\bvs\.', ' '.join(prefix),re.I))
        same_year_shape=len(vals)==len(header_years) or (change_cols and len(vals)>len(header_years))
        if score and year and header_years and len(set(header_years))==len(header_years) and year in header_years and same_year_shape:
            value=vals[header_years.index(year)]
            if value is not None:found.append((value,value_scale(value,text),score,'year_header_table_row',row))
        elif score and len(vals)==1 and (not year or row_years==[year]):
            found.append((vals[0],value_scale(vals[0],text),score,'unique_delimited_value',row))
        if column_headers and len(column_headers)==len(vals) and (not year or active_years==[year]):
            col_scores=[len(qterms & terms(c)) for c in column_headers]
            best=max(col_scores)
            if best and col_scores.count(best)==1:
                context_terms=terms(' '.join(prefix))
                row_terms=qterms-context_terms-set().union(*(terms(c) for c in column_headers))
                row_score=lexical_score(row_terms,label)
                idx=col_scores.index(best)
                if row_score and vals[idx] is not None:
                    found.append((vals[idx],value_scale(vals[idx],text),row_score,'named_column_table_row',row))
    # Colon / equals fixture and real label-value forms.
    for row in rows:
        if not re.search(r'[:=]',row):continue
        label,remainder=re.split(r'[:=]',row,maxsplit=1)
        score=lexical_score(qterms,label)
        vals=numbers(remainder)
        if score and len(vals)==1 and (not year or YEAR.findall(row)==[year]):
            value=vals[0][0];found.append((value,value_scale(value,row),score,'unique_delimited_value',row))
    # Prose uses complete sentences; table lines are deliberately excluded.
    prose=' '.join(row for row in rows if '\t' not in row)
    for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z(])',prose):
        score=lexical_score(qterms,sentence)
        if not score:continue
        sy=YEAR.findall(sentence);vals=numbers(sentence)
        # Dates are metadata, not monetary values.
        spans=[m.span() for m in DATE.finditer(sentence)]
        vals=[v for v in vals if not any(a<=v[1]<b for a,b in spans)]
        if year and 'respectively' in sentence.lower() and year in sy and len(sy)==len(vals) and len(sy)==len(set(sy)):
            value=vals[sy.index(year)][0];found.append((value,value_scale(value,sentence),score,'respectively_year_value',sentence))
        elif len(vals)==1 and (not year or set(sy)=={year}):
            value=vals[0][0];found.append((value,value_scale(value,sentence),score,'single_year_prose',sentence))
    # Unit-directed duration and note identifiers are simple textual spans.
    if re.search(r'how many years|how long',question,re.I):
        for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z(])',prose):
            score=lexical_score(qterms,sentence)
            matches=list(re.finditer(r'(?<![\w.])\d+(?:\.\d+)?(?=\s+years?\b)',sentence,re.I))
            if score and len(matches)==1:found.append((matches[0].group(),'',score+.2,'duration',sentence))
    if re.search(r'which note|what note',question,re.I):
        for row in rows:
            match=re.search(r'\bNOTE\s+[A-Z0-9]+\b',row,re.I)
            score=lexical_score(qterms-{'note','information','belong'},row)
            if match and score:found.append((match.group(),'',score,'note_heading',row))
    if re.match(r'(?i)(?:what does|how does|how did|when (?:is|are|was|were))\b',question):
        for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z(])',prose):
            score=lexical_score(qterms,sentence)
            if score<.8:continue
            spans=[sentence.strip()]
            # A parenthetical clause may carry a self-contained definition.
            spans.extend(re.findall(r'\(([^()]{20,})\)',sentence))
            best_span=max(spans,key=lambda span:lexical_score(qterms,span))
            if not year or year in YEAR.findall(best_span):
                found.append((best_span,'',lexical_score(qterms,best_span),'lexical_sentence_span',best_span))
    return found

def extract(question,selected_texts):
    all_candidates=[]
    for evidence_id,text in selected_texts:
        for value,scale,score,rule,source in candidates(question,text):
            if value is not None:all_candidates.append({'value':value,'scale':scale,'score':score,'rule':rule,'source_span':source,'evidence_id':evidence_id})
    ranked=sorted(all_candidates,key=lambda c:(-c['score'],c['value'],c['evidence_id']))
    if not ranked:return None,[], 'no_rule_match'
    best=ranked[0]
    conflicts=[c for c in ranked if (c['value'],c['scale'])!=(best['value'],best['scale']) and best['score']-c['score']<RULES['unique_best_margin']]
    if conflicts:return None,ranked,'ambiguous_rule_matches'
    matches=[c for c in ranked if (c['value'],c['scale'])==(best['value'],best['scale'])]
    return best,matches,'unique_best_rule_match'
