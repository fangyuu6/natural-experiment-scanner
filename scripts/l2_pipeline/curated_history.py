"""T218 (HUB_RULING_20261021_CURATED_HISTORY): adoption history from a curated legal database, under an audit that can fail.

Route: frozen mapping (card predicate -> database rows)  ->  build_history (one coder-format row per state)  ->
audit_plan (seeded, stratified, backup pool)  ->  blind A/B history chunk packets on the sample (existing machinery)  ->
classify/evaluate (pass | extend | fail | inconclusive)  ->  certify (CERTIFIED_HISTORY-equivalent files).

Outcome-blind: reads only the database workbook, the card predicate/universe/window and workers' history records.
Nothing here opens an outcome, an estimate or a forecast. Pure functions except ``certify`` and the ledger helpers,
which write once under a design root / ledger root given by the caller.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from copy import deepcopy
from pathlib import Path

RULING = 'docs/plans/l2-pipeline/HUB_RULING_20261021_CURATED_HISTORY.md'
SCHEMA_MAPPING = 'l2_curated_mapping_v1'
SOURCE_KIND = 'curated_database'
SOURCE_TYPE = 'curated_database_audited'
HISTORY_SOURCE = 'db_audited'
CERT_RULE = 'CURATED_DB_HISTORY_T218+fresh_audit'
MAX_AMBIGUOUS_SHARE = 0.20
PLACEHOLDER_YEAR = 1900
EXTENSION = 5
MAX_UNRESOLVED = 5
SAMPLE_FLOOR = 10
SAMPLE_SHARE = 0.25
ALL_STATES_AT_OR_BELOW = 12

# Databases that qualify as date sources (ruling R-CH 1). Anything else is refused by name.
QUALIFYING = {
    ('RAND State Firearm Law Database', '6.0'): dict(
        coverage_end_year=2025, citation='Hoch et al., RAND TL-A243-2-v4, State Firearm Law Database v6.0 (January 2026)',
        license='ODbL (workbook AboutThisFile); RAND notice: noncommercial use, no row-level republication',
        xlsx_sha256='0293ce200822f10e1e537ba045192a64576b6f907217ec8049c20c38eb610141'),
}
PILOT_ONLY = ('APIS',)   # qualifies per topic after a 3-state pilot and a 50% first audit (needs its own ruling entry)
NOT_QUALIFYING = ('SPID', 'CSPP', 'NCSL', 'Everytown', 'Giffords', 'Wikipedia')


class Ineligible(ValueError):
    """The card/database pair is not eligible for the curated route: use fresh coding."""


def canon(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def digest(value) -> str:
    return hashlib.sha256(canon(value)).hexdigest()


def file_sha(path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------- database rows
COLS = {'law_id': 'Law ID', 'state': 'State Postal Abbreviation', 'law_class': 'Law Class', 'subtype': 'Law Class Subtype',
        'guns': 'Handguns or Long Guns', 'effect': 'Effect', 'change': 'Type of Change', 'note': 'Effective Date Note',
        'year': 'Effective Date Year', 'month': 'Effective Date Month', 'day': 'Effective Date Day',
        'citation': 'Statutory Citation', 'sup_year': 'Supersession Date Year', 'sup_month': 'Supersession Date Month',
        'sup_day': 'Supersession Date Day', 'discrepancy': 'Discrepancies'}


def load_rows(xlsx_path, sheet='Database'):
    """Database rows as dicts keyed by the short names in COLS (statute content text is not loaded: it is never published)."""
    from .curated_xlsx import read_sheet
    table = read_sheet(xlsx_path, sheet)
    head = table[0]
    idx = {k: head.index(v) for k, v in COLS.items()}
    out = []
    for raw in table[1:]:
        raw = raw + [None] * (len(head) - len(raw))
        row = {k: raw[i] for k, i in idx.items()}
        for k in ('law_class', 'subtype', 'guns', 'effect', 'change'):
            row[k] = None if row[k] is None else str(row[k]).strip().lower()
        out.append(row)
    return out


# ---------------------------------------------------------------- mapping
def validate_mapping(spec):
    if spec.get('schema') != SCHEMA_MAPPING:
        raise Ineligible('mapping schema')
    db = spec.get('database') or {}
    key = (db.get('name'), db.get('version'))
    if any(bad.lower() in str(db.get('name', '')).lower() for bad in NOT_QUALIFYING):
        raise Ineligible('database %s does not qualify as a date source' % db.get('name'))
    if key not in QUALIFYING:
        raise Ineligible('database %s %s is not on the qualifying list (APIS needs its own pilot ruling)' % key)
    info = QUALIFYING[key]
    if db.get('xlsx_sha256') != info['xlsx_sha256']:
        raise Ineligible('database file sha256 differs from the pinned version')
    for field in ('mapping_id', 'policy_family', 'mechanism', 'universe', 'window_start', 'coverage_end_year', 'classes',
                  'transition_year_drop', 'candidate_log', 'mappers'):
        if field not in spec:
            raise Ineligible('mapping lacks ' + field)
    if spec['coverage_end_year'] > info['coverage_end_year']:
        raise Ineligible('coverage_end beyond the database coverage')
    if not spec['classes'] or any('law_class' not in c or 'include' not in c for c in spec['classes']):
        raise Ineligible('mapping classes need law_class and include')
    if len(spec['mappers']) < 3:
        raise Ineligible('two independent mappers plus a third reader are required')
    if not spec['candidate_log']:
        raise Ineligible('every candidate mapping considered must be logged before state dates are viewed')
    if type(spec['window_start']) is not int or type(spec['transition_year_drop']) is not bool:
        raise Ineligible('window_start must be int and transition_year_drop bool')
    if len(set(spec['universe'])) != len(spec['universe']):
        raise Ineligible('duplicate universe')
    return info


def mapping_sha(spec) -> str:
    return digest(spec)


def _matches(row, rule):
    for field, allowed in rule.items():
        if field not in COLS:
            raise Ineligible('unknown mapping field ' + field)
        values = [None if v is None else str(v).strip().lower() for v in allowed]
        if row[field] not in values:
            return False
    return True


def classify_row(row, spec):
    """'include' | 'exclude' | 'ambiguous' | None (row is not in a candidate class)."""
    for cls in spec['classes']:
        if row['law_class'] != str(cls['law_class']).strip().lower():
            continue
        if any(_matches(row, rule) for rule in cls.get('exclude', [])):
            return 'exclude'
        if _matches(row, cls['include']):
            return 'include'
        return 'ambiguous'
    return None


def ambiguity(rows, spec):
    tally = {'include': 0, 'exclude': 0, 'ambiguous': 0}
    for row in rows:
        c = classify_row(row, spec)
        if c:
            tally[c] += 1
    total = sum(tally.values())
    share = tally['ambiguous'] / total if total else 1.0
    return dict(tally, candidate_rows=total, ambiguous_share=share)


# ---------------------------------------------------------------- build
def _is_placeholder_year(row):
    return row['year'] == PLACEHOLDER_YEAR


def _md_placeholder(row):
    note = (row.get('note') or '').lower()
    return bool(note) or row['month'] in (None, 0) or row['day'] in (None, 0)


def _interval(row):
    return dict(law_id=row['law_id'], start=row['year'], end=row['sup_year'], citation=row['citation'],
                effective=[row['year'], row['month'], row['day']], placeholder_year=_is_placeholder_year(row),
                placeholder_month_day=_md_placeholder(row), note=row['note'], discrepancy=row['discrepancy'],
                supersession=[row['sup_year'], row['sup_month'], row['sup_day']])


def build_history(rows, spec, universe=None):
    """One coder-format record per state, or an exclusion with a reason. No outcome is read."""
    info = validate_mapping(spec)
    amb = ambiguity(rows, spec)
    if amb['ambiguous_share'] > MAX_AMBIGUOUS_SHARE:
        raise Ineligible('ambiguous rows %.0f%% > %.0f%%: fresh coding' % (100 * amb['ambiguous_share'], 100 * MAX_AMBIGUOUS_SHARE))
    universe = list(universe or spec['universe'])
    W, END, drop = spec['window_start'], spec['coverage_end_year'], spec['transition_year_drop']
    source_id = 'DB:%s:%s' % (spec['database']['name'], spec['database']['version'])
    by_state = {u: [] for u in universe}
    ambiguous_rows = {u: 0 for u in universe}
    for row in rows:
        kind = classify_row(row, spec) if row['state'] in by_state else None
        if kind == 'include' and type(row['year']) is int:
            by_state[row['state']].append(row)
        elif kind == 'ambiguous':
            ambiguous_rows[row['state']] += 1
    accepted, excluded, reasons = [], [], {}
    for unit in universe:
        ivs = sorted((_interval(r) for r in by_state[unit] if r['year'] <= END and r['change'] != 'repeal'), key=lambda x: (x['start'], x['law_id']))
        repeal_rows = [r for r in by_state[unit] if r['year'] <= END and r['change'] == 'repeal']
        base = dict(unit=unit, mechanism=spec['mechanism'], source_id=source_id, confidence='medium', date_precision='year')
        # T219: a repeal row is never an onset; a qualifying repeal inside coverage excludes the state (reversal rule)
        if repeal_rows:
            excluded.append(unit); reasons[unit] = 'repeal_row'
            continue
        if not ivs and ambiguous_rows[unit]:
            # T219: a state whose only candidate-class rows are ambiguous is neither treated nor a clean control: excluded
            excluded.append(unit); reasons[unit] = 'ambiguous_rows_only'
            continue
        if not ivs:
            accepted.append(dict(base, status='never_treated', start_period=None, best_evidence_start_year=None,
                locator='%s: no qualifying row (mapping %s)' % (source_id, spec['mapping_id']),
                db_provenance=dict(law_ids=[], absence_of_row=True, coverage_end_year=END)))
            continue
        # reversal rule (written before dates are seen): any ended or interrupted qualifying law inside coverage excludes the state
        running, gap = ivs[0]['end'], False
        for nxt in ivs[1:]:
            if running is not None and nxt['start'] > running:
                gap = True
            running = None if (running is None or nxt['end'] is None) else max(running, nxt['end'])
        ended = running is not None and running <= END
        if gap or ended:
            excluded.append(unit)
            if not gap and ivs[0]['placeholder_year'] and running < W:
                reasons[unit] = 'placeholder_year_not_in_force_at_window_start'
            else:
                reasons[unit] = 'repeal_and_reenactment' if gap else 'reversal'
            continue
        first = ivs[0]
        Y = first['start']
        flags = dict(placeholder_year=first['placeholder_year'], placeholder_month_day=first['placeholder_month_day'])
        prov = dict(law_ids=[i['law_id'] for i in ivs], citations=[i['citation'] for i in ivs], effective=first['effective'],
                    supersession=[i['supersession'] for i in ivs], discrepancy=[i['discrepancy'] for i in ivs if i['discrepancy']],
                    flags=flags, multi_row=len(ivs) > 1, coverage_end_year=END)
        if first['placeholder_year']:
            if ivs[-1]['end'] is not None and ivs[-1]['end'] < W:
                excluded.append(unit); reasons[unit] = 'placeholder_year_not_in_force_at_window_start'
                continue
            Y = W - 1   # upper bound only: the law is in force before the window (exclusion flag, never timing)
            prov['placeholder_substitute_year'] = Y
        if Y < W:
            accepted.append(dict(base, status='treated', start_period=Y, best_evidence_start_year=Y, pre_window_bound=True,
                evidence_status='known_start', locator=first['citation'] or source_id,
                evidence_refs=[dict(source_id=source_id, locator=first['citation'] or '', supports='start_year', year=Y,
                                    law_id=first['law_id'])],
                db_provenance=prov))
        else:
            accepted.append(dict(base, status='treated', start_period=Y + 1 if drop else Y, best_evidence_start_year=Y,
                evidence_status='known_start', locator=first['citation'] or source_id,
                first_wholly_exposed_calendar_year=Y + 1,
                evidence_refs=[dict(source_id=source_id, locator=first['citation'] or '', supports='start_year', year=Y,
                                    law_id=first['law_id'])],
                db_provenance=prov))
    return dict(accepted=accepted, excluded=excluded, exclusion_reasons=reasons, ambiguity=amb,
                mapping_sha256=mapping_sha(spec), database=dict(spec['database'], citation=info['citation']))


# ---------------------------------------------------------------- audit plan
def derive_seed(xlsx_sha, mapping_hash, design_id) -> int:
    return int(hashlib.sha256((xlsx_sha + mapping_hash + design_id).encode()).hexdigest()[:16], 16)


def _forced(accepted, spec):
    W, END = spec['window_start'], spec['coverage_end_year']
    treated = [r for r in accepted if r['status'] == 'treated' and not r.get('pre_window_bound')]
    bound = [r for r in accepted if r.get('pre_window_bound')]
    never = [r for r in accepted if r['status'] == 'never_treated']
    forced = {}

    def add(unit, why):
        forced.setdefault(unit, []).append(why)
    ordered = sorted(treated, key=lambda r: (r['best_evidence_start_year'], r['unit']))
    for r in ordered[:2]: add(r['unit'], 'earliest_adopter')
    for r in ordered[-2:]: add(r['unit'], 'latest_adopter')
    for r in accepted:
        p = r.get('db_provenance', {})
        f = p.get('flags', {})
        if f.get('placeholder_year') or f.get('placeholder_month_day'): add(r['unit'], 'placeholder_date')
        if p.get('discrepancy'): add(r['unit'], 'discrepancy_note')
        if p.get('multi_row'): add(r['unit'], 'multiple_rows_or_supersession')
        y = r.get('best_evidence_start_year')
        if r['status'] == 'treated' and not r.get('pre_window_bound') and type(y) is int and (abs(y - W) <= 1 or abs(y - END) <= 1):
            add(r['unit'], 'near_window_edge')
    return forced, treated, bound, never


def audit_plan(history, spec, design_id):
    """Seeded stratified sample, extension pool and replacement order. Contains no database value."""
    accepted = history['accepted']
    seed = derive_seed(spec['database']['xlsx_sha256'], history['mapping_sha256'], design_id)
    rng = random.Random(seed)
    forced, treated, bound, never = _forced(accepted, spec)
    coded = sorted(r['unit'] for r in accepted)
    n_adopt = len(treated)
    if n_adopt <= ALL_STATES_AT_OR_BELOW:
        sample = coded
        reasons = {u: sorted(set(forced.get(u, []) + ['census_small_universe'])) for u in sample}
        backups = []
        k = len(sample)
    else:
        k = max(SAMPLE_FLOOR, math.ceil(SAMPLE_SHARE * n_adopt))
        must = dict(forced)
        # at least 2 never-treated and 1 pre-window state, drawn at random from their own strata
        for pool, n, why in ((sorted(r['unit'] for r in never), 2, 'control_stratum'), (sorted(r['unit'] for r in bound), 1, 'pre_window_stratum')):
            have = [u for u in pool if u in must]
            for u in rng.sample([u for u in pool if u not in must], max(0, min(n - len(have), len(pool) - len(have)))):
                must.setdefault(u, []).append(why)
        sample_set = sorted(must)
        k_eff = max(k, len(sample_set))
        rest = sorted(u for u in coded if u not in must)
        rng.shuffle(rest)
        take = k_eff - len(sample_set)
        random_part = rest[:take]
        backups = rest[take:]
        sample = sorted(sample_set + random_part)
        reasons = {u: sorted(set(must.get(u, ['random_draw']))) for u in sample}
        k = k_eff
    return dict(schema='l2_curated_audit_plan_v1', design_id=design_id, seed=seed, k=k, n_adopting=n_adopt,
                sample=sample, strata=reasons, random_part=sorted(u for u in sample if reasons[u] == ['random_draw']),
                backup_order=backups, extension_size=min(EXTENSION, len(backups)),
                mapping_sha256=history['mapping_sha256'], xlsx_sha256=spec['database']['xlsx_sha256'],
                census=n_adopt <= ALL_STATES_AT_OR_BELOW)


def audit_chunk_specs(plan, units=None, chunk_units=10, source_prefix='CUR'):
    """Chunk blocks for the existing history chunk packets (agents.history.emit_history_job(chunk=...))."""
    units = list(units if units is not None else plan['sample'])
    blocks = [units[i:i + chunk_units] for i in range(0, len(units), chunk_units)] or [[]]
    return [dict(completion_units=b, chunk=dict(index=i + 1, total=len(blocks), source_prefix='%s%d_' % (source_prefix, i + 1)))
            for i, b in enumerate(blocks)]


BLIND_FORBIDDEN = ('db_provenance', 'law_id', 'law_ids', 'citations', 'effective', 'best_evidence_start_year', 'start_period',
                   'status', 'xlsx_sha256', 'mapping_sha256', 'seed', 'strata')


def audit_packet(plan, card):
    """What the A/B workers receive for the sample: predicate, window, units. Never a database value or the selection reason."""
    t = card['treatment']
    packet = dict(design_id=plan['design_id'], units=list(plan['sample']), predicate=t['predicate'],
                  mechanism=card.get('mechanism'), coverage_end=t['coverage_end'],
                  start_period_kind=t.get('start_period_kind'), transition_year_drop=t.get('transition_year_drop'),
                  instruction='Code each unit from official statute text only; no database value is available to you.')
    assert_blind(packet)
    return packet


def assert_blind(packet):
    def walk(x, path=''):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in BLIND_FORBIDDEN:
                    raise ValueError('audit packet leaks %s%s' % (path, k))
                walk(v, path + k + '.')
        elif isinstance(x, list):
            for v in x: walk(v, path)
    walk(packet)
    return True


# ---------------------------------------------------------------- compare & evaluate
def classify(db_row, fresh, spec):
    """Compare one database-built record with the reconciled fresh record of the same state (None = unreachable)."""
    W, END = spec['window_start'], spec['coverage_end_year']
    if fresh is None or fresh.get('status') not in {'treated', 'never_treated', 'not_yet_treated'}:
        return dict(status='unverified')
    f_year = fresh.get('best_evidence_start_year')
    if db_row['status'] == 'never_treated':
        if fresh['status'] == 'never_treated' or (type(f_year) is int and f_year > END):
            return dict(status='match')
        return dict(status='predicate', official_start_period=fresh.get('start_period'))
    if fresh['status'] == 'never_treated':
        return dict(status='predicate')
    if type(f_year) is not int:
        return dict(status='unverified')
    if db_row.get('pre_window_bound'):
        return dict(status='match') if f_year < W else dict(status='timing', corrected_start_period=f_year + 1)
    if f_year != db_row['best_evidence_start_year']:
        return dict(status='timing', corrected_start_period=f_year + (1 if spec['transition_year_drop'] else 0))
    date = fresh.get('operation_date') or fresh.get('effective_date')
    eff = db_row.get('db_provenance', {}).get('effective') or []
    if isinstance(date, str) and len(date) == 10 and len(eff) == 3 and None not in eff and not db_row['db_provenance']['flags']['placeholder_month_day']:
        if (int(date[5:7]), int(date[8:10])) != (eff[1], eff[2]):
            return dict(status='detail')
    return dict(status='match')


def evaluate(plan, checks):
    """Pass rule R-CH 7. ``checks``: dicts with unit, status (match|timing|predicate|detail|unverified), official_locator,
    phase (sample|replacement|extension), optional cause and corrected_start_period."""
    sample, backups = set(plan['sample']), plan['backup_order']
    keyed = {}
    for c in checks:
        if c['unit'] in keyed: raise ValueError('Duplicate audit check: ' + c['unit'])
        if c.get('status') not in {'match', 'timing', 'predicate', 'detail', 'unverified'}: raise ValueError('Invalid audit status')
        if c['status'] != 'unverified' and not c.get('official_locator'): raise ValueError('Verified check needs an official locator')
        keyed[c['unit']] = c
    for u in sample:
        if u not in keyed: raise ValueError('Audit lacks sample state ' + u)
    base = [keyed[u] for u in sorted(sample)]
    unverifiable_share = sum(c['status'] == 'unverified' for c in base) / len(base)
    # a replacement is allowed once per unverified sample state, in the fixed backup order
    unverified = [c['unit'] for c in base if c['status'] == 'unverified']
    repl_expected = backups[:len(unverified)]
    repl = [keyed[u] for u in repl_expected if u in keyed]
    final = [c for c in base if c['status'] != 'unverified'] + repl
    for c in checks:
        if c['unit'] not in sample and c['unit'] not in repl_expected and c['unit'] not in plan['backup_order'][len(unverified):len(unverified) + EXTENSION]:
            raise ValueError('Audit check outside sample, replacements and extension: ' + c['unit'])
    ext_units = backups[len(unverified):len(unverified) + EXTENSION]
    role = {**{u: 'sample' for u in sample}, **{u: 'replacement' for u in repl_expected}, **{u: 'extension' for u in ext_units}}
    for c in checks:
        if c.get('phase') is not None and role.get(c['unit']) != c['phase']:
            raise ValueError('Audit check %s has phase %s, expected %s' % (c['unit'], c['phase'], role.get(c['unit'])))
    ext = [keyed[u] for u in ext_units if u in keyed]
    unresolved = sum(c['status'] == 'unverified' for c in final) + max(0, len(unverified) - len(repl))
    pool = final
    hard = [c for c in pool + ext if c['status'] in {'timing', 'predicate'}]
    timing = [c for c in hard if c['status'] == 'timing']
    predicate = [c for c in hard if c['status'] == 'predicate']
    details = [c for c in pool + ext if c['status'] == 'detail']
    causes = [c.get('cause') for c in hard if c.get('cause')]
    systematic = len(causes) != len(set(causes)) or any(c.get('cause') == 'systematic' for c in hard)
    corrected = all(c.get('corrected_start_period') is not None for c in timing)
    out = dict(sample_size=len(sample), known_start_errors=len(hard), timing_errors=len(timing), predicate_errors=len(predicate),
               detail_mismatches=len(details), unresolved=unresolved, known_errors_corrected=corrected, systematic_cause=systematic,
               unverifiable_share=unverifiable_share, replacements=repl_expected[:len(repl)], extension_units=ext_units,
               unverified_database_units=sorted(unverified),
               random_part_errors=sum(1 for c in hard if c['unit'] in plan['random_part']), random_part_size=len(plan['random_part']))
    if plan['census']:
        out['census'] = True
    if unverifiable_share > 1 / 3:
        return dict(out, passed=False, verdict='inconclusive', route='not_evaluable')
    if predicate or len(timing) >= 2 or systematic or unresolved > MAX_UNRESOLVED:
        return dict(out, passed=False, verdict='fail', route='fresh_coding')
    need_ext = (len(timing) == 1 or len(details) >= 3) and not plan['census']
    if need_ext:
        ext_hard = [c for c in ext if c['status'] != 'match']
        if len(ext) < len(ext_units) or len(ext_units) < EXTENSION:
            if len(ext_units) < EXTENSION:
                return dict(out, passed=False, verdict='fail', route='fresh_coding', reason='extension pool too small')
            return dict(out, passed=False, verdict='extend', route='extend')
        if ext_hard:
            return dict(out, passed=False, verdict='fail', route='fresh_coding', reason='extension state not clean')
    if not corrected:
        return dict(out, passed=False, verdict='fail', route='fresh_coding', reason='timing error without corrected date')
    return dict(out, passed=True, verdict='pass', route=HISTORY_SOURCE)


def apply_corrections(history, checks, spec):
    """Official text wins: replace the DB year for audited timing errors; keep the DB value beside it."""
    fixed = deepcopy(history['accepted'])
    by = {r['unit']: r for r in fixed}
    for c in checks:
        if c['status'] == 'timing' and c['unit'] in by:
            r = by[c['unit']]
            old = dict(best_evidence_start_year=r['best_evidence_start_year'], start_period=r['start_period'])
            new_start = c['corrected_start_period']
            r['audit_correction'] = dict(database_value=old, corrected_start_period=new_start, official_locator=c.get('official_locator'))
            r['best_evidence_start_year'] = new_start - (1 if spec['transition_year_drop'] else 0)
            r['start_period'] = new_start
            r.pop('pre_window_bound', None)
    return fixed


# ---------------------------------------------------------------- card declaration
def declaration(payload):
    value = (payload.get('automation') or {}).get('history_source')
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'kind', 'mapping_sha256', 'reason', 'rule'}
            or value['kind'] != SOURCE_KIND or value['reason'] != 'access' or value['rule'] != RULING
            or not isinstance(value['mapping_sha256'], str) or len(value['mapping_sha256']) != 64):
        raise ValueError('Invalid history_source declaration')
    if (payload.get('automation') or {}).get('history_reuse') is not None:
        raise ValueError('history_source and history_reuse are mutually exclusive')
    return value


# ---------------------------------------------------------------- ledger (policy-level, one audit per key)
def audit_key(spec, design_universe):
    return digest(dict(xlsx=spec['database']['xlsx_sha256'], mapping=mapping_sha(spec), universe=sorted(design_universe)))


def claim_audit(ledger_dir, spec, design_universe, design_id, *, reaudit_ruling=None):
    """One audit per (database sha, mapping sha, universe). A re-audit needs a new mapping hash AND a hub ruling path; at most one."""
    ledger_dir = Path(ledger_dir); ledger_dir.mkdir(parents=True, exist_ok=True)
    policy = spec['policy_family'] + '__' + spec['database']['xlsx_sha256'][:12]
    path = ledger_dir / (policy + '.json')
    state = json.loads(path.read_text()) if path.exists() else dict(policy=policy, audits=[])
    key = audit_key(spec, design_universe)
    if any(a['key'] == key for a in state['audits']):
        raise ValueError('Audit already claimed for this database, mapping and universe: no second draw')
    failed = [a for a in state['audits'] if a.get('result') in {'fail', 'inconclusive'}]
    if state['audits'] and not failed:
        # a passed or open audit exists for the policy+database: a new design must reuse (history_reuse rule), not draw again
        raise ValueError('Policy already has a db audit; use certified-history reuse')
    if failed:
        if len(state['audits']) >= 2:
            raise ValueError('Failed policy: no second re-audit, fresh coding')
        if not reaudit_ruling:
            raise ValueError('A failed policy goes to fresh coding; one re-audit needs a signed transform ruling')
        if key == failed[0]['key']:
            raise ValueError('Re-audit needs a new mapping hash')
    state['audits'].append(dict(key=key, design_id=design_id, mapping_sha256=mapping_sha(spec), result=None,
                                reaudit_ruling=reaudit_ruling))
    path.write_text(json.dumps(state, sort_keys=True, indent=1) + '\n')
    return path


def resume_claim(ledger_dir, spec, design_universe, design_id):
    """T287 (HUB_RULING_20261023_T287_CLOSURES Q5): the SAME open claim may be used again by the SAME design. Needs an existing ledger entry with this key, this design
    and this mapping hash whose result is still None (no verdict was ever recorded). Never appends, never redraws; anything else raises."""
    policy = spec['policy_family'] + '__' + spec['database']['xlsx_sha256'][:12]
    path = Path(ledger_dir) / (policy + '.json')
    if not path.exists():
        raise ValueError('No ledger claim to resume')
    state = json.loads(path.read_text())
    key = audit_key(spec, design_universe)
    hits = [a for a in state['audits'] if a['key'] == key]
    if len(hits) != 1 or len(state['audits']) != 1:
        raise ValueError('Resume needs exactly one open claim for this key')
    a = hits[0]
    if a.get('design_id') != design_id or a.get('mapping_sha256') != mapping_sha(spec) or a.get('result') is not None:
        raise ValueError('Resume refused: the claim belongs to another design or mapping, or already has a result')
    return path


def record_result(ledger_dir, spec, design_universe, result):
    path = Path(ledger_dir) / (spec['policy_family'] + '__' + spec['database']['xlsx_sha256'][:12] + '.json')
    state = json.loads(path.read_text())
    key = audit_key(spec, design_universe)
    for a in state['audits']:
        if a['key'] == key:
            if a['result'] is not None:
                raise ValueError('Audit result already recorded')
            a['result'] = result
    path.write_text(json.dumps(state, sort_keys=True, indent=1) + '\n')
    return path


# ---------------------------------------------------------------- certify
def certify(design_dir, history, spec, plan, checks, verdict, *, card_excluded=(), ensure_once=None, sha=None, engine_records=None):
    """Write DB_HISTORY_SOURCE.json, CERTIFIED_HISTORY.json (db_audited), TREATMENT.json, S1_IMPORT_PROVENANCE.json (write-once).

    Refuses anything but a passed audit. ``ensure_once``/``sha``/``engine_records`` default to the live helpers."""
    if verdict.get('verdict') != 'pass' or verdict.get('passed') is not True:
        raise ValueError('Curated history can be certified only after a passed audit: ' + str(verdict.get('verdict')))
    if ensure_once is None:
        from .storage import ensure_once, sha
        from .intake.official_history import engine_records
    from . import prewindow
    design_dir = Path(design_dir)
    accepted = apply_corrections(history, checks, spec)
    info = QUALIFYING[(spec['database']['name'], spec['database']['version'])]
    source = ensure_once(design_dir / 'DB_HISTORY_SOURCE.json', dict(
        schema='l2_db_history_source_v1', rule=RULING, database=spec['database'], citation=info['citation'], license=info['license'],
        changes_made='States mapped to the card predicate by script; official-text corrections recorded per audited state; no row-level table republished.',
        mapping=spec, mapping_sha256=history['mapping_sha256'], plan=plan, checks=checks, verdict=verdict,
        per_state={r['unit']: r.get('db_provenance') for r in history['accepted']},
        excluded=history['excluded'], exclusion_reasons=history['exclusion_reasons'], ambiguity=history['ambiguity']))
    counts = dict(known_match=len(accepted))
    audit = dict(passed=True, sample_size=verdict['sample_size'], known_start_errors=verdict['known_start_errors'],
                 known_errors_corrected=verdict['known_errors_corrected'], unresolved=verdict['unresolved'],
                 history_source=HISTORY_SOURCE)
    certificate = ensure_once(design_dir / 'CERTIFIED_HISTORY.json', dict(
        comparison=dict(accepted=accepted, agreement=None, counts=counts, gaps=[], paired_known=0),
        audit=audit, sample=plan['sample'], conflicts=[],
        excluded_conflict_states=sorted(history['excluded']),
        excluded_conflict_state_reasons=dict(history['exclusion_reasons']),
        history_source=dict(kind=SOURCE_KIND, label=HISTORY_SOURCE, db_history_source_sha256=sha(source),
                            mapping_sha256=history['mapping_sha256'], database_sha256=spec['database']['xlsx_sha256']),
        rule=CERT_RULE, execution_authorized=False))
    bound = prewindow.bound_units(accepted)
    excluded = set(card_excluded) | set(history['excluded']) | set(bound)
    records = engine_records([r for r in accepted], excluded)
    extra = {'pre_window_bound_units': bound} if bound else {}
    treatment = ensure_once(design_dir / 'TREATMENT.json', dict(
        records=records, all_records=accepted, **extra, audit=audit, sample=plan['sample'], source_sha256=sha(certificate),
        excluded_primary_units=sorted(excluded), excluded_conflict_states=sorted(history['excluded']),
        excluded_conflict_state_reasons=dict(history['exclusion_reasons']),
        transition_year_drop=spec['transition_year_drop'], execution_authorized=False))
    proof = ensure_once(design_dir / 'S1_IMPORT_PROVENANCE.json', dict(
        evidence=[dict(path=str(source.resolve()), sha256=sha(source)), dict(path=str(certificate.resolve()), sha256=sha(certificate))],
        v3_passed=True, source_type=SOURCE_TYPE))
    return dict(source=source, certificate=certificate, treatment=treatment, provenance=proof)
