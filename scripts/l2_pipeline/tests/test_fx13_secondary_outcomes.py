"""Secondary registration coverage; invented values and mocked fits only."""
from __future__ import annotations
import copy
import hashlib
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline import registered
from scripts.l2_pipeline.variants import registered_cards, registered_names, secondary_name
from scripts.l2_pipeline.tests.test_pipeline import card
from scripts.l2_pipeline.tests.test_s6_review_fixes import bundle
from scripts.l2_pipeline.storage import write_once, sha


def mock_numerics(stack):
    def fit(declaration, rows, **kwargs):
        kwargs['on_estimate']()
        return dict(status='ESTIMATED', result=dict(overall=dict(att=1.))), {}, {}
    stack.enter_context(patch('scripts.l2_pipeline.engine.runner.fit_primary', side_effect=fit))
    stack.enter_context(patch('scripts.l2_pipeline.engine.ri.permutation_reference', return_value={}))
    stack.enter_context(patch('scripts.l2_pipeline.engine.ri.diagnostics', return_value=dict(statistics={})))
    stack.enter_context(patch('scripts.l2_pipeline.engine.noise.noise_summary', return_value={}))
    stack.enter_context(patch('scripts.l2_pipeline.registered.validate_calibration', return_value={}))
    stack.enter_context(patch('scripts.l2_pipeline.engine.ri.gate_checks', return_value=[]))


