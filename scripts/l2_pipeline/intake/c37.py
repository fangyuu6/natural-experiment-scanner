"""C37 prospective collection and wave release; historical packets stay immutable.

Family diversity orders waves, never removes eligible designs. This versioned
entry supersedes C36's cumulative-cap preparation without changing bound code.
"""
from __future__ import annotations

import argparse
import collections
import copy
import json
from pathlib import Path

from .config import IntakeConfig, REPO
from . import v8b_calendar as audit
from scripts.l2_pipeline.storage import ensure_once, sha

C35 = REPO / 'data/l2_pipeline/sources/research_panel/c35_20261010'
C36 = C35.parent / 'c36_20261010'
COLLECTION = C35 / 'c37_collection'
FILTERED = C35 / 'c37_filtered/c35_20261010'
WAVE = C36 / 'c37_wave1/c36_20261010'


def read(path):
    return json.loads(Path(path).read_text())


def binding(path):
    return dict(path=str(Path(path).resolve()), sha256=sha(path))


def order_waves(rows, *, cap=6, occupied=None):
    """Keep every nonduplicate design, assigning overflow to subsequent waves."""
    if type(cap) is not int or cap < 1:
        raise ValueError('Wave cap must be positive')
    counts = collections.Counter(occupied or {})
    if any(type(n) is not int or not 0 <= n <= cap for n in counts.values()):
        raise ValueError('Current-wave occupancy exceeds the diversity cap')
    result = []
    for row in sorted(rows, key=lambda r: (not r['screening_pass'], r['policy_id'])):
        r = copy.deepcopy(row)
        family = r['mapping']['family']
        n = counts[family]
        r.update(wave=n // cap + 1, family_position=n % cap + 1,
                 emit_eligible=n < cap,
                 disposition='EMITTED_PENDING_PURPOSE_AND_CALIBRATION' if n < cap else 'QUEUED_NEXT_WAVE')
        counts[family] += 1
        result.append(r)
    return result


def calibration_verdict(job, answer):
    """Existing V8b class-a rule, independent of V3/S1 certification."""
    audit.validate_audit_clock(job, answer)
    from scripts.l2_pipeline.agents.collect import _locator_checks, validate_blindness
    validate_blindness(job, answer)
    checks = _locator_checks(answer['checks'])
    offsets = [r for r in checks if r.get('class_a_offset') is True]
    contradictions = [r['unit'] for r in offsets if r.get('status') == 'unresolved'
                      or r.get('direction_match') is not True
                      or r.get('panel_year') != r.get('official_operational_calendar_year')]
    confirmed = len(checks) == 8 and bool(offsets) and not contradictions
    return dict(confirmed=confirmed, verdict='CONFIRMED' if confirmed else
                'CONTRADICTED' if contradictions else 'INCONCLUSIVE',
                class_a_offsets=len(offsets), contradicted_states=contradictions,
                counts=dict(collections.Counter(r['status'] for r in checks)),
                known_errors=[r for r in checks if r['status'] == 'known_error'],
                unresolved_states=[r['unit'] for r in checks if r['status'] == 'unresolved'],
                s1_ready=False, execution_authorized=False)


def relocate_job(source, destination, old_root, new_root):
    """Move discovery/output paths only; retain source, card and sample bindings."""
    from scripts.l2_pipeline.agents.jobs import load_job, _write_text_once
    old = read(Path(source).with_suffix('.job.json'))
    new = copy.deepcopy(old)
    text = Path(source).read_text()
    for field in ('job_path', 'output_dir', 'output_path', 'result_path', 'promote_path'):
        value = old[field]
        if not Path(value).is_relative_to(old_root):
            raise ValueError('Job discovery path escaped its intake')
        new[field] = str(new_root / Path(value).relative_to(old_root))
        text = text.replace(value, new[field])
    # Host-plan roots are orchestration metadata, outside the bound inputs.
    for kind in ('calibration_jobs', 'fresh_audit_jobs'):
        text = text.replace(str(old_root / kind), str(new_root / kind))
    if new['job_path'] != str(destination):
        raise ValueError('Relocated job identity differs')
    _write_text_once(destination, text)
    new['job_sha256'] = sha(destination)
    ensure_once(destination.with_suffix('.job.json'), new)
    relocated = load_job(destination)
    if relocated.contract != old['contract'] or list(relocated.inputs) != old['inputs']:
        raise ValueError('Scientific bindings changed during relocation')
    return relocated


