"""Append-only, idempotent writer for terminal PARK/STOP decisions (T202, WINDOW_PLAN_20261021 item 4).

Adds rows to the closure ledger data/l2_pipeline/registry/FINAL_DECISIONS_20261020.json (the file the L2
scoreboard reads) so the scoreboard does not lag behind the pipeline. It touches no design state file, registry
entry or ledger other than that one file. Existing rows are kept byte-for-byte and in order; a before-image copy is
written once per call that changes something; a re-run with the same rows changes nothing.
"""
from __future__ import annotations
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

LEDGER = 'FINAL_DECISIONS_20261020.json'
REGISTRY = Path(__file__).resolve().parents[2] / 'data/l2_pipeline/registry'
# Gate classes approved on 2026-10-20 (the by_gate keys of the 80-row ledger). A new class needs a hub ruling.
APPROVED_GATES = frozenset({
    'GATE0_PARK_S3_RI', 'S0_NO_SAME_CONSTRUCT_SERIES', 'GATE0_PARK_S3_SIBLING', 'AUDIT_V3_1',
    'HISTORY_CONTRACT_EXHAUSTED', 'HISTORY_INSUFFICIENT', 'PURPOSE_NO_SERIES', 'RECONCILE_C43_GATES',
    'RI_FEASIBILITY_0.90', 'S0_OUTCOME_UNAVAILABLE', 'S2_C46', 'S3_COUNT_FLOOR', 'S3_SERIES_HOLES',
    'S3_SIDECAR_PRESCREEN',
    # 2026-10-23 (HUB_RULING_20261023_LAST_STOPS Q1, panel 5-0): R3.1 item 5, no headline from the randomization p alone when neither interval is certified.
    'R3.1_INTERVAL_FALLBACK'})
STATES = frozenset({'PARK', 'STOP', 'GATE0_PARK'})
FIELDS = ('design_id', 'attempt', 'decision', 'gate', 'basis', 'state_at_closure', 'park_time_utc', 'source')
SAFE = re.compile(r'^[A-Za-z0-9_.-]+$')


def validate(row):
    """Raise ValueError unless `row` is a terminal not-evaluable decision with a named, approved gate."""
    missing = [k for k in FIELDS if k not in row]
    if missing:
        raise ValueError('row missing fields: ' + ', '.join(missing))
    extra = sorted(set(row) - set(FIELDS) - {'recorded_utc'})
    if extra:
        raise ValueError('row has unknown fields: ' + ', '.join(extra))
    for key in ('design_id', 'attempt'):
        if not isinstance(row[key], str) or not SAFE.match(row[key]):
            raise ValueError('bad ' + key + ': ' + repr(row[key]))
    if row['decision'] != 'NOT_EVALUABLE':
        raise ValueError('only NOT_EVALUABLE terminal decisions are recorded: ' + repr(row['decision']))
    if row['gate'] not in APPROVED_GATES:
        raise ValueError('gate is not in the approved class table: ' + repr(row['gate']))
    if row['state_at_closure'] not in STATES:
        raise ValueError('state_at_closure must be PARK, STOP or GATE0_PARK: ' + repr(row['state_at_closure']))
    for key in ('basis', 'source'):
        if not isinstance(row[key], str) or not row[key].strip():
            raise ValueError(key + ' must be a non-empty string')


def _read(path):
    value = json.loads(Path(path).read_text())
    if value.get('schema') != 'l2_final_decisions_v1' or not isinstance(value.get('designs'), list):
        raise ValueError('not an l2_final_decisions_v1 ledger: ' + str(path))
    return value


def plan(rows, registry=REGISTRY, ledger=LEDGER):
    """Dry run: (new_rows, already_present, conflicts). Writes nothing."""
    rows = [dict(r) for r in rows]
    seen = set()
    for r in rows:
        validate(r)
        key = (r['design_id'], r['attempt'])
        if key in seen:
            raise ValueError('duplicate row in the request: ' + repr(key))
        seen.add(key)
    value = _read(Path(registry) / ledger)
    have = {(r['design_id'], r['attempt']): r for r in value['designs']}
    new, present, conflicts = [], [], []
    for r in rows:
        old = have.get((r['design_id'], r['attempt']))
        if old is None:
            new.append(r)
        elif old.get('gate') == r['gate']:
            present.append(r)
        else:
            conflicts.append((r, old))
    return new, present, conflicts


def append(rows, registry=REGISTRY, ledger=LEDGER, *, task, apply=False, now=None):
    """Append the rows that are not yet in the ledger. Dry run unless apply=True.

    Refuses (nothing written) when any row names a gate different from an existing row of the same design and
    attempt. Returns a dict(new, present, written, before_image)."""
    new, present, conflicts = plan(rows, registry, ledger)
    if conflicts:
        raise ValueError('conflicting existing rows (gate differs): ' + ', '.join(sorted(r['design_id'] for r, _ in conflicts)))
    result = dict(new=[r['design_id'] for r in new], present=[r['design_id'] for r in present], written=False,
                  before_image=None)
    if not new or not apply:
        return result
    if not SAFE.match(task):
        raise ValueError('bad task id')
    path = Path(registry) / ledger
    when = (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')
    value = _read(path)
    before = path.with_name(path.name + '.before_' + task)
    if before.exists():
        raise ValueError('before-image already exists, refusing to overwrite: ' + str(before))
    raw = path.read_bytes()
    with before.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    value['designs'] = value['designs'] + [dict(r, recorded_utc=when) for r in new]
    value['count'] = len(value['designs'])
    by_gate = dict(value.get('by_gate', {}))
    for r in new:
        by_gate[r['gate']] = by_gate.get(r['gate'], 0) + 1
    value['by_gate'] = by_gate
    tmp = path.with_name(path.name + '.tmp_' + task)
    with tmp.open('xb') as stream:
        stream.write(json.dumps(value, indent=1, ensure_ascii=False).encode()); stream.flush(); os.fsync(stream.fileno())
    os.replace(tmp, path)
    log = path.with_name(path.name + '.appends.jsonl')
    with log.open('ab') as stream:
        stream.write((json.dumps(dict(time=when, task=task, added=result['new'], before_image=str(before),
                                      before_sha256=__import__('hashlib').sha256(raw).hexdigest())) + '\n').encode())
        stream.flush(); os.fsync(stream.fileno())
    result.update(written=True, before_image=str(before))
    return result
