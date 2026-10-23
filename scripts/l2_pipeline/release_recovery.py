"""F11 (REDESIGN W3 follow-up, 2026-10-18): reopen the designs the queue STOPped in the 2026-10-18 release incident.

Between 2026-10-18T02:30Z and 04:40Z the worker task released packets that no worker could finish (51-state history packets,
scanned PDFs, reviewer sweeps); a packet released three times went to the hub queue and the hub queue STOPped its design:
``Sonnet packet released by workers 3 times``. Nothing was wrong with those designs. After F7 (history packets of at most 10
jurisdictions), F8 (``evidence-text``), F9 (compact review pack) and F10 (a queue event PARKs, never STOPs) are live, this
module puts them back.

Each design whose **current attempt** has a STOP record written at or after ``SINCE`` whose reason is a queue event
(``Sonnet packet released by workers N times`` or ``Sonnet lease expired twice/three times``) goes through the existing
card-bound requeue as a new attempt: the new card is the old card with only its version bumped (written beside it so relative
source paths stay valid), decision ``resume``, superseded status ``SUPERSEDED_RELEASE_RECOVERY`` (whitelisted in
``requeue``). The requeue keeps every completed legal gate (history, reconcile, audit) and restarts at the first undone
action; nothing of the old attempt is deleted or edited, nothing is dispatched or fitted. With ``include_parks`` it also
reopens designs F10 PARKED with ``QUEUE_PARK`` (any date). A STOP with another reason (a science finding, a collector
failure) is never touched. A design the requeue refuses (frozen or sealed: ``requeue`` does not reopen those) is listed in the
receipt as ``kept`` with the reason: it needs its own ruling, not a card change.

With ``--worker-side-only`` (worker-fix G4) a QUEUE_PARK that is not a worker-side failure (a structural release: too_large, tool_missing,
input_unreadable) stays in the ``kept`` list: a new attempt would meet the same wall.

Only inside a stopped code window (``CODE_TASK.lock`` names a ``WINDOW_*`` task, ``AUTOPILOT_STOP`` set, clean stop observed).
Idempotent: a design that already has a new attempt no longer qualifies (a repeated call lists nothing); each call writes one
numbered receipt under ``data/l2_pipeline/autopilot/release_recovery/applications/``; ``plan`` is read-only.
"""
from __future__ import annotations

import inspect
import json
import re
from datetime import datetime
from pathlib import Path

from .storage import ensure_once, sha

RULING = 'docs/plans/l2-pipeline/HUB_ORDER_20261018_RELEASE_RECOVERY.md'
STATUS = 'SUPERSEDED_RELEASE_RECOVERY'
SINCE = '2026-10-18T02:30:00+00:00'
REASON = re.compile(r'Sonnet packet released by workers \d+ times|Sonnet lease expired (?:twice|three times|\d+ times)')
FROZEN = ('FREEZE_PREP.json', 'FREEZE_FINAL.json', 'APPROVAL_PREP.json', 'APPROVAL_FINAL.json', 'TERMINAL.json')


def live():
    """F7-F10 must be in the code that runs this; the recovery refuses otherwise (reopened designs would hit the same walls)."""
    try:
        from .agents import evidence_fetch, history_chunks, review_pack, sonnet_queue as q
        from . import release_policy
    except ImportError as exc:
        raise ValueError('F7-F10 are not live: ' + str(exc)) from exc
    checks = (('F7 chunked queue state', 'chunked' in q.STATES and hasattr(history_chunks, 'merge')),
              ('F8 evidence-text', hasattr(evidence_fetch, 'evidence_text')),
              ('F9 review pack', hasattr(review_pack, 'build')),
              ('F10 release kinds', 'kind' in inspect.signature(q.release).parameters),
              ('F10 queue park', hasattr(release_policy, 'dispose_hub_packet')))
    missing = [name for name, ok in checks if not ok]
    if missing:
        raise ValueError('F7-F10 are not live: ' + ', '.join(missing))


def _when(text):
    value = datetime.fromisoformat(text)
    return value if value.tzinfo else value.replace(tzinfo=datetime.fromisoformat(SINCE).tzinfo)


def _matches(pilot, row, since, include_parks):
    """(kind, record) when this attempt ended on a queue event, else None."""
    from .autopilot import read
    stop = pilot.stop_record(row)
    if stop:
        if _when(stop['time']) >= _when(since) and REASON.search(str(stop.get('reason', ''))):
            return 'STOP', stop
        return None
    if include_parks:
        park = read(pilot.case_dir(row) / 'PARKED.json', {}) or {}
        if (park.get('metrics') or {}).get('kind') == 'QUEUE_PARK' and pilot.next_action(row) == 'PARK':
            return 'PARK', park
    return None


def worker_side(park):
    """Worker-fix G4: a QUEUE_PARK that only says workers were lost (lease expiry, or worker-kind releases), nothing about the packet."""
    metrics = park.get('metrics') or {}
    return metrics.get('cause') == 'lease_expired' or (metrics.get('cause') == 'released_repeatedly' and metrics.get('release_kind') == 'worker')


