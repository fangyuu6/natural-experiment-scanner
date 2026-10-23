"""Bind independently reconciled V9 histories and completed V3 audits to S1.

This route certifies dated tuples under V4, not lifetime continuity. Retained
state conflicts park the design; excluded and unknown states never become controls.
"""
from copy import deepcopy
import json
from pathlib import Path

from ..agents.jobs import load_job
from ..contracts import Disposition, StageResult
from ..storage import artifact, ensure_once, public_input, sha
from ..stages.s1_treatment import audit_sample, evaluate_v3, reconcile

SOURCE_TYPE = 'official_history_v9'
RULINGS = (
    'docs/plans/l2-scale/HUB_RULING_V3_AUDIT.md',
    'docs/plans/l2-scale/HUB_RULING_V4_STOCK_HISTORY.md',
    'docs/plans/l2-pipeline/HUB_RULING_V9_HISTORY_EVIDENCE.md',
    'docs/plans/l2-pipeline/HUB_RULING_C11_IN_OPERATION.md',
    'docs/plans/l2-pipeline/HUB_RULING_C22_EXCLUDED_CONFLICT.md',
)


def read(path):
    return json.loads(public_input(Path(path)).read_text())


def binding(path):
    path = public_input(Path(path))
    return dict(path=str(path), sha256=sha(path))


CONFLICT_KINDS = ('known_conflict', 'best_evidence_year_conflict')
RESOLUTIONS = 'AUDIT_RESOLUTIONS.json'
RESOLUTIONS_SCHEMA = 'l2_audit_resolutions_v1'


def conflict_rows(comparison, excluded=(), cap=10, coders=None):
    """REDESIGN R2: the A/B disagreement states a fresh audit can settle (both coders gave a known coding).

    One-sided or both-unknown states are not auditable and stay C42 exclusions. Units the card itself excludes are never
    reopened. Sorted by unit, at most ``cap``; the rest stay C42 exclusions. A best-evidence-year gap carries no codings, so the
    caller's ``coders`` ({'a': {unit: row}, 'b': {unit: row}}) fill them in; without them the codings are None (counting only)."""
    seen, rows = set(), []
    for gap in sorted(comparison['gaps'], key=lambda g: g['unit']):
        if gap['kind'] not in CONFLICT_KINDS or gap['unit'] in excluded or gap['unit'] in seen:
            continue
        seen.add(gap['unit'])
        both = [deepcopy(gap.get(side) if gap.get(side) is not None else (coders or {}).get(side, {}).get(gap['unit'])) for side in ('a', 'b')]
        rows.append(dict(unit=gap['unit'], kind=gap['kind'], a=both[0], b=both[1]))
    return rows[:cap]


def _locator(value):
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, (list, dict)) and value:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    raise ValueError('Official source evidence missing')


def resolve_conflicts(conflict_units, checks):
    """Validate the auditor's conflict rows against the prelocked conflict states.

    Each state gets exactly one row: ``official_date`` with an integer first-full ``corrected_start_period`` and a locator, or
    ``unresolved`` with a locator. Returns (resolved rows sorted by unit, unresolved units sorted)."""
    units = [c['unit'] for c in conflict_units]
    if not isinstance(checks, list) or any(not isinstance(r, dict) for r in checks):
        raise ValueError('Audit conflict rows must be a list of objects')
    keyed = {r.get('unit'): r for r in checks}
    if len(keyed) != len(checks) or set(keyed) != set(units):
        raise ValueError('Audit conflict rows do not match the prelocked conflict states')
    resolved, unresolved = [], []
    for item in sorted(conflict_units, key=lambda c: c['unit']):
        row = keyed[item['unit']]
        locator = _locator(row.get('official_locator'))
        if row.get('status') == 'official_date':
            date = row.get('corrected_start_period')
            if type(date) is not int:
                raise ValueError('Conflict row with an official date needs an integer first-full period')
            resolved.append(dict(unit=item['unit'], kind=item['kind'], start_period=date, official_locator=locator))
        elif row.get('status') == 'unresolved':
            unresolved.append(item['unit'])
        else:
            raise ValueError('Conflict row status must be official_date or unresolved')
    return resolved, unresolved


def _compact(row):
    return {k: row.get(k) for k in ('status', 'start_period', 'mechanism', 'best_evidence_start_year')}


