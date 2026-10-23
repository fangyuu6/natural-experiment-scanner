"""Bound worker for freeze checks, final signatures and the registered run."""
from pathlib import Path
from argparse import Namespace
from . import snapshots


def perform(request):
    root = Path(request['root']); manifest = request['manifest']
    snapshots.validate(root, manifest['code_snapshot'], manifest['code_inventory'])
    operation = request['operation']; payload = request['payload']
    if operation == 'c63_sandbox_replay':
        from .c63_rehearsal import execute_copy
        return execute_copy(root,payload['approval'])
    if operation == 'c62_sandbox_replay':
        from .c62_rehearsal import execute_copy
        return execute_copy(root,payload['approval'])
    if operation == 'dry_load':
        from .dry_load import run
        return run(root,payload['signer'])
    if operation == 'verify':
        from .freeze import verify
        return verify(root, **payload)
    if operation == 'collect':
        from .agents.collect import collect_job
        return collect_job(payload['job_path'], allow_historical_draft=payload['allow_historical_draft'],self_check=payload.get('self_check',False))
    if operation == 'probe':
        # Invented regression fixture; no study loader or estimation is invoked.
        from .engine.ri import RI_SEED
        return dict(seed=RI_SEED, file=str(Path(__file__).resolve()))
    if operation == 'approve':
        import json
        from types import SimpleNamespace
        from .registration import approve
        # Hub identity comes from frozen authority/prior approval, never live
        # caller objects. Preparation approval has no earlier signature yet.
        authority = root/'REGISTRATION.json'
        if authority.is_file(): signer = json.loads(authority.read_text())['hub_signer']
        else: signer = json.loads((root/'APPROVAL_PREP.json').read_text())['signed_by']
        card_name = next(name for name, item in manifest['files'].items() if item['role'] == 'card')
        design_id = json.loads((root/card_name).read_text())['design_id']
        ctx = SimpleNamespace(design_dir=root, card=SimpleNamespace(design_id=design_id,
            payload=dict(automation=dict(hub_signer=signer))))
        return approve(ctx, Namespace(**payload['args']), payload['stage'])
    from .cards import load_card
    from .contracts import StageContext
    if operation == 'seal':
        from .registration import seal
        from .run2_preparation import effective_context
        contexts = [effective_context(StageContext(load_card(Path(c['card_path'])), Path(c['workspace']), Path(c['repo'])))
                    for c in payload['contexts']]
        return seal(contexts)
    context = payload['context']
    card = load_card(Path(context['card_path']))
    if card.payload != context['card_payload']:
        from dataclasses import replace
        card = replace(card, payload=context['card_payload'])
    ctx = StageContext(card, Path(context['workspace']), Path(context['repo']))
    if operation == 'run_stage':
        from .cli import run_stage
        stage = payload.get('stage', 'S6')
        if stage not in {'S6', 'S7'}:
            raise ValueError('Snapshot stage must be S6 or terminal S7')
        return run_stage(ctx, stage).as_dict()
    if operation == 'run':
        from .stages.s6_execute import run
        return run(ctx).as_dict()
    if operation == 'calibrate':
        from .registration import calibration
        return calibration(ctx)
    raise ValueError('Unknown snapshot operation')
