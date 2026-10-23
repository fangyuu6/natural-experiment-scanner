"""Run4 adapter provenance, scope boundaries and import-safe replay CLIs."""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.run4_adapters import published_study_intake as adapter
from scripts.l2_pipeline.run4_adapters import verify_dry_run as dry_run
from scripts.l2_pipeline.run4_adapters.paths import REPO_ROOT, Run4Paths
from scripts.l2_pipeline.stages.s1_treatment import audit_sample
from scripts.l2_pipeline.storage import sha
from scripts.l2_pipeline.tests.test_pipeline import card


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


class PublishedIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.paths = Run4Paths(self.root)
        self.source = self.root / 'paper.txt'
        self.source.write_text('Public paper appendix: Table A1, page 32.')
        self.units = 'AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS'.split()
        self.records = [dict(unit=unit, status='treated' if i < 10 else 'never_treated',
                             start_period=1994 if i < 10 else None,
                             mechanism='one intervention', locator='Table A1, page 32')
                        for i, unit in enumerate(self.units)]
        payload = card()
        payload['design_id'] = 'run4_lit_fixture'
        payload['sources'] = [dict(source_id='paper', path='paper.txt', sha256=sha(self.source),
                                   url='https://example.invalid/open-paper',
                                   locator='Table A1, page 32', license='Public test fixture')]
        payload['treatment'] = dict(source_type='published_did_study', universe=self.units,
                                    complete_list_source_id='paper', seed=77, predicate='one intervention',
                                    source_window=dict(start_year=1990, end_year=2000))
        payload['outcome'].update(years=[1990, 2000], units=self.units, frequency='annual')
        payload['analysis'] = dict(min_controls=5)
        payload['first_order']['purpose_source_type'] = 'published_did_study'
        payload['automation'] = dict(intake=dict(source_type='published_did_study'),
                                     run4=dict(purpose_job_required=False),
                                     coverage_window=dict(effective_period_years=list(range(1990, 2001))))
        self.card_path = save(self.paths.cards_dir / (payload['design_id'] + '.json'), payload)
        self.ctx = StageContext(load_card(self.card_path), self.paths.jobs_root / 'work', self.root)
        self.projection = save(self.ctx.design_dir / 'LITERATURE_SOURCE_PROJECTION.json',
                               dict(source_sha256=sha(self.source), records=self.records))
        self.selection = save(self.ctx.design_dir / 'AUDIT_SELECTION.json',
                              dict(sample=audit_sample(self.records, 77)))
        self.job_path = self.paths.jobs_root / 'S1' / payload['design_id'] / 'auditor.md'
        self.job_path.parent.mkdir(parents=True)
        self.job_path.write_text('Test auditor job; no model calls.')
        self.output_dir = self.ctx.design_dir / 'agents/auditor'
        self.original = self.output_dir / 'audit.json'
        self.job = SimpleNamespace(stage='S1', role='auditor', design_id=payload['design_id'],
                                   output_path=str(self.original), output_dir=str(self.output_dir),
                                   contract=dict(source_type='published_did_study',
                                       complete_list_crosscheck_required=True,
                                       no_dual_coding_originals=True,
                                       source_sha256=sha(self.source), selection_sha256=sha(self.selection),
                                       sample=audit_sample(self.records, 77),
                                       bound_artifacts={'paper.txt': sha(self.source)}))
        save(self.paths.cards_dir / '_meta/S0_ACCEPTANCE.json',
             dict(accepted_literature_designs=[dict(design_id=payload['design_id'],
                                                   card_sha256=sha(self.card_path),
                                                   s1_job_sha256=sha(self.job_path))]))
        self.patches = [patch.object(adapter, 'load_job', return_value=self.job),
                        patch.object(adapter, 'first_order_gate', return_value={'passed': True}),
                        patch.object(adapter, 'coverage_bounded_card', side_effect=lambda value: copy.deepcopy(value)),
                        patch('scripts.l2_pipeline.cli.preflight')]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def answer(self):
        archive = self.output_dir / 'history.txt'
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text('Historical complete roster from a public source.')
        return dict(design_id=self.job.design_id, role='auditor',
                    source_sha256=sha(self.source), selection_sha256=sha(self.selection),
                    blindness_attestation=dict(other_submissions_read=False, outcome_associations_read=False),
                    checks=[dict(unit=unit, status='match', official_locator='Statute section 1, p. 3')
                            for unit in self.job.contract['sample']],
                    complete_list_crosscheck=dict(status='VERIFIED', predicate='one intervention',
                        source_window=dict(start_year=1990, end_year=2000),
                        source_url='https://example.invalid/history', locator='Table 1, page 4',
                        source_path=str(archive), source_sha256=sha(archive),
                        records=copy.deepcopy(self.records), unresolved_units=[]))

    def test_relative_archive_paths_and_source_hash_binding(self):
        report = adapter.verify_literature(self.card_path, self.paths)
        self.assertTrue(report['s0_passed'])
        self.assertFalse(report['s1_ready'])
        self.source.write_text('Changed source bytes')
        with self.assertRaisesRegex(ValueError, 'Paper original bytes changed'):
            adapter.verify_literature(self.card_path, self.paths)

    def test_adoption_locator_and_complete_roster_are_required(self):
        value = json.loads(self.projection.read_text())
        value['records'][0]['locator'] = ''
        save(self.projection, value)
        with self.assertRaisesRegex(ValueError, 'scope/locator'):
            adapter.verify_literature(self.card_path, self.paths)
        value['records'][0]['locator'] = 'p. 3'
        value['records'][-1] = copy.deepcopy(value['records'][0])
        save(self.projection, value)
        with self.assertRaisesRegex(ValueError, 'incomplete/duplicated'):
            adapter.verify_literature(self.card_path, self.paths)

    def test_changed_seed_selection_and_insufficient_pre_support_reject(self):
        save(self.selection, {'sample': list(reversed(self.job.contract['sample']))})
        with self.assertRaisesRegex(ValueError, 'Audit seed/sample changed'):
            adapter.verify_literature(self.card_path, self.paths)
        save(self.selection, {'sample': self.job.contract['sample']})
        payload = json.loads(self.card_path.read_text())
        payload['automation']['coverage_window']['effective_period_years'] = list(range(1993, 2001))
        save(self.card_path, payload)
        with self.assertRaisesRegex(ValueError, 'Coverage-bounded support failed'):
            adapter.verify_literature(self.card_path, self.paths)

    def test_missing_auditor_parks_without_treatment_or_readiness_write(self):
        before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        report = adapter.collect_audit(self.job_path, self.paths)
        self.assertEqual(report['status'], 'PARK')
        self.assertFalse(report['s1_ready'])
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_historical_crosscheck_mismatch_parks_without_generic_promotion(self):
        answer = self.answer()
        answer['complete_list_crosscheck']['records'][0]['start_period'] = 1995
        save(self.original, answer)
        with patch('scripts.l2_pipeline.agents.collect.collect_job') as collect:
            result = adapter.collect_audit(self.job_path, self.paths)
        collect.assert_not_called()
        self.assertEqual(result['status'], 'PARK')
        self.assertIn('Full-list discrepancies', result['reason'])
        self.assertFalse((self.ctx.design_dir / 'TREATMENT.json').exists())

    def test_verified_history_preserves_published_originals_and_records_corrections(self):
        answer = self.answer()
        corrected_unit = answer['checks'][0]['unit']
        answer['complete_list_crosscheck']['source_window'].update(
            coverage_end='2000-12-31', basis='Independent dated roster reconstruction')
        row = answer['complete_list_crosscheck']['records'][0]
        row.pop('locator')
        archive = Path(answer['complete_list_crosscheck']['source_path'])
        row['official_evidence'] = [dict(source_url='https://example.invalid/history',
            locator='Table 1, page 4', source_path=str(archive), source_sha256=sha(archive))]
        answer['checks'][0].update(status='known_error', corrected_start_period=1995)
        next(row for row in answer['complete_list_crosscheck']['records']
             if row['unit'] == corrected_unit)['start_period'] = 1995
        save(self.original, answer)
        save(self.job_path.with_suffix('.job.json'), {'fixture': 'job identity'})
        code_path = self.root / 'scripts/l2_pipeline/run4_adapters/published_study_intake.py'
        code_path.parent.mkdir(parents=True)
        code_path.write_text('# fixture provenance for portable checkout')
        before = self.source.read_bytes()
        with patch('scripts.l2_pipeline.agents.collect.collect_job', return_value={'validated': True}), \
             patch.object(adapter, '__file__', str(code_path)):
            result = adapter.collect_audit(self.job_path, self.paths)
        self.assertTrue(result['s1_ready'])
        treatment = json.loads((self.ctx.design_dir / 'TREATMENT.json').read_text())
        self.assertTrue(treatment['s1_certified'])
        self.assertEqual(treatment['published_original_records'], self.records)
        self.assertEqual(next(row for row in treatment['records']
                              if row['unit'] == corrected_unit)['start_period'], 1995)
        self.assertEqual(treatment['outcome_values_read'], 0)
        self.assertEqual(self.source.read_bytes(), before)
        proof = json.loads((self.ctx.design_dir / 'S1_IMPORT_PROVENANCE.json').read_text())
        self.assertFalse(proof['dual_coder_originals_manufactured'])
        self.assertTrue(proof['complete_list_checked'])
        self.assertFalse(proof['numeric_replication_gap_enabled'])
        self.assertEqual(treatment['complete_list_crosscheck'], answer['complete_list_crosscheck'])
        self.assertEqual(treatment['crosscheck_normalization']['locator_alias_units'], [row['unit']])
        self.assertFalse(treatment['crosscheck_normalization']['scientific_values_changed'])

    def test_nested_crosscheck_locators_require_bound_archived_evidence(self):
        answer = self.answer()
        row = answer['complete_list_crosscheck']['records'][0]
        row.pop('locator')
        archive = Path(answer['complete_list_crosscheck']['source_path'])
        row['official_evidence'] = [dict(source_url='https://example.invalid/history',
            locator='Table 1, page 4', source_path=str(archive), source_sha256='0' * 64)]
        save(self.original, answer)
        with self.assertRaisesRegex(ValueError, 'row archived bytes missing/changed'):
            adapter.collect_audit(self.job_path, self.paths)
        self.assertFalse((self.ctx.design_dir / 'TREATMENT.json').exists())

    def test_crosscheck_scope_annotations_cannot_change_locked_boundaries(self):
        for changes in (dict(start_year=1991), dict(end_year=2001),
                        dict(start_year=True), dict(start_date='1990-02-01'),
                        dict(end_date='2000-06-30'), dict(coverage_end='2001-12-31')):
            with self.subTest(changes=changes):
                answer = self.answer()
                answer['complete_list_crosscheck']['source_window'].update(changes)
                save(self.original, answer)
                with self.assertRaisesRegex(ValueError, 'historical scope mismatch'):
                    adapter.collect_audit(self.job_path, self.paths)
                self.assertFalse((self.ctx.design_dir / 'TREATMENT.json').exists())

    def test_crosscheck_archive_must_remain_in_auditor_output(self):
        answer = self.answer()
        answer['complete_list_crosscheck'].update(source_path=str(self.source), source_sha256=sha(self.source))
        save(self.original, answer)
        with self.assertRaisesRegex(ValueError, 'archive must be within auditor output'):
            adapter.collect_audit(self.job_path, self.paths)

    def test_outside_card_job_and_symlink_paths_reject(self):
        with self.assertRaisesRegex(ValueError, 'Only run4 cards'):
            adapter.verify_literature(self.root / 'outside.json', self.paths)
        with self.assertRaisesRegex(ValueError, 'Job outside'):
            adapter.collect_audit(self.root / 'outside.md', self.paths)
        link = self.paths.cards_dir / 'linked.json'
        outside = self.root / 'outside.json'
        outside.write_text('{}')
        link.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'Only run4 cards'):
            adapter.verify_literature(link, self.paths)


