#!/usr/bin/env python3
"""L1-B card assembler for batch bulk_20261015 (queue v3 -> L2 design cards).

Reads researcher records (JSON, one per policy) and writes L2 design cards in the
documented intake format (scripts/l2_pipeline/design_card.schema.json, same layout
as the cards of intake/bulk_20261012). It uses only repository functions that read
catalogs and value-free support sidecars: no outcome value is decoded, no network
call is made, no L2 code or registry file is changed.

Usage (repo root, venv python with the user-site path used by the watchdog):
  PYTHONPATH=$HOME/Library/Python/3.9/lib/python/site-packages \
  .venv_did/bin/python -B scripts/l1/b1015_cards.py build  --records DIR --out DIR
  .venv_did/bin/python -B scripts/l1/b1015_cards.py check  --cards DIR [--report FILE]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CATALOG_REL = 'scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V17.json'
BATCH = 'bulk_20261020'
QUEUE_VERSION = 'queue_v4_20261020'
SCREEN_VERSION = 'bulk_20261020_queue_v4_v1'

STATES = ('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND '
          'OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY').split()
STATES50 = sorted(STATES)
STATE_NAMES = ('Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|'
               'Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|'
               'Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|'
               'North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|'
               'Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming').split('|')

ANALYSIS = {
    'clock': 'calendar_year',
    'diagnostics': ['pretrend_joint', 'pretrend_economic', 'placebo'],
    'estimand': 'cohort-size weighted supported-post ATT',
    'inference': 'Webb first; complete-control-path CT fallback; point+RI if uncertified',
    'min_cohorts': 2, 'min_controls': 5, 'min_post_periods': 2, 'min_pre_periods': 3, 'min_treated': 6,
    'spec_status': 'PROSPECTIVE_DRAFT_REQUIRES_S0_S5_AND_FREEZE',
    'stop_rules': ['support', 'registered_calibration', 'fail_closed_integrity'],
    'weights': 'equal states within cell; cohort-size supported-post aggregation',
    'controls': 'never_treated',
}
SENSITIVITIES = [
    {'name': 'start_plus_one', 'changes': {'treatment': {'start_shift': 1}}},
    {'name': 'start_minus_one', 'changes': {'treatment': {'start_shift': -1}}},
]
HISTORICAL_CONVENTION = (
    'approximate first-effective-year list from literature/official compilation; SCREENING ONLY, not a certified '
    'history; S1 official-history A/B audit required. Operative year = calendar year in which the predicate first '
    'holds; the V8b transition-year drop discards that year.')
SCREEN_BASIS = ('record adoption list x series value-free support flags (publisher/V8 sidecar) with the V8b '
                'transition-year drop; approximate screen, not certified; no outcome values read')
SCREEN_BASIS_NO_SIDECAR = ('record adoption list x declared calendar (no certified value-free support sidecar for this '
                           'series: optimistic complete rectangle) with the V8b transition-year drop; approximate '
                           'screen, not certified; no outcome values read')
RECORDED_AT = 'S0 intake (card authoring), before any outcome value is read'


def sha_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def catalog():
    return json.loads((ROOT / CATALOG_REL).read_text())


def lint_mechanism(text, forbidden_years=True):
    """C58: the card predicate carries no state names and no adoption years."""
    problems = []
    for name in STATE_NAMES:
        if re.search(r'\b' + re.escape(name) + r'\b', text):
            problems.append('state name: ' + name)
    if forbidden_years and re.search(r'\b(19[0-9]{2}|20[0-4][0-9])\b', text):
        problems.append('year-like token')
    return problems


def series_spec(series_id):
    spec = catalog()['series'][series_id]
    return spec


def outcome_block(record, window, spec_sid):
    """Primary outcome declaration bound to the pinned V15 catalog (same layout as the intake cards)."""
    cat = catalog()
    spec = cat['series'][spec_sid]
    years_all = spec.get('years') or []
    years = [y for y in range(window[0], window[1] + 1) if y in set(years_all)] if years_all else list(range(window[0], window[1] + 1))
    if spec.get('support_artifact'):
        # Declared periods list only years in which the value-free sidecar shows at least one usable unit
        # (explicit period inventory with holes, as the coverage rule allows); never a year nobody observed.
        from scripts.l2_pipeline.prescreen import flags
        sidecar = flags(dict(spec, series_id=spec_sid))
        observed = {y for (_u, y) in sidecar}
        years = [y for y in years if y in observed]
    freq = spec['frequency'][0] if isinstance(spec.get('frequency'), list) else spec.get('frequency', 'annual')
    out = {
        'catalog_path': CATALOG_REL,
        'catalog_sha256': sha_file(ROOT / CATALOG_REL),
        'connector': spec['registered_connector'],
        'series_id': spec_sid,
        'measure_id': spec_sid,
        'frequency': freq,
        'outcome_scale': 'rate',
        'outcome_unit': record['outcome'].get('outcome_unit') or spec['units'],
        'source_outcome_unit': spec['units'],
        'population_filters': copy.deepcopy(spec.get('population_filters', {})),
        'source_population_filters': copy.deepcopy(spec.get('population_filters', {})),
        'license': spec.get('license', ''),
        'source_url': spec.get('source_url', ''),
        'source_validation_required': True,
        'units': list(STATES50),
        'years': [years[0], years[-1]],
        'period_years': years,
    }
    if spec.get('paths'):
        out['paths'] = copy.deepcopy(spec['paths'])
        out['source_sha256'] = copy.deepcopy(spec['source_sha256'])
    elif spec.get('exports'):
        paths = [str((ROOT / e['raw_path']).resolve()) for e in spec['exports']]
        out['paths'] = paths
        out['source_sha256'] = {str((ROOT / e['raw_path']).resolve()): e['sha256'] for e in spec['exports']}
    elif spec.get('artifact'):
        out['paths'] = [spec['artifact']['path']]
        out['source_sha256'] = {spec['artifact']['path']: spec['artifact']['sha256']}
    if spec.get('support_artifact'):
        out['support_path'] = spec['support_artifact']['path']
        out['support_sha256'] = spec['support_artifact']['sha256']
    if spec.get('analysis_only'):
        out.update(analysis_only=True, republication_prohibited=True)
    return out


def secondary_block(series_id, basis, role_text):
    from scripts.l2_pipeline.population_resolution import secondary_declaration
    from scripts.l2_pipeline.connectors import population_v63
    source, meta = population_v63.current_catalog()
    current = population_v63.catalog_module(source['schema_version'])
    prefix = 'outcome_v' + str(source['schema_version']) + '_'
    entry = dict(outcome=series_id, role='secondary', predeclared_basis=basis)
    entry.update(secondary_declaration(series_id, source, current, prefix, ROOT))
    return entry


def population_resolution_block(record, expected_dilution):
    """V6.3 mechanical selection recorded before the independent purpose job (same layout as bulk_20261012)."""
    from scripts.l2_pipeline.population_resolution import choose, bind, AUTHORITY
    from scripts.l2_pipeline.connectors import population_v63
    source, meta = population_v63.current_catalog()
    fo = record['first_order']
    purpose = dict(status='VERIFIED', target_outcome=fo['target_outcome'], target_construct=fo.get('target_construct'))
    res = choose(dict(policy_family=record['policy']['family']), purpose, source, meta)
    block = copy.deepcopy(res)
    block.update(
        authority=bind(AUTHORITY),
        catalog=bind(ROOT / CATALOG_REL),
        population_catalog=bind(population_v63.CURRENT_PATH),
        expected_dilution=expected_dilution,
        recorded_at=RECORDED_AT,
    )
    # key order close to the existing intake cards
    order = ['rule', 'authority', 'catalog', 'population_catalog', 'construct', 'eligible_scope', 'candidates',
             'selected', 'decision', 'reason', 'expected_dilution', 'outcome_values_read', 'outcome_association_calls',
             'recorded_at', 'equivalence_evidence', 'equivalence_citation', 'secondary_series']
    ordered = {k: block[k] for k in order if k in block}
    ordered.update({k: v for k, v in block.items() if k not in ordered})
    return ordered, res


def classify_states(adopt, window, thresholds, tdrop=True, observed=None):
    """Left-censoring and late-adopter screen for a treated state against the declared calendar.

    A treated state needs >= min_pre clean pre-years before its operative year and >= min_post retained post-years
    after the transition-year drop. States failing this are excluded from both arms (never filled as controls).
    ``observed`` (optional) is the inventory of calendar years in which the series has any usable unit; the
    pre/post counts then use observed years only, as the S3 support check does."""
    first, last = window
    min_pre, min_post = thresholds['min_pre_periods'], thresholds['min_post_periods']
    years = [y for y in range(first, last + 1) if observed is None or y in observed]
    keep, left, late = {}, {}, {}
    for state, year in adopt.items():
        pre = [y for y in years if y < year]
        post = [y for y in years if y >= year + (1 if tdrop else 0)]
        if len(pre) < min_pre:
            left[state] = year
        elif len(post) < min_post:
            late[state] = year
        else:
            keep[state] = year
    return keep, left, late


def screen_support(adopters, never, sid, window, analysis, excluded_other):
    """Value-free expected support, computed with the repository's own support_check."""
    from scripts.l2_pipeline.prescreen import flags
    from scripts.l2_pipeline.support import support_check
    spec = dict(catalog()['series'][sid], series_id=sid)
    has_sidecar = bool(spec.get('support_artifact'))
    years = list(range(window[0], window[1] + 1))
    omitted_years = []
    if has_sidecar:
        keys = flags(spec)
        in_spec = set(spec.get('years') or years)
        omitted_years = [y for y in years if y in in_spec and not any((u, y) in keys for u in STATES50)]
        years = [y for y in years if any((u, y) in keys for u in STATES50)]
    else:
        spec_years = set(spec.get('years') or years)
        years = [y for y in years if y in spec_years]
        keys = {(u, y) for u in STATES50 for y in years}
    tr = [dict(unit=u, status='treated', start_period=y) for u, y in sorted(adopters.items())]
    tr += [dict(unit=u, status='never_treated', start_period=None) for u in sorted(never)]
    summary = support_check(keys, tr, analysis, period_years=years, transition_year_drop=True)
    per_year = [sum(1 for u in STATES50 if (u, y) in keys) for y in years]
    usable_all = sorted(u for u in never if all((u, y) in keys for y in years))
    ge90 = sorted(u for u in never if sum((u, y) in keys for y in years) >= 0.9 * len(years))
    cohorts = sorted({y for y in adopters.values()})
    cell_controls = [len(c['controls']) for c in summary.get('cells', [])]
    out = {
        'basis': SCREEN_BASIS if has_sidecar else SCREEN_BASIS_NO_SIDECAR,
        'comparison_group': 'never_treated',
        'window': [window[0], window[1]],
        'omitted_years': omitted_years,
        'period_years_n': len(years),
        'v8_certified_usable_states_per_year': {
            'min': min(per_year), 'median': float(statistics.median(per_year)), 'max': max(per_year),
            'n_years': len(years), 'of': 50},
        'treated_n': len(adopters), 'treated_states': sorted(adopters),
        'cohorts_n': len(cohorts), 'cohort_years': cohorts,
        'never_n': len(never), 'never_with_all_years_usable_n': len(usable_all),
        'never_usable_ge90pct_n': len(ge90),
        'min_cell_controls': min(cell_controls) if cell_controls else 0,
        'left_censored': [], 'insufficient_post': [], 'v11_dropped_post_cells': 0,
        'passed': bool(summary.get('passed')),
        'thresholds': {k: analysis[k] for k in ('min_treated', 'min_cohorts', 'min_controls', 'min_pre_periods', 'min_post_periods')},
        'excluded_unknown_or_other_n': excluded_other,
    }
    if not summary.get('passed'):
        out['support_issues'] = summary.get('issues')
    return out, summary


