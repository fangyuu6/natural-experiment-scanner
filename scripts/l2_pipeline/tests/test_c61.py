"""C61 production card inventories and invented numerical/metadata regressions."""
import copy,csv,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline.contract_card import registered_execution_card
from scripts.l2_pipeline.cards import load_card,validate_card
from scripts.l2_pipeline.variants import registered_cards,secondary_entries
from scripts.l2_pipeline.storage import sha
ROOT=Path(__file__).resolve().parents[3]

class CardTests(unittest.TestCase):
    def test_every_active_secondary_card_renders_and_loads(self):
        from scripts.l2_pipeline.autopilot import Autopilot,read
        from scripts.l2_pipeline.stages.s4_specs import generate_spec
        pilot=Autopilot();count=0
        for row in read(pilot.registry/'ACTIVE_DESIGNS.json')['designs']:
            original=read(row['card'])
            root=pilot.backend.root(row)
            spec=read(root/'SPEC.json')
            if not secondary_entries(spec or original):continue
            if spec:
                card=read(root/'SCIENTIFIC_CARD.json') or dict(pilot.backend.ctx(row).card.payload)
                support=read(root/'SUPPORT.json')
            else:
                from scripts.l2_pipeline.connectors.rates import prospective_card
                card=prospective_card(original)
                support=dict(primary_passed=True,variants=[dict(variant=n,passed=True) for n in registered_cards(card)])
                # An unregistered intake has no S4 gate yet. Exercise its card
                # inventory without certifying purpose or scientific readiness.
                spec={k:copy.deepcopy(card[k]) for k in ('design_id','version','track','mechanism','population','analysis','treatment','outcome','sensitivities','first_order')}
                spec['secondary_outcomes']=secondary_entries(card)
            with self.subTest(design=row['design_id']),tempfile.TemporaryDirectory() as d:
                target=Path(d)
                for n,v in [('CARD.json',card),('SPEC.json',spec),('SUPPORT.json',support)]:
                    (target/n).write_text(json.dumps(v))
                manifest=dict(files={n:dict(role='card' if n=='CARD.json' else 'verification',sha256=sha(target/n)) for n in ['CARD.json','SPEC.json','SUPPORT.json']})
                rendered=registered_execution_card(target,manifest)
                self.assertNotIn('secondary_outcomes',rendered)
                pure={k:v for k,v in rendered.items() if k!='_card_path'}
                self.assertEqual(validate_card(pure),[])
                (target/'rendered.json').write_text(json.dumps(pure))
                loaded=load_card(target/'rendered.json')
                self.assertEqual(secondary_entries(loaded.payload),secondary_entries(spec))
                variants=registered_cards(dict(loaded.payload))
                self.assertEqual(len(variants),1+len(spec['sensitivities'])+len(secondary_entries(spec)))
                if (root/'SPEC.json').exists():
                    # Exercise load_registration too, with actual registered
                    # metadata and opaque panel bytes. Mock only final approvals,
                    # calibration certificates and forecasts absent at draft time.
                    from scripts.l2_pipeline import registered
                    from scripts.l2_pipeline.storage import ensure_once
                    from contextlib import ExitStack
                    import shutil
                    for n in ('TREATMENT.json','PANELS.json','SECONDARY_PANELS.json','PANELS_C79.json','SUPPORT_C79.json'):
                        if (root/n).exists():shutil.copyfile(root/n,target/n)
                    for n in ('panels','secondary_panels'):
                        if (root/n).exists():shutil.copytree(root/n,target/n)
                    spec=copy.deepcopy(spec);spec['card_sha256']=sha(target/'CARD.json')
                    (target/'SPEC.json').write_text(json.dumps(spec))
                    ensure_once(target/'CALIBRATION_GATES.json',dict(passed=True,cases={card['design_id']: {}}))
                    ensure_once(target/'PREDICTIONS_LOCK.json',dict(design_id=card['design_id'],spec_sha256=sha(target/'SPEC.json'),personas=[],files={}))
                    files={}
                    for item in target.iterdir():
                        if item.is_file():files[item.name]=dict(sha256=sha(item),role='card' if item.name=='CARD.json' else 'verification')
                    for folder in ['panels','secondary_panels']:
                        for item in (target/folder).glob('*/*'):
                            if item.is_file():files[str(item.relative_to(target))]=dict(sha256=sha(item),role='verification')
                    (target/'FREEZE_FINAL.json').write_text(json.dumps(dict(files=files)))
                    with ExitStack() as stack:
                        stack.enter_context(patch('scripts.l2_pipeline.registered.interval_plan',return_value=(dict(expected_designs=[card['design_id']],certification_path='SPEC.json'),None)))
                        stack.enter_context(patch('scripts.l2_pipeline.registered.validate_calibration'))
                        stack.enter_context(patch('scripts.l2_pipeline.engine.inference.validate_certification'))
                        stack.enter_context(patch('scripts.l2_pipeline.engine.panels._value',side_effect=AssertionError('Outcome parsed')))
                        loaded_bundle=registered.load_registration(target,rendered)
                    self.assertEqual(set(loaded_bundle['expected']),set(variants))
                    manifest['files']['SPEC.json']['sha256']=sha(target/'SPEC.json')
                changed=copy.deepcopy(spec);changed['analysis']['min_pre_periods']+=1
                (target/'SPEC.json').write_text(json.dumps(changed));manifest['files']['SPEC.json']['sha256']=sha(target/'SPEC.json')
                with self.assertRaisesRegex(ValueError,'scientific card differs'):
                    registered_execution_card(target,manifest)
            count+=1
        self.assertGreater(count,0)

