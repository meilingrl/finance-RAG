"""Question-blind block regions and lossless spatial line serialization.

No question, answer, mapping or retrieval argument is accepted. Official blocks
remain indivisible; consecutive tabular blocks plus preceding column headings
are grouped. This is a conservative heuristic, not a table recognition model.
"""
from __future__ import annotations
import re
import statistics
from .common import reject_gold, bbox

REGION_POLICY = {
    "version": "blind-block-regions-v2",
    "official_blocks_indivisible": True,
    "table_numeric_columns_min": 2,
    "table_gap_page_width_fraction": .025,
    "table_join_page_height_fraction": .04,
    "header_lookback_page_height_fraction": .15,
    "paragraph_join_page_height_fraction": .012,
    "multicolumn_policy": "whole_page_diagnostic_if_two_narrow_columns_overlap",
    "overlapping_closed_regions": "merge_until_disjoint",
    "question_exposure": False,
    "gold_exposure": False,
    "text_serialization": "official_words_spatial_lines_tabs_v1",
}
NUMERIC = re.compile(r"^[$€£]?\(?[-+]?\d[\d,.]*\)?%?$|^[—–−-]$")

def spatial_lines(item):
    """Reorder only reconciled official words, retaining every token exactly once."""
    words = item.get("words") or {}
    texts, boxes = words.get("word_list", []), words.get("bbox_list", [])
    original = item["text"]
    bounds=bbox(item["bbox"])
    if bounds[3]-bounds[1]>2*(bounds[2]-bounds[0]) and len(texts)>1:
        return [{"text": original, "bbox": bounds, "cells": [], "word_indices": []}], "rotated_or_vertical_block_official_order"
    if not texts or len(texts) != len(boxes) or ' '.join(original.split()) != ' '.join(' '.join(texts).split()):
        return [{"text": original, "bbox": bbox(item["bbox"]), "cells": [], "word_indices": []}], "official_block_fallback"
    validated = []
    try:
        for i, (text, box) in enumerate(zip(texts, boxes)):
            if not isinstance(text, str):
                raise ValueError("Non-string word")
            validated.append({"text": text, "bbox": bbox(box), "index": i})
    except (ValueError, TypeError):
        return [{"text": original, "bbox": bbox(item["bbox"]), "cells": [], "word_indices": []}], "invalid_word_geometry_fallback"
    height = statistics.median(w["bbox"][3]-w["bbox"][1] for w in validated)
    lines = []
    for word in sorted(validated, key=lambda w: ((w["bbox"][1]+w["bbox"][3])/2, w["bbox"][0], w["index"])):
        center = (word["bbox"][1]+word["bbox"][3])/2
        eligible = [(abs(center-row["center"]), i) for i,row in enumerate(lines) if abs(center-row["center"]) <= .45*height]
        if eligible:
            row=lines[min(eligible)[1]]; row["words"].append(word)
            row["center"]=statistics.mean((w["bbox"][1]+w["bbox"][3])/2 for w in row["words"])
        else:
            lines.append({"center":center,"words":[word]})
    result=[]
    for row in sorted(lines,key=lambda r:r["center"]):
        ordered=sorted(row["words"],key=lambda w:(w["bbox"][0],w["index"]))
        widths=[(w["bbox"][2]-w["bbox"][0])/max(1,len(w["text"])) for w in ordered]
        gap=max(2.5*statistics.median(widths),.8*height)
        cells=[];current=[]
        for word in ordered:
            if current and word["bbox"][0]-current[-1]["bbox"][2] > gap:
                cells.append(current);current=[]
            current.append(word)
        if current:cells.append(current)
        def merged(ws):return [min(w['bbox'][0] for w in ws),min(w['bbox'][1] for w in ws),max(w['bbox'][2] for w in ws),max(w['bbox'][3] for w in ws)]
        result.append({"text":"\t".join(" ".join(w['text'] for w in c) for c in cells),"bbox":merged(ordered),
                       "cells":[{"text":" ".join(w['text'] for w in c),"bbox":merged(c)} for c in cells],
                       "word_indices":[w['index'] for w in ordered]})
    assert sorted(i for row in result for i in row['word_indices']) == list(range(len(texts)))
    return result,"lossless_official_words"

def _is_numeric_cell(text):
    words=text.split()
    return bool(words) and all(NUMERIC.fullmatch(word) or word in {'$','€','£','%'} for word in words)