def corrected_comparison(comparison, checks, sample, *, remainder_census=False, limits=None, conflict_units=None, conflict_checks=None):
    """Apply located errors without changing blind originals or unresolved dates.

    REDESIGN R2: ``conflict_units``/``conflict_checks`` settle A/B disagreement states. The V3 error gate sees the random
    sample only; a state the audit dates joins ``accepted`` as a treated state with that date (coders' codings kept beside it)."""
    result = deepcopy(comparison)
    audit = evaluate_v3(sample, checks, remainder_census=remainder_census, limits=limits)
    by_unit = {r['unit']: r for r in result['accepted']}
    for check in checks:
        if check['unit'] not in by_unit:
            raise ValueError('Audited state is not independently agreed')
        if check['status'] == 'known_error':
            by_unit[check['unit']].update(start_period=check['corrected_start_period'],
                                        audit_correction=deepcopy(check))
        elif check['status'] == 'unresolved':
            by_unit[check['unit']]['audit_unresolved'] = True
    if conflict_units:
        resolved, _ = resolve_conflicts(conflict_units, conflict_checks)
        items = {c['unit']: c for c in conflict_units}
        for row in resolved:
            item = items[row['unit']]
            new = dict(unit=row['unit'], status='treated', start_period=row['start_period'],
                       best_evidence_start_year=row['start_period'] - 1,
                       mechanism=item['a'].get('mechanism') or item['b'].get('mechanism'), locator=row['official_locator'],
                       audit_resolution=dict(kind=row['kind'], official_locator=row['official_locator'],
                                             coder_a=_compact(item['a']), coder_b=_compact(item['b'])))
            if row['unit'] in by_unit:
                result['accepted'][result['accepted'].index(by_unit[row['unit']])] = new
            else:
                result['accepted'].append(new)
        done = {r['unit'] for r in resolved}
        for gap in result['gaps']:
            if gap['unit'] in done and gap['kind'] in CONFLICT_KINDS:
                gap['resolved_by_audit'] = True
    conflicts = [g['unit'] for g in result['gaps']
                 if g['kind'] in {'known_conflict', 'best_evidence_year_conflict'}]
    return result, audit, sorted(conflicts)


def audit_resolutions(report, answer, audit_files):
    """The value of AUDIT_RESOLUTIONS.json for a report whose R2 conflict states were audited (None if it had none)."""
    units = report.get('audit_conflict_units') or []
    if not units:
        return None
    resolved, unresolved = resolve_conflicts(units, answer.get('conflict_checks'))
    return dict(schema=RESOLUTIONS_SCHEMA, design_id=report['design_id'], conflict_units=units, resolved=resolved, unresolved=unresolved,
                reconciliation=audit_files['reconciliation'], audit_answer=audit_files['audit_answer'], audit_original=audit_files['audit_original'],
                execution_authorized=False, outcome_values_read=0)


def load_audit_resolutions(design_dir):
    """The hash-valid AUDIT_RESOLUTIONS.json of a design directory, None if absent; ValueError when anything was edited."""
    path = Path(design_dir) / RESOLUTIONS
    if not path.is_file():
        return None
    value = json.loads(path.read_text())
    if (value.get('schema') != RESOLUTIONS_SCHEMA or value.get('execution_authorized') is not False
            or any(set(value[k]) != {'path', 'sha256'} or sha(public_input(Path(value[k]['path']))) != value[k]['sha256']
                   for k in ('reconciliation', 'audit_answer', 'audit_original'))):
        raise ValueError('Audit resolutions binding changed')
    report = read(value['reconciliation']['path'])
    answer = read(value['audit_answer']['path'])
    expected = audit_resolutions(report, answer, {k: value[k] for k in ('reconciliation', 'audit_answer', 'audit_original')})
    if expected != value:
        raise ValueError('Audit resolutions differ from the audited conflict rows')
    return value


def conflict_scope(cfg, conflicts):
    """Scope certification only to explicitly registered whole-state drops.

    Ordinary exclusions alone do not close a conflict. Keep the conflict ledger
    and the fixed V3 sample intact; this declaration only scopes its S1 blocker.
    """
    declared = cfg.get('excluded_conflict_states', [])
    if (not isinstance(declared, list)
            or any(not isinstance(unit, str) or not unit for unit in declared)
            or len(set(declared)) != len(declared)):
        raise ValueError('excluded_conflict_states must be a unique list of states')
    excluded = set(cfg['excluded_units'])
    if not set(declared) <= excluded or not set(declared) <= set(cfg['universe']):
        raise ValueError('Conflict exclusions must be in the universe and excluded from both arms')
    return dict(excluded_conflict_states=sorted(declared),
                retained_conflict_states=sorted(set(conflicts) - set(declared)))


