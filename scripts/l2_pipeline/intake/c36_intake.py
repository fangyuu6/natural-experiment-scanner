"""V10 codebook-locked threshold intake; no outcome data or model dispatch in build.

Existing C35, source adapters and sealed code remain byte-identical. Binary
derived sources keep original numeric policy cells in a separate lineage file.
"""
from __future__ import annotations

import argparse
import collections
import copy
import csv
import json
import re
from pathlib import Path

from . import build_intake as base, c35_intake as c35, v8b_calendar as audit
from .config import IntakeConfig, REPO
from scripts.l2_pipeline.connectors import outcome_v7
from scripts.l2_pipeline.sources.research_panel.core import history, read_panel, write_panel
from scripts.l2_pipeline.storage import ensure_once, sha

ROOT = REPO / 'data/l2_pipeline/sources/research_panel/c36_20261010'
SOURCE_CATALOG = ROOT.parent / 'CATALOG.json'
COHORT = ROOT.parent / 'c18_20261010/INTAKE.json'
SCREEN = ROOT.parent / 'v8b_20261009/ELIGIBILITY_EVIDENCE.json'
RULING = REPO / 'docs/plans/l2-pipeline/HUB_RULING_V10_THRESHOLDS.md'
CONFIG = IntakeConfig(ROOT, 361010, 362010, 'c36')


def read(path):
    return json.loads(Path(path).read_text())


def bind(path):
    return dict(path=str(Path(path).resolve()), sha256=sha(Path(path)))


def check(condition, message):
    if not condition:
        raise ValueError(message)


def classify():
    """Lock declarations before any policy-panel cells are opened."""
    catalog = read(SOURCE_CATALOG)
    cohort = {r['policy_id'] for r in read(COHORT)['rows']
              if r['in_scope'] and r['disposition'] == 'PARKED_PREDECLARED_THRESHOLD_REQUIRED'}
    check(len(cohort) == 87, 'C36 requires the exact 87 threshold-pending identities')
    review = read(ROOT / 'CODEBOOK_REVIEW.json')
    rows = [copy.deepcopy(r) for r in review['recommendations'] if r['policy_id'] in cohort]
    check(len(rows) == 87 and len({r['policy_id'] for r in rows}) == 87, 'Codebook review identity')
    variables = {v['policy_id']: v for v in catalog['variables']}
    for row in rows:
        v = variables[row['policy_id']]
        check(v['kind'] != 'binary_adoption', 'Already binary policy in C36')
        check(row['class'] in {'a', 'b', 'c'} and row['quote'] and row['locator'] and row['reason'], 'Missing classification evidence')
        if row['class'] == 'a':
            check(row['minimum'] == 0 and '-1 =' not in row['quote'], 'Minimum is an existing policy')
            check(' '.join(row['quote'].split()) == ' '.join(v['definition'].split()), 'Codebook quote differs from catalog definition')
            codes = sorted({float(x) for x in re.findall(r'(?<![\d.])(-?\d+(?:\.\d+)?)\s*=', row['quote'])})
            check(codes and min(codes) == 0, 'Codebook minimum not declared')
            row.update(operator='>', threshold=0, outcome_associations_examined=False,
                       declared_at='2026-10-10', intensity_changes_ignored=True,
                       allowed_source_codes=codes,
                       rule='V10 any version: active iff original policy value > documented no-policy minimum')
            ensure_once(ROOT / 'thresholds' / (row['policy_id'].replace(':', '_') + '.json'), row)
        elif row['class'] == 'b':
            check(row.get('statute_quote') and row.get('operator') in {'>=', '<='}, 'Statute-defined cutoff missing')
            ensure_once(ROOT / 'thresholds' / (row['policy_id'].replace(':', '_') + '.json'), row)
    output = dict(schema_version='v10_threshold_declarations_v1', rows=rows,
                  counts={k: sum(r['class'] == k for r in rows) for k in ('a', 'b', 'c')},
                  input_bindings=[bind(p) for p in (SOURCE_CATALOG, COHORT, RULING, ROOT / 'CODEBOOK_REVIEW.json')],
                  selection_uses_policy_distribution=False, selection_uses_outcomes=False,
                  model_calls=0, outcome_association_calls=0)
    ensure_once(ROOT / 'THRESHOLD_CLASSIFICATION.json', output)
    return output


