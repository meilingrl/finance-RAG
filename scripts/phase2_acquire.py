#!/usr/bin/env python3
"""Acquire a bounded official TAT-DQA pilot without downloading full ZIP archives.

Only QA metadata is downloaded in full. Dataset answers stay under isolated_gold;
input-only workers must not read that directory. This script selects by metadata,
never answer content, mappings, or system performance. No models are used.
"""
from __future__ import annotations
import argparse, concurrent.futures, hashlib, json, random, re, struct, sys, time, zlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

REVISION = 'bc46208b22f0bfe47152c98fd0938d238ce868c2'
BASE = f'https://huggingface.co/datasets/next-tat/TAT-DQA/resolve/{REVISION}/'
SEED = 42

def sha(data): return hashlib.sha256(data).hexdigest()
def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
def jsonl(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in data))
def portable(p): return str(Path(p).resolve().relative_to(Path(__file__).resolve().parents[1]))

class Fetcher:
    def __init__(self, root): self.root, self.requests = root, []
    def range(self, filename, start, end, target):
        """Require exact byte range; never accept a silently downloaded archive."""
        size = end-start+1
        if target.exists():
            data=target.read_bytes()
            if len(data) != size: raise ValueError(f'Cached range size mismatch: {target}')
            return data
        url=BASE+filename
        for attempt in range(2):
            try:
                with urlopen(Request(url, headers={'Range':f'bytes={start}-{end}','Accept-Encoding':'identity'}),timeout=60) as r:
                    if r.status != 206: raise ValueError(f'Range unsupported: {filename}: {r.status}')
                    cr=r.headers.get('Content-Range','')
                    if not cr.startswith(f'bytes {start}-{end}/'): raise ValueError(f'Wrong range: {cr}')
                    data=r.read(size+1)
                    if len(data)!=size: raise ValueError('Range response size mismatch')
                    event={'url':url,'start':start,'end':end,'bytes':size,'sha256':sha(data),'content_range':cr,'etag':r.headers.get('ETag'),'retrieved_utc':datetime.now(timezone.utc).isoformat()}
                target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
                self.requests.append(event)
                return data
            except Exception:
                if attempt: raise
                time.sleep(1)
    def metadata(self, split):
        name=f'tatdqa_dataset_{split}.json'; target=self.root/'isolated_gold'/'official_metadata'/name
        if not target.exists():
            with urlopen(Request(BASE+name,headers={'Accept-Encoding':'identity'}), timeout=90) as r:
                if r.status != 200: raise ValueError(f'Full metadata response {r.status}')
                length=int(r.headers.get('Content-Length',0))
                if length>15_000_000: raise ValueError('Unexpected metadata size')
                data=r.read(15_000_001)
                if len(data)>15_000_000 or length and len(data)!=length: raise ValueError('Metadata cap/length mismatch')
                event={'url':BASE+name,'bytes':len(data),'sha256':sha(data),'etag':r.headers.get('ETag'),'retrieved_utc':datetime.now(timezone.utc).isoformat()}
            target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data); self.requests.append(event)
        data=target.read_bytes()
        return json.loads(data),{'path':portable(target),'sha256':sha(data),'bytes':len(data),'original_split':split,'url':BASE+name}

