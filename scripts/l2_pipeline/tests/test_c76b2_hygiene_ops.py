"""C79 and C77/C80 prospective metadata contracts; no real estimates."""
import copy,json,tempfile,unittest,threading,time,fcntl
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts.l2_pipeline.hygiene import plan,treated_mass
from scripts.l2_pipeline.coverage import normalize_series_breaks
from scripts.l2_pipeline.ops import watchdog,ingest_gate
from scripts.l2_pipeline.storage import ensure_once

class HygieneTests(unittest.TestCase):
    def fixture(self):
        card=json.loads((Path(__file__).parents[1]/'examples/design.json').read_text())
        card.update(analysis=dict(controls='never_treated',weights='equal'),treatment={},sensitivities=[dict(name='same',changes={}),dict(name='different',changes={'analysis':{'weights':'other'}})])
        cells=[dict(g=2000,t=2001,post=True,treated=['A','B','C','D'],controls=['X'],base_period=1999)]
        support=dict(variants=[dict(variant=n,passed=True,cell_inventory=cells) for n in ('primary','same','different')])
        return card,support
    def test_same_cells_and_operator_only_dedupes_and_caps_omit(self):
        card,support=self.fixture();before=copy.deepcopy((card,support));notes,entries=plan(card,support)
        self.assertEqual(notes['dropped'],[dict(name='same',reason='identical to primary')])
        self.assertEqual(notes['added'],['omit_A','omit_B','omit_C'])
        self.assertEqual([s['name'] for s in entries],['different','omit_A','omit_B','omit_C'])
        self.assertTrue(all(e['report_only'] and not e['gates_primary'] for e in entries[1:]))
        self.assertEqual((card,support),before)
    def test_mass_is_treated_post_only_threshold_inclusive(self):
        rows=dict(cell_inventory=[dict(post=True,treated=['A']+list('BCDEFGHIJ')),dict(post=False,treated=['Z']*100)])
        self.assertEqual(treated_mass(rows)['A'],.1);self.assertNotIn('Z',treated_mass(rows))
        card,support=self.fixture();support['variants'][0]=dict(variant='primary',**rows)
        self.assertEqual(len(plan(card,support)[0]['added']),3)
    def test_cohort_event_time_and_measurement_are_operator_dimensions(self):
        card,support=self.fixture()
        for field,value in [('g',1999),('event_time',5)]:
            changed=copy.deepcopy(support);changed['variants'][1]['cell_inventory']=copy.deepcopy(changed['variants'][1]['cell_inventory'])
            changed['variants'][1]['cell_inventory'][0][field]=value
            self.assertEqual(plan(card,changed)[0]['dropped'],[])
        card['sensitivities'][0]['changes']={'outcome':{'measure_id':'different'}}
        self.assertEqual(plan(card,support)[0]['dropped'],[])
    def test_full_year_omissions_retain_primary_panel_binding(self):
        from scripts.l2_pipeline.tests.test_v4_annual_sensitivities import fixture
        from scripts.l2_pipeline.tests.test_pipeline import card as make_card
        from scripts.l2_pipeline.full_year import construct_full_year
        from scripts.l2_pipeline.hygiene import prepare_support
        from scripts.l2_pipeline.storage import sha
        import csv
        declaration,metadata=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'panels/primary';directory.mkdir(parents=True)
            source=root/'source.csv'
            with source.open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(metadata[0]));writer.writeheader();writer.writerows(metadata)
            (directory/'metadata.csv').write_bytes(source.read_bytes())
            (directory/'panel.csv').write_text('unit,year,outcome_value\nA,2000,0\n')
            panel=dict(variant='primary',panel_path=str(directory/'panel.csv'),metadata_path=str(directory/'metadata.csv'),panel_sha256=sha(directory/'panel.csv'),metadata_sha256=sha(directory/'metadata.csv'))
            ensure_once(root/'PANELS.json',dict(variants=[panel]))
            summary,_=construct_full_year(declaration,'primary',metadata,{})
            support=dict(primary_passed=True,variants=[dict(summary,variant='primary',passed=True,**{k:v for k,v in panel.items() if k!='variant'})])
            card=make_card();card['analysis']=dict(support_adapter='annual_full_year_v1',full_year_variant='primary',support_metadata_path='source.csv',min_post_periods=2)
            card['treatment']=dict(full_year_declaration=declaration);card['outcome']['years']=declaration['window']
            notes,entries=plan(card,support);self.assertEqual(len(notes['added']),3)
            prepared,_=prepare_support(card,support,notes,entries,dict(records=[],source_checks={}),root,root)
            for row in prepared['variants'][1:]:
                self.assertEqual(row['panel_sha256'],panel['panel_sha256']);self.assertEqual(row['metadata_sha256'],panel['metadata_sha256'])
                self.assertIn('state',row);self.assertIs(row['gates_primary'],False)
    def test_unknown_geometry_does_not_drop(self):
        card,support=self.fixture()
        for r in support['variants']:r['cell_inventory']=[]
        self.assertEqual(plan(card,support)[0]['dropped'],[])
    def test_control_geometry_difference_is_not_duplicate(self):
        card,support=self.fixture();support=copy.deepcopy(support)
        support['variants'][1]['cell_inventory']=copy.deepcopy(support['variants'][1]['cell_inventory'])
        support['variants'][1]['cell_inventory'][0]['controls']=['Y']
        self.assertEqual(plan(card,support)[0]['dropped'],[])

