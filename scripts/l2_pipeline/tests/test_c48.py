"""C48 contract tests: synthetic crash inputs and metadata, no real fits."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest,zipfile
from unittest.mock import patch
from scripts.l2_pipeline.agents.collect import validate_blindness
from scripts.l2_pipeline.c48_audit import declaration,register,effective_context,NAME,RULE
from scripts.l2_pipeline.connectors import outcome_v8,outcome_v9,population_v63,c48_fars
from scripts.l2_pipeline.contracts import DesignCard,StageContext
from scripts.l2_pipeline.storage import sha,ensure_once
from scripts.l2_pipeline.autopilot import Autopilot,Backend,atomic,read


def att():return dict(other_submissions_read=False,our_outcome_data_read=False,our_estimates_read=False,published_results_read=True)


class BlindnessTests(unittest.TestCase):
    def test_v7_published_read_allowed_for_every_legal_role(self):
        for stage in ('S0','S1'):
            result=validate_blindness(SimpleNamespace(stage=stage,contract={}),dict(blindness_attestation=att(),outcome_associations_computed=False))
            self.assertTrue(result['published_results_read'])
        for key in ('other_submissions_read','our_outcome_data_read','our_estimates_read'):
            value=att();value[key]=True
            with self.assertRaises(ValueError):validate_blindness(SimpleNamespace(stage='S1',contract={}),dict(blindness_attestation=value))
        for key in ('our_forecasts_read','outcome_associations_computed','outcome_models_fitted'):
            with self.assertRaises(ValueError):validate_blindness(SimpleNamespace(stage='S1',contract={}),dict(blindness_attestation=att(),blindness_incident={key:True}))

    def test_legacy_incident_disclosure_and_ambiguous_exposure(self):
        job=SimpleNamespace(stage='S1',contract={})
        value=dict(blindness_attestation=dict(other_submissions_read=False,outcome_associations_read=True),blindness_incident=dict(detail='Public published snippets. No repository outcome panels, peer submissions were read. Outcome material was not used to code dates or run associations.'))
        self.assertTrue(validate_blindness(job,value)['published_results_read'])
        value['blindness_incident']['project_outcome_files_read']=True
        with self.assertRaises(ValueError):validate_blindness(job,value)
        value.pop('blindness_incident')
        with self.assertRaisesRegex(ValueError,'lacks project'):validate_blindness(job,value)

    def test_old_false_receipt_shape_preserved_and_false_not_zero(self):
        job=SimpleNamespace(stage='S1',contract={})
        value=dict(blindness_attestation=dict(other_submissions_read=False,outcome_associations_read=False))
        self.assertIsNone(validate_blindness(job,value))
        value['blindness_attestation']['outcome_associations_read']=0
        with self.assertRaises(ValueError):validate_blindness(job,value)

    def test_legacy_published_receipt_still_uses_v7(self):
        job=SimpleNamespace(stage='S1',contract={'source_type':'published_did_study'})
        value=dict(blindness_attestation=dict(other_submissions_read=False,outcome_associations_read=False))
        self.assertEqual(validate_blindness(job,value)['validation_basis'],'legacy_all_false_attestation')


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);repo=Path(self.temp.name)
        path=repo/'card.json';self.payload=dict(schema_version='1.0',design_id='fixture',version='v1',policy_family='fixture',mechanism='fixture',track='DiD',unit='state',outcome_family='fixture',treatment=dict(excluded_units=['XX']),sensitivities=[])
        ensure_once(path,self.payload);ruling=repo/RULE;ensure_once(ruling,dict(fixture=True))
        self.ctx=StageContext(DesignCard.from_mapping(self.payload,path),repo/'work',repo)
        from scripts.l2_pipeline.intake.official_history import corrected_comparison
        from scripts.l2_pipeline.intake.history_routing import select_fresh_sample
        rows=[dict(unit=u,status='treated',start_period=2000) for u in ['AR']+['Y'+str(i) for i in range(9)]]
        report=dict(historical_comparison=dict(accepted=rows,gaps=[]),audit_eligible_records=rows,fresh_policy=dict(seed=123,excluded_units=[]))
        sample=select_fresh_sample(rows,report['fresh_policy'])
        checks=[dict(unit=u,status='unresolved' if u=='AR' else 'match',official_locator='fixture') for u in sample]
        self.comparison=corrected_comparison(report['historical_comparison'],checks,sample)[0]
        evidence=[ensure_once(repo/'RECONCILIATION.json',report),ensure_once(repo/'OFFICIAL_AUDIT.json',dict(checks=checks))]
        self.evidence=[dict(path=str(p),sha256=sha(p)) for p in evidence]

    def test_registered_before_s5_preserves_primary_and_unions_exclusions(self):
        before=sha(self.ctx.card.path);ctx=register(self.ctx,self.comparison,self.evidence)
        self.assertEqual(ctx.card.payload['treatment'],self.payload['treatment'])
        self.assertEqual(ctx.card.payload['sensitivities'],[dict(name=NAME,changes=dict(treatment=dict(excluded_units=['AR','XX'])))])
        self.assertEqual(before,sha(ctx.card.path))
        self.assertEqual(ctx.card.payload,register(ctx,self.comparison,self.evidence).card.payload)
        self.assertEqual(ctx.card.payload,effective_context(self.ctx).card.payload)

    def test_no_unresolved_no_overlay_and_frozen_rejects(self):
        self.assertIsNone(declaration(self.payload,dict(accepted=[dict(unit='AR')])))
        self.assertFalse((register(self.ctx,dict(accepted=[]),self.evidence).design_dir/'C48_AUDIT_PREPARATION.json').exists())
        ensure_once(self.ctx.design_dir/'FREEZE_PREP.json',dict(fixture=True))
        with self.assertRaisesRegex(ValueError,'frozen'):register(self.ctx,self.comparison,self.evidence)

    def test_sensitivity_cannot_remove_existing_exclusions_or_add_other_changes(self):
        payload=deepcopy(self.payload);payload['sensitivities']=[dict(name=NAME,changes=dict(treatment=dict(excluded_units=['ZZ'])))]
        self.assertEqual(declaration(payload,self.comparison)['sensitivities'][0]['changes']['treatment']['excluded_units'],['AR','XX','ZZ'])
        payload['sensitivities'][0]['changes']['analysis']={}
        with self.assertRaises(ValueError):declaration(payload,self.comparison)

    def test_tampered_evidence_and_scientific_overlay_rejected(self):
        register(self.ctx,self.comparison,self.evidence)
        path=self.ctx.design_dir/'C48_AUDIT_PREPARATION.json';value=read(path);value['sensitivities'][0]['changes']['treatment']['excluded_units']=[];atomic(path,value)
        with self.assertRaises(ValueError):effective_context(self.ctx)

    def test_v3_threshold_still_binding(self):
        from scripts.l2_pipeline.stages.s1_treatment import evaluate_v3
        sample=[str(i) for i in range(10)]
        checks=[dict(unit=u,status='unresolved' if i<5 else 'match',official_locator='fixture') for i,u in enumerate(sample)]
        self.assertTrue(evaluate_v3(sample,checks)['passed'])
        checks[5]['status']='unresolved';self.assertFalse(evaluate_v3(sample,checks)['passed'])

    def test_coordinated_comparison_and_sensitivity_tamper_rejected(self):
        register(self.ctx,self.comparison,self.evidence)
        path=self.ctx.design_dir/'C48_AUDIT_PREPARATION.json';value=read(path)
        value['comparison']['accepted'][1]['audit_unresolved']=True
        derived=declaration(self.payload,value['comparison']);value.update(derived);atomic(path,value)
        with self.assertRaisesRegex(ValueError,'bound fresh audit'):effective_context(self.ctx)


class FarsTests(unittest.TestCase):
    def archive(self,body):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);p=Path(temp.name)/'FARS1980NationalCSV.zip'
        with zipfile.ZipFile(p,'w') as z:z.writestr('ACCIDENT.CSV',body)
        return p

    def test_counts_all_deaths_only_when_at_least_one_drinking_driver(self):
        p=self.archive('STATE,ST_CASE,YEAR,FATALS,DRUNK_DR\n1,10001,80,3,2\n1,10002,80,2,0\n1,10003,80,1,1\n')
        result=c48_fars.parse_archive(p,1980,['AL']);self.assertEqual(result['counts']['AL'],4);self.assertEqual(result['totals']['AL'],6)
        self.assertTrue(result['field_present'])

    def test_absent_field_is_missing_not_a_zero(self):
        p=self.archive('STATE,ST_CASE,YEAR,FATALS\n1,10001,2024,3\n')
        self.assertFalse(c48_fars.parse_archive(p,2024,['AL'])['field_present'])

    def test_duplicate_and_missing_state_fail_closed(self):
        p=self.archive('STATE,ST_CASE,YEAR,FATALS,DRUNK_DR\n1,10001,80,3,2\n1,10001,80,3,2\n')
        with self.assertRaises(ValueError):c48_fars.parse_archive(p,1980,['AL'])
        p=self.archive('STATE,ST_CASE,YEAR,FATALS,DRUNK_DR\n1,10001,80,3,2\n')
        with self.assertRaises(ValueError):c48_fars.parse_archive(p,1980,['AL','AK'])

    def test_v9_is_additive_and_complete_metadata(self):
        old=outcome_v8.catalog(verify_sources=False);new=outcome_v9.catalog(verify_sources=False)
        self.assertEqual(set(new['series'])-set(old['series']),{c48_fars.SID})
        for sid,spec in old['series'].items():self.assertEqual(dict(spec,registered_connector='outcome_v9_'+sid),new['series'][sid])
        source,meta=population_v63.current_catalog()
        for sid in new['series']:
            if sid.startswith(('fars_','brfss_','ipeds_','fbi_','acs1_dropout','acs1_snap')):self.assertIn(sid,meta['series'])
        self.assertTrue(population_v63.same_construct('SNAP participation (receipt of SNAP/food-stamp benefits)','acs1_snap_household_receipt_rate',version=9))
        self.assertTrue(population_v63.same_construct('driver_traffic_deaths','fars_driver_fatality_rate_age15_20',version=8))
        self.assertFalse(population_v63.same_construct('drinking_driver_crash_deaths','fars_all_road_fatality_rate',version=9))

    def test_v9_identity_rejects_wrong_filter_and_measure(self):
        from scripts.l2_pipeline.connectors.common import ConnectorRequest
        s=outcome_v9.catalog(verify_sources=False)['series'][c48_fars.SID]
        options=dict(connector='outcome_v9_'+c48_fars.SID,series_id=c48_fars.SID,measure_id=c48_fars.SID,population_filters=s['population_filters'])
        req=ConnectorRequest(options,{},(1980,),('AL',),'annual',c48_fars.REPO)
        with self.assertRaises(ValueError):outcome_v9._construct(req,outcome_v9.catalog(verify_sources=False))
        options['measure_id']='fars_all_road_fatality_rate'
        with self.assertRaises(ValueError):outcome_v9._construct(req,outcome_v9.catalog(verify_sources=False))


class StopTests(unittest.TestCase):
    def test_c48_supersession_keeps_a_fresh_failure_visible(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d).resolve();pilot=Autopilot(repo/'data/l2_pipeline/registry',Backend(repo));row=dict(design_id='fixture',attempt_id='v1')
            pilot.stop(row,'audit','old attestation failure');path=pilot.case_dir(row)/'STOP.json'
            ensure_once(pilot.case_dir(row)/'STOP_SUPERSEDED_C48.json',dict(stop=dict(path=str(path),sha256=sha(path)),evidence=[]))
            self.assertIsNone(pilot.stop_record(row));pilot.stop(row,'audit','new real failure')
            self.assertEqual(pilot.stop_record(row)['reason'],'new real failure')

if __name__=='__main__':unittest.main()
