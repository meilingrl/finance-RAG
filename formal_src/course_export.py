"""Course-only source projection; never exports the private engineering snapshot."""
from __future__ import annotations
import hashlib
import json
import re
import zipfile
from pathlib import Path
from .contracts import RunResults

MANIFEST=Path(__file__).resolve().parents[1]/'docs/delivery/COURSE_SUBMISSION_MANIFEST.json'
FORBIDDEN={'data','artifacts','.local-secrets','.venv-app','.git','__pycache__','node_modules','venv','checkpoint'}
EXTENSIONS={'.py','.md','.txt','.json','.html','.css','.js','.cjs','.yaml','.yml'}


def collect_course_sources(root:Path, *, credential_values=())->dict[str,Path]:
    root=Path(root).resolve();config=json.loads(MANIFEST.read_text());files={}
    for pattern in config['source_patterns']:
        for path in sorted(root.glob(pattern)):
            relative=path.relative_to(root)
            if not path.is_file() or FORBIDDEN.intersection(relative.parts) or any(part.startswith('.env') for part in relative.parts):
                continue
            if not path.resolve().is_relative_to(root) or path.suffix not in EXTENSIONS:
                raise ValueError('Unsafe course source binding')
            content=path.read_bytes()
            if any(value and value.encode() in content for value in credential_values):
                raise ValueError('Credential found in course source')
            files[relative.as_posix()]=path
    return files


def runtime_configuration(spec):
    """Preserve the exact frozen runtime or reject an unexportable extension."""
    from urllib.parse import urlsplit
    from .run_plan import CANONICAL_PROFILE,content_sha
    profile=spec['resolved_config'];provider=profile['provider']
    allowed={'endpoint','max_output_tokens','reserve_input_tokens','reasoning_effort','stream','timeout_seconds','max_request_bytes'}
    if not isinstance(provider,dict) or set(provider)-allowed:
        raise ValueError('Unexportable provider runtime fields')
    endpoint=urlsplit(provider.get('endpoint',''))
    if (endpoint.scheme not in {'https','http'} or not endpoint.hostname or endpoint.username
            or endpoint.password or endpoint.query or endpoint.fragment):
        raise ValueError('Unexportable provider endpoint')
    for field,value in provider.items():
        if field=='endpoint':continue
        if field=='reasoning_effort':
            if value not in {'none','minimal','low','medium','high','xhigh','max','ultra'}:raise ValueError('Unexportable provider reasoning value')
        elif field=='stream':
            if type(value) is not bool:raise ValueError('Unexportable provider stream value')
        elif type(value) not in (int,float) or value<=0:
            raise ValueError('Unexportable provider numeric value')
    if profile['code_sha']!=spec['code_sha'] or profile['freeze_sha']!=spec['freeze_sha'] or profile['resource_manifest_sha']!=spec['resource_sha']:
        raise ValueError('Runtime identity mismatch')
    identity={'models':profile['models'],'provider':provider}
    return {'schema_version':1,'run_id':spec['run_id'],'resolved_config':profile,
            'canonical_recipe_sha256':content_sha(CANONICAL_PROFILE),
            'runtime_profile_sha256':content_sha(profile),
            'code_sha':profile['code_sha'],'prompt_sha':profile['prompt_sha'],
            'resource_manifest_sha':profile['resource_manifest_sha'],'freeze_sha':profile['freeze_sha'],
            'model_provider_identity':identity,'model_provider_sha256':content_sha(identity)}


