"""V7 state-drop registration and metadata/numerical mask parity; synthetic only."""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.l2_pipeline.engine.design import build_design, declaration_from_support
from scripts.l2_pipeline.full_year import construct_full_year
from scripts.l2_pipeline.registered import numerical_declaration, registered_panel_keys
from scripts.l2_pipeline.stages.s3_support import run
from scripts.l2_pipeline.storage import sha
from scripts.l2_pipeline.support import V7_SUPPORT_GAP_RULE, support_check


def fixture():
    records = [dict(unit='T'+str(i), status='treated', start_period=2004 if i<3 else 2006)
               for i in range(8)] + [dict(unit='C'+str(i), status='never_treated', start_period=None)
                                     for i in range(6)]
    years = list(range(2000, 2009))
    keys = {(r['unit'],y) for r in records for y in years}
    cfg = dict(min_pre_periods=3, min_controls=5, min_cohorts=2, min_treated=6,
               support_gap_rule=V7_SUPPORT_GAP_RULE)
    card = dict(design_id='v7_invented', track='DiD', analysis=cfg, treatment={},
                outcome=dict(frequency='annual', years=[2000,2008]), sensitivities=[])
    return records, years, keys, cfg, card


def write_csv(path, fields, rows):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader();writer.writerows(rows)


