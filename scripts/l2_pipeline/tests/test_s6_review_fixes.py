"""Complete S6 regression fixtures; all outcomes and approvals are invented."""
from __future__ import annotations
import copy
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline import registered
from scripts.l2_pipeline.engine import estimator, inference, ri
from scripts.l2_pipeline.engine.design import build_design
from scripts.l2_pipeline.execution import execute_once, DeclaredStop
from scripts.l2_pipeline.storage import write_once, sha
from scripts.l2_pipeline.tests.test_equivalence import invented_fixture
from scripts.l2_pipeline.tests.test_interval_review_fixes import evaluation, fixture


def calibration_record(design):
    from scripts.l2_pipeline.engine.simulation import COUNTS, OFFSETS
    return dict(case_id='invented',counts=COUNTS,seeds=dict(base=estimator.SEED,offsets=OFFSETS,RI=estimator.RI_SEED),
        targets_met=True,status='ready',training_tail_probability=.01,design_binding=inference.design_binding(design),
        critical_values=dict(pretrend_joint=0.,pretrend_economic=1e6,placebo=1e6),
        ri_feasibility=ri.inventory([{} for _ in range(199)]),tested_pre_event_times=ri.pre_family(design),
        heldout=dict(clean=dict(repetitions=100,full_blocking_set_rejected=0,full_blocking_set_rejection_rate=0.,
            per_check={k:dict(rejected=0,rejection_rate=0.) for k in estimator.CHECKS})))


def sealed_review(digest, design_id='invented'):
    return dict(design_id=design_id,candidate_sha256=digest,review_scope='sealed_candidate',complete_sweeps=1,
        must=[],should=[],verdict='PASS',fix_cycles=0,independence_attestation=dict(
            wrote_implementation=False,wrote_fixes=False,real_outcome_associations_read=False))


def bundle():
    declaration,rows=invented_fixture();design=build_design(declaration,rows)
    names=['primary','start_plus_one','unsupported','omit_state_X']
    declarations={n:dict(copy.deepcopy(declaration),variant=n) for n in names}
    forecasts={'persona':dict(point=1.,interval=[-1.,2.],interval_level=.8,original_sha256='a'*64,
        original=dict(near_zero_threshold=.25,direction='positive',prob_positive=.7,prob_negative=.1,
            prob_near_zero=.2,prob_diagnostics_pass=.6))}
    record=dict(expected=names,support={n:dict(support_pass=n!='unsupported') for n in names},declarations=declarations,
        bindings={n:n for n in names},calibration=calibration_record(design),forecasts=forecasts,
        interval_evaluation_path=None,interval_expected_designs=['invented'],design_id='invented',version='v1',
        spec_sha256='b'*64,manifest_sha256='c'*64)
    return record,rows


