"""Selection uses legal stocks/coverage metadata and preserves stop rules under -O."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from . import run8_selection as selection


def record(unit,year,**extra):
    return dict(unit=unit,status='treated',start_period=year,primary_excluded=False,**extra)

class Run8SelectionTests(unittest.TestCase):
    def test_boundary_first_treated_year_counts_as_post_as_in_run7(self):
        periods=list(range(1999,2004))
        eligible,supported=selection.supported_records([record('AL',2002),record('AK',2003)],periods)
        self.assertEqual(len(eligible),2)
        self.assertEqual([r['unit'] for r in supported],['AL'])

    def test_declared_missing_period_not_filled_and_excluded_states_stay_excluded(self):
        values=[record('AL',2003),dict(record('AK',2003),primary_excluded=True),
                dict(record('AZ',None),status='unknown',primary_excluded=True),
                dict(record('AR',None),status='never_treated')]
        _,supported=selection.supported_records(values,[1999,2001,2003,2004])
        self.assertEqual(supported,[])
        _,supported=selection.supported_records(values,[1999,2000,2001,2003,2004])
        self.assertEqual([r['unit'] for r in supported],['AL'])

    def test_longest_legal_window_earliest_tie_not_event_selected(self):
        variable={'coverage':{'historical_complete_50_state_windows':[[2000,2003],[1980,1983],[2006,2008]]}}
        self.assertEqual(selection.legal_window(variable),[1980,1983])
        self.assertIsNone(selection.legal_window({'coverage':{'historical_complete_50_state_windows':[]}}))

    def test_blocked_prospective_series_never_grants_support(self):
        for status in ('blocked_access_denied','planned','unavailable'):
            self.assertFalse(selection.series_available({'status':status,'years':list(range(1999,2025))}))
        self.assertFalse(selection.series_available({'years':[]}))
        self.assertTrue(selection.series_available({'status':'implemented_local_adapter','years':[1999]}))

    def test_metadata_helpers_do_not_open_outcomes(self):
        original={'policy_id':'cspp:drugs_marijuana_decriminalization','kind':'binary_adoption'}
        mutated=copy.deepcopy(original);mutated.update(outcome_path='never.csv',effect=99,p_value=.0001)
        with patch('builtins.open',side_effect=AssertionError('outcome read')),patch('pathlib.Path.open',side_effect=AssertionError('outcome read')):
            self.assertEqual(selection.candidate_lead(original,{}),selection.candidate_lead(mutated,{}))
            self.assertEqual(selection.supported_records([record('AL',2002)],list(range(1999,2005)))[1][0]['unit'],'AL')

    def test_medical_cannabis_specific_first_order_design_not_overwritten(self):
        self.assertIn('different construct',selection.duplicate_reason('cspp:drugs_medical_marijuana'))
        self.assertIsNone(selection.duplicate_reason('ukcpr:eitc_state_refundable'))
        self.assertIn('Existing minimum-wage',selection.duplicate_reason('cspp:labor_minwage_abovefed'))

    def test_output_extension_only_run8_and_is_restored(self):
        from scripts.l2_pipeline.sources.research_panel import core,s1,registration
        originals=[m.owned for m in (core,s1,registration)]
        with selection.bounded_research_outputs():
            self.assertEqual(core.owned(selection.OUTPUT/'work'),selection.OUTPUT/'work')
            with self.assertRaises(ValueError):core.owned(selection.OUTPUT.parent/'unassigned')
            with self.assertRaises(ValueError):s1.owned(selection.REPO/'docs/unassigned')
        self.assertEqual([m.owned for m in (core,s1,registration)],originals)

    def test_firearm_product_clauses_share_root_family(self):
        a=selection.candidate_lead({'policy_id':'siegel:universal','kind':'binary_adoption'}, {})
        b=selection.candidate_lead({'policy_id':'siegel:universalh','kind':'binary_adoption'}, {})
        self.assertEqual(a[1],b[1])
        self.assertEqual(a[1],'universal_background_check')

    def test_emit_is_immutable_and_scope_bounded(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(selection,'OUTPUT',Path(tmp)):
            path=Path(tmp)/'report.json'
            self.assertEqual(selection.emit(path,{'n':1}),path.resolve())
            self.assertEqual(selection.emit(path,{'n':1}),path.resolve())
            with self.assertRaises(ValueError):selection.emit(path,{'n':2})
            with self.assertRaises(ValueError):selection.emit(Path(tmp).parent/'outside.json',{})


    def test_age_years_exact_metadata_and_unexported_age_never_supported(self):
        spec={'series_id':'wonder_births_by_maternal_age','years':[1999,2000,2003],
              'age_group_years':{'15-19':[1999,2000,2003],'15-17':[2003]}}
        self.assertEqual(selection.declared_periods(spec,{'age_group':'15-17'}),[2003])
        self.assertEqual(selection.declared_periods(spec,{'age_group':'unknown'}),[])

    def test_tracker_complete_window_never_fills_missing_days(self):
        rows=[dict(unit=u,year=y,missing_days=0,unknown_days=0,conflict_days=0)
              for u in ('AL','AK') for y in range(2000,2004)]
        rows[-1]['missing_days']=364
        policy=dict(states=['AL','AK'],coverage={'start_year':2000,'end_year':2003},annual_rows=rows,history_complete=False)
        self.assertEqual(selection.longest_tracker_window(policy),[2000,2002])
        rows[1]['unknown_days']=1
        self.assertEqual(selection.longest_tracker_window(policy),[2000,2000])

    def test_local_outcome_sources_are_absolute_and_receipt_hash_bound(self):
        spec=dict(local_source_paths=['data/l2_pipeline/connectors_v2/wonder/raw/test.tsv'],
          exports=[dict(raw_path='data/l2_pipeline/connectors_v2/wonder/raw/test.tsv',sha256='a'*64,url='https://wonder.cdc.gov/')],license='public domain')
        result=selection.outcome_sources(spec)
        self.assertTrue(Path(result['paths'][0]).is_absolute())
        self.assertEqual(result['source_sha256'][result['paths'][0]],'a'*64)

if __name__=='__main__':unittest.main()
