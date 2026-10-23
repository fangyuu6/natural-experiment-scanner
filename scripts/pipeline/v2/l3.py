#!/usr/bin/env python3
"""Retroactive annual L3 comparison and count-only compiled-panel inventory.

No network, covariate magnitudes, party columns, association tables or estimates.
Native event-only absence is never expanded into zeros. DK's supplied state-year
panels retain their explicit zero convention only within each supplied window.
"""
import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

CONTIG = set('AL AZ AR CA CO CT DE FL GA ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split())
UNBLINDED = 'athlete agents act'
PENDING = {'rights of the terminally ill act (1985)', 'interstate compact for mutual military aid in an emergency', 'renewenergy', 'securities act (1956)'}
FIELDS = ('start_year', 'adoption_status')
SEED = 'lean-v2-l3-audit-20261006'
SMALL_THIRD_READ = 5
STATUS_BAR = .05
YEAR_BAR = .10


def read(path, delimiter=','):
    with Path(path).open(newline='') as stream:
        return list(csv.DictReader(stream, delimiter=delimiter))


def write(path, records, fields=None):
    records = list(records)
    fields = fields or list(records[0])
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def year(value):
    value = str(value).strip()
    return int(value[:4]) if re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?', value) else None


def primary_supported(record):
    try:
        evidence = json.loads(record.get('evidence_json', '') or '[]')
    except ValueError:
        evidence = []
    if isinstance(evidence, dict):
        evidence = [evidence]
    return any(isinstance(e, dict) and e.get('url') and
               (e.get('locator') or record.get('section_ref')) and
               any(t in str(e.get('source_type', '')).lower() for t in
                   ('official', 'agency', 'state_code', 'session_law', 'compact', 'tracker', 'ulc', 'commission', 'legislative'))
               and e.get('evidence_role') != 'search_lead_only' for e in evidence)


def analysis_value(records, field):
    """Known values collapse across record layout/day precision; mixed known years stay ambiguous.

    Ever adopted refers to the original pilot target 2025. This status diagnostic
    is separate from the DK historical panel-window status used for the audit.
    Invalid historical negative certificates remain unknown.
    """
    target = 'first_enactment_date' if field == 'start_year' else 'adoption_status'
    rows = [r for r in records if r['field_id'] == target]
    known = [r for r in rows if r.get('value_status') == 'known']
    if field == 'start_year':
        values = {year(r['value']) for r in known}
        values.discard(None)
    else:
        # A pilot rejected all historical nonadoption certificates. Do not resurrect them.
        start_years = {year(r['value']) for r in records if r['field_id']=='first_enactment_date' and r.get('value_status')=='known'}
        start_years.discard(None)
        positive_status = 'not_yet' if start_years and min(start_years)>2025 else 'treated'
        values = {positive_status for r in known if r['value'] == 'adopted'}
        values |= {'not_applicable' for r in known if r['value'] == 'not_applicable'}
    if len(values) > 1:
        return None, 'ambiguous', False
    if len(values) == 1:
        value = next(iter(values))
        relevant = [r for r in known if (year(r['value']) == value if field == 'start_year' else r['value'] in {'adopted', 'not_applicable'})]
        return value, 'known', any(primary_supported(r) for r in relevant)
    return None, 'unknown' if rows else 'missing', False


def compare(a, b):
    va, sa, pa = a
    vb, sb, pb = b
    both = sa == sb == 'known'
    unknown = sa == sb == 'unknown'
    return dict(a_value=va, a_status=sa, b_value=vb, b_status=sb,
                analysis_agree=int(both and va == vb), both_known=int(both),
                both_known_supported=int(both and pa and pb), both_unknown=int(unknown),
                raw_agree=int((both and va == vb) or unknown),
                analysis_dispute=int(both and va != vb),
                evidence_gap=int(not both), ambiguous=int(sa == 'ambiguous' or sb == 'ambiguous'))


def audit_draw(pairs, seed=SEED):
    """Deterministic random unit sample, 20%, minimum 10; stratify positive/absence claims.

    SHA256 ranks are independent of official answers. Both strata represented
    when nonempty; selection probabilities and sampled denominator retained.
    """
    n = len(pairs)
    if not n:
        return []
    size = min(n, max(10, math.ceil(.20 * n)))
    strata = defaultdict(list)
    for pair in pairs:
        ay = pair['adopt_year']
        strata['positive' if ay is not None and ay <= pair['end'] else 'absence'].append(pair)
    names = sorted(strata)
    allocation = {name: int(size * len(strata[name]) / n) for name in names}
    if size >= len(names):
        for name in names:
            allocation[name] = min(len(strata[name]), max(2, allocation[name]))
    while sum(allocation.values()) > size:
        eligible = [name for name in names if allocation[name] > min(2, len(strata[name]))]
        name = max(eligible, key=lambda name: (allocation[name], name))
        allocation[name] -= 1
    while sum(allocation.values()) < size:
        name = max(names, key=lambda name: (size * len(strata[name]) / n - allocation[name], name))
        allocation[name] += 1
    sampled = []
    for name in names:
        ranked = sorted(strata[name], key=lambda p: hashlib.sha256(f"{seed}|{p['policy']}|{p['state']}".encode()).hexdigest())
        for pair in ranked[:allocation[name]]:
            sampled.append(dict(pair, stratum=name, stratum_population=len(ranked),
                                stratum_sample=allocation[name], inclusion_probability=allocation[name] / len(ranked)))
    return sampled


def need_calendar(rule, decision_year):
    if rule.get('defensibility') != 'candidate_objective_need':
        return None
    try:
        reference = decision_year - int(rule['count_lag_years'])
        valid = [int(y) for y in re.findall(r'\d{4}', rule.get('valid_reference_years', ''))]
        if valid:
            eligible = [y for y in valid if y <= reference and decision_year-y <= 4]
            if not eligible:
                return None
            reference = max(eligible)
        excluded = {int(y) for y in re.findall(r'\d{4}', rule.get('excluded_reference_years', ''))}
        return reference if int(rule['first_year']) <= reference <= int(rule['last_year']) and reference not in excluded else None
    except (ValueError, KeyError):
        return None


def observed_need(root, inputs):
    """Exact indicator adapters: annual LAUS and prebuilt FARS/VM2 ratio, presence only.

    Poverty three-year averages and ACS B27016 are not substituted for the v0
    annual poverty/Table19 and B27001 definitions. No political field is accessed.
    Other dictionary indicators have no adapter here and remain unmeasured.
    """
    coverage = defaultdict(set)
    fips = dict(zip('01 04 05 06 08 09 10 12 13 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56'.split(),
                    'AL AZ AR CA CO CT DE FL GA ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split()))
    for path in sorted((root/'data/l4/insight_inputs/downloads/bls_laus_state_unemployment/raw').glob('*.json')):
        inputs.add(path)
        payload = json.loads(path.read_text())
        for series in payload.get('Results', {}).get('series', []):
            sid = series['seriesID']
            state = fips.get(sid[5:7]) if re.fullmatch(r'LAUST\d{2}0000000000003', sid) else None
            if state:
                for cell in series['data']:
                    if cell['period'] == 'M13' and str(cell.get('value', '')).strip() not in {'', '-', 'NA', 'N/A'}:
                        coverage['Annual state unemployment rate'].add((state, int(cell['year'])))
    path = root/'data/l4/insight_inputs/panels/gt_state_year.csv'
    if path.exists():
        inputs.add(path)
        for row in read(path):
            if row['state'] in CONTIG and row.get('fatality_rate', '').strip() not in {'', 'NA', 'N/A'}:
                coverage['Traffic fatalities per 100 million vehicle miles traveled'].add((row['state'], int(row['need_observation_year'])))
    return coverage


def run(root: Path, out: Path) -> dict:
    root, out = Path(root), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    base = root/'data/l3_pooled'
    pilot = base/'pilot10'
    inputs = set()
    policies = sorted(p.stem for p in (pilot/'templates').glob('*.csv'))
    groups = defaultdict(list)
    units_by_policy = defaultdict(set)
    for policy in policies:
        path = pilot/'templates'/(policy+'.csv')
        inputs.add(path)
        units_by_policy[policy].update(row['unit_id'] for row in read(path))
    for coder in 'AB':
        for policy in policies:
            path = pilot/coder/(policy+'.csv')
            inputs.add(path)
            for row in read(path):
                units_by_policy[policy].add(row['unit_id'])
                row['_source_path'] = str(path.relative_to(root))
                groups[(policy, row['unit_id'], coder)].append(row)
    cells = []
    for policy in policies:
        for state in sorted(units_by_policy[policy]):
            for field in FIELDS:
                cells.append(dict(policy_id=policy, unit_id=state, field=field,
                                  blind_eligible=int(policy != UNBLINDED),
                                  **compare(analysis_value(groups[policy, state, 'A'], field),
                                            analysis_value(groups[policy, state, 'B'], field))))
    inputs.add(pilot/'reconciled/reconciled_fields.csv')
    official = {}
    for row in read(pilot/'reconciled/reconciled_fields.csv'):
        if row['field_id'] == 'first_enactment_date' and row['resolution'].startswith('accepted_') and row['reconciled_value_status'] == 'known' and row['definition_pending'] != '1':
            y = year(row['reconciled_value'])
            if y is not None:
                official[row['policy_id'], row['unit_id']] = y
    for cell in cells:
        key = (cell['policy_id'], cell['unit_id'])
        oy = official.get(key)
        resolved = bool(cell['analysis_dispute'] and cell['field'] == 'start_year' and oy is not None)
        cell['existing_third_read_resolved'] = int(resolved)
        cell['remaining_analysis_dispute'] = int(cell['analysis_dispute'] and not resolved)
        cell['reconciled_analysis_value'] = oy if resolved else cell['a_value'] if cell['analysis_agree'] else None
        target = 'first_enactment_date' if cell['field']=='start_year' else 'adoption_status'
        for coder in 'AB':
            cell[coder.lower()+'_original_refs_json'] = json.dumps([{k:r.get(k, '') for k in ('_source_path','record_id','version_id','field_id','value','value_status','section_ref','evidence_json')} for r in groups[cell['policy_id'],cell['unit_id'],coder] if r['field_id']==target], sort_keys=True)
    write(out/'l3_analysis_cells.csv', cells)
    inputs.add(base/'policy_crosswalk.csv')
    links = read(base/'policy_crosswalk.csv')
    author_to_native = {r['author_id']:r['spid_policy'] for r in links if r['status'] == 'matched_id'}
    author_names = {r['author_id']:r['author_policy'] for r in links if r['author_id']}
    inputs.add(base/'author_state_observation_windows.csv')
    pairs = []
    for row in read(base/'author_state_observation_windows.csv'):
        author = row['author_id']
        policy = author_to_native.get(author, author_names.get(author, author))
        pairs.append(dict(author_id=author, policy=policy, state=row['state'],
                          bundle='SPID' if author.startswith('dv_spid:') else 'NBER',
                          adopt_year=int(float(row['adopt_year'])) if row['adopt_year'] else None,
                          start=int(row['start']), end=int(row['end']), rows=int(row['rows']), zeros=int(row['zeros'])))
    assert len({(p['author_id'], p['state']) for p in pairs}) == len(pairs), 'duplicate compiled state/policy pairs'
    assert all(p['state'] in CONTIG and p['rows']==p['end']-p['start']+1 for p in pairs), 'invalid compiled geography/window'
    by_policy = defaultdict(list)
    for pair in pairs:
        by_policy[pair['policy']].append(pair)
    audits = []
    for policy in policies:
        for pair in audit_draw(by_policy[policy]):
            oy = official.get((policy, pair['state']))
            claimed = int(pair['adopt_year'] is not None and pair['adopt_year'] <= pair['end'])
            os = int(oy <= pair['end']) if oy is not None else None
            timed = oy is not None and claimed and os == 1
            audits.append(dict(policy_id=policy, author_id=pair['author_id'], unit_id=pair['state'],
                               compiled_start=pair['start'], compiled_end=pair['end'], compiled_year=pair['adopt_year'],
                               compiled_status=claimed, stratum=pair['stratum'], stratum_population=pair['stratum_population'],
                               stratum_sample=pair['stratum_sample'], inclusion_probability=pair['inclusion_probability'],
                               official_year=oy, official_status=os, status_reviewed=int(os is not None),
                               status_error=int(os != claimed) if os is not None else '', timing_reviewed=int(timed),
                               timing_error=int(oy != pair['adopt_year']) if timed else '',
                               review_status='existing_official_first_year' if oy is not None else 'not_reviewed',
                               reason='accepted original first-adoption year; status evaluated at compiled sample end' if oy is not None else 'targeted official third read needed; unknown is not an absence'))
    write(out/'l3_audit_sample.csv', audits)
    per_policy = []
    for policy in policies:
        pc = [r for r in cells if r['policy_id'] == policy]
        pa = [r for r in audits if r['policy_id'] == policy]
        initial_units = {r['unit_id'] for r in pc if r['analysis_dispute']}
        differing_units = {r['unit_id'] for r in pc if r['remaining_analysis_dispute']}
        comparable = sum(r['both_known'] for r in pc)
        known_agree = sum(r['analysis_agree'] for r in pc)
        status_n = sum(r['status_reviewed'] for r in pa)
        status_errors = sum(int(r['status_error'] or 0) for r in pa)
        timing_n = sum(r['timing_reviewed'] for r in pa)
        timing_errors = sum(int(r['timing_error'] or 0) for r in pa)
        # A sampled unreviewed absence/positive is unresolved, so no denominator removal.
        complete = bool(pa) and status_n == len(pa) and timing_n == sum(r['compiled_status'] for r in pa)
        status_weight = sum(1/r['inclusion_probability'] for r in pa if r['status_reviewed'])
        timing_weight = sum(1/r['inclusion_probability'] for r in pa if r['timing_reviewed'])
        weighted_status_error = sum(int(r['status_error'] or 0)/r['inclusion_probability'] for r in pa if r['status_reviewed']) / status_weight if status_weight else None
        weighted_timing_error = sum(int(r['timing_error'] or 0)/r['inclusion_probability'] for r in pa if r['timing_reviewed']) / timing_weight if timing_weight else None
        full_status_weight = sum(1/r['inclusion_probability'] for r in pa)
        full_timing_weight = sum(1/r['inclusion_probability'] for r in pa if r['compiled_status'])
        status_screen_rate = sum((int(r['status_error']) if r['status_reviewed'] else 1)/r['inclusion_probability'] for r in pa) / full_status_weight if full_status_weight else None
        timing_screen_rate = sum((int(r['timing_error']) if r['timing_reviewed'] else 1)/r['inclusion_probability'] for r in pa if r['compiled_status']) / full_timing_weight if full_timing_weight else None
        audit_pass = complete and weighted_status_error <= STATUS_BAR and (not timing_n or weighted_timing_error <= YEAR_BAR)
        escalation = (complete and weighted_status_error > STATUS_BAR) or (complete and timing_n and weighted_timing_error > YEAR_BAR)
        per_policy.append(dict(policy_id=policy, blind_eligible=int(policy != UNBLINDED), units=len(units_by_policy[policy]),
                               comparable_cells=comparable, known_agreement_cells=known_agree,
                               known_agreement_rate=round(known_agree/comparable, 6) if comparable else '',
                               raw_agree=sum(r['raw_agree'] for r in pc), both_unknown=sum(r['both_unknown'] for r in pc),
                               analysis_disputes=sum(r['analysis_dispute'] for r in pc), initial_dispute_units=len(initial_units), existing_third_read_resolved=sum(r['existing_third_read_resolved'] for r in pc), remaining_analysis_disputes=sum(r['remaining_analysis_dispute'] for r in pc), dispute_units=len(differing_units),
                               evidence_gap_cells=sum(r['evidence_gap'] for r in pc), ambiguous_cells=sum(r['ambiguous'] for r in pc),
                               small_third_read=int(0 < len(differing_units) <= SMALL_THIRD_READ and len(differing_units)/len(units_by_policy[policy]) <= .10),
                               definition_pending=int(policy in PENDING), blind_repass_needed=int(policy == UNBLINDED),
                               compiled_panel_units=len(by_policy[policy]), compiled_panel_events=sum(p['adopt_year'] is not None and p['start'] <= p['adopt_year'] <= p['end'] for p in by_policy[policy]),
                               audit_sample_units=len(pa), status_reviewed=status_n, status_errors=status_errors,
                               timing_reviewed=timing_n, timing_errors=timing_errors, reviewed_only_weighted_status_error_rate=weighted_status_error, reviewed_only_weighted_timing_error_rate=weighted_timing_error, unresolved_as_fail_status_screen_rate=status_screen_rate, unresolved_as_fail_timing_screen_rate=timing_screen_rate, audit_pass=int(audit_pass),
                               audit_remaining_units=len(pa)-status_n,
                               data_ready=int(audit_pass and policy not in PENDING and policy != UNBLINDED and not differing_units),
                               conditional_convention_panel=int(bool(by_policy[policy])),
                               readiness='full_coding_escalation' if escalation else 'definition_then_audit' if policy in PENDING else 'blind_repass_then_audit' if policy == UNBLINDED else 'targeted_audit_and_third_read' if differing_units else 'targeted_audit'))
    write(out/'l3_policy_readiness.csv', per_policy)
    inputs.update({base/'need_dictionary_v0.csv', base/'author_need_dictionary_v0.csv', base/'first100_queue.csv'})
    need_rules = {r['policy']:r for r in read(base/'need_dictionary_v0.csv')}
    author_rules = {r['author_id']:r for r in read(base/'author_need_dictionary_v0.csv')}
    coverage = observed_need(root, inputs)
    queue = {r['policy'] for r in read(base/'first100_queue.csv')}
    scopes = {'pilot10':set(policies), 'fixed100':queue, 'author_spid_all':{p['policy'] for p in pairs if p['bundle']=='SPID'},
              'author_nber_unmerged':{p['policy'] for p in pairs if p['bundle']=='NBER'}}
    # Keep NBER definitions separate; no legal-tool deduplication has been accepted.
    pooled = []
    for name, wanted in scopes.items():
        pp = [p for p in pairs if p['policy'] in wanted and (p['bundle']=='NBER' if name=='author_nber_unmerged' else p['bundle']=='SPID')]
        events = calendar_events = covered_events = risk = calendar_risk = covered_risk = 0
        candidate_policies, calendar_policies, covered_policies = set(), set(), set()
        observed_rows = sum(p['rows'] for p in pp)
        for pair in pp:
            rule = need_rules.get(pair['policy'], author_rules.get(pair['author_id'], {}))
            if rule.get('defensibility') == 'candidate_objective_need':
                candidate_policies.add(pair['policy'])
            for y in range(pair['start'], pair['end']+1):
                event = pair['adopt_year'] == y
                at_risk = pair['adopt_year'] is None or y <= pair['adopt_year']
                events += event
                risk += at_risk
                ref = need_calendar(rule, y)
                observed = ref is not None and (pair['state'], ref) in coverage.get(rule.get('indicator'), set())
                calendar_events += event and ref is not None
                calendar_risk += at_risk and ref is not None
                covered_events += event and observed
                covered_risk += at_risk and observed
                if at_risk and ref is not None:
                    calendar_policies.add(pair['policy'])
                if at_risk and observed:
                    covered_policies.add(pair['policy'])
        pooled.append(dict(scope=name, policies=len({p['policy'] for p in pp}), state_policy_pairs=len(pp),
                           supplied_panel_rows=observed_rows, events=events, convention_zero_rows=sum(p['zeros'] for p in pp),
                           at_risk_rows=risk, need_dictionary_candidate_policies=len(candidate_policies),
                           need_calendar_supported_policies=len(calendar_policies), need_calendar_events=calendar_events,
                           need_calendar_risk_rows=calendar_risk, need_observed_covered_policies=len(covered_policies),
                           need_observed_events=covered_events, need_observed_risk_rows=covered_risk,
                           audit_accepted_policies=sum(r['audit_pass'] for r in per_policy) if name=='pilot10' else 0,
                           convention='DK explicit supplied panel zero; within original state/policy observation windows; first-adoption absorbing convention',
                           availability_rule='need-covered: >=1 at-risk row with exact indicator presence at dictionary lag; does not mean complete coverage',
                           audit_status='pending random positive-and-absence review; original convenience date diagnostic is separate'))
    write(out/'l3_pooled_counts.csv', pooled)
    inputs.add(pilot/'reconciled/reference_date_comparison.csv')
    diagnostics = []
    for source in ('SPID','DellaVigna_Kim_SPID'):
        rows = [r for r in read(pilot/'reconciled/reference_date_comparison.csv') if r['coded_source']=='reconciled' and r['reference_source']==source and r['comparable']=='1']
        diagnostics.append(dict(reference_source=source, comparable=len(rows), agree=sum(r['agree']=='1' for r in rows),
                                errors=sum(r['agree']!='1' for r in rows), random_sample=False,
                                absence_claims_audited=0, interpretation='convenience positive-only date diagnostic; cannot pass random status/absence audit'))
    blind = [r for r in cells if r['blind_eligible']]
    known = sum(r['both_known'] for r in blind)
    summary = dict(protocol='lean_v2', analysis_granularity='first-enactment year; adoption status at original pilot target2025',
                   analysis_scope='union of original template and discovered coder jurisdictions; pooled counts separate contiguous48', blind_policy_count=9, all_policy_count=10, blind_cells=len(blind),
                   raw_agree=sum(r['raw_agree'] for r in blind), both_unknown=sum(r['both_unknown'] for r in blind),
                   both_known=known, both_known_supported=sum(r['both_known_supported'] for r in blind), supported_analysis_agree=sum(r['both_known_supported'] and r['analysis_agree'] for r in blind), analysis_agree=sum(r['analysis_agree'] for r in blind),
                   known_agreement_rate=round(sum(r['analysis_agree'] for r in blind)/known,6) if known else None,
                   analysis_disputes=sum(r['analysis_dispute'] for r in blind), remaining_analysis_disputes=sum(r['remaining_analysis_dispute'] for r in blind), existing_third_read_resolved=sum(r['existing_third_read_resolved'] for r in blind), evidence_gaps=sum(r['evidence_gap'] for r in blind),
                   policies_with_no_value_disputes=sum(r['dispute_units']==0 for r in per_policy),
                   policies_small_third_read=sum(r['small_third_read'] for r in per_policy), small_third_read_max_units=SMALL_THIRD_READ,
                   data_ready_policies=sum(r['data_ready'] for r in per_policy),
                   conditional_compiled_policies=sum(r['conditional_convention_panel'] for r in per_policy),
                   random_audit_drawn_units=len(audits), random_audit_reviewed_units=sum(r['status_reviewed'] for r in audits),
                   random_audit_remaining_units=sum(not r['status_reviewed'] for r in audits),
                   random_audit_accepted_policies=sum(r['audit_pass'] for r in per_policy),
                   audit_bars=dict(status_error=STATUS_BAR,year_error=YEAR_BAR,unreviewed='audit incomplete; never excluded from draw'),
                   convenience_date_diagnostics=diagnostics, pooled_counts=pooled,
                   actual_need_adapters=['BLS LAUS annual M13 presence', 'existing FARS/VM2 fatality-rate presence at stated reference year'],
                   need_adapter_limit='Other v0 indicators remain unmeasured here; candidate calendar coverage is reported separately; no numeric need or party values tabulated.',
                   downloads_this_run=[], real_association_fit_calls=0, association_tables=0)
    # Inputs include original code plus raw/derived sources actually consulted.
    inputs.add(Path(__file__).resolve())
    transitive = {base/'downloads/DVKim_SPID_iqt.dta', base/'downloads/DVKim_NBER_iqt.dta'}
    inputs.update(transitive)
    manifest = []
    for path in sorted(inputs):
        manifest.append(dict(path=str(path.relative_to(root)), role='transitive_raw_of_author_windows' if path in transitive else 'direct_input', bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    write(out/'l3_input_manifest.csv', manifest)
    summary['input_manifest_sha256'] = hashlib.sha256((out/'l3_input_manifest.csv').read_bytes()).hexdigest()
    (out/'l3_summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True)+'\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = run(args.root, args.out or args.root/'data/pipeline/v2')
    print(json.dumps({k: result[k] for k in ('blind_cells','analysis_agree','both_known','analysis_disputes','data_ready_policies','conditional_compiled_policies')}))


if __name__ == '__main__':
    main()