def binary_value(value, rule):
    if value is None:
        return None
    if rule['class'] == 'a':
        check(value >= rule['minimum'], 'Policy cell below codebook-defined minimum')
        if rule.get('allowed_source_codes') and value not in rule['allowed_source_codes']:
            return None
        return int(value > rule['minimum'])
    return int(value >= rule['threshold'] if rule['operator'] == '>=' else value <= rule['threshold'])


def derive_sources(classification):
    """Materialize only policy cells, never workbook Data or outcome artifacts."""
    source = read(SOURCE_CATALOG)
    rules = {r['policy_id']: r for r in classification['rows'] if r['class'] in {'a', 'b'}}
    variables = []
    for original in source['variables']:
        if original['policy_id'] not in rules:
            continue
        v = copy.deepcopy(original)
        rule = rules[v['policy_id']]
        declaration = ROOT / 'thresholds' / (v['policy_id'].replace(':', '_') + '.json')
        v.update(kind='binary_adoption', original_kind=original['kind'], original_unit=original['unit'],
                 unit='indicator (V10 any-version adoption)', original_definition=original['definition'],
                 definition=original['definition'] + ' | V10: any version, original value > 0.' if rule['class'] == 'a'
                 else original['definition'] + ' | V10 statute cutoff: ' + str(rule['threshold']),
                 threshold_declaration=bind(declaration), legal_certification_granted=False)
        variables.append(v)
    rows, originals, conflicts = [], [], collections.Counter()
    for panel in source['panels']:
        wanted = set(panel['policy_ids']) & set(rules)
        if not wanted:
            continue
        path = Path(panel['path'])
        check(sha(path) == panel['sha256'], 'Original policy source hash changed')
        for row in read_panel(path):
            if row['policy_id'] not in wanted:
                continue
            originals.append(row)
            active = binary_value(row['value'], rules[row['policy_id']])
            conflict = row['value'] is not None and active is None
            conflicts[row['policy_id']] += int(conflict)
            rows.append(dict(row, value=active, status='source_encoding_conflict' if conflict else row['status']))
    path = ROOT / 'panels/v10_binary.csv'
    if (ROOT / 'CATALOG_V10.json').exists():
        derived = read(ROOT / 'CATALOG_V10.json')
        check(read_panel(path) == rows and read_panel(ROOT / 'panels/original_policy_cells.csv') == originals,
              'Derived source is immutable')
        return derived, rows
    write_panel(path, rows)
    write_panel(ROOT / 'panels/original_policy_cells.csv', originals)
    derived = dict(schema_version='c36_v10_derived_catalog_v1', variables=variables, files=source['files'],
                   panels=[dict(adapter='v10_binary', path=str(path), sha256=sha(path),
                                size_bytes=path.stat().st_size, policy_ids=sorted(rules), canonical_rows=len(rows))],
                   source_catalog=bind(SOURCE_CATALOG), classification=bind(ROOT / 'THRESHOLD_CLASSIFICATION.json'),
                   original_policy_cells=bind(ROOT / 'panels/original_policy_cells.csv'),
                   source_encoding_conflicts=dict(conflicts),
                   classification_locked_before_policy_cells=True, outcome_association_calls=0)
    ensure_once(ROOT / 'CATALOG_V10.json', derived)
    return derived, rows


def series_mapping(family, target, sid, **extra):
    s = c35.CATALOG_DATA['series'][sid]
    m = dict(family=family, target=target, connector=s['registered_connector'], series_id=sid,
             connector_exists=True, years=s['years'], population=s['population'],
             population_filters=s.get('population_filters', {}), source_url=s['source_url'], free_public=True,
             license=s['license'], outcome_scale='rate', frequency=s['frequency'][0],
             purpose_status='PENDING_V62_S0', local_support_certified=False,
             catalog_path=str(outcome_v7.CATALOG_PATH.relative_to(REPO)), catalog_sha256=sha(outcome_v7.CATALOG_PATH))
    for key in ('paths', 'source_sha256', 'units', 'analysis_only', 'republication_prohibited'):
        if key in s:
            m['outcome_unit' if key == 'units' else key] = s[key]
    if s.get('support_artifact'):
        m.update(support_path=s['support_artifact']['path'], support_sha256=s['support_artifact']['sha256'])
    m.update(extra)
    return m