class V7SupportTests(unittest.TestCase):
    def test_gap_drops_only_affected_state_and_preserves_history_and_numerical_geometry(self):
        records, years, keys, cfg, card = fixture()
        keys.remove(('T0',2008))
        support = support_check(keys,records,cfg,period_years=years)
        self.assertTrue(support['passed'])
        self.assertEqual(support['support_gap_excluded_units'],['T0'])
        self.assertEqual(support['support_gap_drops'][0]['missing_periods'],[2008])
        self.assertEqual(len(support['retained_treated']),7)
        declaration = numerical_declaration(card,support,dict(records=records),'primary')
        self.assertNotIn('T0',declaration['cohorts'])
        self.assertNotIn('T0',declaration['never_controls'])
        self.assertEqual(records[0]['status'],'treated')
        self.assertEqual(records[0]['start_period'],2004)
        mask = set(map(tuple,support['analysis_mask']))
        self.assertFalse(any(u=='T0' for u,t in mask))
        design = build_design(declaration,[(u,t,0.,None) for u,t in sorted(keys)])
        self.assertEqual(support['cell_inventory'],design['cells'])
        self.assertEqual(next(c['treated'] for c in design['cells'] if c['g']==2004),['T1','T2'])
        self.assertEqual(support['outcome_values_parsed'],0)

    def test_without_predeclaration_original_support_stop_is_preserved(self):
        records, years, keys, cfg, _ = fixture()
        cfg.pop('support_gap_rule');keys.remove(('T0',2008))
        support = support_check(keys,records,cfg,period_years=years)
        self.assertFalse(support['passed'])
        self.assertEqual(support['support_gap_excluded_units'],[])
        self.assertIn('T0',support['retained_treated'])

    def test_engine_minimums_cannot_be_relaxed_and_state_cannot_become_control(self):
        records, years, keys, cfg, card = fixture()
        cfg.update(min_pre_periods=1,min_controls=1,min_cohorts=1,min_treated=1)
        for u in ['T0','T1','T2']:keys.remove((u,2008))
        support = support_check(keys,records,cfg,period_years=years)
        self.assertFalse(support['passed'])
        self.assertEqual(support['effective_support_minimums'],dict(min_pre_periods=3,min_controls=5,min_cohorts=2,min_treated=6))
        self.assertEqual(support['issues']['insufficient_treated'],1)
        self.assertEqual(support['issues']['insufficient_cohorts'],1)
        declaration = numerical_declaration(card,support,dict(records=records),'primary')
        self.assertFalse(set(['T0','T1','T2']) & set(declaration['case']['sample_mask']['include_units']))

    def test_missing_control_state_is_reported_and_five_controls_required(self):
        records, years, keys, cfg, _ = fixture()
        keys.remove(('C0',2008))
        support = support_check(keys,records,cfg,period_years=years)
        self.assertTrue(support['passed'])
        self.assertEqual(len(support['never_controls']),5)
        keys.remove(('C1',2008))
        support = support_check(keys,records,cfg,period_years=years)
        self.assertFalse(support['passed'])
        self.assertEqual(support['comparison_group'],'not_yet_treated')
        self.assertEqual(support['issues']['insufficient_treated'],1)
        self.assertEqual(support['issues']['insufficient_cohorts'],1)
        self.assertTrue(all(c['n_control']>=5 for c in support['cell_inventory']))

    def test_three_pre_years_required_even_if_card_asks_for_one(self):
        records, years, keys, cfg, _ = fixture()
        cfg['min_pre_periods']=1
        years=list(range(2002,2009))
        support=support_check(keys,records,cfg,period_years=years)
        self.assertFalse(support['passed'])
        self.assertEqual(support['support_gap_excluded_units'],['T0','T1','T2'])
        self.assertEqual(support['support_gap_drops'][0]['reason'],'Insufficient registered clean pre-periods')
        self.assertEqual(support['issues']['insufficient_treated'],1)

    def test_unsupported_early_cohort_drops_without_stopping_supported_cohorts(self):
        records,years,keys,cfg,_=fixture()
        records.append(dict(unit='EARLY',status='treated',start_period=2001))
        keys|={('EARLY',t) for t in years}
        support=support_check(keys,records,cfg,period_years=years)
        self.assertTrue(support['passed'])
        self.assertEqual(support['support_gap_excluded_units'],['EARLY'])
        self.assertEqual(support['cohorts'],2)

    def test_omitted_calendar_cell_does_not_trigger_support_drop(self):
        records,years,keys,cfg,_=fixture()
        keys.remove(('T0',2008));cfg['excluded_periods']=[2008]
        support=support_check(keys,records,cfg,period_years=years)
        self.assertTrue(support['passed'])
        self.assertEqual(support['support_gap_excluded_units'],[])

    def test_invalid_or_changed_rule_fails_with_or_without_assertions(self):
        records, years, keys, cfg, card = fixture()
        cfg['support_gap_rule']='discard_if_unfavorable'
        with self.assertRaisesRegex(ValueError,'Unknown registered'):
            support_check(keys,records,cfg,period_years=years)
        cfg['support_gap_rule']=V7_SUPPORT_GAP_RULE
        support=support_check(keys,records,cfg,period_years=years)
        card['analysis']=dict(cfg,support_gap_rule=None)
        with self.assertRaisesRegex(ValueError,'differs'):
            numerical_declaration(card,support,dict(records=records),'primary')

    def test_s3_poisoned_values_remain_opaque_and_execution_mask_matches(self):
        records, years, keys, cfg, card = fixture()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);directory=root/'panels/primary';directory.mkdir(parents=True)
            write_csv(directory/'metadata.csv',['unit','year','available'],[
                dict(unit=u,year=t,available='0' if (u,t)==('T0',2008) else '1')
                for u,t in sorted(keys)])
            write_csv(directory/'panel.csv',['unit','year','outcome_value'],[
                dict(unit=u,year=t,outcome_value='DO_NOT_DECODE') for u,t in sorted(keys)])
            panel=dict(variant='primary',metadata_sha256=sha(directory/'metadata.csv'),panel_sha256=sha(directory/'panel.csv'))
            docs={'TREATMENT.json':dict(records=records),'PANELS.json':dict(variants=[panel])}
            ctx=SimpleNamespace(card=SimpleNamespace(payload=card,track='DiD',design_id='v7_invented'),
                                design_dir=root,read=lambda name:docs[name])
            result=run(ctx)
            self.assertEqual(result.disposition.value,'ready')
            self.assertEqual(result.metrics['support_gap_excluded_units'],['T0'])
            support=json.loads((root/'SUPPORT.json').read_text())['variants'][0]
            declaration=numerical_declaration(card,support,docs['TREATMENT.json'],'primary')
            self.assertEqual(registered_panel_keys(declaration,directory/'metadata.csv'),set(map(tuple,support['analysis_mask'])))

    def test_full_year_drop_preserves_exact_mask_and_numerical_geometry(self):
        years=list(range(2000,2021))
        source=dict(key='v7_full_year',period_years=years,window=[years[0],years[-1]],
                    operative={u:2006 for u in 'ABCD'}|{u:2009 for u in 'EFGH'},
                    never_controls=list('IJKLMN'),source_ready=True)
        metadata=[dict(unit=u,year=str(y),annual_usable='0' if (u,y)==('A',2012) else '1')
                  for u in 'ABCDEFGHIJKLMN' for y in years]
        legacy,_=construct_full_year(source,'primary',metadata,{})
        self.assertFalse(legacy['support_pass'])
        source['support_gap_rule']=V7_SUPPORT_GAP_RULE
        summary,mask=construct_full_year(source,'primary',metadata,{})
        self.assertTrue(summary['support_pass'])
        self.assertEqual(summary['support_gap_excluded_units'],['A'])
        self.assertFalse(any(u=='A' for u,t in mask))
        declaration=declaration_from_support(source,summary,key=source['key'])
        design=build_design(declaration,[(u,t,0.,None) for u,t in sorted(mask)])
        self.assertEqual(summary['cell_inventory'],design['cells'])
        self.assertNotIn('A',declaration['cohorts'])
        self.assertNotIn('A',declaration['never_controls'])


if __name__=='__main__':unittest.main()
