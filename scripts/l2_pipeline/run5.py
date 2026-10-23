"""Run5 metadata-only replay of archived public tracker histories.

Selection is a fixed legal-provision rule. It reads neither outcome values nor
published effects. Correlated provisions remain two policy families and require
separate identification review; card count is never an independent-policy count.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from .policy_tracker_intake import prepare_batch
from .storage import ensure_once, public_input, sha

NAL = 'pdaps_naloxone'
GS = 'pdaps_good_samaritan'
EXCLUDED_GS = {
    'goodsam-law': 'Existing run2_overdose_good_samaritan already registers overdose mortality',
    'goodsam-paroleyn__no': 'Complement of the same protection; no new mechanism',
    'goodsam-paroleyn__unclear': 'Unclear legal status is unknown, not a treatment',
    'goodsam-cs__law_provides_other_procedural_protections': 'Unspecified procedural scope needs a legal definition',
    'goodsam-paraphernalia__law_provides_other_procedural_protections': 'Unspecified procedural scope needs a legal definition',
    'goodsam-mit-type__alcohol_related_offenses': 'Alcohol-specific protection has no exact acute-poisoning outcome in the current connector',
    'goodsam-mit-type__other_offenses_beyond_controlled_substances_and_alcohol_related_violations': 'Unspecified other crimes do not define a linked overdose-prevention mechanism',
}


def select_policies(records):
    """Legal scope only: substantive immunity/access protections, no conditions."""
    selected, exclusions = [], []
    for raw in records:
        tracker, pid = raw['tracker_id'], raw['policy_id']
        reason = None
        if tracker == NAL:
            if pid == 'pharmacist-dispensing-method__standing_order':
                reason = 'Existing batch2 naloxone_standing already registers overdose mortality'
            elif raw.get('conditional_parent_question_id') and not pid.startswith('pharmacist-dispensing-method__'):
                reason = 'Eligibility/care condition within an immunity; retained as source metadata, not another program design'
        elif tracker == GS:
            reason = EXCLUDED_GS.get(pid)
        else:
            reason = 'Prescribing/access construct needs a first-order connector or policy-specific purpose evidence'
        if reason:
            exclusions.append(dict(tracker_id=tracker, policy_id=pid, reason=reason))
            continue
        policy = copy.deepcopy(raw)
        # The selected category is part of the legal predicate, not a label on
        # a common unspecified multi-choice question.
        policy['original_tracker_definition'] = policy.get('definition')
        policy['definition'] = policy['title']
        policy['domain'] = 'Health'
        policy['policy_family'] = 'naloxone_overdose_prevention' if tracker == NAL else 'overdose_good_samaritan'
        policy['outcome'] = dict(
            connector='nchs_mortality', measure_id='drug_overdose_deaths_count',
            target_outcome='drug_overdose_deaths_count', relation='closest_proxy',
            proxy_justification=(
                'All-drug overdose mortality measures the same fatal-overdose construct; '
                'nonopioid deaths and people outside the legally affected group cause population dilution. '
                'Policy-specific purpose and population review must approve this proxy.'),
            target_population_gap=True,
            target_population_description='People at risk of overdose affected by the specified protection/access provision; naloxone reverses opioid overdose',
            population_description='All state residents, all ages and sexes; final all-drug overdose deaths, no opioid-only isolation',
            population_filters={}, years=[1999, 2017], outcome_unit='deaths',
            measurement_url='https://data.cdc.gov/resource/bi63-dtpu.csv',
            evidence_locator='Final NCHS all-drug overdose state totals; exact policy-purpose and population-proxy review pending')
        selected.append(policy)
    return selected, exclusions


def prepare(archive_dir: Path, output_dir: Path):
    archive_dir = public_input(archive_dir)
    records = []
    inputs = {}
    for tracker in (NAL, GS):
        path = archive_dir/'sources'/(tracker+'.policies.json')
        value = json.loads(public_input(path).read_text())
        records.extend(value['policies'] if isinstance(value, dict) else value)
        inputs[str(path)] = sha(path)
    policies, exclusions = select_policies(records)
    selected = ensure_once(output_dir/'SELECTED_POLICIES.json', dict(
        policies=policies, exclusions=exclusions, inputs=inputs,
        selection_rule='run5_legal_provision_v1', expected_sign_used=False,
        policy_families=2, provisions_are_not_independent_policy_families=True,
        outcome_values_read=0, published_effects_read=0))
    report = prepare_batch(policies, output_dir/'intake')
    downloads = []
    for relative in ('sources/DOWNLOADS.json', 'extra/DOWNLOADS.json'):
        path = archive_dir/relative
        if path.is_file():
            value = json.loads(path.read_text())
            downloads.extend(value['downloads'] if isinstance(value, dict) else value)
    for item in downloads:
        path = public_input(Path(item['path']))
        if path.stat().st_size != item['size_bytes'] or sha(path) != item['sha256']:
            raise ValueError('Downloaded tracker archive changed: '+str(path))
    summary = dict(report, selected_policies_path=str(selected), selected_policies_sha256=sha(selected),
        exclusions=exclusions, independent_policy_families=2,
        new_downloads=downloads, download_count=len(downloads),
        download_bytes=sum(r['size_bytes'] for r in downloads),
        interpretation='Provision cards are correlated legal mechanisms within two drug-policy families; no independent causal evidence or S0/S1 readiness asserted',
        required_next_steps=['Policy-specific purpose/population-proxy acceptance',
                            'Official V3 audits', 'Clean cohort/control/co-adoption support',
                            'Fresh specifications, forecasts, freeze and authorized execution'])
    ensure_once(output_dir/'RUN5_REPORT.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archives', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    from .storage import output_root
    from .policy_tracker_intake import REPO
    out = output_root(args.output_dir, REPO)
    report = prepare(args.archives, out)
    print(json.dumps({k:report[k] for k in (
        'cards_emitted', 'cards_by_domain', 'histories_acquired', 'complete_histories',
        'first_order_ready', 'earliest_cohort_supported', 'auditor_jobs', 'coder_jobs',
        'independent_policy_families', 'download_count', 'download_bytes')}, indent=2))
    return int(any('error' in r for r in report['results']))


if __name__ == '__main__':
    raise SystemExit(main())