def mapping(v):
    n = v['policy_id'].split(':')[1]
    if n.startswith(('guncontrol_', 'w_guncontrol_')):
        return series_mapping('firearms', 'firearm_homicide_rate', 'wonder_firearm_homicide_rate')
    if n == 'regulation_motorcycle_helmets':
        return series_mapping('road_safety', 'motorcyclist_fatality_rate', 'fars_motor_vehicle_occupant_fatality_rate',
                              population_dilution='All motor vehicle occupants include motorcycle occupants; S0 must assess broader population proxy')
    if n == 'w_abortion_parental_notice_1983_2014':
        return series_mapping('reproductive_health', 'minor_birth_rate', 'cdc_teen_birth_rate',
                              population_filters={'age_group': '15-17'}, population='Females age15-17; statutory minor population proxy subject to S0')
    if n == 'boehmke_snrpresc':
        return base.missing('senior_pharmaceuticals', 'eligible_senior_prescription_access', 'state_senior_prescription_access', range(1980, 2025),
                            'https://www.cms.gov/data-research', 'Seniors ineligible for Medicaid; pharmaceutical access/assistance, not generic insurance')
    if n in {'sstmrrge', 'w_gayrights_civilunions_marriage'}:
        return base.missing('family_law', 'affected_group_marriage_or_union_formation', 'state_same_sex_union_formation_rate', range(1990, 2025),
                            'https://www.census.gov/programs-surveys/acs/data.html', 'Same-sex couples; legal recognition and union formation')
    if n in {'w_gayrights_employment_discrimit', 'w_labor_state_ada'}:
        return base.missing('employment_rights', 'protected_group_employment_and_earnings', 'state_protected_group_employment_earnings', range(1979, 2025),
                            'https://www.census.gov/data/datasets/time-series/demo/cps/cps-basic.html', 'Statute-specific sexual-orientation/gender-identity or disabled workers; exact group linkage required')
    if n in {'labor_collective_bargaining_teac', 'w_labor_relations_acts'}:
        return base.missing('labor_relations', 'covered_worker_union_coverage', 'cps_state_covered_sector_union_rate', range(1979, 2025),
                            'https://www.census.gov/data/datasets/time-series/demo/cps/cps-basic.html', 'Covered teachers or workers; bargaining/union coverage')
    if n.startswith('licenses_'):
        return base.missing('occupational_licensing', 'covered_occupation_employment_earnings', 'cps_state_licensed_occupation_employment_earnings', range(1979, 2025),
                            'https://www.census.gov/data/datasets/time-series/demo/cps/cps-basic.html', 'Statute-covered architects or engineers; no total employment substitution')
    if n.startswith('gambling_'):
        return base.missing('gambling', 'lottery_or_casino_participation_receipts', 'state_gambling_participation_receipts', range(1970, 2025),
                            'https://www.census.gov/programs-surveys/state.html', 'State lottery or licensed casinos; licensed gambling receipts per adult')
    if n == 'w_environment_solar_taxcredit':
        return base.missing('solar_energy', 'residential_solar_installation_rate', 'eia_state_residential_solar_installations', range(1990, 2025),
                            'https://www.eia.gov/electricity/data.php', 'Residential solar capacity/installations; exact sector and historical vintages needed')
    if n == 'w_animal_cruelty_felony':
        return base.missing('animal_welfare', 'animal_cruelty_incidence', 'fbi_state_animal_cruelty_rate', range(1990, 2025),
                            'https://cde.ucr.cjis.gov/', 'Aggravated animal cruelty; early historical coverage unverified')
    if n.startswith('w_education_'):
        return base.missing('school_religious_practice', 'covered_school_religious_practice', 'state_school_religious_practice', range(1980, 2025),
                            'https://nces.ed.gov/', 'Permitted/required school display or reflection periods; generic school completion is a different construct', free='UNKNOWN')
    if n == 'genderrights_jury_service':
        return base.missing('jury_access', 'female_jury_participation', 'state_female_jury_participation', range(1900, 2025),
                            'https://www.uscourts.gov/', 'State-court eligible women/jury participation; historical annual state records unverified', free='UNKNOWN')
    if n in {'w_gayrights_public_accomodations', 'w_race_disc_public_accom2', 'w_race_disc_public_accommodation'}:
        return base.missing('public_accommodation_rights', 'protected_group_service_discrimination', 'state_public_accommodation_discrimination', range(1950, 2025),
                            'https://www.justice.gov/crt', 'Covered race/sexual orientation/gender identity service users; complaints and service access', free='UNKNOWN')
    return c35.mapping(v)