def collect_c35():
    from scripts.l2_pipeline.agents.collect import collect_job
    from scripts.l2_pipeline.agents.jobs import load_job
    from scripts.l2_pipeline.agents.purpose import accept_purpose_verification
    from scripts.l2_pipeline.contracts import DesignCard, StageContext
    config = IntakeConfig.from_existing(C35)
    audit.configure(config); audit.install()
    rows = []
    reasons = {
        'absvot': 'Two distinct same-policy studies establish election turnout; statutory scope independently establishes qualified registered voters. Purpose only; VEP/highest-office measurement remains downstream.',
        'earlvot': 'Two distinct early-voting studies establish election turnout and Illinois independently establishes registered-voter scope. Purpose only; no measurement or calendar certification.',
        'death_penalty': 'Two distinct capital-punishment-law studies establish statewide homicide incidence; Virginia abolition verifies direct criminal-law coverage. Purpose only; mortality-series measurement remains downstream.',
        'regulation_bicycle_helmets': 'Located WV/NC purpose clauses target child bicycling mortality. C20 ruling 5 predeclares FARS ages 5–14 as primary; retain age and road coverage dilution for measurement review.',
        'regulation_mandatory_seatbelts': 'Located mandatory-belt purpose and occupant-fatality study establish occupant mortality and statutory covered occupants. C20 ruling 4 fixes FARS occupant deaths as primary.',
        'gunshowh': 'Unresolved V6.2 same-policy two-study route: only Liu is confirmed as a qualifying gun-show study; Oregon scope/safety alone does not establish all-intent mortality as purpose.'}
    for row in read(C35 / 'INTAKE.json')['rows']:
        if not row['emit_eligible']:
            continue
        did = row['design_id']
        purpose_job = load_job(C35 / 's0_jobs/S0' / did / 'purpose_verifier.md')
        receipt = collect_job(purpose_job.job_path)
        purpose = read(purpose_job.output_path)
        ctx = StageContext(DesignCard.from_mapping(read(purpose_job.contract['card_path']),
                           Path(purpose_job.contract['card_path'])), C35 / 'purpose_work', REPO)
        decision = 'accept' if purpose['status'] == 'VERIFIED' else 'park'
        ruling = accept_purpose_verification(ctx, decision, reasons[row['policy_id'].split(':')[1]])
        ruling_path = ctx.design_dir / 'PURPOSE_HUB_RULING_V62.json'
        job = load_job(C35 / 'calibration_jobs/S1' / did / ('auditor.' + C35.name + '.md'))
        answer = read(job.output_path)
        cal_receipt = collect_job(job.job_path)
        verdict = calibration_verdict(job, answer)
        cal_path = ensure_once(COLLECTION / 'calibration' / did / 'CALIBRATION_COLLECTION.json',
                              dict(**verdict, job=binding(job.job_path), original=binding(job.output_path),
                                   collection=cal_receipt, scope='V8b direction only; V3/S1 not granted'))
        acceptance = dict(**verdict, design_id=did, calibration_job_sha256=sha(job.job_path),
                          calibration_output_sha256=sha(job.output_path),
                          calibration_collection_path=str(cal_path), calibration_collection_sha256=sha(cal_path),
                          purpose_ruling_path=str(ruling_path), purpose_ruling_sha256=sha(ruling_path),
                          purpose_accepted=decision == 'accept', model_calls=0)
        ensure_once(Path(job.output_dir) / 'CALIBRATION_HUB_ACCEPTANCE.json', acceptance)
        rows.append(dict(design_id=did, policy_id=row['policy_id'], purpose=decision.upper(),
                         purpose_ruling=binding(ruling_path), purpose_collection=receipt,
                         calibration=verdict, fresh_eligible=decision == 'accept' and verdict['confirmed']))
    ensure_once(COLLECTION / 'COLLECT_REPORT.json', dict(rows=rows, model_calls=0, originals_retained=True,
                fresh_eligible=sum(r['fresh_eligible'] for r in rows), measurement_accepted=0, s1_ready=0))
    ensure_once(COLLECTION / 'NEEDS_RULING.json', dict(questions=[dict(design_id='c35_siegel_gunshowh',
                question='枪展手枪背景核查：是否沿用 C20 的 firearm_homicide_rate，或保留当前 all-intent firearm mortality？若保留，哪个第二项同政策类型研究或精确法定目的条款满足 V6.2？',
                current_disposition='S0 PARK; calibration INCONCLUSIVE; no fresh dispatch')],
                ordinary_clock_errors_require_new_history_not_new_ruling=True))
    return rows


