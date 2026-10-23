"""Synthetic metadata contracts; no real outcomes or estimation calls."""
from __future__ import annotations
import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.l2_pipeline.engine.design import build_design
from scripts.l2_pipeline.engine.panels import (PanelBinding, audit_binding,
    load_bound_panel, metadata_eligible_keys, sha)
from scripts.l2_pipeline.execution import DeclaredStop
from scripts.l2_pipeline.registered import numerical_declaration, registered_panel_keys
from scripts.l2_pipeline.stages.s3_support import run
from scripts.l2_pipeline.support import support_check


def fixture():
    records=[dict(unit='T'+str(i),status='treated',start_period=2004 if i<3 else 2006)
        for i in range(6)] + [dict(unit='C'+str(i),status='never_treated',start_period=None)
        for i in range(6)]
    years=list(range(2000,2009));keys={(r['unit'],y) for r in records for y in years}
    cfg=dict(min_pre_periods=3,min_controls=5,min_cohorts=2,min_treated=6)
    card=dict(design_id='invented',track='DiD',analysis=cfg,treatment={},
        outcome=dict(frequency='annual',years=[years[0],years[-1]]),sensitivities=[])
    return records,years,keys,cfg,card


def write_csv(path,fields,rows):
    with path.open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerows(rows)


class AnnualSupportContractTests(unittest.TestCase):
    def test_exact_pre_post_inventory_and_clean_anchor(self):
        records,years,keys,cfg,card=fixture()
        support=support_check(keys,records,cfg,period_years=years)
        self.assertTrue(support['passed']);self.assertEqual(support['outcome_values_parsed'],0)
        declaration=numerical_declaration(card,support,dict(records=records),'primary')
        design=build_design(declaration,[(u,t,0.,None) for u,t in sorted(keys)])
        self.assertEqual(support['cell_inventory'],design['cells'])
        self.assertTrue(any(not c['post'] for c in support['cell_inventory']))
        self.assertEqual(support['anchor_years'],[2000,2001,2002,2003])
        self.assertEqual(support['anchor_unit_n'],12)
        self.assertEqual([(c['g'],c['t'],c['base_period'],c['treated'],c['controls'])
            for c in support['cell_inventory'] if c['post']],
            [(c['cohort'],c['period'],c['base_period'],c['treated'],c['controls']) for c in support['cells']])

    def test_entire_missing_declared_year_stops(self):
        records,years,keys,cfg,_=fixture()
        keys={k for k in keys if k[1]!=2008}
        support=support_check(keys,records,cfg,period_years=years)
        self.assertFalse(support['passed'])
        self.assertEqual(support['missing_declared_calendar_periods'],[2008])
        self.assertEqual(support['issues']['missing_declared_calendar_periods'],1)

    def test_s3_flags_mask_and_numerical_inventory_agree(self):
        records,years,keys,cfg,card=fixture()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);directory=root/'panels/primary';directory.mkdir(parents=True)
            metadata=[dict(unit=u,year=y,available='1',annual_usable='1',linkage_certified='1',suppression_flag='0')
                for u,y in sorted(keys)]
            flags=['available','annual_usable','linkage_certified','suppression_flag']
            invalid={('C0',2005):('available','0'),('C0',2006):('annual_usable','0'),
                     ('C0',2007):('linkage_certified','0'),('C0',2008):('suppression_flag','1')}
            for row in metadata:
                if (row['unit'],row['year']) in invalid:
                    flag,value=invalid[row['unit'],row['year']];row[flag]=value
            write_csv(directory/'metadata.csv',['unit','year']+flags,metadata)
            # Poison tails prove metadata planning never decodes outcome fields.
            write_csv(directory/'panel.csv',['unit','year','outcome_value'],[
                dict(unit=u,year=y,outcome_value='MUST_NOT_PARSE') for u,y in sorted(keys)])
            panel=dict(variant='primary',metadata_sha256=sha(directory/'metadata.csv'),panel_sha256=sha(directory/'panel.csv'))
            documents={'TREATMENT.json':dict(records=records),'PANELS.json':dict(variants=[panel])}
            ctx=SimpleNamespace(card=SimpleNamespace(payload=card,track='DiD',design_id='invented'),
                design_dir=root,read=lambda name:documents[name])
            result=run(ctx);self.assertEqual(result.disposition.value,'ready')
            support=json.loads((root/'SUPPORT.json').read_text())['variants'][0]
            declaration=numerical_declaration(card,support,dict(records=records),'primary')
            mask=registered_panel_keys(declaration,directory/'metadata.csv')
            self.assertEqual(mask,keys-set(invalid))
            design=build_design(declaration,[(u,t,0.,None) for u,t in sorted(mask)])
            self.assertEqual(support['cell_inventory'],design['cells'])
            self.assertEqual(set(map(tuple,support['analysis_mask'])),mask)
            metadata[0]['available']='0'
            write_csv(directory/'metadata.csv',['unit','year']+flags,metadata)
            with self.assertRaisesRegex(DeclaredStop,'mask differs'):
                registered_panel_keys(declaration,directory/'metadata.csv')

    def test_metadata_flags_reject_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'metadata.csv'
            write_csv(path,['unit','year','available'],[
                dict(unit='T',year=2000,available='1'),dict(unit='T',year=2000,available='0')])
            with self.assertRaisesRegex(RuntimeError,'Duplicate'):
                metadata_eligible_keys(path)


