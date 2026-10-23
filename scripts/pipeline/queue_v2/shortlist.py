#!/usr/bin/env python3
"""Build a review batch from queue metadata and declared readiness judgments only."""
import argparse
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
QCEW = 'https://www.bls.gov/cew/downloadable-data-files.htm'
CPS = 'https://www.census.gov/data/datasets/time-series/demo/cps/cps-basic.html'
TAX = 'https://www2.census.gov/govs/statetax/historical_data.html'
# A connector pool, not a universal admission list; other designs stay in queue.
# evidence: 2 = official mechanism packet, identity/support reconciliation pending;
# 1 = extraction evidence only. data: 2 = free compatible proxy located;
# 1 = free broad proxy, eligible-group/geography link unresolved; 0 = no free link.
POOL = {
 'occupational_licensing': ('License recognition split', 2, 2, 0, CPS, 'DID_LICENSE_RECOGNITION.md', 'Split universal recognition from boards, compacts and amendments.'),
 'paid_family_leave': ('Mandatory PFML benefits split', 2, 2, 0, CPS, 'DID_PAID_FAMILY_LEAVE.md', 'Split mandatory/private-worker benefits from voluntary and public-worker laws.'),
 'prevailing_wage': ('Public-works wage coverage/rate reforms', 2, 2, 1, QCEW, 'DID_PREVAILING_WAGE.md', 'Separate coverage, rate setting and first mandates; code contract clocks.'),
 'earned_income_tax_credit': ('State EITC generosity/refundability', 2, 2, 2, 'https://www.census.gov/programs-surveys/cps/data/datasets.html', 'DID_EARNED_INCOME_TAX_CREDIT.md', 'Correct NJ rate and DE enactment errors before reconstructing equivalent cohorts.'),
 'collective_bargaining_funding': ('Collective bargaining funding rules', 1, 2, 0, CPS, None, 'Verify affected worker groups and mandatory funding versus dues rules.'),
 'workplace_safety': ('Workplace safety rules', 1, 1, 0, QCEW, None, 'Split exposure by hazard/industry; employment is a broad proxy, not injury incidence.'),
 'gig_worker_compensation': ('Gig-worker compensation rules', 1, 1, 0, CPS, None, 'Worker classification and state residence/workplace exposure need matching.'),
 'ban_the_box': ('Hiring criminal-history restrictions', 1, 1, 0, CPS, None, 'Split public/private employers; public outcomes lack criminal-history linkage.'),
 'wage_subsidy': ('Wage subsidy eligibility reforms', 1, 1, 0, QCEW, None, 'Identify benefit eligibility and distinguish subsidies from wage mandates.'),
 'historic_preservation_tax_credit': ('Historic rehabilitation tax credits', 1, 2, 0, TAX, None, 'Code credit cap/refundability and free construction-industry proxy.'),
 'rd_tax_credit': ('Research and development credits', 1, 2, 0, TAX, None, 'Separate credit generosity/eligibility; total tax receipts are a broad outcome.'),
 'film_tax_credit': ('Film production tax credits', 1, 2, 0, QCEW, None, 'Verify program caps, claim dates and NAICS 5121 suppression.'),
 'estate_tax': ('State estate tax exemptions/rates', 1, 2, 0, TAX, None, 'Separate repeal, exemption and rate instruments; revenue measurement is fiscal-year.'),
 'hotel_occupancy_tax': ('Hotel occupancy taxes', 1, 1, 0, QCEW, None, 'Local tax units need city/county matching; state rows do not certify statewide exposure.'),
 'tax_increment_financing': ('Tax increment financing authorization', 1, 1, 0, 'https://www.census.gov/programs-surveys/gov-finances.html', None, 'State authorization differs from local district adoption and fiscal exposure.'),
 'property_tax_assessment': ('Property assessment rules', 1, 1, 0, 'https://www.census.gov/programs-surveys/gov-finances.html', None, 'Local implementation, reassessment frequency and affected parcels remain missing.'),
 'property_tax_relief': ('Property tax relief eligibility', 1, 1, 0, 'https://www.census.gov/programs-surveys/gov-finances.html', None, 'Split homeowner, veteran, senior and business relief; identify incidence geography.'),
 'severance_tax': ('Resource severance tax changes', 1, 2, 0, TAX, None, 'Separate resources, extraction volumes and tax bases; commodity prices are confounds.'),
 'commercial_driver_licensing': ('Commercial driver licensing', 1, 1, 0, QCEW, None, 'Separate eligibility/test and administrative fees; transport exposure support pending.'),
 'child_labor_enforcement': ('Child labor enforcement reforms', 1, 1, 0, CPS, None, 'Public CPS minors and employer/industry coverage need support certification.'),
 'local_sales_tax': ('Local sales tax authorization', 1, 1, 0, 'https://www.census.gov/programs-surveys/gov-finances.html', None, 'Local election/implementation clocks required; state authorization alone is insufficient.'),
 'homestead_exemption': ('Homestead exemptions', 1, 1, 0, 'https://www.census.gov/programs-surveys/gov-finances.html', None, 'Separate legal eligibility thresholds and local implementation.'),
}
PILOTS = {'occupational_licensing', 'paid_family_leave', 'prevailing_wage', 'earned_income_tax_credit'}

