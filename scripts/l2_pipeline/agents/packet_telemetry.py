"""T226(a): per-packet telemetry for the Sonnet queue. Read-only; one CSV / JSONL summary command.

    python -B -m scripts.l2_pipeline.agents.packet_telemetry [--root QUEUE_ROOT] [--csv PATH] [--jsonl PATH]
        [--by none|design|day] [--since ISO_UTC] [--states done,hub,...]

One row per packet: claim and finish timestamps, minutes, worker(s), verdict, the gate where the design attempt left the chain,
and the token count when the worker reported it (``finish --tokens N``; optional, absent = empty). Nothing here writes
into the queue or the registry; a packet whose files cannot be read still gets a row with what could be read.
"""
from __future__ import annotations
import argparse, csv, glob, json, sys
from datetime import datetime, timezone
from pathlib import Path

STATES = ('pending', 'in_progress', 'done', 'hub', 'chunked', 'retired_stopped', 'rerouted_codex', 'retired_stale')
FIELDS = ['job_id', 'state', 'design_id', 'attempt_id', 'role', 'chunk', 'chunk_total', 'enqueued_at', 'first_claimed_at',
          'last_claimed_at', 'finished_at', 'wait_minutes', 'minutes', 'queue_to_finish_minutes', 'worker', 'workers',
          'leases', 'expiries', 'releases', 'hub_returns', 'verdict', 'verdict_detail', 'gate_exit', 'gate_exit_reason',
          'tokens']


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if isinstance(ts, (int, float)) else ''


def _minutes(a, b):
    return round((b - a) / 60, 1) if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b >= a else ''


def gate_exit(root, row):
    """(gate, reason) where the design attempt left the chain: PARKED / STOP record, FINISHED, or in_flight."""
    try:
        case = Path(root).parent.parent / 'autopilot' / row['design_id'] / row['attempt_id']
    except (KeyError, TypeError):
        return '', ''
    for name, label in (('PARKED_C83_FINAL.json', 'PARK'), ('PARKED.json', 'PARK'), ('STOP_C84.json', 'STOP'),
                        ('STOP_C70.json', 'STOP'), ('STOP.json', 'STOP')):
        value = _read(case / name)
        if value:
            return (label + ':' + str(value.get('action') or '')), str(value.get('reason') or '')[:160]
    if (case / 'actions' / 'register.json').is_file():
        return 'FINISHED', ''
    return ('in_flight' if case.is_dir() else 'unknown_attempt'), ''


def verdict_of(packet, binding, completion):
    sha = (completion or {}).get('output_sha256')
    out_dir = Path(binding.get('output_path', '')).parent
    candidates = [out_dir / ('SELF_CHECK_' + sha + '.json')] if sha else []
    if not candidates or not candidates[0].is_file():
        candidates = sorted(glob.glob(str(out_dir / 'SELF_CHECK_*.json')), key=lambda p: Path(p).stat().st_mtime)[-1:]
    for path in candidates:
        value = _read(path)
        if isinstance(value, dict):
            if value.get('validated') is True:
                return 'validated', ''
            return 'not_validated', str(value.get('error') or '')[:160]
    return ('no_self_check' if completion else ''), ''


