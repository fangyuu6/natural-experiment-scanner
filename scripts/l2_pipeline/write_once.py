"""Write-once guards and reopened attempts (2026-10-19): one policy, three helpers.

A write-once guard (``storage.ensure_once``, ``agents.jobs._write_text_once``, the packet checks of ``agents.purpose``,
``agents.history_packet``, ``history_reuse``, ``run2_jobs``) fires when a file that already exists would be written again with
other bytes. Two causes were seen: (1) the path is keyed by the design (or version) only, so an attempt opened later by a
requeue meets what an earlier attempt wrote (``history_completion/<design>``, ``replacements/<design>/<role>``,
``S0/<design>/purpose_verifier``); (2) a record is derived again from state that moved on since it was written.

* ``replacement_slot``: the contract-replacement slot of a job is the one that already holds the job's lineage; a job without
  a slot gets the design/role directory when nothing is in it (the layout of every old slot), else one directory per attempt.
  Old slots are read as written and never touched.
* ``park``: a guard failure of an unfrozen attempt is an engineering event, not a check on the design: it is a reopenable PARK
  (the F10 / V3.1 shape: kind QUEUE_PARK, reopen release_recovery, text starting INSUFFICIENT_WORK so L3 does not count it).
  A frozen or sealed attempt keeps its STOP (its bytes are the authority).
* ``matches``: the guard texts this applies to. Integrity checks of bound evidence, rulings, locks and snapshots ("... bytes
  changed", "Locked ... changed", "Frozen ... changed") are not in the list and stay STOPs.
"""
from __future__ import annotations

import json
from pathlib import Path

RULE = 'docs/plans/l2-pipeline/WRITE_ONCE_SWEEP_20261019.md'
PARK_KIND = 'QUEUE_PARK'
PARK_PREFIX = 'QUEUE_PARK: INSUFFICIENT_WORK (engineering event, reopenable): '
FROZEN = ('FREEZE_PREP.json', 'FREEZE_FINAL.json', 'APPROVAL_PREP.json', 'APPROVAL_FINAL.json', 'EXECUTION_ONCE.json',
          'TERMINAL.json', 'FAILURE.json')
# The write-once guards a retry cannot get past with the same inputs: the file is there, a fresh derivation differs.
GUARD_TEXTS = (
    'Existing preparation artifact changed',          # storage.ensure_once, registration._retain_cards
    'Existing job changed',                           # agents.jobs._write_text_once
    'Existing purpose packet differs from card',      # agents.purpose.emit_purpose_verification
    'Existing completion job is not bound',           # c83_history.completion_job
    'Existing clean history predecessor changed',     # agents.history_packet.clean_pending
    'Existing reused artifact changed',               # history_reuse
    'Existing packet changed',                        # run2_jobs
)
RECORDS = ('INTENT.json', 'REPLACEMENT.json', 'TRANSPORT_REPAIR.json')


def matches(reason) -> bool:
    text = str(reason)
    return any(t in text for t in GUARD_TEXTS)


def frozen(root) -> bool:
    """True when the attempt's design directory holds a freeze, an approval or an execution record (or is unknown)."""
    if root is None:
        return True
    root = Path(root)
    return any((root / name).exists() for name in FROZEN)


def park_value(row, action, reason, time):
    """The PARKED.json of a write-once guard failure (same keys as the tick's own PARK and the F10 queue park)."""
    return dict(design_id=row['design_id'], attempt_id=row['attempt_id'], action=action, status='PARKED',
                reason=PARK_PREFIX + 'a write-once file cannot be written again unchanged (' + str(reason)[:300]
                + '); no check of the design failed',
                metrics=dict(kind=PARK_KIND, reopen='release_recovery', cause='WRITE_ONCE_GUARD', outcome_values_read=0, rule=RULE),
                rule=RULE, time=time)


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _names(record):
    """Every job path a slot record mentions (original, replacement, failed replacement, repaired job)."""
    if not isinstance(record, dict):
        return set()
    repaired = record.get('repaired_job')
    values = [record.get('original_job'), record.get('replacement_job'), record.get('failed_replacement_job'),
              repaired.get('path') if isinstance(repaired, dict) else None]
    return {str(v) for v in values if v}


def mentions(slot, path) -> bool:
    """True when a record of this slot names the job path: the slot already holds the lineage of that job."""
    return any(str(path) in _names(_read(Path(slot) / name)) for name in RECORDS)


def replacement_slot(autopilot_dir, design_id, attempt_id, role, path):
    """The contract-replacement slot of one job: ``replacements/<design>/<role>`` (every old slot, and the first slot of a
    design), or ``replacements/<design>/attempts/<attempt>/<role>`` once the design directory holds another job's lineage.
    A slot that names the job always wins, so an old slot is read as written and a reopened attempt never meets it."""
    base = Path(autopilot_dir) / 'replacements' / design_id
    legacy = base / role
    keyed = base / 'attempts' / attempt_id / role if attempt_id else None
    for slot in (legacy, keyed):
        if slot is not None and mentions(slot, path):
            return slot
    if keyed is None or not any((legacy / name).exists() for name in RECORDS):
        return legacy
    return keyed
