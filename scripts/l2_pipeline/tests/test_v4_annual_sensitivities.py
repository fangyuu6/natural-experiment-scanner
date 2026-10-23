"""V4 registered sensitivities alter geometry using invented metadata only."""
import copy
import unittest

from scripts.l2_pipeline.full_year import construct_full_year, effective_full_year_declaration


def fixture():
    years=list(range(2000,2025))
    source=dict(key='invented_v4',window=[2000,2024],period_years=years,
        operative={u:2015 for u in 'ABC'}|{u:2018 for u in 'DEF'},
        never_controls=list('GHIJK'),source_ready=True,
        uncertain_history_units=['A','G'],constant_definition_window=[2011,2024])
    metadata=[dict(unit=u,year=str(y),annual_usable='1',linkage_certified='1')
              for u in 'ABCDEFGHIJK' for y in years]
    return source,metadata


class V4AnnualSensitivityTests(unittest.TestCase):
    def test_drop_uncertain_histories_excludes_treated_and_controls(self):
        source,metadata=fixture();original=copy.deepcopy(source)
        effective,routed=effective_full_year_declaration(source,'drop_uncertain_history_states')
        self.assertEqual(effective['exclusions'],['A','G']);self.assertEqual(routed,'primary')
        summary,mask=construct_full_year(source,'drop_uncertain_history_states',metadata,{})
        self.assertEqual(summary['variant'],'drop_uncertain_history_states')
        self.assertFalse({'A','G'} & {u for u,_ in mask})
        self.assertNotIn('A',summary['retained_treated']);self.assertNotIn('G',summary['never_controls'])
        self.assertEqual(source,original)
    def test_constant_definition_calendar_is_shifted_and_primary_unchanged(self):
        source,metadata=fixture();original=copy.deepcopy(source)
        before,before_mask=construct_full_year(source,'primary',metadata,{})
        effective,routed=effective_full_year_declaration(source,'constant_definition_window')
        self.assertEqual(effective['window'],[2011,2024]);self.assertEqual(effective['period_years'],list(range(2011,2025)))
        self.assertEqual(routed,'primary')
        summary,mask=construct_full_year(source,'constant_definition_window',metadata,{})
        self.assertEqual(summary['window'],[2011,2024]);self.assertEqual(summary['period_index_to_year']['0'],2011)
        self.assertTrue(all(y>=2011 for _,y in mask));self.assertLess(len(mask),len(before_mask))
        self.assertEqual(next(c for c in summary['cohort_checks'] if c['cohort_year']==2016)['cohort_index'],5)
        after,after_mask=construct_full_year(source,'primary',metadata,{})
        self.assertEqual(before,after);self.assertEqual(before_mask,after_mask);self.assertEqual(source,original)
        self.assertEqual(summary['estimator_calls'],0);self.assertFalse(summary['outcome_values_read'])
    def test_all_uncertain_histories_leave_empty_unsupported_sensitivity(self):
        source,metadata=fixture();source['uncertain_history_units']=list('ABCDEFGHIJK')
        summary,mask=construct_full_year(source,'drop_uncertain_history_states',metadata,{})
        self.assertEqual(mask,set());self.assertFalse(summary['support_pass']);self.assertEqual(summary['estimator_calls'],0)
    def test_constant_definition_window_requires_valid_registered_subset(self):
        source,_=fixture()
        for window in (None,[2011,2025],[1999,2024],[2024,2011],[True,2024],[2011]):
            with self.subTest(window=window):
                source['constant_definition_window']=window
                with self.assertRaisesRegex(ValueError,'registered integer subset'):
                    effective_full_year_declaration(source,'constant_definition_window')
    def test_base_calendar_still_validated_before_effective_window(self):
        source,metadata=fixture();source['period_years'].remove(2002)
        with self.assertRaisesRegex(ValueError,'complete integer calendar axis'):
            construct_full_year(source,'constant_definition_window',metadata,{})

if __name__=='__main__': unittest.main()