def packet_row(root, state, packet):
    packet = Path(packet)
    binding = _read(packet / 'PACKET.json') or {}
    st = _read(packet / 'STATE.json') or {}
    lease = _read(packet / 'LEASE.json') or {}
    completion = _read(packet / 'COMPLETION.json')
    tele = _read(packet / 'TELEMETRY.json') or {}
    row = binding.get('row') or {}
    chunk = binding.get('chunk') or {}
    leases = []
    for path in sorted(packet.glob('EXPIRED_*.json')):
        v = (_read(path) or {}).get('lease') or {}
        leases.append(v)
    releases = [(_read(p) or {}) for p in sorted(packet.glob('RELEASED_*.json'))]
    for r in releases:
        if r.get('lease'):
            leases.append(r['lease'])
    if lease:
        leases.append(lease)
    claims = [l.get('claimed_at') for l in leases if isinstance(l.get('claimed_at'), (int, float))]
    workers = []
    for l in leases:
        if l.get('worker') and l['worker'] not in workers:
            workers.append(l['worker'])
    finished = (completion or {}).get('completed_at')
    verdict, detail = verdict_of(packet, binding, completion)
    if not verdict and state == 'hub':
        verdict = 'hub'
    gate, reason = gate_exit(root, row)
    enq = st.get('enqueued_at')
    last_claim = max(claims) if claims else None
    first_claim = min(claims) if claims else None
    return dict(job_id=packet.name, state=state, design_id=row.get('design_id', ''), attempt_id=row.get('attempt_id', ''),
        role=chunk.get('role', ''), chunk=chunk.get('index', ''), chunk_total=chunk.get('total', ''),
        enqueued_at=_iso(enq), first_claimed_at=_iso(first_claim), last_claimed_at=_iso(last_claim), finished_at=_iso(finished),
        wait_minutes=_minutes(enq, first_claim), minutes=_minutes(last_claim, finished),
        queue_to_finish_minutes=_minutes(enq, finished), worker=(lease or {}).get('worker') or (workers[-1] if workers else ''),
        workers='|'.join(workers), leases=len(leases) if leases else 0, expiries=st.get('expiries', 0),
        releases=st.get('releases', 0), hub_returns=st.get('returns', 0), verdict=verdict, verdict_detail=detail,
        gate_exit=gate, gate_exit_reason=reason, tokens=tele.get('tokens', ''))


def rows(root, states=STATES, since=None):
    out = []
    for state in states:
        base = Path(root) / state
        if not base.is_dir():
            continue
        for packet in sorted(base.iterdir()):
            if not packet.is_dir() or packet.name.startswith('.'):
                continue
            out.append(packet_row(root, state, packet))
    if since:
        out = [r for r in out if (r['finished_at'] or r['last_claimed_at'] or r['enqueued_at']) >= since]
    return out


def summarize(all_rows, by='none'):
    def total(group):
        done = [r for r in group if r['minutes'] != '']
        tok = [r['tokens'] for r in group if r['tokens'] != '']
        return dict(packets=len(group), finished=sum(1 for r in group if r['finished_at']),
                    validated=sum(1 for r in group if r['verdict'] == 'validated'),
                    not_validated=sum(1 for r in group if r['verdict'] == 'not_validated'),
                    expiries=sum(int(r['expiries'] or 0) for r in group),
                    minutes=round(sum(r['minutes'] for r in done), 1),
                    median_minutes=sorted(r['minutes'] for r in done)[len(done) // 2] if done else '',
                    tokens_reported=len(tok), tokens=sum(tok))
    if by == 'none':
        return {'all': total(all_rows)}
    key = (lambda r: r['design_id']) if by == 'design' else (lambda r: (r['finished_at'] or r['enqueued_at'])[:10])
    groups = {}
    for r in all_rows:
        groups.setdefault(key(r), []).append(r)
    return {k: total(v) for k, v in sorted(groups.items())}


def main(argv=None):
    here = Path(__file__).resolve().parents[3]
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=here / 'data/l2_pipeline/registry/SONNET_QUEUE')
    p.add_argument('--csv', type=Path)
    p.add_argument('--jsonl', type=Path)
    p.add_argument('--by', choices=['none', 'design', 'day'], default='none')
    p.add_argument('--since', help='ISO UTC prefix, e.g. 2026-10-20')
    p.add_argument('--states', default=','.join(STATES))
    a = p.parse_args(argv)
    data = rows(a.root, tuple(s for s in a.states.split(',') if s), a.since)
    if a.csv:
        with a.csv.open('w', newline='') as h:
            w = csv.DictWriter(h, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(data)
    if a.jsonl:
        a.jsonl.write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in data))
    json.dump(dict(rows=len(data), summary=summarize(data, a.by), csv=str(a.csv) if a.csv else None,
                   jsonl=str(a.jsonl) if a.jsonl else None), sys.stdout, indent=1)
    print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
