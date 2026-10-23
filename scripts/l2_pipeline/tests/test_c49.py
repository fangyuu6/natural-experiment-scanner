"""C49 synthetic contracts and outcome-blind queue regressions; no study fits."""
from copy import deepcopy
from pathlib import Path
import tempfile,unittest
from unittest.mock import patch
from scripts.l2_pipeline.autopilot import Autopilot,Backend,read,atomic
from scripts.l2_pipeline.storage import sha,ensure_once
from scripts.l2_pipeline.connectors import c49_ucr,outcome_v9,outcome_v10,population_v63
from scripts.l2_pipeline.population_resolution import choose,revised_card
from scripts.l2_pipeline.agents.replacement import rejection_kind,ContractRejection
from scripts.l2_pipeline.c42 import Park
from scripts.l2_pipeline.first_order import first_order_gate
from scripts.l2_pipeline.c48_audit import prepare_s1
from scripts.l2_pipeline.tests import test_c48 as c48_tests

ROOT=Path(__file__).resolve().parents[3]


class UCRTests(unittest.TestCase):
    def source(self,**changes):
        return dict(year='1979',state_abbr='AL',state_name=' Alabama',population='10000',violent_crime='20',property_crime='50',caveats='',**changes)

    def test_two_constructs_use_same_source_population_and_keep_missing(self):
        source=self.source()
        v,_=c49_ucr.parse([source],'violent_crime');p,_=c49_ucr.parse([source],'property_crime')
        self.assertEqual(v['AL',1979]['value'],200);self.assertEqual(p['AL',1979]['value'],500)
        source['violent_crime']='';v,_=c49_ucr.parse([source],'violent_crime')
        self.assertIsNone(v['AL',1979]['value']);self.assertFalse(v['AL',1979]['available'])
        source['population']='0';p,_=c49_ucr.parse([source],'property_crime');self.assertIsNone(p['AL',1979]['value'])

    def test_source_identity_and_duplicate_fail_closed(self):
        source=self.source()
        with self.assertRaisesRegex(ValueError,'duplicate'):c49_ucr.parse([source,source],'violent_crime')
        source['state_abbr']='AK'
        with self.assertRaisesRegex(ValueError,'mismatch'):c49_ucr.parse([source],'violent_crime')
        source=self.source();source['violent_crime']='-1'
        with self.assertRaises(ValueError):c49_ucr.parse([source],'violent_crime')

    def test_v10_additive_with_complete_source_quality_sidecars(self):
        old=outcome_v9.catalog(verify_sources=False);new=outcome_v10.catalog()
        self.assertEqual(set(new['series'])-set(old['series']),set(c49_ucr.SERIES))
        for sid,s in old['series'].items():self.assertEqual(dict(s,registered_connector='outcome_v10_'+sid),new['series'][sid])
        for sid in c49_ucr.SERIES:
            s=new['series'][sid];self.assertEqual(s['years'],list(range(1979,2026)))
            meta=read(ROOT/s['support_artifact']['path']);self.assertFalse(meta['outcome_values_in_sidecar'])
            self.assertEqual(len(meta['metadata']),51*47)
            latest=[m for m in meta['metadata'] if m['year']==2025]
            self.assertEqual({m['unit'] for m in latest if m.get('limited_data')},{'FL','ND'})
            self.assertTrue(all(not m['annual_usable'] for m in latest if m['unit'] in {'FL','ND'}))
            self.assertTrue(any(m['annual_usable'] for m in latest if m['unit'] not in {'FL','ND'}))
            self.assertFalse(any(m['provisional'] or m['partial_year'] for m in latest))
            self.assertTrue(any(m.get('source_id_corrected') for m in meta['metadata'] if m['unit']=='ND' and m['year']==2022))
            self.assertFalse(any('outcome_value' in m or 'offense_count' in m for m in meta['metadata']))


