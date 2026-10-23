"""Host liveness for one-shot dispatches; never retries or edits job packages.

Legacy reservations are matched to their exact -o result path. A complete ps
snapshot can prove absence. Inaccessible process state is ambiguous for at most
three hours; reconciliation lives outside frozen/sealed job directories.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from ..storage import ensure_once, sha

MAX_AMBIGUOUS_SECONDS = 3 * 3600


def process_table():
    try:
        result = subprocess.run(['ps','-axo','pid=,stat=,lstart=,command='],
            capture_output=True,text=True,timeout=10,check=False)
        if result.returncode: return None
        rows = {}
        for line in result.stdout.splitlines():
            parts=line.strip().split(None,7)
            if len(parts)!=8: continue
            try: pid=int(parts[0]); argv=shlex.split(parts[7])
            except ValueError: continue
            if parts[1].startswith('Z'): continue
            rows[pid]=dict(start=' '.join(parts[2:7]),argv=argv)
        return rows
    except (OSError,subprocess.TimeoutExpired): return None


def identity(pid):
    table=process_table()
    return table.get(pid,{}).get('start') if table is not None else None


def receipt_path(repo,reservation):
    key=hashlib.sha256(str(Path(reservation).resolve()).encode()).hexdigest()
    return Path(repo)/'data/l2_pipeline/registry/worker_reconciliations'/(key+'.json')


def age(reservation,value):
    try: started=datetime.fromisoformat(value['started_at'].replace('Z','+00:00')).timestamp()
    except (KeyError,ValueError,TypeError): started=reservation.stat().st_mtime
    return max(0,time.time()-started)


def status(repo,reservation,table=None,*,snapshot_taken=False,reconcile=False):
    reservation=Path(reservation)
    if (reservation.parent/'DISPATCH_RESULT.json').exists(): return 'done'
    receipt=receipt_path(repo,reservation)
    if receipt.exists(): return 'failed'
    value=json.loads(reservation.read_text())
    if not snapshot_taken: table=process_table()
    argv=value.get('argv',[])
    output=argv[argv.index('-o')+1] if '-o' in argv and argv.index('-o')+1<len(argv) else None
    if table is not None:
        # Match the executing CLI, never the scheduler or an unrelated PID.
        if output and any('-o' in r['argv'] and r['argv'][r['argv'].index('-o')+1:r['argv'].index('-o')+2]==[output]
                          for r in table.values()): return 'alive'
        pid=value.get('host_pid')
        host_only = pid in table and value.get('host_start')==table[pid]['start']
        # A live wrapper alone cannot prove that its Codex worker is alive.
        # It protects the startup/completion window conservatively for <=3h.
        if host_only:
            if age(reservation,value)<MAX_AMBIGUOUS_SECONDS: return 'ambiguous'
            dead=False
        else:
            dead=bool(output or (pid and value.get('host_start')))
        # A known PID with a mismatched identity is a recycled/dead host.
    else: dead=False
    state='dead' if dead else 'ambiguous'
    if state=='ambiguous' and age(reservation,value)<MAX_AMBIGUOUS_SECONDS: return state
    if reconcile:
        ensure_once(receipt,dict(schema='l2_dispatch_reconciliation_v1',status='FAILED_NO_RETRY',
            reservation=str(reservation.resolve()),reservation_sha256=sha(reservation),
            reason='Worker verifiably dead' if dead else 'Ambiguous liveness exceeded three hours',
            liveness=state,retries=0,execution_reauthorized=False,
            time=datetime.now(timezone.utc).isoformat()))
    return 'failed'


def running(repo,*,reconcile=True):
    table=process_table()
    return sum(status(repo,p,table,snapshot_taken=True,reconcile=reconcile) in {'alive','ambiguous'}
               for p in (Path(repo)/'data/l2_pipeline').rglob('DISPATCH_ONCE.json'))
