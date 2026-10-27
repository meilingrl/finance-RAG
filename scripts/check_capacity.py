"""Estimate token demand and latency from saved calls. Sends no API requests."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.service import ApplicationService
from formal_src.capacity import from_workspace
from app.workspace import resolve_workspace
if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--workspace',type=Path);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
 workspace=resolve_workspace(ROOT,args.workspace);service=ApplicationService(ROOT,workspace)
 remaining,_,blocks=service._budget()
 if blocks:raise SystemExit(','.join(blocks))
 report=from_workspace(workspace,remaining);args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='sources'},indent=2))
