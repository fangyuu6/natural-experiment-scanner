"""Invented outcomes only: registered counts and current-panel CI fallback."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline import registered
from scripts.l2_pipeline.engine import calibration, inference
from scripts.l2_pipeline.engine.runner import registered_multiplier_count
from scripts.l2_pipeline.tests.test_interval_review_fixes import evaluation, fixture, result
from scripts.l2_pipeline.tests.test_s6_review_fixes import bundle


class RegisteredMultiplierTests(unittest.TestCase):
    def test_one_numeric_registered_count_and_invalid_types(self):
        self.assertEqual(registered_multiplier_count({'analysis':{'multiplier_count':4999}}),4999)
        self.assertEqual(registered_multiplier_count({}),4999)
        for count in (True,1,0,-1,'4999',4999.):
            with self.subTest(count=count),self.assertRaises(ValueError):
                registered_multiplier_count({'analysis':{'multiplier_count':count}})

    def test_primary_sensitivity_omission_and_placebo_share_registered_count(self):
        # A different invented count exposes branch-specific hardcoding without
        # fitting any real policy outcome or treating RI labels as multipliers.
        b,rows=bundle();b['multiplier_count']=37
        from scripts.l2_pipeline.engine.runner import fit_primary
        from scripts.l2_pipeline.engine.ri import diagnostics
        with patch('scripts.l2_pipeline.engine.runner.fit_primary',wraps=fit_primary) as fits, \
             patch('scripts.l2_pipeline.engine.ri.diagnostics',wraps=diagnostics) as placebo:
            out=registered.run_registered(b,load_panel=lambda name:(rows,{'passed':True}))
        self.assertEqual(out['status'],'COMPLETED')
        self.assertEqual([call.kwargs['B'] for call in fits.call_args_list],[37,37,37])
        self.assertEqual([call.kwargs['B'] for call in placebo.call_args_list],[37,37])
        self.assertEqual(out['omissions']['omit_state_X']['result']['inference']['B'],37)
        self.assertEqual(out['noise_floor']['feasibility']['requested_draws'],999)


class CurrentPanelFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design,op,cls.outcomes=fixture()
        cls.evaluation=evaluation(cls.design,op)

    def report(self,certificate,outcomes=None):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'certification.json';path.write_text(json.dumps(certificate))
            return inference.apply_reporting(result(),self.design,
                {k:0. for k in self.outcomes} if outcomes is None else outcomes,
                primary=True,evaluation_path=path,design_id='invented',expected_designs=['invented'])

    def certificate(self,failed=None):
        record=copy.deepcopy(self.evaluation)
        if failed:
            for row in record['world_records']:
                row[failed]={'status':'failed','reason':'Invented failed certification'}
            record['summaries']=inference.interval_summaries(record['world_records'])
            record['world_records_sha256']=inference.object_sha256(record['world_records'])
            record['heldout_world_records']=record['world_records'][:100]
            record['heldout_summaries']=inference.interval_summaries(record['heldout_world_records'])
            record['heldout_world_records_sha256']=inference.object_sha256(record['heldout_world_records'])
        return calibration.certify_intervals({'invented':record},expected_designs=['invented'])

    def test_webb_passes_certification_but_current_failure_uses_certified_ct(self):
        certificate=self.certificate()
        self.assertEqual(certificate['selected_method'],'webb_wcu_t')
        # Production 4999-draw candidate algebra is real, on invented zeros.
        out=self.report(certificate)
        self.assertEqual(out['overall']['ci95'],[0.,0.])
        self.assertEqual(out['overall']['interval']['method'],'control_path_ct')
        self.assertTrue(out['overall']['interval']['reported'])
        reporting=out['inference']['interval_reporting']
        self.assertEqual(reporting['calibrated_method'],'webb_wcu_t')
        self.assertEqual(reporting['selected_method'],'control_path_ct')
        self.assertIn('studentization',reporting['current_panel_failures']['webb_wcu_t'])

    def test_current_failure_cannot_rescue_with_uncertified_ct(self):
        out=self.report(self.certificate(failed='control_path_ct'))
        self.assertIsNone(out['overall']['ci95'])
        self.assertFalse(out['overall']['interval']['reported'])
        self.assertEqual(out['inference']['interval_reporting']['certified_methods'],['webb_wcu_t'])
        self.assertIn('studentization',out['overall']['interval']['reason'])

    def test_feasible_webb_keeps_priority_while_ct_failure_is_independent(self):
        outcomes=dict(self.outcomes);del outcomes['G',0]
        out=self.report(self.certificate(),outcomes)
        self.assertTrue(out['overall']['interval']['reported'])
        self.assertEqual(out['overall']['interval']['method'],'webb_wcu_t')
        self.assertIn('complete CT donor',out['inference']['interval_reporting']['current_panel_failures']['control_path_ct'])

    def test_both_current_candidates_fail_reports_point_plus_ri_reason(self):
        outcomes={k:0. for k in self.outcomes};del outcomes['G',0]
        out=self.report(self.certificate(),outcomes)
        self.assertIsNone(out['overall']['ci95'])
        self.assertFalse(out['overall']['interval']['reported'])
        failures=out['inference']['interval_reporting']['current_panel_failures']
        self.assertEqual(set(failures),set(inference.METHODS))
        self.assertIn('control_path_ct',out['overall']['interval']['reason'])


if __name__=='__main__':unittest.main()