def plan(pilot, since=SINCE, include_parks=False, worker_side_only=False):
    """Read-only: ``candidates`` (rows to requeue) and ``kept`` (rows that cannot be requeued, with the reason).

    ``worker_side_only`` (G4): a QUEUE_PARK that is not a worker-side failure (a structural release: too_large, tool_missing,
    input_unreadable) is listed in ``kept`` instead; the same wall would stop the new attempt, so it needs its own look."""
    from .autopilot import read
    from .c70_l3 import protected
    candidates, kept = [], []
    for row in read(pilot.registry / 'ACTIVE_DESIGNS.json')['designs']:
        found = _matches(pilot, row, since, include_parks)
        if not found:
            continue
        root = pilot.backend.root(row)
        if worker_side_only and found[0] == 'PARK' and not worker_side(found[1]):
            kept.append(dict(design_id=row['design_id'], attempt_id=row['attempt_id'], kind=found[0],
                             why='structural queue park (not a worker-side failure): a new attempt would meet the same wall; needs its own look',
                             action=found[1].get('action'), reason=str(found[1].get('reason', ''))[:200]))
        elif protected(root) or any((root / name).exists() for name in FROZEN):
            kept.append(dict(design_id=row['design_id'], attempt_id=row['attempt_id'], kind=found[0],
                             why='frozen or sealed: the requeue route does not reopen it; needs its own ruling',
                             action=found[1].get('action'), reason=str(found[1].get('reason', ''))[:200]))
        else:
            candidates.append(dict(row=row, kind=found[0], record=found[1]))
    return candidates, kept


def apply(pilot=None, task=None, since=SINCE, include_parks=False, worker_side_only=False):
    """Requeue every candidate once. Returns dict(records, kept, refused, receipt, requeue_receipts)."""
    from .autopilot import Autopilot, read
    from .ops.watchdog import clean_stop, guard
    from .requeue import requeue
    live()
    p = pilot or Autopilot()
    out = p.repo / 'data/l2_pipeline/autopilot/release_recovery'
    authority = p.repo / RULING
    with guard(p.registry):
        held = read(p.registry / 'CODE_TASK.lock', {}).get('task')
        if not isinstance(held, str) or not held.startswith('WINDOW_') or (task is not None and held != task):
            raise ValueError('A stopped WINDOW_* code window is required (CODE_TASK.lock: ' + str(held) + ')')
        if not (p.registry / 'AUTOPILOT_STOP').exists():
            raise ValueError('A stopped WINDOW_* code window is required (AUTOPILOT_STOP missing)')
        clean_stop(p.repo)
        candidates, kept = plan(p, since, include_parks, worker_side_only)
        records, refused = [], []
        for item in candidates:
            row = item['row']
            payload = read(row['card'])
            payload['version'] = 'v' + str(int(payload['version'][1:]) + 1)
            card = ensure_once(Path(row['card']).with_name(Path(row['card']).stem + '_' + payload['version'] + '.json'), payload)
            ruling = ensure_once(out / 'rulings' / (row['design_id'] + '__' + payload['version'] + '.json'), dict(
                schema='l2_requeue_ruling_v1', design_id=row['design_id'], prior_card_sha256=sha(row['card']), card_sha256=sha(card),
                decision='resume', superseded_status=STATUS, prior_record=dict(kind=item['kind'], action=item['record'].get('action'),
                                                                                 reason=str(item['record'].get('reason', ''))[:300]),
                authority=dict(path=str(authority.resolve()), sha256=sha(authority)),
                reason='Release incident 2026-10-18: the design was ' + ('STOPped' if item['kind'] == 'STOP' else 'PARKed')
                       + ' by a queue event (packet released or lease expired), not by a check on the design; F7-F10 are live; '
                       'card unchanged except its version',
                execution_authorized=False))
            try:
                records.append(requeue(row['design_id'], card, ruling, p))
            except ValueError as exc:   # one design's refusal never stops the others (nothing was written for it)
                refused.append(dict(design_id=row['design_id'], attempt_id=row['attempt_id'], why=str(exc)[:300]))
        value = dict(schema='l2_release_recovery_v1', since=since, include_parks=include_parks, worker_side_only=worker_side_only,
                     records=[dict(design_id=r['design_id'], new_attempt=r['new_row']['attempt_id'], restart_action=r['restart_action'],
                                   retained_actions=r['retained_actions']) for r in records],
                     kept=kept, refused=refused, dispatches=0, actual_data_estimation_calls=0)
        earlier = sorted((out / 'applications').glob('APPLICATION_*.json')) if (out / 'applications').is_dir() else []
        run = out / 'applications' / ('APPLICATION_%03d.json' % (len(earlier) + 1))   # one receipt per call, in order
        ensure_once(run, value)
        return dict(value, receipt=str(run), requeue_receipts=records)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'apply'])
    parser.add_argument('--task', help='task name in CODE_TASK.lock (default: any WINDOW_* task)')
    parser.add_argument('--include-parks', action='store_true', help='also reopen designs F10 PARKed as QUEUE_PARK')
    parser.add_argument('--worker-side-only', action='store_true', help='with --include-parks: leave structural queue parks (too_large, tool_missing, input_unreadable) in the kept list')
    parser.add_argument('--since', default=SINCE)
    args = parser.parse_args(argv)
    from .autopilot import Autopilot
    pilot = Autopilot()
    if args.command == 'plan':
        candidates, kept = plan(pilot, args.since, args.include_parks, args.worker_side_only)
        print(json.dumps(dict(candidates=[dict(design_id=c['row']['design_id'], attempt_id=c['row']['attempt_id'], kind=c['kind'],
                                              action=c['record'].get('action'), reason=str(c['record'].get('reason', ''))[:160])
                                         for c in candidates], kept=kept), indent=2))
    else:
        result = apply(pilot, args.task, args.since, args.include_parks, args.worker_side_only)
        print(json.dumps({k: v for k, v in result.items() if k != 'requeue_receipts'}, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
