"""F7 (REDESIGN W3 follow-up, 2026-10-18): V9 history packets of at most ``CHUNK_UNITS`` jurisdictions.

A 51-jurisdiction V9 history packet needs about 100 fetched, hashed and quote-verified official pages. That cannot be
finished inside one 90-minute lease (workers said so, 24 times, in the 2026-10-18 incident; dashboard task T15 asked for
the split on 2026-10-16). The queue now splits such a packet when it enqueues it:

* the canonical job (role A or B, 51 units) is unchanged and stays the only job the autopilot knows;
* ``enqueue`` emits one **chunk job** per block of at most 10 units (``agents.history.emit_history_job`` with
  ``chunk=``: same contract, same blindness rules, same answer-free plan restricted to the block, its own output directory)
  and one ordinary packet for each, plus an inert **parent packet** in ``chunked/`` that lists them (``CHUNKS.json``);
* a chunk is claimed, worked, self-checked and finished like any packet (the same collector, restricted to its units);
* when every chunk is ``done`` the parent is **merged**: one ordinary V9 submission with all units is written to the
  canonical job's own ``output_path`` (records in universe order, sources, research log, time added up, notes kept under
  ``chunk_merge``), the fetch logs and archived pages are combined in the parent packet, and the parent moves to ``done/``.
  From there the autopilot collects the parent exactly as if one worker had written it (V9 validator, evidence rule,
  reconciliation). A design's history is complete only when all chunks are in; a chunk in the hub queue parks the design
  (``release_policy``); a chunk lost from the queue is enqueued again from its canonical job.

Blindness and peer rules are unchanged: a chunk packet reads only its own answer-free plan restricted to its units, never
a peer's submission. One addition: a worker identity that worked on a chunk of role A of a design may not claim a chunk of
role B of that design (and the reverse), because small packets make "one worker codes both roles" likely. Nothing here
reads an outcome value.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from copy import deepcopy
from pathlib import Path

CHUNK_UNITS = 10
CORE_KEYS = {'design_id', 'role', 'source_sha256', 'source_pack_sha256', 'history_output_schema', 's1_ready',
             'execution_authorized', 'outcome_associations_computed', 'time_spent_seconds', 'blindness_attestation',
             'records', 'sources', 'research_log', 'collection_complete', 'chunk_merge'}


def _read(path):
    return json.loads(Path(path).read_text())


def is_target(job):
    """A V9 official-history A/B job whose universe is larger than one chunk and that is not itself a chunk."""
    from .history_packet import is_history
    return (is_history(job) and job.contract.get('evidence_standard') == 'HUB_RULING_V9_HISTORY_EVIDENCE'
            and not job.contract.get('history_chunk')
            and len(job.contract.get('manual', {}).get('universe', [])) > CHUNK_UNITS)


def split(units, size=CHUNK_UNITS):
    """Balanced blocks, in universe order: 51 units -> 6 blocks of 9, 9, 9, 8, 8, 8."""
    units = list(units)
    if not units:
        raise ValueError('No units to split')
    blocks = -(-len(units) // size)
    base, extra = divmod(len(units), blocks)
    out, start = [], 0
    for i in range(blocks):
        n = base + (1 if i < extra else 0)
        out.append(units[start:start + n])
        start += n
    return out


def prefix(index):
    return 'k%02d_' % index


def chunk_jobs(parent, row=None):
    """Emit (or find again) the chunk jobs of ``parent``. Deterministic paths: re-running creates nothing new."""
    from .history import emit_history_job
    blocks = split(parent.contract['manual']['universe'])
    jobs = []
    for i, units in enumerate(blocks, 1):
        jobs.append(emit_history_job(parent.job_path, jobs_root=Path(parent.output_dir) / 'chunks' / ('c%02d' % i),
                                     attempt_id='chunk%02dof%02d' % (i, len(blocks)), completion_units=units,
                                     chunk=dict(index=i, total=len(blocks), source_prefix=prefix(i))))
    return jobs


def chunk_meta(job):
    return dict(job.contract['history_chunk'], design_id=job.design_id, role=job.role)


# --- queue: group state, repair, merge -------------------------------------------------------------------------------

def group(parent_packet):
    return _read(Path(parent_packet) / 'CHUNKS.json')


def group_state(root, parent_packet):
    """'done' (after the merge), 'running', or 'pending' (a chunk packet is missing: enqueue repairs it).
    A chunk in the hub queue raises the queue's park."""
    from . import sonnet_queue as q
    from .. import release_policy
    manifest = group(parent_packet)
    found = []
    live = [c for c in manifest['chunks'] if not c.get('blocked')]
    if not live:   # T226 P2: every chunk holds only units that already failed on a blocked host: no packet, the design goes to its gate
        from ..c42 import Park
        units = sorted(u for c in manifest['chunks'] for u in c['units'])
        raise Park('INSUFFICIENT_WORK: every planned history unit already failed on a blocked host in an earlier attempt and no new access route '
                   'exists (shared pack empty for them); no packet spent: ' + ', '.join(units),
                   dict(kind='HISTORY_UNITS_BLOCKED', units=units, rule='T226_P2', outcome_values_read=0))
    for chunk in live:
        packet = q.locate(root, chunk['job_id'])
        found.append((chunk, packet))
    for chunk, packet in found:
        if packet is not None and packet.parent.name == 'hub':
            raise release_policy.queue_park(q.hub_reason(packet), packet)
    if any(packet is None for _, packet in found):
        return 'pending'
    if all(packet.parent.name == 'done' for _, packet in found):
        merge(root, parent_packet)
        return 'done'
    return 'running'


