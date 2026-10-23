"""Tracker import boundaries, reversals, audit provenance and no model calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline import policy_tracker_intake as intake
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.storage import ensure_once, sha


def policy_fixture():
    units = ['AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID']
    return dict(tracker_id='fixture', policy_id='naloxone_authority',
        title='Naloxone authority', definition='Permit naloxone distribution to prevent overdose mortality',
        source_url='https://example.test/history', source_path='', sha256='', size_bytes=0,
        license='Fixture public data', as_of='2024-12-31',
        coverage=dict(start_year=2010, end_year=2024), states=units,
        history_complete=True, repeals_included=True,
        annual_rows=[dict(unit=unit, year=y, active=int(y >= 2014+(i%2)),
            locator=f'Fixture {unit} year {y}') for i,unit in enumerate(units) for y in range(2010,2025)],
        outcome=dict(connector='nchs_mortality', measure_id='drug_overdose_deaths_count',
            population_filters={}, years=[2010,2024], outcome_unit='deaths'))


class TrackerHistoryTests(unittest.TestCase):
    def test_explicit_zeros_only_and_reversal_preserved(self):
        p = policy_fixture()
        for row in p['annual_rows']:
            if row['unit']=='AL' and row['year']==2017:
                row['active']=0
        history = intake.normalize_history(p)
        al = next(r for r in history['records'] if r['unit']=='AL')
        self.assertEqual(al['reversal_years'], [2017])
        self.assertEqual(al['intervals'], [dict(start_period=2014,end_period=2016),dict(start_period=2018,end_period=2024)])
        self.assertTrue(al['primary_excluded'])
        self.assertTrue(history['history_complete'])
        self.assertEqual(history['outside_source_window'], 'unknown')

    def test_missing_year_never_becomes_never_treated(self):
        p = policy_fixture()
        for row in p['annual_rows']:
            if row['unit']=='AL':
                row['active']=0
        p['annual_rows']=[r for r in p['annual_rows'] if not (r['unit']=='AL' and r['year']==2013)]
        history=intake.normalize_history(p)
        al=next(r for r in history['records'] if r['unit']=='AL')
        self.assertEqual(al['status'],'unknown')
        self.assertEqual(al['unknown_years'],[2013])
        self.assertFalse(history['history_complete'])

    def test_baseline_active_is_left_censored(self):
        p=policy_fixture()
        for row in p['annual_rows']:
            if row['unit']=='AL': row['active']=1
        al=next(r for r in intake.normalize_history(p)['records'] if r['unit']=='AL')
        self.assertEqual(al['status'],'unknown')
        self.assertIsNone(al['start_period'])
        self.assertTrue(al['left_censored'])

    def test_quarterly_mixed_year_is_not_full_year_exposure(self):
        rows=[dict(unit='AL',year=2012,quarter=q,active=int(q>=3),locator=f'Q{q}') for q in range(1,5)]
        rows += [dict(unit='AL',year=2013,quarter=q,active=1,locator=f'2013Q{q}') for q in range(1,5)]
        annual,events=intake.annualize_quarters(rows)
        self.assertIsNone(annual[0]['active'])
        self.assertTrue(annual[0]['transition_year'])
        self.assertEqual(annual[1]['active'],1)
        self.assertFalse(annual[1]['daily_full_year_certified'])
        self.assertEqual(events[0]['quarter'],3)

    def test_invalid_or_duplicate_cells_fail_closed(self):
        p=policy_fixture(); p['annual_rows'].append(p['annual_rows'][0])
        with self.assertRaisesRegex(ValueError,'Duplicate'):intake.normalize_history(p)
        p=policy_fixture(); p['annual_rows'][0]['active']=True
        with self.assertRaisesRegex(ValueError,'0/1/null'):intake.normalize_history(p)

    def test_earliest_cohort_obeys_actual_connector_bounds(self):
        p=policy_fixture(); history=intake.normalize_history(p)
        card=intake.build_card(p,p['outcome'])
        self.assertEqual(card['outcome']['years'],[2010,2017])
        check=intake.earliest_cohort_check(card,history)
        self.assertTrue(check['passed'])
        self.assertEqual(check['earliest_cohort'],2014)
        p['outcome']['years']=[2013,2024]
        card=intake.build_card(p,p['outcome'])
        self.assertFalse(intake.earliest_cohort_check(card,history)['passed'])
        self.assertFalse(card['first_order']['evidence_status']=='verified_by_hub')

    def test_existing_same_construct_duplicate_rejected(self):
        p=policy_fixture()
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            intake.build_card(p,p['outcome'],prior_design=dict(design_id='old',first_order_same_construct=True))
        card=intake.build_card(p,p['outcome'],prior_design=dict(design_id='old',outcome_id='employment'))
        self.assertEqual(card['design_id'],'old_fo')
        self.assertFalse(card['automation']['first_order_reintake']['prior_result_values_read'])

    def test_known_dated_transition_preserves_start_and_excludes_clean_pre(self):
        p=policy_fixture()
        p['interval_rows']=[dict(unit=u,start_date='2014-07-01',end_date='2024-12-31',active=1,locator='Dated fixture') for u in p['states']]
        for row in p['annual_rows']:
            row.update(missing_days=0,unknown_days=0,conflict_days=0)
            if row['unit']=='AL' and row['year']==2013:
                row.update(active=None,transition_year=True)
        history=intake.normalize_history(p)
        al=next(r for r in history['records'] if r['unit']=='AL')
        self.assertEqual(al['status'],'treated')
        self.assertEqual(al['start_period'],2014)
        self.assertFalse(al['primary_excluded'])
        self.assertTrue(history['history_complete'])
        card=intake.build_card(p,p['outcome'])
        check=intake.earliest_cohort_check(card,history)
        self.assertEqual(check['partial_exposure_periods_excluded'],[2013])
        self.assertEqual(check['pre_periods'],[2010,2011,2012])

    def test_scoped_daily_inventory_keeps_future_gap_raw_but_not_in_scope(self):
        p=policy_fixture();p['history_complete']=False
        p['interval_rows']=[dict(unit=u,start_date='2010-01-01',end_date='2023-12-31',active=0,locator='Dated fixture') for u in p['states']]
        for row in p['annual_rows']:
            row.update(missing_days=0,unknown_days=0,conflict_days=0)
            if row['year']==2024: row.update(active=None,missing_days=365)
        history=intake.normalize_history(p,scope=[2010,2017])
        self.assertTrue(history['history_complete'])
        self.assertFalse(history['tracker_declares_complete'])
        self.assertEqual(history['coverage']['end_year'],2017)
        self.assertEqual(history['raw_source_coverage']['end_year'],2024)
        self.assertTrue(any(r['year']==2024 and r['missing_days'] for r in history['raw_annual_rows']))

    def test_hub_purpose_acceptance_cannot_override_failed_earliest_cohort(self):
        from scripts.l2_pipeline.first_order import first_order_gate
        p=policy_fixture();card=intake.build_card(p,p['outcome'])
        card['first_order']['evidence_status']='verified_by_hub'
        gate=first_order_gate(card)
        self.assertFalse(gate['passed'])
        self.assertTrue(any('earliest' in issue.lower() for issue in gate['issues']))

    def test_scoped_official_correction_preserves_original_and_overlay(self):
        p=policy_fixture();history=intake.normalize_history(p)
        original=copy.deepcopy(history['records'][0])
        checks=[dict(unit=original['unit'],status='known_error',corrected_start_period=2015,
                     official_locator='https://official.test/statute',
                     corrected_full_year_intervals=[dict(start_period=2015,end_period=2024)])]
        records,sensitivities,blockers=intake.corrected_records(history,checks)
        self.assertFalse(blockers);self.assertFalse(sensitivities)
        self.assertEqual(records[0]['start_period'],2015)
        self.assertEqual(records[0]['original_tracker_intervals'],original['intervals'])
        self.assertEqual(history['records'][0],original)
        checks[0]['corrected_start_period']=2009
        records,sensitivities,blockers=intake.corrected_records(history,checks)
        self.assertFalse(blockers)
        self.assertTrue(records[0]['primary_excluded'])
        self.assertEqual(sensitivities[0]['start_period'],2009)


class TrackerAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        p=policy_fixture()
        raw=self.root/'source.csv'; raw.write_text('fixture legal source\n')
        p.update(source_path=str(raw),sha256=sha(raw),size_bytes=raw.stat().st_size)
        history=intake.normalize_history(p); history['raw_source_sha256']=p['sha256']
        hp=ensure_once(self.root/'history.json',history)
        card=intake.build_card(p,p['outcome'],history_path=hp)
        cp=ensure_once(self.root/'card.json',card)
        self.ctx=StageContext(load_card(cp),self.root/'work',intake.REPO)
        self.jobs=self.root/'jobs'

    def tearDown(self):self.temp.cleanup()

    def test_only_ten_state_auditor_emitted_and_hash_sample_locked(self):
        from scripts.l2_pipeline.agents.jobs import load_job
        from scripts.l2_pipeline.agents.dispatch import printable
        result=intake.run(self.ctx,jobs_root=self.jobs)
        self.assertEqual(result.disposition,'parked')
        self.assertEqual(result.metrics['auditor_jobs'],1)
        self.assertEqual(result.metrics['coder_jobs'],0)
        jobs=list(self.jobs.rglob('*.md'))
        self.assertEqual(len(jobs),1)
        job=load_job(jobs[0]); intake.validate_tracker_job(job)
        self.assertEqual(len(job.contract['sample']),10)
        self.assertNotIn('A',job.contract)
        self.assertIn('--search exec',printable(job))
        self.assertFalse((self.ctx.design_dir/'TREATMENT.json').exists())
        prior=jobs[0].read_bytes()
        intake.run(self.ctx,jobs_root=self.jobs)
        self.assertEqual(jobs[0].read_bytes(),prior)

    def test_small_treated_universe_emits_no_audit(self):
        value=json.loads(self.ctx.card.path.read_text())
        hp=Path(value['treatment']['tracker_history_path'])
        history=json.loads(hp.read_text())
        history['records']=history['records'][:9]
        history['units']=history['units'][:9]
        new_hp=ensure_once(self.root/'small-history.json',history)
        value['treatment'].update(tracker_history_path=str(new_hp),tracker_history_sha256=sha(new_hp),universe=history['units'])
        new_cp=ensure_once(self.root/'small-card.json',value)
        ctx=StageContext(load_card(new_cp),self.root/'smallwork',intake.REPO)
        result=intake.run(ctx,jobs_root=self.root/'smalljobs')
        self.assertEqual(result.metrics['auditor_jobs'],0)
        self.assertEqual(result.metrics['sample_size'],9)

    def test_tampered_source_rejected(self):
        value=json.loads(self.ctx.card.path.read_text())
        Path(value['sources'][0]['path']).write_text('tampered')
        with self.assertRaisesRegex(ValueError,'source hash mismatch'):
            intake.run(self.ctx,jobs_root=self.jobs)

    def test_official_corrections_require_revised_interval_version(self):
        result=intake.run(self.ctx,jobs_root=self.jobs)
        job_path=next(self.jobs.rglob('*.md'))
        from scripts.l2_pipeline.agents.jobs import load_job
        job=load_job(job_path)
        checks=[dict(unit=u,status='match',official_locator='Official fixture') for u in job.contract['sample']]
        checks[0].update(status='known_error',corrected_start_period=2015)
        answer=dict(checks=checks)
        ensure_once(Path(job.output_path),answer)
        ensure_once(Path(job.promote_path),answer)
        with patch('scripts.l2_pipeline.agents.collect.collect_job',return_value={'validated':True}):
            result=intake.run(self.ctx,jobs_root=self.jobs)
        self.assertEqual(result.disposition,'parked')
        self.assertIn('corrections',result.reason)
        self.assertFalse((self.ctx.design_dir/'TREATMENT.json').exists())


if __name__=='__main__':unittest.main()