def build(queue_dir):
    queue_dir = Path(queue_dir)
    input_path = queue_dir / 'research_candidates.json'
    research = json.loads(input_path.read_text())
    available = {d['policy_family']: d for d in research['did_ranked']}
    if not PILOTS <= set(available):
        raise ValueError('Missing pilot families: ' + str(PILOTS - set(available)))
    rows = []
    for family, (label, evidence, data, penalty, url, dossier, missing) in POOL.items():
        if family not in available:
            continue
        d = available[family]
        priority = 2 if d['domain'] in ('labor_market', 'taxation_fiscal', 'appropriations_budget') else 1
        rows.append({'design_id': d['design_id'], 'track': 'DiD', 'policy_family': family, 'label': label,
            'domain': d['domain'], 'priority': {'domain': priority, 'evidence': evidence, 'free_data': data,
            'known_extraction_error_penalty': penalty, 'score': priority * 10 + evidence * 3 + data * 2 - penalty},
            'dataset_url': url, 'dossier': dossier, 'status': 'verifying' if dossier else 'queued',
            'missing': missing, 'source_text_count': d['distinct_source_texts'], 'state_count': len(d['states']),
            'source_record_ids': d['all_source_record_ids'],
            'representative_source_ids': [s['source_text_id'] for s in d['representative_sources'][:3]]})
    rows.sort(key=lambda d: (-d['priority']['score'], d['policy_family'], d['design_id']))
    rows = rows[: 18]
    if len(rows) != 18 or not PILOTS <= {d['policy_family'] for d in rows}:
        raise ValueError('Batch needs eighteen distinct DiD hypotheses including four dossier pilots.')
    def records(filename):
        with (queue_dir / filename).open() as stream:
            for line in stream:
                yield json.loads(line)
    rdd = next(d for d in records('rdd_candidates.jsonl')
        if any(s['source_text_id'] == 'govinfo-govinfo-plaw-plaw-105publ220' for s in d['source_records'])
        and str(d['threshold']['cutoff']).replace(',', '') == '500000'
        and d['source_records'][0]['source'] == 'legacy_csv')
    h1b_anchor = 'regulations.gov-regulations-USCIS-2023-0005-1323'
    rct_candidates = [d for d in records('rct_mentions.jsonl') if 'h1b' in d['policy_family'].lower()]
    if not rct_candidates:
        raise ValueError('Missing actual existing-extraction H1B allocation mention.')
    rct = next((d for d in rct_candidates if any(s['source_text_id'] == h1b_anchor for s in d['source_records'])), rct_candidates[0])
    for d, label, url, dossier, missing, data in [
        (rdd, 'WIA local-area designation: population 500,000', 'https://www.bls.gov/lau/tables.htm', 'RDD_WIA_LOCAL_AREA.md', 'Official population vintage, requests/designation decisions and geographic support.', 1),
        (rct, 'Historical H-1B cap lottery', 'https://www.dol.gov/agencies/eta/foreign-labor/performance', 'RCT_H1B_CAP_LOTTERY.md', 'Actual winner/loser roster and linked free outcomes; public LCA files are not ITT outcomes.', 0)]:
        rows.append({'design_id': d['design_id'], 'track': d['track'], 'policy_family': d['policy_family'],
            'label': label, 'domain': d['domain'], 'priority': {'domain': 2, 'evidence': 2, 'free_data': data,
            'known_extraction_error_penalty': 0, 'score': 20 + 6 + data * 2}, 'dataset_url': url,
            'dossier': dossier, 'status': 'verifying', 'missing': missing,
            'source_text_count': len({s['source_text_id'] for s in d['source_records']}), 'state_count': None,
            'source_record_ids': [s['source_record_id'] for s in d['source_records']],
            'representative_source_ids': [s['source_text_id'] for s in d['source_records']]})
    assert len(rows) == len({d['design_id'] for d in rows}) == 20
    for rank, d in enumerate(rows, 1):
        d['rank'] = rank
    return {'batch_id': 'first_batch_v2.0', 'queue_input_sha256': hashlib.sha256(input_path.read_bytes()).hexdigest(),
        'selection_rule': 'Connector pool ranked by 10*domain+3*evidence+2*free_data-known_error_penalty; eighteen DiD slots and one each RDD/RCT verification pilot. Lexical family tie-break, then stable ID. No outcomes or estimates read.',
        'score_definitions': 'domain2 labor/public finance, 1 other; evidence2 official mechanism packet (scope reconciliation pending), 1 extraction only; free_data2 free compatible proxy, 1 broad/geography-incomplete proxy,0 no assignment-outcome linkage; known-error penalty2 for EITC contradictions and 1 for prevailing-wage extraction mismatch. No official data-ready claims.',
        'design_count_basis': '20 provisional design hypotheses; family/instrument splits must be resolved before counting distinct frozen designs. Existing prototype designs remain in full queue; this batch selects other families.',
        'designs': rows}

