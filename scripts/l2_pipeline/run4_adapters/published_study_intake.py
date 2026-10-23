"""Run4 task-owned published-study route; metadata verification and guarded S1.

No dispatch, outcome download, fitting, forecast or freeze occurs here. Core S0
requires statute-purpose jobs; the explicit owner exception is isolated here.
Research originals are untrusted data. A passing check is not legal verification.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

from .paths import Run4Paths, REPO_ROOT

ROOT = REPO_ROOT
from scripts.l2_pipeline.agents.jobs import load_job
from scripts.l2_pipeline.cards import load_card, validate_card
from scripts.l2_pipeline.contracts import Disposition, StageContext, StageResult
from scripts.l2_pipeline.coverage import coverage_bounded_card
from scripts.l2_pipeline.first_order import first_order_gate
from scripts.l2_pipeline.run3_plan import support_inventory
from scripts.l2_pipeline.stages.s1_treatment import audit_sample, evaluate_v3, reconcile
from scripts.l2_pipeline.storage import artifact, ensure_once, sha

CARDS = ROOT / 'data/l2_pipeline/cards/run4'
JOBS = ROOT / 'data/l2_pipeline/jobs/run4'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def http(value):
    parsed = urlsplit(value) if isinstance(value, str) else None
    return bool(parsed and parsed.scheme in {'http', 'https'} and parsed.hostname)


def verify_literature(path, paths=None):
    paths = paths or Run4Paths()
    ROOT, CARDS, JOBS = paths.repo_root, paths.cards_dir, paths.jobs_root
    path = paths.reference(path)
    require(path.parent == CARDS.resolve(), 'Only run4 cards may enter this route')
    card = load_card(path)
    payload = card.payload
    require(not validate_card(payload), 'Card schema invalid')
    require(payload['treatment'].get('source_type') == 'published_did_study', 'Wrong source route')
    require(payload['automation']['intake'].get('source_type') == 'published_did_study', 'Source-type mismatch')
    require(payload['automation']['run4'].get('purpose_job_required') is False, 'Literature purpose job exception missing')
    require(payload['first_order'].get('purpose_source_type') == 'published_did_study', 'Paper target binding missing')
    require(first_order_gate(payload)['passed'], 'V6/V6.1 first-order gate did not pass')
    bounded = coverage_bounded_card(payload)
    require(payload['outcome'] == bounded['outcome'], 'Coverage bounds changed')
    ctx = StageContext(card, JOBS / 'work', ROOT)
    projection_path = ctx.design_dir / 'LITERATURE_SOURCE_PROJECTION.json'
    projection = read(projection_path)
    source = next(s for s in payload['sources'] if s['source_id'] == payload['treatment']['complete_list_source_id'])
    require(sha(paths.reference(source['path'])) == source['sha256'] == projection['source_sha256'], 'Paper original bytes changed')
    records = projection['records']
    universe = payload['treatment']['universe']
    require(len(records) == len(universe) and {r['unit'] for r in records} == set(universe), 'Paper roster incomplete/duplicated')
    require(not reconcile(records, records, universe)['gaps'], 'Paper tuples incomplete')
    window = payload['treatment']['source_window']
    states = {r['unit']: dict(start_year=window['start_year'], end_year=window['end_year'], adopt_year=r['start_period']) for r in records}
    periods = payload['automation']['coverage_window']['effective_period_years']
    support = support_inventory(periods, states, available_by_unit={u: periods for u in universe})
    require(support['passed'], 'Coverage-bounded support failed')
    require(sum(r['status'] == 'never_treated' for r in records) >= payload['analysis']['min_controls'], 'Control roster too small')
    require(not (JOBS / 'S0' / card.design_id).exists(), 'Literature cards must not have S0 purpose jobs')
    job_path = JOBS / 'S1' / card.design_id / 'auditor.md'
    job = load_job(job_path)
    ledger = read(CARDS / '_meta/S0_ACCEPTANCE.json')
    accepted = [r for r in ledger['accepted_literature_designs'] if r['design_id'] == card.design_id]
    require(len(accepted) == 1, 'Literature card is not in the reviewed accepted selector')
    require(accepted[0]['card_sha256'] == sha(path) and accepted[0]['s1_job_sha256'] == sha(job_path), 'Reviewed acceptance binding changed')
    from scripts.l2_pipeline.cli import preflight
    preflight(ctx, 'S1')
    selection_path = ctx.design_dir / 'AUDIT_SELECTION.json'
    selection = read(selection_path)
    require(selection['sample'] == audit_sample(records, payload['treatment']['seed']), 'Audit seed/sample changed')
    require(len(selection['sample']) == 10, 'Small-universe V3 ruling absent')
    require(job.stage == 'S1' and job.role == 'auditor' and job.design_id == card.design_id, 'Job identity mismatch')
    require(job.contract.get('source_type') == 'published_did_study', 'Auditor route mismatch')
    require(job.contract.get('complete_list_crosscheck_required') is True, 'Crosscheck requirement missing')
    require(job.contract.get('no_dual_coding_originals') is True, 'Do not manufacture dual coder originals')
    for artifact_path, digest in job.contract['bound_artifacts'].items():
        require(sha(paths.reference(artifact_path)) == digest, 'Input binding changed: ' + artifact_path)
    s1_ready = any((ctx.design_dir / 'receipts/S1' / name).is_file()
                   for name in ('published_source_intake.json', 'published_source_intake_v7.json'))
    if s1_ready:
        preflight(ctx, 'S2')
        require(read(ctx.design_dir / 'TREATMENT.json').get('s1_certified') is True, 'S1 receipt lacks certified treatment')
    return dict(design_id=card.design_id, source_type='published_did_study', card_sha256=sha(path),
                job_path=str(job_path), job_sha256=sha(job_path), s0_passed=True, s1_ready=s1_ready,
                support=support, physical_data_verified=False, outcome_values_read=0)


def verify_all(paths=None):
    paths = paths or Run4Paths()
    CARDS, JOBS = paths.cards_dir, paths.jobs_root
    literature = []
    l1 = []
    for path in sorted(CARDS.glob('*.json')):
        payload = read(path)
        require(not validate_card(payload), 'Invalid card ' + str(path))
        source_type = payload['treatment'].get('source_type')
        if source_type == 'published_did_study':
            literature.append(verify_literature(path, paths))
        else:
            require(source_type == 'l1_queue_v2', 'Unrecognized intake source')
            pointer = payload['automation']['l1_text']
            require(sha(paths.reference(pointer['path'])) == pointer['sha256'], 'L1 excerpt changed')
            evidence = read(paths.reference(pointer['path']))
            require(evidence['text_excerpt'].strip() and evidence['source_text_id'] in payload['lineage']['source_text_ids'], 'L1 original-text identity missing')
            require(sha(paths.reference(evidence['source_path'])) == evidence['source_sha256'], 'L1 statutory bytes changed')
            purpose = load_job(JOBS / 'S0' / payload['design_id'] / 'purpose_verifier.md')
            require(purpose.contract['l1_text_excerpt_sha256'] == pointer['sha256'], 'Purpose job lacks bound original excerpt')
            coders = [load_job(JOBS / 'S1' / payload['design_id'] / (role + '.md')) for role in ('A', 'B')]
            require(len({j.output_dir for j in coders}) == 2, 'Blind coder outputs overlap')
            require(all(j.contract.get('research_only_until_complete_list_bound') is True for j in coders), 'Incomplete histories cannot advance S1')
            require(read(JOBS / 'S1' / payload['design_id'] / 'AUDITOR_RECIPE.json')['dispatchable'] is False, 'Premature L1 audit')
            l1.append(payload['design_id'])
    ledger = read(CARDS / '_meta/S0_ACCEPTANCE.json')
    require([r['design_id'] for r in ledger['accepted_literature_designs']] == [r['design_id'] for r in literature], 'Acceptance selector mismatch')
    for row in ledger['accepted_literature_designs']:
        require(sha(paths.reference(row['card_path'])) == row['card_sha256'] and sha(paths.reference(row['s1_job_path'])) == row['s1_job_sha256'], 'Accepted artifacts changed')
    require(ledger['l1_cards'] == len(l1), 'L1 count mismatch')
    jobs = [load_job(p) for stage in ('S0', 'S1')
            for p in sorted((JOBS / stage).glob('*/*.md'))]
    require(len({j.output_dir for j in jobs}) == len(jobs), 'Job output directories overlap')
    return dict(status='METADATA_VALID_INCOMPLETE_TARGET', literature_s0=len(literature), l1=len(l1),
                jobs=len(jobs), purpose_jobs=sum(j.role == 'purpose_verifier' for j in jobs),
                literature_auditors=sum(j.role == 'auditor' for j in jobs), blind_research_coders=sum(j.role in {'A', 'B'} for j in jobs),
                literature_shortfall=max(0, 30-len(literature)), s1_ready=sum(r['s1_ready'] for r in literature), model_calls=0,
                outcome_values_read=0, evidence_scan_is_not_authority=True)


def collect_audit(job_path, paths=None, *, v7=False):
    """Require preserved full-list crosscheck before standard V3 promotion.

    Unlike the generic collector, keep the crosscheck and original reported
    dates in TREATMENT.json. Any nonsampled discrepancy parks for full recoding.
    No missing/current-only stock is silently converted into historical zeros.
    """
    paths = paths or Run4Paths()
    ROOT, CARDS, JOBS = paths.repo_root, paths.cards_dir, paths.jobs_root
    job_path = paths.reference(job_path)
    require(job_path.is_relative_to(JOBS.resolve()), 'Job outside task outputs')
    job = load_job(job_path)
    report = verify_literature(CARDS / (job.design_id + '.json'), paths)
    require(job.role == 'auditor', 'Only literature auditor may enter this route')
    original = paths.reference(job.output_path)
    require(original.is_relative_to(JOBS.resolve()), 'Original outside task outputs')
    if not original.is_file():
        return dict(status='PARK', reason='Auditor original not received', s1_ready=False)
    answer = read(original)
    require(answer.get('design_id') == job.design_id and answer.get('role') == 'auditor', 'Auditor identity mismatch')
    require(answer.get('source_sha256') == job.contract['source_sha256'] and answer.get('selection_sha256') == job.contract['selection_sha256'], 'Audit binding changed')
    if v7:
        from scripts.l2_pipeline.agents.collect import validate_blindness
        blindness = validate_blindness(job, answer)
    else:
        require(answer.get('blindness_attestation') == dict(other_submissions_read=False, outcome_associations_read=False), 'Auditor blindness unconfirmed')
    audit = evaluate_v3(job.contract['sample'], answer.get('checks', []))
    crosscheck = answer.get('complete_list_crosscheck', {})
    if v7 and audit['passed'] and crosscheck.get('status') == 'UNRESOLVED':
        return collect_reported_history_v7(job, answer, report, audit, blindness, paths)
    if not audit['passed'] or crosscheck.get('status') != 'VERIFIED':
        return dict(status='PARK', reason='V3 or historical complete-list crosscheck unresolved', audit=audit, s1_ready=False)
    card = load_card(CARDS / (job.design_id + '.json'))
    ctx = StageContext(card, JOBS / 'work', ROOT)
    projection = read(ctx.design_dir / 'LITERATURE_SOURCE_PROJECTION.json')
    window = card.payload['treatment']['source_window']
    require(crosscheck.get('predicate') == card.payload['treatment']['predicate'], 'Crosscheck legal definition mismatch')
    checked_window = crosscheck.get('source_window', {})
    require(isinstance(checked_window, dict) and all(
        type(checked_window.get(key)) is int and checked_window[key] == window[key]
        for key in ('start_year', 'end_year')), 'Crosscheck historical scope mismatch')
    # Preserve documentary annotations without requiring the original to omit
    # them. Explicit date boundaries must still match the locked annual scope.
    for key, expected in (
        ('start_date', f"{window['start_year']}-01-01"),
        ('end_date', f"{window['end_year']}-12-31"),
        ('coverage_end', f"{window['end_year']}-12-31"),
    ):
        require(key not in checked_window or checked_window[key] == expected,
                'Crosscheck historical scope mismatch')
    require(http(crosscheck.get('source_url')) and bool(crosscheck.get('locator')), 'Crosscheck source locator missing')
    archived = paths.reference(crosscheck.get('source_path', ''))
    require(archived.is_relative_to(Path(job.output_dir).resolve()), 'Crosscheck archive must be within auditor output')
    require(archived.is_file() and sha(archived) == crosscheck.get('source_sha256'), 'Crosscheck archived bytes missing/changed')
    require(crosscheck.get('unresolved_units') == [], 'Historical roster contains unresolved units')
    corrected = copy.deepcopy(projection['records'])
    for check in answer['checks']:
        if check['status'] == 'known_error':
            require(type(check.get('corrected_start_period')) is int, 'Known date error lacks integer correction')
            next(r for r in corrected if r['unit'] == check['unit'])['start_period'] = check['corrected_start_period']
        elif check['status'] == 'unresolved':
            next(r for r in corrected if r['unit'] == check['unit'])['audit_unresolved'] = True
    rows = copy.deepcopy(crosscheck.get('records', []))
    row_evidence_paths = []
    normalized_units = []
    for row in rows:
        if row.get('locator'):
            continue
        # Auditor originals may carry located evidence per source instead of
        # a flat locator. Bind those bytes before deriving the comparison alias.
        evidence = row.get('official_evidence', [])
        require(isinstance(evidence, list) and bool(evidence), 'Crosscheck row lacks located official evidence')
        locators = []
        for ref in evidence:
            require(http(ref.get('source_url')) and bool(ref.get('locator')), 'Crosscheck row source locator missing')
            evidence_path = paths.reference(ref.get('source_path', ''))
            require(evidence_path.is_relative_to(Path(job.output_dir).resolve()), 'Crosscheck row archive outside auditor output')
            require(evidence_path.is_file() and sha(evidence_path) == ref.get('source_sha256'), 'Crosscheck row archived bytes missing/changed')
            row_evidence_paths.append(evidence_path)
            locators.append(ref['source_url'] + ' | ' + ref['locator'])
        row['locator'] = '; '.join(locators)
        normalized_units.append(row['unit'])
    universe = card.payload['treatment']['universe']
    require(len(rows) == len(universe), 'Independent historical roster incomplete')
    comparison = reconcile(corrected, rows, universe)
    if comparison['gaps']:
        return dict(status='PARK', reason='Full-list discrepancies require recoding and a new audit lock', gaps=comparison['gaps'], s1_ready=False)
    from scripts.l2_pipeline.agents.collect import collect_job
    receipt = collect_job(job_path)
    require(receipt.get('validated') is True, 'Generic collector rejected auditor')
    treatment = dict(records=corrected, published_original_records=projection['records'],
                     source_type='published_did_study', source_sha256=projection['source_sha256'],
                     audit=audit, sample=job.contract['sample'], complete_list_crosscheck=crosscheck,
                     original_sha256=sha(original), card_sha256=report['card_sha256'],
                     comparison=comparison['counts'], source_window=window,
                     crosscheck_normalization=dict(locator_alias_units=normalized_units, scientific_values_changed=False),
                     required_sensitivities={'drop_unresolved_audit_dates': sorted(r['unit'] for r in corrected if r.get('audit_unresolved'))},
                     required_s4_checks=['partial first-year exposure and operative date clock',
                         'unresolved-date sensitivity and source coverage limits',
                         'population/proxy measurement and co-occurring policies',
                         'paper/pipeline units and estimand harmonization before L3'],
                     outside_source_window='unknown', s1_certified=True, outcome_values_read=0)
    target = ctx.design_dir / 'TREATMENT.json'
    ensure_once(target, treatment)
    evidence_paths = [paths.reference(p) for p in job.contract['bound_artifacts']]
    evidence_paths += [job_path, job_path.with_suffix('.job.json'), original, archived, Path(__file__).resolve()]
    evidence_paths = list(dict.fromkeys(evidence_paths + row_evidence_paths))
    require(all(p.resolve().is_relative_to(ROOT) for p in evidence_paths), 'S1 provenance must remain in repository task outputs')
    provenance = ctx.design_dir / 'S1_IMPORT_PROVENANCE.json'
    ensure_once(provenance, dict(source_type='published_did_study',
        evidence=[dict(path=str(p.resolve().relative_to(ROOT)), sha256=sha(p)) for p in evidence_paths],
        dual_coder_originals_manufactured=False, complete_list_checked=True,
        numeric_replication_gap_enabled=False, owner_route='2026-10-08 run4 instruction'))
    result = StageResult('S1', job.design_id, Disposition.READY,
        'Paper timing, historical roster crosscheck and seeded V3 audit verified; actual outcomes remain S2',
        artifacts=(artifact(target, 'treatment'), artifact(provenance, 'source_provenance')),
        metrics=dict(audit, outcome_values_read=0))
    ensure_once(ctx.design_dir / 'receipts/S1/published_source_intake.json',
        dict(card_sha256=report['card_sha256'], result=result.as_dict()))
    return dict(status='SOURCE_READY', s1_ready=True, treatment_path=str(target), treatment_sha256=sha(target),
                next_stage='S2 actual data availability, then S3 support; no estimation authorization')



def collect_reported_history_v7(job, answer, report, audit, blindness, paths):
    """Bind the published adoption table and seeded official audit under V5/V7.

    The unresolved full historical census remains documentary evidence. It is
    not relabeled verified, and convention zeros end at the paper's window.
    """
    root = paths.repo_root
    card = load_card(paths.cards_dir / (job.design_id + '.json'))
    ctx = StageContext(card, paths.jobs_root / 'work', root)
    projection_path = ctx.design_dir / 'LITERATURE_SOURCE_PROJECTION.json'
    projection = read(projection_path)
    original = paths.reference(job.output_path)
    corrected = copy.deepcopy(projection['records'])
    for check in answer['checks']:
        row = next(r for r in corrected if r['unit'] == check['unit'])
        if check['status'] == 'known_error':
            require(type(check.get('corrected_start_period')) is int, 'Known date error lacks integer correction')
            row['start_period'] = check['corrected_start_period']
        elif check['status'] == 'unresolved':
            row['audit_unresolved'] = True
    window = card.payload['treatment']['source_window']
    require(all(check['corrected_start_period'] <= window['end_year']
                for check in answer['checks'] if check['status'] == 'known_error'),
            'Official correction after source coverage needs a new scope version')
    from scripts.l2_pipeline.agents.collect import collect_job
    receipt = collect_job(paths.reference(job.job_path))
    require(receipt.get('validated') is True, 'Generic collector rejected auditor')
    treatment = dict(records=corrected, published_original_records=projection['records'],
        source_type='published_did_study', source_sha256=projection['source_sha256'],
        audit=audit, sample=job.contract['sample'], source_window=window,
        complete_list_crosscheck=answer['complete_list_crosscheck'],
        historical_list_basis='paper_reported_adoption_table_crosschecked_by_seeded_V3_audit',
        convention_zero_scope=window, outside_source_window='unknown',
        v7_blindness=blindness, original_sha256=sha(original), card_sha256=report['card_sha256'],
        required_sensitivities={'drop_unresolved_audit_dates': sorted(r['unit'] for r in corrected if r.get('audit_unresolved'))},
        required_s4_checks=['partial first-year exposure and operative date clock',
            'unresolved-date sensitivity and source coverage limits',
            'population/proxy measurement and co-occurring policies',
            'paper/pipeline units and estimand harmonization before L3'],
        s1_certified=True, outcome_values_read=0)
    target = ensure_once(ctx.design_dir / 'TREATMENT.json', treatment)
    evidence = [paths.reference(p) for p in job.contract['bound_artifacts']]
    evidence += [paths.reference(job.job_path), paths.reference(job.job_path).with_suffix('.job.json'), original,
                 Path(__file__).resolve(), root / 'docs/plans/l2-scale/HUB_RULING_V7_LITERATURE_BLINDNESS.md',
                 root / 'docs/plans/l2-scale/HUB_RULING_V5_LITERATURE_TABLES.md']
    proof = ctx.design_dir / 'S1_IMPORT_PROVENANCE.json'
    # verify_literature already revalidated any accepted import and its archival
    # bindings. Recollection must retain that original collector-code vintage.
    if not proof.is_file():
        ensure_once(proof, dict(source_type='published_did_study',
            evidence=[dict(path=str(p.resolve().relative_to(root)), sha256=sha(p)) for p in dict.fromkeys(evidence)],
            dual_coder_originals_manufactured=False, complete_list_checked=False,
            historical_list_basis=treatment['historical_list_basis'], numeric_replication_gap_enabled=False,
            owner_route='Explicit V7 run4 task; reported adoption table treatment history'))
    result = StageResult('S1', job.design_id, Disposition.READY,
        'V7 paper-reported treatment history and seeded V3 audit accepted; historical census limitation retained',
        artifacts=(artifact(target, 'treatment'), artifact(proof, 'source_provenance')),
        metrics=dict(audit, outcome_values_read=0))
    ensure_once(ctx.design_dir / 'receipts/S1/published_source_intake_v7.json',
                dict(card_sha256=report['card_sha256'], result=result.as_dict()))
    return dict(status='SOURCE_READY', s1_ready=True, treatment_path=str(target), treatment_sha256=sha(target),
                v7=True, audit=audit, outcome_values_read=0)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--verify', action='store_true')
    mode.add_argument('--collect-audit', type=Path)
    parser.add_argument('--v7', action='store_true', help='Apply V7 published-result blindness and paper-reported treatment history')
    parser.add_argument('--repo-root', type=Path, default=ROOT)
    parser.add_argument('--cards-dir', type=Path)
    parser.add_argument('--jobs-root', type=Path)
    args = parser.parse_args(argv)
    paths = Run4Paths(args.repo_root, args.cards_dir, args.jobs_root)
    result = verify_all(paths) if args.verify else collect_audit(args.collect_audit, paths, v7=args.v7)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return int(result.get('status') == 'PARK')


if __name__ == '__main__':
    raise SystemExit(main())
