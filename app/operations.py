"""Session-local handles for long checks; a handle is never automatically replayed."""
import copy
import threading
from fastapi import HTTPException
from formal_src.contracts import uuid
from .jobs import digest, now


class Operations:
    def __init__(self, diagnostics_dir=None):
        self._lock=threading.RLock()
        self._records={}
        self._diagnostics_dir=diagnostics_dir

    def get(self, operation_id):
        uuid(operation_id)
        with self._lock:
            record=self._records.get(operation_id)
            if record is None:
                raise HTTPException(404, '检查记录不在当前服务会话中；不会自动重新调用模型。请确认服务是否重启。')
            return copy.deepcopy(record['public'])

    def start(self, operation_id, kind, values, action):
        uuid(operation_id)
        identity=digest({'kind':kind,'values':values})
        with self._lock:
            old=self._records.get(operation_id)
            if old:
                if old['identity']!=identity:raise HTTPException(409, '同一检查编号不能用于不同配置')
                return copy.deepcopy(old['public'])
            if any(r['public']['state']=='running' for r in self._records.values()):
                raise HTTPException(409, '已有模型检查或实验准备正在运行，请等待原检查完成。')
            # Keep identities for the whole session so a late retry cannot re-execute.
            if len(self._records)>=512:raise HTTPException(409, '本次服务会话的检查记录已达上限，请在任务完成后重启服务。')
            record={'identity':identity,'public':{'operation_id':operation_id,'kind':kind,'state':'running','started_at':now()}}
            self._records[operation_id]=record
            def execute():
                try:
                    result=action()
                    terminal={'state':'completed','result':result}
                except Exception as exc:
                    # Error text can contain endpoint credentials or response bodies.
                    terminal={'state':'failed','message':'检查未能完成，请查看本地诊断记录；不会自动重复调用模型。','error_type':type(exc).__name__}
                    if self._diagnostics_dir is not None:
                        from formal_src.diagnostics import record_failure
                        diagnostic=record_failure(self._diagnostics_dir,exc,'preparation_check')
                        if diagnostic:terminal['diagnostic_id']=diagnostic['diagnostic_id']
                with self._lock:record['public'].update(terminal,finished_at=now())
            threading.Thread(target=execute,daemon=True,name='course-check').start()
            return copy.deepcopy(record['public'])
