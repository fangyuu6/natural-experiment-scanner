"""Synthetic, outcome-free tests of S1 bindings and official-auditor contracts.

The .gov URLs below are inert fixture labels; no network request is made and
these tests confer no legal certification on actual research-panel rows.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.l2_pipeline.contracts import DesignCard, Disposition, StageContext
from scripts.l2_pipeline.sources.research_panel import core, s1


class S1BindingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='research_panel_s1_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        raw = self.root / 'synthetic_source.txt'
        raw.write_text('Synthetic policy panel; no legal data or outcomes.\n')
        variable = dict(policy_id='fixture:policy', source_id='fixture',
            definition='Synthetic policy presence', kind='binary_adoption', unit='0/1',
            clock='synthetic_annual_status', outcomes=[], historical_year_end=2025)
        rows = []
        for index, state in enumerate(core.STATES):
            for year in range(2000, 2003):
                value = int(index < 15 and year >= 2001)
                # One left-censored state and one reversed state must not be
                # eligible for the seeded absorbing primary audit sample.
                if index == 15:
                    value = 1
                if index == 16:
                    value = int(year == 2001)
                rows.append(dict(state=state, year=year, policy_id='fixture:policy',
                    value=value, locator=f'synthetic:{state}:{year}'))
        canonical, variables = core.canonicalize(rows, [variable])
        panel = self.root / 'panel.csv'
        core.write_panel(panel, canonical)
        self.catalog = self.root / 'catalog.json'
        self.catalog.write_text(json.dumps(dict(variables=variables,
            panels=[dict(path=str(panel), sha256=core.digest(panel))],
            files=[dict(source_id='fixture', path=str(raw), sha256=core.digest(raw),
                size_bytes=raw.stat().st_size, url='https://fixture.invalid/panel',
                license='Synthetic test data')])) + '\n')
        self.cfg = dict(source_type='research_panel', policy_id='fixture:policy', seed=22,
            universe=list(core.STATES), predicate=variable['definition'], window=[2000, 2002],
            catalog_path=str(self.catalog), catalog_sha256=core.digest(self.catalog),
            panel_path=str(panel), panel_sha256=core.digest(panel))
        card = self.root / 'card.json'
        payload = dict(design_id='synthetic', version='v1', policy_family='fixture',
            mechanism=variable['definition'], track='DiD', unit='state',
            outcome_family='none', treatment=self.cfg)
        card.write_text(json.dumps(payload) + '\n')
        self.ctx = StageContext(DesignCard.from_mapping(payload, card), self.root / 'jobs', core.REPO)
        self.raw = raw

    def emit(self):
        result = s1.run(self.ctx)
        self.assertEqual(result.disposition, Disposition.PARKED)
        directory = self.ctx.design_dir / 'agents/auditor'
        job = json.loads((directory / 'auditor.job.json').read_text())
        return directory, job

    def answer(self, directory, job):
        imported = json.loads((self.ctx.design_dir / 'PANEL_IMPORT.json').read_text())
        selection = json.loads((self.ctx.design_dir / 'AUDIT_SELECTION.json').read_text())
        checks, downloads = [], []
        for unit in job['sample']:
            evidence = directory / f'{unit}.txt'
            evidence.write_text(f'Synthetic official-evidence fixture for {unit}; start 2001.\n')
            downloads.append(dict(path=str(evidence), url=f'https://fixture.gov/{unit}',
                size_bytes=evidence.stat().st_size, license='Synthetic test data',
                sha256=core.digest(evidence), scan=dict(method='synthetic_fixture', model_scan=False)))
            checks.append(dict(unit=unit, status='match', official_locator=f'https://fixture.gov/{unit}',
                predicate_confirmed=True, population_confirmed=True, clock_confirmed=True,
                first_full_exposure_period=2001, evidence_paths=[str(evidence)]))
        answer = dict(job_sha256=core.digest(directory / 'auditor.job.json'),
            import_sha256=selection['import_sha256'], checks=checks, downloads=downloads)
        self.save_answer(directory, answer)
        return imported, selection, answer

    @staticmethod
    def save_answer(directory, answer):
        (directory / 'audit.json').write_text(json.dumps(answer) + '\n')

    def test_pending_emits_one_auditor_and_preserves_exclusions(self):
        directory, job = self.emit()
        self.assertEqual(len(job['sample']), 10)
        self.assertEqual(job['role'], 'auditor')
        self.assertTrue(job['no_dual_coding_originals'])
        self.assertEqual(job['model_calls'], 0)
        self.assertFalse((directory.parent / 'A').exists())
        self.assertFalse((directory.parent / 'B').exists())
        self.assertNotIn(core.STATES[15], job['sample'])
        self.assertNotIn(core.STATES[16], job['sample'])
        result = s1.run(self.ctx)
        self.assertEqual(result.metrics['coder_jobs'], 0)

    def test_valid_synthetic_audit_reaches_ready_with_reversed_state_excluded(self):
        directory, job = self.emit()
        self.answer(directory, job)
        result = s1.run(self.ctx)
        self.assertEqual(result.disposition, Disposition.READY)
        treatment = json.loads((self.ctx.design_dir / 'TREATMENT.json').read_text())
        units = {r['unit'] for r in treatment['records']}
        self.assertNotIn(core.STATES[15], units)
        self.assertNotIn(core.STATES[16], units)
        reversed_record = next(r for r in treatment['original_panel_records'] if r['unit'] == core.STATES[16])
        self.assertEqual(reversed_record['reversal_years'], [2002])
        self.assertEqual(len(treatment['original_panel_records']), 50)

    def test_modified_raw_download_rejected_before_audit(self):
        self.raw.write_text('changed raw fixture\n')
        with self.assertRaisesRegex(ValueError, 'Source download receipt mismatch'):
            s1.load_inputs(self.cfg)

    def test_modified_raw_download_rejected_when_collecting_locked_job(self):
        directory, job = self.emit()
        imported, selection, _ = self.answer(directory, job)
        self.raw.write_text('changed after sample lock\n')
        with self.assertRaisesRegex(ValueError, 'Bound research-panel input changed'):
            s1.collect(directory, imported, selection)

    def test_job_prompt_lock_rejects_tampering(self):
        directory, job = self.emit()
        imported, selection, _ = self.answer(directory, job)
        (directory / 'auditor.md').write_text('tampered prompt\n')
        with self.assertRaisesRegex(ValueError, 'Bound research-panel input changed'):
            s1.collect(directory, imported, selection)

    def test_official_evidence_must_be_hash_bound(self):
        directory, job = self.emit()
        imported, selection, answer = self.answer(directory, job)
        Path(answer['downloads'][0]['path']).write_text('changed evidence\n')
        with self.assertRaisesRegex(ValueError, 'Invalid official evidence receipt'):
            s1.collect(directory, imported, selection)

    def test_unofficial_evidence_url_rejected(self):
        directory, job = self.emit()
        imported, selection, answer = self.answer(directory, job)
        answer['downloads'][0]['url'] = 'https://commercial.invalid/history'
        self.save_answer(directory, answer)
        with self.assertRaisesRegex(ValueError, 'local downloaded official material'):
            s1.collect(directory, imported, selection)

    def test_duplicate_audited_state_cannot_replace_selected_state(self):
        directory, job = self.emit()
        imported, selection, answer = self.answer(directory, job)
        answer['checks'][1] = dict(answer['checks'][0])
        self.save_answer(directory, answer)
        with self.assertRaisesRegex(ValueError, 'prelocked seeded sample'):
            s1.collect(directory, imported, selection)

    def test_clock_confirmation_requires_boolean_true(self):
        directory, job = self.emit()
        imported, selection, answer = self.answer(directory, job)
        answer['checks'][0]['clock_confirmed'] = 'true'
        self.save_answer(directory, answer)
        with self.assertRaisesRegex(ValueError, 'unresolved predicate/population/clock'):
            s1.collect(directory, imported, selection)

    def test_unresolved_check_requires_reason(self):
        directory, job = self.emit()
        imported, selection, answer = self.answer(directory, job)
        answer['checks'][0]['status'] = 'unresolved'
        self.save_answer(directory, answer)
        with self.assertRaisesRegex(ValueError, 'requires reason'):
            s1.collect(directory, imported, selection)

    def test_known_start_error_does_not_silently_rewrite_stock(self):
        directory, job = self.emit()
        _, _, answer = self.answer(directory, job)
        answer['checks'][0].update(status='known_error', first_full_exposure_period=2002,
                                  corrected_start_period=2002)
        self.save_answer(directory, answer)
        result = s1.run(self.ctx)
        self.assertEqual(result.disposition, Disposition.PARKED)
        self.assertIn('new prospective', result.reason)
        self.assertFalse((self.ctx.design_dir / 'TREATMENT.json').exists())

    def test_current_partial_and_future_years_cannot_enter_s1(self):
        # Guard executes before source-window construction; no invented panel
        # rows can turn a July/current-year snapshot into observed history.
        for end in (2026, 2027):
            cfg = dict(self.cfg, window=[2000, end])
            with self.subTest(end=end), self.assertRaisesRegex(ValueError, 'current partial or future'):
                s1.load_inputs(cfg)

    def test_panel_hash_must_be_bound_by_catalog(self):
        catalog = json.loads(self.catalog.read_text())
        catalog['panels'][0]['sha256'] = '0' * 64
        self.catalog.write_text(json.dumps(catalog) + '\n')
        cfg = dict(self.cfg, catalog_sha256=core.digest(self.catalog))
        with self.assertRaisesRegex(ValueError, 'Panel not bound to catalog'):
            s1.load_inputs(cfg)

    def test_row_partial_future_flag_rejected_even_without_catalog_cutoff(self):
        panel = Path(self.cfg['panel_path'])
        rows = core.read_panel(panel)
        catalog = json.loads(self.catalog.read_text())
        catalog['variables'][0].pop('historical_year_end')
        for status in ('future_snapshot_unverified', 'current_year_partial', 'partial_start_year'):
            rows[0]['year_status'] = status
            core.write_panel(panel, rows)
            catalog['panels'][0]['sha256'] = core.digest(panel)
            self.catalog.write_text(json.dumps(catalog) + '\n')
            cfg = dict(self.cfg, panel_sha256=core.digest(panel), catalog_sha256=core.digest(self.catalog))
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'Partial/future source period'):
                s1.load_inputs(cfg)


if __name__ == '__main__':
    unittest.main()
