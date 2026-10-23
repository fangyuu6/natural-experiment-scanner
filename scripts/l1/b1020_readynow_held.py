#!/usr/bin/env python3
"""T186: build the held sex-offender card with the hub's pending preferred_series ruling emulated in-process
(no repository file is changed). Usage: b1020_readynow_held.py RECORD_DIR OUT_DIR"""
import copy, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts/l1'))
from scripts.l2_pipeline.connectors import population_v63
import b1020_cards as B
orig = population_v63.target_metadata
def patched(*a, **k):
    t = orig(*a, **k)
    if t and t.get('construct') == 'rape':
        t = copy.deepcopy(t); t['preferred_series'] = 'fbi_ucr_rape_legacy_rate'
    return t
population_v63.target_metadata = patched
for p in sorted(Path(sys.argv[1]).glob('*.json')):
    card, diag = B.build_card(json.loads(p.read_text()))
    B.dump(card, Path(sys.argv[2]) / (card['design_id'] + '.json'))
    print(card['design_id'], diag['screen_passed'], diag['mechanical_series'], diag['kept'][:3], len(diag['kept']))
