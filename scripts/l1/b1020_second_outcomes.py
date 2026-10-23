#!/usr/bin/env python3
"""L1-B card assembler for batch bulk_20261020/second_outcomes: second-outcome (T185) cards on policies whose adoption history is
already certified in L2 (automation.history_reuse, HUB_RULING_20261016_HISTORY_REUSE).

Reads one small outcome spec per card (written by a card-writer), derives the policy and adoption parts from the
SOURCE design's current card (so the treatment predicate, mechanism, universe, policy family and coverage_end are
identical, as the reuse ruling requires), and builds the card with the b1015_cards assembler pinned to the current
outcome catalog. Read-only on L2: no outcome value is decoded, no network call, no registry or code change.

Usage (repo root):
  PYTHONPATH=$HOME/Library/Python/3.9/lib/python/site-packages \
  .venv_did/bin/python -B scripts/l1/b1020_second_outcomes.py probe --family F --target-outcome T [--construct C]
  .venv_did/bin/python -B scripts/l1/b1020_second_outcomes.py source --design-id D        # facts a writer needs
  .venv_did/bin/python -B scripts/l1/b1020_second_outcomes.py build --specs DIR --out DIR [--report FILE]
  (then)  .venv_did/bin/python -B scripts/l1/b1015_cards.py check --cards DIR  (patched for V16 via this script's `check`)
  .venv_did/bin/python -B scripts/l1/b1020_second_outcomes.py check --cards DIR [--report FILE]
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts' / 'l1'))
import b1020_cards as B  # noqa: E402

B.BATCH = 'bulk_20261020'
B.QUEUE_VERSION = 'second_outcomes_20261020'
B.SCREEN_VERSION = 'bulk_20261020_second_outcomes_v1'
GROUP = 'second_outcomes'
RULING = 'docs/plans/l2-pipeline/HUB_RULING_20261016_HISTORY_REUSE.md'
LEFT_LATE = {'left_censored', 'late_adopter'}


def active_rows():
    act = json.loads((ROOT / 'data/l2_pipeline/registry/ACTIVE_DESIGNS.json').read_text())['designs']
    return {r['design_id']: r for r in act}


def source_card(design_id):
    rows = active_rows()
    if design_id not in rows:
        raise SystemExit('not an active design: ' + design_id)
    return json.loads(Path(rows[design_id]['card']).read_text())


def fix_source(entry):
    """Repo-relative, existing path (hash recomputed) or no path at all. Source cards of older batches carry paths
    that were rebased twice (.../cards/data/l2_pipeline/...); the real file is found from the last 'data/' segment."""
    e = dict(entry)
    path = e.get('path')
    if not path:
        return e
    cands = [Path(path)] if Path(path).is_absolute() else [ROOT / path]
    for marker in ('data/l2_pipeline/', 'data/did_samples/', 'data/pipeline/', 'data/'):
        i = str(path).rfind(marker)
        if i >= 0:
            cands.append(ROOT / str(path)[i:])
    for c in cands:
        if c.is_file():
            e['path'] = str(c.resolve().relative_to(ROOT))
            e['sha256'] = B.sha_file(c)
            return e
    e.pop('path', None)
    e.pop('sha256', None)
    return e


def derive(src, window):
    """policy and adoption parts of a b1015 record, taken from the source card's own screening list."""
    t = src['treatment']
    sa = t['screening_adoption']
    states = {s: int(y) for s, y in sa['adopters'].items() if isinstance(y, int)}
    other = {}
    for s, v in sa.get('other_status', {}).items():
        if s == 'DC':
            continue
        if v.get('status') in LEFT_LATE and isinstance(v.get('year'), int):
            states[s] = int(v['year'])  # re-classified against the new window by the assembler
        else:
            other[s] = copy.deepcopy(v)
    detail = {}
    for row in sa.get('adoption_screen', []) or []:
        detail[row['state']] = dict(date=row.get('effective_date') or str(row.get('effective_year')),
                                    confidence=row.get('confidence', 'secondary'), source=row.get('source', ''))
    adopt_sources = [dict(url=fs.get('url', ''), locator=fs.get('locator', ''), license=fs.get('license', 'unverified'),
                          **({'complete_list': fs['complete_list']} if 'complete_list' in fs else {}),
                          **({'path': fs['path']} if fs.get('path') else {}))
                     for fs in (fix_source(s) for s in src['sources'] if s['source_id'].startswith(('adoption_', 'statute_')))][:1] or \
        [dict(url='unresolved', locator='see source card', license='unverified')]
    return dict(
        policy=dict(name=src['policy_family'].replace('_', ' '), family=src['policy_family'],
                    predicate_clean=t['predicate'],
                    predicate_with_provenance=sa.get('researcher_predicate_with_provenance', t['predicate']),
                    clock_note=sa.get('clock_note', 'see source design card')),
        adoption=dict(window=[window[0], window[1]], coverage_end=t['coverage_end'],
                      complete_list=bool(sa.get('record_complete_list', True)),
                      states=dict(sorted(states.items())), never=list(sa.get('never_through_coverage_end', [])),
                      other_status=other, detail=detail, sources=adopt_sources,
                      reversals_or_ambiguities=copy.deepcopy(sa.get('reversals_or_ambiguities', [])),
                      after_window=copy.deepcopy(sa.get('adopters_after_window_treated_as_not_yet_or_never_in_window', {}))))