class SecondaryOutcomes(unittest.TestCase):
    def test_inventory_and_own_homicide_measurement_preserve_primary_bytes(self):
        c=card();before=json.dumps(c,sort_keys=True)
        c['outcome'].update(population_filters={'injury_intent':'all','injury_mechanism':'firearm'})
        c['population']['filters']=dict(c['outcome']['population_filters'])
        c['first_order']['secondary_outcomes']=[dict(outcome='wonder_firearm_homicide_deaths',role='secondary',
            connector='outcome_v4_wonder_firearm_homicide_deaths',paths=['invented.tsv'],source_sha256={'invented.tsv':'a'*64})]
        original=copy.deepcopy(c);variants=registered_cards(c);name=secondary_name(c['first_order']['secondary_outcomes'][0])
        self.assertEqual(variants['primary'],original)
        secondary=variants[name]
        self.assertEqual(secondary['population']['filters']['injury_intent'],'homicide')
        self.assertEqual(secondary['outcome']['source_population_filters'],secondary['population']['filters'])
        self.assertEqual(secondary['analysis'],original['analysis'])
        self.assertEqual(secondary['treatment'],original['treatment'])
        self.assertEqual(c,original)
        self.assertIn(name,registered_names(c))

    def test_supported_secondary_executes_and_forecasts_stay_primary(self):
        b,rows=bundle();name='secondary_invented_suicide';b['expected'].append(name)
        b['support'][name]={'support_pass':True};b['declarations'][name]={};b['bindings'][name]=name
        loaded=[]
        with ExitStack() as stack:
            mock_numerics(stack)
            result=registered.run_registered(b,load_panel=lambda n:(loaded.append(n) or [],{}))
        self.assertIn(name,loaded)
        self.assertEqual(result['registered_results'][name]['status'],'ESTIMATED')
        self.assertEqual(result['forecast_comparison']['rows'][0]['realized_ATT'],1.)
        self.assertEqual(result['registered_results'][name]['diagnostics']['reported_only'],True)

    def test_unsupported_secondary_survives_primary_stop_and_exception(self):
        for exception in (False,True):
            b,rows=bundle();name='secondary_invented_homicide';b['expected'].append(name)
            b['support'][name]=dict(support_pass=False,reason='Registered support insufficient',
                issues={'insufficient_treated':1},retained_treated=['CO','CT','RI','WA'],effective_support_minimums={'min_treated':6})
            if not exception:b['support']['primary']['support_pass']=False
            def load(n):raise RuntimeError('invented failure')
            if exception:
                with self.assertRaises(registered.RegisteredExecutionError) as caught:
                    registered.run_registered(b,load_panel=load)
                result=caught.exception.result
            else:result=registered.run_registered(b,load_panel=load)
            row=result['registered_results'][name]
            self.assertEqual(row['status'],'UNSUPPORTED')
            self.assertIn('4 < registered minimum 6',row['reason'])
            self.assertEqual(result['actual_data_estimation_calls'],0)

    def test_not_run_inventory_has_all_secondaries(self):
        s=dict(design_id='invented',version='v1',sensitivities=[],secondary_outcomes=[
            dict(outcome='homicide',role='secondary',connector='invented')])
        result=registered.not_run_result(s,'batch aborted')
        self.assertEqual(set(result['registered_results']),{'primary','secondary_homicide'})

    def test_unimplemented_secondary_is_explicit_without_blocking_primary(self):
        from scripts.l2_pipeline.contracts import DesignCard,StageContext
        from scripts.l2_pipeline.stages import s2_outcomes,s3_support
        c=card();c['sensitivities']=[]
        c['outcome'].update(frequency='annual',years=[1999,2001]);c['analysis'].update(controls='never_treated',min_pre_periods=3,min_controls=5,min_cohorts=2)
        c['first_order']['secondary_outcomes']=[dict(outcome='invented_unknown',role='secondary',connector='invented_unknown')]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=write_once(root/'card.json',c);ctx=StageContext(DesignCard.from_mapping(c,path),root/'work',root)
            def construct(value,base,directory):
                directory.mkdir(parents=True,exist_ok=True)
                (directory/'panel.csv').write_text('unit,year,outcome_value\nCA,2000,poison\n')
                (directory/'metadata.csv').write_text('unit,year\nCA,2000\n')
                record=dict(panel_path=str(directory/'panel.csv'),metadata_path=str(directory/'metadata.csv'),
                    panel_sha256=sha(directory/'panel.csv'),metadata_sha256=sha(directory/'metadata.csv'),source_files=[])
                write_once(directory/'CONSTRUCTION.json',record);return record
            write_once(ctx.design_dir/'TREATMENT.json',dict(records=[]))
            with patch('scripts.l2_pipeline.panel_builder.build_panel',side_effect=construct):
                self.assertEqual(s2_outcomes.run(ctx).disposition.value,'ready')
            with patch('scripts.l2_pipeline.stages.s3_support.support_check',return_value=dict(passed=True,disposition='ready',reason='invented supported')):
                self.assertEqual(s3_support.run(ctx).disposition.value,'ready')
            support=ctx.read('SUPPORT.json')['variants'][1]
            self.assertEqual(support['state'],'UNSUPPORTED')
            self.assertIn('own registered population_filters',support['reason'])
            self.assertFalse((ctx.design_dir/'panels/secondary_invented_unknown/panel.csv').exists())

    def test_secondary_sidecar_freeze_inventory_is_complete(self):
        from scripts.l2_pipeline.registration import _inventory
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_once(root/'PREDICTIONS_LOCK.json',dict(files={},original_files={}))
            write_once(root/'SECONDARY_PANELS.json',{})
            for name in ('panel.csv','metadata.csv','SUPPORT.json','CONSTRUCTION.json','VARIANT_CARD.json'):
                path=root/'secondary_panels/homicide'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('invented')
            class Context:design_dir=root
            inventory=_inventory(Context())
            self.assertEqual(inventory['SECONDARY_PANELS.json'],'verification')
            self.assertEqual(inventory['secondary_panels/homicide/panel.csv'],'outcome')
            self.assertEqual(inventory['secondary_panels/homicide/SUPPORT.json'],'support')

    def test_no_secondary_finished_design_not_run_bytes_unchanged(self):
        # Existing finished prototype identity; frozen sources/outcomes are not
        # opened. This golden is the pre-FX13 terminal serialization contract.
        s=dict(design_id='automatic_voter',version='v1',sensitivities=[dict(name='omit_state_A'),dict(name='start_plus_one')],secondary_outcomes=[])
        expected=b'{"actual_data_estimation_calls":0,"design_id":"automatic_voter","forecast_comparison":{"rows":[],"status":"NOT_ASSESSABLE"},"omissions":{"omit_state_A":{"reason":"invented batch abort","result":null,"status":"NOT_RUN"}},"reason":"invented batch abort","registered_results":{"primary":{"reason":"invented batch abort","result":null,"status":"NOT_RUN"},"start_plus_one":{"reason":"invented batch abort","result":null,"status":"NOT_RUN"}},"schema":"l2_registered_result_v1","status":"NOT_RUN","version":"v1"}'
        actual=json.dumps(registered.not_run_result(s,'invented batch abort'),sort_keys=True,separators=(',',':')).encode()
        self.assertEqual(actual,expected)

    def test_real_finished_design_full_mocked_execution_matches_pre_fix_bytes(self):
        root=Path(__file__).resolve().parents[3]/'data/l2_pipeline/jobs/run2/postdispatch/work/run2_breast_density_notification/v1'
        spec=json.loads((root/'SPEC.json').read_text())
        self.assertEqual(json.loads((root/'TERMINAL.json').read_text())['status'],'COMPLETED')
        self.assertEqual(spec.get('secondary_outcomes',[]),[])
        names=registered_names(spec)
        b=dict(expected=names,support={n:{'support_pass':True} for n in names},declarations={n:{} for n in names},
            bindings={n:n for n in names},forecasts={},calibration={'counts':{'RI':199}},interval_evaluation_path=None,
            interval_expected_designs=[spec['design_id']],design_id=spec['design_id'],version=spec['version'],
            spec_sha256='b'*64,manifest_sha256='c'*64)
        with ExitStack() as stack:
            mock_numerics(stack);result=registered.run_registered(b,load_panel=lambda binding:([],{}))
        payload=json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        # Captured from the unmodified registered.py on these real registered
        # names, with the same invented callback results. No archival fit.
        self.assertEqual(len(payload),819)
        self.assertEqual(hashlib.sha256(payload).hexdigest(),'431add08700cc7261f33d9844a354c1c76a29379e4e33866dec609175cf0fcc8')

    def test_sidecar_loader_binds_own_support_without_decoding_values(self):
        from scripts.l2_pipeline.support import support_check,declared_annual_periods
        from scripts.l2_pipeline.engine.panels import metadata_eligible_keys
        from scripts.l2_pipeline.stages.s3_support import variant_treatment
        from scripts.l2_pipeline.tests.test_equivalence import invented_fixture
        d,rows=invented_fixture();c=card()
        c['outcome'].update(years=[0,13],period_years=list(range(14)),frequency='annual')
        c['sensitivities']=[]
        c['analysis'].update(controls='never_treated',min_pre_periods=3,min_controls=5,min_cohorts=2,min_treated=6,support_gap_rule='drop_affected_state_v7')
        treatment=[dict(unit=u,status='treated' if u in d['cohorts'] else 'never_treated',start_period=d['cohorts'].get(u)) for u in d['case']['sample_mask']['include_units']]
        c['treatment']['records']=treatment
        c['first_order']['secondary_outcomes']=[dict(outcome='wonder_firearm_'+intent+'_deaths',role='secondary',connector='outcome_v4_wonder_firearm_'+intent+'_deaths') for intent in ('homicide','suicide')]
        variants=registered_cards(c)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write_once(root/'CARD.json',c);c['_card_path']=str(root/'CARD.json')
            spec={k:c[k] for k in ('design_id','version','track','analysis','treatment','sensitivities','outcome')}
            spec.update(secondary_outcomes=c['first_order']['secondary_outcomes'],interval_path=['Webb','CT_complete_control_path','point_plus_RI_if_uncertified'],card_sha256=sha(root/'CARD.json'))
            write_once(root/'SPEC.json',spec);write_once(root/'TREATMENT.json',dict(records=treatment))
            panels=[];supports=[];secondaries=[]
            for name,v in variants.items():
                sid=name.removeprefix('secondary_');directory=root/('panels/primary' if name=='primary' else 'secondary_panels/'+sid);directory.mkdir(parents=True)
                selected=[r for r in rows if name!='secondary_wonder_firearm_homicide_deaths' or r[0]!='F']
                (directory/'panel.csv').write_text('unit,year,outcome_value\n'+''.join(f'{u},{t},poison-not-a-number\n' for u,t,_,_ in selected))
                (directory/'metadata.csv').write_text('unit,year\n'+''.join(f'{u},{t}\n' for u,t,_,_ in selected))
                summary=support_check(metadata_eligible_keys(directory/'metadata.csv'),variant_treatment(treatment,v),v['analysis'],period_years=declared_annual_periods(v['outcome']))
                construction=dict(connector=v['outcome']['connector'],population_filters=v['outcome'].get('population_filters',{}),panel_sha256=sha(directory/'panel.csv'),metadata_sha256=sha(directory/'metadata.csv'))
                if name=='primary':panels.append(dict(variant=name,**construction));supports.append(dict(variant=name,**summary))
                else:
                    write_once(directory/'SUPPORT.json',summary)
                    secondaries.append(dict(outcome=sid,role='secondary',construction=construction,support_sha256=sha(directory/'SUPPORT.json'),support_passed=summary['support_pass']))
            write_once(root/'PANELS.json',dict(variants=panels));write_once(root/'SUPPORT.json',dict(variants=supports));write_once(root/'SECONDARY_PANELS.json',dict(secondaries=secondaries))
            write_once(root/'CALIBRATION_GATES.json',dict(passed=True,cases={c['design_id']: {}}))
            write_once(root/'PREDICTIONS_LOCK.json',dict(design_id=c['design_id'],spec_sha256=sha(root/'SPEC.json'),personas=[],files={}))
            files={str(p.relative_to(root)):dict(sha256=sha(p),role='fixture') for p in root.rglob('*') if p.is_file()}
            files['CARD.json']['role']='card'
            write_once(root/'FREEZE_FINAL.json',dict(files=files))
            from scripts.l2_pipeline.contract_card import registered_execution_card
            c=registered_execution_card(root,dict(files=files))
            with patch('scripts.l2_pipeline.registered.interval_plan',return_value=(dict(expected_designs=[c['design_id']],certification_path='SPEC.json'),None)), \
                 patch('scripts.l2_pipeline.registered.validate_calibration'),patch('scripts.l2_pipeline.engine.inference.validate_certification'), \
                 patch('scripts.l2_pipeline.engine.panels._value',side_effect=AssertionError('Outcome decoded')):
                loaded=registered.load_registration(root,c)
            supported='secondary_wonder_firearm_suicide_deaths';unsupported='secondary_wonder_firearm_homicide_deaths'
            self.assertEqual(loaded['expected'],['primary',unsupported,supported])
            self.assertIn(supported,loaded['declarations'])
            self.assertIn(unsupported,loaded['bindings'])
            self.assertNotIn(unsupported,loaded['declarations'])
            self.assertFalse(loaded['support'][unsupported]['support_pass'])
            self.assertTrue(loaded['support'][supported]['support_pass'])


if __name__=='__main__':unittest.main()
