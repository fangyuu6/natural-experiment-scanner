#!/usr/bin/env python3
"""Hub-owned administrative preregistration gate. No estimation or coding."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from build_frame import ROOT, OUT, file_hash, write_json

APPROVAL = OUT / 'HUB_FREEZE.json'
LOCK_PATHS = [OUT / 'PROTOCOL.md', OUT / 'PILOT_PLAN.md',
              OUT / 'CODING_SCHEMA.json', OUT / 'FRAME_MANIFEST.json']
LOCK_PATHS += sorted((ROOT / 'scripts/l3_ideas/I01').glob('*.py'))


def require_hub_freeze():
    if not APPROVAL.exists():
        raise SystemExit('BLOCKED: hub has not frozen I01; no coding or target associations')
    d = json.loads(APPROVAL.read_text())
    if d.get('status') != 'FROZEN' or d.get('approved_by') != 'hub' or not d.get('approval_reference'):
        raise SystemExit('BLOCKED: invalid hub approval record')
    expected_paths = {str(p.relative_to(ROOT)) for p in LOCK_PATHS}
    if {x['path'] for x in d['files']} != expected_paths:
        raise SystemExit('BLOCKED: approval scope differs from current protocol/code')
    for item in d['files']:
        if file_hash(ROOT / item['path']) != item['sha256']:
            raise SystemExit(f'BLOCKED: approved bytes changed: {item["path"]}')
    frame = json.loads((OUT / 'FRAME_MANIFEST.json').read_text())
    for item in frame['artifacts'] + frame['inputs'] + [frame['code']]:
        if file_hash(ROOT / item['path']) != item['sha256']:
            raise SystemExit(f'BLOCKED: frozen frame/input changed: {item["path"]}')
    return d


def freeze(reference):
    if APPROVAL.exists():
        raise SystemExit('existing approval cannot be overwritten; create a new version')
    # Hub invokes this only after reviewing concrete files and deciding to approve.
    # It records an administrative decision, not cryptographic proof of identity.
    from build_frame import verify
    verify(check_inputs=True)
    files = [{'path': str(p.relative_to(ROOT)), 'sha256': file_hash(p)} for p in LOCK_PATHS]
    write_json(APPROVAL, {'status': 'FROZEN', 'approved_by': 'hub',
                         'approval_reference': reference,
                         'created_at_utc': datetime.now(timezone.utc).isoformat(),
                         'files': files, 'outcomes_coded_at_freeze': 0,
                         'target_associations_computed_at_freeze': 0})
    require_hub_freeze()
    print('PASS: hub-approved bytes locked; future analyses must call require_hub_freeze()')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument('--verify', action='store_true')
    action.add_argument('--freeze', action='store_true')
    p.add_argument('--approval-reference')
    a = p.parse_args()
    if a.freeze:
        if not a.approval_reference:
            p.error('--freeze requires the hub decision reference')
        freeze(a.approval_reference)
    else:
        require_hub_freeze()
        print('PASS: I01 hub freeze and input hashes verified')
