"""Pinned real-source import QA, independent of outcome data or estimates."""
import importlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.l2_pipeline.sources.research_panel.core import DATA, STATES, canonicalize, digest, write_panel
from scripts.l2_pipeline.sources.research_panel.s1 import verify_bindings


class DownloadedPanels(unittest.TestCase):
    def test_rebuild_three_saved_tables_with_complete_source_bindings(self):
        catalog = json.loads((DATA / 'CATALOG.json').read_text())
        for panel in catalog['panels']:
            with self.subTest(adapter=panel['adapter']):
                module = importlib.import_module('scripts.l2_pipeline.sources.research_panel.' + panel['adapter'])
                rows, variables, receipts = module.load()
                for variable in variables:
                    if variable['source_id'] == 'siegel_firearms':
                        variable['historical_year_end'] = 2025
                    if variable['source_id'] == 'vz':
                        variable['historical_year_start'] = 1975
                canonical, metadata = canonicalize(rows, variables)
                self.assertEqual(len(canonical), panel['canonical_rows'])
                self.assertEqual(set(r['state'] for r in canonical), set(STATES))
                self.assertTrue(all(v['definition'] and v['first_order_outcome_candidates'] for v in metadata))
                self.assertTrue(all(r['value'] is None or r['value'] in (0, 1)
                    for r in canonical if next(v for v in metadata if v['policy_id'] == r['policy_id'])['kind'] == 'binary_adoption'))
                saved = [v for v in catalog['variables'] if v['policy_id'] in panel['policy_ids']]
                self.assertEqual(metadata, saved)
                with tempfile.TemporaryDirectory(prefix='research_panel_qa_') as tmp:
                    rebuilt = Path(tmp) / 'panel.csv'
                    write_panel(rebuilt, canonical)
                    self.assertEqual(digest(rebuilt), panel['sha256'])
        for receipt in catalog['files']:
            self.assertEqual(Path(receipt['path']).stat().st_size, receipt['size_bytes'])
            self.assertEqual(digest(receipt['path']), receipt['sha256'])
            self.assertTrue(receipt['url'].startswith('https://'))
            self.assertTrue(receipt['license'])

    def test_all_prepared_jobs_are_seeded_auditor_only_and_intact(self):
        report = json.loads((DATA / 'BUILD_REPORT.json').read_text())
        active_path = DATA / 'ACTIVE_REGENERATION.json'
        if active_path.is_file():
            # Historical prompts preserve their original parser hashes; the
            # active pointer identifies current-template dispatchable jobs.
            from scripts.l2_pipeline.agents.jobs import load_job
            active = json.loads(active_path.read_text())
            archive = json.loads(Path(active['archive_manifest']).read_text())
            for item in archive['files']:
                self.assertEqual(Path(item['path']).stat().st_size, item['size_bytes'])
                self.assertEqual(digest(item['path']), item['sha256'])
            work = Path(active['work_dir'])
            jobs = list(Path(active['jobs_root']).glob('S1/*/*.md'))
            self.assertEqual(len(jobs), report['auditor_jobs'])
            self.assertFalse(list(work.glob('*/v1/agents/A/*')))
            self.assertFalse(list(work.glob('*/v1/agents/B/*')))
            self.assertFalse(list(work.glob('*/v1/TREATMENT.json')))
            self.assertEqual(report['estimation_calls'], 0)
            for path in jobs:
                job = load_job(path)
                self.assertEqual(job.role, 'auditor')
                self.assertEqual(len(set(job.contract['sample'])), 10)
                self.assertEqual(job.contract['model_calls'], 0)
                self.assertEqual(job.contract['attempt_id'], active['attempt_id'])
                self.assertEqual(job.contract['attempt_kind'], 'template_regeneration')
                verify_bindings(job.contract['bound_inputs'])
            return
        jobs = list((DATA / 'audit_work').glob('*/v1/agents/auditor/auditor.job.json'))
        self.assertEqual(len(jobs), report['auditor_jobs'])
        self.assertFalse(list((DATA / 'audit_work').glob('*/v1/agents/A/*')))
        self.assertFalse(list((DATA / 'audit_work').glob('*/v1/agents/B/*')))
        self.assertFalse(list((DATA / 'audit_work').glob('*/v1/TREATMENT.json')))
        self.assertEqual(report['estimation_calls'], 0)
        for path in jobs:
            job = json.loads(path.read_text())
            self.assertEqual(job['role'], 'auditor')
            self.assertEqual(len(set(job['sample'])), 10)
            self.assertEqual(job['model_calls'], 0)
            verify_bindings(job['bound_inputs'])
            lock = json.loads((path.parent / 'JOB_LOCK.json').read_text())
            verify_bindings(list(lock.values()))


if __name__ == '__main__':
    unittest.main()
