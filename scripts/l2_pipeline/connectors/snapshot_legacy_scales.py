"""Reproduce compatibility metadata from an outcome-free, pre-change baseline.

Reads only the coordinator's previously captured registered card mappings;
never reads raw outcomes, result values, political covariates or credentials.
"""
import argparse
import json
from pathlib import Path
from .rates import card_hash
from .common import sha


def snapshot(baseline, output):
    baseline, output = Path(baseline), Path(output)
    records = json.loads(baseline.read_text())
    if records.get('errors'):
        raise ValueError('Legacy variant baseline contains errors')
    cards = {}
    for record in records['variants'].values():
        card = record['card']
        digest = card_hash(card)
        cards[digest] = dict(card_path=record['card_path'], variant=record['variant'],
                            design_id=card.get('design_id'), version=card.get('version'),
                            original_scale=card.get('outcome_scale', 'source_measurement'))
    value = dict(schema='exact_legacy_outcome_scales_v1',
                 baseline_sha256=sha(baseline), cards=cards,
                 purpose='Exact frozen/finished primary and registered variant mappings before C16b; any scientific mutation revokes exemption',
                 ignored_runtime_fields=['_card_path'], outcome_association_calls=0,
                 regeneration='python3 -B -m scripts.l2_pipeline.connectors.snapshot_legacy_scales --baseline <pre-change LEGACY_VARIANTS_BEFORE.json> --output scripts/l2_pipeline/connectors/LEGACY_OUTCOME_SCALES.json')
    output.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    return len(cards)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(snapshot(args.baseline, args.output))
