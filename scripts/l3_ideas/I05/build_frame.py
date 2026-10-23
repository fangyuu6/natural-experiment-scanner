#!/usr/bin/env python3
"""I05 phase 1: metadata-only frame and seeded samples. No analysis values."""
import argparse
import collections
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import re
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/plans/l3-ideas/I05'
SEED = 2026100605
INPUTS = [
    'data/output/policy_db_20260415_483k.csv',
    'data/pipeline/queue_v2/sources.jsonl',
    'data/pipeline/queue_v2/did_designs.jsonl',
    'data/pipeline/queue_v2/rdd_candidates.jsonl',
    'data/pipeline/queue_v2/rct_mentions.jsonl',
]
FIELDS = ['frame_id', 'source_text_id', 'source_kind', 'domain', 'domain_group',
          'queue_tracks', 'queue_group', 'stratum', 'year', 'coverage_band',
          'jurisdiction', 'policy_family_id', 'provisional_legal_id', 'title',
          'source_url', 'metadata_strict_enacted', 'legacy_viable_strict']


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return {'path': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size,
            'sha256': h.hexdigest()}


def write_json(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def write_csv(name, records, fields):
    with (OUT / name).open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(records)


def domain_group(domain):
    if domain in ('criminal_justice', 'labor_market', 'social_insurance'):
        return domain
    if domain in ('public_health', 'education'):
        return 'health_education'
    return 'other_domains'


def allocation(groups, total, floor):
    """Minimum per nonempty stratum, then sqrt(N), largest remainders."""
    a = {h: min(floor, len(v)) for h, v in groups.items()}
    if sum(a.values()) > total:
        raise ValueError('Too many strata for frozen sample size')
    while sum(a.values()) < min(total, sum(map(len, groups.values()))):
        active = [h for h in sorted(groups) if a[h] < len(groups[h])]
        remaining = total - sum(a.values())
        denom = sum(math.sqrt(len(groups[h])) for h in active)
        quota = {h: remaining * math.sqrt(len(groups[h])) / denom for h in active}
        added = 0
        for h in active:
            n = min(int(quota[h]), len(groups[h]) - a[h])
            a[h] += n
            added += n
        for h in sorted(active, key=lambda h: (-(quota[h] % 1), h)):
            if sum(a.values()) >= total:
                break
            if a[h] < len(groups[h]):
                a[h] += 1
                added += 1
        if not added:
            raise ValueError('Allocation stalled')
    return a


def select(groups, sizes, label):
    result = []
    for h in sorted(groups):
        # Independent per-stratum streams: unaffected by iteration order elsewhere.
        key = int(hashlib.sha256(f'{SEED}|{label}|{h}'.encode()).hexdigest(), 16)
        pool = sorted(groups[h], key=lambda r: r['frame_id'])
        random.Random(key).shuffle(pool)
        result.extend(pool[:sizes[h]])
    return result


def tally(rows):
    return {k: dict(sorted(collections.Counter(str(r[k]) for r in rows).items()))
            for k in ('source_kind', 'domain', 'domain_group', 'year',
                      'coverage_band', 'stratum', 'queue_group', 'metadata_strict_enacted')}


def verify():
    manifest = json.loads((OUT / 'FRAME_MANIFEST.json').read_text())
    errors = []
    for item in manifest['inputs'] + manifest['artifacts']:
        p = ROOT / item['path']
        if not p.exists() or digest(p) != item:
            errors.append('Hash/size mismatch: ' + item['path'])
    with (OUT / 'FRAME.csv').open() as f:
        frame = list(csv.DictReader(f))
    with (OUT / 'MAIN_SAMPLE.csv').open() as f:
        main = list(csv.DictReader(f))
    with (OUT / 'PILOT_SAMPLE.csv').open() as f:
        pilot = list(csv.DictReader(f))
    with (OUT / 'COVERAGE_FRAME.csv').open() as f:
        coverage = list(csv.DictReader(f))
    for name, rows in [('frame', frame), ('main', main), ('pilot', pilot), ('coverage', coverage)]:
        if len({r['frame_id'] for r in rows}) != len(rows):
            errors.append('Duplicate IDs: ' + name)
    groups = collections.defaultdict(list)
    for r in frame:
        groups[r['stratum']].append(r)
    if len(frame) != manifest['frame_size'] or len(main) != 200:
        errors.append('Unexpected frame/main size')
    main_sizes = allocation(groups, 200, 8)
    expected_main = select(groups, main_sizes, 'main')
    if [r['frame_id'] for r in main] != [r['frame_id'] for r in expected_main]:
        errors.append('Main draw not reproducible')
    mg = collections.defaultdict(list)
    for r in expected_main:
        mg[r['stratum']].append(r)
    core_sizes = allocation(mg, 40, 2)
    expected_pilot = select(mg, core_sizes, 'pilot_core')
    cg = collections.defaultdict(list)
    for r in coverage:
        cg[r['coverage_band']].append(r)
    sizes = {h: min(10, len(v)) for h, v in cg.items()}
    expected_pilot += select(cg, sizes, 'pilot_coverage')
    if [r['frame_id'] for r in pilot] != [r['frame_id'] for r in expected_pilot]:
        errors.append('Pilot draw not reproducible')
    for r in main:
        n, N = main_sizes[r['stratum']], len(groups[r['stratum']])
        if int(r['stratum_N']) != N or int(r['sample_n']) != n or abs(float(r['inclusion_probability']) - n / N) > 1e-12:
            errors.append('Bad sampling probability: ' + r['frame_id'])
    if any(r['coverage_band'] != 'complete_window' for r in main):
        errors.append('Incomplete-window unit in primary sample')
    print(json.dumps({'verification': 'FAIL' if errors else 'PASS', 'issues': errors,
                      'frame_size': len(frame), 'main_size': len(main),
                      'pilot_size': len(pilot), 'outcome_coding_calls': 0,
                      'association_estimation_calls': 0}, indent=2))
    return 1 if errors else 0


def build():
    if (OUT / 'FRAME_MANIFEST.json').exists():
        raise SystemExit('Frame already locked; --verify only. Amendments need a new version.')
    csv.field_size_limit(20_000_000)
    base, excluded = {}, collections.Counter()
    duplicates = 0
    with (ROOT / INPUTS[0]).open() as f:
        for r in csv.DictReader(f):
            sid = r['policy_id']
            if sid in base:
                duplicates += 1
                continue
            if r['source_family'] == 'State Legislation':
                kind = 'state_legislation'
            elif r['source_family'] == 'Federal Legislation' and re.search(r'plaw-\d+publ\d+$', sid):
                kind = 'federal_public_law'
            else:
                excluded[r['source_family']] += 1
                continue
            # No score, suggested outcome/data or extracted beneficiary field enters frame.
            base[sid] = {'title': r['title'], 'source_url': r['url'],
                         'source_kind': kind, 'jurisdiction': r['jurisdiction']}
    tracks = collections.defaultdict(set)
    for name in INPUTS[2:]:
        with (ROOT / name).open() as f:
            for line in f:
                r = json.loads(line)
                if r.get('disposition') == 'government_gambling_lottery':
                    continue
                for s in r['source_records']:
                    if s['source_text_id'] in base:
                        tracks[s['source_text_id']].add(r['track'])
    matched = set()
    with (ROOT / INPUTS[1]).open() as f:
        for line in f:
            r = json.loads(line)
            sid = r['source_text_id']
            if sid not in base or sid in matched or r['source'] != 'legacy_csv' or r['chapter_id']:
                continue
            matched.add(sid)
            year = r['year']
            try:
                year = int(year)
            except (ValueError, TypeError):
                year = None
            kind = base[sid]['source_kind']
            lower = 2016 if kind == 'state_legislation' else 1995
            if year is None:
                band = 'unknown_year'
            elif lower <= year <= 2021:
                band = 'complete_window'
            elif 2022 <= year <= 2026:
                band = 'recent_window'
            else:
                raise ValueError(f'Unexpected historical coverage {sid}: {year}')
            domain = r['domain'] or 'other'
            q = 'queue_lead' if tracks[sid] else 'not_queued'
            base[sid].update(frame_id='I05-' + sid, source_text_id=sid,
                domain=domain, domain_group=domain_group(domain),
                queue_tracks='|'.join(sorted(tracks[sid])), queue_group=q,
                stratum=f'{kind}|{domain_group(domain)}|{q}', year=year or '',
                coverage_band=band, policy_family_id=r['policy_family_id'],
                provisional_legal_id=r['legal_instrument_id'],
                metadata_strict_enacted=str(r['strict_enacted']).lower(),
                legacy_viable_strict=str(r['legacy_viable_strict']).lower())
    if matched != set(base):
        raise ValueError('Unmatched legacy metadata IDs: ' + str(len(set(base) - matched)))
    rows = sorted(base.values(), key=lambda r: r['frame_id'])
    frame = [r for r in rows if r['coverage_band'] == 'complete_window']
    coverage = [r for r in rows if r['coverage_band'] != 'complete_window']
    groups = collections.defaultdict(list)
    for r in frame:
        groups[r['stratum']].append(r)
    sizes = allocation(groups, 200, 8)
    main = select(groups, sizes, 'main')
    mg = collections.defaultdict(list)
    for r in main:
        mg[r['stratum']].append(r)
    ps = allocation(mg, 40, 2)
    pilot = select(mg, ps, 'pilot_core')
    sample_fields = FIELDS + ['sample_role', 'stratum_N', 'sample_n', 'inclusion_probability', 'weight']
    main_rows, pilot_rows = [], []
    core_ids = {r['frame_id'] for r in pilot}
    for r in main:
        h, N = r['stratum'], len(groups[r['stratum']])
        main_rows.append(dict(r, sample_role='main', stratum_N=N, sample_n=sizes[h],
                              inclusion_probability=sizes[h]/N, weight=N/sizes[h]))
    for r in pilot:
        h, N = r['stratum'], len(groups[r['stratum']])
        n = ps[h]
        pilot_rows.append(dict(r, sample_role='core_pilot', stratum_N=N, sample_n=n,
                               inclusion_probability=n/N, weight=N/n))
    cg = collections.defaultdict(list)
    for r in coverage:
        cg[r['coverage_band']].append(r)
    cs = {h: min(10, len(v)) for h, v in cg.items()}
    for r in select(cg, cs, 'pilot_coverage'):
        h, N = r['coverage_band'], len(cg[r['coverage_band']])
        pilot_rows.append(dict(r, sample_role='coverage_audit_only', stratum_N=N,
                               sample_n=cs[h], inclusion_probability=cs[h]/N, weight=N/cs[h]))
    write_csv('FRAME.csv', frame, FIELDS)
    write_csv('COVERAGE_FRAME.csv', coverage, FIELDS)
    write_csv('MAIN_SAMPLE.csv', main_rows, sample_fields)
    write_csv('PILOT_SAMPLE.csv', pilot_rows, sample_fields)
    counts = {'seed': SEED, 'unit': 'provisional parent text / legal instrument lead',
              'primary_frame_N': len(frame), 'coverage_frame_N': len(coverage),
              'main_sample_N': len(main), 'pilot_core_N': len(core_ids),
              'pilot_coverage_N': sum(cs.values()), 'primary': tally(frame),
              'coverage': tally(coverage), 'excluded_rows_by_source_family': dict(excluded),
              'duplicate_selected_source_ID_rows_collapsed': duplicates,
              'strata': [{'stratum': h, 'N': len(groups[h]), 'main_n': sizes[h],
                          'pilot_n': ps[h]} for h in sorted(groups)]}
    write_json('FRAME_COUNTS.json', counts)
    report = ['# I05 metadata frame: descriptive counts only', '',
              f'Seed: `{SEED}`. Primary candidate N={len(frame):,}; coverage-only N={len(coverage):,}.',
              f'Main draw: {len(main)}; pilot: {len(pilot_rows)} ({len(core_ids)} core + {sum(cs.values())} coverage checks).',
              'These are provisional parent-text instruments, not certified enacted laws or studies.',
              'No beneficiary, observability, paper population, result or association was coded.', '',
              '| Stratum: source / domain group / queue | Candidates | Main | Pilot core |',
              '|---|---:|---:|---:|']
    report += [f"| {x['stratum'].replace('|', ' / ')} | {x['N']} | {x['main_n']} | {x['pilot_n']} |" for x in counts['strata']]
    for title, key in [('Primary years', 'year'), ('Primary domains (LLM metadata)', 'domain'),
                       ('Coverage-only years (blank = unknown)', 'year')]:
        report += ['', '## ' + title, '', '| Metadata value | Count |', '|---|---:|']
        source_counts = counts['coverage' if title.startswith('Coverage') else 'primary'][key]
        report += [f'| {k or "unknown"} | {v} |' for k,v in source_counts.items()]
    report += ['', 'Federal public laws: 1995–2021; sampled state legislation: 2016–2021.',
               '2022–2026 and unknown-year leads are coverage-only; no primary-window substitution.',
               'Queue status uses membership in any DiD/RDD/nongambling RCT lead; it is unverified.',
               'All metadata eligibility states and all L1 screening outcomes remain in the frame.',
               'Federal bills without a public-law ID, regulations and executive texts are outside this statutory pilot.',
               'State corpus inclusion probabilities are unknown: weights target this corpus, never all US laws.',
               'Manifest locks inputs, script and frame/draw bytes before coding; protocol still awaits hub freeze.']
    (OUT / 'FRAME_REPORT.md').write_text('\n'.join(report) + '\n')
    artifacts = [digest(OUT / n) for n in ['FRAME.csv', 'COVERAGE_FRAME.csv',
                 'MAIN_SAMPLE.csv', 'PILOT_SAMPLE.csv', 'FRAME_COUNTS.json', 'FRAME_REPORT.md']]
    artifacts.append(digest(Path(__file__)))
    write_json('FRAME_MANIFEST.json', {'version': 'I05-frame-v1',
        'created_utc': datetime.now(timezone.utc).isoformat(), 'seed': SEED,
        'frame_size': len(frame), 'coverage_size': len(coverage),
        'sampling': 'stratified SRS without replacement; min8 main/min2 pilot then sqrt(N)',
        'analysis_protocol_status': 'DRAFT_AWAITING_HUB_FREEZE',
        'inputs': [digest(ROOT / p) for p in INPUTS], 'artifacts': artifacts,
        'downloads': [], 'outcome_coding_calls': 0, 'association_estimation_calls': 0})
    print(json.dumps({k: counts[k] for k in ['primary_frame_N','coverage_frame_N',
         'main_sample_N','pilot_core_N','pilot_coverage_N','seed']}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    sys.exit(verify() if args.verify else build())
