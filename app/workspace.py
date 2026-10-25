"""Explicit output-workspace selection, applied on the next server launch."""
import json
from pathlib import Path


def resolve_workspace(project_root, override=None):
    root=Path(project_root).resolve()
    if override is not None:return Path(override).expanduser().resolve()
    settings=root/'artifacts/app-entry.json'
    if settings.exists():return Path(json.loads(settings.read_text())['workspace']).resolve()
    return root/'artifacts/formal-v1'


def configure_workspace(project_root, selected, current_workspace):
    root=Path(project_root).resolve();target=Path(selected).expanduser()
    if not target.is_absolute():raise ValueError('An absolute output directory is required')
    target=target.resolve()
    if root.is_relative_to(target) or target.is_relative_to(root) and not target.is_relative_to(root/'artifacts'):
        raise ValueError('Output workspace must not contain the project or overwrite its assets')
    if target.exists() and (not target.is_dir() or any(target.iterdir()) and not (target/'runs').is_dir()):
        raise ValueError('Choose an empty directory or an existing application workspace')
    path=root/'artifacts/app-entry.json';path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({'workspace':str(target)},indent=2)+'\n');temporary.replace(path)
    return {'output_directory':str(target),'restart_required':target!=Path(current_workspace).resolve(),
            'current_output_directory':str(Path(current_workspace).resolve())}