class RemoteZip:
    def __init__(self, split, fetch):
        self.split,self.fetch=split,fetch
        self.filename=f'tatdqa_docs_{split}.zip'
        self.cache=fetch.root/'acquisition_cache'/split
        info_path=self.cache/'archive_info.json'
        if info_path.exists(): info=json.loads(info_path.read_text())
        else:
            with urlopen(Request(BASE+self.filename, method='HEAD'),timeout=60) as r:
                info={'bytes':int(r.headers['Content-Length']),'etag':r.headers.get('ETag'),'url':BASE+self.filename}
            dump(info_path,info)
        self.info=info; size=info['bytes']; start=max(0,size-65557)
        tail=fetch.range(self.filename,start,size-1,self.cache/'tail.bin')
        pos=tail.rfind(b'PK\x05\x06')
        if pos<0: raise ValueError('Missing ZIP EOCD')
        _,disk,cd_disk,disk_n,n,cd_size,cd_start,comment=struct.unpack_from('<4s4H2IH',tail,pos)
        if disk or cd_disk or n==65535 or cd_start==0xffffffff: raise ValueError('Unsupported ZIP64/multi-disk')
        central=fetch.range(self.filename,cd_start,cd_start+cd_size-1,self.cache/'central.bin')
        entries={}; p=0
        while p<len(central):
            v=struct.unpack_from('<4s6H3I5H2I',central,p)
            if v[0]!=b'PK\x01\x02': raise ValueError('Bad central signature')
            name_len,extra_len,comment_len=v[10:13]
            name=central[p+46:p+46+name_len].decode('utf-8')
            if name in entries: raise ValueError('Duplicate archive member')
            entries[name]={'name':name,'method':v[4],'crc':v[7],'compressed_size':v[8],'size':v[9],'offset':v[16]}
            p+=46+name_len+extra_len+comment_len
        if len(entries)!=n: raise ValueError('Central-directory count mismatch')
        self.entries=entries
        dump(self.cache/'member_index.json', {'archive':info,'entries':entries,'eocd_entries':n,'central_start':cd_start,'central_bytes':cd_size})
    def read(self, member, target):
        entry=self.entries[member]
        if target.exists(): data=target.read_bytes()
        else:
            off=entry['offset']
            header=self.fetch.range(self.filename,off,off+29,self.cache/(Path(member).name+'.header'))
            v=struct.unpack('<4s5H3I2H',header)
            if v[0]!=b'PK\x03\x04' or v[2]&1: raise ValueError('Encrypted or invalid local ZIP header')
            start=off+30+v[9]+v[10]; end=start+entry['compressed_size']-1
            data=self.fetch.range(self.filename,start,end,self.cache/(Path(member).name+'.compressed'))
            if entry['method']==8: data=zlib.decompress(data,-15)
            elif entry['method']!=0: raise ValueError('Unsupported ZIP compression')
            target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
        if len(data)!=entry['size'] or zlib.crc32(data)&0xffffffff!=entry['crc']: raise ValueError('ZIP member length/CRC mismatch')
        return {'path':portable(target),'archive_member':member,'source_url':BASE+self.filename,'bytes':len(data),'sha256':sha(data),'crc32':f"{entry['crc']:08x}"}

def eligible(q):
    if q.get('answer_type')!='span': return 'answer_type_not_span'
    if q.get('req_comparison') is not False: return 'comparison_or_missing_flag'
    if not isinstance(q.get('question'),str) or not q['question'].strip(): return 'missing_question'
    return None

def question_record(q,d,role,split):
    return {'doc_id':d['uid'],'source_report':d['source'],'split':role,'original_split':split,'question_id':str(q['uid']),'question':q['question'],'answer_type':q['answer_type']}

def gold_record(q,d,role):
    mappings=q.get('block_mapping') or []
    return {'question_id':str(q['uid']),'doc_id':d['uid'],'split':role,'answer':q.get('answer'),'scale':q.get('scale',''),'derivation':q.get('derivation'),'facts':q.get('facts'),'block_mapping':mappings,'support_block_ids':sorted({str(k) for m in mappings for k in m}),'mapping_verified':False,'support_complete':False,'mapping_status':'official_offsets_not_semantically_verified','offset_convention':'zero_based_python_character_slice_end_exclusive'}

