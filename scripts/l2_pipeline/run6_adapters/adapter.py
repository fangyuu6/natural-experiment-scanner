"""Run6 immutable metadata intake using existing S0/V6/S1 contracts.

Code lives under scripts; archived metadata remain in the run data directory. No shared pipeline code, run5 input,
outcome file, estimate, or frozen study is modified or opened by this adapter.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
from collections import Counter

from scripts.l2_pipeline import policy_tracker_intake as intake
from scripts.l2_pipeline.cards import validate_card
from scripts.l2_pipeline.storage import ensure_once

from scripts.l2_pipeline.run6_adapters.paths import DATA_ROOT
ROOT = DATA_ROOT
ORIGINAL_BUILD = intake.build_card


def source_earliest(policy, card):
    """Keep earliest observed source activation before outcome-window trimming.

    States active at the source baseline are left censored. A later observed
    cohort cannot replace a dated early activation that loses V6 preperiods.
    No claim that quarter stocks establish January1 full exposure is made.
    """
    events = []
    by_unit = {}
    if policy.get('interval_rows'):
        for row in policy['interval_rows']:
            by_unit.setdefault(row['unit'], []).append((row['start_date'], row.get('active'), row))
    elif policy.get('quarterly_rows'):
        for row in policy['quarterly_rows']:
            key = '%04d-Q%d' % (row['year'], row['quarter'])
            by_unit.setdefault(row['unit'], []).append((key, row.get('active'), row))
    else:
        for row in policy.get('annual_rows', []):
            by_unit.setdefault(row['unit'], []).append((str(row['year']), row.get('active'), row))
    baseline = []
    unknown_early = []
    for unit, rows in sorted(by_unit.items()):
        rows.sort(key=lambda r: r[0])
        previous = None
        for index, (date, active, row) in enumerate(rows):
            if index == 0 and active == 1:
                baseline.append(unit)
                break
            if active == 1 and previous == 0:
                events.append(dict(unit=unit,year=int(date[:4]),source_date=date,locator=row['locator']))
                break
            if active == 1 and previous is None:
                unknown_early.append(unit)
                break
            previous = active
    cohort = min((r['year'] for r in events),default=None)
    check = copy.deepcopy(card['automation']['earliest_cohort_check'])
    periods = card['automation']['coverage_window']['effective_period_years']
    source_pre = [y for y in periods if cohort is not None and y < cohort]
    source_post = [y for y in periods if cohort is not None and y > cohort]
    # Activation year is excluded conservatively, even if quarter1 records
    # first show the new stock. The official audit must establish the day.
    pass_source = cohort is not None and len(source_pre) >= 3 and len(source_post) >= 2
    check.update(source_earliest_observed_activation_year=cohort,
                 source_activation_events=events,
                 source_baseline_left_censored_units=baseline,
                 source_first_activation_after_unknown_units=unknown_early,
                 source_earliest_pre_periods=source_pre,source_earliest_post_periods=source_post,
                 source_earliest_support_passed=pass_source,
                 no_late_cohort_replacement=True,
                 activation_year_not_counted_as_clean=True)
    check['passed'] = check['passed'] and pass_source
    if not check['passed']:
        check['status'] = 'insufficient_or_unknown_source_earliest_cohort'
        check['reason'] = 'Require existing V6 check AND earliest source activation support; no later-cohort substitution'
    return check


def build_card(policy, outcome, **kwargs):
    # Names/lineage are set before any card/source/audit byte lock is emitted.
    kwargs['design_id'] = policy['policy_id'] if policy['policy_id'].startswith('run6_') else 'run6_' + policy['policy_id']
    card = ORIGINAL_BUILD(policy, outcome, **kwargs)
    card['lineage']['queue_version'] = 'tracker_first_run6_20261009'
    card['automation']['intake']['domain'] = policy.get('domain','Health')
    card['treatment']['seed'] = 6006
    card['analysis']['design_independence_status'] = 'family_and_mechanism_pending_coadoption_review'
    card['automation']['earliest_cohort_check'] = source_earliest(policy,card)
    card['automation']['duplicate_review'] = policy.get('duplicate_review',{})
    if not policy.get('outcome',{}).get('target_gap') and not policy.get('first_order_candidate',True):
        card['first_order']['availability']='unavailable'
        card['automation']['tracker']['target_gap']='Statutory first-order target has no current connector; plausible mortality is downstream'
    errors = validate_card(card)
    if errors:
        raise ValueError('; '.join(errors))
    return card


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,default=ROOT)
    parser.add_argument('--policies',type=Path)
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args(argv)
    args.policies=args.policies or args.data_root/'SELECTED_POLICIES.v2.json'
    args.output_dir=args.output_dir or args.data_root/'prepared_v2'
    value=json.loads(args.policies.read_text())
    policies=value.get('policies',[]) if isinstance(value,dict) else value
    original=intake.build_card
    intake.build_card=build_card
    try:
        report=intake.prepare_batch(policies,args.output_dir/'intake')
    finally:
        intake.build_card=original
    report['distinct_candidate_families']=len({p['policy_family'] for p in policies})
    candidate_by_id={p['policy_id']:p for p in policies}
    eligible_families={candidate_by_id[r['policy_id']]['policy_family'] for r in report['results']
        if 'error' not in r and r['target_mapping']['mapped']
        and not candidate_by_id[r['policy_id']]['outcome'].get('target_gap')
        and candidate_by_id[r['policy_id']].get('first_order_candidate',True)}
    report['distinct_families']=len(eligible_families)
    report['mapped_first_order_lead_families']=sorted(eligible_families)
    report['families_by_domain']={d:sorted({p['policy_family'] for p in policies if p.get('domain')==d})
        for d in sorted({p.get('domain','unassigned') for p in policies})}
    report['target_distinct_families']=15
    report['target_met']=report['distinct_families']>=15
    report['family_count_includes_purpose_or_history_parked_leads']=True
    report['family_count_excludes_unmapped_or_downstream_candidates']=True
    report['independent_designs_not_inferred_from_card_count']=True
    ensure_once(args.output_dir/'RUN6_REPORT.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='results'},indent=2))
    return int(any('error' in r for r in report['results']))


if __name__=='__main__':
    raise SystemExit(main())