def repair(root, parent_packet, row):
    """Enqueue again every chunk whose packet is gone from the queue (retired with a stopped attempt, for example)."""
    from .jobs import load_job
    from . import sonnet_queue as q
    made = []
    for chunk in group(parent_packet)['chunks']:
        if chunk.get('blocked'):
            continue
        if q.locate(root, chunk['job_id']) is None:
            made.append(q._enqueue_locked(load_job(Path(chunk['job_path'])), row, root, chunk['job_id'], chunk=chunk['meta']))
    return made


def expand(root, parent, row, parent_id):
    """Called by ``enqueue`` under the queue lock: chunk packets plus the inert parent. Returns the enqueue result."""
    from . import sonnet_queue as q
    from .contract_tests import check_job_types
    from .history_packet import check as check_history_packet
    jobs = chunk_jobs(parent, row)
    listed = []
    from . import unit_blockers
    for i, job in enumerate(jobs, 1):
        check_job_types([job])
        check_history_packet(job)
        job_id = q.identity(job.job_path)
        meta = dict(parent_job_id=parent_id, parent_job_path=parent.job_path, index=i, total=len(jobs),
                    design_id=parent.design_id, role=parent.role, units=list(job.contract['manual']['universe']))
        try:   # T226 P2: a chunk whose every unit already failed on a blocked host (earlier attempt, no new route) is not enqueued
            blocked = unit_blockers.blocked_units(root, parent.design_id, meta['units'], (row or {}).get('attempt_id'))
        except (OSError, ValueError, KeyError, TypeError):
            blocked = {}
        if blocked and len(blocked) == len(meta['units']):
            listed.append(dict(index=i, job_id=job_id, job_path=job.job_path, job_sha256=job.job_sha256, units=meta['units'],
                               output_path=job.output_path, meta=meta, blocked=blocked))
            continue
        q._enqueue_locked(job, row, root, job_id, chunk=meta)
        listed.append(dict(index=i, job_id=job_id, job_path=job.job_path, job_sha256=job.job_sha256,
                           units=meta['units'], output_path=job.output_path, meta=meta))
    staging = q._stage_packet(root, parent, row, parent_id)
    q.atomic(staging / 'CHUNKS.json', dict(schema='l2_history_chunks_v1', parent_job_id=parent_id, parent_job_path=parent.job_path,
                                           parent_job_sha256=parent.job_sha256, chunk_units=CHUNK_UNITS, chunks=listed))
    os.rename(staging, Path(root) / 'chunked' / parent_id)
    return dict(backend='sonnet_queue', job_id=parent_id, packet=str(Path(root) / 'chunked' / parent_id), chunks=len(listed))


