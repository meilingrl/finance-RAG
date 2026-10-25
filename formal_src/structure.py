"""Deterministic input-only V2 cell relations; uncertainty is retained."""
from __future__ import annotations
import re
from src.ragpilot.common import reject_gold, digest
from .contracts import safe_json

NUMBER=re.compile(r'(?:[$€£¥]\s*)?[+-]?\(?\d[\d,.]*\)?\s*%?')
YEAR=re.compile(r'(?:19|20)\d{2}')
UNIT=re.compile(r'\b(?:in\s+(?:thousands?|millions?|billions?)|percent(?:ages?)?)\b',re.I)


def describe_units(units:list[dict])->list[dict]:
    def reject_question(value):
        if isinstance(value,dict):
            if {'question','question_id','question_text'} & set(value):
                raise ValueError('Question exposure is forbidden in structural inputs')
            for item in value.values():reject_question(item)
        elif isinstance(value,list):
            for item in value:reject_question(item)
    output=[]
    if len({u['evidence_id'] for u in units})!=len(units):raise ValueError('Duplicate structural evidence')
    unit_order={u['evidence_id']:i+1 for i,u in enumerate(sorted(units,key=lambda u:u['evidence_id']))}
    for unit in units:
        safe_json(unit)
        reject_question(unit)
        reject_gold(unit)
        nodes=[];rows=[];node_blocks={}
        for block_index,block in enumerate(unit.get('blocks',[])):
            for line in block.get('lines',[]):
                cells=[]
                for cell in line.get('cells',[]):
                    text=cell['text']
                    if ' '.join(text.split()) not in ' '.join(unit['text'].split()):
                        raise ValueError('Structural cell adds text absent from shared evidence')
                    node={'node_id':f'N{unit_order[unit["evidence_id"]]}.C{len(nodes)+1}','text':text,'bbox':list(cell['bbox'])}
                    nodes.append(node);cells.append(node);node_blocks[node['node_id']]=block_index
                if cells:rows.append((block_index,cells))
        relations=[];seen=set()
        def add(kind,source,target):
            key=(kind,source['node_id'],target['node_id'])
            if key not in seen:
                seen.add(key);relations.append({'relation_id':f'R{len(relations)+1}',
                                               'kind':kind,'source':key[1],'target':key[2]})
        declarations=[n for n in nodes if UNIT.search(n['text']) and not NUMBER.fullmatch(n['text'].strip())]
        header_rows=[];last_block=None;data_seen=False
        for block_index,row in rows:
            if last_block!=block_index:
                header_rows=[];data_seen=False;last_block=block_index
            numeric=[n for n in row if NUMBER.fullmatch(n['text'].strip())]
            labels=[n for n in row if n not in numeric]
            year_header=len(numeric)>=2 and all(YEAR.fullmatch(n['text'].strip()) for n in numeric) and (not labels or all('year' in n['text'].lower() for n in labels))
            text_header=len(row)>=2 and not numeric
            if year_header or text_header:
                if data_seen:
                    header_rows=[];data_seen=False
                if year_header:
                    header_rows=[old for old in header_rows if not any(YEAR.fullmatch(n['text'].strip()) for n in old)]
                header_rows.append(row);continue
            if numeric and labels:data_seen=True
            for value in numeric:
                possible=[n for n in labels if n['bbox'][2]<=value['bbox'][0]]
                if len(possible)!=1:continue
                add('row_label',value,possible[0])
                center=(value['bbox'][0]+value['bbox'][2])/2
                previous_width=0
                for header in reversed(header_rows):
                    possible=[n for n in header if n['bbox'][0]<=center<=n['bbox'][2] and n['bbox'][3]<=value['bbox'][1]]
                    if len(possible)==1:
                        width=possible[0]['bbox'][2]-possible[0]['bbox'][0]
                        if width>previous_width:
                            add('column_header',value,possible[0]);previous_width=width
                applicable=[n for n in declarations if node_blocks[n['node_id']]==block_index and n['bbox'][3]<=value['bbox'][1]]
                if applicable:
                    nearest=max(n['bbox'][3] for n in applicable)
                    applicable=[n for n in applicable if n['bbox'][3]==nearest]
                    if len(applicable)==1:add('unit',value,applicable[0])
        output.append({'evidence_id':unit['evidence_id'],'evidence_sha':digest(unit),
                       'nodes':nodes,'relations':relations,'question_exposure':False,
                       'method_version':'cell-relations-v2','verified':False,
                       'uncertainties':[] if relations else ['No uniquely supported cell relations']})
    from .evidence import build_associations, reference_markers
    by_id={record['evidence_id']:record for record in output}
    marker=re.compile(r'\(([0-9]{1,2}|[a-z])\)')
    for edge in build_associations(units):
        if edge['kind']!='footnote':continue
        source,target=by_id[edge['source']],by_id[edge['target']]
        for node in source['nodes']:
            for match in reference_markers(node['text']):
                targets=[n for n in target['nodes'] if re.match(r'^\s*\('+re.escape(match[1])+r'\)\s+',n['text'])]
                if len(targets)==1:
                    source['relations'].append({'relation_id':f'R{len(source["relations"])+1}',
                                                'kind':'footnote','source':node['node_id'],'target':targets[0]['node_id']})
    return output


def project_structure(records:list[dict], selected_ids:list[str])->list[dict]:
    indexed={r['evidence_id']:r for r in records}
    if len(indexed)!=len(records) or any(e not in indexed for e in selected_ids):
        raise ValueError('Missing or duplicate structural evidence')
    all_nodes={n['node_id'] for record in records for n in record['nodes']}
    if len(all_nodes)!=sum(len(record['nodes']) for record in records):
        raise ValueError('Duplicate structural node IDs')
    selected_nodes={n['node_id'] for eid in selected_ids for n in indexed[eid]['nodes']}
    result=[]
    for eid in selected_ids:
        record=indexed[eid];safe_json(record);reject_gold(record)
        if any(r['source'] not in all_nodes or r['target'] not in all_nodes for r in record['relations']):
            raise ValueError('Structural relation references an unknown node')
        relations=[r for r in record['relations'] if r['source'] in selected_nodes and r['target'] in selected_nodes]
        result.append({**record,'relations':relations})
    return result
