"""F10 (REDESIGN W3 follow-up, 2026-10-18): what a Sonnet packet that cannot be finished does to its design.

Incident 2026-10-18 02:30-04:40Z: the worker task was raised to 6 workers and used the ``release`` command. A packet
released three times goes to the hub queue, and the hub queue STOPped the whole design ("Sonnet packet released by
workers 3 times"): about 17 in-flight designs went to STOP although nothing was wrong with any design. The causes of the
releases were packet problems (a 51-state history packet that needs about 100 fetched pages, a scanned PDF, a reviewer
sweep of 300k tokens), not design problems.

Rule from now on (HUB order 2026-10-18, F10):

* A STOP says "this design failed a check and needs a ruling". A queue event never says that. Whatever the queue
  escalates to the hub (a structural release, three worker releases, repeated lease expiry) becomes a documented PARK
  of the design (``PARKED.json`` with ``metrics.kind == QUEUE_PARK`` and a reason that begins ``QUEUE_PARK: INSUFFICIENT_WORK``), plus
  one HUB_QUEUE.json entry that carries the queue's own reason text. The park is reopenable through the card-bound
  requeue route (``release_recovery``); nothing is deleted and no card changes.
* A release is *structural* when the packet itself cannot be finished by any worker: ``too_large`` (more than one lease of
  work), ``tool_missing`` (a tool the packet needs is not available to the worker) or ``input_unreadable`` (a bound input or
  a fetched source cannot be read). The worker says so with ``release --kind``; an old-style release without ``--kind`` is
  sorted by a conservative reading of its reason text. A structural release goes to the hub at once (a second worker would
  meet the same wall). Any other release (``worker``: out of context or time, a transient network problem) goes back to
  pending as before, and the third one goes to the hub.
* Lease expiry (a worker that stopped heartbeating) also PARKs: it is an operations event, not a verdict on the design,
  and the R1+ rules already classify it as PACKET_LOST (a PARK). Keeping STOP for it would reproduce the incident the next
  time workers die.

Nothing here reads an outcome value.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

KINDS = ('worker', 'too_large', 'tool_missing', 'input_unreadable')
STRUCTURAL = KINDS[1:]
PARK_KIND = 'QUEUE_PARK'
# L3 (build_live_set.GATE_RULES, bytes pinned by PREREG_AMEND_20261017_F1_V1) maps a PARKED reason to a gate by pattern; the
# token INSUFFICIENT_WORK puts a queue park in its existing "excluded, not counted" bucket (work not completed, no verdict on
# the design) instead of "unclassified". The words after it say what really happened.
PREFIX = PARK_KIND + ': INSUFFICIENT_WORK (queue event, reopenable): '
RULE = ('REDESIGN W3 F10 (2026-10-18): a queue event never STOPs a design; it is a documented, reopenable PARK '
        '(QUEUE_PARK) that release_recovery reopens through the card-bound requeue')
STATUS = 'PARKED_QUEUE'

# Conservative reading of an old-style reason (a release without --kind). First family that matches wins.
_PATTERNS = (
    ('tool_missing', re.compile(
        r"\bno (?:pdf|pdftoppm|pdftotext|ocr)\b|pdf text (?:tool|extraction)|(?:tool|tools|commands?) (?:is |are )?(?:missing|unavailable|excluded)"
        r"|(?:allowed|available) (?:shell )?(?:tools?|commands?) (?:cannot|exclude|do not)|(?:tools?|commands?) (?:cannot|can't) "
        r"(?:read|run|parse|open)|cannot (?:run|execute) (?:code|tests?|python)|read-only tools?", re.I)),
    ('input_unreadable', re.compile(
        r"unreadable|cannot (?:be )?(?:read|parsed|opened)|no text layer|scanned (?:image|pdf)|image-only|binary (?:download|file)"
        r"|cannot parse|js shell|javascript shell", re.I)),
    ('too_large', re.compile(
        r"too (?:large|big|heavy|long)|not (?:be )?(?:completable|feasible|achievable)|cannot be (?:completed|finished|done)"
        r"|(?:in|within|inside) (?:one|a single|this) (?:worker )?(?:lease|session|run|pass)|90-minute|exceeds? (?:one|a|the) "
        r"(?:lease|session|worker)|hundreds of|\d+\+? (?:fetched|fetch|official|sources|pages)", re.I)),
)


def classify(reason):
    """``worker`` or the structural kind a free-text release reason describes (conservative: doubt means ``worker``)."""
    text = str(reason or '')
    for kind, pattern in _PATTERNS:
        if pattern.search(text):
            return kind
    return 'worker'


def normalise_kind(kind, reason=None):
    """The release kind: the worker's ``--kind`` (also ``structural_too_large``), else read from the reason text."""
    if kind is None or str(kind).strip() == '':
        return classify(reason)
    value = str(kind).strip().lower().replace('-', '_')
    value = value[len('structural_'):] if value.startswith('structural_') else value
    if value not in KINDS:
        raise ValueError('Unknown release kind ' + repr(kind) + '; use one of ' + ', '.join(KINDS))
    return value


