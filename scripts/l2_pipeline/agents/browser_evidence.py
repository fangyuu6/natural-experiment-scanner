"""Hub-run import of normal-browser downloads into a history packet's evidence archive (staged 2026-10-19; not applied).

Why: ``sonnet_queue fetch-evidence`` is the only door into a packet's ``EVIDENCE_LOG.json`` and it needs a live lease. When
the machine's own fetch fails for an official host (timeouts, a TLS alert, Wayback HTTP 429) a person can still open the page in
a normal browser. This module takes such a delivery and writes it into the same archive and log the fetch command writes, so
``verify-quote`` / ``evidence-text`` / the NAMED_PANEL_Q3 evidence rule treat it like any fetched file: the bytes sit in
``<job output dir>/evidence/<sha256>.<ext>`` (read-only), the log entry carries URL, final URL, UTC time, size, SHA256 and
``via: "browser"`` (plus the browser string and, for a Wayback copy, the snapshot fields).

Delivery directory (written by the browser agent, R25 format): ``HANDOFF.json`` with
``{"schema": "l2_browser_evidence_handoff_v1", "design_id": ..., "items": [{"file", "url", "final_url", "fetched_at_utc",
"bytes", "sha256", "browser", ["content_type"], ["origin": "live"|"wayback"], ["snapshot_timestamp"], ["live_error"]}]}``.
``file`` is relative to the delivery directory. Nothing is trusted: every hash and size is recomputed, URLs pass the same
``check_url`` rules as a fetch, and the design id must match the packet's job.

Hub use (after a code window has applied this module and the ``evidence_fetch.staged_entry`` patch):
    python -B -m scripts.l2_pipeline.agents.browser_evidence --job-id ID --delivery DIR            # plan only
    python -B -m scripts.l2_pipeline.agents.browser_evidence --job-id ID --delivery DIR --apply    # write
The packet must be ``pending`` or ``in_progress`` (a ``done``/``hub``/``chunked`` packet is refused). A worker that later runs
``fetch-evidence --url URL`` for an imported URL gets the staged entry back without any network call.
Idempotent: an item already imported (same URL and SHA256) is skipped. Nothing is dispatched; no outcome value is read.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from . import evidence_fetch as ef
from . import sonnet_queue as q

SCHEMA = 'l2_browser_evidence_handoff_v1'
VIA = 'browser'
OPEN_STATES = ('pending', 'in_progress')
TYPES = {'.pdf': 'application/pdf', '.html': 'text/html', '.htm': 'text/html', '.txt': 'text/plain'}
STAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')
REQUIRED = ('file', 'url', 'final_url', 'fetched_at_utc', 'bytes', 'sha256', 'browser')


class ImportRefused(ValueError):
    pass


def _epoch(stamp):
    return calendar.timegm(time.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ'))


def load_handoff(delivery):
    path = Path(delivery) / 'HANDOFF.json'
    if not path.is_file():
        raise ImportRefused('HANDOFF.json missing in ' + str(delivery))
    value = json.loads(path.read_text())
    if value.get('schema') != SCHEMA or not isinstance(value.get('items'), list) or not value['items']:
        raise ImportRefused('HANDOFF.json must carry schema ' + SCHEMA + ' and a non-empty items list')
    return value


def check_item(delivery, item):
    """Validate one delivered file; returns (url, fetch-result dict, extra log fields, fetched-at epoch). Raises ImportRefused."""
    delivery = Path(delivery).resolve()
    missing = [k for k in REQUIRED if item.get(k) in (None, '')]
    if missing:
        raise ImportRefused('item lacks ' + ', '.join(missing) + ': ' + str(item.get('file')))
    path = delivery / item['file']
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(delivery):
        raise ImportRefused('file missing, a symlink or outside the delivery directory: ' + item['file'])
    raw = path.read_bytes()
    if not raw or len(raw) > ef.SIZE_CAP:
        raise ImportRefused('file empty or over the size cap: ' + item['file'])
    if len(raw) != item['bytes']:
        raise ImportRefused('size differs from the receipt (%d vs %s): %s' % (len(raw), item['bytes'], item['file']))
    if hashlib.sha256(raw).hexdigest() != str(item['sha256']).lower():
        raise ImportRefused('SHA256 differs from the receipt: ' + item['file'])
    try:
        url, final_url = ef.check_url(item['url']), ef.check_url(item['final_url'])
    except ef.FetchError as exc:
        raise ImportRefused(str(exc) + ': ' + item['file']) from exc
    if not STAMP.fullmatch(str(item['fetched_at_utc'])):
        raise ImportRefused('fetched_at_utc must look like 2026-10-19T08:00:00Z: ' + item['file'])
    content_type = item.get('content_type') or TYPES.get(path.suffix.lower())
    if content_type not in set(TYPES.values()):
        raise ImportRefused('only PDF, HTML and plain text are imported: ' + item['file'])
    if raw[:5] == b'%PDF-' and content_type != 'application/pdf':
        raise ImportRefused('PDF bytes under a non-PDF content type: ' + item['file'])
    extra = dict(browser=str(item['browser']), origin=item.get('origin') or 'live', receipt_file=item['file'])
    if extra['origin'] not in ('live', 'wayback'):
        raise ImportRefused('origin must be live or wayback: ' + item['file'])
    if extra['origin'] == 'wayback':
        stamp = str(item.get('snapshot_timestamp', ''))
        host = urlsplit(final_url).hostname or ''
        if not re.fullmatch(r'\d{14}', stamp) or host != 'web.archive.org':
            raise ImportRefused('a Wayback copy needs snapshot_timestamp (14 digits) and a web.archive.org final_url: ' + item['file'])
        extra.update(snapshot_timestamp=stamp, snapshot_url=final_url, live_error=item.get('live_error'))
    result = dict(status=200, final_url=final_url, content_type=content_type, charset=item.get('charset') or '', data=raw)
    return url, result, extra, _epoch(item['fetched_at_utc'])


def _annotate(packet, sha256, url, extra):
    """Add the browser fields to the entry record_fetch just appended (same atomic write as the fetch log)."""
    _, log = ef.packet_files(packet)
    value = ef._read(log)
    entry = next(e for e in reversed(value['entries']) if e['sha256'] == sha256 and e['url'] == url and e.get('via') == VIA)
    entry.update(extra)
    ef._atomic(log, value)
    return entry


def import_delivery(root, job_id, delivery, apply=False):
    """Plan (apply=False) or write (apply=True) every item of a delivery into the packet's archive and fetch log."""
    handoff = load_handoff(delivery)
    with q.locked(root):
        packet = q.locate(root, job_id)
        if packet is None or packet.parent.name not in OPEN_STATES:
            raise ImportRefused('packet must be pending or in_progress (found: %s)' % (packet.parent.name if packet else 'none'))
        job, binding = ef.history_binding(packet / 'PACKET.json')
        if job is None:
            raise ImportRefused('only V9 official-history packets with network access take evidence')
        if str(ef._archive_dir(job).parent) not in binding['allowed_write_paths']:
            raise ImportRefused('archive directory is outside the packet write scope')
        if handoff.get('design_id') not in (None, job.design_id):
            raise ImportRefused('delivery is for %s, packet is for %s' % (handoff.get('design_id'), job.design_id))
        entries = ef._entries(packet)
        if len(entries) + len(handoff['items']) > ef.MAX_FETCHES_PER_PACKET:
            raise ImportRefused('fetch limit for this packet would be exceeded')
        checked = [check_item(delivery, item) for item in handoff['items']]   # all items validate before anything is written
        report = []
        for url, result, extra, when in checked:
            sha = hashlib.sha256(result['data']).hexdigest()
            done = any(e['sha256'] == sha and e['url'] == url and e.get('via') == VIA for e in entries)
            report.append(dict(url=url, final_url=result['final_url'], sha256=sha, bytes=len(result['data']),
                               archive_path='evidence/' + sha + ef._extension(result['data'], result['content_type']),
                               action='already_imported' if done else ('import' if apply else 'would_import'), origin=extra['origin']))
            if apply and not done:
                ef.record_fetch(packet, job, url, result, VIA, now=when)
                _annotate(packet, sha, url, extra)
    return dict(job_id=job_id, packet_state=packet.parent.name, design_id=job.design_id, applied=apply,
                archive_dir=str(ef._archive_dir(job)), items=report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=q.root_for(Path(__file__).resolve().parents[3]))
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--delivery', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='write; without it the command only prints the plan')
    args = parser.parse_args(argv)
    try:
        value = import_delivery(args.root, args.job_id, args.delivery, apply=args.apply)
    except (ImportRefused, ef.FetchError, ValueError, OSError) as exc:
        print(json.dumps(dict(refused=str(exc)), indent=2))
        return 1
    print(json.dumps(value, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
