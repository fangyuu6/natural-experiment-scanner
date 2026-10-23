"""V7 reported treatment history reopens preserved literature audits."""
import copy
import json
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline.tests import test_run4_adapters as fixtures
from scripts.l2_pipeline.run4_adapters import published_study_intake as adapter


class V7PublishedHistoryTests(fixtures.PublishedIntakeTests):
    # Inherited tests preserve the legacy adapter's strict default contract.
    def test_v7_unresolved_census_uses_reported_table_and_preserves_original(self):
        self.job.job_path = str(self.job_path)
        answer = self.answer()
        answer['complete_list_crosscheck']['status'] = 'UNRESOLVED'
        answer['complete_list_crosscheck']['unresolved_units'] = self.units
        answer['blindness_attestation'] = dict(other_submissions_read=False,
            our_outcome_data_read=False, our_estimates_read=False, published_results_read=True)
        save = fixtures.save
        save(self.original, answer)
        save(self.job_path.with_suffix('.job.json'), {'fixture': 'binding'})
        code = self.root / 'scripts/l2_pipeline/run4_adapters/published_study_intake.py'
        code.parent.mkdir(parents=True); code.write_text('# portable fixture')
        for name in ('HUB_RULING_V7_LITERATURE_BLINDNESS.md', 'HUB_RULING_V5_LITERATURE_TABLES.md'):
            ruling = self.root / 'docs/plans/l2-scale' / name
            ruling.parent.mkdir(parents=True, exist_ok=True); ruling.write_text('Test ruling')
        original_bytes = self.original.read_bytes()
        with patch('scripts.l2_pipeline.agents.collect.collect_job', return_value={'validated': True}), \
             patch.object(adapter, '__file__', str(code)):
            result = adapter.collect_audit(self.job_path, self.paths, v7=True)
        self.assertTrue(result['s1_ready'])
        treatment = json.loads((self.ctx.design_dir / 'TREATMENT.json').read_text())
        self.assertEqual(treatment['records'], self.records)
        self.assertEqual(treatment['complete_list_crosscheck']['status'], 'UNRESOLVED')
        self.assertEqual(treatment['outside_source_window'], 'unknown')
        self.assertEqual(self.original.read_bytes(), original_bytes)
        self.assertFalse(json.loads((self.ctx.design_dir / 'S1_IMPORT_PROVENANCE.json').read_text())['complete_list_checked'])

    def test_v7_failed_v3_is_still_parked(self):
        answer = self.answer()
        answer['complete_list_crosscheck']['status'] = 'UNRESOLVED'
        for row in answer['checks'][:6]: row['status'] = 'unresolved'
        fixtures.save(self.original, answer)
        with patch('scripts.l2_pipeline.agents.collect.collect_job') as collect:
            result = adapter.collect_audit(self.job_path, self.paths, v7=True)
        collect.assert_not_called()
        self.assertFalse(result['s1_ready'])
