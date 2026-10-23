"""Per-reason breakdown of tick blockers (logging only; no scheduling decision reads it).

T202 / WINDOW_PLAN_20261021 item 2. The IDLE ledger row keeps its `blockers` counts exactly as before
(the watchdog idle proof compares them). This module adds a separate `blocker_reasons` mapping and a
`STARVED_BLOCKED` note naming the top open reason when the queue is empty and nothing can run.
Every function here is best effort: a failure returns an empty result and never breaks the tick.
"""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

# Ordered: first matching rule wins. Text is matched case-insensitively against the tick row's `reason`.
RULES = (
    ('NOVEL_GATE', ('novel/science', 'review should requires', 'hub closure')),
    ('PREP_DEPENDENCY', ('missing dependency',)),
    ('QUEUE_PARK', ('queue_park', 'sonnet lease expired', 'released by workers', 'hub_queue required')),
    ('HISTORY_FINAL', ('insufficient_work', 'official history', 'source id/url invalid')),
    ('MUST_OPEN', ('must>', 'non-pass draft_readiness')),
    ('SUPPORT_OR_RI_GATE', ('registered support insufficient', 'feasibility below', 'below minimum',
                            'audit unavailable', 'fresh audit did not pass', 'insufficient_pre')),
    ('CARD_BOUND_S0_S2', ('no local public files', 'missing declared public file', 'first-order outcome',
                          'source path differs', 'laus ratio card', 'rate default requires', 'outcome.paths')),
)
SPECIAL = {'FINISHED': 'FINISHED', 'GATE0_PARK': 'GATE0_PARK', 'PRESCREEN_PARK': 'PRESCREEN_PARK'}
NON_RUNNABLE = frozenset({'PARK', 'STOP', 'FINISHED', 'PRESCREEN_PARK', 'GATE0_PARK'})
NOT_OPEN = frozenset({'FINISHED', 'GATE0_PARK', 'PRESCREEN_PARK', 'CLOSED_FINAL'})


def classify(entry, closed=frozenset()):
    """One tick report row -> reason label. Rows that are not blocked count as RUNNABLE."""
    nxt = entry.get('next')
    if nxt in SPECIAL:
        return SPECIAL[nxt]
    if nxt not in ('PARK', 'STOP'):
        return 'RUNNABLE'
    if entry.get('design_id') in closed:
        return 'CLOSED_FINAL'
    text = str(entry.get('reason') or '').lower()
    for label, needles in RULES:
        if any(n in text for n in needles):
            return label
    return 'PARK_OTHER' if nxt == 'PARK' else 'STOP_OTHER'


def closed_designs(registry):
    """design_id set of every final-decision ledger (FINAL_DECISIONS_*.json); empty when absent."""
    found = set()
    try:
        for path in sorted(Path(registry).glob('FINAL_DECISIONS_*.json')):
            found.update(r['design_id'] for r in json.loads(path.read_text()).get('designs', []))
    except (OSError, ValueError, KeyError, TypeError):
        return frozenset()
    return frozenset(found)


def breakdown(report, closed=frozenset()):
    """(counts, members): label -> n and label -> sorted design ids."""
    members = {}
    for entry in report:
        members.setdefault(classify(entry, closed), []).append(entry.get('design_id'))
    return ({k: len(v) for k, v in sorted(members.items())},
            {k: sorted(map(str, v)) for k, v in sorted(members.items())})


def idle_extra(pilot, report):
    """Extra fields for the IDLE row. Never raises."""
    try:
        counts, _ = breakdown(report, closed_designs(pilot.registry))
        return dict(blocker_reasons=counts)
    except Exception:
        return {}


def queue_counts(repo):
    """(pending, in_progress) of the Sonnet queue; (0, 0) when no queue exists; None when unreadable."""
    root = Path(repo) / 'data/l2_pipeline/registry/SONNET_QUEUE'
    try:
        if not root.is_dir():
            return (0, 0)
        count = lambda name: sum(1 for _ in (root / name).iterdir()) if (root / name).is_dir() else 0
        return (count('pending'), count('in_progress'))
    except OSError:
        return None


def note_starved(pilot, report):
    """Write STARVED_BLOCKED when nothing is queued, running or runnable. Returns True when a row was written."""
    try:
        if pilot.pending or any(e.get('next') not in NON_RUNNABLE for e in report):
            return False
        queue = queue_counts(pilot.repo)
        if queue != (0, 0):
            return False
        counts, members = breakdown(report, closed_designs(pilot.registry))
        open_counts = {k: v for k, v in counts.items() if k not in NOT_OPEN}
        if not open_counts:
            return False
        top = sorted(open_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        return bool(pilot.ledger({}, 'tick', 'STARVED_BLOCKED', 'record', top_reason=top, top_count=open_counts[top],
                                  top_designs=members[top][:20], blocker_reasons=counts))
    except Exception:
        return False