def capacity(family, ledger):
    lower_bound = ledger['families'].get(family, {}).get('prior_distinct_design_lower_bound', 0)
    return max(0, CONFIG.family_cap - lower_bound)


def emit(row, v, panel, imported, evidence_path, priors):
    from scripts.l2_pipeline.contracts import DesignCard, StageContext
    from scripts.l2_pipeline.agents.purpose import emit_purpose_verification
    did, m = row['design_id'], row['mapping']
    cfg = dict(source_type='research_panel', policy_id=v['policy_id'], predicate=v['definition'],
               window=row['source_window'], universe=imported['units'], catalog_path=str(ROOT / 'CATALOG_V10.json'),
               catalog_sha256=sha(ROOT / 'CATALOG_V10.json'), panel_path=panel['path'], panel_sha256=panel['sha256'],
               threshold_path=v['threshold_declaration']['path'], threshold_sha256=v['threshold_declaration']['sha256'],
               seed=CONFIG.audit_seed, transition_year_drop=True)
    card = dict(design_id=did, version=ROOT.name, policy_family=m['family'], mechanism=v['definition'], track='DiD', unit='state',
                outcome_family=m['target'], treatment=cfg, transition_year_drop=True,
                population=dict(target=m['population'], filters=m['population_filters']),
                first_order=dict(target_outcome=m['series_id'], target_population=m['population'], evidence_status='pending',
                                 purpose_rule='V6.2', purpose_basis='PENDING', statute_refs=[], literature_refs=[]),
                outcome=dict(connector=m['connector'], series_id=m['series_id'], measure=m['series_id'], measure_id=m['series_id'],
                             frequency=m['frequency'], period_years=row['geometry']['outcome_retained_years'],
                             outcome_scale='rate', population_filters=m['population_filters'], source_validation_required=True),
                intake_status='PROSPECTIVE_UNREGISTERED', execution_authorized=False,
                selection_rule='cumulative_family_cap6_then_screen_policy_id')
    for key in ('paths', 'source_sha256', 'outcome_unit', 'license', 'analysis_only', 'republication_prohibited',
                'catalog_path', 'catalog_sha256', 'support_path', 'support_sha256'):
        if key in m:
            card['outcome'][key] = m[key]
    cp = ensure_once(ROOT / 'cards' / (did + '.json'), card)
    ctx = StageContext(DesignCard.from_mapping(card, cp), ROOT / 'purpose_work', REPO)
    jobs = [emit_purpose_verification(ctx, ROOT / 's0_jobs').job_path]
    ip = ROOT / 'imports' / did / 'PANEL_IMPORT.json'
    source = dict(contract=dict(card_path=str(cp), bound_inputs=[bind(ip)]))
    for kind in ('calibration', 'fresh_audit'):
        jobs.append(str(audit.emit_job(row, source, row['sample_plan'], kind, ROOT,
                                      evidence_path=evidence_path, prior_packets=priors['packets'])))
    return jobs