def markdown(batch):
    lines = ['# L2 first verification batch — queue v2', '',
        '20 provisional design hypotheses: 18 DiD, 1 RDD and 1 lottery pilot. Four DiD dossiers cover the highest-ranked DiD slots. All remain queued/verifying; no design is data-ready, frozen or run.', '',
        'Priority uses evidence strength, free-data compatibility and labor/public-finance preference. The declared score is 10×domain+3×evidence+2×data−known-error penalty; scales and per-design inputs are in `data/pipeline/queue_v2/first_batch.json`. One RDD and one lottery slot reserve verification capacity. No expected sign, significance, L1 policy-significance score, outcome association or benchmark result enters selection.', '',
        'The initial connector pool contains 22 named labor/fiscal families, chosen for reusable CPS/QCEW/Census-finance links. It is a first-batch capacity choice, not a universal admissibility rule; the entire queue and all dispositions remain available. Database dates screen waves only. Deduplicate legal identity and split mechanisms before reporting distinct executed-design N.', '',
        '| Rank | Track / verification hypothesis | Queue design ID | E / data / penalty; score | Existing texts / states | Status |',
        '|---|---|---|---|---|---|']
    for d in batch['designs']:
        p = d['priority']; label = d['label']
        if d['dossier']:
            label = f"[{label}](dossiers/{d['dossier']})"
        lines.append(f"| {d['rank']} | {d['track']}: {label} | `{d['design_id']}` | {p['evidence']} / {p['free_data']} / {p['known_extraction_error_penalty']}; {p['score']} | {d['source_text_count']} / {d['state_count'] if d['state_count'] is not None else 'n/a'} | {d['status']} |")
    lines += ['', '## Data links and next evidence', '', '| Rank | Free dataset lead | Missing item / next gate |', '|---|---|---|']
    for d in batch['designs']:
        lines.append(f"| {d['rank']} | [Public source]({d['dataset_url']}) | {d['missing']} |")
    lines += ['', 'Free source leads are linked proxies, not certified treatment×outcome panels. [QCEW](https://www.bls.gov/cew/downloadable-data-files.htm) offers state/county employment and wages quarterly (NAICS 1990 onward); [CPS basic](https://www.census.gov/data/datasets/time-series/demo/cps/cps-basic.html) offers state individual/household monthly files from 1994; [Census tax history](https://www2.census.gov/govs/statetax/historical_data.html) offers annual state tax collections from 1992. Local-finance use additionally requires individual-government coverage and boundary checks. Dossiers record pilot geography, frequency, years, official clocks, verified literature, co-policies and missing evidence.', '',
        '## Pilot lineage and dispositions', '']
    for d in batch['designs']:
        if d['dossier']:
            lines.append(f"- `{d['design_id']}`: existing source IDs " + ', '.join('`'+s+'`' for s in d['representative_source_ids']) + f"; [dossier](dossiers/{d['dossier']}).")
    lines += ['', 'EITC remains a verification case after official sources contradict extracted NJ generosity and DE timing. H-1B remains blocked by absent public winner/loser outcome linkage; historical uniform draws must be separated from FY2027 weighted selection. WIA requires the governor-selected historical population vintage and actual designation requests/decisions. These are field-specific blockers, not unsuccessful estimates.', '',
        '[Remote-seller taxes](dossiers/DID_REMOTE_SELLER_TAX.md) and [motor-fuel taxes](dossiers/DID_FUEL_EXCISE_TAX.md) are deferred discovery dossiers: official multi-state reforms exist, but no eligible matched state-wave parent was recovered under the current strict/date evidence. They are outside the counted 20. New evidence can reopen those source dispositions without changing the public old funnel.', '',
        '## Execution handoff', '',
        'Start blind legal A/B coding with the four DiD packets and independent RD/lottery mechanism checks. Reconcile intervention identity, complete exposure clocks and untreated/unknown status. Build free-data connectors and inspect coverage/support counts only. Freeze the outcome, estimand, clocks, controls, inference, diagnostics, stop/reopen rules and hashes before estimating. Do not choose among supported specs after seeing effects.', '',
        'Every shortlist design maps through the registry adapter to source text→provisional legal instrument/family→design→outcome/spec version→run. Six dossiers advance to verifying with reasons; fourteen remain queued. Current outcome-spec/run arrays are empty. Static export retains every status and later original-unit result/diagnostic/support/reference fields for build-time inlining; no site or deployment files changed.', '',
        'Planning scenario: 100 source-text reviews and 20 legal-design verifications in month 1; budget roughly 80–160 analyst-hours for coding/reconciliation and initial connectors. 8–10 frozen executions/month is the roadmap target conditional on support, not an observed throughput estimate. Measure minutes, source-search attempts, connector time and disposition reasons for two consecutive batches before raising capacity. Stop counts belong in the denominator.', '',
        'No data files downloaded; no new LLM/API calls, estimation, accounts, publication or credentials access. Web references were checked 2026-10-06 and treated as untrusted factual material; no separate local PDF/HTML scan was claimed. Machine selections can be reproduced with `PYTHONDONTWRITEBYTECODE=1 python3 scripts/pipeline/queue_v2/shortlist.py`.']
    return '\n'.join(lines) + '\n'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--queue-dir', type=Path, default=ROOT / 'data/pipeline/queue_v2')
    parser.add_argument('--document', type=Path, default=ROOT / 'docs/plans/l2-scale/FIRST_BATCH.md')
    parser.add_argument('--seed-registry', action='store_true', help='Create new queued/verifying snapshots and static export; refuses existing registry files.')
    args = parser.parse_args()
    batch = build(args.queue_dir)
    (args.queue_dir / 'first_batch.json').write_text(json.dumps(batch, ensure_ascii=False, indent=2) + '\n')
    args.document.parent.mkdir(parents=True, exist_ok=True)
    args.document.write_text(markdown(batch))
    if args.seed_registry:
        seed_registry(batch, args.queue_dir)
    print(json.dumps({'designs': 20, 'dossiers': sum(bool(d['dossier']) for d in batch['designs']), 'ids': [d['design_id'] for d in batch['designs']]}))

