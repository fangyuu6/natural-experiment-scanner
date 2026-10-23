"""V8b fixed sample geometry; invented treatment metadata only."""
import unittest
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from scripts.l2_pipeline.v8b import (select_states, validate_audit_clock, CALIBRATION_SEED,
                                    AUDIT_SEED, OPERATION_TARGET, LEGACY_TARGET, TEMPLATE)


class V8bJobsTests(unittest.TestCase):
    def rows(self, n):
        return [dict(unit=f'S{i:02}', status='treated', primary_excluded=False) for i in range(n)]

    def test_disjoint_fixed_seeds(self):
        rows=self.rows(30); previous=[r['unit'] for r in rows[:10]]
        plan=select_states(rows,previous)
        self.assertEqual(plan['status'],'PREPARED')
        self.assertEqual(len(plan['calibration']),8)
        self.assertEqual(len(plan['fresh_audit']),10)
        self.assertFalse(set(plan['calibration']) & set(previous))
        self.assertFalse(set(plan['calibration']) & set(plan['fresh_audit']))
        self.assertNotEqual(CALIBRATION_SEED,AUDIT_SEED)
        self.assertEqual(select_states(list(reversed(rows)),previous),plan)

    def test_insufficient_calibration_does_not_resample(self):
        rows=self.rows(10)
        self.assertEqual(select_states(rows,[r['unit'] for r in rows[:10]])['status'],
                         'PARKED_INSUFFICIENT_DISJOINT_CALIBRATION')

    def test_codebook_skips_calibration_and_preserves_ten(self):
        rows=self.rows(10)
        plan=select_states(rows,[r['unit'] for r in rows],codebook=True)
        self.assertEqual(plan['status'],'PREPARED')
        self.assertEqual(plan['calibration'],[])
        self.assertEqual(len(plan['fresh_audit']),10)

    def test_never_treated_and_excluded_do_not_fill_sample(self):
        rows=self.rows(9)+[dict(unit='NEVER',status='never_treated'),
                             dict(unit='EXCLUDED',status='treated',primary_excluded=True)]
        self.assertEqual(select_states(rows,[])['status'],'PARKED_INSUFFICIENT_FRESH_AUDIT')

    def test_small_treated_universe_calibration_can_include_never_states(self):
        rows=self.rows(12)+[dict(unit=f'N{i:02}',status='never_treated') for i in range(38)]
        plan=select_states(rows,[r['unit'] for r in rows[:10]])
        self.assertEqual(plan['status'],'PREPARED')
        self.assertEqual(len(plan['calibration']),8)
        self.assertEqual(len(plan['fresh_audit']),10)
        self.assertEqual(plan['calibration_treated_n'],2)
        self.assertEqual(sum(u.startswith('S') for u in plan['calibration']),2)
        self.assertTrue(all(u.startswith('S') for u in plan['fresh_audit']))
        self.assertFalse(set(plan['calibration']) & set(plan['fresh_audit']))
        self.assertFalse(set(plan['calibration']) & {r['unit'] for r in rows[:10]})

    def clock_fixture(self, target=OPERATION_TARGET):
        rows = [dict(unit=f'S{i}', status='treated', start_period=2005) for i in range(10)]
        job = SimpleNamespace(contract=dict(audit_target=target, import_sha256='invented',
            sample=[r['unit'] for r in rows], sampled_tuples=rows))
        answer = dict(import_sha256='invented', checks=[dict(unit=r['unit'], status='match',
            panel_year=2005, official_locator='https://official.example/operative-clause',
            official_operational_date='2005-01-01', official_operational_calendar_year=2005,
            official_effective_calendar_year=2005) for r in rows])
        return job, answer

    def test_operation_clock_accepts_later_operation_without_changing_original(self):
        job, answer = self.clock_fixture()
        for check, nominal, operation in zip(answer['checks'], (2015, 2019), (2016, 2020)):
            check.update(status='known_error', panel_year=nominal,
                official_effective_calendar_year=nominal,
                official_operational_date=f'{operation}-01-01',
                official_operational_calendar_year=operation, corrected_start_period=operation,
                statutory_operative_date=f'{operation}-01-01')
            job.contract['sampled_tuples'][answer['checks'].index(check)]['start_period'] = nominal
        original = copy.deepcopy(answer)
        result = validate_audit_clock(job, answer)
        self.assertTrue(result['operation_clock_validated'])
        self.assertFalse(result['s1_ready'])
        self.assertEqual(answer, original)

    def test_legacy_nominal_year_refuses_ca_and_co_style_operation_conflicts(self):
        for nominal, operation, date_field in ((2015, 2016, 'statutory_operative_date'),
                                               (2019, 2020, 'operational_activation_date')):
            job, answer = self.clock_fixture(LEGACY_TARGET)
            check = answer['checks'][0]
            check.update(panel_year=nominal, official_effective_calendar_year=nominal,
                         **{date_field: f'{operation}-01-01'})
            job.contract['sampled_tuples'][0]['start_period'] = nominal
            with self.assertRaisesRegex(ValueError, 'nominal year contradicts operation'):
                validate_audit_clock(job, answer)

    def test_legacy_clock_without_conflicting_evidence_remains_readable(self):
        job, answer = self.clock_fixture(LEGACY_TARGET)
        self.assertFalse(validate_audit_clock(job, answer)['operation_clock_validated'])

    def test_operation_evidence_is_required_and_no_nominal_fallback(self):
        for changes in ({'official_operational_date': None},
                        {'official_operational_calendar_year': 2004},
                        {'operational_activation_date': '2006-01-01'},
                        {'status': 'known_error', 'corrected_start_period': 2004},
                        {'panel_year': 2004}):
            job, answer = self.clock_fixture()
            answer['checks'][0].update(changes)
            with self.assertRaises(ValueError):
                validate_audit_clock(job, answer)

    def test_new_template_binds_operation_and_preserves_nominal_provenance(self):
        text = TEMPLATE.read_text()
        self.assertEqual(TEMPLATE.name, 'v8b_operational_auditor.md')
        self.assertIn('official_operational_calendar_year', text)
        self.assertIn('official_operational_date', text)
        self.assertIn('HUB_RULING_C11_IN_OPERATION', text)
        self.assertIn('official_effective_date', text)

    def test_collector_checks_clock_before_promotion_and_keeps_old_original(self):
        from scripts.l2_pipeline.agents.collect import collect_job
        job, answer = self.clock_fixture(LEGACY_TARGET)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job.contract.update(jobs_kind='v8b_calendar_year', source_type='research_panel',
                                source_sha256='source', selection_sha256='selection')
            job.stage='S1'; job.role='auditor'; job.design_id='synthetic'; job.job_path='invented'
            job.job_sha256='job'; job.output_path=str(root/'audit.json'); job.output_dir=str(root)
            job.promote_path=str(root/'PROMOTED.json')
            answer.update(design_id='synthetic', role='auditor', source_sha256='source',
                          selection_sha256='selection', blindness_attestation=dict(
                              other_submissions_read=False, outcome_associations_read=False))
            answer['checks'][0].update(operational_activation_date='2006-01-01')
            original = json.dumps(answer)
            Path(job.output_path).write_text(original)
            with patch('scripts.l2_pipeline.agents.collect.load_job', return_value=job):
                with self.assertRaisesRegex(ValueError, 'nominal year contradicts operation'):
                    collect_job('invented')
                self.assertFalse(Path(job.promote_path).exists())
                self.assertFalse((root/'COLLECTION.json').exists())
                self.assertEqual(Path(job.output_path).read_text(), original)
                answer['checks'][0].pop('operational_activation_date')
                Path(job.output_path).write_text(json.dumps(answer))
                self.assertTrue(collect_job('invented')['validated'])
                normalized=json.loads(Path(job.promote_path).read_text())
                self.assertFalse(normalized['s1_ready'])
                self.assertTrue(normalized['hub_acceptance_required'])

if __name__=='__main__': unittest.main()
