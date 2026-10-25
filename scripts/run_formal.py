"""Application-managed inference subprocess; credentials arrive only via environment."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from formal_src.runner import run,atomic
from formal_src.budget import append

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--run-dir',required=True);args=parser.parse_args();rd=Path(args.run_dir).resolve()
 try:
  spec=json.loads((rd/'spec.json').read_text())
  run(spec,lambda event:append(rd/'worker-events.jsonl',event),{'run_dir':str(rd)})
 except Exception:
  # An unowned launcher must never overwrite an active owner's result.
  return 1
 return 0
if __name__=='__main__':sys.exit(main())