def seed_registry(batch, queue_dir):
    """Create review seeds once; a production status history must never be overwritten."""
    sys.path.insert(0, str(ROOT))
    from scripts.pipeline.registry.import_queue import import_queue
    from scripts.pipeline.registry.validate import validate_registry
    from scripts.pipeline.registry.export_static import export_static, inline_safe_json
    queue_dir = Path(queue_dir)
    names = ['first_batch_registry_queued.json', 'first_batch_registry.json', 'first_batch_site_registry.json']
    if any((queue_dir / n).exists() for n in names):
        raise FileExistsError('Registry seeds already exist; append status events using the registry contract.')
    rows = {d['design_id']: d for d in batch['designs']}
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    registry = import_queue(queue_dir, set(rows), stamp)
    for d in registry['designs']:
        b = rows[d['design_id']]; p = b['priority']
        d['priority'] = {'evidence_strength': p['evidence'], 'free_data_availability': p['free_data'],
            'domain_priority': p['domain'], 'rationale': batch['selection_rule'] + ' ' + b['missing'] +
            ' Known extraction error penalty=' + str(p['known_extraction_error_penalty'])}
    prior = copy.deepcopy(registry)
    for d in registry['designs']:
        b = rows[d['design_id']]
        if b['status'] != 'verifying':
            continue
        reason = 'Official-source/readiness packet assembled; ' + b['missing']
        ref = 'docs/plans/l2-scale/dossiers/' + b['dossier']
        d['history'].append({'event_id': d['design_id'] + ':first_batch_verifying', 'at': stamp,
            'from_status': 'queued', 'to_status': 'verifying', 'actor': 'first_batch_source_review',
            'reason': reason, 'reopen_condition': None, 'evidence_refs': [ref],
            'outcome_spec_id': None, 'run_id': None, 'readiness_snapshot': None})
        d['status'] = 'verifying'; d['reason'] = reason
        d['readiness']['evidence_refs'].append(ref); d['readiness']['missing'].append(b['missing'])
    errors = validate_registry(registry, previous=prior)
    if errors:
        raise ValueError(errors)
    values = [json.dumps(prior, indent=2, sort_keys=True), json.dumps(registry, indent=2, sort_keys=True),
        inline_safe_json(export_static(registry, previous=prior))]
    for name, value in zip(names, values):
        with (queue_dir / name).open('x') as stream:
            stream.write(value + '\n')

if __name__ == '__main__':
    main()