def filter_c35(rows):
    from scripts.l2_pipeline.agents.jobs import load_job
    from scripts.l2_pipeline.intake.fresh_gate import fresh_gate
    eligible = {r['design_id']: r for r in rows if r['fresh_eligible']}
    original = read(C35 / 'INTAKE.json')
    filtered = copy.deepcopy(original)
    filtered['rows'] = [r for r in filtered['rows'] if r['design_id'] in eligible]
    filtered['summary'] = dict(catalog_variables=len(eligible), emitted=len(eligible))
    filtered['c37_filter'] = dict(original=binding(C35 / 'INTAKE.json'), purpose_only=True,
                                  s1_ready=False, model_calls=0)
    ensure_once(FILTERED / 'INTAKE.json', filtered)
    jobs, provenance = [], []
    for did, row in sorted(eligible.items()):
        purpose = FILTERED / 'purpose_rulings' / did / 'PURPOSE_HUB_RULING_V62.json'
        purpose.parent.mkdir(parents=True, exist_ok=True)
        if not purpose.exists():
            purpose.write_bytes(Path(row['purpose_ruling']['path']).read_bytes())
        for kind in ('calibration', 'fresh_audit'):
            relative = Path(kind + '_jobs/S1') / did / ('auditor.' + C35.name + '.md')
            job = relocate_job(C35 / relative, FILTERED / relative, C35, FILTERED)
            old = load_job(C35 / relative)
            provenance.append(dict(original=binding(old.job_path), relocated=binding(job.job_path),
                                   sample=job.contract['sample'], seed=job.contract['seed'],
                                   scientific_bindings_unchanged=True))
            if kind == 'calibration':
                output = Path(job.output_path); output.parent.mkdir(parents=True, exist_ok=True)
                if not output.exists(): output.write_bytes(Path(old.output_path).read_bytes())
                acceptance = read(Path(old.output_dir) / 'CALIBRATION_HUB_ACCEPTANCE.json')
                acceptance.update(calibration_job_sha256=sha(job.job_path),
                                  purpose_ruling_path=str(purpose), purpose_ruling_sha256=sha(purpose),
                                  original_completed_job=binding(old.job_path), original_output=binding(old.output_path),
                                  relocated_copy_is_not_a_new_dispatch=True)
                ensure_once(Path(job.output_dir) / 'CALIBRATION_HUB_ACCEPTANCE.json', acceptance)
            else:
                jobs.append(binding(job.job_path))
    ensure_once(FILTERED / 'JOBS_MANIFEST.json', dict(jobs=jobs, model_calls=0))
    ensure_once(COLLECTION / 'FILTERED_JOB_PROVENANCE.json', dict(jobs=provenance))
    audit.configure(IntakeConfig.from_existing(FILTERED)); audit.install()
    fresh_gate(FILTERED, validate_audit_clock=audit.validate_audit_clock)
    return dict(root=str(FILTERED), jobs=len(eligible), fresh_gate='PASS', model_calls=0)


