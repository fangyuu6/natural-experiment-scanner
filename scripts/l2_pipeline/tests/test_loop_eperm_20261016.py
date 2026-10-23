"""LOOP_EPERM 2026-10-16: a host EPERM must neither kill the loop nor STOP a design; a dead loop must be flagged."""
import errno,json,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline import autopilot as ap
from scripts.l2_pipeline.autopilot import Autopilot,atomic,read,scheduler_lock
from scripts.l2_pipeline.ops import watchdog as w
from scripts.l2_pipeline.tests.test_autopilot import MockAgents

def eperm(path='x'):return PermissionError(errno.EPERM,'Operation not permitted',path)

class EnvironmentalTests(unittest.TestCase):
    def test_only_host_conditions_are_environmental(self):
        for code in (errno.EPERM,errno.EACCES,errno.ENOSPC,errno.EIO,errno.EROFS):self.assertTrue(ap.environmental(OSError(code,'x')))
        self.assertTrue(ap.environmental(eperm()))
        for exc in (FileNotFoundError(2,'x'),FileExistsError(17,'x'),ValueError('x'),KeyError('x'),RuntimeError('x')):self.assertFalse(ap.environmental(exc))

class TickTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name).resolve();self.registry=self.repo/'data/l2_pipeline/registry'
        self.backend=MockAgents(self.repo);self.pilot=Autopilot(self.registry,self.backend)
        rows=[dict(design_id='d'+str(i),attempt_id='test',work_dir=str(self.repo/'data/l2_pipeline/work'),card='invented',card_sha256='invented',route='V9',managed_execution=True) for i in range(2)]
        atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=rows));self.rows=rows
    def drive(self,limit=60):
        for _ in range(limit):
            self.pilot.tick();self.backend.finish()
            if self.pilot.next_action(self.rows[0])=='STOP':return
    def test_eperm_aborts_the_tick_and_does_not_stop_the_design(self):
        self.backend.fail['d0','S3']=eperm()
        with self.assertRaises(PermissionError):self.drive()
        self.assertFalse(list((self.repo/'data/l2_pipeline/autopilot').glob('d0/*/STOP*.json')))
        self.assertFalse(any(v['design_id']=='d0' for v in read(self.registry/'HUB_QUEUE.json',dict(entries=[]))['entries']))
        self.backend.fail.clear();self.drive(20)  # the condition passed: the design carries on from where it was
        self.assertNotEqual(self.pilot.next_action(self.rows[0]),'STOP')
        self.assertIn(('d0','S3'),self.backend.actions)
    def test_design_errors_still_stop_the_design(self):
        self.backend.fail['d0','S3']=FileNotFoundError(errno.ENOENT,'missing input')
        self.drive()
        self.assertEqual(self.pilot.next_action(self.rows[0]),'STOP')
        self.assertNotEqual(self.pilot.next_action(self.rows[1]),'STOP')

class LoopTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name).resolve();self.registry=self.repo/'data/l2_pipeline/registry';self.registry.mkdir(parents=True)
        self.stop=self.registry/'AUTOPILOT_STOP'
    def run_loop(self):
        argv=['loop','--registry',str(self.registry),'--stop-file',str(self.stop),'--poll-seconds','1']
        make=lambda registry,**kw:Autopilot(registry,MockAgents(self.repo))  # temp repo: nothing touches the real data
        with patch('scripts.l2_pipeline.agents.dispatch.require_host'),patch.object(ap,'Autopilot',make),patch.object(ap,'ENVIRONMENT_RETRY_SECONDS',0),patch('time.sleep'):
            return ap.main(argv)
    def events(self):return [json.loads(l) for l in (self.registry/'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()]
    def test_loop_survives_a_transient_eperm_and_still_stops_cleanly(self):
        calls=[]
        def tick(self_,dry_run=False,**kw):
            calls.append(1)
            if len(calls)==1:raise eperm('STOP.json')
            self.stop.touch();return {}
        with patch.object(Autopilot,'tick',tick):self.assertEqual(self.run_loop(),0)
        self.assertEqual(len(calls),2);self.assertIn('CLEAN_STOP',[e['decision'] for e in self.events()])
        self.assertFalse((self.registry/'LOOP_CRASH.json').exists())
    def test_persistent_eperm_exits_with_a_crash_marker(self):
        calls=[]
        def tick(self_,dry_run=False,**kw):calls.append(1);raise eperm('STOP.json')
        with patch.object(Autopilot,'tick',tick),patch.object(ap,'MAX_ENVIRONMENT_TICKS',3):
            with self.assertRaises(SystemExit) as caught:self.run_loop()
        self.assertEqual(caught.exception.code,1);self.assertEqual(len(calls),3)
        marker=read(self.registry/'LOOP_CRASH.json');self.assertEqual(marker['schema'],'l2_loop_crash_v1');self.assertIn('Operation not permitted',marker['error'])
        self.assertNotIn('CLEAN_STOP',[e['decision'] for e in self.events()])
    def test_other_errors_still_end_the_loop_at_once(self):
        calls=[]
        def tick(self_,dry_run=False,**kw):calls.append(1);raise ValueError('contract violated')
        with patch.object(Autopilot,'tick',tick):
            with self.assertRaises(SystemExit):self.run_loop()
        self.assertEqual(len(calls),1);self.assertIn('contract violated',read(self.registry/'LOOP_CRASH.json')['error'])

class CrashAlertTests(unittest.TestCase):
    T0=datetime(2026,10,16,7,0,tzinfo=timezone.utc)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name).resolve();self.reg=self.repo/'data/l2_pipeline/registry';self.reg.mkdir(parents=True)
        self.write([('CLEAN_STOP',self.T0-timedelta(hours=1)),('LOOP_START',self.T0)])
    def write(self,events):
        (self.reg/'AUTOPILOT_LEDGER.jsonl').write_text(''.join(json.dumps(dict(decision=d,time=t.isoformat()))+'\n' for d,t in events))
    def test_dead_loop_without_clean_stop_is_flagged_once(self):
        now=self.T0+timedelta(hours=3)
        self.assertEqual(w.crashed_loop(self.repo,now)['latest_start'],self.T0.isoformat())
        first=w.crash_alert(self.repo,now);self.assertEqual(first['latest_start'],self.T0.isoformat())
        self.assertEqual(read(self.reg/'LOOP_CRASH_ALERT.json')['schema'],'l2_loop_crash_alert_v1')
        self.assertIsNone(w.crash_alert(self.repo,now))  # same crash: no repeat alert
        self.write([('CLEAN_STOP',self.T0-timedelta(hours=1)),('LOOP_START',self.T0),('LOOP_START',self.T0+timedelta(hours=4))])
        self.assertEqual(w.crash_alert(self.repo,self.T0+timedelta(hours=9))['latest_start'],(self.T0+timedelta(hours=4)).isoformat())
    def test_no_alert_for_clean_stop_young_loop_live_scheduler_or_code_window(self):
        now=self.T0+timedelta(hours=3)
        self.write([('LOOP_START',self.T0),('CLEAN_STOP',self.T0+timedelta(hours=2))]);self.assertIsNone(w.crashed_loop(self.repo,now))
        self.write([('LOOP_START',self.T0)])
        self.assertIsNone(w.crashed_loop(self.repo,self.T0+timedelta(seconds=30)))
        with scheduler_lock(self.reg):self.assertIsNone(w.crashed_loop(self.repo,now))
        (self.reg/'CODE_TASK.lock').write_text('{}');self.assertIsNone(w.crashed_loop(self.repo,now))
    def test_a_crashed_loop_is_still_never_restarted_automatically(self):
        (self.reg/'AUTOPILOT_STOP').touch()
        with self.assertRaisesRegex(ValueError,'no CLEAN_STOP'):w.clean_stop(self.repo)

if __name__=='__main__':unittest.main()


class ForecastModelIdTests(unittest.TestCase):
    def test_dot_and_hyphen_ids_are_the_same_model_and_others_are_not(self):
        from scripts.l2_pipeline.forecast_slots import same_model
        self.assertTrue(same_model('claude-sonnet-5.5','claude-sonnet-5-5'))
        self.assertTrue(same_model('claude-sonnet-5.5','claude-sonnet-5.5'))
        self.assertTrue(same_model('gpt-6.1-sol','gpt-6.1-sol'))
        for registered,reported in [('claude-sonnet-5.5','claude-sonnet-5-4'),('claude-sonnet-5.5','gpt-6.1-sol'),('claude-sonnet-5.5',''),('claude-sonnet-5.5',None),('claude-sonnet-5.5',55),('',''),(None,None)]:
            self.assertFalse(same_model(registered,reported),(registered,reported))
    def test_provenance_accepts_the_worker_spelling_and_still_refuses_another_model(self):
        from types import SimpleNamespace
        from scripts.l2_pipeline.forecast_slots import provenance
        job=SimpleNamespace(contract=dict(forecast_slot=dict(slot=3,kind='x',model_id='claude-sonnet-5.5',pack_sha256='p')),job_sha256='j')
        value=lambda model:dict(author_provenance=dict(worker_id='w',model_id=model))
        self.assertEqual(provenance(job,value('claude-sonnet-5-5'))['model_id'],'claude-sonnet-5-5')
        with self.assertRaisesRegex(ValueError,'differs from registered slot'):provenance(job,value('claude-sonnet-5-4'))

from scripts.l2_pipeline.tests import test_c46
from scripts.l2_pipeline.storage import ensure_once,sha as _sha

class ModelIdRequeueTests(unittest.TestCase):
    """The STOP is filed, then the window step reopens it as a new attempt under the new status."""
    def setUp(self):
        test_c46.RequeueTests.setUp(self)  # fixture only; its own tests are not inherited
        # a second design with a model-id STOP next to the fixture's unrelated 'purpose' STOP
        self.registry.joinpath('CODE_TASK.lock').write_text(json.dumps(dict(task='LOOP_EPERM')))
        self.model_row=dict(self.row,design_id='model-id-fixture',attempt_id='old')
        card=dict(self.card,design_id='model-id-fixture');self.model_card=self.repo/'data/l2_pipeline/cards/model_old.json';atomic(self.model_card,card)
        self.model_row.update(card=str(self.model_card),card_sha256=_sha(self.model_card))
        rows=read(self.registry/'ACTIVE_DESIGNS.json')['designs'];atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=rows+[self.model_row]))
        self.pilot.stop(self.model_row,'forecasts','ValueError: Forecast model differs from registered slot')
        for a in ['history','reconcile','audit']:ensure_once(self.pilot.checkpoint(self.model_row,a),dict(action=a))
        root=self.pilot.backend.root(self.model_row)
        for n in ['RECONCILIATION.json','CODING_LOCK.json','AUDIT_SELECTION.json']:ensure_once(root/n,dict(fixture=n))
        auth=self.repo/'docs/plans/l2-pipeline/INTAKE_AND_PARK_MAP_20261016.md';auth.parent.mkdir(parents=True);auth.write_text('fixture authority')
    def test_only_the_exact_model_id_stop_is_reopened_with_the_new_status(self):
        from scripts.l2_pipeline.maintenance import requeue_model_id_stops
        done=requeue_model_id_stops(self.pilot)
        self.assertEqual([d['design_id'] for d in done],['model-id-fixture'])
        self.assertEqual(done[0]['restart_action'],'purpose-direction');self.assertEqual(done[0]['retained_actions'],['history','reconcile','audit'])
        statuses={e['design_id']:e['status'] for e in read(self.registry/'HUB_QUEUE.json')['entries']}
        self.assertEqual(statuses['model-id-fixture'],'SUPERSEDED_FORECAST_MODEL_ID')
        other=[e for e in read(self.registry/'HUB_QUEUE.json')['entries'] if e['design_id']=='c46-fixture'][0]
        self.assertFalse(other.get('status','').startswith('SUPERSEDED_'))  # the 'purpose' STOP of the other fixture is untouched
        rows={r['design_id']:r for r in read(self.registry/'ACTIVE_DESIGNS.json')['designs']}
        self.assertEqual(rows['model-id-fixture']['attempt_id'],'requeue_v2');self.assertEqual(rows['c46-fixture']['attempt_id'],'old')
        self.assertEqual(requeue_model_id_stops(self.pilot),[])  # idempotent
    def test_needs_the_named_stopped_window(self):
        from scripts.l2_pipeline.maintenance import requeue_model_id_stops
        self.registry.joinpath('CODE_TASK.lock').write_text(json.dumps(dict(task='OTHER')))
        with self.assertRaisesRegex(ValueError,'stopped code window'):requeue_model_id_stops(self.pilot)
