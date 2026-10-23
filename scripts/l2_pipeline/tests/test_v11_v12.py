"""Prospective comparison and first-spell rules on synthetic metadata only."""
from copy import deepcopy
import unittest
from scripts.l2_pipeline.v11_v12 import comparison_group, register_card
from scripts.l2_pipeline.support import support_check, standard_annual_declaration
from scripts.l2_pipeline.stages.s1_treatment import reconcile
from scripts.l2_pipeline.c42 import exclusion_scope
from scripts.l2_pipeline.engine.design import build_design
from scripts.l2_pipeline.engine.estimator import placebo_operator
from scripts.l2_pipeline.engine.ri import predraw_labels, relabeled_design

CFG=dict(min_treated=6,min_controls=5,min_cohorts=2,min_pre_periods=3,min_post_periods=2,
         controls='never_treated',support_gap_rule='drop_affected_state_v7')


def staggered():
    return [dict(unit=f'U{i:02}',status='treated',start_period=g,mechanism='m',locator='e')
            for i,g in enumerate([2005]*6+[2007]*6+[2010]*6)]

class V11Tests(unittest.TestCase):
    def test_mechanical_group_and_card_binding(self):
        rows=staggered()
        self.assertEqual(comparison_group(rows),'not_yet_treated')
        rows += [dict(unit=f'C{i}',status='never_treated') for i in range(5)]
        self.assertEqual(comparison_group(rows),'never_treated')
        c=dict(analysis={})
        self.assertEqual(register_card(c,'not_yet_treated')['comparison_group'],'not_yet_treated')
        with self.assertRaisesRegex(ValueError,'differs'):
            register_card(dict(c,comparison_group='never_treated'),'not_yet_treated')

    def test_staggered_cells_controls_placebo_ri_share_rule(self):
        rows=staggered(); years=list(range(2000,2013));keys={(r['unit'],t) for r in rows for t in years}
        s=support_check(keys,rows,CFG,period_years=years)
        self.assertTrue(s['passed'],s['issues'])
        self.assertEqual(s['comparison_group'],'not_yet_treated')
        self.assertEqual(len(s['retained_treated']),12)
        from scripts.l2_pipeline.c42 import post_support_gate
        gate=post_support_gate(dict(variants=[dict(s,variant='primary')]),CFG)
        self.assertEqual(gate['comparison_group'],'not_yet_treated')
        self.assertEqual(gate['retained_controls'],len(s['comparison_controls']))
        self.assertTrue(all(len(c['controls'])>=5 for c in s['cells']))
        self.assertTrue(all(t<next(r['start_period'] for r in rows if r['unit']==u)
                            for c in s['cells'] for u in c['controls'] for t in [c['period']]))
        d=standard_annual_declaration(rows,CFG,years,summary=s)
        design=build_design(d,[(u,t,0.,None) for u,t in sorted(keys)])
        self.assertEqual(design['cells'],s['cell_inventory'])
        p=placebo_operator(design)
        self.assertTrue(p['mass'])
        self.assertTrue(all(len(c['controls'])>=5 for c in p['cells']))
        self.assertTrue(any(design['adoption'][u] is not None for c in p['cells'] for u in c['controls']))
        fake=relabeled_design(d,design,predraw_labels(design,1)[0])
        self.assertEqual(fake['control_group'],'not_yet_treated')
        self.assertTrue(all(c['n_control']>=5 for c in fake['cells']))

    def test_full_year_fallback_support_and_numerical_inventory_match(self):
        from scripts.l2_pipeline.support import annual_full_year_support
        from scripts.l2_pipeline.engine.design import declaration_from_support
        rows=staggered();years=list(range(2000,2013))
        d=dict(key='synthetic',window=[2000,2012],period_years=years,omitted_years=[],
               operative={r['unit']:r['start_period'] for r in rows},exclusions=[],never_controls=[],
               exposure_end_dates={},actual_clean_onset={},anchor_availability_exclusions=[],
               comparison_group='not_yet_treated',support_gap_rule='drop_affected_state_v7')
        metadata=[dict(unit=r['unit'],year=str(y),annual_usable='1') for r in rows for y in years]
        s,mask=annual_full_year_support(d,'primary',metadata)
        self.assertTrue(s['support_pass'],s['checks'])
        declaration=declaration_from_support(d,s,key='synthetic')
        design=build_design(declaration,[(u,t,0.,None) for u,t in sorted(mask)])
        self.assertEqual(s['cell_inventory'],design['cells'])
        self.assertEqual(design['control_group'],'not_yet_treated')
        self.assertTrue(all(c['n_control']>=5 for c in design['cells']))

    def test_four_controls_drop_cells_and_censored_metadata_is_not_gap(self):
        rows=staggered()[:16];years=list(range(2000,2013));keys={(r['unit'],t) for r in rows for t in years}
        s=support_check(keys,rows,CFG,period_years=years)
        self.assertFalse(s['passed']) # 2007 cohort has only four future controls
        self.assertTrue(all(len(c['controls'])>=5 for c in s['cells']))
        rows=staggered();rows[0]['censor_after_period']=2007
        keys={(r['unit'],t) for r in rows for t in years if t<=r.get('censor_after_period',9999)}
        s=support_check(keys,rows,CFG,period_years=years)
        self.assertNotIn('U00',s['support_gap_excluded_units'])
        self.assertTrue(all(t<=2007 for u,t in s['analysis_mask'] if u=='U00'))
        d=standard_annual_declaration(rows,CFG,years,summary=s)
        design=build_design(d,[(r['unit'],t,0.,None) for r in rows for t in years])
        self.assertFalse(design['exposure']['U00'][2008]['full'])
        self.assertNotIn(('U00',2008),design['mask'])

