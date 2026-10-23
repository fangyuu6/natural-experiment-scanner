#!/usr/bin/env python3
"""Post-run display decisions around the unchanged, hash-bound case generator."""
from __future__ import annotations

from copy import deepcopy
import argparse
import json
import os
from pathlib import Path
import sys
from types import FunctionType

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.did_samples.cases import case_sidecar as frozen

ADDENDA = ROOT / 'scripts/viz_site/data/case_label_addenda.json'
ROW_GUARD = 'Objective Other estimates rule exceeds eight rows; arbiter decision required'
RULE = ('For full-result policies, select unrounded outcome-matched interval zero-inclusion flips symmetrically; '
        'merge the confirmed NY LaValle aliases while retaining IDs; hard-fail above eight rows. Keep the frozen substantive row selection and omission ranges; display only '
        'placebos whose row ID is exactly <headline_id>/placebo. Reapply the frozen '
        'maximum of eight Other estimates rows. Supply missing benchmark_unit words '
        'only from the post-run label addendum; retain all original label bindings '
        'and every row in the repository result JSONs. Describe failed '
        'treatment_two_source_dates audits from the recorded inconsistent audit-label '
        'jurisdictions and their policy statuses (using the bound treatment table '
        'when absent in the failed details); preserve unrelated stopping reasons. Apply explicitly '
        'allowlisted post-run display words without replacing frozen labels or '
        'changing numerical fields. Preserve computed stopped-case primary results '
        'and their stopping reasons for compact page display.')
DISPLAY_WORD_FIELDS = {'policy', 'source_caption', 'treatment', 'estimand_line'}
AUDIT_ISSUE = 'mandatory comparison audit incomplete or failed'
REASON_IDS = {'pretrend_joint', 'pretrend_economic', 'placebo', 'calibration_integrity',
              'registered_inventory', 'measured_primary_diagnostics',
              'computed_comparison_audits', 'execution_error', 'interrupted'}


def _with_globals(function, **overrides):
    """Use frozen code with isolated globals; never mutate the imported module."""
    return FunctionType(function.__code__, dict(function.__globals__, **overrides),
                        function.__name__, function.__defaults__, function.__closure__)


def select_other_rows(registry, headline_ids, primary_ids=(), *, interval_flips=True):
    # The frozen selector guards before returning its diagnostics. Defer exactly
    # that guard, without disabling any of its other validations.
    def defer_row_guard(condition, reason):
        if reason != ROW_GUARD:
            frozen.require(condition, reason)

    selector = _with_globals(frozen.select_other_rows, require=defer_row_guard)
    selected, diagnostics = selector(registry, headline_ids, primary_ids)
    own_placebos = {identity + '/placebo' for identity in headline_ids}
    diagnostics = [row for row in diagnostics if row['kind'] != 'placebo'
                   or len(row['ids']) == 1 and row['ids'][0] in own_placebos]
    required = {identity for ids in selected for identity in ids}
    for identity, row in registry.items():
        if not interval_flips or identity in headline_ids or frozen.role(identity, row) in (
                'omission', 'omit_one_jurisdiction', 'omit_one_cohort', 'placebo'):
            continue
        if not frozen.number(frozen.overall(row).get('att')):
            continue
        for headline in headline_ids:
            main = registry[headline]
            if frozen.outcome_key(row) != frozen.outcome_key(main):
                continue
            if not frozen.number(frozen.overall(main).get('att')):
                continue
            # Unrounded, symmetric, outcome-specific point intervals.
            low, high = frozen.interval(row)
            ml, mh = frozen.interval(main)
            for candidate in (row, main):
                info = candidate.get('interval_metadata', {})
                frozen.require(info.get('method') in ('unit Gaussian multiplier', 'unit multiplier bootstrap') and
                               frozen.number(info.get('nominal_level')) and 0 < info['nominal_level'] <= 1,
                               'Missing actual interval method/level: ' + identity)
            if (low <= 0 <= high) != (ml <= 0 <= mh):
                required.add(identity)
    merged = []
    for identity, row in registry.items():
        if identity not in required:
            continue
        if any(display_same_scalar(row, registry[h]) for h in headline_ids):
            continue
        aliases = next((ids for ids in merged
                        if display_same_scalar(row, registry[ids[0]])), None)
        if aliases is None:
            merged.append([identity])
        else:
            aliases.append(identity)
    count = len(merged) + len(diagnostics)
    frozen.require(count <= 8, ROW_GUARD +
                   f' (after display filter: {len(merged)} estimates + '
                   f'{len(diagnostics)} diagnostics = {count})')
    return merged, diagnostics


