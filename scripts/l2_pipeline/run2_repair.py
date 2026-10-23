"""Fork failed preparation into a fresh workspace, retaining audited originals.

Only S0/S1 artifacts are copied. Existing panels, later receipts and agent
outputs remain in their original workspace. No official execution can be forked.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from .cards import load_card
from .cli import preflight
from .contracts import StageContext
from .storage import ensure_once, sha

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / 'data/l2_pipeline/jobs/run2'


def fork_preparation(design_id: str) -> dict:
    card = load_card(REPO / 'data/l2_pipeline/cards/run2' / (design_id + '.json'))
    source = StageContext(card, ROOT / 'work', REPO)
    target = StageContext(card, ROOT / 'work_repaired', REPO)
    if any((source.design_dir / name).exists() for name in
           ('EXECUTION_ONCE.json', 'REGISTRATION.json', 'FREEZE_FINAL.json', 'RESULT.json')):
        raise ValueError('Only unfrozen preparation may be forked')
    preflight(source, 'S2')
    inventory = {}
    for stage in ('S0', 'S1'):
        receipt_path = sorted((source.design_dir / 'receipts' / stage).glob('*.json'))[-1]
        receipt = json.loads(receipt_path.read_text())
        if receipt['result']['disposition'] != 'ready':
            raise ValueError('Cannot fork non-ready ' + stage)
        for artifact in receipt['result']['artifacts']:
            old = Path(artifact['path'])
            new = target.design_dir / old.relative_to(source.design_dir)
            new.parent.mkdir(parents=True, exist_ok=True)
            data = old.read_bytes()
            if new.exists():
                if new.read_bytes() != data:
                    raise ValueError('Repaired source artifact changed')
            else:
                with new.open('xb') as stream:
                    stream.write(data)
            inventory[str(old)] = {'sha256': sha(old), 'copy': str(new)}
            artifact['path'] = str(new)
        ensure_once(target.design_dir / 'receipts' / stage / '000-imported.json', receipt)
    lineage = ensure_once(target.design_dir / 'PREPARATION_FORK.json', {
        'source_workspace': str(source.workspace), 'target_workspace': str(target.workspace),
        'design_id': design_id, 'originals': inventory,
        'reason': 'Earlier partial S2 VARIANT_CARD is immutable; prospective construction uses a fresh workspace',
        'official_estimation_consumed': False, 's1_audit_reused_without_changes': True,
    })
    preflight(target, 'S2')
    return {'design_id': design_id, 'work_dir': str(target.workspace), 'lineage': str(lineage)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--design-id', action='append', required=True)
    args = parser.parse_args(argv)
    print(json.dumps([fork_preparation(identity) for identity in args.design_id], indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