def release_c36():
    from . import c36_intake as historical
    from scripts.l2_pipeline.connectors import outcome_v7
    parked = REPO / 'data/l2_pipeline/runs/run7/v62_official_history/jobs/v9_20261009/c10/fresh_audit_jobs/c12/work/run7_minor_abortion_parental_involvement/v13/PARKED_C12.json'
    terminal_path = REPO / 'data/l2_pipeline/registry/TERMINAL_RUNS.json'
    ubc = [r for r in read(terminal_path)['runs'] if r['design_id'] == 'run8_universal_background_check'][-1]
    decisions = [
        dict(policy_id='cspp:guncontrol_opencarry', duplicate=False, reason='Open carry regulates public carrying; concealed carry, purchase and private-sale checks regulate distinct constructs.'),
        dict(policy_id='cspp:w_abortion_parental_notice_1983_2014', duplicate=False, retry_of='run7_minor_abortion_parental_involvement', parked=binding(parked), reason='Prior design is PARKED, not terminal/in flight. Retry uses codebook-locked V10 CSPP annual-policy source instead of the prior official-history reconstruction. Known interruptions remain retained evidence and must pass fresh S1; no legal certification granted.'),
        dict(policy_id='cspp:w_guncontrol_bc_dealers', duplicate=False, reason='Dealer-sale state background-check requirements are distinct from private-sale UBC and gun-show checks; handguns/long guns are threshold versions of the same dealer construct.'),
        dict(policy_id='cspp:w_guncontrol_bc_privatesales', duplicate=True, duplicate_of=ubc['design_id'], terminal_status=ubc['status'], terminal_registry=binding(terminal_path), reason='Hub explicitly identifies private-sale checks as duplicating completed UBC. Same purchase-screening policy and first-order firearm-death construct; completed UBC has all-intent mortality primary and homicide secondary, so selecting its homicide component does not create a new design. Handgun/long-gun versions likewise do not create a new construct.')]
    ensure_once(C36 / 'c37_wave1/DUPLICATE_DECISIONS.json', dict(rows=decisions,
                rule='same policy construct + same first-order outcome as terminal or in-flight; PARKED may retry via validated source'))
    previous = read(C36 / 'INTAKE.json')
    candidates = [r for r in previous['rows'] if r.get('geometry', {}).get('passed')
                  and r['mapping']['connector_exists']]
    if {r['policy_id'] for r in candidates} != {r['policy_id'] for r in decisions}:
        raise ValueError('C36 candidate identities changed')
    by_policy = {d['policy_id']: d for d in decisions}
    scheduled = order_waves([r for r in candidates if not by_policy[r['policy_id']]['duplicate']])
    # Keep historical code and its input-binding bytes; bind the new version separately.
    report = copy.deepcopy(previous)
    report['rows'] = scheduled
    for r in scheduled:
        r['duplicate_review'] = by_policy[r['policy_id']]
        r['remaining_family_capacity'] = 6
        r['family_cap_basis'] = 'per_wave_ordering_only; prior waves do not consume capacity'
    report['summary'] = dict(catalog_variables=len(scheduled), emitted=sum(r['emit_eligible'] for r in scheduled),
                            candidate_count=4, duplicates=1, cap_excluded=0,
                            families=dict(collections.Counter(r['mapping']['family'] for r in scheduled)))
    report['c37_supersedes'] = binding(C36 / 'INTAKE.json')
    report['input_bindings'] += [binding(Path(__file__)), binding(C36 / 'c37_wave1/DUPLICATE_DECISIONS.json')]
    evidence = ensure_once(WAVE / 'INTAKE.json', report)
    # Reuse immutable V10 source and imports, while writing only a new prospective root.
    catalog = read(C36 / 'CATALOG_V10.json')
    ensure_once(WAVE / 'CATALOG_V10.json', catalog)
    config = IntakeConfig(WAVE, 361010, 362010, 'c36')
    # C36's original prior inventory includes run7's official-history packets.
    historical.base.configure(IntakeConfig(C36, 361010, 362010, 'c36'),
                              catalog=C36 / 'CATALOG_V10.json', screening_evidence=historical.SCREEN)
    priors = historical.base.prior_packets()
    audit.configure(config); audit.install(); outcome_v7.register()
    panel = catalog['panels'][0]
    jobs = []
    for r in scheduled:
        if not r['emit_eligible']: continue
        v = next(v for v in catalog['variables'] if v['policy_id'] == r['policy_id'])
        imported = read(r['import_binding']['path'])
        ensure_once(WAVE / 'imports' / r['design_id'] / 'PANEL_IMPORT.json', imported)
        jobs += emit_v10(r, v, panel, imported, evidence,
                        priors.get(r['policy_id'], dict(states=set(), packets=[])))
    ensure_once(WAVE / 'JOBS_MANIFEST.json', dict(jobs=[binding(p) for p in jobs],
                fresh_audit_root=str(WAVE / 'fresh_audit_jobs'),
                fresh_dispatch_requires='V6.2 ACCEPT + confirmed V8b calibration + independent direction', model_calls=0))
    for kind in ('s0', 'calibration', 'fresh_audit'):
        ensure_once(WAVE / (kind + '_jobs') / 'ROOT_MANIFEST.json', dict(emitted_designs=3,
                    status='BLOCKED_PENDING_CALIBRATION' if kind == 'fresh_audit' else 'PREPARED', model_calls=0))
    return dict(root=str(WAVE), jobs=len(jobs), designs=3, duplicates=1, cap_excluded=0, model_calls=0)