def _numeric_leaves(value, path=()):
    """Retain every unrounded numerical leaf when confirming executed aliases."""
    if isinstance(value, dict):
        return {key: number for name, item in value.items()
                for key, number in _numeric_leaves(item, path + (name,)).items()}
    if isinstance(value, list):
        return {key: number for index, item in enumerate(value)
                for key, number in _numeric_leaves(item, path + (index,)).items()}
    return {path: value} if isinstance(value, (int, float)) else {}


def display_same_scalar(left, right):
    """Ignore rationale only for the confirmed NY LaValle executed aliases."""
    configurations = [deepcopy(frozen.substantive(row)) for row in (left, right)]
    identities = {row.get('id') for row in (left, right)}
    if identities == {'w1996_2019/y/never_treated/include_with_source_date',
                      'w1996_2019/y/never_treated/ny_lavalle_clock'}:
        for config in configurations:
            if isinstance(config.get('exposure_clock'), dict):
                config['exposure_clock'].pop('reason', None)
    return (configurations[0] == configurations[1] and
            frozen.overall(left) == frozen.overall(right) and
            left.get('interval_metadata') == right.get('interval_metadata') and
            _numeric_leaves(left) == _numeric_leaves(right))


def merge_labels(labels, addenda):
    """Only the approved unit phrase may fill an absent top-level label key."""
    frozen.require(addenda.get('version') == 1, 'Unsupported label addendum version')
    merged = dict(labels)
    entries = addenda.get('cases', {})
    frozen.require(isinstance(entries, dict), 'Invalid label addendum cases')
    for entry in entries.values():
        frozen.require(isinstance(entry, dict) and set(entry) <= {'benchmark_unit', 'display_words', 'display_outcomes', 'display_benchmark'},
                       'Label addendum may supply allowlisted display words only')
        if 'benchmark_unit' in entry:
            frozen.words(entry['benchmark_unit'], 'addendum.benchmark_unit')
        benchmarks = entry.get('display_benchmark', {})
        frozen.require(isinstance(benchmarks, dict), 'Display benchmark may supply words only')
        for column, value in benchmarks.items():
            frozen.require(isinstance(column, str), 'Display benchmark column must be a name')
            frozen.words(value, 'addendum.display_benchmark.' + column)
        outcomes = entry.get('display_outcomes', {})
        frozen.require(isinstance(outcomes, dict), 'Display outcomes must supply words only')
        for column, fields in outcomes.items():
            frozen.require(isinstance(column, str) and isinstance(fields, dict) and
                           set(fields) <= {'outcome', 'population'}, 'Display outcomes may supply words only')
            for key, value in fields.items():
                frozen.words(value, 'addendum.display_outcomes.' + column + '.' + key)
        display = entry.get('display_words', {})
        frozen.require(isinstance(display, dict) and set(display) <= DISPLAY_WORD_FIELDS,
                       'Label addendum may supply allowlisted display words only')
        for key, value in display.items():
            frozen.words(value, 'addendum.display_words.' + key)
    for key, value in entries.get(labels['case_id'], {}).items():
        if key == 'benchmark_unit' and key not in merged:
            merged[key] = dict(value)
    return merged


