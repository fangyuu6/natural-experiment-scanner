"""C62 value-free screens and 32-cell invented integration matrix.

Stage functions are real. Test-only authority and compact numerical certificates
are injected at external approval/production-count boundaries. No real design
can use these certificates or acquire execution permission.
"""
import copy,csv,itertools,json,tempfile,unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.tests.test_pipeline import card,REPO


class ScreenTests(unittest.TestCase):
    def test_path_binding_only_from_exact_pinned_catalog(self):
        from scripts.l2_pipeline.prescreen import bind_outcome
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'source').write_text('opaque')
            spec=dict(paths=['source'],source_sha256={'source':sha(root/'source')})
            catalog=ensure_once(root/'catalog.json',dict(series={'s':spec}))
            out=dict(series_id='s',catalog_path='catalog.json',catalog_sha256=sha(catalog))
            self.assertEqual(bind_outcome(out,root)['paths'],[str((root/'source').resolve())])
            self.assertEqual(bind_outcome(dict(out,paths=['unrelated']),root)['paths'],['unrelated'])
            with self.assertRaisesRegex(ValueError,'binding differs'):bind_outcome(dict(out,paths=['source'],source_sha256={}),root)

    def test_scope_requeue_requires_reason_intersection(self):
        from scripts.l2_pipeline.maintenance import require_match
        self.assertEqual(require_match('Invalid coding status',['history_status']),['history_status'])
        with self.assertRaises(ValueError):require_match('S3 Registered support insufficient',['history_status'])
        with self.assertRaises(ValueError):require_match('Invalid coding status',[])

    def test_pre_review_load_precedes_draft_and_prescreen_not_processed(self):
        from scripts.l2_pipeline.autopilot import CHAIN,Autopilot
        self.assertLess(CHAIN.index('draft-dry-load'),CHAIN.index('draft'))
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);c=card();c['outcome'].update(years=[2000,2005],paths=[])
            from scripts.l2_pipeline.prescreen import evaluate
            value=evaluate(c,root)
            self.assertEqual(value['status'],'PRESCREEN_PARK');self.assertFalse(value['processed'])

    def test_history_packet_ci_blocks_nested_adoption_and_keeps_window(self):
        from scripts.l2_pipeline.agents.contract_tests.packets import emit_fixture_job
        from scripts.l2_pipeline.agents.history_packet import check,answer_bearing
        with tempfile.TemporaryDirectory() as d:
            job=emit_fixture_job(Path(d),'s1_official_history_v9');check(job)
            self.assertFalse(answer_bearing(job.contract))
            for key in ('adopters','screening_adoption','adoption_screen','adoption_years'):
                self.assertTrue(answer_bearing({'card':{'nested':{key:{'CA':2000}}}}))
            self.assertIn('--self-check',Path(job.job_path).read_text())

    def test_collector_self_check_same_validation_no_promotions(self):
        from scripts.l2_pipeline.agents.contract_tests.packets import emit_fixture_job
        from scripts.l2_pipeline.agents.contract_tests import golden_output
        from scripts.l2_pipeline.agents.collect import collect_job
        for kind in ('s0_purpose','s1_coder','s1_auditor','s1_followup_auditor','s1_third_read','s1_official_history_v9','s5_forecast','s6_draft_reviewer','s0l_literature'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as d:
                root=Path(d);job=emit_fixture_job(root,kind);ensure_once(Path(job.output_path),golden_output(job))
                self.assertTrue(job.contract.get('collector_self_check_required'))
                self.assertIn('--self-check --job',Path(job.job_path).read_text())
                before={str(p):sha(p) for p in root.rglob('*') if p.is_file()}
                self.assertTrue(collect_job(job.job_path,self_check=True)['validated'])
                self.assertEqual(before,{str(p):sha(p) for p in root.rglob('*') if p.is_file()})
                value=json.loads(Path(job.output_path).read_text());value['design_id']='wrong';Path(job.output_path).write_text(json.dumps(value))
                with self.assertRaises(ValueError):collect_job(job.job_path,self_check=True)


def matrix_fixture(root,group,shift,secondaries,scale,frequency):
    from scripts.l2_pipeline.connectors.common import STATES
    from scripts.l2_pipeline.contracts import StageContext,DesignCard
    from scripts.l2_pipeline.stages import s3_support,s4_specs
    from scripts.l2_pipeline.variants import registered_cards
    c=card();units=list(STATES.values())[:16];step=2 if frequency=='biennial' else 1
    years=list(range(2000,2000+12*step,step))
    c['outcome'].update(frequency=frequency,years=[years[0],years[-1]],period_years=years,units=units,outcome_scale=scale)
    c['analysis'].update(controls=group,comparison_group=group,min_pre_periods=3,min_post_periods=2,min_controls=5,min_treated=6,min_cohorts=2,
        estimand='invented ATT',clock='calendar_year',weights='equal',inference='Webb',diagnostics=['pretrend_joint','pretrend_economic','placebo'],stop_rules=['support'])
    c['automation']=dict(hub_signer='synthetic_only')
    c['treatment']=dict(universe=units)
    c['sensitivities']=[dict(name='shift',changes=dict(treatment=dict(start_shift=shift)))]
    if secondaries:c['first_order']['secondary_outcomes']=[dict(outcome='invented_secondary',role='secondary',**copy.deepcopy(c['outcome']))]
    path=ensure_once(root/'card.json',c);ctx=StageContext(DesignCard.from_mapping(c,path),root/'work',REPO)
    tr=[dict(unit=u,status='treated' if i<8 else 'never_treated',start_period=years[5 if i<4 else 7] if i<8 else None) for i,u in enumerate(units)]
    ensure_once(ctx.design_dir/'TREATMENT.json',dict(records=tr))
    entries=[]
    for name,vc in registered_cards(c).items():
        directory=ctx.design_dir/'panels'/name;directory.mkdir(parents=True)
        with (directory/'panel.csv').open('w') as f:
            f.write('unit,year,outcome_value\n')
            for i,u in enumerate(units):
                for j,y in enumerate(years):f.write(f'{u},{y},{(i*j%7)+i*.1+j*.2+10}\n')
        with (directory/'metadata.csv').open('w') as f:
            f.write('unit,year,available,annual_usable,linkage_certified\n')
            for u in units:
                for y in years:f.write(f'{u},{y},1,1,1\n')
        entries.append(dict(variant=name,panel_sha256=sha(directory/'panel.csv'),metadata_sha256=sha(directory/'metadata.csv'),connector=vc['outcome']['connector'],population_filters={}))
    ensure_once(ctx.design_dir/'PANELS.json',dict(variants=entries))
    result=s3_support.run(ctx)
    if result.disposition.value!='ready':raise AssertionError(result)
    s4_specs.run(ctx)
    return ctx


class MatrixTests(unittest.TestCase):
    def test_prep_calibration_seal_dry_load_32_cells(self):
        from scripts.l2_pipeline import registration as reg,dry_load
        from scripts.l2_pipeline.engine import calibration
        from scripts.l2_pipeline.freeze import build_manifest
        from scripts.l2_pipeline.registered import load_registration
        from scripts.l2_pipeline.agents.jobs import PERSONAS
        from scripts.l2_pipeline.predictions import lock_predictions
        from scripts.l2_pipeline.contract_card import registered_execution_card
        cells=list(itertools.product(('never_treated','not_yet_treated'),(-1,1),(False,True),('rate','count'),('annual','biennial')))
        for group,shift,secondaries,scale,freq in cells:
            with self.subTest(group=group,shift=shift,secondaries=secondaries,scale=scale,freq=freq),tempfile.TemporaryDirectory() as d,ExitStack() as stack:
                ctx=matrix_fixture(Path(d),group,shift,secondaries,scale,freq);root=ctx.design_dir
                self.assertTrue(dry_load.draft(ctx)['passed'])
                for persona in PERSONAS:
                    ensure_once(root/'predictions'/persona/'submission.json',dict(persona=persona,design_id='invented',spec_sha256=sha(root/'SPEC.json'),point=0,interval=[-1,1],interval_level=.8,rationale='invented',disclosures=[]))
                for persona in PERSONAS:
                    original=root/'predictions'/persona/'submission.json'
                    ensure_once(original.parent/'COLLECTION.json',dict(validated=True,stage='S5',role=persona,design_id='invented',original_path=str(original.resolve()),original_sha256=sha(original),promoted_sha256=sha(original)))
                lock_predictions(root,list(PERSONAS),'invented',sha(root/'SPEC.json'))
                # Compact certificates exercise stage orchestration and real
                # loading. Their production checks are replaced ONLY here.
                report=ensure_once(Path(d)/'TESTS.json',dict(fixture=True))
                tests=dict(runs=[dict(transcript='test_invented_parallel_parity_and_independent_reference (fixture) ... ok')]*2)
                stack.enter_context(patch.object(reg,'_check_evidence',return_value=tests))
                stack.enter_context(patch.object(reg,'_review',return_value=dict(verdict='PASS',fixture=True)))
                stack.enter_context(patch('scripts.l2_pipeline.snapshots.active',return_value={'fixture':True,'code_inventory':{}}))
                stack.enter_context(patch('scripts.l2_pipeline.snapshots.manifest_for',return_value=None))
                stack.enter_context(patch.object(reg,'require_verified',return_value=dict(ok=True)))
                stack.enter_context(patch.object(reg,'verify',return_value=dict(ok=True,manifest_sha256='0'*64)))
                def manifest(root,stage,files,personas,signer):
                    value=dict(files={n:dict(role=r,sha256=sha(root/n)) for n,r in files.items()},code_snapshot=dict(digest='0'*64))
                    if stage=='final':value['dry_load_inventory']=dry_load.inventory(root,files)
                    return ensure_once(root/('FREEZE_'+stage.upper()+'.json'),value)
                stack.enter_context(patch.object(reg,'build_manifest',side_effect=manifest))
                reg.prep(ctx,SimpleNamespace(signer='synthetic_only',test_report=report,review_attempt='fixture'),['invented'])
                # Real anchor and structural/RI operators run; heavy simulation
                # calls use two invented worlds, retaining the actual outputs.
                original_diag=calibration.calibrate_synthetic;original_interval=calibration.evaluate_intervals
                stack.enter_context(patch.object(calibration,'calibrate_synthetic',side_effect=lambda key,op,sigma:original_diag(key,op,sigma,counts=dict(training=20,power=2,heldout=2,B=19,RI=199),include_power=False)))
                stack.enter_context(patch.object(calibration,'evaluate_intervals',side_effect=lambda key,op,sigma,**kw:original_interval(key,op,sigma,repetitions=2,B=19,heldout=2)))
                stack.enter_context(patch('scripts.l2_pipeline.registered.validate_calibration'))
                stack.enter_context(patch('scripts.l2_pipeline.engine.inference.validate_certification'))
                reg.calibration(ctx)
                # Real certification aggregator runs; production count validation
                # remains mocked and cannot escape this temporary fixture.
                stack.enter_context(patch('scripts.l2_pipeline.cli.run_stage',return_value=SimpleNamespace(disposition=SimpleNamespace(value='ready'))))
                ensure_once(root/'APPROVAL_PREP.json',dict(fixture=True,execution_authorized=False))
                reg.seal([ctx])
                value=dry_load.inputs(root)
                self.assertTrue(value['passed']);self.assertEqual(value['actual_data_estimation_calls'],0)
                expected=[r['variant'] for r in json.loads((root/'SUPPORT_C79.json').read_text())['variants'] if r.get('support_pass',r.get('passed')) is True]
                self.assertEqual(sorted(r['variant'] for r in value['loaded']),sorted(expected))
                self.assertIn('primary',expected)
                self.assertFalse((root/'EXECUTION_ONCE.json').exists())

if __name__=='__main__':unittest.main()

class IntakeBoundaryTests(unittest.TestCase):
    def test_new_prescreen_park_dispatches_nothing_and_inflight_is_exempt(self):
        from scripts.l2_pipeline.tests.test_autopilot import MockAgents
        from scripts.l2_pipeline.autopilot import Autopilot
        from scripts.l2_pipeline.storage import ensure_once
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d).resolve();p=Autopilot(repo/'data/l2_pipeline/registry',MockAgents(repo))
            c=card();c['outcome'].update(years=[2000,2005]);path=ensure_once(repo/'data/l2_pipeline/card.json',c)
            row=dict(design_id='invented',attempt_id='new',route='V9',card=str(path),card_sha256=sha(path),work_dir=str(repo/'data/l2_pipeline/work'),prescreen_enabled=True)
            self.assertIsNone(p.agent_action(row,'history'))
            self.assertEqual(p.next_action(row),'GATE0_PARK')
            self.assertFalse((p.case_dir(row)/'jobs/history.json').exists())
            row=dict(row,attempt_id='inflight');row.pop('prescreen_enabled')
            p.agent_action(row,'history')
            self.assertTrue((p.case_dir(row)/'jobs/history.json').exists())
            self.assertFalse((p.case_dir(row)/'PRESCREEN.json').exists())

    def test_tolerance_uses_one_success_and_never_parks_on_inconclusive(self):
        from scripts.l2_pipeline.prescreen import screen_ri
        from scripts.l2_pipeline.engine.ri import inventory
        with patch('scripts.l2_pipeline.engine.ri.pre_family',return_value=[-2]),patch('scripts.l2_pipeline.engine.ri.predraw_labels',return_value=range(999)),patch('scripts.l2_pipeline.engine.ri.relabeled_design',side_effect=ValueError('RI lead inventory changed under relabeling')) as build:
            value=screen_ri({},{});self.assertFalse(value['passed']);self.assertEqual(build.call_count,20)
            self.assertLess(value['checks']['RI199']['maximum_possible_feasible_fraction'],.9)

class HubBatchTests(unittest.TestCase):
    def test_daily_unknown_classes_are_distinct_and_new_class_waits_next_day(self):
        from scripts.l2_pipeline.maintenance import daily_batch
        from scripts.l2_pipeline.autopilot import atomic
        with tempfile.TemporaryDirectory() as d:
            registry=Path(d)/'registry';p=SimpleNamespace(registry=registry)
            queue=dict(entries=[dict(status='NEEDS_RULING',reason='ValueError: new alpha bug'),dict(status='NEEDS_RULING',reason='ValueError: new beta bug')])
            atomic(registry/'HUB_QUEUE.json',queue)
            self.assertEqual(len(daily_batch(p,'2026-10-11')['classes']),2)
            queue['entries'].append(dict(status='NEEDS_RULING',reason='ValueError: new gamma bug'));atomic(registry/'HUB_QUEUE.json',queue)
            self.assertEqual(len(daily_batch(p,'2026-10-11')['classes']),2)
            self.assertEqual(len(daily_batch(p,'2026-10-12')['classes']),1)
