"""T226(d): list and safely retire stale hub-queue packets (hub operation, dry run by default).

A packet sits in ``SONNET_QUEUE/hub/`` when its workers were lost or handed it back. ``release_policy.dispose_hub_packet``
PARKs (or ignores) the design once and writes ``HUB_DISPOSITION.json``; after that the packet only sits there. When the
design has moved on (a later attempt, a terminal state) the packet is stale: nothing reads it for a decision.

This command moves such packets by one atomic rename into ``SONNET_QUEUE/retired_stale/`` (a sibling of ``retired_stopped/``
and ``rerouted_codex/``). It never deletes, never touches pending/in_progress/done/chunked, and writes
``RETIRED_STALE.json`` (reason, time, criteria evidence) inside the packet before the rename.

    python -B -m scripts.l2_pipeline.agents.stale_packets list   [--csv PATH] [--json] [--pre-w3-only]
    python -B -m scripts.l2_pipeline.agents.stale_packets expire [--apply] --reason TEXT [--include-current-terminal]
                                                                 [--pre-w3-only] [--job-id ID]

``expire`` without ``--apply`` is the same as ``list``. ``--apply`` needs ``L2_HUB=1`` and ``--reason`` (1 to 1000 characters).

A packet is refused (``blocked``) when any of these holds (every refusal is written in the ``blockers`` column):
  * its attempt is the design's current attempt and that design is in flight (not FINISHED / PARK / STOP);
  * its attempt is current and the design is terminal, unless ``--include-current-terminal`` is given (the park or stop
    that the packet caused still names it);
  * the design's current attempt keeps a job file that lists the packet's job path (the job is still wanted);
  * the packet is a chunk and its parent group belongs to a current in-flight attempt;
  * it holds a COMPLETION.json, belongs to a forecast/S5 persona (``packet_scope.authors`` reads hub packets), or its files
    cannot be read (never retire on doubt).
The rule is re-evaluated under the QUEUE lock (``sonnet_queue.locked``) immediately before each rename.

``retired_stale`` is deliberately NOT in ``sonnet_queue.STATES``: ``locate`` would otherwise return it and ``state()`` would
report ``running`` for a job whose packet is retired; leaving it out gives the same behaviour as ``retired_stopped``
(``locate`` is None, ``enqueue`` builds a fresh packet from the canonical job if a design still needs it).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import sonnet_queue as q

RETIRED = 'retired_stale'
# REDESIGN W3 (whole stack, second window WINDOW_REDESIGN_3B) closed and went live at 08:16:01Z on 2026-10-18
# (docs/plans/l2-pipeline/REDESIGN_W3_STATUS.md). The first window (07:01-07:16Z) was rolled back and does not count.
W3_LIVE = '2026-10-18T08:16:01+00:00'
TERMINAL = ('finished', 'park', 'stop')
COLUMNS = ['job_id', 'design_id', 'attempt_id', 'role', 'chunk', 'enqueued_at', 'hub_entered_at', 'expiries', 'releases',
           'hub_cause', 'hub_reason', 'hub_disposition', 'attempt_vs_design', 'attempt_own_end', 'design_state',
           'pre_or_post_w3', 'depends_on_it', 'dependency_detail', 'category', 'eligible']


def _read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.is_file() else default


def _iso(ts):
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec='seconds')


def _ts(text):
    return datetime.fromisoformat(text).timestamp()


class LiveResolver:
    """Design state read from the live registry with the autopilot's own functions (read only; nothing is written)."""

    def __init__(self, repo):
        from ..autopilot import Autopilot
        self.pilot = Autopilot()
        if Path(self.pilot.repo).resolve() != Path(repo).resolve():
            raise ValueError('Autopilot repo differs from the queue repo')
        self.active = {r['design_id']: r for r in
                       _read(self.pilot.registry / 'ACTIVE_DESIGNS.json', {}).get('designs', [])}

    def current(self, design_id):
        return self.active.get(design_id)

    def design_state(self, row):
        action = self.pilot.next_action(row)
        return {'FINISHED': 'finished', 'STOP': 'stop'}.get(action) or ('park' if 'PARK' in action else 'in_flight')

    def attempt_end(self, row):
        if self.pilot.stop_record(row):
            return 'stopped'
        case = self.pilot.case_dir(row)
        return 'parked' if any((case / n).exists() for n in ('PARKED_C83_FINAL.json', 'PARKED.json')) else None

    def case_dir(self, row):
        return self.pilot.case_dir(row)


def _mentions(directory, patterns):
    """Files under directory/*.json that contain any of the strings (cheap text test, like autopilot.stopped)."""
    directory = Path(directory)
    hits = []
    if directory.is_dir():
        for f in sorted(directory.glob('*.json')):
            try:
                text = f.read_text()
            except OSError:
                hits.append(f.name + '(unreadable)')
                continue
            if any(p in text for p in patterns):
                hits.append(f.name)
    return hits