def resource_sha_index(spec, summary, manifest):
    """References only: public model downloads and dataset content identities."""
    from .contracts import relative
    catalog_path=Path(__file__).resolve().parents[1]/'configs/formal/minilm-files.json'
    catalog=json.loads(catalog_path.read_text())
    public_catalog={key:catalog[key] for key in ('repo_id','revision','license')}
    public_catalog['files']=[{key:entry[key] for key in ('file','size','sha256','url')} for entry in catalog['files']]
    known={entry['sha256']:entry['url'] for entry in public_catalog['files']}
    models=[];dataset=[]
    for binding in manifest['resources']:
        relative(binding['path'])
        item={key:binding[key] for key in ('kind','path','sha256')}
        if binding['kind']=='model':
            item['public_download_url']=known.get(binding['sha256'])
            item['public_download_status']='pinned_sha_match' if binding['sha256'] in known else 'no_verified_public_pointer_for_registered_sha'
            models.append(item)
        else:dataset.append(item)
    return {'schema_version':1,'run_id':summary['run_id'],'input_sha':summary['input_sha'],
            'output_sha':summary['output_sha'],'evaluation_sha':summary['evaluation_sha'],
            'resource_manifest_sha':spec['resource_sha'],'datasets_and_weights_included':False,
            'model_resources':{'included':False,'pointer_policy':'public_pinned_downloads_only_when_registered_sha_matches',
                'registered_resources':models,'public_download_manifest':public_catalog,
                'public_download_manifest_sha256':hashlib.sha256(catalog_path.read_bytes()).hexdigest()},
            'dataset_resources':{'included':False,'pointer_policy':'content_sha_only_no_dataset_bytes',
                'registered_resources':dataset,'acquisition_note':'Obtain dataset separately under its publisher terms; no private download URLs or evaluation contents are included.'}}