class S6ReviewFixes(unittest.TestCase):
    def test_full_registration_loader_binds_every_variant_without_decoding_outcomes(self):
        from scripts.l2_pipeline.tests.test_pipeline import card
        from scripts.l2_pipeline.engine.calibration import certify_intervals
        d,rows=invented_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=card();c['outcome'].update(years=[0,13],frequency='annual')
            c['analysis'].update(controls='never_treated')
            c['sensitivities']=[dict(name='unsupported',changes={})]
            records=[dict(unit=u,status='treated' if u in d['cohorts'] else 'never_treated',
                start_period=d['cohorts'].get(u)) for u in d['case']['sample_mask']['include_units']]
            c['treatment']['records']=records
            write_once(root/'CARD.json',c);c['_card_path']=str(root/'CARD.json')
            spec={k:c[k] for k in ('design_id','version','track','analysis','treatment','sensitivities','outcome')}
            spec['interval_path']=['Webb','CT_complete_control_path','point_plus_RI_if_uncertified']
            spec['card_sha256']=sha(root/'CARD.json');write_once(root/'SPEC.json',spec)
            write_once(root/'TREATMENT.json',dict(records=records))
            summary=dict(variant='primary',passed=True)
            decl=registered.numerical_declaration(c,summary,dict(records=records),'primary')
            design=build_design(decl,rows);summary['cell_inventory']=design['cells']
            write_once(root/'SUPPORT.json',dict(variants=[summary,dict(variant='unsupported',passed=False)]))
            panels=[]
            for name in ('primary','unsupported'):
                directory=root/'panels'/name;directory.mkdir(parents=True)
                # Poisoned tails establish that registration loading has no values path.
                (directory/'panel.csv').write_text('unit,year,outcome_value\n'+''.join(f'{u},{t},opaque-future-value\n' for u,t,_,_ in rows))
                (directory/'metadata.csv').write_text('unit,year\n'+''.join(f'{u},{t}\n' for u,t,_,_ in rows))
                panels.append(dict(variant=name,panel_sha256=sha(directory/'panel.csv'),metadata_sha256=sha(directory/'metadata.csv')))
            write_once(root/'PANELS.json',dict(variants=panels))
            _,op,_=fixture();op['design']=design
            write_once(root/'INTERVAL_CERTIFICATION.json',certify_intervals({'invented':evaluation(design,op)},expected_designs=['invented']))
            write_once(root/'INTERVAL_PLAN.json',dict(schema='l2_interval_plan_v1',expected_designs=['invented'],
                candidate_order=list(inference.METHODS),certification_path='INTERVAL_CERTIFICATION.json'))
            write_once(root/'CALIBRATION_GATES.json',dict(passed=True,cases={'invented':calibration_record(design)}))
            write_once(root/'predictions/persona/submission.json',dict(persona='persona',design_id='invented',spec_sha256=sha(root/'SPEC.json'),point=1.,interval=[0.,2.],interval_level=.8))
            write_once(root/'PREDICTIONS_LOCK.json',dict(personas=['persona'],design_id='invented',spec_sha256=sha(root/'SPEC.json'),
                files={'predictions/persona/submission.json':sha(root/'predictions/persona/submission.json')}))
            original=root/'predictions/persona/attempts/old/persona.filled.json'
            write_once(original,dict(point=1.,direction='positive'))
            collection=root/'predictions/persona/COLLECTION.json'
            write_once(collection,dict(original_path='/relocated/old/persona.filled.json',original_sha256=sha(original)))
            lock=json.loads((root/'PREDICTIONS_LOCK.json').read_text())
            lock['original_files']={str(p.relative_to(root)):sha(p) for p in (original,collection)}
            (root/'PREDICTIONS_LOCK.json').write_text(json.dumps(lock))
            files={str(p.relative_to(root)):dict(role='interval_plan' if p.name=='INTERVAL_PLAN.json' else 'fixture',sha256=sha(p)) for p in root.rglob('*') if p.is_file()}
            files['CARD.json']['role']='card'
            write_once(root/'FREEZE_FINAL.json',dict(files=files))
            with self.assertRaisesRegex(ValueError,'Original forecast escapes execution root'):
                registered.load_registration(root,c)
            from scripts.l2_pipeline.forecast_snapshot import create
            manifest=dict(files=files,forecast_snapshot=create(root,lock))
            (root/'FREEZE_FINAL.json').write_text(json.dumps(manifest))
            with patch('scripts.l2_pipeline.engine.panels._value',side_effect=AssertionError('Outcome decoded')):
                loaded=registered.load_registration(root,c)
            self.assertEqual(loaded['expected'],['primary','unsupported'])
            self.assertEqual(set(loaded['bindings']),{'primary','unsupported'})
            self.assertEqual(set(loaded['forecasts']),{'persona'})
            self.assertEqual(loaded['forecasts']['persona']['original']['point'],1.)
            # C72: a frozen addendum must survive both loader branches and
            # reach the production runner (invented outcomes only).
            self.assertIsNone(loaded['l3_export_v2'])
            from scripts.l2_pipeline.c64 import SPEC_NAME
            addendum=dict(geography={},first_order=dict(purpose_direction=-1))
            write_once(root/SPEC_NAME,addendum)
            files[SPEC_NAME]=dict(role='spec_addendum',sha256=sha(root/SPEC_NAME))
            (root/'FREEZE_FINAL.json').write_text(json.dumps(manifest))
            with patch('scripts.l2_pipeline.engine.panels._value',side_effect=AssertionError('Outcome decoded')):
                final=registered.load_registration(root,c)
                draft=registered.load_registration(root,c,_draft_manifest=manifest)
            self.assertEqual(final['l3_export_v2'],draft['l3_export_v2'])
            self.assertEqual(final['l3_export_v2']['first_order'],addendum['first_order'])
            self.assertTrue(draft['fitting_disabled'])
            with self.assertRaisesRegex(ValueError,'Draft'):registered.run_registered(draft)
            final['multiplier_count']=37
            final['interval_evaluation_path']=None
            out=registered.run_registered(final,load_panel=lambda binding:(rows,dict(invented=True)))
            self.assertNotEqual(out['status'],'FAILED_NO_RETRY',out.get('reason'))
            self.assertIn('l3_export_v2',out)
            self.assertEqual(out['l3_export_v2']['geography'],final['l3_export_v2']['geography'])
            self.assertIn('s_pre',out['l3_export_v2'])
            self.assertEqual(len(out['l3_export_v2']['placebo_labels']['draws']),999)
            # The mandatory gate follows registration with actual value loading,
            # without invoking run_registered or a numerical estimator.
            from scripts.l2_pipeline import dry_load
            manifest.update(code_snapshot=dict(digest='a'*64),dry_load_inventory=dry_load.inventory(root,files))
            (root/'FREEZE_FINAL.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError): dry_load.inputs(root)  # poisoned value tails
            panel=root/'panels/primary/panel.csv'
            panel.write_text('unit,year,outcome_value\n'+''.join(f'{u},{t},12345.6789\n' for u,t,_,_ in rows))
            files['panels/primary/panel.csv']['sha256']=sha(panel)
            p=json.loads((root/'PANELS.json').read_text());p['variants'][0]['panel_sha256']=sha(panel)
            (root/'PANELS.json').write_text(json.dumps(p));files['PANELS.json']['sha256']=sha(root/'PANELS.json')
            manifest['dry_load_inventory']=dry_load.inventory(root,files)
            (root/'FREEZE_FINAL.json').write_text(json.dumps(manifest))
            with patch('scripts.l2_pipeline.registered.run_registered',side_effect=AssertionError('Fit reached')), \
                 patch('scripts.l2_pipeline.engine.runner.fit_primary',side_effect=AssertionError('Fit reached')):
                dry=dry_load.inputs(root)
            self.assertEqual([r['variant'] for r in dry['loaded']],['primary'])
            self.assertNotIn('12345',json.dumps(dry))
            self.assertFalse((root/'EXECUTION_ONCE.json').exists())
            # The original intake hash can differ from the audited retained
            # card. Only frozen registration provenance may bridge them.
            spec=json.loads((root/'SPEC.json').read_text());spec['card_sha256']='f'*64
            (root/'SPEC.json').write_text(json.dumps(spec));files['SPEC.json']['sha256']=sha(root/'SPEC.json')
            write_once(root/'REGISTRATION.json',dict(card_sha256='f'*64))
            files['REGISTRATION.json']=dict(role='verification',sha256=sha(root/'REGISTRATION.json'))
            lock=json.loads((root/'PREDICTIONS_LOCK.json').read_text());lock['spec_sha256']=sha(root/'SPEC.json')
            (root/'PREDICTIONS_LOCK.json').write_text(json.dumps(lock));files['PREDICTIONS_LOCK.json']['sha256']=sha(root/'PREDICTIONS_LOCK.json')
            (root/'FREEZE_FINAL.json').write_text(json.dumps(manifest))
            with patch('scripts.l2_pipeline.engine.panels._value',side_effect=AssertionError('Outcome decoded')):
                self.assertEqual(registered.load_registration(root,c)['expected'],['primary','unsupported'])
            (root/'REGISTRATION.json').write_text(json.dumps(dict(card_sha256='e'*64)))
            files['REGISTRATION.json']['sha256']=sha(root/'REGISTRATION.json');(root/'FREEZE_FINAL.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'Scientific execution identity changed'):registered.load_registration(root,c)
            (root/'REGISTRATION.json').write_text(json.dumps(dict(card_sha256='f'*64)))
            files['REGISTRATION.json']['sha256']=sha(root/'REGISTRATION.json');(root/'FREEZE_FINAL.json').write_text(json.dumps(manifest))
            (root/'panels/unsupported/metadata.csv').write_text('unit,year\n')
            with self.assertRaisesRegex(ValueError,'absent/changed'):
                registered.load_registration(root,c)

    def test_registered_primary_companion_omission_diagnostics_ri_noise_forecasts(self):
        b,rows=bundle();loaded=[]
        def load(name):loaded.append(name);return rows,dict(passed=True)
        out=registered.run_registered(b,load_panel=load)
        self.assertEqual(out['status'],'COMPLETED')
        self.assertEqual(loaded,['primary','start_plus_one','omit_state_X'])
        primary=out['registered_results']['primary']
        for family in ('group_time','event_study','group','calendar','overall'):
            self.assertTrue(primary['result'][family])
        self.assertTrue(primary['reference']['passed'])
        self.assertEqual(out['registered_results']['unsupported']['status'],'UNSUPPORTED')
        self.assertEqual(out['registered_results']['start_plus_one']['status'],'ESTIMATED')
        self.assertEqual(out['omissions']['omit_state_X']['status'],'ESTIMATED')
        self.assertIsNotNone(out['omission_range'])
        self.assertEqual(out['noise_floor']['feasibility']['requested_draws'],999)
        self.assertEqual(set(out['diagnostics']['statistics']),set(estimator.CHECKS))
        self.assertEqual(len(out['gate_table']),3)
        comparison=out['forecast_comparison']['rows'][0]
        self.assertEqual(comparison['realized_ATT'],primary['result']['overall']['att'])
        self.assertIn('direction_brier',comparison);self.assertIn('diagnostics_brier',comparison)
        self.assertFalse(out['forecast_comparison']['selection_affected_by_scores'])
        self.assertIsNone(primary['result']['overall']['ci95'])

    def test_primary_statistical_stop_preserves_all_sensitivity_numbers(self):
        b,rows=bundle();b['calibration']['critical_values']['pretrend_joint']=1.
        out=registered.run_registered(b,load_panel=lambda n:(rows,dict(passed=True)))
        self.assertEqual(out['status'],'STOPPED');self.assertEqual(out['stop_class'],'STATISTICAL_CALIBRATION')
        self.assertEqual(out['omissions']['omit_state_X']['status'],'ESTIMATED')
        self.assertEqual(out['forecast_comparison']['status'],'SCORED')

    def test_unexpected_sensitivity_failure_archives_traceback_and_not_run_once(self):
        b,rows=bundle()
        def load(name):
            if name=='start_plus_one':raise RuntimeError('invented sensitivity loader failure')
            return rows,dict(passed=True)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch('scripts.l2_pipeline.execution.require_verified',return_value={'manifest_sha256':'c'*64}):
                with self.assertRaises(registered.RegisteredExecutionError):
                    execute_once(root,'fixture_hub',lambda:b,lambda data:registered.run_registered(data,load_panel=load))
                out=json.loads((root/'RESULT.json').read_text())
                self.assertEqual(out['status'],'FAILED_NO_RETRY')
                self.assertEqual(out['omissions']['omit_state_X']['status'],'NOT_RUN')
                self.assertEqual(out['registered_results']['primary']['status'],'ESTIMATED')
                self.assertIn('RuntimeError',json.loads((root/'FAILURE.json').read_text())['traceback'])
                with self.assertRaises(FileExistsError):execute_once(root,'fixture_hub',lambda:None,lambda data:{})

    def test_shared_declared_stop_and_primary_support_stop(self):
        from scripts.l2_pipeline.engine.runner import DeclaredStop as NumericalStop
        self.assertIs(NumericalStop,DeclaredStop)
        b,rows=bundle();b['support']['primary']['support_pass']=False
        out=registered.run_registered(b,load_panel=lambda n:self.fail('Stopped primary loaded outcomes'))
        self.assertEqual(out['status'],'STOPPED')
        self.assertEqual(out['registered_results']['start_plus_one']['status'],'NOT_RUN')
        self.assertEqual(out['forecast_comparison']['status'],'NOT_ASSESSABLE')

    def test_s6_reaches_once_only_adapter_after_review_and_approval(self):
        from scripts.l2_pipeline.stages import s6_execute
        from scripts.l2_pipeline.contracts import DesignCard,StageContext
        b,rows=bundle()
        with tempfile.TemporaryDirectory() as tmp:
            workspace=Path(tmp);c=dict(design_id='invented',version='v1',policy_family='family',mechanism='fixture',
                track='DiD',unit='state',outcome_family='employment',automation=dict(hub_signer='fixture_hub'))
            path=workspace/'card.json';write_once(path,c)
            ctx=StageContext(DesignCard.from_mapping(c,path),workspace,workspace)
            retained=dict(c,analysis=dict(controls='never_treated'))
            write_once(ctx.design_dir/'CARD.json',retained)
            write_once(ctx.design_dir/'SPEC.json',dict(retained,analysis=dict(controls='not_yet_treated'),comparison_group='not_yet_treated'))
            write_once(ctx.design_dir/'SUPPORT.json',dict(variants=[dict(variant='primary',comparison_group='not_yet_treated')]))
            files={n:dict(role='card' if n=='CARD.json' else 'verification',sha256=sha(ctx.design_dir/n)) for n in ['CARD.json','SPEC.json','SUPPORT.json']}
            write_once(ctx.design_dir/'FREEZE_FINAL.json',dict(files=files))
            write_once(ctx.design_dir/'EXECUTION_REVIEW.json',sealed_review(sha(ctx.design_dir/'FREEZE_FINAL.json')))
            with patch('scripts.l2_pipeline.stages.s6_execute.verify',return_value={'ok':True}), \
                 patch('scripts.l2_pipeline.execution.require_verified',return_value={'manifest_sha256':'c'*64}), \
                 patch('scripts.l2_pipeline.registered.load_registration',return_value=b) as loader, \
                 patch('scripts.l2_pipeline.engine.panels.load_bound_panel',return_value=(rows,dict(passed=True))):
                result=s6_execute.run(ctx)
            self.assertEqual(result.disposition.value,'ready');self.assertEqual(loader.call_count,1)
            execution_card=loader.call_args.args[1]
            self.assertEqual(execution_card['analysis']['controls'],ctx.read('SPEC.json')['comparison_group'])
            self.assertEqual(execution_card['analysis'],ctx.read('SPEC.json')['analysis'])
            self.assertEqual(json.loads((ctx.design_dir/'TERMINAL.json').read_text())['status'],'COMPLETED')
            self.assertEqual(json.loads((ctx.design_dir/'RESULT.json').read_text())['forecast_comparison']['status'],'SCORED')

    def test_final_freeze_requires_full_certification_and_canonical_identity(self):
        from scripts.l2_pipeline.tests.test_pipeline import FinalApprovalTests
        from scripts.l2_pipeline.freeze import verify
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'original';FinalApprovalTests().make_bundle(root)
            self.assertTrue(verify(root,'final','fixture_hub')['ok'])
            copied=Path(tmp)/'copy';shutil.copytree(root,copied)
            opened=[]
            with self.assertRaisesRegex(RuntimeError,'Canonical study root'):
                execute_once(copied,'fixture_hub',lambda:opened.append(True),lambda data:{})
            self.assertEqual(opened,[])
            value=json.loads((root/'INTERVAL_CERTIFICATION.json').read_text());value['cases']['invented']['counts']['B']=99
            (root/'INTERVAL_CERTIFICATION.json').write_text(json.dumps(value))
            m=json.loads((root/'FREEZE_FINAL.json').read_text());m['files']['INTERVAL_CERTIFICATION.json']['sha256']=sha(root/'INTERVAL_CERTIFICATION.json')
            (root/'FREEZE_FINAL.json').write_text(json.dumps(m))
            self.assertFalse(verify(root,'final','fixture_hub',require_approval=False)['ok'])

    def test_calibration_claim_without_design_binding_or_registered_counts_refuses(self):
        b,rows=bundle();record=b['calibration']
        for mutate in (lambda r:r.pop('design_binding'),lambda r:r['counts'].update(training=10),
                       lambda r:r['heldout']['clean']['per_check']['placebo'].update(rejected=6,rejection_rate=.06)):
            r=copy.deepcopy(record);mutate(r)
            with self.assertRaises(ValueError):registered.validate_calibration(r)

    def test_s7_exports_stopped_terminal_and_forecast_records(self):
        from scripts.l2_pipeline.cli import preflight
        from scripts.l2_pipeline.contracts import StageContext,DesignCard
        from scripts.l2_pipeline.stages.s7_export import run
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=dict(design_id='invented',version='v1',policy_family='fixture',mechanism='fixture',track='DiD',
                unit='state',outcome_family='employment',lineage={})
            path=write_once(root/'card.json',c);ctx=StageContext(DesignCard.from_mapping(c,path),root,root)
            write_once(ctx.design_dir/'TERMINAL.json',dict(status='STOPPED',reason='registered gate'))
            write_once(ctx.design_dir/'RESULT.json',dict(status='STOPPED',forecast_comparison=dict(status='SCORED')))
            write_once(ctx.design_dir/'receipts/S6/1.json',dict(card_sha256=sha(path),result=dict(disposition='stopped',artifacts=[])))
            preflight(ctx,'S7');self.assertEqual(run(ctx).disposition.value,'ready')
            out=json.loads((ctx.design_dir/'EXPORT.json').read_text())
            self.assertEqual(out['l3_meta']['status'],'STOPPED')
            self.assertEqual(out['l3_meta']['result']['forecast_comparison']['status'],'SCORED')

    def test_batch_unexpected_failure_marks_remaining_designs_not_run_without_loader(self):
        from scripts.l2_pipeline.cli import main
        from scripts.l2_pipeline.contracts import StageResult,Disposition
        from scripts.l2_pipeline.tests.test_pipeline import card
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cards=root/'cards';cards.mkdir();workspace=root/'work'
            for name in ('a','b'):
                c=dict(card(),design_id=name);write_once(cards/(name+'.json'),c)
                write_once(workspace/name/'v1/SPEC.json',dict(design_id=name,version='v1',sensitivities=[dict(name='sensitivity')]))
            def fail(ctx,stage):
                write_once(ctx.design_dir/'FAILURE.json',dict(status='FAILED_NO_RETRY',message='invented failure'))
                return StageResult(stage,ctx.card.design_id,Disposition.FAILED,'invented failure')
            with patch('scripts.l2_pipeline.cli.run_stage',side_effect=fail) as run_stage,redirect_stdout(io.StringIO()):
                status=main(['run','--stage','S6','--batch',str(cards),'--work-dir',str(workspace)])
            self.assertEqual(status,1);self.assertEqual(run_stage.call_count,1)
            out=json.loads((workspace/'b/v1/RESULT.json').read_text())
            self.assertEqual(out['status'],'NOT_RUN')
            self.assertEqual(out['registered_results']['sensitivity']['status'],'NOT_RUN')
            self.assertFalse((workspace/'b/v1/EXECUTION_ONCE.json').exists())


if __name__=='__main__':unittest.main()