def build():
    classification = classify()
    derived, policy_rows = derive_sources(classification)
    c35.CATALOG_DATA = outcome_v7.catalog(verify_sources=False)
    outcome_v7.register()
    c35.SUPPORT = {sid: c35.load_support(s) for sid, s in c35.CATALOG_DATA['series'].items() if s['adapter'] == 'c35_artifact'}
    ledger = read(ROOT / 'FAMILY_CAP_LEDGER.json')
    base.configure(CONFIG, catalog=ROOT / 'CATALOG_V10.json', screening_evidence=SCREEN)
    priors = base.prior_packets()
    screening = read(SCREEN)
    passed = {r['policy_id'] for r in screening['rows'] if r['screening_pass']}
    originals = {v['policy_id']: v for v in read(SOURCE_CATALOG)['variables']}
    transformed = {v['policy_id']: v for v in derived['variables']}
    panel = derived['panels'][0]
    rows, imports = [], {}
    for declaration in classification['rows']:
        pid = declaration['policy_id']
        row = dict(policy_id=pid, design_id='c36_' + pid.replace(':', '_'), kind=originals[pid]['kind'],
                   v10_class=declaration['class'], label=originals[pid].get('label', originals[pid]['definition']),
                   in_scope=True, screening_pass=pid in passed, screening_status='PASS' if pid in passed else 'UNAUDITED',
                   emit_eligible=False, codebook_confirmed=False, purpose_accepted=False, s0_accept=False)
        if declaration['class'] == 'c':
            row.update(disposition='EXCLUDED_V10', reason=declaration['reason'])
            rows.append(row)
            continue
        v = transformed[pid]
        row['mapping'] = mapping(v)
        windows = v['coverage']['historical_complete_50_state_windows']
        # The maximal source window is fixed before comparing outcome support.
        if not windows:
            window = [v['coverage']['start_year'], v['coverage']['end_year']]
            row['disposition'] = 'PARKED_NO_COMPLETE_HISTORICAL_SOURCE_WINDOW'
        else:
            window = min(windows, key=lambda w: (-(w[1] - w[0] + 1), w[0]))
        imported = history(policy_rows, v, window)
        ensure_once(ROOT / 'imports' / row['design_id'] / 'PANEL_IMPORT.json', imported)
        imports[pid] = imported
        row.update(source_window=window, import_binding=bind(ROOT / 'imports' / row['design_id'] / 'PANEL_IMPORT.json'),
                   eligibility_basis='V10_direction_requires_disjoint_calibration', legal_operation_clock_certified=False)
        row['geometry'] = c35.supported_geometry(imported, row['mapping'])
        if derived['source_encoding_conflicts'].get(pid, 0):
            row['geometry']['passed'] = False
            row['disposition'] = 'PARKED_SOURCE_ENCODING_CONFLICT'
            row['source_encoding_conflict_cells'] = derived['source_encoding_conflicts'][pid]
        elif not windows:
            row['geometry']['passed'] = False
            row['geometry']['complete_window_required'] = True
        elif not row['geometry']['passed']:
            row['disposition'] = 'PARKED_GEOMETRY'
        elif not row['mapping']['connector_exists']:
            row['disposition'] = 'CONNECTOR_BUILD_REQUIRED'
        else:
            row['prior_family_lower_bound'] = ledger['families'].get(row['mapping']['family'], {}).get('prior_distinct_design_lower_bound', 0)
            row['remaining_family_capacity'] = capacity(row['mapping']['family'], ledger)
            row['sample_plan'] = audit.select_states(imported['records'], priors[pid]['states'])
            row['disposition'] = 'QUEUED_CUMULATIVE_FAMILY_CAP_6' if not row['remaining_family_capacity'] else row['sample_plan']['status']
        rows.append(row)
    pools = collections.defaultdict(list)
    for row in rows:
        if row.get('disposition') == 'PREPARED':
            pools[row['mapping']['family']].append(row)
    emitted = []
    for family, pool in sorted(pools.items()):
        pool.sort(key=lambda r: (not r['screening_pass'], r['policy_id']))
        for row in pool[:capacity(family, ledger)]:
            row.update(emit_eligible=True, disposition='EMITTED_PENDING_PURPOSE_AND_CALIBRATION', rank=len(emitted) + 1)
            emitted.append(row)
        for row in pool[capacity(family, ledger):]:
            row['disposition'] = 'QUEUED_CUMULATIVE_FAMILY_CAP_6'
    eligible = [r for r in rows if r['v10_class'] in {'a', 'b'}]
    geometries = [r for r in eligible if r['geometry']['passed']]
    ready = [r for r in geometries if r['mapping']['connector_exists']]
    queue = [r for r in geometries if not r['mapping']['connector_exists']]
    summary = dict(catalog_variables=87, v10_classes=classification['counts'], eligible=len(eligible),
                   complete_window=sum(bool(transformed[r['policy_id']]['coverage']['historical_complete_50_state_windows']) for r in eligible),
                   geometry_pass=len(geometries), connector_and_geometry=len(ready), connector_build_queue=len(queue),
                   cap_blocked=sum(r['disposition'] == 'QUEUED_CUMULATIVE_FAMILY_CAP_6' for r in rows),
                   source_encoding_conflict=sum(r['disposition'] == 'PARKED_SOURCE_ENCODING_CONFLICT' for r in rows),
                   emitted=len(emitted), families=dict(collections.Counter(r['mapping']['family'] for r in emitted)),
                   passing_families=dict(collections.Counter(r['mapping']['family'] for r in geometries)))
    bindings = [bind(p) for p in (ROOT / 'THRESHOLD_CLASSIFICATION.json', ROOT / 'CATALOG_V10.json', ROOT / 'FAMILY_CAP_LEDGER.json',
                                SCREEN, Path(__file__), outcome_v7.CATALOG_PATH)]
    bindings += [bind(REPO / s['support_artifact']['path']) for s in c35.CATALOG_DATA['series'].values() if s['adapter'] == 'c35_artifact']
    report = dict(schema_version='research_panel_intake_v1', attempt_id=ROOT.name, rows=rows, summary=summary,
                  input_bindings=bindings, declared_seeds=dict(calibration=CONFIG.calibration_seed, fresh_audit=CONFIG.audit_seed),
                  legal_certification_granted=False, outcome_association_calls=0, model_calls=0, new_downloads=[])
    evidence = ensure_once(ROOT / 'INTAKE.json', report)
    ensure_once(ROOT / 'CONNECTOR_BUILD_QUEUE.json', dict(rows=queue, coverage_is_prospective=True, new_downloads=[]))
    ensure_once(ROOT / 'DOWNLOAD_INVENTORY.json', dict(new_downloads=[], inherited_files=read(SOURCE_CATALOG)['files']))
    jobs = []
    for row in emitted:
        jobs += emit(row, transformed[row['policy_id']], panel, imports[row['policy_id']], evidence, priors[row['policy_id']])
    for name in ('s0_jobs', 'calibration_jobs', 'fresh_audit_jobs'):
        (ROOT / name).mkdir(parents=True, exist_ok=True)
        ensure_once(ROOT / name / 'ROOT_MANIFEST.json', dict(attempt_id=ROOT.name, root=name, emitted_designs=len(emitted),
                    status='PREPARED' if emitted else 'EMPTY_CUMULATIVE_CAP', launch_requires='calibration + direction + V6.2 purpose' if name == 'fresh_audit_jobs' else 'hub dispatch',
                    model_calls=0))
    ensure_once(ROOT / 'JOBS_MANIFEST.json', dict(attempt_id=ROOT.name, jobs=[bind(Path(j)) for j in jobs],
                fresh_audit_root=str(ROOT / 'fresh_audit_jobs'), fresh_dispatch_requires='calibration + direction + V6.2 purpose', model_calls=0))
    with (ROOT / 'VARIABLE_COUNTS.csv').open('x', newline='') as f:
        fields = ['policy_id', 'v10_class', 'family', 'connector_exists', 'treated_n', 'supported_cohorts_n', 'min_cohort_controls', 'disposition']
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, row.get('mapping', {}).get(k, row.get('geometry', {}).get(k, 'NA'))) for k in fields})
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def verify():
    report = read(ROOT / 'INTAKE.json')
    check(len(report['rows']) == 87, 'Denominator changed')
    for binding in report['input_bindings']:
        check(sha(Path(binding['path'])) == binding['sha256'], 'Intake binding changed')
    catalog = read(ROOT / 'CATALOG_V10.json')
    for item in (catalog['source_catalog'], catalog['classification'], catalog['original_policy_cells']):
        check(sha(Path(item['path'])) == item['sha256'], 'Derived source lineage changed')
    classification = read(ROOT / 'THRESHOLD_CLASSIFICATION.json')
    for item in classification['input_bindings']:
        check(sha(Path(item['path'])) == item['sha256'], 'Classification binding changed')
    for v in catalog['variables']:
        check(sha(Path(v['threshold_declaration']['path'])) == v['threshold_declaration']['sha256'], 'Threshold declaration changed')
    rules = {r['policy_id']: r for r in read(ROOT / 'THRESHOLD_CLASSIFICATION.json')['rows']}
    original_rows = read_panel(ROOT / 'panels/original_policy_cells.csv')
    binary_rows = read_panel(catalog['panels'][0]['path'])
    check(len(binary_rows) == len(original_rows), 'Derived policy denominator changed')
    for original, binary in zip(original_rows, binary_rows):
        active = binary_value(original['value'], rules[original['policy_id']])
        conflict = original['value'] is not None and active is None
        check(dict(original, value=active, status='source_encoding_conflict' if conflict else original['status']) == binary,
              'Derived policy flag differs from predeclared minimum')
    ledger = read(ROOT / 'FAMILY_CAP_LEDGER.json')
    for binding in ledger['input_bindings']:
        check(sha(Path(binding['path'])) == binding['sha256'], 'Family-cap source changed')
    emitted = [r for r in report['rows'] if r['emit_eligible']]
    c35.CATALOG_DATA = outcome_v7.catalog(verify_sources=False)
    c35.SUPPORT = {sid: c35.load_support(s) for sid, s in c35.CATALOG_DATA['series'].items() if s['adapter'] == 'c35_artifact'}
    counts = collections.Counter(r['mapping']['family'] for r in emitted)
    for family, count in counts.items():
        check(count <= capacity(family, ledger), 'Cumulative family cap')
    for row in report['rows']:
        if row['v10_class'] == 'c':
            continue
        check(sha(Path(row['import_binding']['path'])) == row['import_binding']['sha256'], 'History hash changed')
        v = next(v for v in catalog['variables'] if v['policy_id'] == row['policy_id'])
        imported = history(binary_rows, v, row['source_window'])
        check(imported == read(row['import_binding']['path']), 'History/spells differ')
        m = mapping(v)
        check(m == row['mapping'], 'Definition-led connector changed')
        g = c35.supported_geometry(imported, m)
        if catalog['source_encoding_conflicts'].get(row['policy_id'], 0):
            g['passed'] = False
        elif not v['coverage']['historical_complete_50_state_windows']:
            g.update(passed=False, complete_window_required=True)
        check(g == row['geometry'], 'Metadata-only support geometry changed')
    if emitted:
        from .verify_intake import main as verify_packets
        verify_packets(IntakeConfig.from_existing(ROOT))
    else:
        check(report['summary']['connector_and_geometry'] == report['summary']['cap_blocked'], 'Empty wave lacks cap explanation')
        check(read(ROOT / 'JOBS_MANIFEST.json')['jobs'] == [], 'Empty wave contains jobs')
    print(json.dumps(dict(status='PASS', emitted=len(emitted), fresh_launch_gate='BLOCKED_PENDING_CALIBRATION' if emitted else 'BLOCKED_EMPTY_CUMULATIVE_CAP')))


