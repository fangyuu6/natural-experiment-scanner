"""Prevent convenience outcomes or legal extrapolation in prospective planning."""
import csv
import copy
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline import run3_plan as plan
from scripts.l2_pipeline.first_order import first_order_gate


class Run3AcceptanceTests(unittest.TestCase):
    def states(self, year=2003):
        return {str(i): dict(adopt_year=year, start_year=2000, end_year=2010)
                for i in range(6)}

    def test_observed_support_thresholds_and_no_earliest_trimming(self):
        periods = list(range(2000, 2006))
        self.assertTrue(plan.support_inventory(periods, self.states())['passed'])
        units = self.states(); units['early'] = dict(adopt_year=2001, start_year=2000, end_year=2010)
        self.assertFalse(plan.support_inventory(periods, units)['passed'])
        self.assertFalse(plan.support_inventory([2000, 2002, 2004, 2006, 2008], self.states())['passed'])
        self.assertFalse(plan.support_inventory([], self.states())['passed'])
        self.assertFalse(plan.support_inventory(list(range(2004, 2011)), self.states())['passed'])

    def test_missing_state_cells_do_not_inherit_global_years(self):
        periods = list(range(2000, 2006))
        available = {u: periods for u in self.states()}; available['0'] = [2003, 2004, 2005]
        result = plan.support_inventory(periods, self.states(), available_by_unit=available)
        self.assertFalse(result['passed']); self.assertEqual(len(result['eligible_treated_states']), 5)

    def test_earliest_cohort_cannot_borrow_other_states_pre_periods(self):
        periods = list(range(2000, 2007))
        states = self.states(2004)
        states['early'] = dict(adopt_year=2003, start_year=2000, end_year=2010)
        observed = {u: periods for u in states}; observed['early'] = [2003, 2004, 2005, 2006]
        result = plan.support_inventory(periods, states, available_by_unit=observed)
        self.assertEqual(result['earliest_analyzed_cohort'], 2003)
        self.assertEqual(len(result['eligible_treated_states']), 6)
        self.assertFalse(result['passed'])

    def candidate(self):
        from scripts.l2_pipeline.tests.test_v6_outcome_first import OutcomeFirstTests
        c = OutcomeFirstTests().candidate('test')
        c['first_order'].update(target_outcome='wages', primary_outcome='wages')
        c['outcome']['years'] = [2001, 2006]
        return c

    def test_acceptance_requires_all_three_conditions_and_never_mutates_card(self):
        c = self.candidate(); prior = copy.deepcopy(c)
        purpose = dict(status='VERIFIED', target_outcome='wages', target_population='Eligible workers')
        periods = list(range(2001, 2007))
        states = self.states(2004); available = {u: periods for u in states}
        result = plan.s0_acceptance_decision(purpose, True, c, states, available)
        self.assertEqual(result['decision'], 'ACCEPT'); self.assertFalse(result['execution_authorized'])
        self.assertEqual(c, prior)
        for changed, validated, reason in (
            (dict(purpose, status='UNRESOLVED'), True, 'purpose_unresolved'),
            (purpose, False, 'collector_contract_invalid'),
            (dict(purpose, target_outcome='hours'), True, 'same_construct_connector_unavailable')):
            result = plan.s0_acceptance_decision(changed, validated, c, states, available)
            self.assertEqual(result['decision'], 'PARK'); self.assertEqual(result['primary_reason'], reason)

    def test_same_construct_population_proxy_cannot_bypass_empty_coverage(self):
        c = self.candidate()
        c['outcome'].update(connector='brfss_state_prevalence', measure_id='colorectal_screening_pct', years=[2001, 2010])
        c['first_order']['primary_outcome'] = 'colorectal_screening_pct'
        c['treatment']['source_window']['end_year'] = 2010
        p = dict(status='VERIFIED', target_outcome='colorectal_cancer_screening_receipt_pct_among_statutorily_covered_nonsymptomatic_enrollees', target_population='Covered enrollees')
        result = plan.s0_acceptance_decision(p, True, c, self.states(), {'0': [2016, 2018]})
        self.assertTrue(result['conditions']['b']['passed'])
        self.assertEqual(result['primary_reason'], 'coverage_no_overlap')
        self.assertEqual(result['conditions']['c']['actual_period_years'], [])

    def test_dispatch_selection_empty_and_mismatches_fail_closed(self):
        report = dict(records=[], accepted_design_ids=[], accepted_s1_job_paths=[])
        self.assertEqual(plan.accepted_s1_paths(report), [])
        report['accepted_s1_job_paths'] = ['parked.md']
        with self.assertRaises(ValueError): plan.accepted_s1_paths(report)
        row = dict(design_id='test', decision='ACCEPT', failing_conditions=[],
                   conditions={k: dict(passed=True) for k in ('a', 'b', 'c')}, s1_job_path='only.md')
        report = dict(records=[row], accepted_design_ids=['test'], accepted_s1_job_paths=['only.md'])
        self.assertEqual(plan.accepted_s1_paths(report), ['only.md'])
        row['conditions']['c']['passed'] = False
        with self.assertRaises(ValueError): plan.accepted_s1_paths(report)

    def test_empty_dispatch_does_not_call_dispatcher_and_hash_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'acceptance.json'
            path.write_text(json.dumps(dict(records=[], accepted_design_ids=[], accepted_s1_job_paths=[])))
            with patch('scripts.l2_pipeline.agents.dispatch.main') as dispatch:
                result = plan.dispatch_accepted_s1(path, plan.sha(path), dry_run=True)
                self.assertEqual(result['selected_jobs'], 0)
                self.assertEqual(result['model_calls'], 0)
                dispatch.assert_not_called()
                with self.assertRaises(ValueError): plan.dispatch_accepted_s1(path, None)
                with self.assertRaises(ValueError): plan.dispatch_accepted_s1(path, 'wrong')


