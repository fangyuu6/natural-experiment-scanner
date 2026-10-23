#!/usr/bin/env python3
"""Outcome-free, immutable L1 parent-text frame and stratified pilot draw."""
import argparse
import collections
import csv
import hashlib
import json
import math
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/plans/l3-ideas/I01'
QUEUE = ROOT / 'data/pipeline/queue_v2'
SEED = 2026100601
PILOT_N = 96
MAIN_N = 600
FOCUS = {'labor_market', 'public_health', 'social_insurance'}
FIELDS = ['source_text_id', 'source_record_id', 'cohort', 'source_group',
          'domain', 'domain_band', 'jurisdiction', 'state', 'year', 'era',
          'title', 'source_url', 'policy_family_id', 'strict_enacted_metadata',
          'metadata_band', 'queue_tracks', 'queue_band', 'stratum']
SAFE_QUERY = {'id', 'i', 'b', 's', 'n', 'h', 'l', 'sn', 'pn', 'year', 'bill',
              'billid', 'bill_id', 'billnumber', 'billtype', 'bill_number',
              'bill_type', 'number', 'type', 'doc', 'docname', 'docnum', 'leg',
              'session', 'sessionyear', 'sessionname', 'sa', 'sln', 'nav',
              'file', 'filename', 'bs', 'ses', 'page'}


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_url(value):
    try:
        u = urlsplit(str(value or ''))
        if u.scheme not in ('http', 'https') or u.username or u.password:
            return ''
        q = [(k, v) for k, v in parse_qsl(u.query)
             if k.lower() in SAFE_QUERY and len(v) < 160]
        return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), ''))
    except ValueError:
        return ''


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def write_csv(path, fields, rows):
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def read_jsonl(path, ledger):
    h = hashlib.sha256()
    size = 0
    with path.open('rb') as f:
        for line in f:
            h.update(line)
            size += len(line)
            yield json.loads(line)
    ledger.append({'path': str(path.relative_to(ROOT)), 'bytes': size,
                   'sha256': h.hexdigest()})


def era(year):
    if not year:
        return 'unknown'
    y = int(year)
    if y < 1995:
        return 'pre1995'
    if y <= 2005:
        return '1995_2005'
    if y <= 2015:
        return '2006_2015'
    if y <= 2020:
        return '2016_2020'
    if y <= 2024:
        return '2021_2024'
    return '2025_plus'


def allocation(groups, total, minimum):
    """Fixed minimum plus sqrt(N), focus factor 3; largest remainders, capped."""
    result = {s: min(minimum, len(rows)) for s, rows in groups.items()}
    if sum(result.values()) > total or sum(map(len, groups.values())) < total:
        raise ValueError('sample size incompatible with allocation minima')
    while sum(result.values()) < total:
        remaining = total - sum(result.values())
        active = {s: math.sqrt(len(rows)) * (3 if '|focus|' in s else 1)
                  for s, rows in groups.items() if result[s] < len(rows)}
        norm = sum(active.values())
        quotas = {s: remaining * v / norm for s, v in active.items()}
        for s in active:
            result[s] += min(math.floor(quotas[s]), len(groups[s]) - result[s])
        # One per remainder, then recalculate if a stratum hit its capacity.
        for s in sorted(active, key=lambda k: (-(quotas[k] % 1), k)):
            if sum(result.values()) >= total:
                break
            if result[s] < len(groups[s]):
                result[s] += 1
    return result


