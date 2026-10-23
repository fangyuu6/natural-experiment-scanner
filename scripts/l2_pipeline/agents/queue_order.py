"""T226(b): claim order by closeness to FINISHED.

A pending packet of a design that has fewer chain stages left is claimed first, so designs reach a documented decision sooner
and half-done designs do not wait behind fresh ones. The order is stable (remaining stages, then the old oldest-first key,
then the packet name). A packet that has waited longer than AGE_CAP_SECONDS goes first, oldest first, so no design starves.
Any read problem counts as "unknown" (sorted after known stages, old order), never as an error: ordering must not block a claim.
"""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace

AGE_CAP_SECONDS = 24 * 3600
UNKNOWN = 10 ** 6


def chain_of(row, repo):
    from .. import autopilot, rules
    if rules.pin_of_row(row) is None:
        return autopilot.CHAIN
    rules_value = rules.of_row(SimpleNamespace(repo=repo), row)
    return rules.chain(rules_value) if rules_value is not None else autopilot.CHAIN


def remaining_stages(row, root):
    """Chain actions of the packet's attempt without a checkpoint file; UNKNOWN when it cannot be read."""
    try:
        repo = Path(root).parent.parent.parent.parent  # <repo>/data/l2_pipeline/registry/SONNET_QUEUE
        case = Path(root).parent.parent / 'autopilot' / row['design_id'] / row['attempt_id']
        chain = chain_of(row, repo)
        return sum(1 for action in chain if not (case / 'actions' / (action + '.json')).is_file())
    except Exception:  # noqa: BLE001 - ordering must never block a claim
        return UNKNOWN


def order_key(binding, enqueued, name, remaining, now, age_cap=AGE_CAP_SECONDS):
    aged = now - enqueued >= age_cap
    return (0 if aged else 1, enqueued if aged else remaining, enqueued, name)


def ordered(root, entries, now):
    """entries: [(enqueue_time, name, packet_path)] -> packet paths in claim order."""
    cache, keyed = {}, []
    for enqueued, name, packet in entries:
        try:
            binding = json.loads((Path(packet) / 'PACKET.json').read_text())
            row = binding.get('row') or {}
            key = (row.get('design_id'), row.get('attempt_id'))
            if key not in cache:
                cache[key] = remaining_stages(row, root)
            remaining = cache[key]
        except Exception:  # noqa: BLE001
            binding, remaining = {}, UNKNOWN
        keyed.append((order_key(binding, enqueued, name, remaining, now), packet))
    return [packet for _, packet in sorted(keyed, key=lambda item: item[0])]
