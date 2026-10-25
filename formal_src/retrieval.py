"""Same-candidate BM25 and local-only windowed MiniLM, including k=4."""
from __future__ import annotations
import math
from pathlib import Path
from src.ragpilot.common import reject_gold
from src.ragpilot.retrieval import _bm25
from .contracts import safe_json


class LocalEncoder:
    def __init__(self, model_path):
        path=Path(model_path)
        if not path.is_dir() or not (path/'modules.json').is_file():
            raise ValueError('A registered local MiniLM model directory is required')
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ValueError('The optional local encoder dependency is missing') from exc
        self.model=SentenceTransformer(str(path.resolve()),device='cpu',local_files_only=True,trust_remote_code=False)
        self.model.max_seq_length=256
    def tokenize(self,text):
        return self.model.tokenizer.encode(text,add_special_tokens=False)
    def decode(self,tokens):
        return self.model.tokenizer.decode(tokens,skip_special_tokens=True)
    def encode(self,texts):
        return self.model.encode(texts,normalize_embeddings=True,show_progress_bar=False,convert_to_numpy=True,prompt='').tolist()


def _cosine(a,b):
    if len(a)!=len(b) or not a or not all(type(v) in (int,float) and math.isfinite(v) for v in a+b):
        raise ValueError('Invalid encoder output')
    norm=math.sqrt(sum(x*x for x in a)*sum(y*y for y in b))
    return sum(x*y for x,y in zip(a,b))/norm if norm else 0.


def retrieve(question:dict, units:list[dict], config:dict, *, encoder=None)->dict:
    safe_json(question);reject_gold(question)
    for unit in units:
        safe_json(unit);reject_gold(unit)
    kind=config.get('retriever','bm25');k=config.get('k',2)
    if kind not in {'bm25','minilm','sentence_transformers'} or type(k) is not int or k not in {1,2,4}:
        raise ValueError('Unsupported retrieval configuration')
    if not all(isinstance(question.get(key),str) and question[key] for key in ('question_id','doc_id','question')):
        raise ValueError('Question identity and text are required')
    candidates=sorted([u for u in units if u['doc_id']==question['doc_id']],key=lambda u:u['evidence_id'])
    ids=[u['evidence_id'] for u in candidates]
    if len(set(ids))!=len(ids):raise ValueError('Duplicate evidence ID')
    windows={};truncated={};scores=[]
    if kind=='bm25':
        cfg={'tokenizer':config.get('tokenizer',config.get('variant','finance')),'k1':config.get('k1',1.5),'b':config.get('b',.75)}
        if type(cfg['k1']) not in (int,float) or not math.isfinite(cfg['k1']) or cfg['k1']<=0 or type(cfg['b']) not in (int,float) or not math.isfinite(cfg['b']) or not 0<=cfg['b']<=1:
            raise ValueError('Invalid BM25 parameters')
        scores,_=_bm25(question['question'],candidates,cfg)
    elif candidates:
        encoder=encoder or LocalEncoder(config.get('model_path',''))
        variant=config.get('variant','windowed')
        if variant not in {'windowed','first_segment'}:raise ValueError('Unknown encoder variant')
        size=config.get('window_size',192);stride=config.get('stride',160)
        if type(size) is not int or type(stride) is not int or not 1<=stride<=size<=192:
            raise ValueError('Invalid encoder window policy')
        if len(encoder.tokenize(question['question']))>254:
            raise ValueError('Query exceeds verified encoder budget; no truncation')
        query=encoder.encode([question['question']])[0]
        for unit in candidates:
            tokens=encoder.tokenize(unit['text']);chunks=[]
            original_length=len(tokens)
            if variant=='first_segment':tokens=tokens[:254]
            effective_size=254 if variant=='first_segment' else size
            effective_stride=254 if variant=='first_segment' else stride
            for start in range(0,max(1,len(tokens)),effective_stride):
                part=tokens[start:start+effective_size]
                chunks.append(encoder.decode(part) if hasattr(encoder,'decode') else ' '.join(part))
                if start+effective_size>=len(tokens):break
            vectors=encoder.encode(chunks)
            if len(vectors)!=len(chunks):raise ValueError('Encoder dropped windows')
            scores.append(max(_cosine(query,v) for v in vectors))
            windows[unit['evidence_id']]=len(chunks)
            truncated[unit['evidence_id']]=variant=='first_segment' and original_length>254
    by_id=dict(zip(ids,scores))
    selected=sorted(ids,key=lambda eid:(-by_id[eid],eid))[:k]
    return {'question_id':question['question_id'],'doc_id':question['doc_id'],
            'retriever':'bm25' if kind=='bm25' else 'minilm','candidate_ids':ids,'selected_ids':selected,
            'candidate_scores':by_id,'actual_k':len(selected),'requested_k':k,'status':'ok',
            'window_counts':windows,'input_truncated':truncated,'config':dict(config)}
