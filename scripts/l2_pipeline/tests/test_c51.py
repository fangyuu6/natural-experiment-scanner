"""Registered post support gates, using synthetic metadata and poisoned tails."""
from copy import deepcopy
import unittest
from scripts.l2_pipeline.support import support_check, standard_annual_declaration, annual_full_year_support
from scripts.l2_pipeline.engine.design import build_design, declaration_from_support


def fixture():
    years=list(range(2000,2011))
    rows=[dict(unit=f'T{i}',status='treated',start_period=g) for i,g in enumerate([2004]*3+[2006]*3+[2010]*2)]
    rows += [dict(unit=f'C{i}',status='never_treated',start_period=None) for i in range(6)]
    keys={(r['unit'],y) for r in rows for y in years}
    cfg=dict(min_pre_periods=3,min_post_periods=2,min_controls=5,min_cohorts=2,min_treated=6,controls='never_treated')
    return rows,years,keys,cfg


class C51Tests(unittest.TestCase):
    def test_single_post_cohort_dropped_in_metadata_and_numerical_pools(self):
        rows,years,keys,cfg=fixture();original=deepcopy(rows)
        s=support_check(keys,rows,cfg,period_years=years)
        self.assertTrue(s['passed'],s['issues']);self.assertEqual(s['post_support_excluded_units'],['T6','T7'])
        self.assertEqual(s['post_support_drops'][0]['retained_post_periods'],[2010])
        self.assertEqual(rows,original)
        d=standard_annual_declaration(rows,cfg,years,summary=s)
        # Non-numeric values prove no numerical values are inspected.
        design=build_design(d,[(u,t,'DO_NOT_PARSE',None) for u,t in sorted(keys)])
        self.assertEqual(design['cells'],s['cell_inventory'])
        self.assertFalse({'T6','T7'} & set(design['units']))
        self.assertFalse(any(u in {'T6','T7'} for c in design['cells'] for u in c['controls']))
        self.assertEqual(set(map(tuple,s['analysis_mask'])),design['mask'])

    def test_exclusions_censoring_and_transition_do_not_count_as_posts(self):
        rows,years,keys,cfg=fixture();rows[6]['start_period']=rows[7]['start_period']=2009
        for patch,enabled in [(dict(excluded_periods=[2010]),False),({},True)]:
            s=support_check(keys,rows,dict(cfg,**patch),period_years=years,transition_year_drop=enabled)
            self.assertTrue(s['passed'],s['issues']);self.assertEqual(s['post_support_excluded_units'],['T6','T7'])
        for r in rows[6:8]:r.update(start_period=2008,censor_after_period=2008)
        s=support_check(keys,rows,cfg,period_years=years)
        self.assertEqual(s['post_support_excluded_units'],['T6','T7'])

    def test_higher_registered_minimum_and_controls_limit_retained_posts(self):
        rows,years,keys,cfg=fixture();rows[6]['start_period']=rows[7]['start_period']=2009
        self.assertNotIn('T6',support_check(keys,rows,cfg,period_years=years)['post_support_excluded_units'])
        self.assertIn('T6',support_check(keys,rows,dict(cfg,min_post_periods=3),period_years=years)['post_support_excluded_units'])
        keys -= {(f'C{i}',2010) for i in range(2)}
        self.assertIn('T6',support_check(keys,rows,cfg,period_years=years)['post_support_excluded_units'])

    def test_remaining_gates_stop_and_invalid_threshold_rejected(self):
        rows,years,keys,cfg=fixture();cfg['min_treated']=8
        s=support_check(keys,rows,cfg,period_years=years)
        self.assertFalse(s['passed']);self.assertIn('insufficient_treated',s['issues'])
        for value in [0,-1,True,1.5,'2',None]:
            with self.assertRaises(ValueError):support_check(keys,rows,dict(cfg,min_post_periods=value),period_years=years)
        self.assertEqual(support_check(keys,rows,dict(cfg,min_post_periods=1),period_years=years)['min_post_periods'],2)

    def test_future_comparison_counts_distinct_supported_periods(self):
        rows,years,keys,cfg=fixture();rows=rows[:8]
        for r in rows[6:]:r['start_period']=2007
        keys={(r['unit'],y) for r in rows for y in years}
        s=support_check(keys,rows,dict(cfg,controls='not_yet_treated',min_controls=2),period_years=years)
        # A insufficient future pool cannot certify a single-post cohort.
        for g in {c['cohort'] for c in s['cells']}:
            self.assertGreaterEqual(len({c['period'] for c in s['cells'] if c['cohort']==g}),2)

    def test_full_year_constructor_and_replay_drop_single_post(self):
        rows,years,keys,cfg=fixture()
        d=dict(key='synthetic',window=[years[0],years[-1]],period_years=years,omitted_years=[],
            operative={r['unit']:r['start_period']-1 for r in rows if r['status']=='treated'},
            exclusions=[],never_controls=[r['unit'] for r in rows if r['status']=='never_treated'],
            exposure_end_dates={},actual_clean_onset={},anchor_availability_exclusions=[],min_post_periods=2)
        metadata=[dict(unit=u,year=str(y),annual_usable='1') for u,y in sorted(keys)]
        s,mask=annual_full_year_support(d,'primary',metadata)
        self.assertTrue(s['support_pass'],s['checks'])
        self.assertTrue(next(c for c in s['cohort_checks'] if c['cohort_year']==2010)['dropped'])
        declaration=declaration_from_support(d,s,key='synthetic')
        design=build_design(declaration,[(u,t,'POISON',None) for u,t in sorted(mask)])
        self.assertEqual(design['cells'],s['cell_inventory']);self.assertFalse({'T6','T7'}&set(design['units']))
        d['min_post_periods']=6
        s,_=annual_full_year_support(d,'primary',metadata)
        self.assertNotIn('T3',s['retained_treated'])

    def test_legacy_full_year_future_variant_enforces_post_gate_without_marker(self):
        years=list(range(2000,2014))
        d=dict(key='synthetic',window=[2000,2013],period_years=years,omitted_years=[],
            operative={f'T{i}':g for i,g in enumerate([2003]*6+[2005]*6+[2008]*6+[2010]*6)},
            exclusions=[],never_controls=[],exposure_end_dates={},actual_clean_onset={},
            anchor_availability_exclusions=[],min_post_periods=2)
        metadata=[dict(unit=u,year=str(y),annual_usable='1') for u in d['operative'] for y in years]
        s,mask=annual_full_year_support(d,'not_yet_controls',metadata)
        posts=[c for c in s['cell_inventory'] if c['post']]
        self.assertNotIn(9,{c['g'] for c in posts})
        self.assertTrue(posts)
        for g in {c['g'] for c in posts}:
            self.assertGreaterEqual(len({c['t'] for c in posts if c['g']==g}),2)
        declaration=declaration_from_support(d,s,key='synthetic',variant='not_yet_controls')
        design=build_design(declaration,[(u,t,'POISON',None) for u,t in sorted(mask)])
        self.assertEqual(design['cells'],s['cell_inventory'])

    def test_ri_relabeling_enforces_post_gate_after_censoring(self):
        from scripts.l2_pipeline.full_year import construct_full_year
        from scripts.l2_pipeline.engine.ri import relabeled_design
        years=list(range(2000,2011));a=[f'A{i}' for i in range(6)];b=[f'B{i}' for i in range(6)];controls=[f'C{i}' for i in range(6)]
        d=dict(key='synthetic',window=[2000,2010],period_years=years,omitted_years=[],
            operative={**{u:2003 for u in a},**{u:2005 for u in b}},exclusions=[],never_controls=controls,
            exposure_end_dates={u:'2007-01-01' for u in a},reversal_handling='retain_ended_exposure',
            actual_clean_onset={},anchor_availability_exclusions=[],min_post_periods=2,source_ready=True)
        metadata=[dict(unit=u,year=str(y),annual_usable='1') for u in a+b+controls for y in years]
        s,mask=construct_full_year(d,'primary',metadata,{})
        self.assertTrue(s['support_pass'],s['checks'])
        declaration=declaration_from_support(d,s,key='synthetic')
        design=build_design(declaration,[(u,t,'POISON',None) for u,t in sorted(mask)])
        bundles={u:(design['adoption'][u],None if design['adoption'][u] is None else design['operative'][u]-design['adoption'][u],design['exposure'][u],design['whole_window_clean'][u]) for u in design['units']}
        swap={**dict(zip(a,b)),**dict(zip(b,a))}
        labels=[bundles[swap.get(u,u)] for u in design['units']]
        relabeled=relabeled_design(declaration,design,labels)
        posts=[c for c in relabeled['cells'] if c['post']]
        self.assertNotIn(6,{c['g'] for c in posts})
        for g in {c['g'] for c in posts}:
            self.assertGreaterEqual(len({c['t'] for c in posts if c['g']==g}),2)

    def test_historical_acceptance_cannot_bypass_registered_post_gate(self):
        from scripts.l2_pipeline.acceptance import archival_support
        with self.assertRaisesRegex(ValueError,'cannot use historical replay'):
            archival_support(dict(min_post_periods=2),'primary',{})