class Followups(unittest.TestCase):
    def test_yearless_notes_preserved_non_window(self):
        self.assertEqual(normalize_series_breaks(['Changing questionnaire']),[dict(note='Changing questionnaire',definition_change=False,window_edge=False,year_unknown=True)])
        with self.assertRaises(ValueError):normalize_series_breaks(['Changed in 2011'])
    def test_any_estimated_canary_opens_and_documented_park_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp).resolve();reg=repo/'data/l2_pipeline/registry'
            pilot=SimpleNamespace(repo=repo,registry=reg,case_dir=lambda r:repo/r['design_id'])
            ensure_once(reg/'INGEST_GATE.json',dict(canary_designs=['park','estimated','working']))
            rows=[dict(design_id=n,attempt_id='a',card_sha256='h') for n in ['park','estimated','working']]
            ensure_once(reg/'ACTIVE_DESIGNS.json',dict(designs=rows))
            ensure_once(repo/'park/PARKED.json',dict(design_id='park',attempt_id='a',status='PARKED',reason='support',action='stages'))
            card=ensure_once(repo/'card.json',dict(design_id='new'))
            with patch.object(ingest_gate,'estimated_runs',return_value=[dict(design_id='estimated')]):
                value=ingest_gate.check(pilot,[card])
            self.assertEqual(value['phase'],'BATCH_OPEN');self.assertEqual(value['canary_designs'],['estimated','working'])
    def test_already_stopped_helper_preserves_idle_exemption(self):
        from scripts.l2_pipeline.tests.test_c70_ops import IdleWindowTests
        fixture=IdleWindowTests();fixture.setUp()
        try:
            before=(fixture.reg/'AUTOPILOT_STOP').stat().st_mtime_ns
            value=watchdog.stop_and_begin_code_task('C76B2',fixture.root,timeout=2)
            self.assertTrue(value['idle']);self.assertEqual((fixture.reg/'AUTOPILOT_STOP').stat().st_mtime_ns,before)
        finally:fixture.doCleanups()
    def test_stop_begin_holds_ops_lock_through_clean_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);reg=repo/'data/l2_pipeline/registry';reg.mkdir(parents=True)
            (reg/'AUTOPILOT_LEDGER.jsonl').write_text('')
            observed=[]
            def stop():
                while not (reg/'AUTOPILOT_STOP').exists():time.sleep(.001)
                with (reg/'OPS.lock').open('a') as f:
                    try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError:observed.append('blocked')
                with (reg/'AUTOPILOT.lock').open('a') as scheduler:
                    fcntl.flock(scheduler,fcntl.LOCK_EX)
                    (reg/'AUTOPILOT_LEDGER.jsonl').write_text(json.dumps(dict(time='2026-10-12T10:00:00+00:00',decision='CLEAN_STOP'))+'\n')
                    time.sleep(.04)
            thread=threading.Thread(target=stop);thread.start()
            value=watchdog.stop_and_begin_code_task('C76B2',repo,timeout=2,poll_seconds=.01);thread.join()
            self.assertEqual(observed,['blocked']);self.assertEqual(value['task'],'C76B2')
            self.assertTrue((reg/'CODE_TASK.lock').exists())

if __name__=='__main__':unittest.main()