def apply_display_words(sidecar, addenda):
    """Copy explicitly reviewed prose into the derived projection only."""
    for key, value in addenda.get('cases', {}).get(sidecar['case_id'], {}).get('display_words', {}).items():
        frozen.require(key in DISPLAY_WORD_FIELDS, 'Display override is not a prose field')
        sidecar[key] = dict(frozen.words(value, 'addendum.display_words.' + key))
    overrides = addenda.get('cases', {}).get(sidecar['case_id'], {}).get('display_outcomes', {})
    for row in list(sidecar['primary_estimates'].values()) + sidecar.get('other_estimates', []) + list(sidecar.get('registered_results', {}).values()):
        column = row.get('outcome_column')
        if column in overrides:
            for key, value in overrides[column].items():
                row[key] = dict(frozen.words(value, 'display_outcomes.' + column + '.' + key))
    benchmarks = addenda.get('cases', {}).get(sidecar['case_id'], {}).get('display_benchmark', {})
    for row in sidecar['primary_estimates'].values():
        if row.get('outcome_column') in benchmarks:
            row['comparison_line'] = dict(benchmarks[row['outcome_column']])
    return sidecar


def display_stop_reasons(result, plain_reasons, *, treatment=None):
    """Explain a recorded data-audit failure without implying an execution error."""
    if result.get('status') != 'INTERNAL_ONLY' or not any(
            gate.get('gate') == 'computed_comparison_audits' and gate.get('pass') is False
            for gate in result.get('gate_table', [])):
        return plain_reasons
    rows = dict(result.get('primary_estimates', {}), **result.get('registered_results', {}))
    issues = result.get('stop_issues', [])
    audit_ids = [issue.split(':', 1)[0] for issue in issues
                 if issue.partition(':')[2].strip() == AUDIT_ISSUE]
    audit_rows = [rows.get(identity, {}) for identity in audit_ids] if audit_ids else list(rows.values())
    missing_units, statuses, other_audit_failure = set(), {}, False
    for row in audit_rows:
        audit = row.get('evidence', {}).get('replication_audit', {})
        failed = [check for check in audit.get('checks', []) if check.get('pass') is False]
        if not failed:
            other_audit_failure |= bool(audit_ids) or audit.get('complete') is False
        for check in failed:
            if check.get('check') != 'treatment_two_source_dates':
                other_audit_failure = True
                continue
            for detail in check.get('details', {}).get('checks', []):
                if (detail.get('pass') is False and detail.get('unit')
                        and not detail.get('source1_date') and not detail.get('source2_date')):
                    missing_units.add(detail['unit'])
                    statuses.setdefault(detail['unit'], set()).add(
                        detail.get('policy_status') or
                        (treatment or {}).get(detail['unit'], {}).get('policy_status'))
    if not missing_units:
        return plain_reasons
    count = len(missing_units)
    noun = 'jurisdiction' if count == 1 else 'jurisdictions'
    zh_noun = '辖区'
    if all(statuses[unit] == {'never_treated'} for unit in missing_units):
        noun = 'never-treated comparison state' if count == 1 else 'never-treated comparison states'
        zh_noun = '始终未处理的对照州'
    reason = frozen.pair(
        f'Source dates missing for {count} {noun}.',
        f'{count} 个{zh_noun}缺少来源日期。')
    inconsistent = all(statuses[unit] == {'never_treated'} for unit in missing_units) and all(
        detail.get('verification') == 'verified_two_sources' or
        detail.get('date_verification_status') == 'verified_two_sources'
        for row in audit_rows
        for check in row.get('evidence', {}).get('replication_audit', {}).get('checks', [])
        if check.get('check') == 'treatment_two_source_dates'
        for detail in check.get('details', {}).get('checks', [])
        if detail.get('unit') in missing_units and detail.get('pass') is False)
    if inconsistent:
        reason = frozen.pair(f'Date-audit labels were inconsistent for {count} {noun}.',
                             f'{count} 个始终未处理对照州的日期核验标签与记录不一致。')
    replacements = [frozen.pair('The published comparison audit was incomplete or failed.',
                                '已发表估计的比较审计未完成或未通过。')] if not other_audit_failure else []
    # Frozen inference treats an unrecognized row-prefixed audit issue as an
    # execution error. Keep genuine errors, including explicitly declared ones.
    inferred_errors = [issue for issue in issues if issue.split(':', 1)[0] not in REASON_IDS
                       and 'interrupted' not in issue]
    if (result.get('stop_reasons') is None and inferred_errors
            and not any(issue.split(':', 1)[0] == 'execution_error' for issue in issues)
            and all(issue.partition(':')[2].strip() == AUDIT_ISSUE for issue in inferred_errors)):
        replacements.append(frozen.pair('Execution stopped after an error.', '执行因错误而停止。'))
    return [reason] + [item for item in plain_reasons if item not in replacements]