def derive(cfg, card, repo):
    """Reproduce the certificate from immutable collector and source bindings."""
    evidence = []
    for item in cfg['history_bindings']:
        if sha(public_input(Path(item['path']))) != item['sha256']:
            raise ValueError('Official history input changed: ' + item['path'])
        evidence.append(item)
    paths = {Path(i['path']).name: Path(i['path']) for i in evidence}
    history = read(paths['HISTORY.json'])
    report = read(paths['RECONCILIATION.json'])
    lock = read(paths['CODING_LOCK.json'])
    job = load_job(Path(cfg['official_audit_job']))
    c = job.contract
    if job.design_id != card['design_id'] or job.stage != 'S1' or job.role != 'auditor':
        raise ValueError('Official audit identity differs')
    for path, expected in c['bound_artifacts'].items():
        if sha(public_input(Path(path))) != expected:
            raise ValueError('Audit bound input changed')
        evidence.append(dict(path=path, sha256=expected))
    from ..source_packs import load_source_pack
    manifest_path = Path(c['source_pack']['manifest_path'])
    if sha(manifest_path) != c['source_sha256']:
        raise ValueError('Official source manifest changed')
    pack = load_source_pack(manifest_path)
    evidence.append(binding(manifest_path))
    for source in pack['sources']:
        evidence.extend(binding(source[k]) for k in ('path', 'text_path'))
    if (history['design_id'] != job.design_id or report['design_id'] != job.design_id
            or sha(paths['HISTORY.json']) != report['history_sha256']
            or history['validated_comparison'] != report['historical_comparison']):
        raise ValueError('History/reconciliation binding differs')
    if (set(history['collector_validation']) != {'A', 'B'}
            or history['collector_validation']['A']['original_path'] ==
               history['collector_validation']['B']['original_path']):
        raise ValueError('Independent A/B originals required')
    for role, receipt in history['collector_validation'].items():
        if receipt.get('rule') == 'exact_source_key_capitalization_only':
            original = read(receipt['original_path'])
            compatible = deepcopy(original)
            for source in compatible['sources']:
                for old, new in (('URL', 'url'), ('SHA256', 'sha256'), ('final_URL', 'final_url')):
                    if old in source:
                        source[new] = source[old]
            if (receipt.get('compatibility_validated') is not True
                    or compatible != read(receipt['view_path'])
                    or compatible != read(receipt['normalized_path'])
                    or receipt.get('normalization', {}).get('issues') != []):
                raise ValueError('Source-key compatibility changed scientific content')
            for field in ('original', 'view', 'normalized'):
                p = Path(receipt[field + '_path'])
                if sha(p) != receipt[field + '_sha256']:
                    raise ValueError('Source-key compatibility bytes changed')
                evidence.append(binding(p))
            continue
        if (receipt.get('validated') is not True or receipt['role'] != role
                or receipt['quality_gate']['status'] != 'PASS'
                or receipt['quality_gate']['evidence_standard'] != 'V9'):
            raise ValueError('V9 coder collection did not pass')
        for field in ('original', 'promoted'):
            p = Path(receipt[field + '_path'])
            if sha(p) != receipt[field + '_sha256']:
                raise ValueError('V9 original/normalization changed')
            evidence.append(binding(p))
    if lock['files'] != c['original_hashes']:
        raise ValueError('Coding lock differs from audited originals')
    for path, expected in lock['files'].items():
        if sha(path) != expected:
            raise ValueError('Blind original changed')
        evidence.append(dict(path=path, sha256=expected))
    alias = Path(lock['mechanism_alias_registry'])
    if sha(alias) != lock['mechanism_alias_registry_sha256']:
        raise ValueError('V9 alias registry changed')
    evidence.append(binding(alias))
    original = read(job.output_path)
    collection = read(Path(job.output_dir) / 'COLLECTION.json')
    if (collection.get('validated') is not True
            or collection['original_sha256'] != sha(job.output_path)
            or collection['job_sha256'] != sha(job.job_path)
            or collection['promoted_sha256'] != sha(job.promote_path)):
        raise ValueError('Completed audit collector binding differs')
    answer = read(job.promote_path)
    if (original['design_id'] != job.design_id or original['role'] != 'auditor'
            or original['source_sha256'] != c['source_sha256']
            or original['selection_sha256'] != c['selection_sha256']
            or original['blindness_attestation'] !=
               dict(other_submissions_read=False, outcome_associations_read=False)
            or answer['checks'] != original['checks']):
        raise ValueError('Audit identity/blindness differs')
    audited_card = read(paths['CARD_AUDIT_RATE_V2.json'])
    if (card['mechanism'] != c['mechanism'] or card.get('outcome_scale') != 'rate'
            or card.get('transition_year_drop') is not True
            or cfg.get('start_period_kind') != 'operative_year'
            or audited_card['outcome_scale'] != 'rate'
            or card['outcome']['period_years'] != audited_card['outcome']['period_years']
            or card['outcome']['population_filters'] != audited_card['outcome']['population_filters']
            or card['treatment']['universe'] != c['manual']['universe']
            or cfg['seed'] != c['manual']['seed']):
        raise ValueError('Card differs from audited outcome/clock scope')
    comparison = history['validated_comparison']
    reconcile(comparison['accepted'], comparison['accepted'], cfg['universe'])
    if audit_sample(comparison['accepted'], cfg['seed']) != c['sample']:
        raise ValueError('Fresh seeded sample differs')
    if any(r['mechanism'] != card['mechanism'] for r in comparison['accepted']):
        raise ValueError('Mixed history mechanism')
    for r in comparison['accepted']:
        if r['status'] == 'never_treated' and not all(
            r.get('absence_basis', {}).get(k) is True for k in (
                'official_code_history_no_earlier_enactment',
                'no_earlier_in_compilations_on_disk', 'no_search_log_contradiction')):
            raise ValueError('V9 absence basis incomplete')
    comparison, audit, conflicts = corrected_comparison(comparison, answer['checks'], c['sample'])
    scope = conflict_scope(cfg, conflicts)
    # Full-history metadata equality is not binary agreement: source IDs and
    # narrative differ. Explicit interruption/history uncertainty remains data.
    uncertain = set(r['unit'] for r in comparison['accepted']
                    if r.get('history_uncertain') or r.get('observed_interruptions'))
    for row in history['records']:
        for original_row in row.get('original_rows', {}).values():
            if original_row.get('observed_interruptions') or original_row.get('history_uncertain'):
                uncertain.add(row['unit'])
    for p in (job.job_path, Path(job.job_path).with_suffix('.job.json'),
              job.output_path, job.promote_path, Path(job.output_dir) / 'COLLECTION.json'):
        evidence.append(binding(p))
    for rel in RULINGS:
        evidence.append(binding(repo / rel))
    evidence.append(binding(Path(__file__)))
    return dict(schema='official_history_v9_certificate_v1', design_id=job.design_id,
        comparison=comparison, audit=audit, sample=c['sample'], conflicts=conflicts,
        **scope,
        uncertain_history_units=sorted(uncertain),
        original_full_history_certification=history['full_history_certification'],
        certification_basis='V9 independent dated tuples, V3 fixed sample, C11 operation clock, V4 scoped exclusions',
        evidence=sorted({i['path']: i for i in evidence}.values(), key=lambda i:i['path']),
        execution_authorized=False, outcome_association_calls=0)