def parents_of(root):
    """job_id of a chunk -> parent packet directory (chunked/*/CHUNKS.json)."""
    out = {}
    for parent in sorted((Path(root) / 'chunked').glob('*')):
        try:
            for chunk in _read(parent / 'CHUNKS.json', {}).get('chunks', []):
                out[chunk['job_id']] = parent
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def hub_entry_time(packet):
    """When the packet reached hub/: the last lease expiry or release on it (the event that moved it)."""
    packet = Path(packet)
    times = []
    state = _read(packet / 'STATE.json', {}) or {}
    tr = state.get('expiry_transition')
    if tr and tr.get('status') == 'hub':
        times.append(tr['time'])
    for pattern in ('EXPIRED_*.json', 'RELEASED_*.json'):
        for f in packet.glob(pattern):
            if re.fullmatch(r'(EXPIRED|RELEASED)_\d+\.json', f.name):
                v = _read(f, {})
                if 'time' in v:
                    times.append(v['time'])
    return max(times) if times else None


def evaluate(root, packet, resolver, parents=None, w3_live=W3_LIVE):
    """Everything known about one hub packet, and whether it may be retired. Pure read."""
    root, packet = Path(root), Path(packet)
    parents = parents if parents is not None else parents_of(root)
    info = dict(job_id=packet.name, blockers=[], eligible=False)
    try:
        binding = _read(packet / 'PACKET.json')
        state = _read(packet / 'STATE.json')
        row = binding['row']
        info.update(design_id=row['design_id'], attempt_id=row['attempt_id'],
                    role=(binding.get('chunk') or {}).get('role') or '', chunk=(binding.get('chunk') or {}).get('index', ''),
                    enqueued_at=_iso(state.get('enqueued_at')), expiries=state.get('expiries', 0))
        job_path = binding['job_path']
        entered = hub_entry_time(packet)
        info['hub_entered_at'] = _iso(entered)
        info['releases'] = len([p for p in packet.glob('RELEASED_*.json') if re.fullmatch(r'RELEASED_\d+\.json', p.name)])
        info['hub_cause'] = _cause(packet)
        info['hub_reason'] = q.hub_reason(packet)[:160]
        disp = _read(packet / 'HUB_DISPOSITION.json', {}) or {}
        info['hub_disposition'] = disp.get('disposition', '')
        info['pre_or_post_w3'] = ('unknown' if entered is None else 'pre_w3' if entered < _ts(w3_live) else 'post_w3')
        current = resolver.current(row['design_id'])
        same = current is not None and current.get('attempt_id') == row['attempt_id']
        info['attempt_vs_design'] = 'current' if same else ('superseded' if current is not None else 'design_not_active')
        info['attempt_own_end'] = resolver.attempt_end(row) or 'none'
        info['design_state'] = resolver.design_state(current) if current is not None else 'none'
        detail = []
        live = _mentions(resolver.case_dir(current) / 'jobs', [job_path, packet.name]) if current is not None else []
        if live:
            detail.append('current attempt job files: ' + ','.join(live))
        cps = _mentions(resolver.case_dir(row) / 'actions', [job_path, packet.name])
        if cps:
            detail.append('checkpoints of the packet attempt: ' + ','.join(cps))
        parent = parents.get(packet.name)
        if parent is not None:
            pstate = _read(parent / 'PACKET.json', {}) or {}
            detail.append('chunk parent ' + parent.name + ' reads it via group_state (parent attempt '
                          + str((pstate.get('row') or {}).get('attempt_id')) + ')')
        info['dependency_detail'] = '; '.join(detail)
        if (packet / 'COMPLETION.json').exists():
            info['blockers'].append('has COMPLETION.json')
        try:
            from .packet_scope import persona
            if persona(binding) is not None:
                info['blockers'].append('forecast/S5 persona packet (packet_scope.authors reads hub)')
        except (OSError, ValueError, KeyError):
            info['blockers'].append('persona unreadable')
        if same and info['design_state'] == 'in_flight':
            info['blockers'].append('attempt is current and design is in flight')
        elif same:
            info['category'] = 'current_terminal'
        if live and not same:
            info['blockers'].append('current attempt still lists the job')
        if parent is not None and same and info['design_state'] == 'in_flight':
            info['blockers'].append('chunk parent group_state of a live attempt reads it')
        info['depends_on_it'] = 'yes' if info['blockers'] else 'no'
        info.setdefault('category', 'superseded' if not same else 'current_terminal')
        if info['blockers']:
            info['category'] = 'blocked'
        info['eligible'] = not info['blockers']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        info['blockers'].append('unreadable: ' + type(exc).__name__ + ': ' + str(exc)[:120])
        info.update(category='blocked', depends_on_it='unknown', eligible=False)
    return info


def _cause(packet):
    from .. import release_policy
    return release_policy.story(packet)['cause']


def scan(root, resolver, w3_live=W3_LIVE):
    root = Path(root)
    hub = root / 'hub'
    parents = parents_of(root)
    return [evaluate(root, p, resolver, parents, w3_live) for p in sorted(hub.iterdir())] if hub.is_dir() else []