class PortableReplayTests(unittest.TestCase):
    def test_all_archived_builders_import_without_reading_or_writing_evidence(self):
        with patch.object(Path, 'read_text', side_effect=AssertionError('Import read evidence')), \
             patch.object(Path, 'write_text', side_effect=AssertionError('Import wrote evidence')), \
             patch.object(Path, 'mkdir', side_effect=AssertionError('Import created outputs')):
            for name in ('build_additional', 'build_l1', 'build_literature', 'finalize_metadata'):
                module = importlib.import_module('scripts.l2_pipeline.run4_adapters.' + name)
                self.assertTrue(callable(module.run))
                self.assertTrue(callable(module.main))

    def test_default_paths_are_derived_from_tracked_module_and_custom_paths_from_repo(self):
        self.assertEqual(Run4Paths().repo_root, REPO_ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            paths = Run4Paths(Path(tmp), Path('cards'), Path('jobs'))
            self.assertEqual(paths.cards_dir, Path(tmp).resolve() / 'cards')
            self.assertEqual(paths.jobs_root, Path(tmp).resolve() / 'jobs')
            self.assertEqual(paths.reference('source.txt'), Path(tmp).resolve() / 'source.txt')

    def test_builder_validators_survive_optimized_mode_and_have_no_data_code_loader(self):
        for path in (REPO_ROOT / 'scripts/l2_pipeline/run4_adapters').glob('*.py'):
            source = path.read_text()
            self.assertNotIn('spec_from_file_location', source)
            self.assertNotIn('~', source)
            self.assertNotIn('/private/tmp/run4', source)
            self.assertNotIn('assert ', source)
        process = subprocess.run([sys.executable, '-B', '-O', '-m',
                                  'scripts.l2_pipeline.run4_adapters.published_study_intake',
                                  '--help'], cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('--repo-root', process.stdout)

    def test_catalog_routes_validate_construct_and_window_without_loading_values(self):
        value = card()
        value['outcome'].update(connector='qcew_annual', years=[2001, 2005], units=['AL'],
                                frequency='annual', measure_id='employment')
        value['first_order'].update(target_outcome='employment', primary_outcome='employment')
        with tempfile.TemporaryDirectory() as tmp:
            path = save(Path(tmp) / 'card.json', value)
            with patch('scripts.l2_pipeline.connectors.public_annual.qcew_annual',
                       side_effect=AssertionError('Outcome values were loaded')):
                dry_run.connector_contract(load_card(path))
                value['outcome']['years'] = [2000, 2005]
                save(path, value)
                with self.assertRaisesRegex(ValueError, 'metadata coverage'):
                    dry_run.connector_contract(load_card(path))
                value['outcome']['years'] = [2001, 2005]
                value['outcome']['measure_id'] = 'unemployment_rate'
                save(path, value)
                with self.assertRaisesRegex(ValueError, 'target/measure'):
                    dry_run.connector_contract(load_card(path))

    def test_dry_run_only_invokes_metadata_contracts_and_unknown_connector_is_explicit(self):
        value = card()
        value['outcome'].update(years=[1990, 2000], units=['AL'], frequency='annual')
        with tempfile.TemporaryDirectory() as tmp:
            path = save(Path(tmp) / 'card.json', value)
            with self.assertRaisesRegex(ValueError, 'Unexpected prospective connector'):
                dry_run.connector_contract(load_card(path))
        with patch.object(dry_run.intake, 'verify_all', return_value={}), \
             patch.object(dry_run.intake, 'verify_literature', side_effect=ValueError('outside')):
            with tempfile.TemporaryDirectory() as tmp:
                paths = Run4Paths(Path(tmp))
                report = dry_run.run(paths, output_dir=Path(tmp) / 'reports',
                                     stages=(), check_optimized=False)
                self.assertEqual(report['actual_dispatches'], 0)
                self.assertEqual(report['outcome_association_calls'], 0)
                self.assertFalse(paths.jobs_root.exists())


if __name__ == '__main__':
    unittest.main()
