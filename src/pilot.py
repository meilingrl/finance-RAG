#!/usr/bin/env python3
"""Run python src/pilot.py --help. No model execution command exists."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from ragpilot.common import read_json, write_json
from ragpilot.data import prepare, prepare_inputs
from ragpilot.pipeline import DEFAULT_CONFIG, make_fixture, run_offline, freeze_nonllm, score_frozen

def parser():
    p = argparse.ArgumentParser(description=__doc__)
    s = p.add_subparsers(dest="command", required=True)
    demo = s.add_parser("demo", help="Run artificial software fixtures only, with zero API calls")
    demo.add_argument("--output", default="artifacts/fixture-demo")
    imp = s.add_parser("import-tatdqa", help="Import local official QA + paired PDFs/JSON; no downloads")
    imp.add_argument("--qa", action="append", required=True, metavar="SPLIT=PATH")
    imp.add_argument("--docs-dir", required=True)
    imp.add_argument("--output", required=True)
    imp.add_argument("--seed", type=int, default=42)
    imp.add_argument("--dev-docs", type=int, default=4)
    imp.add_argument("--eval-docs", type=int, default=12)
    imp.add_argument("--max-questions", type=int, default=3)
    imp.add_argument("--render", action="store_true", help="Render images locally with optional PyMuPDF")
    imp.add_argument("--regions", help="Optional input-only reviewed coordinates JSON")
    inp=s.add_parser("prepare-inputs",help="Input-only region construction; never opens gold")
    inp.add_argument("--documents",required=True)
    inp.add_argument("--questions",action="append",required=True)
    inp.add_argument("--gold-manifest",required=True,help="Paths and hashes only, not gold contents")
    inp.add_argument("--output",required=True)
    inp.add_argument("--no-render",action="store_true")
    freeze=s.add_parser("freeze-nonllm",help="Seal rules, code and declared configs before evaluation")
    freeze.add_argument("--data",required=True)
    freeze.add_argument("--development-run",action="append",required=True)
    freeze.add_argument("--allowed-configs",required=True)
    freeze.add_argument("--output",required=True)
    score=s.add_parser("score-frozen",help="Score unchanged inference with separately reviewed support mappings")
    score.add_argument("--run",required=True)
    score.add_argument("--gold",required=True)
    score.add_argument("--output",required=True)
    for name in ("run-offline", "development-check"):
        run = s.add_parser(name, help="Local retrieval + E1 + disabled E2/E3/E4 preparation" if name == "run-offline" else "Development-only k=1/2 diagnostic; no automatic configuration selection")
        run.add_argument("--data", required=True)
        run.add_argument("--output", required=True)
        run.add_argument("--freeze", help="Required pre-evaluation code/config/data freeze for isolated gold")
        run.add_argument("--config", help="JSON configuration; configs/examples/config.example.yaml is valid JSON/YAML")
        run.add_argument("--retriever", choices=["bm25", "sentence_transformers"])
        run.add_argument("--model-path", help="Existing local sentence-transformer directory only")
        if name == "run-offline":
            run.add_argument("--k", type=int, choices=[1,2])
            run.add_argument("--split", choices=["development", "pilot_evaluation"], help="Keep role-specific metrics separate")
    return p

def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "demo":
            output = Path(args.output)
            data = output / "fixture-input"
            make_fixture(data)
            result = run_offline(data, output, run_kind="synthetic_software_fixture")
        elif args.command == "import-tatdqa":
            sources = {}
            for spec in args.qa:
                split, path = spec.split("=", 1)
                if split in sources:
                    raise ValueError("Duplicate input split")
                sources[split] = path
            result = prepare(sources, args.docs_dir, args.output, args.seed, args.dev_docs, args.eval_docs, args.max_questions, args.render, args.regions)
        elif args.command == "score-frozen":
            result=score_frozen(args.run,args.gold,args.output)
        elif args.command == "prepare-inputs":
            result=prepare_inputs(args.documents,args.questions,args.output,read_json(args.gold_manifest),render=not args.no_render)
        elif args.command == "freeze-nonllm":
            result=freeze_nonllm(args.data,args.development_run,read_json(args.allowed_configs),args.output)
        else:
            config = read_json(args.config) if args.config else {}
            if args.retriever:
                config["retriever"] = args.retriever
            if args.model_path:
                config["model_path"] = args.model_path
            if getattr(args, "k", None):
                config["k"] = args.k
            if args.command == "run-offline":
                result = run_offline(args.data, args.output, config, split=args.split, freeze_path=args.freeze)
            else:
                result = {"selection_status": "not_selected; inspect verified development mapping coverage before selecting", "runs": []}
                for k in [1,2]:
                    result["runs"].append(run_offline(args.data, Path(args.output)/f"k{k}", {**config, "k": k}, split="development"))
                write_json(Path(args.output)/"development_check.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