def export_bundle(root:Path, run_dir:Path, destination:Path, *, credential_values=())->dict:
    root=Path(root).resolve();run_dir=Path(run_dir).resolve();config=json.loads(MANIFEST.read_text())
    missing=[name for name in config['required_documents'] if not (root/name).is_file()]
    if missing:return {'exported':False,'blocking_items':['course_documents_missing'],'missing':missing}
    try:
        metadata=json.loads((root/'course/submission.json').read_text())
        for field in ('leader_id','leader_name','project_name'):
            if not isinstance(metadata[field],str) or not metadata[field].strip() or re.search(r'[\\/:*?"<>|]',metadata[field]):
                raise ValueError()
        members=metadata['members']
        if not isinstance(members,list) or not 2<=len(members)<=4 or len({m['id'] for m in members})!=len(members):raise ValueError()
        if not any(m['id']==metadata['leader_id'] and m['name']==metadata['leader_name'] for m in members):raise ValueError()
        contributions=[]
        for member in members:
            for field in ('id','name'):
                if not isinstance(member[field],str) or not member[field].strip() or re.search(r'[\\/:*?"<>|]',member[field]):raise ValueError()
            contributions.append(root/f'course/贡献声明_{member["id"]}_{member["name"]}.md')
    except (ValueError,KeyError,TypeError):
        return {'exported':False,'blocking_items':['submission_identity_invalid']}
    if any(not p.is_file() for p in contributions):return {'exported':False,'blocking_items':['contribution_statements_missing']}
    try:
        from .verification import check_seal,check_result_proof,_evaluation_files,validate_audit_plan,_json,_rows,_sha
        from .profile_lifecycle import validate_frozen_profile
        from .audit import audit_status
        state=_json(run_dir/'state.json');spec=_json(run_dir/'spec.json')
        seal,tasks,predictions=check_seal(run_dir)
        proof=check_result_proof(run_dir,required=True)
        summary=RunResults.from_dict(_json(run_dir/'summary.json'))
        validate_frozen_profile(spec['resolved_config'],spec['freeze_sha'])
        files,evaluation_sha=_evaluation_files(run_dir.parent.parent,spec)
        from .course_policy import automatic
        course=automatic(spec['resolved_config'])
        if course:
            audit={'audit_complete':False,'status':'not_performed_course_scope'}
            audit_valid=(summary['audit_complete'] is False and summary['engineering'].get('audit')==audit and summary['engineering'].get('review_policy')=='automatic_course')
        else:
            plan=_json(files['audit_plan.json']);reviews=_json(files['audit_reviews.json'])
            validate_audit_plan(plan,tasks,spec['resolved_config']['audit_plan_sha256'])
            audit=audit_status(plan,reviews)
            audit_valid=(summary['audit_complete'] and audit['audit_complete'] and summary['engineering'].get('audit')==audit and summary['engineering'].get('audit_provenance',{}).get('reviews_sha256')==_sha(files['audit_reviews.json']))
        counts=(4400,3600,800) if spec['plan'].get('execution_scope')=='main_only' else (7600,6600,1000)
        if (state['kind']!='experiment' or state['purpose']!='formal' or state['state']!='completed'
            or spec['kind']!='experiment' or spec['purpose']!='formal' or not audit_valid
            or seal.get('synthetic') is not False or len(tasks)!=counts[0]
            or sum(t['work_type']=='generation' for t in tasks)!=counts[1]
            or sum(t['work_type']=='local_baseline' for t in tasks)!=counts[2]
            or len(summary['main_comparisons'])!=4 or not summary['metrics']
            or summary['evaluation_sha']!=evaluation_sha
            or summary['engineering'].get('evaluation_status')!='scored'
            or summary['engineering'].get('verification_kind')!='sealed_evaluation'
):
            raise ValueError()
    except (OSError,KeyError,ValueError,TypeError):
        return {'exported':False,'blocking_items':['formal_results_not_complete_and_reviewed']}
    try:
        runtime=runtime_configuration(spec)
        registration=_json(run_dir.parent.parent/'resources'/(spec['resource_set_id']+'.json'))
        resource_index=resource_sha_index(spec,summary,registration['manifest'])
    except (OSError,KeyError,ValueError,TypeError):
        return {'exported':False,'blocking_items':['runtime_configuration_not_exportable']}
    runtime_payload=json.dumps(runtime,ensure_ascii=False,indent=2).encode()
    resource_payload=json.dumps(resource_index,ensure_ascii=False,indent=2).encode()
    if any(secret and (secret.encode() in runtime_payload or secret.encode() in resource_payload) for secret in credential_values):
        raise ValueError('Credential in runtime provenance')
    files=collect_course_sources(root,credential_values=credential_values)
    files['README.md']=root/'docs/delivery/COURSE_README.md'
    files.update({'报告.pdf':root/'course/报告.pdf','项目摘要.md':root/'course/项目摘要.md','AI使用声明.md':root/'course/AI使用声明.md'})
    files.update({p.name:p for p in contributions})
    safe_summary={k:summary[k] for k in ('schema_version','run_id','input_sha','output_sha','evaluation_sha','partitions','denominators','metrics','main_comparisons','audit_complete')}
    safe_summary['engineering']={k:summary['engineering'][k] for k in ('purpose','synthetic','source_run_id','verification_kind','evaluation_status','scoring_protocol','denominator','audit','audit_provenance','attempt_count','cost_usd','review_policy','audit_status') if k in summary['engineering']}
    payload=json.dumps(safe_summary,ensure_ascii=False,indent=2).encode()
    if any(secret and secret.encode() in payload for secret in credential_values):raise ValueError('Credential in report data')
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    name=f'{metadata["leader_id"]}_{metadata["leader_name"]}_{metadata["project_name"]}.zip'
    target=destination/name
    if target.exists():return {'exported':False,'blocking_items':['export_already_exists']}
    from uuid import uuid4
    temporary=target.with_name(target.name+'.'+uuid4().hex+'.partial')
    try:
      with zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_DEFLATED) as archive:
          for name,path in sorted(files.items()):
              if not path.resolve().is_relative_to(root):raise ValueError('Unsafe report path')
              data=path.read_bytes()
              if any(secret and secret.encode() in data for secret in credential_values):raise ValueError('Credential in course document')
              archive.writestr(name,data)
          archive.writestr('results/summary.json',payload)
          archive.writestr('runtime/configuration.json',runtime_payload)
          archive.writestr('resources.json',resource_payload)
      if temporary.stat().st_size>config['max_archive_bytes']:
          temporary.unlink()
          return {'exported':False,'blocking_items':['archive_exceeds_50mb']}
      # Exclusive final publication preserves any concurrently created archive.
      import os
      os.link(temporary,target)
      temporary.unlink()
    except Exception:
      temporary.unlink(missing_ok=True)
      raise
    if target.stat().st_size>config['max_archive_bytes']:
        target.unlink()
        return {'exported':False,'blocking_items':['archive_exceeds_50mb']}
    return {'exported':True,'archive':str(target),'bytes':target.stat().st_size,
            'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'blocking_items':[]}