def run(ctx):
    cfg = ctx.card.payload['treatment']
    value = derive(cfg, ctx.card.payload, ctx.repo)
    scope = conflict_scope(cfg, value['conflicts'])
    value = dict(value, **scope)
    certificate = ensure_once(ctx.design_dir / 'CERTIFIED_HISTORY.json', value)
    if scope['retained_conflict_states'] or not value['audit']['passed']:
        return StageResult('S1', ctx.card.design_id, Disposition.PARKED,
            'Known history conflicts: ' + ', '.join(scope['retained_conflict_states']) if scope['retained_conflict_states'] else 'V3 audit failed',
            'Scoped third read of binary/operation conflicts; preserve blind originals',
            (artifact(certificate, 'history_certificate'),), value['audit'])
    excluded = set(cfg['excluded_units'])
    unknown = {g['unit'] for g in value['comparison']['gaps']}
    if not unknown | set(value['uncertain_history_units']) <= excluded:
        raise ValueError('Unknown/interrupted histories must be excluded from both arms')
    unresolved = sorted(r['unit'] for r in value['comparison']['accepted'] if r.get('audit_unresolved'))
    required = dict(drop_uncertain_history_states=value['uncertain_history_units'],
                    drop_unresolved_audit_dates=unresolved)
    variants = {s['name']: s for s in ctx.card.payload['sensitivities']}
    for name, units in required.items():
        if units and not set(units) <= set(variants.get(name, {}).get('changes', {}).get('treatment', {}).get('excluded_units', [])):
            raise ValueError('Missing registered V4 sensitivity: ' + name)
    records = engine_records(value['comparison']['accepted'], excluded)
    treatment = ensure_once(ctx.design_dir / 'TREATMENT.json', dict(
        records=records, all_records=value['comparison']['accepted'], audit=value['audit'],
        sample=value['sample'], source_sha256=sha(certificate),
        excluded_conflict_states=scope['excluded_conflict_states'],
        excluded_primary_units=sorted(excluded), required_sensitivities=required,
        transition_year_drop=ctx.card.payload.get('transition_year_drop', False), execution_authorized=False))
    proof = ensure_once(ctx.design_dir / 'S1_IMPORT_PROVENANCE.json', dict(
        evidence=value['evidence'], v3_passed=True, source_type=SOURCE_TYPE))
    return StageResult('S1', ctx.card.design_id, Disposition.READY,
        'V9 reconciled operation history and fresh V3 passed',
        artifacts=tuple(artifact(p, p.stem.lower()) for p in (certificate, treatment, proof)),
        metrics=value['audit'])


