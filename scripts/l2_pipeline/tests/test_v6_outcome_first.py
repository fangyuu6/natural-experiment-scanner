"""V6 selection cannot use outcomes or bypass purpose/coverage gates."""
import copy
import unittest
from unittest.mock import patch
from scripts.l2_pipeline.stages.s0_intake import rank_queue_rows
from scripts.l2_pipeline.first_order import first_order_gate, target_mapping

class OutcomeFirstTests(unittest.TestCase):
    def candidate(self,name,mapped=True):
        return dict(design_id=name,policy_family=name,track='DiD',priority={'score':0 if mapped else 999},
            first_order={'target_outcome':'wages' if mapped else 'secondhand_smoke_exposure_pct',
                'primary_outcome':'wages','availability':'available','relation':'direct',
                'statute_refs':[{'source_ref':'statute','locator':'section1'}],
                'literature_refs':[{'source_ref':'paper','locator':'target'}],'secondary_outcomes':[]},
            outcome={'connector':'qcew','measure_id':'wages','value_field':'annual_avg_wkly_wage',
                     'years':[2001,2024]},treatment={'source_window':{'start_year':1990,'end_year':2009}},
            automation={'intake':{'ruling':'V6'}})
    def test_mapping_precedes_existing_priority_and_is_idempotent(self):
        rows=[self.candidate('gap',False),self.candidate('mapped')]
        prior=copy.deepcopy(rows)
        ranked=rank_queue_rows(rows)
        self.assertEqual(ranked[0]['design_id'],'mapped')
        self.assertEqual(rank_queue_rows(ranked),ranked)
        self.assertEqual(rows,prior)
        polluted=copy.deepcopy(rows)
        polluted[0].update(expected_sign='positive',effect=999,significance=0.001)
        self.assertEqual(rank_queue_rows(polluted)[0]['design_id'],'mapped')
    def test_manual_card_cannot_omit_v6_metadata_to_bypass_bounds(self):
        c=self.candidate('manual')
        c['first_order']['evidence_status']='verified_by_hub'
        c['automation']={}
        self.assertFalse(first_order_gate(c)['passed'])
        c['outcome']['years']=[2001,2009]
        self.assertTrue(first_order_gate(c)['passed'])

    def test_pending_purpose_does_not_grant_ready_and_forged_window_rejected(self):
        from scripts.l2_pipeline.coverage import coverage_bounded_card
        c=coverage_bounded_card(self.candidate('mapped'))
        self.assertTrue(target_mapping(c)['mapped'])
        self.assertEqual(c['outcome']['years'][-1],2009)
        self.assertFalse(first_order_gate(c)['passed'])
        c['first_order']['evidence_status']='verified_by_hub'
        self.assertTrue(first_order_gate(c)['passed'])
        c['outcome']['years'][-1]=2024
        self.assertFalse(first_order_gate(c)['passed'])

    def proxy_card(self, target, primary, connector, *, population_gap=True):
        c=self.candidate('proxy')
        c['first_order'].update(target_outcome=target,primary_outcome=primary,
            relation='closest_proxy',evidence_status='verified_by_hub',
            proxy_justification='The broader published population measures the same target with intent-to-treat dilution.')
        c['outcome'].update(connector=connector,measure_id=primary,years=[2014,2018],
            paths=['must-never-open.csv'])
        c['treatment']['source_window']={'start_year':1990,'end_year':2024}
        c['automation']['run3']={'known_target_population_gap':population_gap}
        from scripts.l2_pipeline.coverage import coverage_bounded_card
        return coverage_bounded_card(c)

    def test_v61_broader_crc_screening_population_preserves_same_construct(self):
        c=self.proxy_card(
            'colorectal_cancer_screening_receipt_pct_among_statutorily_covered_nonsymptomatic_enrollees',
            'colorectal_screening_pct','brfss_state_prevalence')
        original=copy.deepcopy(c)
        with patch('builtins.open',side_effect=AssertionError('outcome opened')), \
             patch('pathlib.Path.open',side_effect=AssertionError('outcome opened')):
            mapping=target_mapping(c)
            report=first_order_gate(c)
        self.assertTrue(mapping['mapped'],mapping['issues'])
        self.assertTrue(report['passed'],report['issues'])
        self.assertEqual(c,original)
        self.assertEqual(report['outcome_values_read'],0)
        self.assertEqual(report['outcome_association_calls'],0)
        self.assertFalse(mapping['purpose_approval_granted'])

    def test_v61_broader_dependent_insurance_population_uses_catalog_binding(self):
        c=self.proxy_card('dependent_health_insurance_coverage_pct',
            'health_insurance_coverage_pct','acs1_health_insurance')
        self.assertTrue(target_mapping(c)['mapped'])
        self.assertTrue(first_order_gate(c)['passed'])
        c['first_order']['evidence_status']='pending_policy_specific_verification'
        self.assertFalse(first_order_gate(c)['passed'])

    def test_v61_rejects_different_construct_even_with_dilution_rationale(self):
        for target,primary,connector in (
            ('hours','wages','qcew'),
            ('breast_density_awareness_pct','mammography_pct','brfss_state_prevalence'),
            ('health_insurance_coverage_pct','wages','qcew'),
            ('secondhand_smoke_exposure_pct','current_smoking_pct','brfss_state_prevalence'),
            ('ems_interstate_practice_count','nurse_hours','cps_nurse_hours')):
            with self.subTest(target=target,primary=primary):
                c=self.proxy_card(target,primary,connector)
                self.assertFalse(target_mapping(c)['mapped'])
                report=first_order_gate(c)
                self.assertFalse(report['passed'])
                self.assertIn('same construct',' '.join(report['issues']))

    def test_v61_population_gap_requires_explicit_one_line_dilution_proxy(self):
        for gap_owner,gap_key in (('run3','known_target_population_gap'),
                                  ('v6_reassessment','target_population_gap')):
            for relation,reason in (
                ('direct','Same measure with intent-to-treat dilution.'),
                ('closest_proxy',''),
                ('closest_proxy','Broader population is available.'),
                ('closest_proxy','Broader population.\nIntent-to-treat dilution.'),
                ('closest_proxy','Broader population.\rIntent-to-treat dilution.')):
                with self.subTest(gap_owner=gap_owner,relation=relation,reason=reason):
                    c=self.proxy_card('health_insurance_coverage_pct',
                        'health_insurance_coverage_pct','acs1_health_insurance')
                    c['automation'].pop('run3')
                    c['automation'][gap_owner]={gap_key:True}
                    c['first_order'].update(relation=relation,proxy_justification=reason)
                    self.assertFalse(target_mapping(c)['mapped'])
                    self.assertFalse(first_order_gate(c)['passed'])

    def test_v61_population_permission_cannot_bypass_actual_coverage_bounds(self):
        c=self.proxy_card('dependent_health_insurance_coverage_pct',
            'health_insurance_coverage_pct','acs1_health_insurance')
        c['outcome']['years']=[2014,2025]
        self.assertTrue(target_mapping(c)['mapped'])
        self.assertFalse(first_order_gate(c)['passed'])

    def test_v61_same_id_custom_connector_still_passes(self):
        from scripts.l2_pipeline import connectors
        c=self.candidate('fixture')
        c['automation']={}
        c['treatment']={}
        c['outcome'].update(connector='fixture_connector',measure_id='fixture_measure')
        c['first_order'].update(target_outcome='fixture_measure',primary_outcome='fixture_measure')
        with patch.dict(connectors.ADAPTERS,{'fixture_connector':lambda request: None}), \
             patch.object(connectors,'FIRST_ORDER_MEASURES',{'fixture_connector':{'fixture_measure'}}):
            self.assertTrue(first_order_gate(c)['passed'])

    def test_v62_statute_or_two_distinct_studies_evidence_routes(self):
        c=self.candidate('behavioral_target')
        c['outcome']['years']=[2001,2009]
        c['first_order'].update(evidence_status='verified_by_hub',purpose_rule='V6.2')
        c['first_order']['literature_refs']=[]
        self.assertTrue(first_order_gate(c)['passed'])
        c['first_order']['statute_refs']=[]
        c['first_order']['literature_refs']=[{'source_ref':'paper1','locator':'Primary outcome'},
                                            {'source_ref':'paper2','locator':'Same policy-type primary outcome'}]
        self.assertTrue(first_order_gate(c)['passed'])
        c['first_order']['literature_refs'][0]['title']='One Study'
        c['first_order']['literature_refs'][1]['title']='  ONE   study '
        self.assertFalse(first_order_gate(c)['passed'])
        c['first_order']['literature_refs'][1]['title']='A Second Study'
        self.assertTrue(first_order_gate(c)['passed'])
        c['first_order']['literature_refs'][1]['source_ref']='paper1#alternate-location'
        self.assertFalse(first_order_gate(c)['passed'])
        c['first_order']['literature_refs']=c['first_order']['literature_refs'][:1]
        self.assertFalse(first_order_gate(c)['passed'])

    def test_v62_literature_route_preserves_measure_and_population_proxy_checks(self):
        c=self.proxy_card('health_insurance_coverage_pct',
                          'health_insurance_coverage_pct','acs1_health_insurance')
        c['first_order'].update(statute_refs=[],purpose_rule='V6.2',
            literature_refs=[{'source_ref':'paper1','locator':'Purpose'},
                             {'source_ref':'paper2','locator':'Purpose'}])
        self.assertTrue(first_order_gate(c)['passed'])
        c['first_order']['proxy_justification']=''
        self.assertFalse(first_order_gate(c)['passed'])
        c['first_order']['proxy_justification']='Broader population has intent-to-treat dilution.'
        c['first_order']['primary_outcome']='employment_share'
        self.assertFalse(first_order_gate(c)['passed'])

    def test_v62_behavioral_series_identity_maps_without_legal_instrument_alias(self):
        from scripts.l2_pipeline.connectors.catalog import CONNECTOR_CATALOG
        measure='acs1_single_mother_median_family_income'
        name='outcome_v2_'+measure
        entry=CONNECTOR_CATALOG[name]
        c=self.candidate('tanf_behavioral_target')
        c['first_order'].update(target_outcome=measure,primary_outcome=measure)
        c['outcome'].update(connector=name,series_id=measure,measure_id=measure,
                            population_filters=entry['fixed_population_filters'])
        self.assertTrue(target_mapping(c)['mapped'])
        self.assertFalse(target_mapping(c)['purpose_approval_granted'])
        c['outcome']['population_filters']={}
        self.assertFalse(target_mapping(c)['mapped'])
        c['outcome']['population_filters']=entry['fixed_population_filters']
        c['first_order']['target_outcome']='arbitrary_different_behavior'
        self.assertFalse(target_mapping(c)['mapped'])

if __name__=='__main__': unittest.main()
