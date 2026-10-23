"""T226 P1: one shared, hashed pack of pages for the hosts that block the queue workers' own fetches.

Why: 55 of 85 incomplete history chunks mention HTTP 403 / blocked pages (justia, scstatehouse, codes.ohio, akleg, leginfo,
ilga, legislature.ky, nysenate, ncleg, findlaw). Every worker re-hit the same wall. A page fetched ONCE in a normal browser
(by the hub or a browser agent) goes into this pack; ``fetch-evidence`` then serves it to any packet that asks for that URL, with
no network call, and records it in the packet's own EVIDENCE_LOG exactly like a fetch (bytes in the packet's evidence archive,
SHA256, UTC time, ``via: browser``, ``shared_pack: true``). The V9 evidence rule, ``verify-quote`` and ``evidence-text`` need no change.

Layout: <queue root>/SHARED_PACK/INDEX.json and pages/<sha256>.<ext> (read-only). Delivery format = browser_evidence's HANDOFF.json
(same validation: hash, size, URL rules, only PDF/HTML/text), with an optional ``units`` list per item that tags a page with the
jurisdiction codes it documents (this is what lets P2 know a unit now has a route).

    python -B -m scripts.l2_pipeline.agents.shared_pack wanted [--top N] [--csv PATH]   # worklist of blocked URLs by host (read-only)
    python -B -m scripts.l2_pipeline.agents.shared_pack list
    python -B -m scripts.l2_pipeline.agents.shared_pack add --delivery DIR [--apply]   # plan unless --apply (needs L2_HUB=1)
"""
from __future__ import annotations
import argparse, csv, hashlib, json, os, sys, time, uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

PACK = 'SHARED_PACK'
SCHEMA = 'l2_shared_pack_v1'
VIA = 'browser'


def pack_dir(root):
    return Path(root) / PACK


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError, UnicodeError):
        return default


def index(root):
    return _read(pack_dir(root) / 'INDEX.json', dict(schema=SCHEMA, entries=[]))


def _canon(url):
    from .evidence_fetch import canonical_url
    return canonical_url(url)


def lookup(root, url):
    """The pack entry for ``url`` (matched on URL or final URL identity), with its file path, or None."""
    wanted_url = _canon(url)
    for entry in index(root).get('entries', []):
        if wanted_url in {_canon(entry['url']), _canon(entry.get('final_url') or entry['url'])}:
            path = pack_dir(root) / entry['file']
            if path.is_file():
                return dict(entry, path=str(path))
    return None


def covers(root, unit, design_id=None):
    """True when the pack holds at least one page tagged with this unit (and, if the entry names designs, this design)."""
    for entry in index(root).get('entries', []):
        if unit in (entry.get('units') or []) and (not entry.get('designs') or design_id in entry['designs']):
            if (pack_dir(root) / entry['file']).is_file():
                return True
    return False


