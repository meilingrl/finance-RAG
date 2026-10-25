"""Input-only evidence construction. Gold never determines region boundaries."""
from __future__ import annotations
from pathlib import Path
from .regions import spatial_lines, propose_regions
from .common import bbox, contains, digest, file_sha, intersects, reject_gold, safe_identifier, portable_path

def normalize_page(raw):
    # Official files contain a pages array. A single-page dict is accepted for fixtures.
    pages = raw.get("pages", [raw] if "blocks" in raw else [])
    if len(pages) != 1:
        raise ValueError("Only exactly one converted page is eligible")
    page = pages[0]
    bounds = bbox(page["bbox"])
    blocks = []
    seen = set()
    for position, item in enumerate(page.get("blocks", [])):
        key = str(item.get("uuid", item.get("id", position)))
        if key in seen:
            raise ValueError("Duplicate text block identity")
        seen.add(key)
        box = bbox(item["bbox"])
        if not contains(bounds, box):
            raise ValueError("Text block extends outside page bounds")
        text = item.get("text")
        if not isinstance(text, str):
            raise ValueError("Official text block lacks text string")
        lines, serialization = spatial_lines(item)
        blocks.append({"block_id": key, "bbox": box, "text": text, "layout_text": "\n".join(line["text"] for line in lines), "lines": lines, "serialization_status": serialization, "order": item.get("order", position)})
    blocks.sort(key=lambda b: (b["order"], b["bbox"][1], b["bbox"][0], b["block_id"]))
    if not blocks:
        raise ValueError("No converted text blocks")
    return {"bbox": bounds, "blocks": blocks}

def close_region(region, blocks):
    """Expand to whole intersecting blocks, repeatedly; include all added text."""
    region = bbox(region)
    for _ in range(len(blocks) + 1):
        included = [b for b in blocks if intersects(region, b["bbox"])]
        if not included:
            raise ValueError("Region contains no text blocks")
        expanded = [min([region[0]] + [b["bbox"][0] for b in included]),
                    min([region[1]] + [b["bbox"][1] for b in included]),
                    max([region[2]] + [b["bbox"][2] for b in included]),
                    max([region[3]] + [b["bbox"][3] for b in included])]
        if expanded == region:
            return region, included
        region = expanded
    raise ValueError("Region closure did not converge")

def build_units(doc, raw_page, image_path=None, regions=None, segmentation="whole_page"):
    """Whole-page fallback by default; optional coordinates must be input-only reviewed.

    This function receives no QA record and cannot use answers or mappings. Region
    proposals must include provenance declaring that question/gold was not used.
    No algorithmic table subdivision is invented by the prototype.
    """
    reject_gold(doc)
    page = normalize_page(raw_page)
    if segmentation not in {"whole_page", "blind_blocks"}:
        raise ValueError("Unknown segmentation")
    if segmentation == "blind_blocks" and regions is None:
        regions = propose_regions(page)
    proposals = [{"region": page["bbox"], "source": "whole_page_fallback", "question_exposure": False}] if regions is None else regions
    for proposal in proposals:
        reject_gold(proposal)
        if proposal.get("question_exposure") is not False or not proposal.get("source"):
            raise ValueError("Region proposals need input-only provenance and question_exposure=false")
        if not contains(page["bbox"], bbox(proposal["region"])):
            raise ValueError("Region is outside page bounds")
    if segmentation == "blind_blocks":
        # OCR blocks may overlap or contain another block. Merge closed proposals
        # until no block is repeated across candidates; do not inflate candidate count.
        closed=[]
        for p in proposals:
            region, included=close_region(p["region"],page["blocks"])
            closed.append({**p,"region":region,"refs":{b["block_id"] for b in included}})
        changed=True
        while changed:
            changed=False
            for i in range(len(closed)):
                for j in range(i+1,len(closed)):
                    if closed[i]["refs"] & closed[j]["refs"]:
                        a,b=closed[i]["region"],closed[j]["region"]
                        region,included=close_region([min(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),max(a[3],b[3])],page["blocks"])
                        closed[i]={"region":region,"refs":{b["block_id"] for b in included},"source":"blind-block-regions-v2:overlap_merged","question_exposure":False}
                        del closed[j];changed=True;break
                if changed:break
        proposals=[{k:v for k,v in p.items() if k!="refs"} for p in closed]
    result = []
    for index, proposal in enumerate(proposals):
        reject_gold(proposal)
        if proposal.get("question_exposure") is not False or not proposal.get("source"):
            raise ValueError("Region proposals need input-only provenance and question_exposure=false")
        proposed = bbox(proposal["region"])
        if not contains(page["bbox"], proposed):
            raise ValueError("Region is outside page bounds")
        region, blocks = close_region(proposed, page["blocks"])
        full = region == page["bbox"]
        unit = {"evidence_id": f"{safe_identifier(doc['doc_id'])}:p{doc['page']}:u{index:03d}",
                "doc_id": doc["doc_id"], "source_report": doc["source_report"], "page": doc["page"],
                "region": region, "page_bbox": page["bbox"], "text": "\n".join(b["layout_text"] if segmentation == "blind_blocks" else b["text"] for b in blocks),
                "text_serialization": "official_words_spatial_lines_tabs_v1" if segmentation == "blind_blocks" else "official_block_text_v1",
                "image_path": portable_path(image_path) if image_path and full else None,
                "image_sha256": file_sha(image_path) if image_path and full else None,
                "word_block_refs": [b["block_id"] for b in blocks], "blocks": blocks,
                "whole_page": full, "region_source": proposal["source"],
                "scope_status": "programmatic_geometry_only", "visual_verified": False,
                "visual_verification": None, "question_exposure": False}
        unit["content_hash"] = digest({k: unit[k] for k in ("doc_id", "page", "region", "text", "word_block_refs", "blocks")})
        if not any(existing["word_block_refs"] == unit["word_block_refs"] for existing in result):
            result.append(unit)
    represented = {ref for unit in result for ref in unit["word_block_refs"]}
    if segmentation == "blind_blocks" and represented != {b["block_id"] for b in page["blocks"]}:
        raise ValueError("Automatic regions failed complete block coverage")
    return result