def draw_key(row):
    value = f"{SEED}|{row['stratum']}|{row['source_text_id']}".encode()
    return hashlib.sha256(value).hexdigest(), row['source_text_id']


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    # No implicit rebuilds of an existing frame, even before hub approval.
    artifacts = ['FRAME.csv', 'FRAME_COUNTS.json', 'STRATA.csv',
                 'DOMAIN_COUNTS.csv', 'YEAR_COUNTS.csv', 'PILOT.csv',
                 'MAIN_SAMPLE.csv', 'CODER_INPUT.csv', 'FRAME_MANIFEST.json']
    if any((OUT / name).exists() for name in artifacts):
        raise SystemExit('frame artifacts exist; use --verify or a new protocol version')
    ledger = []
    tracks = collections.defaultdict(set)
    queue_rows = {}
    for name, track in [('did_designs.jsonl', 'DiD'),
                        ('rdd_candidates.jsonl', 'RDD'), ('rct_mentions.jsonl', 'RCT')]:
        n = 0
        for d in read_jsonl(QUEUE / name, ledger):
            if d.get('disposition') == 'government_gambling_lottery':
                continue
            n += 1
            for r in d.get('source_records', []):
                tracks[r['source_text_id']].add(track)
        queue_rows[track] = n
    parents = {}
    source_rows = 0
    for d in read_jsonl(QUEUE / 'sources.jsonl', ledger):
        source_rows += 1
        pid = d['source_text_id']
        if pid in parents:
            continue
        # File order: legacy whole text first, then supplementary whole/chapters.
        # No scores, suggested outcomes, design answers or extraction prose exported.
        group = ('state' if d.get('state') else
                 'federal' if d.get('jurisdiction') == 'US Federal' else 'unresolved')
        domain = d.get('domain') or 'unknown'
        y = d.get('year') or ''
        band = 'focus' if domain in FOCUS else 'other'
        meta = ('complete' if y and d.get('title') and d.get('jurisdiction')
                and d.get('enactment_layer_available') else 'gap')
        q = 'queued' if pid in tracks else 'not_queued'
        parents[pid] = {
            'source_text_id': pid, 'source_record_id': d['source_record_id'],
            'cohort': d['cohort'], 'source_group': group,
            'domain': domain, 'domain_band': band,
            'jurisdiction': d.get('jurisdiction', ''), 'state': d.get('state', ''),
            'year': y, 'era': era(y), 'title': d.get('title', ''),
            'source_url': safe_url(d.get('source_url', '')),
            'policy_family_id': d.get('policy_family_id') or '',
            'strict_enacted_metadata': int(bool(d.get('strict_enacted'))),
            'metadata_band': meta, 'queue_tracks': '+'.join(sorted(tracks[pid])),
            'queue_band': q, 'stratum': '|'.join([group, band, q, meta])}
    groups = collections.defaultdict(list)
    for row in parents.values():
        groups[row['stratum']].append(row)
    main = allocation(groups, MAIN_N, 12)
    pilot = allocation(groups, PILOT_N, 4)
    if any(pilot[s] > main[s] for s in groups):
        raise ValueError('pilot must be nested in main sample')
    frame = sorted(parents.values(), key=lambda r: r['source_text_id'])
    write_csv(OUT / 'FRAME.csv', FIELDS, frame)
    sample_rows = []
    pilot_rows = []
    strata = []
    for s in sorted(groups):
        rows = groups[s]
        selected = sorted(rows, key=draw_key)[:main[s]]
        for rank, row in enumerate(selected, 1):
            selected_row = dict(row, within_stratum_rank=rank,
                                stratum_N=len(rows), main_n_h=main[s],
                                main_pi=main[s] / len(rows),
                                main_weight=len(rows) / main[s],
                                pilot_n_h=pilot[s], pilot_pi=pilot[s] / len(rows),
                                pilot_weight=len(rows) / pilot[s],
                                pilot_selected=int(rank <= pilot[s]))
            sample_rows.append(selected_row)
            if rank <= pilot[s]:
                pilot_rows.append(selected_row)
        known_years = [int(r['year']) for r in rows if r['year']]
        strata.append({'stratum': s, 'N': len(rows), 'pilot_n': pilot[s],
                       'main_n': main[s], 'pilot_pi': pilot[s] / len(rows),
                       'main_pi': main[s] / len(rows),
                       'known_year_min': min(known_years) if known_years else '',
                       'known_year_max': max(known_years) if known_years else '',
                       'unknown_year_N': sum(not r['year'] for r in rows),
                       'strict_enacted_metadata_N': sum(r['strict_enacted_metadata'] for r in rows)})
    sample_fields = FIELDS + ['within_stratum_rank', 'stratum_N', 'main_n_h',
                             'main_pi', 'main_weight', 'pilot_n_h', 'pilot_pi',
                             'pilot_weight', 'pilot_selected']
    write_csv(OUT / 'MAIN_SAMPLE.csv', sample_fields, sample_rows)
    write_csv(OUT / 'PILOT.csv', sample_fields, pilot_rows)
    write_csv(OUT / 'STRATA.csv', list(strata[0]), strata)
    # Identical answer-free materials for A/B; never give them full frame/sample.
    coder_fields = ['task_id', 'source_text_id', 'title', 'source_url', 'jurisdiction', 'year']
    blinded = []
    for index, row in enumerate(sorted(pilot_rows, key=lambda r: r['source_text_id']), 1):
        blinded.append(dict(row, task_id=f'I01-P{index:03d}'))
    write_csv(OUT / 'CODER_INPUT.csv', coder_fields, blinded)
    for filename, keys in [('DOMAIN_COUNTS.csv', ['source_group', 'domain']),
                           ('YEAR_COUNTS.csv', ['source_group', 'year'])]:
        counts = collections.Counter(tuple(str(r[k] or 'unknown') for k in keys) for r in frame)
        write_csv(OUT / filename, keys + ['N'],
                  [dict(zip(keys, k), N=n) for k, n in sorted(counts.items())])
    count = lambda key: dict(sorted(collections.Counter(
        str(r[key]) if r[key] not in (None, '') else 'unknown' for r in frame).items()))
    summary = {'version': 'I01-frame-v1', 'seed': SEED,
               'unit': 'provisional parent source-text ID; not certified enacted instruments',
               'frame_N': len(frame), 'source_extraction_rows': source_rows,
               'pilot_N': len(pilot_rows), 'prospective_main_N': len(sample_rows),
               'nonempty_strata': len(groups), 'counts_by_source': count('source_group'),
               'counts_by_domain': count('domain'), 'counts_by_year': count('year'),
               'counts_by_era': count('era'), 'counts_by_cohort': count('cohort'),
               'counts_by_metadata': count('metadata_band'), 'counts_by_queue': count('queue_band'),
               'counts_by_strict_enacted_metadata': count('strict_enacted_metadata'),
               'queue_records_by_track': queue_rows, 'strata': strata,
               'outcomes_coded': 0, 'target_associations_computed': 0, 'downloads': []}
    write_json(OUT / 'FRAME_COUNTS.json', summary)
    code = Path(__file__).resolve()
    write_json(OUT / 'FRAME_MANIFEST.json', {
        'version': 'I01-frame-v1', 'status': 'FRAME_FROZEN_PROTOCOL_PENDING_HUB',
        'seed': SEED, 'frame_N': len(frame), 'pilot_N': PILOT_N, 'main_N': MAIN_N,
        'inputs': ledger, 'code': {'path': str(code.relative_to(ROOT)), 'sha256': file_hash(code)},
        'selection': 'SHA256(seed|stratum|source_text_id) sorted ascending; SRS without replacement within strata',
        'allocation': 'minimum 4 pilot / 12 main; remaining sqrt(N), focus multiplier 3, capped largest remainders',
        'artifacts': [{'path': str((OUT / n).relative_to(ROOT)),
                       'bytes': (OUT / n).stat().st_size, 'sha256': file_hash(OUT / n)}
                      for n in artifacts if n != 'FRAME_MANIFEST.json'],
        'protocol_frozen': False, 'outcomes_coded': 0, 'target_associations_computed': 0})
    print(json.dumps({k: summary[k] for k in ['frame_N', 'pilot_N', 'prospective_main_N', 'nonempty_strata', 'counts_by_source', 'counts_by_queue', 'counts_by_metadata']}))