def source_s3_status(design_id, repo=ROOT):
    """'passed' when the source design's S3 (SUPPORT.json primary variant) passed or the design is estimated, 'failed' when its S3
    failed, 'unknown' when it has not reached S3. Read-only registry and design files, no outcome value."""
    from scripts.l2_pipeline.autopilot import Backend
    rows = active_rows()
    if design_id not in rows:
        return 'unknown'
    root = Backend(repo).root(rows[design_id])
    terminal = json.loads((repo / 'data/l2_pipeline/registry/TERMINAL_RUNS.json').read_text())['runs']
    if any(r.get('design_id') == design_id and r.get('study_estimated') is True for r in terminal):
        return 'passed'
    path = root / 'SUPPORT.json'
    if not path.is_file():
        return 'unknown'
    primary = next((v for v in json.loads(path.read_text()).get('variants', []) if v.get('variant') == 'primary'), None)
    if primary is None or primary.get('passed') is None:
        return 'unknown'
    return 'passed' if primary['passed'] is True else 'failed'


def build_one(spec):
    from scripts.l2_pipeline import rules as _rules
    _stamp = _rules.stamp()
    if _stamp is not None and _rules.number(_stamp['version']) >= 3 and source_s3_status(spec['source_design_id']) != 'passed':
        # R3 (W3): a second-outcome card is written only for a policy whose source design passed S3 or is estimated
        raise SystemExit('R3: source design %s has not passed S3 (status %s): no second-outcome card' % (
            spec['source_design_id'], source_s3_status(spec['source_design_id'])))
    src = source_card(spec['source_design_id'])
    if not spec.get('window'):
        yrs = src['outcome'].get('years') or src['outcome']['period_years']
        spec['window'] = [yrs[0], yrs[-1]]  # T185: inherit the first design's own outcome window
    d = derive(src, spec['window'])
    fo = copy.deepcopy(spec['first_order'])
    record = dict(
        record_id=spec['slug'], design_id=spec['design_id'], group=GROUP, seed=int(spec.get('seed', 20261016)),
        rating=spec.get('rating', 'B-'), verdict=spec.get('verdict', 'CONDITIONAL'), verdict_note=spec.get('verdict_note', ''),
        policy=d['policy'], population=spec['population'], outcome=spec['outcome'], first_order=fo, adoption=d['adoption'],
        notes=spec.get('notes', []), disposition=spec.get('disposition', 'ready'),
        first_order_class=spec.get('first_order_class', 'spillover'),
        l1_cross_reference=[dict(family=src['policy_family'], queue_version='second_outcomes_20261020',
                                 note='second outcome on the certified history of %s (history_reuse)' % spec['source_design_id'])])
    if spec.get('support_gap_rule'):
        record['outcome'] = dict(record['outcome'], support_gap_rule=spec['support_gap_rule'])
    card, diag = B.build_card(record)
    # exact policy identity with the source (reuse ruling section 3)
    st = src['treatment']
    card['mechanism'] = src['mechanism']
    card['treatment']['predicate'] = st['predicate']
    card['treatment']['universe'] = list(st['universe'])
    card['treatment']['coverage_end'] = st['coverage_end']
    card['treatment']['start_period_kind'] = st['start_period_kind']
    card['policy_family'] = src['policy_family']
    card['unit'] = src['unit']
    card['track'] = src['track']
    card['transition_year_drop'] = True
    # carry the source's adoption sources verbatim (statute and compilation links), keep the new study and outcome ones
    keep = [fix_source(s) for s in src['sources'] if s['source_id'].startswith(('adoption_', 'statute_'))]
    new = [s for s in card['sources'] if s['source_id'].startswith(('study_', 'outcome'))]
    if keep:
        card['sources'] = keep + new
        card['treatment']['complete_list_source_id'] = st['complete_list_source_id']
    card['automation']['history_reuse'] = dict(source_design_id=spec['source_design_id'], rule=RULING)
    card['automation']['intake']['record'] = 'data/l2_pipeline/intake/bulk_20261020/second_outcomes/specs/%s.json' % spec['slug']
    return card, diag, src


