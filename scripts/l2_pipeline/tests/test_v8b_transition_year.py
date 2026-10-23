"""V8b metadata geometry and immutable defaults; all new fixtures synthetic."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.l2_pipeline.cards import validate_card, variant_card
from scripts.l2_pipeline.engine.design import build_design
from scripts.l2_pipeline.engine.panels import PanelBinding, load_bound_panel, sha
from scripts.l2_pipeline.freeze import ROLES, build_manifest, verify
from scripts.l2_pipeline.full_year import construct_full_year, declaration_from_card_support
from scripts.l2_pipeline.registered import numerical_declaration, registered_panel_keys
from scripts.l2_pipeline.storage import write_once
from scripts.l2_pipeline.support import support_check
from scripts.l2_pipeline.tests.test_pipeline import card
from scripts.l2_pipeline.tests.test_v7_support import fixture, write_csv


class TransitionYearTests(unittest.TestCase):
    def test_future_adopter_operation_year_dropped_from_ri_mask_without_treated_reclassification(self):
        from scripts.l2_pipeline.intake.official_history import engine_records
        treatment, years, keys, cfg, design_card = self.fixture()
        future=dict(unit='MI',status='not_yet_treated',start_period=2009,best_evidence_start_year=2008)
        future=engine_records([future],set())[0]
        treatment.append(future);keys.update(('MI',y) for y in years)
        summary=support_check(keys,treatment,cfg,period_years=years,transition_year_drop=True)
        self.assertTrue(summary['passed'],summary['issues'])
        self.assertIn(['MI',2008],summary['transition_years_dropped'])
        self.assertNotIn(['MI',2008],summary['analysis_mask'])
        declaration=numerical_declaration(design_card,summary,dict(records=treatment),'primary')
        self.assertNotIn('MI',declaration['cohorts'])
        self.assertNotIn('MI',declaration['never_controls'])
        self.assertEqual(declaration['operative_years']['MI'],2008)
        design=build_design(declaration,[(u,t,0.,None) for u,t in sorted(keys)])
        self.assertEqual(set(design['mask']),set(map(tuple,summary['analysis_mask'])))
        self.assertNotIn(('MI',2008),design['mask'])
        archival=support_check(keys,treatment,cfg,period_years=years,transition_year_drop=False)
        self.assertIn(['MI',2008],archival['analysis_mask'])

    def fixture(self):
        treatment, years, keys, cfg, design_card = fixture()
        design_card['transition_year_drop'] = True
        return treatment, years, keys, cfg, design_card

    def test_schema_boolean_default_and_variant_registration(self):
        base = card()
        self.assertEqual(validate_card(base), [])
        for invalid in (1, 'true', None):
            self.assertTrue(validate_card(dict(base, transition_year_drop=invalid)))
        changed = variant_card(base, dict(transition_year_drop=True))
        self.assertTrue(changed['transition_year_drop'])
        self.assertNotIn('transition_year_drop', base)
        schema = json.loads(Path(__file__).parents[1].joinpath('design_card.schema.json').read_text())
        self.assertIs(schema['properties']['transition_year_drop']['default'], False)

    def test_drop_each_effective_year_shift_once_keep_never_years_and_match_engine(self):
        treatment, years, keys, cfg, design_card = self.fixture()
        original = copy.deepcopy(treatment)
        summary = support_check(keys, treatment, cfg, period_years=years, transition_year_drop=True)
        self.assertTrue(summary['passed'], summary['issues'])
        self.assertEqual(summary['support_gap_excluded_units'], [])
        mask = set(map(tuple, summary['analysis_mask']))
        for row in treatment:
            effective = row['start_period']
            self.assertEqual({t for u,t in mask if u==row['unit']},
                             set(years)-({effective} if row['status']=='treated' else set()))
        declaration = numerical_declaration(design_card, summary, dict(records=treatment), 'primary')
        self.assertEqual(declaration['cohorts']['T0'], 2005)
        self.assertEqual(declaration['operative_years']['T0'], 2004)
        design = build_design(declaration, [(u,t,0.,None) for u,t in sorted(keys)])
        self.assertEqual(design['mask'], mask)
        self.assertEqual(summary['cell_inventory'], design['cells'])
        posts = [c for c in summary['cells'] if c['cohort']==2005]
        self.assertEqual([c['period'] for c in posts], [2005,2006,2007,2008])
        self.assertTrue(all(c['base_period']==2003 for c in posts))
        self.assertEqual(treatment, original)
        self.assertEqual(summary['outcome_values_parsed'], 0)

    def test_deliberate_drop_is_not_missing_support_but_retained_missing_cell_is(self):
        treatment, years, keys, cfg, _ = self.fixture()
        keys.remove(('T0',2004))
        good = support_check(keys,treatment,cfg,period_years=years,transition_year_drop=True)
        self.assertTrue(good['passed'])
        self.assertEqual(good['support_gap_excluded_units'], [])
        keys.remove(('T0',2005))
        gap = support_check(keys,treatment,cfg,period_years=years,transition_year_drop=True)
        self.assertEqual(gap['support_gap_excluded_units'], ['T0'])
        self.assertEqual(gap['support_gap_drops'][0]['missing_periods'], [2005])

    def test_clean_pre_and_final_transition_year_do_not_count_as_support(self):
        treatment, _, keys, cfg, _ = self.fixture()
        summary = support_check(keys,treatment,cfg,period_years=list(range(2002,2009)),transition_year_drop=True)
        self.assertEqual(summary['support_gap_excluded_units'], ['T0','T1','T2'])
        self.assertEqual(summary['support_gap_drops'][0]['clean_pre_periods'], [2002,2003])
        treatment.append(dict(unit='LAST',status='treated',start_period=2008))
        keys.add(('LAST',2008)); keys|={('LAST',y) for y in range(2000,2008)}
        summary = support_check(keys,treatment,cfg,period_years=list(range(2000,2009)),transition_year_drop=True)
        last = next(r for r in summary['support_gap_drops'] if r['unit']=='LAST')
        self.assertEqual(last['reason'], 'No registered post-period support')

    def test_support_and_card_flag_mismatch_refused(self):
        treatment, years, keys, cfg, design_card = self.fixture()
        summary = support_check(keys,treatment,cfg,period_years=years,transition_year_drop=True)
        design_card.pop('transition_year_drop')
        with self.assertRaisesRegex(ValueError, 'differs from metadata'):
            numerical_declaration(design_card,summary,dict(records=treatment),'primary')

    def test_effective_year_outcome_tails_never_decoded_and_original_panel_unchanged(self):
        treatment, years, keys, cfg, design_card = self.fixture()
        summary = support_check(keys,treatment,cfg,period_years=years,transition_year_drop=True)
        declaration = numerical_declaration(design_card,summary,dict(records=treatment),'primary')
        drops = set(map(tuple,summary['transition_years_dropped']))
        with tempfile.TemporaryDirectory() as temp:
            panel, metadata = (Path(temp)/name for name in ('panel.csv','metadata.csv'))
            write_csv(panel,['unit','year','outcome_value'],[
                dict(unit=u,year=t,outcome_value='DO_NOT_DECODE' if (u,t) in drops else '0') for u,t in sorted(keys)])
            write_csv(metadata,['unit','year','available'],[
                dict(unit=u,year=t,available='1') for u,t in sorted(keys)])
            before = panel.read_bytes()
            required = registered_panel_keys(declaration,metadata)
            binding = PanelBinding(panel,panel,metadata,sha(panel),sha(panel),sha(metadata),frozenset(required))
            rows, identity = load_bound_panel(binding)
            self.assertEqual({(u,t) for u,t,_,_ in rows},set(map(tuple,summary['analysis_mask'])))
            self.assertEqual(identity['outcome_values_parsed'],len(keys)-len(drops))
            self.assertEqual(panel.read_bytes(),before)

    def test_full_year_adapter_already_has_v8b_clock_and_does_not_shift_twice(self):
        years = list(range(2000,2021))
        source = dict(key='v8b_full_year', period_years=years, window=[2000,2020],
            operative={u:2006 for u in 'ABCD'}|{u:2009 for u in 'EFGH'},
            never_controls=list('IJKLMN'),source_ready=True)
        metadata=[dict(unit=u,year=str(y),annual_usable='1') for u in 'ABCDEFGHIJKLMN' for y in years]
        original, mask = construct_full_year(source,'primary',metadata,{})
        marked, marked_mask = construct_full_year(dict(source,transition_year_drop=True),'primary',metadata,{})
        self.assertEqual(mask,marked_mask)
        self.assertEqual(original['cell_inventory'],marked['cell_inventory'])
        full_card = dict(design_id=source['key'],transition_year_drop=True,
            analysis=dict(support_adapter='annual_full_year_v1',full_year_variant='primary'),
            treatment=dict(full_year_declaration=source))
        declaration = declaration_from_card_support(full_card,marked)
        self.assertEqual(years[declaration['cohorts']['A']],2007)
        design = build_design(declaration,[(u,y,0.,None) for u in 'ABCDEFGHIJKLMN' for y in years])
        self.assertEqual({(u,years[t]) for u,t in design['mask']},marked_mask)
        self.assertEqual(design['cells'],marked['cell_inventory'])

    def test_freeze_inventory_records_boolean_and_rejects_changed_value(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);files={}
            for role in ROLES:
                name=role+'.json'
                write_once(root/name,dict(card(),transition_year_drop=True) if role=='card' else dict(fixture=role))
                files[name]=role
            write_once(root/'predictions/x/submission.json',dict(persona='x',design_id='invented',spec_sha256=sha(root/'spec.json')))
            files['predictions/x/submission.json']='predictions'
            write_once(root/'PREDICTIONS_LOCK.json',dict(personas=['x'],design_id='invented',
                spec_sha256=sha(root/'spec.json'),files={'predictions/x/submission.json':sha(root/'predictions/x/submission.json')}))
            files['PREDICTIONS_LOCK.json']='predictions'
            path=build_manifest(root,'prep',files,['x'],'hub')
            manifest=json.loads(path.read_text())
            self.assertIs(manifest['transition_year_drop'],True)
            self.assertTrue(verify(root,'prep','hub',require_approval=False)['ok'])
            manifest['transition_year_drop']=False;path.write_text(json.dumps(manifest))
            report=verify(root,'prep','hub',require_approval=False)
            self.assertIn('Freeze transition-year drop differs from card',report['issues'])


if __name__=='__main__': unittest.main()
