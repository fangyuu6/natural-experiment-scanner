"""Explicit, stopped-host recovery of a failed public-panel preparation.

No execution, calibration or failed model worker can use this replay path.
Successful receipts remain immutable; a new S2 receipt supersedes the failure.
"""
from pathlib import Path
from .storage import public_input,sha

FROZEN=('FREEZE_PREP.json','FREEZE_FINAL.json','APPROVAL_PREP.json',
        'APPROVAL_FINAL.json','TERMINAL.json','FAILURE.json','EXECUTION_ONCE.json')


def replay_public_panel(ctx,failed_receipt,authorization):
    from .autopilot import read
    from .cli import run_stage
    root=ctx.design_dir
    if any((root/n).exists() for n in FROZEN):
        raise ValueError('Frozen or consumed design cannot replay preparation')
    authorization=public_input(Path(authorization))
    rule=read(authorization)
    if (rule.get('schema')!='l2_public_panel_replay_authorization_v1'
        or rule.get('stage')!='S2' or rule.get('execution_authorized') is not False
        or ctx.card.design_id not in rule.get('designs',[])):
        raise ValueError('Explicit design-bound S2 replay authorization required')
    if not (ctx.repo/'data/l2_pipeline/registry/AUTOPILOT_STOP').exists():
        raise ValueError('Stopped scheduler required for preparation replay')
    receipt=public_input(Path(failed_receipt['path']))
    if (receipt.parent!=root/'receipts/S2' or sha(receipt)!=failed_receipt['sha256']):
        raise ValueError('Failed S2 receipt binding changed')
    value=read(receipt);result=value['result']
    if (value['card_sha256']!=sha(ctx.card.path) or result.get('stage')!='S2'
        or result.get('disposition')!='failed_no_retry'
        or result.get('reason')!=rule.get('failure_reason')):
        raise ValueError('Replay cannot replace a different failure or successful stage')
    # Crash recovery selects an already-written later READY, never retries twice.
    latest=sorted(receipt.parent.glob('*.json'))[-1]
    if latest!=receipt:
        newer=read(latest)
        if newer['card_sha256']!=sha(ctx.card.path) or newer['result']['disposition']!='ready':
            raise ValueError('Later preparation receipt is not READY; no replay')
        for artifact in newer['result']['artifacts']:
            if sha(artifact['path'])!=artifact['sha256']:
                raise ValueError('Recovered preparation artifact changed')
        return newer['result']
    return run_stage(ctx,'S2').as_dict()
