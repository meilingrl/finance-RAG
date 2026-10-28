"""Bounded diagnostics containing code locations, never messages or local values."""
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
from uuid import uuid4


def record_failure(directory, error, stage):
    frames=[]
    trace=error.__traceback__
    while trace is not None:
        code=trace.tb_frame.f_code
        frames.append({'file':Path(code.co_filename).name[:120],
                       'function':code.co_name[:120],'line':trace.tb_lineno})
        trace=trace.tb_next
    record={'diagnostic_id':str(uuid4()),'time':datetime.now(timezone.utc).isoformat(),
            'stage':stage[:80],'exception_type':type(error).__name__[:120],'frames':frames[-20:]}
    temporary=None
    try:
        directory=Path(directory)
        directory.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=directory,
                                         prefix='.diagnostic-',delete=False) as output:
            temporary=Path(output.name)
            json.dump(record,output,ensure_ascii=True)
            output.flush();os.fsync(output.fileno())
        os.replace(temporary,directory/'diagnostics.json')
        return record
    except OSError:
        # A diagnostic failure must not change experiment state or error handling.
        if temporary is not None:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass
        return None
