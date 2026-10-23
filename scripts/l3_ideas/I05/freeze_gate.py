#!/usr/bin/env python3
"""Read-only hub preregistration gate. Never creates or grants approval."""
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[3]
DOC = 'docs/plans/l3-ideas/I05/'
CODE = 'scripts/l3_ideas/I05/'
COMMITMENTS = [DOC + n for n in ['PROTOCOL.md', 'CODING_MANUAL.md', 'PILOT_PLAN.md',
                                'FRAME_MANIFEST.json']]
COMMITMENTS += [CODE + n for n in ['build_frame.py', 'freeze_gate.py']]


def hashes():
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in COMMITMENTS}


def check():
    path = ROOT / DOC / 'HUB_FREEZE.json'
    issues = []
    if not path.exists():
        return {'status': 'BLOCKED', 'issues': ['Hub freeze has not been supplied'],
                'required_sha256': hashes()}
    try:
        a = json.loads(path.read_text())
        if a.get('status') != 'APPROVED_FOR_CODING':
            issues.append('Expected APPROVED_FOR_CODING')
        if not isinstance(a.get('approved_by'), str) or not a['approved_by'].strip():
            issues.append('Missing hub approver')
        if datetime.fromisoformat(a['approved_utc'].replace('Z', '+00:00')).utcoffset() is None:
            issues.append('Approval time must have a timezone')
        if a.get('sha256') != hashes():
            issues.append('Commitment hashes differ or approval has an incomplete hash set')
        if a.get('no_headline_associations_examined') is not True:
            issues.append('Missing preregistration no-peeking attestation')
        manifest = json.loads((ROOT / DOC / 'FRAME_MANIFEST.json').read_text())
        for entry in manifest['artifacts']:
            p = ROOT / entry['path']
            if p.stat().st_size != entry['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest() != entry['sha256']:
                issues.append('Frame artifact mismatch: ' + entry['path'])
    except (KeyError, TypeError, ValueError, OSError) as e:
        issues.append('Malformed or incomplete freeze: ' + str(e))
    return {'status': 'BLOCKED' if issues else 'PASS_FOR_CODING', 'issues': issues,
            'analysis_authorized': False,
            'note': 'Analysis additionally needs locked full submissions, audits and the protocol support gates.'}


if __name__ == '__main__':
    r = check()
    print(json.dumps(r, indent=2))
    sys.exit(0 if r['status'] == 'PASS_FOR_CODING' else 2)