class Run3PlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pol=json.loads((plan.ROOT/'data/l2_pipeline/cards/run2/_meta/DK_ADOPTION_WINDOW_MAP.json').read_text())
        with (plan.ROOT/'data/l3_pooled/downloads/SPID_v1.2_policies.tab').open() as handle:
            cls.metadata={r['policy']:r for r in csv.DictReader(handle,delimiter='\t')}

    def candidate(self, pid):
        i,row=next((i,r) for i,r in enumerate(plan.CATALOG,1) if r[0]==pid)
        return plan._card(*row,i,self.pol,self.metadata)

    def test_smoke_exposure_and_savings_are_not_replaced_by_behavior_or_enrollment(self):
        for pid,measure in [('smokeban','secondhand_smoke_exposure_pct'),
                            ('prepaid','education_savings_participation')]:
            card=self.candidate(pid)
            self.assertEqual(card['outcome']['measure_id'],measure)
            self.assertEqual(card['first_order']['availability'],'unavailable')
            self.assertFalse(first_order_gate(card)['passed'])

    def test_modern_outcome_window_does_not_extend_dk_zeros(self):
        card=self.candidate('labor_minwage_abovefe')
        self.assertLessEqual(card['outcome']['years'][-1],card['treatment']['source_window']['end_year'])
        self.assertEqual(card['treatment']['source_window'],self.pol['labor_minwage_abovefe']['source_window'])
        self.assertFalse(card['automation']['run3']['legal_coverage_extension_needed'])
        self.assertFalse(first_order_gate(card)['passed'])

    def test_outcome_first_mapping_goal_excludes_alias_padding(self):
        from scripts.l2_pipeline.first_order import target_mapping
        ids={row[0] for row in plan.CATALOG}
        self.assertFalse(ids & {'dui08','dwi reform','zerotoleranceapis','zerotolerancelaw','drugs_boehmke_zeroto','perforfun'})
        values=[self.candidate(row[0]) for row in plan.CATALOG]
        self.assertGreaterEqual(sum(target_mapping(c)['mapped'] for c in values),20)
        for c in values:
            coverage=c['automation']['coverage_window']
            if not coverage['parked']:
                self.assertLessEqual(c['outcome']['years'][-1],c['treatment']['source_window']['end_year'])

    def test_alias_ids_and_unverified_purposes_never_claim_thirty_ready_studies(self):
        self.assertEqual(len(plan.CATALOG),30)
        self.assertEqual(len({r[0] for r in plan.CATALOG}),30)
        for row in plan.CATALOG:
            card=self.candidate(row[0])
            self.assertFalse(first_order_gate(card)['passed'])
            self.assertEqual(card['analysis']['spec_status'],'FRESH_SPEC_FORECAST_AND_FREEZE_REQUIRED')
