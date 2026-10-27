"""Input-only preparation checkpoints, bound to code and registered inputs."""
import json
from pathlib import Path
from .provider import digest
from .recovery import atomic_bytes

class PreparationCache:
 def __init__(self,run_dir,identity):
  self.root=Path(run_dir)/'preparation-cache';self.identity=identity;self.memory={}
  self.root.mkdir(exist_ok=True)
 def get(self,key,build):
  identity={'scope':self.identity,'key':key};name=digest(identity)
  if name in self.memory:return self.memory[name]
  path=self.root/(name+'.json')
  if path.exists():
   try:
    item=json.loads(path.read_text())
    if item['identity']!=identity or digest(item['value'])!=item['sha256']:raise ValueError()
    value=item['value']
   except (ValueError,KeyError,TypeError):raise ValueError('preparation_checkpoint_changed') from None
  else:
   value=build();atomic_bytes(path,json.dumps({'identity':identity,'value':value,'sha256':digest(value)},sort_keys=True,allow_nan=False).encode())
  self.memory[name]=value;return value
