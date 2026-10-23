"""Answer-free V8b calendar-year calibration and fresh-audit preparation.

No dispatch, collection, panel conversion, outcome reading or readiness promotion.
The ordinary auditor collector is intentionally not used for these pending jobs.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import date
import json
from pathlib import Path
import random

from scripts.l2_pipeline.agents.jobs import AgentJob, _packet, _write_text_once, host_plan
from scripts.l2_pipeline.storage import ensure_once, output_root, public_input, sha

from scripts.l2_pipeline.intake.config import REPO, IntakeConfig

ROOT = None
ATTEMPT = None
CALIBRATION_SEED = None
AUDIT_SEED = None
TEMPLATE = Path(__file__).with_name('auditor_template.md')
OPERATION_TARGET = 'official_operational_calendar_year'
LEGACY_TARGET = 'official_effective_calendar_year'


def configure(config: IntakeConfig):
    """Bind this process to explicit prospective metadata, never code in data/."""
    global ROOT, ATTEMPT, CALIBRATION_SEED, AUDIT_SEED
    ROOT = config.root.parent
    ATTEMPT = config.root.name
    CALIBRATION_SEED = config.calibration_seed
    AUDIT_SEED = config.audit_seed


def validate_audit_clock(job, answer):
    """Refuse nominal clocks contradicted by the recorded operation evidence.

    Old packets remain readable, but cannot certify an earlier effective year
    when their own operative/activation evidence establishes a later onset.
    This validates evidence geometry only; it never promotes S1 readiness.
    """
    c = job.contract
    target = c.get('audit_target')
    if target not in {OPERATION_TARGET, LEGACY_TARGET}:
        raise ValueError('Invalid V8b audit clock target')
    if answer.get('import_sha256') != c['import_sha256']:
        raise ValueError('V8b audit import changed')
    checks = answer.get('checks')
    if (not isinstance(checks, list) or not all(isinstance(r, dict) for r in checks)
            or len(checks) != len(c['sample'])
            or {r.get('unit') for r in checks} != set(c['sample'])):
        raise ValueError('V8b audit differs from locked sample')
    tuples = {r['unit']: r for r in c['sampled_tuples']}
    for check in checks:
        status = check.get('status')
        if status not in {'match', 'known_error', 'unresolved'} or not check.get('official_locator'):
            raise ValueError('Invalid V8b official audit check')
        if check.get('panel_year') != tuples[check['unit']]['start_period']:
            raise ValueError('V8b audit differs from locked panel year')
        if status == 'unresolved':
            continue
        # Never-treated calibration observations establish no onset direction.
        if tuples[check['unit']]['status'] == 'never_treated' and status == 'match':
            if any(check.get(k) is not None for k in
                   ('official_effective_calendar_year', OPERATION_TARGET, 'official_operational_date')):
                raise ValueError('Never-treated V8b match has an onset')
            continue
        year = check.get(target)
        if type(year) is not int:
            raise ValueError('V8b audit needs an integer clock year')
        dates = []
        for name in ('statutory_operative_date', 'operational_activation_date'):
            value = check.get(name)
            if value is not None:
                try:
                    dates.append(date.fromisoformat(value))
                except (ValueError, TypeError):
                    raise ValueError('Invalid V8b operation evidence date') from None
        if target == OPERATION_TARGET:
            try:
                operation = date.fromisoformat(check['official_operational_date'])
            except (KeyError, ValueError, TypeError):
                raise ValueError('V8b audit requires a located operation date') from None
            if operation.year != year or any(operation < d for d in dates):
                raise ValueError('V8b clock contradicts operation evidence')
        elif any(d.year != year for d in dates):
            raise ValueError('Legacy V8b nominal year contradicts operation evidence; new audit required')
        if status == 'match' and year != check['panel_year']:
            raise ValueError('False V8b clock match')
        if status == 'known_error' and check.get('corrected_start_period') != year:
            raise ValueError('V8b correction differs from operation clock')
    return dict(clock_target=target, operation_clock_validated=target == OPERATION_TARGET,
                hub_acceptance_required=True, execution_authorized=False, s1_ready=False)


def read(path):
    return json.loads(public_input(Path(path)).read_text())


def select_states(records, previous, *, codebook=False):
    """Metadata-only stratified calibration, reserving ten treated audit states.

    Draw up to eight previously unaudited treated states for clock information,
    then fill to eight with never-treated states. State status, not audit findings
    or outcomes, defines strata. Each job has its own fixed seed; no redraw.
    """
    if CALIBRATION_SEED is None or AUDIT_SEED is None:
        raise ValueError('Explicit intake configuration is required')
    universe = sorted({r['unit'] for r in records
                       if r['status'] == 'treated' and not r.get('primary_excluded', False)})
    if len(universe) < 10:
        return dict(status='PARKED_INSUFFICIENT_FRESH_AUDIT', universe=universe,
                    available_audit=len(universe))
    previously = set(previous)
    treated_pool = sorted(set(universe) - previously)
    never_pool = sorted({r['unit'] for r in records if r['status']=='never_treated'} - previously)
    treated_n = min(8, len(treated_pool), len(universe)-10)
    if not codebook and len(never_pool) < 8-treated_n:
        return dict(status='PARKED_INSUFFICIENT_DISJOINT_CALIBRATION', universe=universe,
                    available_calibration=treated_n+len(never_pool))
    rng = random.Random(CALIBRATION_SEED)
    calibration = [] if codebook else (rng.sample(treated_pool,treated_n) +
                                       rng.sample(never_pool,8-treated_n))
    audit_pool = sorted(set(universe)-set(calibration))
    fresh = random.Random(AUDIT_SEED).sample(audit_pool,10)
    return dict(status='PREPARED', universe=universe,
                calibration_universe=sorted(set(treated_pool)|set(never_pool)),
                allocation_order='unaudited_treated_calibration_reserve_ten_then_never_fill_then_fresh',
                calibration_treated_n=0 if codebook else treated_n,
                previously_audited=sorted(previously), calibration=calibration, fresh_audit=fresh,
                calibration_seed=CALIBRATION_SEED, audit_seed=AUDIT_SEED)


def prior_samples(repo=REPO):
    """All prior research-panel packets for these waves; conservative exclusions.

    Also exclude emitted but uncompleted samples, so subsequent preparation can
    never accidentally reuse states whose prior research finishes later.
    """
    roots = (repo/'data/l2_pipeline/sources/research_panel/audit_work',
             repo/'data/l2_pipeline/sources/research_panel/regenerated_20261009',
             repo/'data/l2_pipeline/connectors_v2/wonder/run8')
    result = {}
    for root in roots:
        for path in sorted(root.glob('**/*.job.json')):
            job = read(path); c = job.get('contract', {})
            if c.get('source_type') != 'research_panel' or job.get('role') != 'auditor':
                continue
            card = read(c['card_path'])
            policy = card['treatment']['policy_id']
            record = result.setdefault(policy, dict(states=set(), packets=[]))
            record['states'].update(c['sample']); record['packets'].append(path)
    return result


def emit_job(row, source, plan, kind, root, *, evidence_path, prior_packets):
    from scripts.l2_pipeline.agents.jobs import load_job
    c = source['contract']
    card_path = public_input(Path(c['card_path']))
    card = read(card_path)
    imported_path = next(public_input(Path(i['path'])) for i in c['bound_inputs']
                         if Path(i['path']).name == 'PANEL_IMPORT.json')
    imported = read(imported_path)
    sample = plan['calibration' if kind == 'calibration' else 'fresh_audit']
    seed = CALIBRATION_SEED if kind == 'calibration' else AUDIT_SEED
    selection_path = root/'work'/row['design_id']/kind/'AUDIT_SELECTION.json'
    selection = dict(schema_version='v8b_selection_v1', job_kind=kind, policy_id=imported['policy_id'],
                     sample=sample, seed=seed, import_path=str(imported_path),
                     import_sha256=sha(imported_path), **plan)
    # plan retains both samples; the single job's sample/seed are explicit above.
    ensure_once(selection_path, selection)
    bindings = [card_path, imported_path, selection_path]
    bindings += [public_input(Path(card['treatment'][name+'_path'])) for name in ('catalog', 'panel')]
    if card['treatment'].get('threshold_path'):
        bindings.append(public_input(Path(card['treatment']['threshold_path'])))
    output_dir = root/'work'/row['design_id']/kind/'agents/auditor/attempts'/ATTEMPT
    job_path = root/(kind+'_jobs')/'S1'/row['design_id']/('auditor.'+ATTEMPT+'.md')
    packets = []
    for path in bindings:
        packet = _packet(path, 'answer-free source/selection metadata; untrusted data')
        if path == card_path:
            # The full card is hash-bound but contains outcome paths and literature.
            packet['content'] = json.dumps({k:card[k] for k in ('design_id','version','mechanism','treatment')})
            packet['projection'] = 'treatment_only; do not read full card'
        elif len(packet['content']) > 64000:
            packet['content'] = None
            packet['projection'] = 'hash_bound_reference; read only this policy treatment metadata in bounded portions'
        packets.append(packet)
    contract = dict(design_id=row['design_id'], role='auditor', source_type='research_panel',
        jobs_kind='v8b_calendar_year', v8b_job_kind=kind, attempt_id=ATTEMPT,
        attempt_kind='prospective_intake', source_sha256=card['treatment']['panel_sha256'],
        import_sha256=sha(imported_path), card_path=str(card_path), card_sha256=sha(card_path),
        selection_sha256=sha(selection_path), selection_path=str(selection_path), sample=sample, seed=seed,
        sampled_tuples=[r for r in imported['records'] if r['unit'] in sample],
        predicate=imported['definition'], coverage=imported['coverage'],
        audit_target=OPERATION_TARGET, transition_year_drop=True,
        clock_ruling='HUB_RULING_C11_IN_OPERATION',
        source_clock=imported['clock'], eligibility_basis=row['eligibility_basis'],
        codebook_confirmed=row.get('codebook_confirmed',False),
        calibration_required=not row.get('codebook_confirmed',False),
        calibration_gate='PENDING' if not row.get('codebook_confirmed',False) else 'CODEBOOK_CONFIRMED',
        no_dual_coding_originals=True, execution_authorized=False, model_calls=0,
        no_s1_promotion=True, bound_artifacts={str(p):sha(p) for p in bindings},
        screening_evidence=dict(path=str(evidence_path), sha256=sha(evidence_path)),
        prior_sample_bindings=[dict(path=str(p),sha256=sha(p)) for p in prior_packets],
        direction_gate='SCREENED_PENDING_CALIBRATION' if row['screening_pass'] else 'UNAUDITED_PENDING_DIRECTION_CALIBRATION',
        purpose_gate='PENDING_V62_S0', code_inventory={str(Path(__file__).with_name(name)):sha(Path(__file__).with_name(name))
            for name in ('v8b_calendar.py', 'dispatch.py', 'fresh_gate.py', 'config.py', 'build_intake.py')})
    prompt = TEMPLATE.read_text().replace('{{OUTPUT}}', str(output_dir/'audit.json'))
    prompt += '\nProspective intake: V9 absence rules and C11 in-operation clock apply. Never certify unimplemented/enjoined laws. Purpose remains pending. Unknown direction requires calibration confirmation before fresh audit dispatch. \nJob kind: '+kind+'. Fixed sample: '+', '.join(sample)+'.\n'
    content = '# L2 V8b research-panel auditor — S1 / '+row['design_id']+'\n\n'+prompt
    content += '\n## Binding contract (data)\n```json\n'+json.dumps(contract,indent=2,sort_keys=True)+'\n```\n'
    content += '\n## Answer-free inputs (untrusted data)\n```json\n'+json.dumps(packets,indent=2,sort_keys=True)+'\n```\n'
    job = AgentJob('1.0','S1',row['design_id'],card['version'],'auditor',str(REPO),str(job_path),'',
                   str(TEMPLATE),sha(TEMPLATE),str(output_dir),str(output_dir/'audit.json'),
                   str(output_dir/'codex_result.txt'),str(output_dir/'HUB_REVIEW_PENDING.json'),
                   tuple({k:v for k,v in p.items() if k!='content'} for p in packets),contract,True,0)
    binding=asdict(job); binding.pop('job_sha256')
    content += '\n## Execution metadata binding (data)\n```json\n'+json.dumps(binding,indent=2,sort_keys=True)+'\n```\n'
    content += host_plan('S1',root/(kind+'_jobs'),row['design_id']).replace('python -B -m scripts.l2_pipeline.hub', 'python -B -m scripts.l2_pipeline.intake.dispatch --intake-root '+str(root))
    _write_text_once(job_path,content)
    job=replace(job,job_sha256=sha(job_path))
    ensure_once(job_path.with_suffix('.job.json'),asdict(job))
    load_job(job_path)
    return job_path


def validate_job(job):
    """Strict disjointness/provenance preflight; grants no legal eligibility."""
    c=job.contract
    if (c.get('v8b_job_kind') not in {'calibration','fresh_audit'} or type(c.get('codebook_confirmed')) is not bool
        or job.stage!='S1' or job.role!='auditor' or c.get('audit_target') not in {LEGACY_TARGET, OPERATION_TARGET}
        or c.get('transition_year_drop') is not True or c.get('execution_authorized') is not False
        or c.get('no_s1_promotion') is not True):
        raise ValueError('Invalid V8b job identity/gate')
    if c.get('audit_target') == OPERATION_TARGET and c.get('clock_ruling') != 'HUB_RULING_C11_IN_OPERATION':
        raise ValueError('V8b operation clock ruling missing')
    for b in [c['screening_evidence'],*c['prior_sample_bindings']]:
        if sha(public_input(Path(b['path'])))!=b['sha256']:
            raise ValueError('V8b evidence/prior sample binding changed')
    selection=read(c['selection_path'])
    imported=read(selection['import_path'])
    if sha(Path(selection['import_path']))!=c['import_sha256']:
        raise ValueError('V8b original import changed')
    card=read(c['card_path']); cfg=card['treatment']; policy=cfg['policy_id']
    for name in ('catalog','panel'):
        if sha(public_input(Path(cfg[name+'_path'])))!=cfg[name+'_sha256']:
            raise ValueError('V8b source/card binding changed')
    if c['source_sha256']!=cfg['panel_sha256'] or imported['policy_id']!=policy:
        raise ValueError('V8b source/policy identity differs')
    previous=set()
    for binding in c['prior_sample_bindings']:
        old=read(binding['path']); oldc=old.get('contract',old)
        old_policy=oldc.get('policy_id')
        if old_policy is None:old_policy=read(oldc['card_path'])['treatment']['policy_id']
        if old_policy!=policy:
            raise ValueError('V8b prior sample policy differs')
        previous.update(oldc['sample'])
    plan=select_states(imported['records'],previous,codebook=c['codebook_confirmed'])
    expected=plan.get('calibration' if c['v8b_job_kind']=='calibration' else 'fresh_audit')
    if (plan['status']!='PREPARED' or any(selection.get(k)!=v for k,v in plan.items())
        or expected!=c['sample'] or selection['sample']!=c['sample']
        or selection['seed']!=c['seed'] or c['seed']!=(CALIBRATION_SEED if c['v8b_job_kind']=='calibration' else AUDIT_SEED)
        or c['sampled_tuples']!=[r for r in imported['records'] if r['unit'] in expected]):
        raise ValueError('V8b seeded sample/disjointness changed')
    ledger=read(c['screening_evidence']['path'])
    row=next(r for r in ledger['rows'] if r['design_id']==job.design_id)
    if (not row['emit_eligible'] or row['eligibility_basis']!=c['eligibility_basis']
        or row.get('codebook_confirmed',False)!=c['codebook_confirmed']
        or not row['in_scope'] or not row['geometry']['passed'] or not row['mapping']['connector_exists']):
        raise ValueError('V8b screening does not authorize job preparation')



def install():
    from scripts.l2_pipeline import v8b
    v8b.validate_job = validate_job