class MaskTests(unittest.TestCase):
    def test_suppressed_unreliable_override_available_and_unknown_flags_fail_closed(self):
        from scripts.l2_pipeline.engine.panels import metadata_eligible_keys
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'metadata.csv'
            p.write_text('unit,year,available,annual_usable,linkage_certified,suppressed,unreliable\n'
                'A,2000,1,1,1,False,False\nA,2001,1,1,1,True,False\n'
                'A,2002,1,1,1,False,true\nA,2003,1,1,1,0,1\nA,2004,1,1,1,unknown,0\n')
            self.assertEqual(metadata_eligible_keys(p),{('A',2000)})

    def test_bad_value_is_redacted(self):
        from scripts.l2_pipeline.engine.panels import _value
        with self.assertRaisesRegex(ValueError,'^Outcome value is not numeric$'):
            _value(dict(outcome_value='PRIVATE_OUTCOME'))

    def test_primary_gate_uses_calibration_count_and_noise_keeps_999(self):
        from contextlib import ExitStack
        from scripts.l2_pipeline.tests.test_fx13_secondary_outcomes import mock_numerics
        from scripts.l2_pipeline.tests.test_s6_review_fixes import bundle
        from scripts.l2_pipeline import registered
        b,_=bundle();counts=[]
        with ExitStack() as stack:
            mock_numerics(stack)
            stack.enter_context(patch('scripts.l2_pipeline.engine.ri.permutation_reference',side_effect=lambda *a,**kw:counts.append(kw['count']) or {}))
            registered.run_registered(b,load_panel=lambda n:([],{}))
        self.assertEqual(counts[:2],[999,b['calibration']['counts']['RI']])

class DataTests(unittest.TestCase):
    def test_gfr_splice_and_catalog_preserve_other_years(self):
        from scripts.l2_pipeline.connectors import outcome_v12,outcome_v13
        old=outcome_v12.catalog(verify_sources=False);new=outcome_v13.catalog()
        sid='nchs_summary_fertility_rate'
        a=json.loads((ROOT/old['series'][sid]['artifact']['path']).read_text())
        b=json.loads((ROOT/new['series'][sid]['artifact']['path']).read_text())
        self.assertEqual([r for r in a['rows'] if r['year']!=1995],[r for r in b['rows'] if r['year']!=1995])
        rows=[r for r in b['rows'] if r['year']==1995];self.assertEqual(len(rows),51)
        for r in rows:
            self.assertAlmostEqual(r['outcome_value'],r['births']/r['female_population_15_44']*1000)
            self.assertEqual(r['source'],'WONDER transcription')
        for k,spec in old['series'].items():
            if k!=sid:self.assertEqual(dict(spec,registered_connector='outcome_v13_'+k),new['series'][k])

    def test_explicit_hub_include_keeps_original_and_c11_clock(self):
        from scripts.l2_pipeline.tests.test_c59_manual import ManualEvidenceTests
        from scripts.l2_pipeline.manual_evidence import supplement
        f=ManualEvidenceTests();f.setUp();f.row.update(status='unknown',start_period=None,
            named_conflict={'kind':'policy_definition_ambiguous'},reason='NEEDS_RULING')
        original=copy.deepcopy(f.value)
        f.packet['review']={'states':{'AL':dict(decision='include',hub_adjudicated=True,effective_date='2015-06-05',authority={'sha256':'a'*64})}}
        result,report=supplement(f.value,f.packet)
        self.assertEqual(f.value,original);self.assertEqual(result['records'][0]['start_period'],2016)
        self.assertEqual(result['records'][0]['hub_adjudication_original']['status'],'unknown')
        self.assertNotIn('named_conflict',result['records'][0])
        self.assertIn('named_conflict',result['records'][0]['hub_adjudication_original'])
        self.assertEqual(report['states'][0]['action'],'hub_adjudicated_inclusion')

    def test_hub_sensitivity_keeps_exclusions_and_survives_c48_reconstruction(self):
        from scripts.l2_pipeline.tests.test_pipeline import card
        from scripts.l2_pipeline.autopilot import Backend
        from scripts.l2_pipeline import c48_audit
        from scripts.l2_pipeline.storage import ensure_once
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);c=card();c['treatment']['excluded_units']=['TX','WI']
            cp=ensure_once(root/'CARD.json',c)
            row=dict(card=str(cp),card_sha256=sha(cp),work_dir=str(root/'work'))
            ruling=dict(binding=dict(path='invented.json',sha256='a'*64),sensitivities=[dict(name='exclude_IN',changes=dict(treatment=dict(excluded_units=['IN'])))])
            comparison=dict(accepted=[dict(unit='CA',audit_unresolved=True)])
            backend=Backend(ROOT)
            with patch('scripts.l2_pipeline.autopilot.durable',side_effect=lambda path,repo:Path(path)),patch('scripts.l2_pipeline.hub_adjudication.load',return_value=ruling),patch('scripts.l2_pipeline.c48_audit.bound_comparison',return_value=comparison):
                ctx=backend.ctx(row)
                sensitivity=next(x for x in ctx.card.payload['sensitivities'] if x['name']=='exclude_IN')
                self.assertEqual(sensitivity['changes']['treatment']['excluded_units'],['IN','TX','WI'])
                registered=c48_audit.register(ctx,comparison,[])
                recovered=backend.ctx(row)
                self.assertEqual(recovered.card.payload,registered.card.payload)
                self.assertIn('drop_unresolved_audit_dates',[s['name'] for s in recovered.card.payload['sensitivities']])

    def test_catalog_v13_construct_gate_returns_a_real_boolean_and_rejects_unknown(self):
        from scripts.l2_pipeline.connectors.population_v63 import same_construct
        for version in (12,13):
            self.assertIs(same_construct('births','nchs_summary_fertility_rate',version=version),True)
            self.assertIs(same_construct('invented_unreviewed_construct','nchs_summary_fertility_rate',version=version),False)