def _read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.is_file() else default


def story(packet):
    """What the queue knows about why this packet sits in hub/: cause, kind, last reason, counts (read-only)."""
    packet = Path(packet)
    marks = sorted((p for p in packet.glob('RELEASED_*.json') if re.fullmatch(r'RELEASED_\d+\.json', p.name)),
                   key=lambda p: int(re.search(r'\d+', p.name).group()))
    state = _read(packet / 'STATE.json', {}) or {}
    last = _read(marks[-1], {}) if marks else {}
    kind = state.get('structural') or last.get('kind')
    if kind in STRUCTURAL:
        cause = 'structural_release'
    elif marks and not (packet / 'EXPIRED_2.json').exists():
        cause = 'released_repeatedly'
    else:
        cause = 'lease_expired'
    return dict(cause=cause, release_kind=kind if kind in STRUCTURAL else ('worker' if marks else None),
                last_reason=str(last.get('reason', ''))[:1000] if last else None, releases=len(marks),
                expiries=int(state.get('expiries', 0) or 0))


# T226: the prefix keeps the token INSUFFICIENT_WORK (L3 pins it), but a lease expiry or repeated worker release says nothing about the
# research or the design, so the words right after the prefix name the cause. A real incomplete history parks with its own text
# ("INSUFFICIENT_WORK: official history is an incomplete research checkpoint", agents/collect).
CAUSE_LABEL = {
    'lease_expired': 'LEASE EXPIRED, workers lost, no research verdict on the packet or the design: ',
    'released_repeatedly': 'RELEASED BY WORKERS, no research verdict on the packet or the design: ',
}


def text_for(hub_reason, cause=None):
    """The reason text of a QUEUE_PARK: the queue's own words, behind a stable prefix (L3 and plan read the prefix)."""
    text = str(hub_reason)
    if text.startswith(PARK_KIND):
        return text
    return PREFIX + CAUSE_LABEL.get(cause, '') + text


def queue_park(hub_reason, packet):
    """The ``Park`` a tick raises when a job's packet reached the hub queue (a ``ValueError``, so old handlers still catch it)."""
    from .c42 import Park
    binding = _read(Path(packet) / 'PACKET.json', {}) or {}
    info = story(packet)
    return Park(text_for(hub_reason, info['cause']) + '; HUB_QUEUE required',
                dict(kind=PARK_KIND, job=binding.get('job_path'), packet=Path(packet).name, outcome_values_read=0,
                     reopen='release_recovery', **info))


def _terminal(pilot, row):
    """'stopped' / 'parked' when this attempt already ended, else None."""
    if pilot.stop_record(row):
        return 'stopped'
    case = pilot.case_dir(row)
    for name in ('PARKED_C83_FINAL.json', 'PARKED.json'):
        if (case / name).exists():
            return 'parked'
    return None


