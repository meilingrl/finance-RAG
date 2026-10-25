#!/usr/bin/env python3
"""Independent post-acquisition checks. Reads isolated QA; never run in input-only worker."""
import hashlib,json,random,sys
from collections import Counter
from pathlib import Path
import fitz
project=Path(__file__).resolve().parents[1]
root=Path(sys.argv[1]) if len(sys.argv)>1 else project/'data/phase2'
if not root.is_absolute(): root=project/root
manifest=json.loads((root/'sample_manifest.json').read_text())
load=lambda p:[json.loads(x) for x in p.read_text().splitlines()]
metadata={s:json.loads((root/f'isolated_gold/official_metadata/tatdqa_dataset_{s}.json').read_text()) for s in ('train','dev')}
original={r['doc']['uid']:(s,r) for s,rs in metadata.items() for r in rs}
selected=manifest['selected_documents']; docs=load(root/'inputs/documents.jsonl')
assert selected==docs
questions=load(root/'development/questions.jsonl')+load(root/'evaluation/questions.jsonl')
gold=load(root/'development/gold.jsonl')+load(root/'isolated_gold/evaluation_gold.jsonl')
qlookup={q['question_id']:q for q in questions}; glookup={g['question_id']:g for g in gold}
assert len(qlookup)==len(questions)==48 and len(glookup)==48
assert len(docs)==16 and len({d['doc_id'] for d in docs})==16
assert len({d['source_report'] for d in docs})==16
for d in docs:
 split,r=original[d['doc_id']]
 assert split==d['original_split']
 assert split==('train' if d['split']=='development' else 'dev')
 assert r['doc']['source']==d['source_report']
 qs=sorted((q for q in r['questions'] if q.get('answer_type')=='span' and q.get('req_comparison') is False and isinstance(q.get('question'),str) and q['question'].strip()),key=lambda q:q['uid'])
 random.Random(f"42:{d['doc_id']}").shuffle(qs)
 assert [q['uid'] for q in qs[:3]]==d['question_ids']
 for oq in qs[:3]:
  q=qlookup[oq['uid']]; g=glookup[oq['uid']]
  assert q['question']==oq['question'] and q['answer_type']==oq['answer_type']
  for key in ('answer','derivation','facts'): assert g[key]==oq.get(key)
  assert g['scale']==oq.get('scale','') and g['block_mapping']==(oq.get('block_mapping') or [])
  assert g['mapping_verified'] is False and g['support_complete'] is False
 raw=json.loads((project/d['ocr_path']).read_text()); assert len(raw['pages'])==1
 with fitz.open(project/d['pdf_path']) as pdf: assert len(pdf)==1
 for f in d['source_files']:
  data=(project/f['path']).read_bytes()
  assert hashlib.sha256(data).hexdigest()==f['sha256']
  assert len(data)==f['bytes']
# Reproduce document selection independently from the saved complete candidate table.
candidates=load(root/'acquisition_metadata/candidate_universe.jsonl'); chosen=[]; dev_reports=set()
for split,target in [('train',4),('dev',12)]:
 pool=sorted([c for c in candidates if c['original_split']==split and c['metadata_candidate']],key=lambda c:c['doc_id']); random.Random(42).shuffle(pool)
 sources=set(); picked=[]
 for c in pool:
  source=c['source_report']
  if source in sources: continue
  if split=='dev' and (source in dev_reports or source in manifest['selection_policy']['exclude_previously_exposed_evaluation_reports']): continue
  if any(f['doc_id']==c['doc_id'] for f in manifest['acquisition_validation_failures']): continue
  sources.add(source); picked.append(c['doc_id'])
  if len(picked)==target: break
 chosen+=picked
 if split=='train': dev_reports=sources
assert chosen==[d['doc_id'] for d in docs]
# Allowlist the question records rather than relying only on keyword blacklists.
allowed={'doc_id','source_report','split','original_split','question_id','question','answer_type'}
assert all(set(q)==allowed for q in questions)
assert not any(set(d)&{'answer','facts','derivation','block_mapping','support_block_ids'} for d in docs)
result={'status':'passed','documents':len(docs),'questions':len(questions),'original_question_text_unchanged':True,'original_gold_unchanged':True,'all_pdf_ocr_sha256_match':True,'all_pairs_single_page':True,'seeded_document_and_question_selection_reproduced':True,'report_disjoint':True,'duplicate_doc_question_ids':0,'input_and_gold_record_fields_separated':True,'semantic_gold_not_claimed':True,'verification_scope':'acquisition and metadata only; no inference, rule tuning, or semantic support adjudication','manifest_sha256':hashlib.sha256((root/'sample_manifest.json').read_bytes()).hexdigest()}
(root/'acquisition_validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
