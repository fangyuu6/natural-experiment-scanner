"""CLI-owned write-once preparation; signatures are explicit hub inputs.

Calibration converts only the registered common pre-adoption rectangle. All
other real outcomes remain opaque bytes. Production worlds/counts are fixed.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime
from .contracts import StageContext
from .storage import encoded, ensure_once, local, output_root, public_input, sha
from .freeze import ACTIONS, REQUIRED_READINESS, build_manifest, code_inventory, environment, require_verified, verify
from .predictions import lock_predictions


def hub_signer(ctx):
    path=ctx.design_dir/'REGISTRATION.json'
    if path.is_file(): return json.loads(path.read_text())['hub_signer']
    return ctx.card.payload.get('automation',{}).get('hub_signer','')


def check_tests(output, *, no_archival_fits=False):
    from concurrent.futures import ThreadPoolExecutor
    before=code_inventory()
    def run_mode(flags):
        cmd=[sys.executable,'-B',*flags,'-m','scripts.l2_pipeline.regression']+(['--no-archival-fits'] if no_archival_fits else [])
        start=time.perf_counter()
        p=subprocess.run(cmd,capture_output=True,text=True)
        return dict(command=cmd,exit_code=p.returncode,wall_seconds=time.perf_counter()-start,
            transcript=p.stdout+p.stderr)
    # Each suite owns its TemporaryDirectory fixtures; both processes must finish
    # before publishing evidence, including when either mode fails.
    with ThreadPoolExecutor(max_workers=2) as pool:
        records=list(pool.map(run_mode, ([], ['-O'])))
    if before!=code_inventory():raise ValueError('Code changed during regression checks')
    if any(r['exit_code'] for r in records):
        raise ValueError('Regression checks failed: '+''.join(r['transcript'] for r in records if r['exit_code']))
    value=dict(schema='l2_pipeline_test_evidence_v1',passed=True,runs=records,no_archival_fits=no_archival_fits,
        code_inventory=code_inventory())
    ensure_once(output,value)
    return dict(path=str(output),sha256=sha(output),wall_seconds=sum(r['wall_seconds'] for r in records))


def _check_evidence(path):
    value=json.loads(public_input(path).read_text())
    current=code_inventory()
    if (value.get('schema')!='l2_pipeline_test_evidence_v1' or value.get('passed') is not True
            or value.get('code_inventory')!=current or len(value.get('runs',[]))!=2
            or any(r.get('exit_code')!=0 for r in value['runs'])):
        raise ValueError('Missing/current regression evidence required before prep freeze')
    return value



def _test_evidence_path(root):
    """Select hash-bound refreshed evidence while preserving the original bytes."""
    selection = root/'TEST_EVIDENCE_SELECTION.json'
    if not selection.exists():
        return root/'TEST_EVIDENCE.json'
    record = json.loads(selection.read_text())
    path = local(root, record['path'])
    if (record.get('schema') != 'l2_test_evidence_selection_v1'
            or path.name != 'TEST_EVIDENCE_'+record['sha256']+'.json'
            or sha(path) != record['sha256']):
        raise ValueError('Selected test evidence binding changed')
    return path


def _retain_test_evidence(root, report):
    value = _check_evidence(report)
    canonical = root/'TEST_EVIDENCE.json'
    if not canonical.exists() or json.loads(canonical.read_text()) == value:
        ensure_once(canonical, value)
        return canonical
    # Existing seals never acquire new inputs. A refresh is allowed only for
    # unsealed preparation and is itself immutable and hash-bound.
    if (root/'FREEZE_PREP.json').exists() or (root/'FREEZE_FINAL.json').exists():
        raise ValueError('Sealed test evidence cannot be refreshed')
    digest = hashlib.sha256(encoded(value)+b"\n").hexdigest()
    path = ensure_once(root/('TEST_EVIDENCE_'+digest+'.json'), value)
    ensure_once(root/'TEST_EVIDENCE_SELECTION.json', dict(
        schema='l2_test_evidence_selection_v1', path=path.name, sha256=sha(path)))
    return _test_evidence_path(root)


def _review(ctx, attempt, signer=None):
    if not attempt or not all(c.isalnum() or c in '._-' for c in attempt):
        raise ValueError('Explicit safe draft-readiness review attempt required')
    path=local(ctx.design_dir,'REVIEW_DRAFT_'+attempt+'.json'); review=json.loads(path.read_text())
    from .stages.s6_execute import review_passed
    candidate=dict(review,review_scope='sealed_candidate')
    if review.get('review_scope')=='draft_readiness' and review_passed(candidate,sha(ctx.design_dir/'SPEC.json'),ctx.card.design_id):
        return review
    closure=local(ctx.design_dir,'DRAFT_RESOLUTION_'+attempt+'.json')
    if not closure.is_file():
        raise ValueError('Matching independent draft-readiness PASS or evidence-backed hub closure required')
    resolution=json.loads(closure.read_text())
    _validate_resolution(ctx,attempt,resolution)
    from . import rules as rules_module
    single=rules_module.at_least(rules_module.of_payload(ctx.card.payload,ctx.repo),2)   # REDESIGN R2: this is the only review
    # Under R2 `single_review_evidence` compares this closure's signer with REGISTRATION.json, inside the frozen code snapshot, after
    # calibration; refuse a differing signer here, while the design is still unfrozen (HUB_RULING_20261019_NOVEL_GATE Amendment 1).
    if single and resolution.get('hub_signer')!=(signer or hub_signer(ctx) or 'hub autopilot (standing authorization 2026-10-10)'):
        raise ValueError('Single-review closure signer differs from the registration signer')
    return dict(schema='l2_resolved_draft_readiness_v1',design_id=ctx.card.design_id,
        candidate_sha256=sha(ctx.design_dir/'SPEC.json'),review_scope='draft_readiness',
        status='RESOLVED_SINGLE_REVIEW' if single else 'HUB_RESOLVED_PENDING_SEALED_REVIEW',independent_verdict=review['verdict'],
        independent_review_path=str(path),independent_review_sha256=sha(path),
        resolution_path=str(closure),resolution_sha256=sha(closure),execution_authorized=False)


def finding_sha256(finding):
    return hashlib.sha256(encoded(finding)).hexdigest()


def _validate_resolution(ctx,attempt,value):
    """Check closures against original independent findings and current bytes."""
    path=local(ctx.design_dir,'REVIEW_DRAFT_'+attempt+'.json')
    review=json.loads(path.read_text())
    attestation={'wrote_implementation':False,'wrote_fixes':False,'real_outcome_associations_read':False}
    if (review.get('design_id')!=ctx.card.design_id or review.get('review_scope')!='draft_readiness'
            or review.get('independence_attestation')!=attestation
            or any(type(v) is not bool for v in review.get('independence_attestation',{}).values())
            or type(review.get('complete_sweeps')) is not int or review['complete_sweeps']!=1
            or type(review.get('fix_cycles')) is not int or not 0<=review['fix_cycles']<=2
            or review.get('verdict')!=('STOP' if review.get('must') else 'PASS')):
        raise ValueError('Matching independent draft review required for hub closure')
    source=value.get('source_spec',{})
    source_path=public_input(Path(source.get('path','')))
    if (not source_path.is_file() or source.get('sha256')!=sha(source_path)
            or source['sha256']!=review.get('candidate_sha256')):
        raise ValueError('Original reviewed specification lineage changed')
    from .predictions import primary_forecast_target
    original=json.loads(source_path.read_text());current=ctx.read('SPEC.json')
    unchanged=primary_forecast_target(original)==primary_forecast_target(current)
    if (value.get('schema')!='l2_draft_resolution_v1' or value.get('design_id')!=ctx.card.design_id
            or value.get('review_attempt')!=attempt or value.get('review_sha256')!=sha(path)
            or value.get('target_spec_sha256')!=sha(ctx.design_dir/'SPEC.json')
            or type(value.get('target_unchanged')) is not bool or value['target_unchanged']!=unchanged
            or not isinstance(value.get('hub_signer'),str) or not value['hub_signer'].strip()
            or value.get('execution_authorized') is not False
            or value.get('outcome_associations_read') is not False):
        raise ValueError('Hub closure identity/current target binding missing')
    findings={(bucket,f['id']):f for bucket in ('must','should') for f in review.get(bucket,[])}
    if len(findings)!=sum(len(review.get(bucket,[])) for bucket in ('must','should')):
        raise ValueError('Duplicate draft finding identifiers')
    records=value.get('resolutions')
    if not isinstance(records,list) or len(records)!=len(findings):
        raise ValueError('Every MUST and SHOULD needs an explicit closure')
    seen=set()
    for record in records:
        key=(record.get('bucket'),record.get('id'));finding=findings.get(key)
        if (key in seen or finding is None or record.get('finding_sha256')!=finding_sha256(finding)
                or record.get('status') not in ('resolved','declined')
                or (record['bucket']=='must' and record['status']!='resolved')
                or not isinstance(record.get('reason'),str) or not record['reason'].strip()
                or not isinstance(record.get('evidence'),list) or not record['evidence']):
            raise ValueError('Unresolved/invalid draft finding closure')
        seen.add(key)
        for evidence in record['evidence']:
            from .closure_evidence import validate
            try: validate(evidence,ctx.repo)
            except ValueError as exc: raise ValueError('Draft closure evidence changed or invalid: '+str(exc)) from exc
    return unchanged


def resolve_draft(ctx,args):
    if not args.signer or not args.resolution or not args.review_attempt:
        raise ValueError('Explicit hub, draft attempt and resolution record required')
    # Inputs already contain source review identity and every exact finding hash.
    value=json.loads(public_input(args.resolution).read_text())
    if value.get('hub_signer')!=args.signer:
        raise ValueError('Resolution signer differs from explicit hub')
    unchanged=_validate_resolution(ctx,args.review_attempt,value)
    path=local(ctx.design_dir,'DRAFT_RESOLUTION_'+args.review_attempt+'.json')
    ensure_once(path,value)
    return dict(path=str(path),sha256=sha(path),resolved_findings=len(value['resolutions']),
        primary_target_unchanged=unchanged,forecasts_must_be_superseded=not unchanged,execution_authorized=False)


def _inventory(ctx):
    root=ctx.design_dir
    roles={'CARD.json':'card','SPEC.json':'spec','TREATMENT.json':'treatment','SUPPORT.json':'support',
        'PANELS.json':'outcome','CODE.json':'code','ENVIRONMENT.json':'environment',
        'PREDICTIONS_LOCK.json':'predictions','INTERVAL_PLAN.json':'interval_plan'}
    for name in ('C65_EXPORT_ANNOTATIONS.json','C65_PANEL_METADATA.json','SPEC_ADDENDUM_C64.json','SPEC_ADDENDUM_C70.json','SPEC_ADDENDUM_C83.json','PURPOSE_DIRECTION_C83.json','REGISTRATION.json','S1_IMPORT_PROVENANCE.json','SENSITIVITY_MANIFEST.json',
                 'REVIEW.json','TEST_EVIDENCE.json','FORECAST_IMPORT.json','RUN2_PREPARATION.json',
                 'SCIENTIFIC_CARD.json','S1_CODE_ARCHIVE.json','REVIEW_LIMITATIONS.json','C48_AUDIT_PREPARATION.json',
                 'FORECAST_SET_ANNOTATION.json','FORECAST_AUTHOR_ANNOTATION.json','DRY_LOAD_DRAFT.json',
                 'FORECAST_SLOTS.json','SUPPORT_C79.json','PANELS_C79.json','PURPOSE_LABEL.json','RULES_SNAPSHOT.json'):
        if (root/name).is_file(): roles[name]='verification'
    for path in sorted(root.glob('DRAFT_RESOLUTION_*.json')): roles[path.name]='verification'
    for path in sorted(root.glob('CLASS_TABLE_ACCEPTED_*.json')): roles[path.name]='verification'
    for path in sorted(root.glob('REVIEW_DRAFT_*.json')): roles[path.name]='verification'
    for path in sorted((root/'closure_evidence').rglob('*')):
        if path.is_file(): roles[str(path.relative_to(root))]='verification'
    for path in sorted(root.glob('TEST_EVIDENCE_*.json')): roles[path.name]='verification'
    for path in sorted((root/'panels').glob('*/*.csv')):
        roles[str(path.relative_to(root))]='outcome' if path.name=='panel.csv' else 'support'
    if (root/'SECONDARY_PANELS.json').is_file():
        roles['SECONDARY_PANELS.json']='verification'
    for path in sorted((root/'secondary_panels').glob('*/*')):
        if path.is_file() and path.name in ('panel.csv','metadata.csv','SUPPORT.json','CONSTRUCTION.json','VARIANT_CARD.json'):
            roles[str(path.relative_to(root))]='outcome' if path.name=='panel.csv' else 'support'
    for path in sorted((root/'forecast_packs').glob('*.json')):roles[str(path.relative_to(root))]='predictions'
    lock=json.loads((root/'PREDICTIONS_LOCK.json').read_text())
    roles.update({p:'predictions' for p in (*lock['files'],*lock['original_files'])})
    return roles


def _snapshot_rules(ctx):
    """REDESIGN R1: the exact rules bytes this design ran under are frozen with it (RULES_SNAPSHOT.json)."""
    from . import rules
    pin=rules.pinned(ctx.card.payload)
    if pin is None:return None
    value=rules.of_payload(ctx.card.payload,ctx.repo)
    path=ensure_once(ctx.design_dir/'RULES_SNAPSHOT.json',value)
    if sha(path)!=pin['sha256']:raise ValueError('Rules snapshot differs from the pinned rules file')
    return path


def lock(ctx):
    from .forecast_slots import roster,validate_contract
    _snapshot_rules(ctx)
    path=ctx.design_dir/'PREDICTIONS_LOCK.json'
    if not path.exists():
        personas=roster(ctx)
        validate_contract(ctx.design_dir,personas)
        lock_predictions(ctx.design_dir,personas,ctx.card.design_id,sha(ctx.design_dir/'SPEC.json'))
    value=json.loads(path.read_text())
    if len(value['files'])!=len(value['personas']) or not value.get('original_files'): raise ValueError('Collected originals required for every forecast')
    for p,h in {**value['files'],**value['original_files']}.items():
        if sha(local(ctx.design_dir,p))!=h: raise ValueError('Locked prediction changed')
    return dict(predictions=len(value['files']),path=str(path),sha256=sha(path))


def _retain_cards(ctx):
    """S0 may retain the effective audited card; keep its receipt bytes intact.

    C42/C48 overlays are verified by the caller's effective context. Both the
    original intake and that exact context are legitimate S0 inputs; an arbitrary
    third card is never accepted. SCIENTIFIC_CARD always binds the effective one.
    """
    from .cards import load_card
    raw=load_card(ctx.card.path).payload
    path=ctx.design_dir/'CARD.json'
    if path.exists():
        retained=json.loads(path.read_text())
        if retained!=raw and retained!=dict(ctx.card.payload):
            # A V3.1 file or the audit's AUDIT_RESOLUTIONS.json (both written at S1) change the effective card after S0 kept its CARD.json.
            if not any((ctx.design_dir/n).is_file() for n in ('C48_AUDIT_PREPARATION.json','AUDIT_RESOLUTIONS.json')):
                raise ValueError('Existing preparation artifact changed: '+str(path))
            from .run2_preparation import effective_context
            from .c42 import effective_context as c42_context
            base = effective_context(StageContext(load_card(ctx.card.path),ctx.workspace,ctx.repo))
            if retained not in (dict(c42_context(base).card.payload),dict(c42_context(base,resolutions=False).card.payload)):
                raise ValueError('Existing preparation artifact changed: '+str(path))
    else:
        ensure_once(path,raw)
    from .connectors.rates import prospective_card
    scientific=prospective_card(ctx.card.payload)
    spec=ctx.read('SPEC.json') if (ctx.design_dir/'SPEC.json').exists() else None
    if spec and spec.get('comparison_group'):
        from .v11_v12 import register_card
        scientific=register_card(scientific,spec['comparison_group'])
        # Derive only the already registered metadata group, never arbitrary SPEC edits.
        if scientific['analysis']!=spec['analysis'] or scientific['treatment']!=spec['treatment']:
            raise ValueError('Scientific card normalization differs from registered specification')
    from .hygiene import scientific_card
    scientific=scientific_card(scientific,ctx.design_dir)
    ensure_once(ctx.design_dir/'SCIENTIFIC_CARD.json',scientific)


def prep(ctx,args,expected):
    if not args.signer or not args.test_report: raise ValueError('Named hub and regression evidence required')
    from .c83_l3 import retain as retain_c83
    retain_c83(ctx)
    from .c64 import retain
    retain(ctx)
    from .c65 import retain as retain_c65
    retain_c65(ctx)
    lock(ctx)
    from .ops.forecast_authors import retain as retain_authors
    retain_authors(ctx)
    _retain_test_evidence(ctx.design_dir,args.test_report)
    ensure_once(ctx.design_dir/'REVIEW.json',_review(ctx,args.review_attempt,args.signer))
    _retain_cards(ctx)
    ensure_once(ctx.design_dir/'REGISTRATION.json',dict(schema='l2_registration_authority_v1',hub_signer=args.signer,
        expected_designs=expected,card_path=str(ctx.card.path.resolve()),card_sha256=sha(ctx.card.path),
        readiness_review_attempt=args.review_attempt))
    from .engine.inference import METHODS
    ensure_once(ctx.design_dir/'INTERVAL_PLAN.json',dict(schema='l2_interval_plan_v1',expected_designs=expected,
        candidate_order=list(METHODS),certification_path='INTERVAL_CERTIFICATION.json'))
    ensure_once(ctx.design_dir/'ENVIRONMENT.json',environment())
    ensure_once(ctx.design_dir/'CODE.json',code_inventory())
    if not (ctx.design_dir/'FREEZE_PREP.json').exists():
        build_manifest(ctx.design_dir,'prep',_inventory(ctx),list(json.loads((ctx.design_dir/'PREDICTIONS_LOCK.json').read_text())['personas']),args.signer)
    check=verify(ctx.design_dir,'prep',args.signer,require_approval=False)
    if not check['ok']: raise ValueError(str(check['issues']))
    return check


def approve(ctx,args,stage):
    from . import snapshots
    frozen = snapshots.manifest_for(ctx.design_dir, stage)
    if frozen and snapshots.active() is None:
        return snapshots.call(ctx.design_dir, frozen, 'approve', dict(
            stage=stage,
            args=dict(signer=args.signer, signed_at=args.signed_at)))
    signer=hub_signer(ctx)
    if not args.signer or args.signer!=signer or not args.signed_at: raise ValueError('Exact named hub and explicit UTC signature timestamp required')
    stamp=datetime.fromisoformat(args.signed_at.replace('Z','+00:00'))
    if stamp.utcoffset() is None or stamp.utcoffset().total_seconds()!=0: raise ValueError('Signature timestamp must be UTC')
    check=verify(ctx.design_dir,stage,signer,require_approval=False)
    if not check['ok']: raise ValueError(str(check['issues']))
    value=dict(status='APPROVED',signed_by=signer,signed_at_utc=args.signed_at,
        manifest_path='FREEZE_'+stage.upper()+'.json',manifest_sha256=check['manifest_sha256'],
        approved_actions=ACTIONS[stage],execution_authorized=stage=='final')
    if stage=='final':
        from .stages.s6_execute import sealed_review_evidence
        value.update(sealed_review_evidence(ctx.design_dir,check['manifest_sha256'],ctx.card.design_id,signer))
        from .dry_load import require_pass
        value['dry_load_sha256']=require_pass(ctx.design_dir,check['manifest_sha256'],ctx.card.design_id)
    ensure_once(ctx.design_dir/('APPROVAL_'+stage.upper()+'.json'),value)
    return require_verified(ctx.design_dir,stage,signer)


def dry_load(ctx):
    from .dry_load import run
    return run(ctx.design_dir,hub_signer(ctx))


def calibration(ctx):
    from . import snapshots
    frozen = snapshots.manifest_for(ctx.design_dir, 'prep')
    if frozen and snapshots.active() is None:
        return snapshots.call(ctx.design_dir, frozen, 'calibrate', dict(context=snapshots.context_payload(ctx)))
    require_verified(ctx.design_dir,'prep',hub_signer(ctx))
    root=ctx.design_dir
    # Retain completed deterministic substeps; never retune/reseed a failed gate.
    if (root/'DIAGNOSTIC_CALIBRATION.json').exists() and (root/'INTERVAL_EVALUATION.json').exists():
        return dict(resumed=True)
    from .registered import numerical_declaration, registered_panel_keys, validate_calibration
    from .engine.design import build_design, structural_rows, treatment_rows
    from .engine.panels import projected_keys
    from .engine.calibration import blind_anchor, calibration_operators, calibrate_synthetic, evaluate_intervals
    from .engine.ri import permutation_reference
    from .engine.inference import design_binding
    from .engine.runner import registered_multiplier_count
    support=json.loads((root/'SUPPORT.json').read_text())['variants'][0]
    if support['variant']!='primary' or not support.get('support_pass',support.get('passed')):
        raise ValueError('Supported primary required')
    declaration=numerical_declaration(ctx.card.payload,support,json.loads((root/'TREATMENT.json').read_text()),'primary')
    rows=[(u,t,0.,None) for u,t in sorted(registered_panel_keys(declaration,root/'panels/primary/metadata.csv'))]
    design=build_design(declaration,rows)
    if design['cells']!=support['cell_inventory']: raise ValueError('Calibration/metadata cell inventory differs')
    op=calibration_operators(declaration,structural_rows(declaration,rows),treatment_rows(declaration))
    if design_binding(design)!=design_binding(op['design']): raise ValueError('Calibration/execution operator differs')
    anchor_units=design['units']; years=support['anchor_years']
    if len(anchor_units)!=support['anchor_unit_n']: raise ValueError('Anchor unit inventory differs')
    # Translate clean profiles back to calendar labels, including earlier actual exposure.
    calendar=declaration.get('period_years')
    to_year=lambda t: calendar[t] if calendar else t
    dirty=[to_year(t) for u in anchor_units for t in design['periods'] if not design['exposure'][u][t]['clean']]
    earliest=min(dirty) if dirty else max(years)+1
    field='employment_percent' if ctx.card.payload['outcome'].get('connector')=='cps' else 'outcome_value'
    with (root/'panels/primary/panel.csv').open() as stream:header=stream.readline().strip().split(',')
    if 'employment_percent' in header: field='employment_percent'
    anchor=blind_anchor(root/'panels/primary/panel.csv',units=anchor_units,periods=years,
        earliest_operative_period=earliest,value_column=field,
        lower_bound=0 if field=='employment_percent' else None,upper_bound=100 if field=='employment_percent' else None)
    ensure_once(root/'ANCHOR.json',anchor)
    ri=permutation_reference(declaration,design,{k:0. for k in design['mask']},count=999)
    from .gate_relax import floor_from_declaration, export_block
    r31 = {} if floor_from_declaration(declaration) is None else dict(ri_floor=export_block(ri['feasibility'], ri['feasibility_199']))
    ensure_once(root/'RI_FEASIBILITY.json',dict(feasibility=ri['feasibility'],seed=ri['seed'],event_times=ri['event_times'],
        design_binding=design_binding(design),actual_data_estimation_calls=0,treated_post_outcome_fields_parsed=0,**r31))
    if not (root/'DIAGNOSTIC_CALIBRATION.json').exists():
        record=calibrate_synthetic(ctx.card.design_id,op,anchor['value'])
        ensure_once(root/'DIAGNOSTIC_CALIBRATION.json',record)
        validate_calibration(record,design)
    if not (root/'INTERVAL_EVALUATION.json').exists():
        ensure_once(root/'INTERVAL_EVALUATION.json',evaluate_intervals(ctx.card.design_id,op,anchor['value'],
            B=registered_multiplier_count(ctx.card.payload)))
    return dict(anchor_sha256=sha(root/'ANCHOR.json'),ri_feasible_fraction=ri['feasibility']['feasible_fraction'],
        actual_data_estimation_calls=0,treated_post_outcome_fields_parsed=0)


def seal(contexts):
    from . import snapshots
    frozen = snapshots.manifest_for(contexts[0].design_dir, 'prep')
    if frozen and snapshots.active() is None:
        for ctx in contexts:
            other = snapshots.manifest_for(ctx.design_dir, 'prep')
            if other is None or other['code_inventory'] != frozen['code_inventory']:
                raise ValueError('Seal batch must share preparatory code bytes')
        return snapshots.call(contexts[0].design_dir, frozen, 'seal', dict(contexts=[snapshots.context_payload(ctx) for ctx in contexts]))
    from .engine.calibration import certify_intervals
    from .registered import validate_calibration
    expected=[c.card.design_id for c in contexts]
    cases={c.card.design_id:json.loads((c.design_dir/'INTERVAL_EVALUATION.json').read_text()) for c in contexts}
    from .gate_relax import R31_DESIGNS
    r31=[c.card.design_id for c in contexts if json.loads((c.design_dir/'RI_FEASIBILITY.json').read_text()).get('ri_floor')]
    cert=certify_intervals(cases,expected_designs=expected,ri_floor_designs=[d for d in r31 if d in R31_DESIGNS])
    diagnostic={c.card.design_id:json.loads((c.design_dir/'DIAGNOSTIC_CALIBRATION.json').read_text()) for c in contexts}
    for value in diagnostic.values(): validate_calibration(value)
    results=[]
    for ctx in contexts:
        root=ctx.design_dir;require_verified(root,'prep',hub_signer(ctx))
        if json.loads((root/'INTERVAL_PLAN.json').read_text())['expected_designs']!=expected: raise ValueError('Seal design inventory differs from prep plan')
        ensure_once(root/'INTERVAL_CERTIFICATION.json',cert)
        ensure_once(root/'CALIBRATION_GATES.json',dict(passed=True,cases=diagnostic))
        test_path=_test_evidence_path(root)
        tests=_check_evidence(test_path)
        proof_test='test_invented_parallel_parity_and_independent_reference'
        if any(not any(line.startswith(proof_test+' ') and line.endswith(' ... ok')
                       for line in run['transcript'].splitlines()) for run in tests['runs']):
            raise ValueError('Explicit invented parallel parity/independent reference evidence required')
        _review(ctx,json.loads((root/'REGISTRATION.json').read_text())['readiness_review_attempt'])
        readiness_review=json.loads((root/'REVIEW.json').read_text())
        from . import rules as rules_module
        single_review=rules_module.at_least(rules_module.of_payload(ctx.card.payload,ctx.repo),2)
        ensure_once(root/'FINAL_READINESS_EVIDENCE.json',dict(
            synthetic_tests_pass=dict(path=test_path.name,sha256=sha(test_path)),
            independent_review_pass=dict(path='REVIEW.json',sha256=sha(root/'REVIEW.json'),
                basis='independent_draft_pass' if readiness_review.get('verdict')=='PASS' else 'independent_findings_with_hub_evidence_closure',
                grants_execution_authority=False,sealed_candidate_independent_pass_required=not single_review),
            independent_reference_pass=dict(path=test_path.name,test=proof_test),
            parallel_parity_pass=dict(path=test_path.name,test=proof_test),
            interval_reporting_valid=dict(path='INTERVAL_CERTIFICATION.json',sha256=sha(root/'INTERVAL_CERTIFICATION.json'))))
        ensure_once(root/'FINAL_READINESS.json',{name:True for name in REQUIRED_READINESS})
        roles=_inventory(ctx)
        roles.update({name:'calibration' for name in ('ANCHOR.json','RI_FEASIBILITY.json','DIAGNOSTIC_CALIBRATION.json','INTERVAL_EVALUATION.json','INTERVAL_CERTIFICATION.json','CALIBRATION_GATES.json')})
        roles.update({name:'verification' for name in ('FREEZE_PREP.json','APPROVAL_PREP.json','FINAL_READINESS.json','FINAL_READINESS_EVIDENCE.json')})
        if not (root/'FREEZE_FINAL.json').exists():
            build_manifest(root,'final',roles,json.loads((root/'PREDICTIONS_LOCK.json').read_text())['personas'],hub_signer(ctx))
        check=verify(root,'final',hub_signer(ctx),require_approval=False)
        if not check['ok']: raise ValueError(str(check['issues']))
        ensure_once(root/'APPROVAL_FINAL.template.json',dict(status='UNSIGNED',signed_by=None,signed_at_utc=None,
            expected_signer=hub_signer(ctx),manifest_path='FREEZE_FINAL.json',manifest_sha256=check['manifest_sha256'],
            review_sha256=None,approved_actions=ACTIONS['final'],execution_authorized=False))
        from .cli import run_stage
        receipt=run_stage(ctx,'S5')
        if receipt.disposition.value!='ready': raise ValueError(receipt.reason)
        results.append(dict(design_id=ctx.card.design_id,**check,selected_method=cert['selected_method']))
    return results


def command(args,repo):
    from .cards import load_card
    from .cli import preflight
    workspace=output_root(args.work_dir,repo)
    paths=[args.design] if args.design else sorted(args.batch.glob('*.json'))
    if not paths: raise ValueError('No design cards')
    from .run2_preparation import effective_context
    contexts=[effective_context(StageContext(load_card(p),workspace,repo)) for p in paths]
    expected=[c.card.design_id for c in contexts]
    if len(set(expected))!=len(expected): raise ValueError('Duplicate designs')
    start=time.perf_counter()
    for ctx in contexts: preflight(ctx,'S5')
    if args.action=='seal': result=seal(contexts)
    elif args.action=='report': return report(contexts,args,repo)
    else:
        result=[]
        for ctx in contexts:
            before=time.perf_counter()
            value=(lock(ctx) if args.action=='lock' else resolve_draft(ctx,args) if args.action=='resolve-draft' else prep(ctx,args,expected) if args.action=='prep'
                else calibration(ctx) if args.action=='calibrate' else dry_load(ctx) if args.action=='dry-load'
                else approve(ctx,args,'prep' if args.action=='approve-prep' else 'final'))
            result.append(dict(design_id=ctx.card.design_id,wall_seconds=time.perf_counter()-before,**value))
    record=dict(action=args.action,results=result,wall_seconds=time.perf_counter()-start,actual_data_estimation_calls=0)
    ensure_once(workspace/'throughput'/(args.action+'-'+str(time.time_ns())+'.json'),record)
    return record


def report(contexts,args,repo):
    if not args.output: raise ValueError('Report output required')
    output=output_root(args.output,repo)
    workspace=contexts[0].workspace
    lines=['# L2 batch4 acceptance · 2026-10-07',
        'S5 locked 10 forecasts; preparatory approval: hub (Claude coordinator), 2026-10-07. Final approval UNSIGNED; sealed S6 jobs undispatched; real estimates 0.',
        'MUST = incorrect registered number or missing registered result; each sealed reviewer performs one complete sweep.']
    for ctx in contexts:
        root=ctx.design_dir; cert=json.loads((root/'INTERVAL_CERTIFICATION.json').read_text());case=cert['cases'][ctx.card.design_id]
        ri=json.loads((root/'RI_FEASIBILITY.json').read_text())['feasibility'];diag=json.loads((root/'DIAGNOSTIC_CALIBRATION.json').read_text())
        lines.append(f"{ctx.card.design_id}: Webb/CT coverage "+'/'.join(f"{case['summaries'][m]['coverage']:.1%}" for m in cert['candidate_order'])+
            '; median/p95 width ratios '+', '.join(m+'='+str(round(case['summaries'][m]['median_width_ratio'],4))+'/'+str(round(case['summaries'][m]['p95_width_ratio'],4)) for m in cert['candidate_order'])+
            f"; selected={cert['selected_method'] or 'point+RI p'}; RI999={ri['feasible_draws']}/999; RI199={diag['ri_feasibility']['feasible_draws']}/199; diagnostic clean={diag['clean_pass_rate']:.1%}.")
        lines.append(ctx.card.design_id+' SHA256: '+', '.join(n+'='+sha(root/n) for n in ('PREDICTIONS_LOCK.json','FREEZE_PREP.json','APPROVAL_PREP.json','INTERVAL_CERTIFICATION.json','CALIBRATION_GATES.json','FREEZE_FINAL.json','APPROVAL_FINAL.template.json'))+'.')
    jobs=workspace/'sealed_jobs'
    for path in sorted(jobs.rglob('*.job.json')):
        job=json.loads(path.read_text());lines.append(job['design_id']+' sealed packet/sidecar: '+str(Path(job['job_path']).relative_to(repo))+' SHA256='+job['job_sha256']+'/'+sha(path)+'.')
    lines.append('Wall-clock seconds: '+', '.join(json.loads(p.read_text())['action']+'='+f"{json.loads(p.read_text())['wall_seconds']:.3f}" for p in sorted((workspace/'throughput').glob('*.json')))+'.')
    lines.extend(['Hub commands (repo root; dispatcher outside sandbox):','```sh',
        'PY=.venv_did/bin/python; B=data/l2_pipeline/cards/batch4; W=data/l2_pipeline/acceptance/batch4',
        'python3 -B scripts/l2_pipeline/agents/dispatch.py --jobs-root "$W/sealed_jobs" --stage S6 --hub-outside-sandbox --max-concurrency 2',
        'for d in pay_secrecy pregnancy_accommodation; do "$PY" -B -m scripts.l2_pipeline collect --job "$W/sealed_jobs/S6/$d/reviewer.20261007-sealed.md"; done',
        '# Only after both collected sealed reviews PASS; hub supplies its actual UTC signing time:',
        '"$PY" -B -m scripts.l2_pipeline registration --action approve-final --batch "$B" --work-dir "$W" --signer "hub (Claude coordinator), 2026-10-07" --signed-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"',
        '"$PY" -B -m scripts.l2_pipeline run --stage S6 --batch "$B" --work-dir "$W"',
        '"$PY" -B -m scripts.l2_pipeline run --stage S7 --batch "$B" --work-dir "$W"',
        '```',
        'Outputs: each <W>/<design>/v1/{FREEZE_PREP,APPROVAL_PREP,INTERVAL_CERTIFICATION,CALIBRATION_GATES,FREEZE_FINAL,APPROVAL_FINAL.template}.json; S7 writes terminal registry/Estimates/L3 local fragments.',
        'New public downloads: 0 files, 0 bytes; URL/size/license inventory empty. No paid calls, real fits, dispatch, uploads/releases, or git mutations.'])
    if len(lines)>25: raise ValueError('Acceptance report exceeds 25 lines')
    output.parent.mkdir(parents=True,exist_ok=True)
    # Reporting is a derived, reviewable view; scientific artifacts remain write-once.
    output.write_text('\n'.join(lines)+'\n')
    return dict(path=str(output),lines=len(lines),sha256=sha(output))
