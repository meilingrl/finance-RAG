#!/usr/bin/env python3
"""Export raw counts and denominators without generating experimental analysis."""
import json,csv
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ragpilot.common import read_json,write_json,now,file_sha
root=Path(sys.argv[1] if len(sys.argv)>1 else 'artifacts/nonllm-phase2/final-v5')
rows=[]
for split in ('development','evaluation'):
    for path in sorted((root/split).glob('*/run.json')):
        folder=path.parent;run=read_json(path);m=read_json(folder/'metrics.json')
        review=root/'reviewed-evaluation'/folder.name/'metrics.json'
        semantic=read_json(review) if split=='evaluation' and review.exists() else m
        a=m['answer_metrics']['E1'];n=a['expected_questions'];ret=list(semantic['retrieval_metrics'].values())[0]
        row={'split':split,'config_name':folder.name,'questions':n,'answered':a['answered_questions'],
             'abstained':a['status_counts']['abstention'],'strict_answer_scale_em_correct':a['correct_per_executed']['numerator'],
             'strict_answer_scale_em_denominator':n,'answer_only_em_correct':a['answer_only_exact_match']['numerator'],
             'answer_only_em_denominator':n,'answer_only_em_answered_denominator':a['answer_only_exact_match']['denominator'],
             'numeric_value_match_correct':a['numeric_value_match']['numerator'],'numeric_value_match_denominator':a['numeric_value_match']['denominator'],
             'numeric_with_units_correct':a['numeric_with_units_match']['numerator'],'numeric_with_units_denominator':a['numeric_with_units_match']['denominator'],
             'verified_any_support_correct':ret['any_support_hit']['numerator'],'verified_any_support_denominator':ret['any_support_hit']['denominator'],
             'verified_complete_support_correct':ret['complete_support_coverage']['numerator'],'verified_complete_support_denominator':ret['complete_support_coverage']['denominator'],
             'evidence_scope_breakdown':ret['by_evidence_scope'],'output':str(folder),'reviewed_metrics':str(review) if split=='evaluation' and review.exists() else None,
             'source_run_sha256':file_sha(path),'retrieval_timing':m['retrieval_timing']}
        if 'encoder_diagnostics' in m:
            e=m['encoder_diagnostics'];row['encoder_truncation']={k:e[k] for k in ('truncated_unit_count','unit_count','selected_truncated_occurrences','selected_occurrences','effect')}
        rows.append(row)
summary={'generated_at':now(),'status':'raw_non_generative_results','official_tatdqa_metrics':False,
         'data':{'documents':16,'questions':48,'development_questions':12,'evaluation_questions':36,'regions':97,'whole_page_documents':3,'local_evaluation_questions':27,'whole_page_evaluation_questions':9},
         'runs':rows,'generative_model_calls':0,'api_spend_usd':0,
         'metric_cautions':['Strict answer+scale EM and answer-only EM have different semantics; both shown with all-question denominators.',
             'Numeric diagnostics use only answered numeric-gold questions, not all questions.',
             'E2/E3/E4 not executed; no scores assigned.',
             'Support metrics use independently verified mappings only; answer-bearing and complete-support scopes are separate.',
             'Three BM25 answer-only matches lose strict EM due equivalent inline-unit/blank-scale representation; two due genuinely missing thousand scales.',
             'Small pilot and rule errors remain; no statistical or course-completion conclusions.']}
write_json(root.parent/'RAW_RESULTS.json',summary)
with (root.parent/'RAW_COUNTS.csv').open('w',newline='',encoding='utf-8') as f:
    columns=[k for k in rows[0] if k not in {'evidence_scope_breakdown','retrieval_timing','encoder_truncation'}]
    writer=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
print(json.dumps(summary,ensure_ascii=False,indent=2))
