"""C46 synthetic regressions and metadata-only catalog checks; no real fits."""
from copy import deepcopy
import csv,io,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline.autopilot import Autopilot,Backend,atomic,read
from scripts.l2_pipeline.requeue import requeue
from scripts.l2_pipeline.storage import sha,ensure_once
from scripts.l2_pipeline.connectors import c46_acs_ipeds as acs,c46_fars as fars,c46_public as public
from scripts.l2_pipeline.connectors.common import ConnectorError,ConnectorRequest


class ParsingTests(unittest.TestCase):
    def test_acs_exact_universes_include_out_of_labor_force(self):
        self.assertEqual(acs.acs_formula('B14005',{1:200,12:8,26:12}),10)
        self.assertEqual(acs.acs_formula('C18120',{4:20,7:5,10:75},'with_disability'),20)
        self.assertEqual(acs.acs_formula('C18120',{5:80,8:10,11:10},'without_disability'),80)
        self.assertIsNone(acs.acs_formula('B14005',{1:200,12:None,26:12}))
        self.assertIsNone(acs.acs_formula('C18120',{4:0,7:0,10:0},'with_disability'))

    def test_acs_negative_sentinel_and_header_vintages(self):
        self.assertIsNone(public.number('-666666666'))
        self.assertEqual(acs.table_cell({'B14005_012E':'4'},'B14005',12),4)
        self.assertEqual(acs.table_cell({'B14005_E012':'4'},'B14005',12),4)
        with self.assertRaises(ConnectorError):acs.table_cell({'B14005_M012':'4'},'B14005',12)

    def test_fars_person_role_and_motorcycle_vintages_and_unknown_ages(self):
        for y,b in [(1975,15),(1982,20),(1991,80),(2024,89)]:
            s,_=fars.classify(1,17,b,y);self.assertTrue(s['motorcyclist']);self.assertTrue(s['driver16_17'])
        self.assertFalse(fars.classify(1,17,15,1982)[0]['motorcyclist'])
        self.assertFalse(fars.classify(3,2,80,1975)[0]['child0_4'])
        self.assertTrue(fars.classify(3,2,80,1982)[0]['child0_4'])
        self.assertTrue(fars.classify(4,999,None,2009)[0]['pedalcyclist'] is False)
        self.assertTrue(fars.classify(6,999,None,2009)[0]['pedalcyclist'])
        self.assertTrue(fars.classify(1,998,80,2024)[1]['driver15_20'])
        self.assertFalse(fars.classify(1,998,80,2024)[0]['driver15_20'])

    def test_fars_every_crash_reproduction_and_certified_subgroup_zero(self):
        with tempfile.TemporaryDirectory() as d,patch.object(fars,'UNITS',('AL',)):
            p=Path(d)/'FARS2024NationalCSV.zip'
            def put(deaths):
                with zipfile.ZipFile(p,'w') as z:
                    z.writestr('accident.csv','STATE,ST_CASE,YEAR,FATALS\n1,1,2024,'+str(deaths)+'\n')
                    z.writestr('person.csv','STATE,ST_CASE,VEH_NO,PER_NO,INJ_SEV,PER_TYP,AGE,BODY_TYP\n1,1,1,1,4,1,17,80\n')
            put(1);result=fars.parse_archive(p,2024);self.assertEqual(result['counts']['AL']['driver16_17'],1);self.assertEqual(result['counts']['AL']['pedalcyclist'],0)
            put(2)
            with self.assertRaisesRegex(ConnectorError,'reproduce'):fars.parse_archive(p,2024)

    def test_fars_not_reported_body_is_uncertified_since2010(self):
        with tempfile.TemporaryDirectory() as d,patch.object(fars,'UNITS',('AL',)):
            p=Path(d)/'fars.zip'
            for year in (2009,2010,2024):
                with zipfile.ZipFile(p,'w') as z:
                    z.writestr('accident.csv',f'STATE,ST_CASE,YEAR,FATALS\n1,1,{year},1\n')
                    z.writestr('person.csv','STATE,ST_CASE,VEH_NO,PER_NO,INJ_SEV,PER_TYP,AGE,BODY_TYP\n1,1,1,1,4,1,17,98\n')
                result=fars.parse_archive(p,year)
                self.assertEqual(result['unknown_body'],['AL'] if year>=2010 else [])
                self.assertEqual(result['counts']['AL']['motorcyclist'],0)

    def test_partial_provisional_year_retains_values_but_closes_annual_support(self):
        with patch.object(public,'UNITS',('AL',)),patch.object(public,'save_panel',side_effect=lambda *a,**k:a):
            result=public.save('fixture',{},[2024,2025],{('AL',2024):1,('AL',2025):2},[], 'all','url','rate',
                flags={('AL',2025):dict(partial_year=True,provisional=True)})
        self.assertEqual([r['outcome_value'] for r in result[2]],[1,2])
        self.assertEqual([m['available'] for m in result[3]],[1,1])
        self.assertEqual([m['annual_usable'] for m in result[3]],[1,0])

    def test_ipeds_totals_exclude_subtotals_and_second_majors_and_include_award21(self):
        hd=[dict(UNITID='1',FIPS='01',SECTOR='1'),dict(UNITID='2',FIPS='01',SECTOR='1')]
        ef=[dict(UNITID='1',EFALEVEL='1',EFTOTLT='100',XEFTOTLT='R'),dict(UNITID='1',EFALEVEL='2',EFTOTLT='60',XEFTOTLT='R')]
        co=[dict(UNITID='1',CIPCODE='99.0000',MAJORNUM='1',AWLEVEL='05',CTOTALT='4',XCTOTALT='R'),dict(UNITID='1',CIPCODE='99',MAJORNUM='1',AWLEVEL='21',CTOTALT='6',XCTOTALT='Z'),dict(UNITID='1',CIPCODE='99',MAJORNUM='2',AWLEVEL='05',CTOTALT='50',XCTOTALT='R')]
        with patch.object(acs,'zip_csv',side_effect=[iter(hd),iter(ef)]):
            values,flags,info=acs.ipeds_year('hd','ef',2024,'enrollment');self.assertEqual(values['AL'],100);self.assertTrue(flags['AL']['unreliable']);self.assertEqual(info['by_state']['AL']['missing'],1)
        with patch.object(acs,'zip_csv',side_effect=[iter(hd),iter(co)]):self.assertEqual(acs.ipeds_year('hd','c',2024,'completions')[0]['AL'],10)

    def test_hate_incidents_deduplicate_offenses_without_zero_filling(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'hate.zip';head='incident_id,data_year,state_abbr,state_name,ori,incident_date\n'
            with zipfile.ZipFile(p,'w') as z:z.writestr('hate_crime.csv',head+'1,2020,AL,Alabama,A,2020-01-01\n1,2020,AL,Alabama,A,2020-01-01\n2,2020,AL,Alabama,A,2020-02-01\n')
            counts,agencies=public.parse_hate(p);self.assertEqual(counts,{('AL',2020):2});self.assertNotIn(('AK',2020),counts)


class RequeueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.repo=Path(self.temp.name).resolve();self.registry=self.repo/'data/l2_pipeline/registry';self.pilot=Autopilot(self.registry,Backend(self.repo))
        original=read(Path(__file__).parents[1]/'examples/did_card.json')
        if original is None:
            original=read(Path(__file__).resolve().parents[3]/'data/l2_pipeline/intake/bulk_20261010/health/cards/bulk10_health_medicaid_sud_imd_waiver.json')
        self.card=deepcopy(original);self.card.update(design_id='c46-fixture',version='v1')
        self.oldpath=self.repo/'data/l2_pipeline/cards/old.json';atomic(self.oldpath,self.card)
        self.row=dict(design_id=self.card['design_id'],attempt_id='old',card=str(self.oldpath),card_sha256=sha(self.oldpath),work_dir=str(self.repo/'data/l2_pipeline/work'),route='V9')
        atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=[self.row]));self.registry.joinpath('AUTOPILOT_STOP').touch()
        self.pilot.ledger({},'loop','CLEAN_STOP','record');self.pilot.stop(self.row,'purpose','fixture needs ruling')
        for a in ['history','reconcile','audit']:ensure_once(self.pilot.checkpoint(self.row,a),dict(action=a))
        root=self.pilot.backend.root(self.row)
        for n in ['RECONCILIATION.json','CODING_LOCK.json','AUDIT_SELECTION.json']:ensure_once(root/n,dict(fixture=n))
        self.newpath=self.repo/'data/l2_pipeline/cards/new.json';new=deepcopy(self.card);new.update(version='v2',outcome_scale='rate');atomic(self.newpath,new)
        self.authority=self.repo/'data/l2_pipeline/rulings/authority.md';self.authority.parent.mkdir(parents=True);self.authority.write_text('fixture reviewed ruling')
        self.ruling=self.repo/'data/l2_pipeline/rulings/ruling.json';self.value=dict(schema='l2_requeue_ruling_v1',design_id=self.card['design_id'],prior_card_sha256=sha(self.oldpath),card_sha256=sha(self.newpath),decision='accept',reason='fixture',authority=dict(path=str(self.authority),sha256=sha(self.authority)))
        atomic(self.ruling,self.value)

    def test_new_card_resumes_s0_and_originals_retained_and_idempotent(self):
        before={str(p):sha(p) for p in self.pilot.backend.root(self.row).rglob('*') if p.is_file()};stopsha=sha(self.pilot.case_dir(self.row)/'STOP.json')
        value=requeue(self.card['design_id'],self.newpath,self.ruling,self.pilot)
        self.assertEqual(value['restart_action'],'purpose-direction');self.assertEqual(value,requeue(self.card['design_id'],self.newpath,self.ruling,self.pilot))
        row=read(self.registry/'ACTIVE_DESIGNS.json')['designs'][0];self.assertEqual(self.pilot.next_action(row),'purpose-direction');self.assertEqual(value['retained_actions'],['history','reconcile','audit'])
        self.assertEqual(read(self.registry/'HUB_QUEUE.json')['entries'][0]['status'],'SUPERSEDED_C46')
        self.assertEqual(before,{p:sha(p) for p in before});self.assertEqual(stopsha,sha(self.pilot.case_dir(self.row)/'STOP.json'))

    def test_receipt_recovers_crash_before_manifest_write(self):
        from scripts.l2_pipeline import requeue as module
        original=module.atomic
        def fail_manifest(path,value):
            if Path(path).name=='ACTIVE_DESIGNS.json':raise OSError('invented crash')
            return original(path,value)
        with patch.object(module,'atomic',side_effect=fail_manifest):
            with self.assertRaises(OSError):requeue(self.card['design_id'],self.newpath,self.ruling,self.pilot)
        result=requeue(self.card['design_id'],self.newpath,self.ruling,self.pilot)
        self.assertEqual(self.pilot.next_action(read(self.registry/'ACTIVE_DESIGNS.json')['designs'][0]),'purpose-direction')
        self.assertEqual(read(self.registry/'HUB_QUEUE.json')['entries'][0]['status'],'SUPERSEDED_C46')

    def test_resume_with_incomplete_legal_gates_restarts_first_missing(self):
        self.pilot.checkpoint(self.row,'audit').unlink();atomic(self.ruling,dict(self.value,decision='resume'))
        result=requeue(self.card['design_id'],self.newpath,self.ruling,self.pilot)
        self.assertEqual(result['restart_action'],'audit');self.assertEqual(result['retained_actions'],['history','reconcile'])

    def test_park_new_card_no_readiness(self):
        atomic(self.ruling,dict(self.value,decision='park'))
        result=requeue(self.card['design_id'],self.newpath,self.ruling,self.pilot);self.assertEqual(result['restart_action'],'PARK')
        self.assertEqual(self.pilot.next_action(read(self.registry/'ACTIVE_DESIGNS.json')['designs'][0]),'PARK')

    def test_freeze_or_finished_cannot_requeue(self):
        ensure_once(self.pilot.backend.root(self.row)/'FREEZE_PREP.json',dict(fixture=True))
        with self.assertRaisesRegex(ValueError,'Frozen'):requeue(self.card['design_id'],self.newpath,self.ruling,self.pilot)
        self.assertEqual(read(self.registry/'ACTIVE_DESIGNS.json')['designs'],[self.row])

    def test_ruling_or_prior_card_tamper_fails_before_mutation(self):
        atomic(self.ruling,dict(self.value,card_sha256='bad'))
        with self.assertRaisesRegex(ValueError,'card-bound'):requeue(self.card['design_id'],self.newpath,self.ruling,self.pilot)
        self.assertEqual(read(self.registry/'ACTIVE_DESIGNS.json')['designs'],[self.row])

    def test_requires_stop_and_new_version(self):
        self.registry.joinpath('AUTOPILOT_STOP').unlink()
        with self.assertRaisesRegex(ValueError,'stopped scheduler'):requeue(self.card['design_id'],self.newpath,self.ruling,self.pilot)