class SelectionTests(unittest.TestCase):
    def test_every_table_entry_cites_c49_and_maps_to_bound_series(self):
        source,meta=population_v63.current_catalog();table=read(ROOT/source['construct_equivalences']['path'])
        for e in table['entries']:
            self.assertIn('HUB_RULINGS_C49.md §1',e['citation'])
            self.assertEqual(e['authority_sha256'],sha(ROOT/e['citation'].split(' §')[0]))
            result=choose(dict(policy_family='fixture'),dict(status='VERIFIED',target_outcome=e['card_construct']),source,meta)
            self.assertEqual(result['selected']['series_id'],e['primary_series']);self.assertEqual(result['secondary_series'],e['secondary_series'])
            self.assertEqual(result['outcome_values_read'],0)
        with self.assertRaisesRegex(ValueError,'genuinely new'):choose(dict(policy_family='fixture'),dict(status='VERIFIED',target_outcome='unrecognized_new_construct'),source,meta)

    def test_twelve_real_queued_purposes_revise_without_units_and_preserve_old(self):
        pilot=Autopilot();baseline=read(ROOT/'data/l2_pipeline/autopilot/c49/BASELINE.json')
        entries=read(ROOT/'data/l2_pipeline/autopilot/c49/OPEN_QUEUE_BASELINE.json')['entries']
        for e in entries:
            if e['action']!='purpose':continue
            old=next(r for r in baseline['active_rows'] if r['design_id']==e['design_id']);card=read(old['card']);before=sha(old['card'])
            purpose=read(pilot.backend.root(old)/'PURPOSE_VERIFICATION.json');resolution=choose(card,purpose)
            new=revised_card(card,purpose,resolution,ROOT)
            self.assertEqual(before,sha(old['card']));self.assertEqual(new['outcome']['units'],card['outcome'].get('units',card['treatment']['universe']))
            self.assertTrue(first_order_gate(new)['passed'],first_order_gate(new));self.assertIn('dilution',new['first_order']['population_resolution']['expected_dilution'])
            if 'preregistration' in old['design_id']:
                self.assertEqual(new['outcome']['frequency'],'biennial');self.assertTrue(new['outcome']['republication_prohibited'])
            if 'parole_abolition' in old['design_id']:
                self.assertEqual(new['outcome']['series_id'],'fbi_ucr_violent_crime_rate')
                self.assertEqual(new['first_order']['secondary_outcomes'][0]['series_id'],'fbi_ucr_property_crime_rate')
            if 'zero_tolerance' in old['design_id']:self.assertEqual(new['outcome']['series_id'],'fars_drinking_driver_crash_fatality_rate')

    def test_v9_binding_is_unchanged_and_equivalence_hash_is_required(self):
        baseline=read(ROOT/'data/l2_pipeline/autopilot/c49/BASELINE.json')
        self.assertEqual(baseline['catalog_v9_sha256'],sha(outcome_v9.CATALOG_PATH))
        self.assertEqual(baseline['population_v9_sha256'],sha(population_v63.V9_PATH))
        _,meta=population_v63.current_catalog();meta['construct_equivalences']['sha256']='bad'
        with self.assertRaisesRegex(ValueError,'table changed'):population_v63.target_metadata('voter turnout',meta=meta)


class ReplacementTests(unittest.TestCase):
    def test_log_defect_and_source_id_defect_route_only_once_then_park(self):
        for message,kind in [('V9 requires a state-specific research log for every unit','V9_RESEARCH_LOG'),('V9 research log missing','V9_RESEARCH_LOG'),('V9 active research time invalid','V9_RESEARCH_LOG'),('V9 source id/URL invalid or duplicate','V9_SOURCE_ID_URL')]:
            self.assertEqual(rejection_kind(ValueError(message)),kind)
            self.assertIsNone(rejection_kind(ValueError(message),dict(status='UNRESOLVED')))
            with tempfile.TemporaryDirectory() as d:
                pilot=Autopilot(Path(d)/'data/l2_pipeline/registry',Backend(Path(d).resolve()));row=dict(design_id='fixture',attempt_id='a')
                slot=Path(d)/'replacement';ensure_once(slot/'REPLACEMENT.json',dict(used=True))
                with patch.object(pilot,'replacement_slot',return_value=slot):
                    with self.assertRaisesRegex(Park,'exhausted'):pilot.replace_submission(row,'history',ContractRejection('fixture.md',kind,message))

    def test_c49_superseded_stop_does_not_hide_new_failure(self):
        with tempfile.TemporaryDirectory() as d:
            pilot=Autopilot(Path(d)/'data/l2_pipeline/registry',Backend(Path(d).resolve()));row=dict(design_id='fixture',attempt_id='a')
            pilot.stop(row,'history','old');path=pilot.case_dir(row)/'STOP.json'
            ensure_once(pilot.case_dir(row)/'STOP_SUPERSEDED_C49.json',dict(stop=dict(path=str(path),sha256=sha(path)),evidence=[]))
            self.assertIsNone(pilot.stop_record(row));pilot.stop(row,'history','new');self.assertEqual(pilot.stop_record(row)['reason'],'new')


class S1Tests(unittest.TestCase):
    def test_s1_entry_registers_bound_sensitivity_without_changing_primary(self):
        helper=c48_tests.AuditTests();helper.setUp();self.addCleanup(helper.doCleanups)
        job=type('Job',(),dict(output_dir=str(helper.ctx.repo),output_path=helper.evidence[1]['path'],promote_path=helper.evidence[1]['path'],contract=dict(reconciliation_path=helper.evidence[0]['path'])))()
        ensure_once(Path(job.output_dir)/'COLLECTION.json',dict(validated=True,original_sha256=sha(job.output_path),promoted_sha256=sha(job.promote_path)))
        with patch('scripts.l2_pipeline.agents.jobs.load_job',return_value=job),patch('scripts.l2_pipeline.autopilot_history.validate_job'):
            ctx=prepare_s1(helper.ctx,'fixture.md')
        self.assertEqual(ctx.card.payload['treatment'],helper.ctx.card.payload['treatment'])
        self.assertIn('AR',ctx.card.payload['sensitivities'][0]['changes']['treatment']['excluded_units'])

if __name__=='__main__':unittest.main()