def build_card(record):
    """Return (card, diagnostics)."""
    pol, fo, ad, oc = record['policy'], record['first_order'], record['adoption'], record['outcome']
    slug = record['record_id']
    design_id = record['design_id']
    sid = oc['series_id']
    thresholds = {k: ANALYSIS[k] for k in ('min_pre_periods', 'min_post_periods')}

    window = (ad['window'][0], ad['window'][1])
    # keep the outcome calendar inside the declared series years
    spec = series_spec(sid)
    sy = spec.get('years') or list(range(window[0], window[1] + 1))
    window = (max(window[0], min(sy)), min(window[1], max(sy)))

    raw_adopt = {s: int(y) for s, y in ad['states'].items() if isinstance(y, int) and s in STATES50 and s not in ad.get('force_exclude', {})}
    observed = None
    if spec.get('support_artifact'):
        from scripts.l2_pipeline.prescreen import flags as _flags
        _keys = _flags(dict(spec, series_id=sid))
        observed = {y for (_u, y) in _keys}
    keep, left, late = classify_states(raw_adopt, window, thresholds, observed=observed)
    never = sorted(s for s in ad.get('never', []) if s in STATES50)
    other = {}
    for s, spec_row in ad.get('other_status', {}).items():
        other[s] = copy.deepcopy(spec_row)
    for s, y in left.items():
        other.setdefault(s, {'status': 'left_censored', 'year': y,
                             'note': 'Operative year leaves fewer than three clean pre-years inside the declared outcome calendar; excluded from both arms.'})
    for s, y in late.items():
        other.setdefault(s, {'status': 'late_adopter', 'year': y,
                             'note': 'Fewer than two retained post-years after the transition-year drop inside the declared calendar; excluded from both arms.'})
    other.setdefault('DC', {'status': 'not_coded', 'year': None, 'note': 'DC is not a state; not coded in this screening list.'})
    for s in STATES50:
        if s not in keep and s not in never and s not in other:
            other[s] = {'status': 'unknown', 'year': None, 'note': 'No dated, concordant source in the record; excluded from both arms.'}

    analysis = copy.deepcopy(ANALYSIS)
    if oc.get('support_gap_rule'):
        analysis['support_gap_rule'] = oc['support_gap_rule']
    excluded_other = len([s for s in other if s != 'DC'])
    screen, summary = screen_support(keep, never, sid, window, analysis, excluded_other)
    analysis['screen_expected_support'] = screen

    outcome = outcome_block(record, window, sid)
    pr_block, resolution = population_resolution_block(record, fo['expected_dilution'])

    # adoption screen rows
    rows = []
    detail = ad.get('detail', {})
    for s in STATES50:
        d = detail.get(s, {})
        if s in keep:
            rows.append(dict(state=s, effective_year=keep[s], effective_date=d.get('date', str(keep[s])), status='adopted',
                             confidence=d.get('confidence', 'secondary'), source=d.get('source', '')))
        elif s in never:
            rows.append(dict(state=s, effective_year=None, effective_date=None, status='never',
                             confidence=d.get('confidence', 'secondary'), source=d.get('source', ad.get('never_source', ''))))
        else:
            o = other.get(s, {})
            rows.append(dict(state=s, effective_year=o.get('year'), effective_date=d.get('date') if d else None,
                             status=o.get('status', 'unknown'), confidence=d.get('confidence', 'secondary'),
                             source=d.get('source', o.get('note', ''))))

    sources = []
    for i, src in enumerate(ad['sources']):
        row = dict(source_id='adoption_%d' % i, url=src['url'], locator=src['locator'], license=src['license'])
        if 'complete_list' in src:
            row['complete_list'] = src['complete_list']
        if src.get('path'):
            # local repository file: bind its bytes
            row['path'] = src['path']
            row['sha256'] = sha_file(ROOT / src['path'])
        sources.append(row)
    for i, lit in enumerate(fo.get('literature', [])):
        sources.append(dict(source_id='study_%d' % i, url='https://doi.org/' + lit['doi'], locator=lit['locator_short'],
                            license=lit.get('license', 'unverified (publisher copyright; cite only)')))
    sources.append(dict(source_id='outcome', url=spec.get('source_url') or 'unresolved',
                        locator='%s (registered connector %s)' % (sid, spec['registered_connector']),
                        license=spec.get('license', 'unverified')))

    lit_refs = []
    for lit in fo.get('literature', []):
        lit_refs.append({
            'source_ref': 'https://doi.org/' + lit['doi'], 'locator': lit['locator'], 'title': lit['title'],
            'authors': lit['authors'], 'year': lit['year'], 'doi': lit['doi'], 'doi_verified': True,
            'supports': lit.get('supports', ['target_outcome']), 'first_order_fit': lit['fit'], 'journal': lit['journal']})

    first_order = {
        'purpose_route': 'established_literature' if not fo.get('statute_refs') else fo.get('purpose_route', 'statute'),
        'purpose_rule': 'V6.2',
        'purpose_basis': 'established_literature' if not fo.get('statute_refs') else 'statute_purpose',
        'evidence_status': 'pending_S0_verification',
        'target_outcome': fo['target_outcome'],
        'primary_outcome': sid,
        'relation': fo['relation'],
        'target_population': fo['target_population'],
        'proxy_justification': fo['proxy_justification'],
        'justification': ('V6.3: same-construct series with largest declared eligible-population coverage, then narrower '
                          'population; selected before any outcome value is read; see population_resolution'),
        'statute_refs': copy.deepcopy(fo.get('statute_refs', [])),
        'literature_refs': lit_refs,
        'availability': 'available',
        'secondary_outcomes': [secondary_block(s['series_id'], s['basis'], '') for s in fo.get('secondary', [])],
        'population_resolution': pr_block,
        'target_description': fo.get('target_description', fo['target_outcome']),
        'purpose_direction': fo.get('purpose_direction'),
        'purpose_direction_basis': fo.get('purpose_direction_basis', 'Not proposed here; F1 is coded blind from statute text at S0 (never from an estimate, forecast or literature sign).'),
        'purpose_direction_status': 'researcher_proposal_pending_two_blind_coders_at_S0; statute text only; never from an estimate, forecast or literature sign' if fo.get('purpose_direction') is not None else 'not_proposed_pending_two_blind_coders_at_S0',
    }

    universe = list(STATES50)
    universe.insert(STATES50.index('DE') + 1, 'DC')
    from scripts.l2_pipeline import rules as _rules  # REDESIGN: a new card pins the rules version current when it is built
    _stamp = _rules.stamp()
    structural = {}
    if _stamp is not None and _rules.number(_stamp['version']) >= 3:
        # R3 (W3): the card declares its structural exclusions at build, each with its reason (no outcome value is read)
        from scripts.l2_pipeline import intake_replay
        series_units = set(STATES50)
        if spec.get('support_artifact'):
            from scripts.l2_pipeline.prescreen import sidecar_units as _sidecar_units
            series_units = set(_sidecar_units(dict(spec, series_id=sid)))
        structural = intake_replay.structural_list(universe, series_units, raw_adopt, window, thresholds, observed=observed,
                                                   transition_year_drop=True)
    treatment = {
        'universe': universe,
        'seed': int(record['seed']),
        'complete_list_source_id': 'adoption_0',
        'predicate': pol['predicate_clean'],
        'coverage_end': ad['coverage_end'],
        'historical_convention': HISTORICAL_CONVENTION,
        'start_period_kind': 'operative_year',
        'transition_year_drop': True,
        'screening_adoption': {
            'adopters': dict(sorted(keep.items())),
            'never_through_coverage_end': never,
            'other_status': dict(sorted(other.items())),
            'adopters_after_window_treated_as_not_yet_or_never_in_window': dict(sorted(ad.get('after_window', {}).items())),
            'adoption_screen': rows,
            'censor_after_period': {},
            'researcher_predicate_with_provenance': pol['predicate_with_provenance'],
            'clock_note': pol['clock_note'],
            'reversals_or_ambiguities': ad.get('reversals_or_ambiguities', []),
            'screening_only': True,
            'record_verdict': record.get('verdict', 'screen_pass_pending_prescreen'),
            'record_verdict_note': (record.get('verdict_note', '').replace('{treated}', str(len(keep))).replace('{cohorts}', str(len(set(keep.values())))).replace('{never}', str(len(never)))),
            'record_complete_list': bool(ad.get('complete_list', True)),
        },
    }

    population = {'description': record['population']['description'], 'filters': copy.deepcopy(spec.get('population_filters', {}))}
    if record['population'].get('filters') is not None:
        population['filters'] = copy.deepcopy(record['population']['filters'])
    outcome['population_filters'] = copy.deepcopy(population['filters'])
    outcome['source_population_filters'] = copy.deepcopy(spec.get('population_filters', {})) or copy.deepcopy(population['filters'])

    card = {
        'schema_version': '1.0', 'version': 'v1', 'design_id': design_id,
        'policy_family': pol['family'], 'mechanism': pol['predicate_clean'], 'track': 'DiD', 'unit': 'state',
        'outcome_family': resolution['construct'], 'outcome_scale': 'rate', 'transition_year_drop': True,
        'sources': sources,
        'lineage': {'queue_version': QUEUE_VERSION, 'screen_version': SCREEN_VERSION,
                    'source_text_ids': [slug, '%s/%s/records/%s.json' % (BATCH, record.get('group', 'queue_v4'), slug)],
                    'legal_instrument_ids': record.get('legal_instrument_ids', ['State statutes and session laws: see treatment.screening_adoption.adoption_screen[].source'])},
        'population': population,
        'outcome': outcome,
        'treatment': treatment,
        'analysis': analysis,
        'sensitivities': copy.deepcopy(SENSITIVITIES),
        'automation': {
            'intake': {
                'batch': BATCH, 'group': record.get('group', 'queue_v4'), 'status': 'PROSPECTIVE_UNREGISTERED',
                'execution_authorized': False, 'dispatch': False,
                'record': 'data/l2_pipeline/intake/%s/%s/records/%s.json' % (BATCH, record.get('group', 'queue_v4'), slug),
                'outcome_values_read': 0, 'outcome_association_calls': 0,
                'series_selection': 'V6.3 mechanical selection recorded in first_order.population_resolution',
                'context_citations_not_counted': record.get('context_citations_not_counted', []),
                'needs': ['V6.2 S0 purpose verification', 'V8b/V9 official history audit', 'calibration'],
                'researcher_rating': record.get('rating', 'B-'),
                'batch_disposition': record.get('disposition', 'ready'),
                'first_order_class': record.get('first_order_class', 'first_order'),
            },
            'l1_cross_reference': record.get('l1_cross_reference', []),
        },
        'notes': record.get('notes', []),
        'first_order': first_order,
    }
    diag = dict(window=window, kept=sorted(keep), left=sorted(left), late=sorted(late), never=never,
                mechanical_series=(resolution.get('selected') or {}).get('series_id'),
                screen_passed=screen['passed'], summary_reason=summary.get('reason'), issues=summary.get('issues'))
    if _stamp is not None:
        card['automation']['rules'] = _stamp
    if structural:
        card['treatment']['excluded_units'] = sorted(structural)
        card['automation']['intake_replay'] = intake_replay.receipt(structural)
    return card, diag