from datetime import date
from scripts.did_samples.cases import case_validate as display_validate


def primary_comparison_block(case, result, *, treatment):
    """Derive controls from bound structure and dates; never read raw outcomes."""
    structure = (result.get('calibration') or {}).get('structure') or {}
    adoption = structure.get('adoption')
    frozen.require(isinstance(adoption, dict) and adoption,
                   'Missing bound primary structural adoption inventory')
    frozen.require(isinstance(treatment, dict) and treatment,
                   'Missing bound treatment table for primary comparison count')
    basis = frozen.time_basis(case)
    units = {u: g for u, g in adoption.items() if frozen.included_unit(case, u)}
    frozen.require(all(g is None or type(g) is int for g in units.values()),
                   'Invalid structural adoption inventory')
    identities = []
    first_projection = None
    for identity, primary in result.get('primary_estimates', {}).items():
        periods = primary.get('periods', primary.get('calendar_years'))
        frozen.require(isinstance(periods, list) and periods,
                       'Missing actual primary window: ' + identity)
        start, end = (frozen.period_index(p, basis) for p in (periods[0], periods[-1]))
        window_start = display_validate.period_start(start, basis)
        window_end = display_validate.period_start(end + 1, basis)
        control_rule = primary.get('control_group')
        frozen.require(control_rule == 'never_treated',
                       'Primary comparison derivation needs whole-window control rule')
        clean = []
        for unit, cohort in units.items():
            frozen.require(unit in treatment, 'Structural unit absent from treatment table: ' + unit)
            declared = treatment[unit]
            status = declared.get('policy_status')
            # Cohorts are already normalised indices in the calibration structure.
            if cohort is not None and cohort <= end:
                continue
            if status == 'never_treated':
                frozen.require(cohort is None, 'Never-treated/cohort contradiction: ' + unit)
                clean.append(unit)
                continue
            frozen.require(status == 'treated', 'Included comparison status is unresolved: ' + unit)
            raw_date = declared.get('effective_date')
            frozen.require(isinstance(raw_date, str) and raw_date,
                           'Known future adopter lacks operative date: ' + unit)
            clock = primary.get('exposure_clock') or case['exposure_clock']
            low, high = display_validate.clock_date_bounds(
                raw_date, declared.get('date_precision'), clock)
            history = (primary.get('exposure_history') or case.get('exposure_history') or {}).get(unit)
            def has_exposure(onset):
                intervals = history or [dict(start=onset.isoformat(), end=None)]
                return any(max(window_start, onset, date.fromisoformat(part['start'])) <
                           min(window_end, date.fromisoformat(part['end']) if part['end'] else window_end)
                           for part in intervals)
            low_exposed, high_exposed = has_exposure(low), has_exposure(high)
            frozen.require(low_exposed == high_exposed,
                           'Date precision leaves whole-window comparison eligibility unresolved: ' + unit)
            if not low_exposed:
                clean.append(unit)
        clean.sort()
        cells = [cell for cell in primary.get('result', {}).get('group_time', []) if cell.get('post')]
        frozen.require(cells and all(type(cell.get('n_control')) is int and
                                     cell['n_control'] == len(clean) for cell in cells),
                       'Primary comparison inventory disagrees with actual post cells: ' + identity)
        projection = (tuple(clean), tuple(periods), control_rule)
        frozen.require(first_projection is None or first_projection == projection,
                       'Displayed primary outcomes have different comparison pools/windows')
        first_projection = projection
        identities.append(identity)
    frozen.require(first_projection is not None, 'No bound primary comparison record')
    clean, periods, control_rule = first_projection
    return dict(primary_comparison_jurisdictions=len(clean),
                primary_comparison_metadata=dict(
                    source='bound_calibration_structure_and_treatment_table',
                    control_rule=control_rule,
                    eligibility='unexposed_throughout_primary_window_including_partial_periods',
                    primary_ids=identities, jurisdictions=list(clean),
                    periods=list(periods), time_basis=basis))