def _is_table(block, width):
    for line in block.get('lines',[]):
        cells=line['cells']
        numeric=[c for c in cells if _is_numeric_cell(c['text'])]
        if len(numeric)>=2:
            return True
        if numeric and len(cells)>=2 and not _is_numeric_cell(cells[0]['text']) and any(b['bbox'][0]-a['bbox'][2] > REGION_POLICY['table_gap_page_width_fraction']*width for a,b in zip(cells,cells[1:])):
            return True
    # Legacy pages without word coordinates: never divide dense numeric blocks.
    text=block['text'];numbers=re.findall(r'(?<!\w)\d[\d,.]*%?',text)
    return len(numbers)>=4 and len(numbers)/max(1,len(text.split()))>=.25

def propose_regions(page):
    reject_gold(page)
    bounds=page['bbox'];width=bounds[2]-bounds[0];height=bounds[3]-bounds[1]
    blocks=sorted(page['blocks'],key=lambda b:(b['bbox'][1],b['bbox'][0],b['order']))
    midpoint=(bounds[0]+bounds[2])/2
    left=[b for b in blocks if b['bbox'][2]-b['bbox'][0]<.5*width and b['bbox'][2]<=midpoint+.04*width and len(b['text'].split())>=15]
    right=[b for b in blocks if b['bbox'][2]-b['bbox'][0]<.5*width and b['bbox'][0]>=midpoint-.02*width and len(b['text'].split())>=15]
    if len(left)>=2 and len(right)>=2:
        overlap=min(max(b['bbox'][3] for b in left),max(b['bbox'][3] for b in right))-max(min(b['bbox'][1] for b in left),min(b['bbox'][1] for b in right))
        if overlap>.1*height:
            return [{'region':bounds,'source':REGION_POLICY['version']+':whole_page_ambiguous_multicolumn','question_exposure':False}]
    table={b['block_id'] for b in blocks if _is_table(b,width)}
    groups=[];i=0
    while i<len(blocks):
        if blocks[i]['block_id'] not in table:
            i+=1;continue
        lo=hi=i
        # Combine neighboring rows, including small intervening column headings.
        j=i+1
        while j<len(blocks):
            gap=blocks[j]['bbox'][1]-max(b['bbox'][3] for b in blocks[lo:hi+1])
            short=len(blocks[j]['text'].split())<=35
            if gap > REGION_POLICY['table_join_page_height_fraction']*height:break
            if blocks[j]['block_id'] in table:
                hi=j;j+=1;continue
            # Only bridge nonnumeric header fragments when another table row follows.
            if short:
                upcoming=blocks[j+1:j+9]
                bridge=[]
                for nxt in upcoming:
                    if nxt['bbox'][1]-max(b['bbox'][3] for b in blocks[lo:hi+1])>.12*height or len(nxt['text'].split())>40:break
                    bridge.append(nxt)
                    if nxt['block_id'] in table:break
                if bridge and bridge[-1]['block_id'] in table:
                    hi=j;j+=1;continue
            break
        # Include all column heading fragments and immediately preceding caption.
        while lo>0:
            prev=blocks[lo-1]
            gap=blocks[lo]['bbox'][1]-prev['bbox'][3]
            from_start=blocks[i]['bbox'][1]-prev['bbox'][1]
            if gap>.045*height or from_start>REGION_POLICY['header_lookback_page_height_fraction']*height:break
            if len(prev['text'].split())>40:break
            if groups and lo-1<=groups[-1][1]:break
            lo-=1
        if groups and lo<=groups[-1][1]:groups[-1]=(groups[-1][0],hi,'table_cluster')
        else:groups.append((lo,hi,'table_cluster'))
        i=hi+1
    occupied={j for lo,hi,_ in groups for j in range(lo,hi+1)}
    i=0
    while i<len(blocks):
        if i in occupied:i+=1;continue
        lo=hi=i
        # Keep headings with following prose; join nearby fragments, never slice a block.
        while hi+1<len(blocks) and hi+1 not in occupied:
            a,b=blocks[hi],blocks[hi+1]
            gap=b['bbox'][1]-a['bbox'][3]
            short=len(a['text'].split())<=14
            continuation=gap<=.004*height
            if (short and gap<=.03*height) or continuation:hi+=1
            else:break
        groups.append((lo,hi,'paragraph_blocks'));i=hi+1
    proposals=[]
    for lo,hi,kind in sorted(groups):
        group=blocks[lo:hi+1]
        region=[min(b['bbox'][0] for b in group),min(b['bbox'][1] for b in group),max(b['bbox'][2] for b in group),max(b['bbox'][3] for b in group)]
        proposals.append({'region':region,'source':REGION_POLICY['version']+':'+kind,'question_exposure':False})
    return proposals
