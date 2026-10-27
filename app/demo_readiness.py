"""Read-only demo preflight using the same evidence and asset checks as execution."""
from formal_src.runner import (input_projection, demo_tasks, projected_model_root,
                               reviewed_assets, condition_content, RunnerBlocked)
from formal_src.retrieval import retrieve, LocalEncoder
from formal_src.evidence import build_associations, expand_once


def check_demo(projection, request, profile):
    data, bindings = input_projection(projection)
    questions, tasks = demo_tasks(request, data['inference_questions.jsonl'])
    question = questions[0]
    units = data['evidence_units.jsonl']
    retriever = request['demo_input']['retriever']
    config = {'retriever':retriever,'k':2, **profile.get('retrieval',{}).get(retriever,{})}
    encoder = LocalEncoder(projected_model_root(bindings)) if retriever == 'minilm' else None
    result = retrieve(question, units, config, encoder=encoder)
    selected = expand_once(result['selected_ids'],units,build_associations(units))
    indexed = {u['evidence_id']:u for u in units}
    evidence = [indexed[e] for e in selected]
    blocks = []
    for task in tasks:
        if task['work_type'] != 'generation':
            continue
        try:
            assets=reviewed_assets(evidence,units,data,bindings,task['condition'])
            condition_content(task['condition'],question['question'],evidence,assets)
        except RunnerBlocked as exc:
            blocks.append(str(exc))
    return sorted(set(blocks))