def dispatch(rest):
    outcome_v7.register()
    report = read(ROOT / 'INTAKE.json')
    if report['summary']['emitted']:
        from .dispatch import main as ordinary
        return ordinary(['--intake-root', str(ROOT), 'dispatch', *rest])
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['S0', 'S1'])
    parser.add_argument('--jobs-root', type=Path, required=True)
    parser.add_argument('--hub-outside-sandbox', action='store_true', required=True)
    parser.add_argument('--max-concurrency', type=int, required=True)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args(rest)
    roots = {'s0_jobs': 'S0', 'calibration_jobs': 'S1', 'fresh_audit_jobs': 'S1'}
    check(args.jobs_root.resolve().parent == ROOT and roots.get(args.jobs_root.name) == args.stage, 'Stage/root mismatch')
    check(args.max_concurrency == 4, 'C36 concurrency is fixed at4')
    check(args.preflight_only, 'C36 has no launchable jobs: cumulative family caps are exhausted')
    verify()
    check(read(args.jobs_root / 'ROOT_MANIFEST.json')['emitted_designs'] == 0, 'Nonempty root')
    check(not list(args.jobs_root.rglob('*.job.json')), 'Unexpected job under empty root')
    print(json.dumps(dict(status='PREFLIGHT_PASS_EMPTY_CUMULATIVE_CAP', jobs_root=str(args.jobs_root), jobs=0, model_calls=0)))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('action', choices=['classify', 'build', 'verify', 'dispatch'])
    args, rest = parser.parse_known_args(argv)
    if args.action == 'dispatch':
        return dispatch(rest)
    {'classify': classify, 'build': build, 'verify': verify}[args.action]()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