def engine_records(rows, excluded):
    """Keep the audited first-full clock distinct from engine operation labels.

    The annual transition adapter drops the operation year and shifts once.
    Exact January-1 activation needs its own calendar mask; it is never silently
    shifted or inferred from an enactment-year-only record here.
    """
    records = deepcopy([r for r in rows if r['unit'] not in excluded])
    from .. import prewindow
    prewindow.forbid(records, 'engine records (a pre_window_bound state must be excluded from both arms)')
    for row in records:
        if row['status'] in {'treated','not_yet_treated'}:
            first_full = row['start_period']
            operation = row.get('best_evidence_start_year')
            if operation != first_full - 1:
                raise ValueError('Retained annual operation/first-full clock needs a located calendar mask: ' + row['unit'])
            timing = row.get('timing', {})
            for owner in (row, timing):
                for key in ('operational_activation_date', 'operative_date', 'implementation_date',
                            'operational_implementation'):
                    value = owner.get(key)
                    if isinstance(value, dict):
                        year = value.get('year')
                        value = value.get('date', year)
                    if isinstance(value, int):
                        year = value
                    elif isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
                        year = int(value[:4])
                    else:
                        continue  # Narrative cannot manufacture an operation date.
                    if year > operation:
                        raise ValueError('Later operation contradicts annual clock: ' + row['unit'])
            row.update(audited_first_full_start_period=first_full,
                       start_period=operation, engine_clock='operation_year_drop_then_shift_once')
            row.update(date_fields(row))
    return records


def _iso(value):
    """ISO calendar date (YYYY-MM-DD) or None; a year-only or narrative value is never turned into a day."""
    from datetime import date
    if isinstance(value, str) and len(value) == 10:
        try: return date.fromisoformat(value).isoformat()
        except ValueError: return None
    return None


def date_fields(row):
    """N12 request (2026-10-17): `enactment_date`, `effective_date` (ISO) and `date_precision` on every treated record.

    Copies what the history coder already recorded (`enactment_date`, `operational_implementation_date`, `operational_date_precision`);
    nothing is searched or inferred, and a value that is not a full ISO date stays null."""
    effective = _iso(row.get('operational_implementation_date'))
    precision = row.get('operational_date_precision')
    return dict(enactment_date=_iso(row.get('enactment_date')), effective_date=effective,
                date_precision=precision if effective and precision in {'day', 'month', 'year'} else None)


def main(argv=None):
    """Install this explicit input route without changing archived S1 code bytes."""
    from .. import hub
    from ..stages import s1_treatment
    previous = s1_treatment.run

    def route(ctx):
        if ctx.card.payload.get('treatment', {}).get('source_type') == SOURCE_TYPE:
            return run(ctx)
        return previous(ctx)

    s1_treatment.run = route
    try:
        return hub.main(argv)
    finally:
        s1_treatment.run = previous


if __name__ == '__main__':
    raise SystemExit(main())