def dispose_hub_packet(pilot, packet, hub_reason):
    """Scheduler hook for one packet in hub/: PARK its design (once), never STOP it. Returns the disposition string.

    * ``parked``: the design's current attempt was running and is now PARKED (QUEUE_PARK) with a HUB_QUEUE entry;
    * ``already_stopped`` / ``already_parked`` / ``superseded``: nothing to do (the attempt ended or was replaced);
    * a tick that already raised the same park (its PARKED.json starts with QUEUE_PARK) only gets the HUB_QUEUE entry.
    The result is written beside the packet (``HUB_DISPOSITION.json``) so a packet is handled once, not every tick."""
    from .autopilot import atomic, read, utc
    from .storage import ensure_once
    packet = Path(packet)
    mark = packet / 'HUB_DISPOSITION.json'
    if mark.exists():
        return _read(mark)['disposition']
    binding = read(packet / 'PACKET.json')
    row = binding['row']
    active = {r['design_id']: r for r in read(pilot.registry / 'ACTIVE_DESIGNS.json', {}).get('designs', [])}
    current = active.get(row.get('design_id'))
    info = story(packet)
    reason = text_for(hub_reason, info['cause']) + ': ' + str(binding.get('job_path'))
    metrics = dict(kind=PARK_KIND, job=binding.get('job_path'), packet=packet.name, outcome_values_read=0,
                   reopen='release_recovery', **info)
    if current is None or current.get('attempt_id') != row.get('attempt_id'):
        disposition = 'superseded'
    else:
        ended = _terminal(pilot, current)
        existing = read(pilot.case_dir(current) / 'PARKED.json', {}) or {}
        ours = str(existing.get('reason', '')).startswith(PARK_KIND) and ended == 'parked'
        if ended == 'stopped':
            disposition = 'already_stopped'
        elif ended == 'parked' and not ours:
            disposition = 'already_parked'
        else:
            disposition = 'already_parked' if ours else 'parked'
            if not ours:
                ensure_once(pilot.case_dir(current) / 'PARKED.json', dict(
                    design_id=current['design_id'], attempt_id=current['attempt_id'], action='dispatch', status='PARKED',
                    reason=reason, metrics=metrics, rule=RULE, time=utc()))
                pilot.ledger(current, 'dispatch', 'PARK', 'record', reason=reason[:300], kind=PARK_KIND)
            queue = read(pilot.registry / 'HUB_QUEUE.json', dict(schema='l2_hub_queue_v1', entries=[]))
            key = (current['design_id'], current['attempt_id'])
            if not any((v['design_id'], v['attempt_id']) == key and not v.get('status', '').startswith('SUPERSEDED_')
                       and v.get('status') == STATUS for v in queue['entries']):
                queue['entries'].append(dict(design_id=current['design_id'], attempt_id=current['attempt_id'], action='dispatch',
                                             status=STATUS, reason=reason, job=binding.get('job_path'), packet=packet.name,
                                             release_kind=info['release_kind'], cause=info['cause'], reopen='release_recovery',
                                             time=utc(), rule=RULE))
                atomic(pilot.registry / 'HUB_QUEUE.json', queue)
    atomic(mark, dict(disposition=disposition, reason=reason, time=utc(), rule=RULE))
    return disposition


def park_from_exception(pilot, queue_root, exc):
    """Enqueue site of the scheduler: a ``Park`` raised by ``sonnet_queue.state`` for a job whose packet is in hub/.
    True when the hub packet was handled here (the design is parked, never stopped)."""
    name = (getattr(exc, 'metrics', None) or {}).get('packet')
    packet = Path(queue_root) / 'hub' / str(name) if name else None
    if packet is None or not packet.is_dir():
        return False
    from .agents.sonnet_queue import hub_reason
    dispose_hub_packet(pilot, packet, hub_reason(packet))
    return True