def cmd_build(args):
    from scripts.l2_pipeline import history_reuse as H
    out = Path(args.out).resolve()
    report = []
    for p in sorted(Path(args.specs).glob('*.json')):
        spec = json.loads(p.read_text())
        try:
            card, diag, src = build_one(spec)
        except (KeyError, SystemExit) as exc:
            row = dict(design_id=spec['design_id'], source=spec['source_design_id'], build_error=type(exc).__name__ + ': ' + str(exc)[:200])
            report.append(row)
            print(json.dumps(row))
            continue
        bad = H.mismatches(card, src)
        lint = B.lint_mechanism(card['mechanism'])
        B.dump(card, out / (card['design_id'] + '.json'))
        row = dict(design_id=card['design_id'], source=spec['source_design_id'], reuse_mismatch=bad, lint=lint,
                   screen_passed=diag['screen_passed'], treated=len(diag['kept']), never=len(diag['never']),
                   left=len(diag['left']), late=len(diag['late']), mechanical=diag['mechanical_series'],
                   primary=card['outcome']['series_id'], window=diag['window'])
        report.append(row)
        print(json.dumps(row))
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=1))
    return 0


def cmd_probe(args):
    from scripts.l2_pipeline.population_resolution import choose
    from scripts.l2_pipeline.connectors import population_v63
    src, meta = population_v63.current_catalog()
    try:
        res = choose(dict(policy_family=args.family),
                     dict(status='VERIFIED', target_outcome=args.target_outcome, target_construct=args.construct), src, meta)
        print(json.dumps(dict(selected=(res.get('selected') or {}).get('series_id'), construct=res.get('construct'),
                              decision=res.get('decision'), candidates=[c['series_id'] for c in res.get('candidates', [])])))
    except ValueError as exc:
        print(json.dumps(dict(error=str(exc)[:200])))
    return 0


def cmd_source(args):
    src = source_card(args.design_id)
    t = src['treatment']
    sa = t['screening_adoption']
    rows = active_rows()[args.design_id]
    print(json.dumps(dict(design_id=args.design_id, card=rows['card'], policy_family=src['policy_family'],
                          predicate=t['predicate'], coverage_end=t['coverage_end'],
                          treated_n=len(sa['adopters']), treated_first_last=[min(sa['adopters'].values()), max(sa['adopters'].values())] if sa['adopters'] else None,
                          never_n=len(sa['never_through_coverage_end']),
                          current_outcome=src['outcome']['series_id'], current_window=src['outcome'].get('years'),
                          current_target=src['first_order'].get('target_outcome')), indent=1))
    return 0


def cmd_check(args):
    return B.cmd_check(args)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build'); b.add_argument('--specs', required=True); b.add_argument('--out', required=True); b.add_argument('--report')
    c = sub.add_parser('check'); c.add_argument('--cards', required=True); c.add_argument('--report')
    p = sub.add_parser('probe'); p.add_argument('--family', required=True); p.add_argument('--target-outcome', required=True); p.add_argument('--construct')
    s = sub.add_parser('source'); s.add_argument('--design-id', required=True)
    args = ap.parse_args(argv)
    return {'build': cmd_build, 'check': cmd_check, 'probe': cmd_probe, 'source': cmd_source}[args.cmd](args)


if __name__ == '__main__':
    raise SystemExit(main())