def _namespace(value, ids, mark):
    """Prefix every source id of one chunk in place (``source_id`` and ``source_ids``), so two chunks cannot collide."""
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if key == 'source_id' and isinstance(item, str) and item in ids:
                value[key] = mark + item
            elif key == 'source_ids' and isinstance(item, list):
                value[key] = [(mark + x if isinstance(x, str) and x in ids else x) for x in item]
            else:
                _namespace(item, ids, mark)
    elif isinstance(value, list):
        for item in value:
            _namespace(item, ids, mark)


def merge_values(parent, chunks, blocked=None):
    """One V9 submission for the whole universe from the chunk submissions.

    ``chunks`` is a list of dict(index, job, value, output_sha256). The merge is faithful and fails closed: it never repairs
    or hides a defect. A foreign or repeated unit stays in ``records`` (the ordinary collector then rejects "complete universe
    once"), a contaminated attestation or an outcome-association flag is carried through as true, a wrong design, role or schema
    keeps the wrong value, and an unreadable chunk gives a document without identity. The ordinary V9 collector then rejects
    the parent exactly as it would reject one worker's bad submission (one contract replacement), and ``chunk_merge.problems``
    lists what the merge saw. Pure: no writes."""
    universe = list(parent.contract['manual']['universe'])
    blocked = blocked or {}   # T226 P2: {chunk index: [units]} deliberately not researched again
    if sorted(c['index'] for c in chunks) != [i for i in range(1, len(chunks) + len(blocked) + 1) if i not in blocked]:
        raise ValueError('Chunk set is incomplete')
    rows, sources, log, notes, seen, renamed, parts, problems = [], [], [], {}, {}, {}, [], []
    seconds, attestation, incomplete, associations, identity, schema = 0, {}, False, False, {}, None
    owner = {}
    for chunk in sorted(chunks, key=lambda c: c['index']):
        job, index = chunk['job'], chunk['index']
        units = list(job.contract['manual']['universe'])
        value = deepcopy(chunk['value'])
        parts.append(dict(index=index, job_path=job.job_path, job_sha256=job.job_sha256, output_sha256=chunk['output_sha256'], units=units))
        if not isinstance(value, dict):
            problems.append(dict(chunk=index, problem='submission is not a JSON object'))
            return dict(chunk_merge=dict(schema='l2_history_chunk_merge_v1', parent_job_sha256=parent.job_sha256, chunks=parts, problems=problems))
        for key in ('design_id', 'role'):
            if value.get(key) != getattr(parent, key):
                identity.setdefault(key, value.get(key))
                problems.append(dict(chunk=index, problem=key + ' differs from the parent job', found=value.get(key)))
        if value.get('history_output_schema') != 'official_history_v9_1':
            schema = schema or value.get('history_output_schema')
            problems.append(dict(chunk=index, problem='history_output_schema differs', found=value.get('history_output_schema')))
        ids = {s.get('source_id') for s in value.get('sources', []) if isinstance(s, dict)}
        by_id = {s.get('source_id'): s for s in value.get('sources', []) if isinstance(s, dict)}
        if any(sid in seen and seen[sid] != by_id[sid] for sid in ids):
            _namespace(value, ids, prefix(index))   # the whole chunk, so its rows keep pointing at its sources
            renamed[index] = sorted(i for i in ids if isinstance(i, str))
        for source in value.get('sources', []):
            if not isinstance(source, dict):
                continue
            declared = source.get('archive_path')
            if isinstance(declared, str) and Path(declared).is_absolute():
                try:   # an absolute path into the chunk's own directory becomes the path the merged parent resolves
                    source['archive_path_original'] = declared
                    source['archive_path'] = str(Path(declared).resolve().relative_to(Path(job.output_dir).resolve()))
                except ValueError:
                    source.pop('archive_path_original', None)
            sid = source.get('source_id')
            if sid not in seen:
                seen[sid] = source
                sources.append(source)
        for row in value.get('records', []):
            unit = row.get('unit') if isinstance(row, dict) else None
            if unit not in units:
                problems.append(dict(chunk=index, problem='record outside the chunk units', unit=unit))
            if unit in owner:
                problems.append(dict(chunk=index, problem='unit repeated', unit=unit, first_chunk=owner[unit]))
            else:
                owner[unit] = index
            rows.append(row)
        log.extend(value.get('research_log', []))
        stated = value.get('time_spent_seconds', 0)
        seconds += stated if type(stated) in (int, float) else 0
        if value.get('collection_complete') is False or {r.get('unit') for r in value.get('records', []) if isinstance(r, dict)} != set(units):
            incomplete = True
        if value.get('outcome_associations_computed') not in (None, False):   # optional key; a true value is carried, never cleaned
            associations = True
        att = value.get('blindness_attestation')
        if not isinstance(att, dict):
            problems.append(dict(chunk=index, problem='blindness attestation missing'))
            attestation = None
        elif attestation is not None:
            for key, item in att.items():   # true anywhere stays true: a contaminated chunk contaminates the merge
                attestation[key] = bool(attestation.get(key)) or bool(item)
        extra = {k: v for k, v in value.items() if k not in CORE_KEYS}
        if extra:
            notes[str(index)] = extra
    position = {u: i for i, u in enumerate(universe)}
    rows = sorted(enumerate(rows), key=lambda p: (position.get(p[1].get('unit') if isinstance(p[1], dict) else None, len(universe)), p[0]))
    merged = dict(design_id=identity.get('design_id', parent.design_id), role=identity.get('role', parent.role),
                  source_sha256=parent.contract['source_sha256'], source_pack_sha256=parent.contract['source_sha256'],
                  history_output_schema=schema or 'official_history_v9_1', s1_ready=False, execution_authorized=False,
                  outcome_associations_computed=associations, time_spent_seconds=seconds,
                  records=[r for _, r in rows], sources=sources, research_log=log,
                  chunk_merge=dict(schema='l2_history_chunk_merge_v1', parent_job_sha256=parent.job_sha256, chunks=parts,
                                   renamed_source_ids={str(k): v for k, v in renamed.items()}, notes=notes, problems=problems,
                                   **({'blocked': {str(k): v for k, v in sorted(blocked.items())}} if blocked else {})))
    if attestation is not None:
        merged['blindness_attestation'] = attestation
    if incomplete or len(merged['records']) != len(universe):
        merged['collection_complete'] = False
    return merged