def structural_mapping(gold,raw):
    blocks={b['uuid']:b['text'] for p in raw['pages'] for b in p['blocks']}
    details=[]; slices=[]; valid=bool(gold['block_mapping'])
    for mapping in gold['block_mapping']:
        for block_id,span in mapping.items():
            text=blocks.get(block_id)
            ok=isinstance(span,list) and len(span)==2 and all(isinstance(x,int) and not isinstance(x,bool) for x in span)
            ok=ok and text is not None and 0<=span[0]<span[1]<=len(text)
            val=text[span[0]:span[1]] if ok else None
            details.append({'block_id':block_id,'offsets':span,'in_bounds':ok,'extracted_slice':val})
            if ok: slices.append(val)
            valid=bool(valid and ok)
    facts=gold.get('facts')
    facts_exact=isinstance(facts,list) and facts==slices
    facts_ws=isinstance(facts,list) and [str(x).split() for x in facts]==[x.split() for x in slices]
    return {'question_id':gold['question_id'],'doc_id':gold['doc_id'],'mapping_present':bool(gold['block_mapping']),'all_offsets_valid':valid,'slices_equal_facts_exact':facts_exact,'slices_equal_facts_whitespace_normalized':facts_ws,'mapping_details':details,'semantic_support_reviewed':False,'support_complete':False,'status':'structural_and_fact_consistency_only_not_semantic_gold'}

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',default='data/phase2'); args=parser.parse_args()
    project=Path(__file__).resolve().parents[1]; root=Path(args.output)
    if not root.is_absolute(): root=project/root
    if (root/'sample_manifest.json').exists(): raise SystemExit('Frozen sample exists; choose a new output directory; do not overwrite.')
    root.mkdir(parents=True,exist_ok=True)
    (root/'isolated_gold').mkdir(exist_ok=True)
    (root/'isolated_gold'/'README.txt').write_text('Evaluation answers and full official QA metadata. Input-only builder MUST NOT OPEN these files until its inference configuration is frozen. This is process isolation, not a filesystem access-control guarantee.\n')
    fetch=Fetcher(root)
    # Full QA metadata is small; original documents/archives are never downloaded in full.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures={split:pool.submit(fetch.metadata,split) for split in ('train','dev')}
        archives={split:pool.submit(RemoteZip,split,fetch) for split in ('train','dev')}
        metadata={split:f.result() for split,f in futures.items()}
        archives={split:f.result() for split,f in archives.items()}
    print('Official metadata and ZIP indexes ready.',flush=True)
    exclusions=[]; pools={}; counts={}; candidates=[]; seen_docs=set(); seen_questions=set(); global_report_sets={}
    for split in ('train','dev'):
        records,_=metadata[split]; archive=archives[split]
        q_counts=Counter(); pool=[]; reports=Counter(); duplicate_q_text=Counter()
        for r in records:
            d=r['doc']; uid=d['uid']; reports[d['source']]+=1
            if uid in seen_docs: raise ValueError('Duplicate official doc ID across splits')
            seen_docs.add(uid)
            good=[]
            for q in r['questions']:
                qid=q['uid']
                if qid in seen_questions: raise ValueError('Duplicate official question ID across splits')
                seen_questions.add(qid); duplicate_q_text[' '.join(q.get('question','').lower().split())]+=1
                reason=eligible(q)
                q_counts[reason or 'eligible_span_noncomparison']+=1
                if reason: exclusions.append({'level':'question','doc_id':uid,'question_id':qid,'original_split':split,'reason':reason})
                else: good.append(q)
            names=archive.entries
            pdf=f'{split}/{uid}.pdf'; ocr=f'{split}/{uid}.json'
            pngs=[n for n in names if re.fullmatch(re.escape(f'{split}/{uid}_')+r'\d+\.png',n)]
            reason=None
            if not d.get('source'): reason='missing_report_identity'
            elif len(good)<3: reason='fewer_than_three_eligible_questions'
            elif pdf not in names or ocr not in names: reason='missing_pdf_ocr_archive_pair'
            elif len(pngs)!=1: reason='archive_page_image_count_not_one'
            row={'doc_id':uid,'source_report':d['source'],'original_split':split,'eligible_question_count':len(good),'archive_page_image_count':len(pngs),'metadata_candidate':reason is None}
            candidates.append(row)
            if reason: exclusions.append({'level':'document','doc_id':uid,'original_split':split,'reason':reason})
            else: pool.append({'record':r,'eligible':good,'candidate':row})
        pool.sort(key=lambda x:x['record']['doc']['uid']); random.Random(SEED).shuffle(pool); pools[split]=pool
        counts[split]={'metadata_documents':len(records),'metadata_questions':sum(q_counts.values()),'question_eligibility_counts':dict(q_counts),'metadata_candidates_with_at_least_three_eligible_questions_and_one_png':len(pool),'unique_source_reports':len(reports),'source_reports_with_multiple_docs':sum(v>1 for v in reports.values()),'repeated_question_text_groups':sum(v>1 for v in duplicate_q_text.values()),'duplicate_question_text_excess_records':sum(v-1 for v in duplicate_q_text.values() if v>1)}
        global_report_sets[split]=set(reports)
    previous=json.loads((project/'data/raw/tatdqa/qa/dev_selected_record.json').read_text())
    if isinstance(previous,dict): previous=[previous]
    exposed_reports={r['doc']['source'] for r in previous}
    print(json.dumps({'candidate_counts':counts,'previously_exposed_evaluation_reports':sorted(exposed_reports)}),flush=True)
    selected=[]; role_reports={}; acquisition_failures=[]
    sys.path.insert(0,str(project/'src'))
    from ragpilot.evidence import normalize_page
    import fitz
    for split,role,target_n in [('train','development',4),('dev','pilot_evaluation',12)]:
        picked=[]; seen_reports=set()
        for rank,item in enumerate(pools[split],1):
            d=item['record']['doc']; uid=d['uid']; report=d['source']
            reason=None
            if role=='pilot_evaluation' and report in role_reports.get('development',set()): reason='source_report_in_selected_development'
            elif role=='pilot_evaluation' and report in exposed_reports: reason='previously_exposed_evaluation_source_report'
            # Exactly one document per source report within each role improves source diversity.
            elif report in seen_reports: reason='source_report_already_selected_in_role'
            if reason:
                exclusions.append({'level':'sampling','doc_id':uid,'original_split':split,'reason':reason,'seeded_rank':rank}); continue
            archive=archives[split]
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                tasks=[pool.submit(archive.read,f'{split}/{uid}.{ext}',root/'inputs'/folder/f'{uid}.{ext}') for ext,folder in [('json','ocr'),('pdf','pdf')]]
                source_files=[f.result() for f in tasks]
            raw=json.loads((root/'inputs'/'ocr'/f'{uid}.json').read_text())
            reason=None
            try: normalized=normalize_page(raw)
            except (ValueError,KeyError,TypeError) as exc: reason='invalid_single_page_ocr'; error=str(exc)
            with fitz.open(root/'inputs'/'pdf'/f'{uid}.pdf') as pdf:
                pages=len(pdf)
                if pages!=1: reason='pdf_not_single_page'; error=f'{pages} pages'
                rect=list(pdf[0].rect); crop=list(pdf[0].cropbox); media=list(pdf[0].mediabox)
            if reason:
                acquisition_failures.append({'doc_id':uid,'reason':reason,'detail':error,'source_files':source_files})
                exclusions.append({'level':'document_validation','doc_id':uid,'original_split':split,'reason':reason}); continue
            chosen_q=sorted(item['eligible'],key=lambda q:q['uid'])
            random.Random(f'{SEED}:{uid}').shuffle(chosen_q); chosen_q=chosen_q[:3]
            doc={'doc_id':uid,'source_report':report,'page':int(d.get('page',1)),'original_split':split,'split':role,'page_count':pages,'page_bbox':normalized['bbox'],'pdf_rect':rect,'pdf_cropbox':crop,'pdf_mediabox':media,'ocr_path':portable(root/'inputs'/'ocr'/f'{uid}.json'),'pdf_path':portable(root/'inputs'/'pdf'/f'{uid}.pdf'),'question_ids':[str(q['uid']) for q in chosen_q],'eligible_question_count':len(item['eligible']),'seeded_candidate_rank':rank,'block_count':len(normalized['blocks']),'source_files':source_files}
            picked.append({'document':doc,'questions':chosen_q,'raw':raw,'official_record':item['record']}); seen_reports.add(report)
            print(f"Selected {role} {len(picked)}/{target_n}: {uid} ({report})",flush=True)
            if len(picked)>=target_n: break
        role_reports[role]=seen_reports; selected.extend(picked)
    documents=[x['document'] for x in selected]
    questions={r:[] for r in ('development','pilot_evaluation')}; gold={r:[] for r in questions}; checks=[]
    for item in selected:
        doc=item['document']; role=doc['split']; d=item['official_record']['doc']
        for q in item['questions']:
            questions[role].append(question_record(q,d,role,doc['original_split']))
            g=gold_record(q,d,role); check=structural_mapping(g,item['raw']); checks.append(check)
            g['mapping_structurally_verified']=check['all_offsets_valid']
            g['mapping_fact_text_consistent']=check['slices_equal_facts_whitespace_normalized']
            gold[role].append(g)
    # No answer content or evidence mappings are written outside development/isolated_gold.
    jsonl(root/'inputs/documents.jsonl',documents)
    jsonl(root/'development/questions.jsonl',questions['development']); jsonl(root/'development/gold.jsonl',gold['development'])
    jsonl(root/'evaluation/questions.jsonl',questions['pilot_evaluation']); jsonl(root/'isolated_gold/evaluation_gold.jsonl',gold['pilot_evaluation'])
    jsonl(root/'isolated_gold/mapping_validation.jsonl',checks)
    jsonl(root/'development/mapping_validation.jsonl',[c for c in checks if c['question_id'] in {q['question_id'] for q in questions['development']}])
    jsonl(root/'acquisition_metadata/candidate_universe.jsonl',candidates); jsonl(root/'acquisition_metadata/exclusions.jsonl',exclusions)
    jsonl(root/'isolated_gold/selected_official_records.jsonl',[i['official_record'] for i in selected])
    if role_reports['development'] & role_reports['pilot_evaluation']: raise ValueError('Report leakage')
    all_q=questions['development']+questions['pilot_evaluation']
    def duplicate_groups(key):
        c=Counter(key(q) for q in all_q); return sum(v>1 for v in c.values())
    mapping_summary={'questions':len(checks),'mapping_present':sum(c['mapping_present'] for c in checks),'all_offsets_valid':sum(c['all_offsets_valid'] for c in checks),'slices_equal_facts_exact':sum(c['slices_equal_facts_exact'] for c in checks),'slices_equal_facts_whitespace_normalized':sum(c['slices_equal_facts_whitespace_normalized'] for c in checks),'semantic_support_reviewed':0,'complete_semantic_gold':0,'meaning':'Structural consistency only. No semantic completeness inference from valid offsets.'}
    manifest={'schema_version':2,'dataset':'TAT-DQA','publisher_revision':REVISION,'seed':SEED,'sample_status':'bounded_non_llm_pilot_subset','metadata_universe':'Complete official train and dev QA metadata at pinned revision. Test split is not inspected.','selection_policy':{'eligible_questions':{'answer_type':'span','req_comparison':False,'question_nonempty':True},'document_filters':{'minimum_eligible_questions':3,'archive_page_image_count':1,'paired_pdf_and_ocr':True,'actual_pdf_and_ocr_page_count':1},'sampling':'Each original split sorted by doc.uid then random.Random(42).shuffle; walk until target count, one source report per role. Questions sorted by uid then random.Random(f"42:{doc_id}").shuffle; first three.','development_original_split':'train','evaluation_original_split':'dev','cross_role_report_disjoint':True,'exclude_previously_exposed_evaluation_reports':sorted(exposed_reports),'performance_used_for_selection':False},'requested':{'development_documents':4,'development_questions':12,'evaluation_documents':12,'evaluation_questions':36},'candidate_counts':counts,'original_split_source_report_overlap_count':len(global_report_sets['train']&global_report_sets['dev']),'exclusion_counts':dict(Counter(x['reason'] for x in exclusions)),'acquisition_validation_failures':acquisition_failures,'selected_documents':documents,'actual':{'documents':len(documents),'questions':len(all_q),'development_documents':sum(d['split']=='development' for d in documents),'evaluation_documents':sum(d['split']=='pilot_evaluation' for d in documents),'development_questions':len(questions['development']),'evaluation_questions':len(questions['pilot_evaluation'])},'duplicates':{'document_ids':len(documents)-len({d['doc_id'] for d in documents}),'question_ids':len(all_q)-len({q['question_id'] for q in all_q}),'question_text_groups':duplicate_groups(lambda q:' '.join(q['question'].lower().split())),'source_reports':len(documents)-len({d['source_report'] for d in documents}),'cross_role_source_reports':len(role_reports['development']&role_reports['pilot_evaluation'])},'mapping_validation':mapping_summary,'gold_isolation':{'evaluation_path':portable(root/'isolated_gold/evaluation_gold.jsonl'),'builder_allowed_paths':[portable(root/'inputs'),portable(root/'development'),portable(root/'evaluation/questions.jsonl')],'source_sampler_has_seen_qa':True,'source_sampler_performed_rule_tuning_or_description_generation':False,'isolation_kind':'separate_file_paths_and_worker_contract_not_OS_sandbox'},'qa_sources':[metadata[s][1] for s in ('train','dev')],'license':'CC BY 4.0 (current publisher statement)','attribution':'TAT-DQA, NExT++ / TAT@NExT, Zhu et al., Towards complex document understanding by discrete reasoning, ACM MM 2022.','model_calls':0,'generated_utc':datetime.now(timezone.utc).isoformat()}
    dump(root/'sample_manifest.json',manifest)
    prior_log=root/'acquisition_metadata/network_requests.json'
    prior=json.loads(prior_log.read_text()) if prior_log.exists() else []
    dump(prior_log,prior+fetch.requests)
    files=[]
    for p in sorted(root.rglob('*')):
        if p.is_file() and 'acquisition_cache' not in p.parts and p.name!='PROVENANCE.json':
            data=p.read_bytes(); files.append({'path':portable(p),'bytes':len(data),'sha256':sha(data)})
    dump(root/'PROVENANCE.json',{'dataset':'TAT-DQA','official_project':'https://nextplusplus.github.io/TAT-DQA/','official_repository':'https://github.com/NExTplusplus/TAT-DQA','publisher':'https://huggingface.co/datasets/next-tat/TAT-DQA','revision':REVISION,'license':'CC BY 4.0','license_url':'https://creativecommons.org/licenses/by/4.0/','license_note':'Current official release pages state CC BY 4.0; historical paper abstract has non-commercial wording. Prior provenance retained.','acquisition_script':portable(Path(__file__)),'acquisition_script_sha256':sha(Path(__file__).read_bytes()),'archive_info':{s:archives[s].info for s in archives},'network_downloaded_bytes_this_process':sum(x['bytes'] for x in fetch.requests),'full_archive_downloads':0,'metadata_complete':True,'documents_downloaded':len(documents)+len(acquisition_failures),'modifications':'PDF/OCR bytes unchanged. Selected questions/gold and manifests reserialized. No question rewriting, no descriptions, no inference.','files':files})
    print(json.dumps({'actual':manifest['actual'],'mapping_validation':mapping_summary,'duplicates':manifest['duplicates'],'path':str(root)},indent=2),flush=True)

if __name__=='__main__': main()
