"""T226 P2: do not spend a history packet on units that already failed on the same blocker class and have no new access route.

Incident data (T222, 2026-10-21): 59 of 527 (design, unit) pairs were never classified by either coder role; 57 of them had been
tried two or more times, each retry meeting the same blocked page (HTTP 403 / JS shell / scanned PDF). A retry of such a unit
costs a worker lease and cannot change the result.

Rule (staged): when a V9 history parent is split into chunks, a chunk whose EVERY unit
  * was left out (no record) by the most recent finished packet of its design that covered it, in an EARLIER attempt, with a
    host-blocked reason in the worker's own notes (``HOST_BLOCKED``), and
  * has no page in the shared pack (``shared_pack``: the new access route) and is not on the hub's allow list (``P2_ALLOW.json``),
is not enqueued. The merge then lacks those records, the merged submission says ``collection_complete: false`` with the units
listed under ``chunk_merge.blocked``, and the design reaches its gate through the ordinary collector (the history-incomplete
PARK, HISTORY_INSUFFICIENT) without a packet being spent. Mixed chunks still run (the other units are worth the lease).
Transient classes (timeout, TLS alert, 5xx, rate limit, Wayback 429) and "not researched" are never blockers: those retry.
This module writes no file.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

HOST_BLOCKED = re.compile(
    r'\b403\b|forbidden|access denied|\bblocked\b|cloudflare|captcha|bot[- ]protect|robots\.txt|javascript[- ](?:shell|only|required)'
    r'|js shell|requires javascript|no text layer|scanned (?:pdf|image)|image-only|empty (?:page|html|response)|blank page|returned no (?:text|content)|page (?:is )?empty', re.I)
ALLOW_FILE = 'P2_ALLOW.json'   # {"design_id": ["TX", ...] | "*"}: hub override, units retried regardless


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError, UnicodeError):
        return None


def classify(text):
    """'host_blocked' when the notes name a blocked host and not merely a transient fault; else None."""
    text = str(text or '')
    if HOST_BLOCKED.search(text):
        return 'host_blocked'
    return None


def unit_notes(value, unit):
    """Everything the worker wrote about one unit: incomplete_units entry plus its research_log findings."""
    parts = []
    inc = value.get('incomplete_units')
    if isinstance(inc, dict):
        parts.append(str(inc.get(unit, '')))
    elif isinstance(inc, list):
        parts.extend(str(x) for x in inc if (isinstance(x, dict) and x.get('unit') == unit) or x == unit)
    for entry in value.get('research_log', []) or []:
        if isinstance(entry, dict) and entry.get('unit') == unit:
            parts.append(str(entry.get('finding', '')) + ' ' + str(entry.get('conflict_resolution_attempts', '')))
    return ' '.join(parts)


def history(root, design_id, exclude_attempt=None):
    """{unit: dict(outcome='ok'|'blocked'|'other', cls, attempt_id, finished_at, packet)}: the latest finished outcome per unit."""
    state, packets = {}, []
    base = Path(root) / 'done'
    if not base.is_dir():
        return state
    for packet in base.iterdir():
        binding = _read(packet / 'PACKET.json')
        chunk = (binding or {}).get('chunk')
        if not chunk or chunk.get('design_id') != design_id:
            continue
        attempt = (binding.get('row') or {}).get('attempt_id')
        if exclude_attempt is not None and attempt == exclude_attempt:
            continue
        done = (_read(packet / 'COMPLETION.json') or {}).get('completed_at')
        packets.append((done if isinstance(done, (int, float)) else 0, packet.name, packet, binding, chunk, attempt))
    for finished, name, packet, binding, chunk, attempt in sorted(packets, key=lambda p: (p[0], p[1])):
        value = _read(binding.get('output_path', ''))
        if not isinstance(value, dict):
            continue
        recorded = {r.get('unit') for r in value.get('records', []) if isinstance(r, dict)}
        for unit in chunk.get('units', []):
            if unit in recorded:
                state[unit] = dict(outcome='ok', cls=None, attempt_id=attempt, finished_at=finished, packet=name)
                continue
            cls = classify(unit_notes(value, unit))
            state[unit] = dict(outcome='blocked' if cls else 'other', cls=cls, attempt_id=attempt, finished_at=finished, packet=name)
    return state


def blocked_units(root, design_id, units, attempt_id):
    """{unit: info} for the units of ``units`` that P2 keeps out of a new packet of ``attempt_id`` (see module docstring)."""
    from . import shared_pack
    allow = _read(Path(root) / ALLOW_FILE) or {}
    allowed = allow.get(design_id)
    if allowed == '*':
        return {}
    past = history(root, design_id, exclude_attempt=attempt_id)
    out = {}
    for unit in units:
        info = past.get(unit)
        if not info or info['outcome'] != 'blocked':
            continue
        if isinstance(allowed, list) and unit in allowed:
            continue
        if shared_pack.covers(root, unit, design_id):
            continue
        out[unit] = info
    return out