def verify(check_inputs=False):
    manifest = json.loads((OUT / 'FRAME_MANIFEST.json').read_text())
    items = manifest['artifacts'] + [manifest['code']]
    if check_inputs:
        items += manifest['inputs']
    for item in items:
        p = ROOT / item['path']
        if file_hash(p) != item['sha256']:
            raise SystemExit(f'hash mismatch: {item["path"]}')
    with (OUT / 'FRAME.csv').open() as f:
        frame = list(csv.DictReader(f))
    ids = {r['source_text_id'] for r in frame}
    if len(ids) != len(frame) or len(ids) != manifest['frame_N']:
        raise SystemExit('frame ID/count mismatch')
    groups = collections.defaultdict(list)
    for r in frame:
        groups[r['stratum']].append(r)
    for filename, size, minimum in [('PILOT.csv', PILOT_N, 4), ('MAIN_SAMPLE.csv', MAIN_N, 12)]:
        expected_allocation = allocation(groups, size, minimum)
        with (OUT / filename).open() as f:
            drawn = list(csv.DictReader(f))
        actual = collections.defaultdict(list)
        for r in drawn:
            actual[r['stratum']].append(r['source_text_id'])
            if r['source_text_id'] not in ids:
                raise SystemExit('sample outside frame')
        if len(drawn) != size or len({r['source_text_id'] for r in drawn}) != size:
            raise SystemExit('sample size/uniqueness mismatch')
        for s, n in expected_allocation.items():
            expected = [r['source_text_id'] for r in sorted(groups[s], key=draw_key)[:n]]
            if actual[s] != expected:
                raise SystemExit(f'draw mismatch: {filename}, {s}')
    print('PASS: hashes, unique parent frame, allocations and deterministic pilot/main draws')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--verify', action='store_true')
    p.add_argument('--check-inputs', action='store_true')
    args = p.parse_args()
    if args.check_inputs and not args.verify:
        p.error('--check-inputs requires --verify')
    verify(args.check_inputs) if args.verify else build()