def summary(rows):
    out = dict(hub_total=len(rows))
    for key in ('category', 'pre_or_post_w3', 'attempt_vs_design', 'design_state', 'hub_cause'):
        counts = {}
        for r in rows:
            counts[r.get(key, '')] = counts.get(r.get(key, ''), 0) + 1
        out[key] = counts
    out['eligible'] = sum(1 for r in rows if r['eligible'])
    return out


def _select(info, include_current_terminal, pre_w3_only):
    if not info['eligible']:
        return False
    if info['category'] == 'current_terminal' and not include_current_terminal:
        return False
    if pre_w3_only and info['pre_or_post_w3'] != 'pre_w3':
        return False
    return True


def expire(root, resolver, reason=None, apply=False, include_current_terminal=False, pre_w3_only=False, job_id=None,
           now=None, w3_live=W3_LIVE):
    """Retire the stale hub packets (plan unless ``apply``). Returns dict(applied, moved, skipped)."""
    root = Path(root)
    now = time.time() if now is None else now
    reason = str(reason or '').strip()
    if apply and not 1 <= len(reason) <= 1000:
        raise ValueError('A reason of 1 to 1000 characters is required')
    if job_id is not None and not re.fullmatch(r'[a-f0-9]{64}', job_id):
        raise ValueError('Invalid packet ID')
    moved, skipped = [], []
    with q.locked(root):
        archive = root / RETIRED
        for packet in sorted((root / 'hub').iterdir()) if (root / 'hub').is_dir() else []:
            if job_id is not None and packet.name != job_id:
                continue
            info = evaluate(root, packet, resolver, None, w3_live)  # fresh, under the lock
            if not _select(info, include_current_terminal, pre_w3_only):
                skipped.append(dict(job_id=packet.name, design_id=info.get('design_id'), category=info.get('category'),
                                    why='; '.join(info['blockers']) or 'not selected (' + info.get('category', '') + ', '
                                    + info.get('pre_or_post_w3', '') + ')'))
                continue
            moved.append(dict(job_id=packet.name, design_id=info['design_id'], attempt_id=info['attempt_id'],
                              category=info['category'], pre_or_post_w3=info['pre_or_post_w3']))
            if not apply:
                continue
            archive.mkdir(exist_ok=True)
            mark = packet / 'RETIRED_STALE.json'
            if not mark.exists():  # a crash between the record and the rename is replayed with the same record
                evidence = {k: info.get(k) for k in COLUMNS if k not in ('eligible',)}
                q.atomic(mark, dict(schema='l2_retired_stale_v1', reason=reason, retired_at=now, retired_at_iso=_iso(now),
                                    w3_live=w3_live, criteria=dict(
                                        rule='hub packet whose attempt is superseded (or current but terminal with explicit flag), '
                                             'nothing live depends on it',
                                        include_current_terminal=include_current_terminal, pre_w3_only=pre_w3_only),
                                    evidence=evidence, bytes_retained=True))
            target = archive / packet.name
            n = 1
            while target.exists():
                n += 1
                target = archive / (packet.name + '.' + str(n))
            os.rename(packet, target)
            moved[-1]['packet'] = str(target)
    return dict(applied=bool(apply), moved=moved, skipped=skipped)


def write_csv(rows, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction='ignore')
        writer.writeheader()
        for r in rows:
            writer.writerow({**r, 'depends_on_it': r.get('depends_on_it', ''),
                             'dependency_detail': (r.get('dependency_detail', '') + (' | BLOCKERS: ' + '; '.join(r['blockers'])
                                                                                     if r.get('blockers') else '')).strip(' |')})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['list', 'expire'])
    repo_default = Path(__file__).resolve().parents[3]
    parser.add_argument('--root', type=Path, default=q.root_for(repo_default))
    parser.add_argument('--apply', action='store_true', help='expire: do it (without it the command only lists)')
    parser.add_argument('--reason')
    parser.add_argument('--job-id')
    parser.add_argument('--csv', type=Path, help='list: write one row per hub packet')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--pre-w3-only', action='store_true')
    parser.add_argument('--include-current-terminal', action='store_true')
    args = parser.parse_args(argv)
    if args.apply and args.action != 'expire':
        parser.error('--apply belongs to expire')
    if args.apply and os.environ.get('L2_HUB') != '1':
        parser.error('expire --apply is a hub operation: set L2_HUB=1')
    if args.apply and not args.reason:
        parser.error('--reason required with --apply')
    repo = args.root.parents[3]
    resolver = LiveResolver(repo)
    if args.action == 'list':
        rows = scan(args.root, resolver)
        if args.csv:
            write_csv(rows, args.csv)
        print(json.dumps(dict(summary=summary(rows), **({'rows': rows} if args.json else {})), indent=2))
        return 0
    value = expire(args.root, resolver, args.reason, args.apply, args.include_current_terminal, args.pre_w3_only, args.job_id)
    print(json.dumps(value, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