class V12Tests(unittest.TestCase):
    def row(self,events):
        return dict(unit='X',status='treated',start_period=2001,best_evidence_start_year=2000,
                    mechanism='m',locator='e',observed_interruptions=events)

    def test_agreed_interruption_censors_first_spell_and_retains_state(self):
        a=self.row([dict(start='2008-06-01',end='2010-01-01',source_ids=['a'])])
        b=self.row([dict(start_date='2008-06-01',end_date='2010-01-01',source_ids=['b'])])
        result=reconcile([a],[b],['X'])
        self.assertFalse(result['gaps'])
        self.assertEqual(result['accepted'][0]['censor_after_period'],2007)
        cfg=exclusion_scope(dict(universe=['X']),result,[dict(records=[a]),dict(records=[b])])
        self.assertEqual(cfg['excluded_units'],[])
        self.assertEqual(cfg['certified_spell_end_periods'],{'X':2007})
        self.assertNotIn('censor_after_period',a)

    def test_one_sided_date_conflict_and_unlocated_events_stay_excluded(self):
        a=self.row([dict(date='2008',source_ids=['a'])])
        for b in (self.row([]),self.row([dict(date='2009',source_ids=['b'])]),self.row([dict(date='2008')])):
            result=reconcile([a],[b],['X'])
            self.assertFalse(result['accepted'])
            cfg=exclusion_scope(dict(universe=['X']),result,[dict(records=[a]),dict(records=[b])])
            self.assertEqual(cfg['excluded_units'],['X'])

    def test_repeal_predicate_reinstatement_symmetric(self):
        a=self.row([dict(abolition_interval=dict(start=2000,end=2008),source_ids=['a'])])
        b=self.row([dict(start_year=2008,source_ids=['b'])])
        result=reconcile([a],[b],['X'])
        self.assertEqual(result['accepted'][0]['censor_after_period'],2007)
        b['observed_interruptions'][0]['start_year']=2009
        self.assertTrue(reconcile([a],[b],['X'])['gaps'])

    def test_pre_start_history_does_not_censor_current_spell(self):
        a=self.row([dict(date='1990',end='1993',source_ids=['a'])])
        b=deepcopy(a);b['observed_interruptions'][0]['source_ids']=['b']
        self.assertIsNone(reconcile([a],[b],['X'])['accepted'][0]['censor_after_period'])