def render_units(units, pdf_path, output_dir, dpi=120):
    """Optional local PyMuPDF rendering; does not claim visual verification."""
    import fitz
    pdf = fitz.open(str(pdf_path))
    try:
        if len(pdf) != 1:
            raise ValueError("Expected a single-page PDF paired to the converted JSON")
        page = pdf[0]
        for unit in units:
            bounds = unit["page_bbox"]
            # Official JSON coordinates can be raster pixels while PDF uses points.
            # Accept only a matching aspect ratio and record the explicit transform.
            sx = page.rect.width / (bounds[2] - bounds[0])
            sy = page.rect.height / (bounds[3] - bounds[1])
            if abs(sx / sy - 1) > 0.005:
                raise ValueError("PDF/converted-coordinate aspect mismatch; manual resolution required")
            region = unit["region"]
            clip = [(region[0]-bounds[0])*sx, (region[1]-bounds[1])*sy,
                    (region[2]-bounds[0])*sx, (region[3]-bounds[1])*sy]
            path = Path(output_dir) / (unit["evidence_id"].replace(":", "_") + ".png")
            path.parent.mkdir(parents=True, exist_ok=True)
            page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), clip=fitz.Rect(clip)).save(str(path))
            unit["image_path"] = portable_path(path)
            unit["image_sha256"] = file_sha(path)
            unit["render"] = {"dpi": dpi, "source_pdf": portable_path(pdf_path), "method": "pymupdf_scaled_clip", "json_to_pdf_scale": [sx, sy], "pdf_clip": clip, "pdf_cropbox": list(page.cropbox), "pdf_mediabox": list(page.mediabox), "coordinate_origin": "cropbox_relative_page.rect"}
    finally:
        pdf.close()
    return units

def descriptions(units):
    """One question-independent, conservative geometry description per evidence unit."""
    import time
    output = []
    for unit in units:
        reject_gold(unit)
        started = time.perf_counter()
        lines = []
        blocks = unit["blocks"]
        for left, right in zip(blocks, blocks[1:]):
            a, b = left["bbox"], right["bbox"]
            if a[3] <= b[1]:
                relation = "is above"
            elif a[2] <= b[0] and min(a[3], b[3]) > max(a[1], b[1]):
                relation = "is to the left of"
            elif abs(a[0]-b[0]) <= 2:
                relation = "has an approximately aligned left edge with"
            else:
                relation = "has an uncertain geometric relationship with"
            lines.append(f"Block {left['block_id']} {relation} block {right['block_id']}.")
        if not lines:
            lines = ["Only one text block is available; row, column, and header relations are unknown."]
        output.append({"evidence_id": unit["evidence_id"], "evidence_content_hash": unit["content_hash"],
                       "text": "\n".join(lines), "source_kind": "coordinate_template_v1", "corrected": False,
                       "question_exposure": False, "verified": False, "verification_notes": "Unverified automatic geometry only; no table semantics inferred.",
                       "elapsed_seconds": time.perf_counter()-started, "corrections": [], "uncertainties": ["No inferred table header or semantic row/column relations"],
                       "template_version": "1.0"})
    return output
