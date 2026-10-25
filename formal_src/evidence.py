"""Question-blind evidence expansion. All answerers use this identical result."""
from src.ragpilot.common import reject_gold
from .contracts import safe_json


def expand_once(seed_ids: list[str], units: list[dict], edges: list[dict]) -> list[str]:
    safe_json(edges)
    for unit in units:
        safe_json(unit);reject_gold(unit)
    indexed={u['evidence_id']:u for u in units}
    if len(indexed)!=len(units) or len(set(seed_ids))!=len(seed_ids) or any(s not in indexed for s in seed_ids):
        raise ValueError('Missing or duplicate evidence identity')
    if len({indexed[s]['doc_id'] for s in seed_ids})>1:
        raise ValueError('Cross-document seeds')
    selected=list(seed_ids)
    # Deliberately iterate only the original seeds, never the expanding result.
    for source in seed_ids:
        for edge in sorted(edges,key=lambda e:(e['source'],e['target'],e['kind'])):
            if edge['source']!=source:
                continue
            target=edge['target']
            if target not in indexed or indexed[target]['doc_id']!=indexed[source]['doc_id']:
                raise ValueError('Invalid cross-document association')
            if edge['kind'] not in {'footnote','continuation'}:
                raise ValueError('Unknown association kind')
            if target not in selected:
                selected.append(target)
    return selected


def build_associations(units:list[dict])->list[dict]:
    """Only explicit marker matches and clear adjacent sentence continuation."""
    import re
    from .contracts import safe_json
    for unit in units:
        safe_json(unit);reject_gold(unit)
    grouped={}
    for unit in units:grouped.setdefault(unit['doc_id'],[]).append(unit)
    edges=[]
    marker=re.compile(r'\(([0-9]{1,2}|[a-z])\)')
    for doc,items in grouped.items():
        ordered=sorted(items,key=lambda u:(u.get('region',[0,0,0,0])[1],u.get('region',[0,0,0,0])[0],u['evidence_id']))
        definitions={}
        for unit in ordered:
            for line in unit['text'].splitlines():
                found=re.match(r'^\s*\(([0-9]{1,2}|[a-z])\)\s+\S',line)
                if found:definitions.setdefault(found[1],[]).append(unit['evidence_id'])
        for unit in ordered:
            for found in reference_markers(unit['text']):
                target=definitions.get(found[1],[])
                if len(set(target))==1 and target[0]!=unit['evidence_id']:
                    edge={'source':unit['evidence_id'],'target':target[0],'kind':'footnote'}
                    if edge not in edges:edges.append(edge)
        for left,right in zip(ordered,ordered[1:]):
            a,b=left.get('region'),right.get('region')
            if a is None or b is None:continue
            last=left['text'].rstrip();first=right['text'].lstrip()
            overlap=max(0,min(a[2],b[2])-max(a[0],b[0]))
            width=min(a[2]-a[0],b[2]-b[0])
            gap=b[1]-a[3]
            if (last and first and last[-1] not in '.!?;:' and first[0].islower()
                    and width>0 and overlap/width>=.8 and 0<=gap<=max(12,(a[3]-a[1])*.5)):
                edges.append({'source':left['evidence_id'],'target':right['evidence_id'],'kind':'continuation'})
    return edges


def reference_markers(text):
    """Attached references or explicit 'note (n)'; standalone numeric cells excluded."""
    import re
    output=[]
    for match in re.finditer(r'\(([0-9]{1,2}|[a-z])\)',text):
        prefix=text[:match.start()]
        if (prefix and prefix[-1].isalpha()) or re.search(r'\b(?:footnote|note) +$',prefix,re.I):
            output.append(match)
    return output