def build_sidecar(case, result, labels, *, treatment=None):
    def selector(registry, headline_ids, primary_ids=()):
        return select_other_rows(registry, headline_ids, primary_ids,
                                 interval_flips=result.get('status') == 'PUBLISHABLE_EXAMPLE')
    builder = _with_globals(frozen.build_sidecar, select_other_rows=selector)
    # Keep the frozen stopped-case design projection unchanged. Status metadata
    # supplies explanatory words only; it must not fill numerical design fields.
    sidecar = builder(case, result, labels, treatment=(
        treatment if result.get('status') == 'PUBLISHABLE_EXAMPLE' else None))
    for row in list(sidecar['primary_estimates'].values()) + sidecar.get('other_estimates', []) + list(sidecar.get('registered_results', {}).values()):
        source = result.get('registered_results', {}).get(row.get('id') or row.get('headline_id'))
        if source and case.get('data_kind') == 'real':
            row['outcome_column'] = frozen.outcome_key(source)
            row['comparison_tier'] = (source.get('comparison') or {}).get('tier')
            row['estimator'] = "Callaway–Sant'Anna" if source.get('result', {}).get('group_time') else None
    if case.get('data_kind') == 'real':
        sidecar['design'].update(primary_comparison_block(case, result, treatment=treatment))
    if frozen.time_basis(case).get('kind') == 'quarter' and case.get('data_kind') == 'real':
        # The documented QWI stable-new-hire observation quarter is hire + 1.
        if case['case_id'] == 'salary_history_ban':
            basis = frozen.time_basis(case)
            primary = next(iter(sidecar['primary_estimates'].values()))
            for name, period in (('reference_first_period', primary['periods'][0]),
                                 ('reference_last_period', primary['periods'][-1])):
                index = frozen.period_index(period, basis) + 1
                code = (index // 4) * 10 + index % 4 + 1
                sidecar['design'][name] = frozen.pair(*(frozen.period_label(code, basis, lang) for lang in ('en', 'zh')))
    coverage = (result.get('calibration') or {}).get('coverage')
    if coverage:
        primary = list(sidecar['primary_estimates'].values())
        point = deepcopy(coverage)
        point.update(window=deepcopy(case['windows']['primary']),
                     periods=list(primary[0]['periods']),
                     outcome_columns=[frozen.outcome_key(result['registered_results'][row['id']]) for row in primary],
                     scope='pooled_declared_main_outcome_point_intervals')
        sidecar['checks']['calibration']['point_interval_coverage'] = point
    if case.get('full_exposure_min_days') is not None:
        sidecar['design']['full_exposure_min_days'] = case['full_exposure_min_days']
    sidecar['stop_reasons'] = display_stop_reasons(result, sidecar['stop_reasons'], treatment=treatment)
    return sidecar


def generate_sidecar(case_path, result_path, labels_path, *, root=ROOT,
                     output=None, addendum_path=ADDENDA, replace=False):
    """Mirror frozen generate_sidecar's validations, provenance and exclusive write."""
    root = Path(root).resolve()
    case_path, result_path, labels_path = (Path(p).resolve() for p in (case_path, result_path, labels_path))
    frozen.require(all(p.is_relative_to(root) for p in (case_path, result_path, labels_path)),
                   'Inputs must be repository-relative')
    case, result, labels = (json.loads(p.read_text(encoding='utf-8')) for p in (case_path, result_path, labels_path))
    relative = str(labels_path.relative_to(root))
    frozen.require(case.get('declaration_sha256') == frozen.declaration_hash(case), 'Case declaration hash mismatch')
    frozen.require(case.get('hashes', {}).get(relative) == frozen.digest(labels_path), 'Labels are not declared/hash-bound')
    frozen.require(result.get('input_hashes', {}).get('case_json') == frozen.digest(case_path), 'Result case.json binding mismatch')
    frozen.require(result.get('input_hashes', {}).get('spec') == case['spec']['sha256'], 'Result spec binding mismatch')
    frozen.require(frozen.digest(frozen.local_path(root, case['spec']['path'])) == case['spec']['sha256'], 'Spec hash mismatch')
    treatment = None
    if 'treatment' in case:
        declaration = case['treatment']
        path = frozen.local_path(root, declaration['path'])
        frozen.require(case.get('hashes', {}).get(declaration['path']) == frozen.digest(path), 'Treatment hash mismatch')
        mapping = declaration['columns']
        logical = ('policy_status', 'effective_date', 'date_precision')
        unit = mapping[declaration['unit_column']]
        treatment = {r[unit]: {name: r[mapping[name]] for name in logical}
                     for r in frozen.project_csv(path, [unit] + [mapping[name] for name in logical])}
    addendum_path = Path(addendum_path).resolve()
    frozen.require(addendum_path.is_relative_to(root), 'Addendum must be repository-relative')
    addenda = json.loads(addendum_path.read_text(encoding='utf-8'))
    labels = merge_labels(labels, addenda)
    sidecar = apply_display_words(build_sidecar(case, result, labels, treatment=treatment), addenda)
    sidecar['provenance'] = dict(case_json_sha256=frozen.digest(case_path), results_sha256=frozen.digest(result_path),
                                 labels_path=relative, labels_sha256=frozen.digest(labels_path),
                                 generator_sha256=frozen.digest(frozen.__file__),
                                 authority='Words are declared; all numerical display values derive from the bound declaration and results.')
    sidecar['provenance']['display_wrapper'] = dict(
        wrapper_sha256=frozen.digest(__file__), addendum_sha256=frozen.digest(addendum_path),
        addendum_path=str(addendum_path.relative_to(root)), rule=RULE)
    target = Path(output) if output else result_path.with_name(case['case_id'] + '_site.json')
    frozen.require(target.resolve().is_relative_to(root), 'Output escapes repository')
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(sidecar, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
    if replace:
        frozen.require(target.resolve() == result_path.with_name(case['case_id'] + '_site.json'),
                       'Replacement is limited to the derived case sidecar')
        temporary = target.with_suffix('.json.regenerated')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                stream.write(encoded)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
    else:
        with target.open('x', encoding='utf-8') as stream:
            stream.write(encoded)
    return sidecar


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case_json', type=Path)
    parser.add_argument('results_json', type=Path)
    parser.add_argument('--labels', required=True, type=Path)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--replace', action='store_true', help='Regenerate the derived <case_id>_site.json atomically')
    args = parser.parse_args()
    try:
        record = generate_sidecar(args.case_json, args.results_json, args.labels,
                                  root=args.root, output=args.output, replace=args.replace)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps(dict(generated=False, error=str(exc)), ensure_ascii=False))
        return 1
    print(json.dumps(dict(generated=True, case_id=record['case_id'],
                         other_rows=len(record['other_estimates']), panel_cells_read=0)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
