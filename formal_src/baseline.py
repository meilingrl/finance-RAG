"""Text-only formal E1 plus the frozen legacy comparator, with explicit ambiguity."""
import re
from src.ragpilot.rules import extract as legacy_extract, candidates, terms, value_scale


def extract(question:str, selected_texts:list[tuple[str,str]], *, min_score:float=.5,
            margin:float=.15, legacy:bool=False)->dict:
    if legacy:
        best,traces,reason=legacy_extract(question,selected_texts)
        return {'status':'answered' if best else 'abstained','answer':best['value'] if best else None,
                'scale':best['scale'] if best else '', 'citations':sorted({r['evidence_id'] for r in traces}) if best else [],
                'method_version':'legacy-e1','reason':reason}
    if not 0<=min_score<=2 or not 0<=margin<=2:raise ValueError('Invalid baseline threshold')
    found=[]
    for eid,text in selected_texts:
        declarations={x.lower().rstrip('s') for x in re.findall(r'\bin\s+(thousands?|millions?|billions?)\b',text,re.I)}
        for value,scale,score,rule,source in candidates(question,text):
            if not scale and len(declarations)==1:
                scale=next(iter(declarations))
            if score>=min_score:
                found.append({'answer':value,'scale':scale,'score':score,'rule':rule,'source':source,'evidence_id':eid})
    found.sort(key=lambda r:(-r['score'],r['answer'],r['evidence_id']))
    if not found:return {'status':'abstained','answer':None,'scale':'','citations':[],'method_version':'formal-e1-v1','reason':'no_confident_text_rule'}
    best=found[0]
    if any((r['answer'],r['scale'])!=(best['answer'],best['scale']) and best['score']-r['score']<=margin for r in found):
        return {'status':'abstained','answer':None,'scale':'','citations':[],'method_version':'formal-e1-v1','reason':'conflicting_text_rules'}
    return {'status':'answered','answer':best['answer'],'scale':best['scale'],
            'citations':sorted({r['evidence_id'] for r in found if (r['answer'],r['scale'])==(best['answer'],best['scale'])}),
            'method_version':'formal-e1-v1','reason':best['rule']}
