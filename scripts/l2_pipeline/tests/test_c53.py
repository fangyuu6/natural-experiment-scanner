"""C53 implementation parity on invented metadata; no real outcome fits."""
from copy import deepcopy
from pathlib import Path
import tempfile,unittest
from unittest.mock import patch
from scripts.l2_pipeline.tests.test_c51 import fixture
from scripts.l2_pipeline.tests.test_v11_v12 import staggered,CFG
from scripts.l2_pipeline.stages.s3_support import variant_treatment
from scripts.l2_pipeline.support import support_check,standard_annual_declaration
from scripts.l2_pipeline.engine.design import build_design,structural_rows,treatment_rows
from scripts.l2_pipeline.engine.calibration import calibration_operators
from scripts.l2_pipeline.storage import ensure_once,sha

class C53Tests(unittest.TestCase):
    def test_late_label_retains_actual_boundary_transition_and_clean_baselines(self):
        for enabled in (False,True):
            rows,years,keys,cfg=fixture();before=deepcopy(rows)
            card=dict(treatment=dict(start_shift=1));shifted=variant_treatment(rows,card)
            s=support_check(keys,shifted,cfg,period_years=years,transition_year_drop=enabled)
            self.assertTrue(s['passed'],s)
            d=standard_annual_declaration(shifted,cfg,years,summary=s,transition_year_drop=enabled)
            design=build_design(d,[(u,t,'POISON',None) for u,t in sorted(keys)])
            self.assertEqual(design['cells'],s['cell_inventory'])
            actual={r['unit']:r['start_period'] for r in rows if r['status']=='treated'}
            for u in design['units']:
                if u in actual:
                    self.assertFalse(design['exposure'][u][actual[u]]['clean'])
                    if enabled:self.assertNotIn((u,actual[u]),design['mask'])
            for c in design['cells']:
                for u in c['treated']:
                    self.assertLess(c['base_period'],actual[u])
                    if not c['post']:self.assertLess(c['t'],actual[u])
            self.assertEqual(rows,before)

    def test_future_controls_never_use_actual_operation_year_after_shift(self):
        rows=staggered();
        for r in rows[12:]:r['start_period']=2012
        years=list(range(2000,2016));keys={(r['unit'],y) for r in rows for y in years}
        shifted=variant_treatment(rows,dict(treatment=dict(start_shift=1)))
        for enabled in (False,True):
            s=support_check(keys,shifted,CFG,period_years=years,transition_year_drop=enabled)
            self.assertTrue(s['passed'],s['issues'])
            design=build_design(standard_annual_declaration(shifted,CFG,years,summary=s,transition_year_drop=enabled),[(u,t,'POISON',None) for u,t in keys])
            self.assertEqual(design['cells'],s['cell_inventory'])
            actual={r['unit']:r['start_period'] for r in rows}
            self.assertTrue(any(c['controls'] for c in design['cells']))
            for c in design['cells']:
                for u in c['controls']:
                    self.assertLess(max(c['t'],c['base_period']),actual[u])

    def test_shift_is_applied_once_and_preserves_censoring(self):
        rows,_,_,_=fixture();rows[0]['censor_after_period']=2007
        c=dict(treatment=dict(start_shift=1));a=variant_treatment(rows,c)
        self.assertEqual(a[0]['operative_period'],2004)
        self.assertEqual(a[0]['start_period'],2005)
        self.assertEqual(a[0]['censor_after_period'],2007)
        self.assertEqual(variant_treatment(rows,dict(treatment={})),rows)
        with self.assertRaises(ValueError):variant_treatment(rows,dict(treatment=dict(start_shift=True)))

    def test_calibration_rebuilds_registered_future_design_exactly(self):
        rows=staggered();years=list(range(2000,2013));keys={(r['unit'],y) for r in rows for y in years}
        for enabled in (False,True):
            s=support_check(keys,rows,CFG,period_years=years,transition_year_drop=enabled)
            d=standard_annual_declaration(rows,CFG,years,summary=s,transition_year_drop=enabled)
            source=[(u,t,'POISON',None) for u,t in sorted(keys)]
            op=calibration_operators(d,structural_rows(d,source),treatment_rows(d),permutations=0)
            design=build_design(d,source)
            self.assertEqual(op['design'],design)
            self.assertEqual(op['design']['control_group'],'not_yet_treated')
            self.assertTrue(op['family']);self.assertTrue(op['placebo_support']['mass'])

    def test_calibration_full_year_normalized_axis_is_not_normalized_twice(self):
        from scripts.l2_pipeline.support import annual_full_year_support
        from scripts.l2_pipeline.engine.design import declaration_from_support
        rows=staggered();years=list(range(2000,2013))
        d=dict(key='invented',window=[2000,2012],period_years=years,omitted_years=[],operative={r['unit']:r['start_period'] for r in rows},exclusions=[],never_controls=[],exposure_end_dates={},actual_clean_onset={},anchor_availability_exclusions=[],comparison_group='not_yet_treated',min_post_periods=2)
        s,mask=annual_full_year_support(d,'primary',[dict(unit=r['unit'],year=str(y),annual_usable='1') for r in rows for y in years])
        dec=declaration_from_support(d,s,key='invented');source=[(u,t,'POISON',None) for u,t in sorted(mask)]
        op=calibration_operators(dec,structural_rows(dec,source),treatment_rows(dec),permutations=0)
        self.assertEqual(op['design'],build_design(dec,source))

    def test_secondary_future_constructor_uses_own_metadata(self):
        from scripts.l2_pipeline.tests.test_pipeline import card
        from scripts.l2_pipeline.contracts import DesignCard,StageContext
        from scripts.l2_pipeline.stages.s3_support import run
        c=card();rows=staggered();years=list(range(2000,2013));c['sensitivities']=[]
        c['treatment'].update(universe=[r['unit'] for r in rows],records=rows)
        c['analysis'].update(CFG,controls='not_yet_treated');c['outcome'].update(years=[2000,2012],frequency='annual')
        c['first_order']['secondary_outcomes']=[dict(outcome='invented_companion',role='secondary',connector='manual_csv',population_filters={})]
        name='secondary_invented_companion'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=ensure_once(root/'CARD.json',c);ctx=StageContext(DesignCard.from_mapping(c,path),root/'work',root)
            ensure_once(ctx.design_dir/'TREATMENT.json',dict(records=rows));panels=[]
            for n in ['primary',name]:
                folder=ctx.design_dir/'panels'/n;folder.mkdir(parents=True)
                # A secondary with only four future controls must fail its own
                # support, while the fully covered primary remains ready.
                selected=rows if n=='primary' else rows[:16]
                (folder/'panel.csv').write_text('unit,year,outcome_value\n'+''.join(f"{r['unit']},{y},POISON\n" for r in selected for y in years))
                (folder/'metadata.csv').write_text('unit,year\n'+''.join(f"{r['unit']},{y}\n" for r in selected for y in years))
                panels.append(dict(variant=n,panel_sha256=sha(folder/'panel.csv'),metadata_sha256=sha(folder/'metadata.csv')))
            ensure_once(ctx.design_dir/'PANELS.json',dict(variants=panels))
            self.assertEqual(run(ctx).disposition.value,'ready')
            secondary=ctx.read('SUPPORT.json')['variants'][1]
            self.assertFalse(secondary['support_pass']);self.assertEqual(secondary['comparison_group'],'not_yet_treated')
            self.assertIn('cell_inventory',secondary)
            self.assertNotIn('No certified secondary',secondary['reason'])

    def test_coverage_inherited_sensitivity_window_passes_but_explicit_mismatch_fails(self):
        from scripts.l2_pipeline.autopilot import read
        from scripts.l2_pipeline.first_order import first_order_gate
        from scripts.l2_pipeline.c48_audit import effective_context
        from scripts.l2_pipeline.contracts import DesignCard,StageContext
        from scripts.l2_pipeline.autopilot import Autopilot
        # Read retained registration metadata only; no source outcomes opened.
        baseline=Path(__file__).resolve().parents[3]/'data/l2_pipeline/autopilot/c52/ACTIVE_STATUS.json'
        # The fixture CARD is a retained, unfrozen registration.
        root=Path(__file__).resolve().parents[3]
        pointer=root/'data/l2_pipeline/autopilot/c53/ACTIVE_BASELINE.json'
        if not pointer.exists():self.skipTest('C53 retained registration fixture unavailable')
        p=Autopilot();row=next(r for r in read(pointer)['designs'] if r['design_id']=='b1010b_child_restraint_booster_age5')
        c=deepcopy(dict(p.backend.ctx(row).card.payload))
        self.assertTrue(first_order_gate(c)['passed'])
        inherited=next(s for s in c['sensitivities'] if s['name']=='drop_unresolved_audit_dates')
        self.assertNotIn('outcome',inherited['changes'])
        inherited['changes']['outcome']={'years':[1900,2024]}
        self.assertIn('Sensitivity windows differ from recomputed V6 coverage bounds',first_order_gate(c)['issues'])

    def test_scan_never_migrates_frozen_or_finished_design(self):
        from scripts.l2_pipeline.c53 import migrate
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ensure_once(root/'FREEZE_PREP.json',{})
            p=SimpleNamespace(backend=SimpleNamespace(root=lambda row:root))
            with patch('scripts.l2_pipeline.c53.OUT',root/'out'):
                with self.assertRaisesRegex(ValueError,'cannot revise frozen'):migrate(p,dict(prior_row=dict(design_id='invented')))