def dump(card, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(card, indent=2, ensure_ascii=False)
    path.write_text(text + '\n', encoding='utf-8')


def cmd_build(args):
    records = sorted(Path(args.records).glob('*.json'))
    out = Path(args.out).resolve()
    report = []
    for p in records:
        record = json.loads(p.read_text())
        card, diag = build_card(record)
        problems = lint_mechanism(card['mechanism'])
        dump(card, out / (card['design_id'] + '.json'))
        if getattr(args, 'split', None):
            sub = {'ready': 'cards', 'reserve': 'reserve', 'held': 'held'}[record.get('disposition', 'ready')]
            dump(card, Path(args.split).resolve() / sub / (card['design_id'] + '.json'))
        report.append(dict(design_id=card['design_id'], lint=problems, **diag))
        print(card['design_id'], 'mech_lint=%s' % problems, 'screen_passed=%s' % diag['screen_passed'],
              'treated=%d never=%d left=%d late=%d' % (len(diag['kept']), len(diag['never']), len(diag['left']), len(diag['late'])),
              'mechanical=%s' % diag['mechanical_series'], 'primary=%s' % card['outcome']['series_id'])
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=1))
    return 0


_AUDITOR_JOBS = None


def prior_audit_units(card):
    """Units already sampled by auditor packets of the same policy (read-only replica of the ingest rule).

    autopilot.ingest treats a prior auditor packet as the same policy when its mechanism/predicate equals the
    card mechanism or treatment predicate (or the policy_id matches). The fresh V3 audit then excludes those
    units; D8 (C85) audits a nonempty remainder as a census and parks an empty one."""
    global _AUDITOR_JOBS
    if _AUDITOR_JOBS is None:
        _AUDITOR_JOBS = []
        for path in (ROOT / 'data/l2_pipeline').rglob('*.job.json'):
            try:
                meta = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            c = meta.get('contract', meta)
            if meta.get('role') == 'auditor' and isinstance(c.get('sample'), list):
                _AUDITOR_JOBS.append((meta.get('design_id'), c.get('mechanism'), c.get('predicate'), c.get('policy_id'), list(c['sample'])))
    mech = card.get('mechanism')
    pred = card.get('treatment', {}).get('predicate')
    policy_id = card.get('treatment', {}).get('policy_id')
    units, designs = set(), set()
    for did, m, pr, pid, sample in _AUDITOR_JOBS:
        if did == card.get('design_id'):
            continue
        if m == mech or pr == mech or m == pred or pr == pred or bool(policy_id and pid == policy_id):
            units |= set(sample)
            designs.add(did)
    return sorted(units), sorted(d for d in designs if d)


