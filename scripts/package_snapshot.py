#!/usr/bin/env python3
"""Package the explicit private engineering snapshot; never uploads or publishes."""
import argparse,fnmatch,hashlib,json,zipfile
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--output',required=True);p.add_argument('--manifest',default='docs/PACKAGING_MANIFEST.json');p.add_argument('--list-only',action='store_true')
a=p.parse_args();root=Path.cwd().resolve();manifest=json.loads(Path(a.manifest).read_text());output=Path(a.output)
if output.exists():raise SystemExit('Refusing to overwrite an existing archive')
files=set()
for pattern in manifest['include']:
 for path in root.glob(pattern):
  if not path.is_file():continue
  if path.is_symlink() or not path.resolve().is_relative_to(root):raise SystemExit('Out-of-project symlink rejected')
  rel=path.relative_to(root).as_posix()
  if any(fnmatch.fnmatch(rel,pat) for pat in manifest['exclude']):continue
  files.add(rel)
records=[]
for rel in sorted(files):
 path=root/rel;records.append({'path':rel,'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
summary={'purpose':manifest['purpose'],'files':records,'file_count':len(records),'uncompressed_bytes':sum(r['bytes'] for r in records),'excluded':manifest['exclude'],'external_resources':manifest['omitted_external_resources']}
if a.list_only:
 print(json.dumps(summary,ensure_ascii=False,indent=2));raise SystemExit(0)
output.parent.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
 for rel in sorted(files):z.write(root/rel,f"{manifest['archive_root']}/{rel}")
 z.writestr(f"{manifest['archive_root']}/PACKAGE_CONTENTS.json",json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
if output.stat().st_size>manifest['max_archive_bytes']:raise SystemExit('Archive exceeds declared maximum; do not deliver')
with zipfile.ZipFile(output) as z:
 if z.testzip() is not None:raise SystemExit('ZIP integrity failed')
print(json.dumps({'path':str(output),'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'files':len(records),'zip_integrity':'passed'},indent=2))