def _link(source, target):
    """Same file under the parent's evidence directory: a hard link (one copy on disk), else a copy. Content-addressed."""
    target = Path(target)
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        tmp = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.tmp')
        tmp.write_bytes(Path(source).read_bytes())
        os.replace(tmp, target)
        target.chmod(0o444)


def merge(root, parent_packet, now=None):
    """Write the merged submission and move the parent packet to done/. Idempotent; the caller does not hold the lock."""
    from . import sonnet_queue as q
    from .jobs import load_job
    from ..storage import sha
    now = time.time() if now is None else now
    with q.locked(root):
        parent_packet = Path(parent_packet)
        if not parent_packet.exists() or parent_packet.parent.name == 'done':
            return str(Path(root) / 'done' / parent_packet.name)
        binding = q.read(parent_packet / 'PACKET.json')
        parent = load_job(Path(binding['job_path']))
        manifest = group(parent_packet)
        chunks, packets, blocked_items = [], [], []
        for item in manifest['chunks']:
            if item.get('blocked'):
                blocked_items.append(item)
                continue
            packet = q.locate(root, item['job_id'])
            if packet is None or packet.parent.name != 'done':
                raise ValueError('Chunk packet is not done: ' + item['job_id'])
            chunk_binding, _ = q.completion_valid(packet)
            job = load_job(Path(chunk_binding['job_path']))
            try:
                value = q.read(job.output_path)
            except (ValueError, UnicodeError):
                value = None   # not JSON: the merge then has no identity and the ordinary collector rejects it
            chunks.append(dict(index=item['index'], job=job, value=value, output_sha256=sha(job.output_path)))
            packets.append(packet)
        merged = merge_values(parent, chunks, {i['index']: sorted(i['blocked']) for i in blocked_items})
        body = (json.dumps(merged, indent=1, sort_keys=True, allow_nan=False) + '\n').encode()
        out = Path(parent.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            if out.read_bytes() != body:
                raise ValueError('Merged history differs from the existing submission')
        else:
            tmp = out.with_name('.' + out.name + '.' + uuid.uuid4().hex + '.tmp')
            tmp.write_bytes(body)
            os.rename(tmp, out)   # the final name appears whole or not at all
        # Combined fetch evidence: the log entries (kept in chunk order) and the archived pages under the parent's own directory.
        from . import evidence_fetch as ef
        entries, workers, rule = [], [], False
        for item, packet in zip([c for c in manifest['chunks'] if not c.get('blocked')], packets):
            log = packet / 'EVIDENCE_LOG.json'
            if log.exists():
                for entry in q.read(log).get('entries', []):
                    source = Path(entry.get('archive_abs_path') or '')
                    if not source.is_file():
                        source = Path(load_job(Path(q.read(packet / 'PACKET.json')['job_path'])).output_dir) / entry['archive_path']
                    _link(source, Path(parent.output_dir) / entry['archive_path'])
                    entries.append(dict(entry, chunk=item['index'], archive_abs_path=str(Path(parent.output_dir) / entry['archive_path'])))
            if (packet / 'EVIDENCE_RULE.json').exists():
                rule = True
                workers.append(str(q.read(packet / 'EVIDENCE_RULE.json').get('worker')))
        if entries:
            q.atomic(parent_packet / 'EVIDENCE_LOG.json', dict(schema='l2_evidence_fetch_log_v1', rule=ef.RULE, entries=entries))
        if rule and not (parent_packet / 'EVIDENCE_RULE.json').exists():
            q.atomic(parent_packet / 'EVIDENCE_RULE.json', dict(rule=ef.RULE, worker=','.join(sorted(set(workers))), set_at=now,
                                                                 note='merged from ' + str(len(packets)) + ' chunk packets'))
        token = __import__('hashlib').sha256(''.join(c['output_sha256'] for c in chunks).encode()).hexdigest()
        q.atomic(parent_packet / 'MERGED.json', dict(chunks=[dict(index=c['index'], output_sha256=c['output_sha256']) for c in chunks],
                                                      merged_at=now, merged_output_sha256=sha(out)))
        if not (parent_packet / 'COMPLETION.json').exists():
            from ..storage import ensure_once
            ensure_once(parent_packet / 'COMPLETION.json', dict(output_sha256=sha(out), completed_at=now, token=token,
                                                                 merged_from=[i['job_id'] for i in manifest['chunks'] if not i.get('blocked')]))
        os.rename(parent_packet, Path(root) / 'done' / parent_packet.name)
    return str(Path(root) / 'done' / parent_packet.name)


# --- claim guard -----------------------------------------------------------------------------------------------------

def _holders(packet):
    """Every worker name that ever held a lease on this packet: the name the claim returned (``worker``).

    Worker-fix G2 (2026-10-18): the name a worker *asked for* (``requested``) is not an identity. The dispatcher reused six base names, so
    every suffixed worker counted for its base and, after six names had touched the A chunks of a design, nobody could claim its B
    chunks (2 designs with 6 B chunks never claimed). A session that claims again presents the name its first claim returned, which is
    still found here."""
    names = set()
    for path in [packet / 'LEASE.json', *packet.glob('EXPIRED_*.json'), *packet.glob('RELEASED_*.json')]:
        if path.is_file():
            try:
                lease = _read(path)
            except ValueError:
                continue
            lease = lease.get('lease', lease)
            names.add(lease.get('worker'))
    names.discard(None)
    return names


def held_roles(root, names):
    """{(design_id, role)} of every history chunk any of ``names`` ever held a lease on (all attempts, all states). One scan."""
    from . import sonnet_queue as q
    names = {n for n in names if n}
    held = set()
    for state in q.STATES:
        for packet in (Path(root) / state).glob('*'):
            try:
                chunk = _read(packet / 'PACKET.json').get('chunk')
            except (OSError, ValueError):
                continue
            if chunk and names & _holders(packet):
                held.add((chunk['design_id'], chunk['role']))
    return held


def role_conflict(root, names, binding, held=None):
    """True when one of ``names`` already held a chunk of the *other* role of the same design (any attempt)."""
    chunk = binding.get('chunk')
    if not chunk:
        return False
    held = held_roles(root, names) if held is None else held
    return any(design == chunk['design_id'] and role != chunk['role'] for design, role in held)
