"""Once-only study reservation is independent of run ID/output choice.
Approval validation precedes reservation; reservation precedes outcome loading.
Unexpected exceptions persist verbatim and consume the attempt permanently.
"""
from __future__ import annotations
import traceback
from pathlib import Path
from typing import Callable, Any
from .freeze import require_verified
from .storage import write_once

class DeclaredStop(ValueError): pass

def execute_once(root: Path, signer: str, loader: Callable[[],Any], estimator: Callable[[Any],dict]) -> dict:
    from . import snapshots
    if snapshots.manifest_for(root) and snapshots.active() is None:
        require_verified(root, 'final', signer)
        raise RuntimeError('Snapshot registered runner required; live callbacks are refused')
    check=require_verified(root,"final",signer)
    # Canonical study root owns a single reservation, independent of result names.
    write_once(root/"EXECUTION_ONCE.json",{"manifest_sha256":check["manifest_sha256"],"status":"ATTEMPT_CONSUMED"})
    try:
        data=loader()
        result=estimator(data)
        if not isinstance(result,dict): raise TypeError("Estimator must return a result record")
        write_once(root/"RESULT.json",result)
        terminal_status="STOPPED" if result.get("status")=="STOPPED" else "COMPLETED"
        write_once(root/"TERMINAL.json",{"status":terminal_status,"reason":result.get("reason"),"manifest_sha256":check["manifest_sha256"]})
        return result
    except DeclaredStop as exc:
        result=getattr(exc,"result",None) or {"status":"STOPPED","reason":str(exc)}
        write_once(root/"RESULT.json",result); write_once(root/"TERMINAL.json",result)
        return result
    except BaseException as exc:
        failure={"status":"FAILED_NO_RETRY","type":type(exc).__name__,"message":str(exc),"traceback":traceback.format_exc(),"manifest_sha256":check["manifest_sha256"]}
        partial=getattr(exc,"result",None)
        try: write_once(root/"FAILURE.json",failure)
        except BaseException: pass  # Reservation remains consumed even if disk/persistence fails.
        if isinstance(partial,dict):
            try: write_once(root/"RESULT.json",partial)
            except BaseException: pass
        raise