class RegisteredMaskLoaderTests(unittest.TestCase):
    def binding(self,root,retained_value):
        panel=root/'panel.csv';metadata=root/'metadata.csv'
        write_csv(panel,['unit','year','outcome_value'],[
            dict(unit='KEEP',year=2000,outcome_value=retained_value),
            dict(unit='EXCLUDED',year=2000,outcome_value='')])
        write_csv(metadata,['unit','year','available','suppression_flag'],[
            dict(unit='KEEP',year=2000,available='1',suppression_flag='0'),
            dict(unit='EXCLUDED',year=2000,available='0',suppression_flag='1')])
        return PanelBinding(panel,panel,metadata,sha(panel),sha(panel),sha(metadata),frozenset({('KEEP',2000)}))

    def test_excluded_suppressed_blank_is_not_required(self):
        with tempfile.TemporaryDirectory() as temp:
            binding=self.binding(Path(temp),'7')
            audit=audit_binding(binding)
            self.assertEqual(audit['cells'],1);self.assertEqual(audit['construction_cells'],2)
            self.assertEqual(audit['outcome_values_parsed'],0)
            rows,identity=load_bound_panel(binding)
            self.assertEqual(rows,[('KEEP',2000,7.,None)])
            self.assertEqual(identity['outcome_values_parsed'],1)

    def test_retained_blank_stops_before_estimation(self):
        with tempfile.TemporaryDirectory() as temp:
            binding=self.binding(Path(temp),'')
            self.assertTrue(audit_binding(binding)['passed'])
            with self.assertRaisesRegex(DeclaredStop,'missing outcome'):
                load_bound_panel(binding)
            from scripts.l2_pipeline.registered import run_registered
            bundle=dict(expected=['primary'],design_id='invented',version='v1',
                spec_sha256='invented',manifest_sha256='invented',support={'primary':{'passed':True}},
                declarations={'primary':{}},bindings={'primary':binding},forecasts={})
            result=run_registered(bundle)
            self.assertEqual(result['status'],'STOPPED')
            self.assertEqual(result['actual_data_estimation_calls'],0)

    def test_analysis_mask_cannot_invent_a_cell(self):
        with tempfile.TemporaryDirectory() as temp:
            binding=self.binding(Path(temp),'7')
            from dataclasses import replace
            with self.assertRaisesRegex(RuntimeError,'exceeds construction'):
                audit_binding(replace(binding,required_keys=frozenset({('ABSENT',2000)})))


if __name__=='__main__':
    unittest.main()
