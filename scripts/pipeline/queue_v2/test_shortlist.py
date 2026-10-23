"""Batch ranking cannot respond to proposed effect signs; registry seeds are one-shot."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.pipeline.queue_v2.shortlist import POOL, build, seed_registry


class ShortlistDiscipline(unittest.TestCase):
    def fixture(self, path):
        did = []
        for n, family in enumerate(POOL):
            did.append({'design_id': 'did-' + str(n), 'policy_family': family,
                'domain': 'labor_market', 'distinct_source_texts': 3, 'states': ['CA', 'NY', 'NJ'],
                'all_source_record_ids': [family + '::whole'],
                'representative_sources': [{'source_text_id': family}]})
        research = {'did_ranked': did}
        (path / 'research_candidates.json').write_text(json.dumps(research))
        rdd = {'design_id': 'rdd-test', 'track': 'RDD', 'policy_family': 'workforce_investment',
            'domain': 'labor_market', 'threshold': {'cutoff': '500000'}, 'source_records': [{
            'source_text_id': 'govinfo-govinfo-plaw-plaw-105publ220', 'source_record_id': 'wia::whole', 'source': 'legacy_csv'}]}
        rct = {'design_id': 'rct-test', 'track': 'RCT', 'policy_family': 'h1b_visa_allocation',
            'domain': 'immigration', 'source_records': [{'source_text_id': 'regulations.gov-regulations-USCIS-2023-0005-1323', 'source_record_id': 'h1b::whole'}]}
        (path / 'rdd_candidates.jsonl').write_text(json.dumps(rdd) + '\n')
        (path / 'rct_mentions.jsonl').write_text(json.dumps(rct) + '\n')
        return research

    def test_effect_or_significance_fields_cannot_change_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            research = self.fixture(path)
            before = build(path)
            changed = copy.deepcopy(research)
            for n, row in enumerate(changed['did_ranked']):
                row.update(expected_sign='positive' if n % 2 else 'negative', estimated_effect=1000-n,
                    p_value=0.00001 if n % 2 else 0.99, policy_significance=5 if n % 2 else 1)
            changed['did_ranked'].reverse()
            (path / 'research_candidates.json').write_text(json.dumps(changed))
            after = build(path)
            self.assertEqual(before['designs'], after['designs'])
            self.assertNotEqual(before['queue_input_sha256'], after['queue_input_sha256'])
            self.assertEqual(len(after['designs']), 20)

    def test_seed_refuses_to_overwrite_a_status_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            existing = path / 'first_batch_registry.json'
            existing.write_text('existing immutable history')
            with self.assertRaises(FileExistsError):
                seed_registry({'designs': []}, path)
            self.assertEqual(existing.read_text(), 'existing immutable history')


if __name__ == '__main__':
    unittest.main()