def emit_v10(row, variable, panel, imported, evidence, priors):
    """Versioned card emitter: all scientific inputs inherit locked C36 metadata."""
    from scripts.l2_pipeline.agents.purpose import emit_purpose_verification
    from scripts.l2_pipeline.contracts import DesignCard, StageContext
    did, m = row['design_id'], row['mapping']
    cfg = dict(source_type='research_panel', policy_id=variable['policy_id'], predicate=variable['definition'],
               window=row['source_window'], universe=imported['units'], catalog_path=str(WAVE / 'CATALOG_V10.json'),
               catalog_sha256=sha(WAVE / 'CATALOG_V10.json'), panel_path=panel['path'], panel_sha256=panel['sha256'],
               threshold_path=variable['threshold_declaration']['path'], threshold_sha256=variable['threshold_declaration']['sha256'],
               seed=362010, transition_year_drop=True)
    card = dict(design_id=did, version=WAVE.name, policy_family=m['family'], mechanism=variable['definition'], track='DiD', unit='state',
                outcome_family=m['target'], treatment=cfg, transition_year_drop=True,
                population=dict(target=m['population'], filters=m['population_filters']),
                first_order=dict(target_outcome=m['series_id'], target_population=m['population'], evidence_status='pending',
                                 purpose_rule='V6.2', purpose_basis='PENDING', statute_refs=[], literature_refs=[]),
                outcome=dict(connector=m['connector'], series_id=m['series_id'], measure=m['series_id'], measure_id=m['series_id'],
                             frequency=m['frequency'], period_years=row['geometry']['outcome_retained_years'],
                             outcome_scale='rate', population_filters=m['population_filters'], source_validation_required=True),
                intake_status='PROSPECTIVE_UNREGISTERED', execution_authorized=False,
                selection_rule='family_cap6_per_wave_then_screen_policy_id; overflow_next_wave',
                c37_lineage=dict(original_intake=binding(C36 / 'INTAKE.json'), duplicate_review=row['duplicate_review']))
    for key in ('paths', 'source_sha256', 'outcome_unit', 'license', 'analysis_only', 'republication_prohibited',
                'catalog_path', 'catalog_sha256', 'support_path', 'support_sha256'):
        if key in m: card['outcome'][key] = m[key]
    cp = ensure_once(WAVE / 'cards' / (did + '.json'), card)
    ctx = StageContext(DesignCard.from_mapping(card, cp), WAVE / 'purpose_work', REPO)
    jobs = [emit_purpose_verification(ctx, WAVE / 's0_jobs').job_path]
    ip = WAVE / 'imports' / did / 'PANEL_IMPORT.json'
    source = dict(contract=dict(card_path=str(cp), bound_inputs=[binding(ip)]))
    for kind in ('calibration', 'fresh_audit'):
        jobs.append(str(audit.emit_job(row, source, row['sample_plan'], kind, WAVE,
                                      evidence_path=evidence, prior_packets=priors['packets'])))
    return jobs


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('action', choices=['collect', 'release', 'dispatch'])
    args, rest = parser.parse_known_args(argv)
    if args.action == 'dispatch':
        from scripts.l2_pipeline.connectors import outcome_v7
        from .dispatch import main as dispatch
        outcome_v7.register()
        return dispatch(rest)
    result = filter_c35(collect_c35()) if args.action == 'collect' else release_c36()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