def cmd_check(args):
    """Validation chain used by earlier batches: schema, c42.completeness, first_order_gate, C62 pre-screen (dry run)."""
    from scripts.l2_pipeline.cards import validate_card
    from scripts.l2_pipeline.c42 import completeness
    from scripts.l2_pipeline.first_order import first_order_gate
    from scripts.l2_pipeline import prescreen
    existing = set()
    act = json.loads((ROOT / 'data/l2_pipeline/registry/ACTIVE_DESIGNS.json').read_text())['designs']
    existing |= {r['design_id'] for r in act}
    ter = json.loads((ROOT / 'data/l2_pipeline/registry/TERMINAL_RUNS.json').read_text())
    existing |= {r['design_id'] for r in ter['runs'] + ter['pre_run_stops']}
    results = []
    for p in sorted(Path(args.cards).resolve().glob('*.json')):
        card = json.loads(p.read_text())
        row = dict(design_id=card['design_id'], path=str(p))
        errs = validate_card(card)
        row['validate'] = 'valid' if not errs else errs
        miss = completeness(card)
        row['complete'] = not miss
        row['missing'] = miss
        gate = first_order_gate(card)
        row['first_order_gate'] = gate['passed']
        row['first_order_issues'] = gate['issues']
        row['duplicate_of_registry'] = card['design_id'] in existing
        row['mechanism_lint'] = lint_mechanism(card['mechanism'])
        try:
            prior_units, prior_designs = prior_audit_units(card)
            row['prior_audit_units'] = prior_units
            row['prior_audit_designs'] = prior_designs
            res = prescreen.evaluate(card, p.parent, excluded_units=tuple(prior_units), repo=ROOT)
            row['prescreen'] = 'PROCEED' if res['status'] == 'PROCEED' else 'PARK'
            row['prescreen_status'] = res['status']
            row['prescreen_reasons'] = res.get('reasons', [])
            row['prescreen_inconclusive'] = res.get('inconclusive')
            trials = res.get('variants', [])
            row['variants_trials_passed'] = '%d/%d' % (sum(1 for t in trials if t.get('passed') is True), len(trials))
            row['construct_inconclusive'] = res.get('construct_inconclusive')
            row['outcome_values_read'] = res.get('outcome_values_read')
            row['actual_data_estimation_calls'] = res.get('actual_data_estimation_calls')
            row['trial_summary'] = [dict(variant=t.get('variant'), shift=t.get('year_shift'), passed=t.get('passed'),
                                         reasons=t.get('reasons'), inconclusive=t.get('inconclusive'),
                                         fresh_pool_n=t.get('fresh_pool_n'), s3=t.get('s3_pass'), ri=t.get('ri_pass')) for t in trials[:3]]
            if res['status'] != 'PROCEED':
                row['pass_type'] = 'PARK'
            elif any(t.get('passed') is True for t in trials):
                row['pass_type'] = 'PROCEED_certified_trial_passed'
            else:
                row['pass_type'] = 'PROCEED_inconclusive_only'
            # R3 (W3): the ingest checks, dry run: reuse is the default, the structural list is replayed on the certified roster
            from scripts.l2_pipeline import rules as _rules
            if _rules.at_least(_rules.of_payload(card, ROOT), 3):
                from scripts.l2_pipeline import history_reuse, intake_replay
                from scripts.l2_pipeline.autopilot import Autopilot
                _pilot = Autopilot()
                row['history_reuse_required'] = history_reuse.required_source(_pilot, card)
                _replay = intake_replay.check_card(_pilot, p, card)
                row['intake_replay'] = _replay['status']
                row['intake_replay_reason'] = _replay.get('reason') or _replay.get('why')
            # V6.3 consistency: the mechanical series must equal the card primary, else S0 re-derives and requeues it
            from scripts.l2_pipeline.population_resolution import choose
            from scripts.l2_pipeline.connectors import population_v63
            src, meta = population_v63.current_catalog()
            fo = card['first_order']
            try:
                mech = choose(dict(policy_family=card['policy_family']),
                              dict(status='VERIFIED', target_outcome=fo['target_outcome'],
                                   target_construct=fo.get('population_resolution', {}).get('construct')), src, meta)
                row['mechanical_series'] = (mech.get('selected') or {}).get('series_id')
                row['mechanical_match'] = row['mechanical_series'] == card['outcome']['series_id']
            except ValueError as exc:
                row['mechanical_series'] = None
                row['mechanical_match'] = False
                row['mechanical_error'] = str(exc)[:160]
        except Exception as exc:  # report, do not hide
            row['prescreen'] = 'ERROR'
            row['prescreen_error'] = type(exc).__name__ + ': ' + str(exc)
        results.append(row)
        print(row['design_id'], row['validate'] if row['validate'] != 'valid' else 'valid', 'complete=%s' % row['complete'],
              'gate=%s' % row['first_order_gate'], row['prescreen'], row.get('pass_type', ''), row.get('prescreen_reasons', ''),
              'mech_match=%s' % row.get('mechanical_match'), 'dup=%s' % row['duplicate_of_registry'])
    if args.report:
        Path(args.report).write_text(json.dumps(results, indent=1, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('--records', required=True)
    b.add_argument('--out', required=True)
    b.add_argument('--report')
    b.add_argument('--split', help='batch base dir: also write cards/ (ready), reserve/ and held/ copies by record disposition')
    c = sub.add_parser('check')
    c.add_argument('--cards', required=True)
    c.add_argument('--report')
    args = ap.parse_args(argv)
    return {'build': cmd_build, 'check': cmd_check}[args.cmd](args)


if __name__ == '__main__':
    raise SystemExit(main())
