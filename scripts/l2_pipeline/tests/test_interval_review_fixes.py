"""Invented-only regression evidence for interval reviewer MUSTs."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from scripts.l2_pipeline.engine import calibration, inference
from scripts.l2_pipeline.engine.design import build_design
from scripts.l2_pipeline.tests.test_equivalence import invented_fixture


def fixture():
    declaration,rows=invented_fixture()
    design=build_design(declaration,rows)
    keys=sorted(design['mask'])
    ui={u:i for i,u in enumerate(design['units'])}
    ti={t:i for i,t in enumerate(design['periods'])}
    op=dict(design=design,keys=keys,units=design['units'],periods=design['periods'],
            indices=([ui[u] for u,t in keys],[ti[t] for u,t in keys]))
    return design,op,{(u,t):y for u,t,y,_ in rows}


def kernel(g,Y,d):
    # Exact invented intervals allow production metadata validation cheaply.
    return dict(att=0.,baseline=[-1.,1.],webb_wcu_t=[-1.5,1.5],
                control_path_ct=[-1.7,1.7],failures={})


def evaluation(design,op,case_id='invented'):
    with patch('scripts.l2_pipeline.engine.inference.candidates',kernel):
        return calibration.evaluate_intervals(case_id,op,1.)


def result():
    return dict(group_time=[dict(ci95=[1,2])],event_study=[dict(simultaneous95=[1,2])],
        group=[],calendar=[],overall=dict(ci95=[1,2]),inference={})


class IntervalReviewFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design,cls.op,cls.outcomes=fixture()
        cls.evaluation=evaluation(cls.design,cls.op)
        cls.certificate=calibration.certify_intervals({'invented':cls.evaluation},expected_designs=['invented'])

    def test_registered_webb_first_and_ct_after_webb_failure(self):
        cases={k:dict(summaries={n:dict(passed=True) for n in inference.METHODS}) for k in ('a','b')}
        self.assertEqual(inference.select_method(cases,expected_designs=['a','b']),'webb_wcu_t')
        cases['b']['summaries']['webb_wcu_t']['passed']=False
        self.assertEqual(inference.select_method(cases,expected_designs=['a','b']),'control_path_ct')
        cases['a']['summaries']['control_path_ct']['passed']=False
        self.assertIsNone(inference.select_method(cases,expected_designs=['a','b']))
        with self.assertRaisesRegex(ValueError,'order'):
            inference.select_method(cases,expected_designs=['a','b'],order=inference.METHODS[::-1])
        with self.assertRaises(ValueError):
            inference.select_method(cases,expected_designs=['a'])
        cases['a']['summaries']['webb_wcu_t']['passed']=1
        with self.assertRaisesRegex(ValueError,'literal'):
            inference.select_method(cases,expected_designs=['a','b'])

    def test_complete_production_evidence_and_design_operator_binding(self):
        cert=self.certificate
        self.assertEqual(inference.validate_certification(cert,expected_designs=['invented'],
            design=self.design,design_id='invented'),'webb_wcu_t')
        self.assertEqual(len(cert['cases']['invented']['heldout_world_records']),100)
        mutations=(lambda c:c['cases']['invented']['counts'].update(B=99),
            lambda c:c['cases']['invented']['counts'].update(repetitions=10),
            lambda c:c['cases']['invented']['counts'].update(heldout=10),
            lambda c:c.update(expected_designs=['invented','omitted']),
            lambda c:c.update(selected_method='control_path_ct'),
            lambda c:c['cases']['invented'].update(heldout_world_records=[]),
            lambda c:c['cases']['invented']['summaries']['webb_wcu_t'].update(coverage=0),
            lambda c:c['cases']['invented']['summaries']['webb_wcu_t'].update(passed=1),
            lambda c:c['cases']['invented'].update(world_records_sha256='0'*64))
        for mutate in mutations:
            c=copy.deepcopy(cert);mutate(c)
            with self.subTest(mutation=mutate),self.assertRaises(ValueError):
                inference.validate_certification(c,expected_designs=['invented'])
        changed=copy.deepcopy(self.design)
        changed['exposure']['A'][0]['clean']=False
        with self.assertRaisesRegex(ValueError,'design/operator'):
            inference.validate_certification(cert,expected_designs=['invented'],design=changed,design_id='invented')
        changed=copy.deepcopy(self.design)
        changed['cells'][-1]['base_period']-=1
        with self.assertRaisesRegex(ValueError,'design/operator'):
            inference.validate_certification(cert,expected_designs=['invented'],design=changed,design_id='invented')
        with self.assertRaisesRegex(ValueError,'missing'):
            inference.validate_certification(cert,expected_designs=['invented'],design=self.design,design_id='other')

    def test_invalid_or_absent_cert_suppresses_every_interval(self):
        for value in (None,{'selected_method':'control_path_ct'},dict(self.certificate,selected_method='control_path_ct')):
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/'certificate.json'
                if value is not None:p.write_text(json.dumps(value))
                out=inference.apply_reporting(result(),self.design,self.outcomes,
                    primary=True,evaluation_path=p if value else None,
                    design_id='invented',expected_designs=['invented'])
                self.assertIsNone(out['overall']['ci95'])
                self.assertIsNone(out['group_time'][0]['ci95'])
                self.assertIsNone(out['event_study'][0]['simultaneous95'])
                self.assertFalse(out['overall']['interval']['reported'])
                self.assertFalse(out['inference']['interval_reporting']['certification_valid'])

    def test_valid_certificate_reports_only_primary_overall(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'certificate.json';p.write_text(json.dumps(self.certificate))
            for primary in (True,False):
                out=inference.apply_reporting(result(),self.design,self.outcomes,primary=primary,
                    evaluation_path=p,design_id='invented',expected_designs=['invented'])
                self.assertEqual(out['overall']['interval']['reported'],primary)
                self.assertIsNone(out['group_time'][0]['ci95'])
                self.assertTrue(out['inference']['interval_reporting']['certification_valid'])
                if primary:self.assertEqual(out['overall']['interval']['method'],'webb_wcu_t')

    def test_webb_degeneracy_retains_ct_and_selected_ct_never_runs_webb(self):
        g=inference.geometry(self.design)
        Y=np.zeros_like(g['H'])
        d=inference.draws(g,99,1)
        rec=inference.candidates(g,Y,d)
        self.assertIsNone(rec['webb_wcu_t'])
        self.assertIn('studentization',rec['failures']['webb_wcu_t'])
        self.assertEqual(rec['control_path_ct'],[0.,0.])
        selected=inference.candidates(g,Y,{'donors':d['donors']},methods=('control_path_ct',))
        self.assertEqual(selected['control_path_ct'],[0.,0.])
        Y=inference.outcome_matrix(g,self.outcomes)
        g['donors']=g['donors'][:4]
        rec=inference.candidates(g,Y,d)
        self.assertIsNotNone(rec['webb_wcu_t'])
        self.assertIsNone(rec['control_path_ct'])
        self.assertIn('five',rec['failures']['control_path_ct'])

    def test_no_ct_donors_or_missing_donor_cell_do_not_block_webb(self):
        design=copy.deepcopy(self.design)
        design['CT_complete_path_reference_controls']=[]
        g=inference.geometry(design)
        self.assertEqual(g['donors'],[])
        d=inference.draws(g,99,1)
        self.assertIsNone(d['donors'])
        rec=inference.candidates(g,inference.outcome_matrix(g,self.outcomes),d)
        self.assertIsNotNone(rec['webb_wcu_t'])
        self.assertIsNone(rec['control_path_ct'])
        g=inference.geometry(self.design)
        outcomes=dict(self.outcomes);del outcomes['G',0]
        rec=inference.candidates(g,inference.outcome_matrix(g,outcomes),inference.draws(g,99,1))
        self.assertIsNotNone(rec['webb_wcu_t'])
        self.assertIn('complete CT donor',rec['failures']['control_path_ct'])

    def test_certified_ct_fallback_reporting_survives_current_webb_degeneracy(self):
        def webb_fails(g,Y,d):
            r=kernel(g,Y,d);r['webb_wcu_t']=None
            r['failures']={'webb_wcu_t':'Degenerate Webb studentization'}
            return r
        with patch('scripts.l2_pipeline.engine.inference.candidates',webb_fails):
            rec=calibration.evaluate_intervals('invented',self.op,1.)
        certificate=calibration.certify_intervals({'invented':rec},expected_designs=['invented'])
        self.assertEqual(certificate['selected_method'],'control_path_ct')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'certification.json';path.write_text(json.dumps(certificate))
            out=inference.apply_reporting(result(),self.design,{k:0. for k in self.outcomes},primary=True,
                evaluation_path=path,design_id='invented',expected_designs=['invented'])
            self.assertTrue(out['overall']['interval']['reported'])
            self.assertEqual(out['overall']['interval']['method'],'control_path_ct')
            self.assertEqual(out['overall']['ci95'],[0.,0.])

    def test_small_numerical_worlds_keep_failures_but_cannot_certify(self):
        rec=calibration.evaluate_intervals('invented',self.op,1.,repetitions=8,B=39,heldout=4)
        self.assertFalse(rec['production_ready'])
        self.assertEqual(len(rec['world_records']),8)
        with self.assertRaisesRegex(ValueError,'counts'):
            calibration.certify_intervals({'invented':rec},expected_designs=['invented'])
        def webb_fails(g,Y,d):
            r=kernel(g,Y,d);r['webb_wcu_t']=None;r['failures']={'webb_wcu_t':'Degenerate Webb studentization'};return r
        with patch('scripts.l2_pipeline.engine.inference.candidates',webb_fails):
            rec=calibration.evaluate_intervals('invented',self.op,1.,repetitions=8,B=39,heldout=4)
        self.assertEqual(rec['summaries']['webb_wcu_t']['failures'],8)
        self.assertFalse(rec['summaries']['webb_wcu_t']['passed'])
        self.assertTrue(rec['summaries']['control_path_ct']['passed'])
        self.assertEqual(rec['summaries']['control_path_ct']['repetitions'],8)

    def test_coverage_width_gates_are_recomputed_from_world_evidence(self):
        for failed_gate in ('coverage','median_width','p95_width'):
            rec=copy.deepcopy(self.evaluation)
            for i,r in enumerate(rec['world_records']):
                if failed_gate=='coverage' and i<80:ci=[.1,.2]
                elif failed_gate=='median_width':ci=[-3.,3.]
                elif failed_gate=='p95_width' and i<100:ci=[-4.,4.]
                else:continue
                for name in inference.METHODS:
                    r[name]=dict(status='ok',ci95=ci,covered=bool(ci[0]<=0<=ci[1]),width=ci[1]-ci[0])
            rec['summaries']=inference.interval_summaries(rec['world_records'])
            rec['world_records_sha256']=inference.object_sha256(rec['world_records'])
            rec['heldout_world_records']=rec['world_records'][:100]
            rec['heldout_summaries']=inference.interval_summaries(rec['heldout_world_records'])
            rec['heldout_world_records_sha256']=inference.object_sha256(rec['heldout_world_records'])
            cert=calibration.certify_intervals({'invented':rec},expected_designs=['invented'])
            with self.subTest(gate=failed_gate):
                self.assertIsNone(cert['selected_method'])
                self.assertFalse(cert['cases']['invented']['summaries']['webb_wcu_t']['passed'])


if __name__=='__main__':unittest.main()
