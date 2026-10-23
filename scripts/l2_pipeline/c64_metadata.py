"""C64 metadata contracts and immutable migration plans; never fit outcomes."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path

ADDENDUM_PATH = 'docs/plans/l3-meta/PREREG_L3_META_ADDENDUM_V2.md'
ADDENDUM_SHA256 = '96e3ec8096f3b8d676a60db5c099766451ed4251bd7578037fdddc0a972ba30a'
COVARIATES_PATH = 'data/l2_pipeline/autopilot/c64/STATE_COVARIATES.json'
SCHEMA = 'l2_addendum_v2_c64'

def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

def _repo(repo=None):
    if repo is not None and (Path(repo) / COVARIATES_PATH).is_file():
        return Path(repo).resolve()
    # Prefer the local devcopy manifest; then the durable repository manifest.
    for parent in Path(__file__).resolve().parents:
        if (parent / COVARIATES_PATH).is_file():
            return parent
    raise ValueError('C64 state covariate manifest is required')

def export_contract(card, repo=None):
    root = _repo(repo)
    manifest_path = root / COVARIATES_PATH
    manifest = json.loads(manifest_path.read_text())
    payload = getattr(card, 'payload', card)
    from .c70_l3 import context_contract as amendment_contract
    return dict(schema=SCHEMA, amendment_v2=amendment_contract(payload,root), addendum_path=ADDENDUM_PATH,
        addendum_sha256=ADDENDUM_SHA256, outcome_compute_stage='S6',
        export_stage='S7', calibration_outcome_computation=False,
        first_order=dict(purpose_direction=payload.get('first_order', {}).get('purpose_direction'),
            coding_stage='S0', coding_status='PENDING_BLIND_CODING',
            rule='Two blind statute/S0-target coders; third on disagreement; no estimate, forecast or literature sign; locator required'),
        s_pre=dict(definition='Pooled sample SD of clean pre-period unit-years in outcome units', ddof=1),
        H=dict(definition='Post years in the fixed balanced event-time profile'),
        placebo_profiles=dict(event_times=list(range(6)), draws='Same fixed noise-floor relabelings; preserve infeasible draws'),
        trough=dict(recent_window=[-3,-1], baseline_end=-4, minimum_baseline_years=3,
            demean='Subtract cross-state U_i mean each year; pre-period data only'),
        geography=dict(universe='CARD.treatment.universe less excluded, conflict, always-treated units',
            treated='Cohorts with supported frozen primary post cells',
            risk_set='U_i less every state with policy in force in last panel year',
            state_covariates=dict(path=COVARIATES_PATH, sha256=sha(manifest_path), manifest=manifest)),
        required_fields=['first_order.purpose_direction','s_pre','H','placebo_profiles','trough','geography','placebo_labels'],
        unregistered_tests=['N6','N8','N11','N12'])

def protected_inventory(repo):
    """Hash existing sealed/frozen/finished package bytes without decoding outcomes."""
    root = Path(repo).resolve()
    base = root / 'data/l2_pipeline'
    package_roots = []
    if base.exists():
        for current, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in {'devcopy', '__pycache__'}]
            names = set(files)
            if (names & {'FREEZE_PREP.json', 'FREEZE_FINAL.json', 'APPROVAL_PREP.json', 'APPROVAL_FINAL.json', 'EXECUTION_ONCE.json', 'TERMINAL.json', 'FAILURE.json', 'L3_EXPORT_DATA.json', 'RESULT.json'}
                    or any(name.startswith('SEALED_') and name.endswith('.json') for name in names)):
                package_roots.append(Path(current))
                dirs[:] = []
    inventory = {}
    for package in package_roots:
        for current, dirs, files in os.walk(package):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            for name in files:
                path = Path(current) / name
                if path.is_file() and not path.is_symlink():
                    inventory[str(path.relative_to(root))] = sha(path)
    for name in (ADDENDUM_PATH, 'docs/plans/l3-meta/PREREG_ADDENDUM_V2_HASH.txt'):
        path = root / name
        if path.is_file():
            inventory[name] = sha(path)
    return dict(sorted(inventory.items()))

def verify_protected(repo, inventory):
    root = Path(repo).resolve()
    errors = []
    for relative, expected in inventory.items():
        path = root / relative
        if not path.is_file():
            errors.append(relative + ': missing')
        elif sha(path) != expected:
            errors.append(relative + ': bytes changed')
    return errors

def _write_once(path, value):
    path = Path(path)
    data = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('Immutable C64 artifact already differs: ' + str(path))
        return
    with path.open('xb') as stream:
        stream.write(data)

def plan_next_versions(repo, output):
    """Create next-card proposals only. Caller binds supplemental SPEC before prep."""
    root = Path(repo).resolve(); output = Path(output).resolve()
    registry = json.loads((root / 'data/l2_pipeline/registry/ACTIVE_DESIGNS.json').read_text())
    rows = []
    for row in registry['designs']:
        identity = row['design_id']
        if Path(identity).name != identity or identity in {'.', '..'}:
            raise ValueError('Invalid design identity')
        card_path = Path(row['card'])
        if not card_path.is_absolute(): card_path = root / card_path
        card = json.loads(card_path.read_text())
        work = Path(row['work_dir'])
        if not work.is_absolute(): work = root / work
        package = work / identity / card.get('version', 'v1')
        # Follow all metadata-only descendants for prior effective-context freezes.
        family = work / identity
        protected = [p for p in family.rglob('*') if p.is_file() and
            (p.name in {'FREEZE_PREP.json','FREEZE_FINAL.json','APPROVAL_PREP.json','APPROVAL_FINAL.json','EXECUTION_ONCE.json','TERMINAL.json','FAILURE.json','L3_EXPORT_DATA.json','RESULT.json'}
             or (p.name.startswith('SEALED_') and p.suffix == '.json'))] if family.exists() else []
        record = dict(design_id=identity, source_card=str(card_path), source_card_sha256=sha(card_path),
            package=str(package))
        if protected:
            record.update(status='addendum fields unavailable', protected_markers=[str(p) for p in sorted(protected)])
        else:
            proposed = copy.deepcopy(card)
            proposed.setdefault('first_order', {}).setdefault('purpose_direction', None)
            proposed.setdefault('l3_amendment_v2',dict(statute_variant=None,interruption_ledger=None,pub_ledger=None,pub_ledger_sha256=None))
            proposed['l3_export_v2'] = export_contract(proposed, root)
            proposed.setdefault('automation',{})['c64_next_card'] = dict(task='C64', source_card_sha256=sha(card_path),
                original_package_unchanged=True, purpose_direction_pending_blind_coding=True)
            target = output / identity / 'NEXT_CARD_C64.json'
            _write_once(target, proposed)
            record.update(status='NEXT_VERSION_PLANNED', next_card=str(target), next_card_sha256=sha(target),
                supplemental_spec='SPEC_ADDENDUM_C64.json',
                supplemental_specs=['SPEC_ADDENDUM_C64.json','SPEC_ADDENDUM_C70.json'],
                requirement='Bind export contract before prep freeze; do not rewrite a prior card or SPEC')
        rows.append(record)
    result = dict(schema='l2_c64_migration_plan_v1', metadata_only=True, registry_modified=False,
        source_registry_sha256=sha(root / 'data/l2_pipeline/registry/ACTIVE_DESIGNS.json'), designs=rows)
    _write_once(output / 'MIGRATION_PLAN.json', result)
    return result


def geography(card, treatment=None):
    """Retain legal metadata and derive exact annual geography without outcomes.

    Explicit annual intervals override absorbing treatment labels. A closed spell
    does not establish subsequent absence unless the history covers that year.
    Unsupported clocks and unresolved histories remain FIELD_MISSING.
    """
    payload = getattr(card, 'payload', card)
    cfg = payload.get('treatment', {})
    legal = treatment or {}
    records = copy.deepcopy(legal.get('records', []))
    out = dict(universe=copy.deepcopy(cfg.get('universe')),
        excluded_units=copy.deepcopy(cfg.get('excluded_units', [])),
        excluded_conflict_states=copy.deepcopy(cfg.get('excluded_conflict_states', [])),
        window=copy.deepcopy(cfg.get('window')),
        outcome_years=copy.deepcopy(payload.get('outcome', {}).get('years')),
        treatment_records=records, treatment_audit=copy.deepcopy(legal.get('audit')),
        treatment_metadata={k: copy.deepcopy(legal[k]) for k in
            ('window','coverage','all_dated_records','excluded_primary_units','source_sha256') if k in legal},
        U=None, R=None, status='FIELD_MISSING', reasons=[], metadata_only=True)
    universe = cfg.get('universe')
    years = payload.get('outcome', {}).get('years')
    frequency = payload.get('outcome', {}).get('frequency', 'annual')
    if (not isinstance(universe, list) or not all(isinstance(u,str) for u in universe)
            or len(set(universe)) != len(universe)):
        out['reasons'].append('Complete unique CARD.treatment.universe missing'); return out
    if (not isinstance(years, list) or len(years) != 2
            or any(type(y) is not int for y in years) or years[0] > years[1]
            or frequency not in {'annual','yearly','year'}):
        out['reasons'].append('Exact annual panel endpoints missing'); return out
    first, last = years
    out['panel_first_year'], out['panel_last_year'] = first, last
    excluded = set(cfg.get('excluded_units', [])) | set(cfg.get('excluded_conflict_states', []))
    always = set(cfg.get('always_treated', [])) | set(cfg.get('always_treated_units', []))
    # T192: a pre_window_bound state is a known pre-window adopter (already treated at the first panel year). Its bound year is
    # not an onset; the reason recorded by the C42 exclusion (or the retained flagged row) is all that is read.
    from .prewindow import REASON as _BOUND_REASON, is_bound as _is_bound
    always |= {u for u, why in (cfg.get('excluded_conflict_state_reasons') or {}).items()
               if isinstance(why, list) and _BOUND_REASON in why}
    always |= {r['unit'] for r in records if isinstance(r, dict) and _is_bound(r) and type(r.get('start_period')) is int
               and r['start_period'] < first}
    indexed = {}; duplicates = set()
    for record in records:
        if not isinstance(record,dict) or not isinstance(record.get('unit'),str): continue
        if record['unit'] in indexed: duplicates.add(record['unit'])
        indexed[record['unit']] = record

    def covered(row, year):
        for scope in (row.get('history_coverage'), row.get('source_window'),
                      legal.get('coverage'), cfg.get('source_window')):
            if not isinstance(scope,dict): continue
            a, b = scope.get('start_year'), scope.get('end_year')
            if type(a) is int and type(b) is int and a <= year <= b:
                return True
        return False

    def active(row, year):
        status = row.get('status')
        if status in {'unknown',None} or row.get('history_uncertain'):
            return None
        if _is_bound(row):
            # in force from its sourced year on (no lapse found); earlier years are unknown, never an onset
            return True if type(row.get('start_period')) is int and year >= row['start_period'] else None
        if year in row.get('unknown_years', []): return None
        # Unknown interval schemas cannot be treated as absence.
        if row.get('unknown_intervals'): return None
        intervals = row.get('operative_intervals', row.get('intervals'))
        if intervals is not None:
            if not isinstance(intervals,list): return None
            parsed = []
            for spell in intervals:
                if not isinstance(spell,dict): return None
                begin = spell.get('start_period', spell.get('start_year'))
                end = spell.get('end_period', spell.get('end_year'))
                # null ends require explicit open-ended declaration.
                if end is None and spell.get('ongoing') is True: end = last
                if type(begin) is not int or type(end) is not int or end < begin: return None
                parsed.append((begin,end))
            if any(a <= year <= b for a,b in parsed): return True
            if covered(row,year): return False
            if status in {'repealed','reverted'} and parsed and year > max(b for a,b in parsed): return False
            return None
        if status == 'never_treated':
            scopes = (row.get('history_coverage'), row.get('source_window'),
                      legal.get('coverage'), cfg.get('source_window'))
            if any(isinstance(scope,dict) for scope in scopes) and not covered(row,year):
                return None
            return False if row.get('start_period') is None else None
        if status in {'always_treated','baseline_treated'}: return True
        if row.get('left_censored') is True: return True if year == first else None
        if status in {'treated','not_yet_treated'}:
            # A dated onset gives pre-adoption absence. Without a complete
            # interval history it cannot certify post-adoption persistence.
            onset = row.get('operative_period', row.get('best_evidence_start_year',
                        row.get('effective_year',row.get('start_period'))))
            if type(onset) is not int: return None
            if year < onset: return False
            if (row.get('absorbing') is True or row.get('current_stock') == 1
                    and row.get('current_stock_year') == year): return True
            return None
        return None

    unknown_first, unknown_last, at_risk = [], [], []
    for unit in sorted(set(universe)-excluded-always):
        row = indexed.get(unit)
        if row is None or unit in duplicates:
            unknown_first.append(unit); unknown_last.append(unit); continue
        if row.get('status') in {'always_treated','baseline_treated'} or row.get('left_censored') is True:
            always.add(unit); continue
        initial, terminal = active(row,first), active(row,last)
        if initial is True:
            always.add(unit); continue
        if initial is None: unknown_first.append(unit)
        if terminal is None: unknown_last.append(unit)
        elif terminal is False: at_risk.append(unit)
    out['always_treated_units'] = sorted(always & set(universe))
    out['known_at_risk_units'] = sorted(at_risk)
    out['unresolved_baseline_units'], out['unresolved_last_year_units'] = unknown_first, unknown_last
    if unknown_first: out['reasons'].append('Baseline legal status unresolved')
    else: out['U'] = sorted(set(universe)-excluded-always)
    if unknown_last: out['reasons'].append('Last-panel-year legal status unresolved')
    if out['U'] is not None and not unknown_last:
        out['R'] = sorted(set(at_risk) & set(out['U']))
    if (legal.get('audit') or {}).get('passed') is not True:
        out['reasons'].append('Passing treatment audit missing')
        out['R'] = None
    if out['U'] is not None and out['R'] is not None:
        out['status'] = 'AVAILABLE'
    return out
