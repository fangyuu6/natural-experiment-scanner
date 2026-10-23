"""Host-only 120-second worker observer; durable evidence stays in data."""
import time
from datetime import datetime
from .autopilot import Autopilot, REGISTRY, read, atomic, utc
from .agents.dispatch import require_host
from .agents import liveness
ROOT=REGISTRY.parent/'autopilot/c44'


def main():
    require_host(True)
    receipt=read(ROOT/'RESTART_RECEIPT.json')
    from datetime import datetime
    wait=max(0,datetime.fromisoformat(receipt['time']).timestamp()+120-time.time())
    time.sleep(wait)
    p=Autopilot();table=liveness.process_table()
    if table is None:raise ValueError('Host process snapshot unavailable; real workers not verifiable')
    workers=[]
    for reservation in (p.repo/'data/l2_pipeline').rglob('DISPATCH_ONCE.json'):
        if (reservation.parent/'DISPATCH_RESULT.json').exists():continue
        state=liveness.status(p.repo,reservation,table,snapshot_taken=True)
        if state=='alive':workers.append(str(reservation))
    atomic(ROOT/'TWO_MINUTE_WORKERS.json',dict(time=utc(),
        elapsed_seconds=time.time()-datetime.fromisoformat(receipt['time']).timestamp(),
        verified_alive=len(workers),workers=workers,scheduler_pid=receipt['scheduler_pid'],
        scheduler_alive=p.backend.alive(receipt['scheduler_pid'])))
    print('verified workers',len(workers))


if __name__=='__main__': main()