def as_fetch_result(entry):
    """(fetch-result dict, extra log fields) for a pack entry; the file bytes are re-hashed first."""
    raw = Path(entry['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ValueError('Shared pack file differs from its hash: ' + entry['file'])
    result = dict(status=200, final_url=entry.get('final_url') or entry['url'], content_type=entry.get('content_type', ''),
                  charset=entry.get('charset', ''), data=raw)
    extra = dict(browser=entry.get('browser'), origin=entry.get('origin') or 'live', shared_pack=True, pack_added_at=entry.get('added_at'))
    if extra['origin'] == 'wayback':
        extra.update(snapshot_timestamp=entry.get('snapshot_timestamp'), snapshot_url=entry.get('snapshot_url'), live_error=entry.get('live_error'))
    return result, extra


def add_delivery(root, delivery, apply=False, now=None):
    """Validate every item (browser_evidence rules), then store content-addressed and index. Returns a report list."""
    from . import browser_evidence as be, evidence_fetch as ef, sonnet_queue as q
    now = time.time() if now is None else now
    handoff = be.load_handoff(delivery)
    checked = []
    for item in handoff['items']:
        url, result, extra, when = be.check_item(delivery, item)
        units = item.get('units') or []
        if not isinstance(units, list) or any(not isinstance(u, str) for u in units):
            raise be.ImportRefused('units must be a list of jurisdiction codes: ' + str(item.get('file')))
        checked.append((url, result, extra, units, item))
    report = []
    with q.locked(root):
        value = index(root)
        have = {(e['url'], e['sha256']) for e in value['entries']}
        for url, result, extra, units, item in checked:
            digest = hashlib.sha256(result['data']).hexdigest()
            name = digest + ef._extension(result['data'], result['content_type'])
            action = 'already_in_pack' if (url, digest) in have else ('add' if apply else 'would_add')
            report.append(dict(url=url, sha256=digest, bytes=len(result['data']), file='pages/' + name, units=units, action=action))
            if apply and action == 'add':
                target = pack_dir(root) / 'pages' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    tmp = target.with_name('.' + name + '.' + uuid.uuid4().hex + '.tmp')
                    tmp.write_bytes(result['data'])
                    os.replace(tmp, target)
                    target.chmod(0o444)
                value['entries'].append(dict(url=url, final_url=result['final_url'], sha256=digest, bytes=len(result['data']),
                    content_type=result['content_type'], charset=result.get('charset', ''), file='pages/' + name, units=units,
                    designs=item.get('designs') or [], fetched_at_utc=item['fetched_at_utc'], added_at=now, **extra))
        if apply and any(r['action'] == 'add' for r in report):
            q.atomic(pack_dir(root) / 'INDEX.json', value)
    return report


# Hub note 2026-10-22: the first curated-route blind audit packets (campus, dv_firearm_removal, mental_health_commitment) came back
# incomplete because the Texas statutes site returns an empty page and no readable official text or amendment history was reached
# for NM, FL, MA, PA, GA, LA, MD (session-law dating before 1999 generally). Those units and hosts come first in the worklist.
PRIORITY_UNITS = ('TX', 'NM', 'FL', 'MA', 'PA', 'GA', 'LA', 'MD')
PRIORITY_HOSTS = ('statutes.capitol.texas.gov', 'capitol.texas.gov', 'texas.public.law', 'tlc.texas.gov', 'statutes.legis.state.tx.us')
ASK = 'official session-law or statute-history page (with amendment history); a Wayback copy if the live page is empty or blocked'


def _units_left_out(chunk, value):
    recorded = {r.get('unit') for r in value.get('records', []) if isinstance(r, dict)}
    if chunk:
        return [u for u in chunk['units'] if u not in recorded]
    named = {e.get('unit') for e in value.get('research_log', []) or [] if isinstance(e, dict)}
    inc = value.get('incomplete_units')
    named |= set(inc) if isinstance(inc, dict) else {x.get('unit') if isinstance(x, dict) else x for x in (inc or [])}
    return sorted(u for u in named if u and u not in recorded)


def wanted(root, top=15):
    """Worklist of pages the queue workers could not fetch: URLs named in the research log for units left out with a blocked
    host reason (chunk packets and the curated-route blind audit packets). Rows for the priority jurisdictions / hosts come first.
    Read-only. Returns dict(hosts=[(host, n)], rows=[...])."""
    from . import unit_blockers as ub
    rows, hosts, seen = [], Counter(), set()
    done = Path(root) / 'done'
    packets = sorted(done.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True) if done.is_dir() else []
    for packet in packets:
        binding = _read(packet / 'PACKET.json') or {}
        chunk = binding.get('chunk')
        value = _read(binding.get('output_path', ''))
        if not isinstance(value, dict) or not isinstance(value.get('records'), list):
            continue
        design = (chunk or {}).get('design_id') or (binding.get('row') or {}).get('design_id')
        for unit in _units_left_out(chunk, value):
            if not ub.classify(ub.unit_notes(value, unit)):
                continue
            for entry in value.get('research_log', []) or []:
                if isinstance(entry, dict) and entry.get('unit') == unit:
                    for url in entry.get('urls') or []:
                        key = (design, unit, url)
                        if key in seen or lookup(root, url):
                            continue
                        seen.add(key)
                        host = (urlsplit(url).hostname or '').lower()
                        host = host[4:] if host.startswith('www.') else host
                        first = unit in PRIORITY_UNITS or host in PRIORITY_HOSTS
                        hosts[host] += 1
                        rows.append(dict(priority=1 if first else 0, host=host, design_id=design, unit=unit, url=url, ask=ASK))
    rows.sort(key=lambda r: (-r['priority'], r['host'], r['design_id'] or '', r['unit']))
    ranked = Counter()
    for r in rows:
        if r['priority']:
            ranked[r['host']] += 1
    ordered = ranked.most_common(top) + [h for h in hosts.most_common(top) if h[0] not in ranked][:max(0, top - len(ranked))]
    return dict(hosts=ordered, rows=rows, priority_rows=sum(r['priority'] for r in rows))


def main(argv=None):
    here = Path(__file__).resolve().parents[3]
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('action', choices=['wanted', 'list', 'add'])
    p.add_argument('--root', type=Path, default=here / 'data/l2_pipeline/registry/SONNET_QUEUE')
    p.add_argument('--top', type=int, default=15)
    p.add_argument('--csv', type=Path)
    p.add_argument('--delivery', type=Path)
    p.add_argument('--apply', action='store_true')
    a = p.parse_args(argv)
    if a.action == 'wanted':
        w = wanted(a.root, a.top)
        if a.csv:
            with a.csv.open('w', newline='') as h:
                wr = csv.DictWriter(h, fieldnames=['priority', 'host', 'design_id', 'unit', 'url', 'ask'])
                wr.writeheader()
                wr.writerows(w['rows'])
        json.dump(dict(hosts=w['hosts'], urls=len(w['rows']), priority_urls=w['priority_rows'], csv=str(a.csv) if a.csv else None), sys.stdout, indent=1)
    elif a.action == 'list':
        json.dump(index(a.root), sys.stdout, indent=1)
    else:
        if not a.delivery:
            p.error('--delivery required')
        if a.apply and os.environ.get('L2_HUB') != '1':
            p.error('add --apply is a hub operation: set L2_HUB=1')
        json.dump(add_delivery(a.root, a.delivery, a.apply), sys.stdout, indent=1)
    print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