class CatalogTests(unittest.TestCase):
    def test_v8_additive_and_no_old_bindings_modified(self):
        from scripts.l2_pipeline.connectors import outcome_v8,outcome_v7
        old=outcome_v7.catalog(verify_sources=False);new=outcome_v8.catalog(verify_sources=True)
        self.assertEqual(len(set(new['series'])-set(old['series'])),15)
        for sid,s in old['series'].items():self.assertEqual(dict(s,registered_connector='outcome_v8_'+sid),new['series'][sid])
        self.assertEqual(sha(outcome_v7.CATALOG_PATH),new['inherited_catalog']['sha256'])

    def test_support_is_value_free_and_gaps_are_not_zeros(self):
        from scripts.l2_pipeline.connectors import outcome_v8
        data=outcome_v8.catalog(verify_sources=False)
        for sid,s in data['series'].items():
            if s['adapter']!='c46_artifact':continue
            support=read(acs.REPO/s['support_artifact']['path']);self.assertFalse(support['outcome_values_in_sidecar'])
            self.assertTrue(all(not({'outcome_value','fatality_count','population_denominator'}&m.keys()) for m in support['metadata']))
            if sid.startswith('acs1_'):
                missing=[m for m in support['metadata'] if m['year']==2020];self.assertEqual(len(missing),51);self.assertTrue(all(m['available']==0 for m in missing))
            if 'driver_fatality' in sid:
                missing=[m for m in support['metadata'] if m['year'] in range(1990,2000)];self.assertTrue(all(m['denominator_available']==0 and m['available']==0 for m in missing))
            if sid=='fbi_hate_crime_incident_rate':
                partial=[m for m in support['metadata'] if m['year']==2025]
                self.assertEqual(len(partial),51);self.assertTrue(all(m['annual_usable']==0 and m['partial_year'] for m in partial))
            if sid=='fars_motorcyclist_fatality_rate':
                uncertain=[m for m in support['metadata'] if m.get('source_issue')=='unknown_motor_vehicle_body_type']
                self.assertTrue(uncertain);self.assertTrue(all(m['linkage_certified']==0 for m in uncertain))

    def test_new_adapter_exact_population_hash_and_outcome_only(self):
        from scripts.l2_pipeline.connectors import outcome_v8
        outcome_v8.register();s=outcome_v8.catalog(verify_sources=False)['series']['brfss_current_smoking_pct'];options=dict(connector=s['registered_connector'],series_id=s['series_id'],measure_id=s['series_id'],paths=s['paths'],source_sha256=s['source_sha256'],population_filters=s['population_filters'],frequency='annual',outcome_scale='rate',outcome_unit=s['units'])
        r=ConnectorRequest(options,s['population_filters'],(2011,),('AL',),'annual',acs.REPO)
        self.assertEqual(len(outcome_v8.construct(r).rows),1)
        with self.assertRaises(ConnectorError):outcome_v8.construct(ConnectorRequest(dict(options,party=True),r.filters,r.years,r.units,r.frequency,r.base_dir))
        with self.assertRaises(ConnectorError):outcome_v8.construct(ConnectorRequest(options,dict(age_min=15),r.years,r.units,r.frequency,r.base_dir))

if __name__=='__main__':unittest.main()
