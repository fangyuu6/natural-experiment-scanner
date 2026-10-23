"""C70 operating rules on invented temporary registries; no dispatch or fits."""
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.l2_pipeline.autopilot import atomic, read
from scripts.l2_pipeline.ops import ingest_gate, watchdog
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests.test_autopilot import AutopilotTests as PilotFixture


class IdleWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.reg=self.root/'data/l2_pipeline/registry'
        self.reg.mkdir(parents=True)
        self.now=datetime(2026,10,12,15,tzinfo=timezone.utc)
        self.tick=self.now-timedelta(seconds=10)
        self.stop=dict(time=self.now.isoformat(),decision='CLEAN_STOP')
        self.events=[dict(time=self.tick.isoformat(),decision='IDLE',blockers={'PARK':1,'STOP':1}),self.stop]
        self.write_ledger()
        marker=self.reg/'AUTOPILOT_STOP';marker.touch()
        stamp=(self.now-timedelta(seconds=5)).timestamp();os.utime(marker,(stamp,stamp))
        atomic(self.reg/'ACTIVE_DESIGNS.json',dict(designs=[dict(design_id='a'),dict(design_id='b')]))
        self.state=dict(schema='l2_loop_state_v1',time=self.tick.isoformat(),next_counts={'PARK':1,'STOP':1},
                        queued_jobs=0,active_designs_sha256=sha(self.reg/'ACTIVE_DESIGNS.json'))
        atomic(self.reg/'LOOP_STATE.json',self.state)
        atomic(self.reg/'MAINTENANCE_WINDOWS.json',dict(windows=[dict(task=str(i),day='2026-10-12') for i in range(4)]))

    def write_ledger(self):
        (self.reg/'AUTOPILOT_LEDGER.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in self.events))

    def test_idle_window_exempt_at_limit_and_recorded_with_proof(self):
        value=watchdog.begin_code_task('C70',self.root,self.now)
        self.assertIs(value['idle'],True);self.assertNotIn('override_consumed',value)
        self.assertEqual(value['idle_proof']['next_counts'],{'PARK':1,'STOP':1})
        self.assertEqual(read(self.reg/'MAINTENANCE_WINDOWS.json')['windows'][-1],value)

    def test_stale_idle_ledger_cannot_exempt_latest_busy_tick(self):
        for counts,queued in [({'PARK':1,'draft':1},0),({'PARK':1,'STOP':1},1),({'FINISHED':1,'draft':1},0),({'PRESCREEN_PARK':1,'execute':1},0)]:
            with self.subTest(counts=counts,queued=queued):
                atomic(self.reg/'LOOP_STATE.json',dict(self.state,next_counts=counts,queued_jobs=queued))
                with self.assertRaisesRegex(ValueError,'limit'):watchdog.begin_code_task('C70',self.root,self.now)

    def test_finished_and_prescreen_park_are_non_runnable_like_park_and_stop(self):
        # C78: the live registry was FINISHED 7 / PARK 73 / PRESCREEN_PARK 1 / STOP 1 and was not treated as idle.
        self.assertEqual(watchdog.NON_RUNNABLE,{'PARK','STOP','FINISHED','PRESCREEN_PARK','GATE0_PARK'})
        for counts in ({'FINISHED':2},{'FINISHED':1,'PRESCREEN_PARK':1},{'PARK':1,'PRESCREEN_PARK':1},
                       {'FINISHED':7,'PARK':73,'PRESCREEN_PARK':1,'STOP':1}):
            with self.subTest(counts=counts):
                n=sum(counts.values())
                atomic(self.reg/'ACTIVE_DESIGNS.json',dict(designs=[dict(design_id=str(i)) for i in range(n)]))
                self.events[0]=dict(self.events[0],blockers=counts);self.write_ledger()
                atomic(self.reg/'LOOP_STATE.json',dict(self.state,next_counts=counts,active_designs_sha256=sha(self.reg/'ACTIVE_DESIGNS.json')))
                self.assertEqual(watchdog.idle_proof(self.reg,self.stop)['next_counts'],counts)
        value=watchdog.begin_code_task('C78',self.root,self.now)  # four windows already used today
        self.assertIs(value['idle'],True);self.assertNotIn('override_consumed',value)
        self.assertEqual(value['idle_proof']['next_counts'],counts)

    def test_idle_requires_pre_stop_fresh_active_bound_tick_and_note(self):
        for change in [dict(time=self.now.isoformat()),dict(time=(self.now-timedelta(minutes=11)).isoformat()),
                       dict(active_designs_sha256='changed'),dict(next_counts={'PARK':1}),dict(queued_jobs=-1)]:
            with self.subTest(change=change):
                atomic(self.reg/'LOOP_STATE.json',dict(self.state,**change))
                self.assertIsNone(watchdog.idle_proof(self.reg,self.stop))
        atomic(self.reg/'LOOP_STATE.json',self.state)
        self.events=[self.stop];self.write_ledger()
        self.assertIsNone(watchdog.idle_proof(self.reg,self.stop))

    def test_changed_registry_or_new_loop_rejects_idle_proof(self):
        original=(self.reg/'ACTIVE_DESIGNS.json').read_bytes()
        atomic(self.reg/'ACTIVE_DESIGNS.json',dict(designs=[]))
        self.assertIsNone(watchdog.idle_proof(self.reg,self.stop))
        (self.reg/'ACTIVE_DESIGNS.json').write_bytes(original)
        self.events.insert(1,dict(decision='LOOP_START',time=(self.tick+timedelta(seconds=1)).isoformat()))
        self.write_ledger();self.assertIsNone(watchdog.idle_proof(self.reg,self.stop))

    def test_idle_history_receipts_do_not_reconsume_quota(self):
        atomic(self.reg/'MAINTENANCE_WINDOWS.json',dict(windows=[dict(task='C'+str(i),day='2026-10-12',idle=True) for i in range(6)]))
        for i in range(6):
            ensure_once(self.root/('data/l2_pipeline/autopilot/c'+str(i)+'/RESTART_RECEIPT.json'),
                        dict(time=(self.now-timedelta(hours=1)).isoformat()))
        (self.reg/'LOOP_STATE.json').unlink()
        value=watchdog.begin_code_task('busy',self.root,self.now)
        self.assertIs(value['idle'],False)


class TickStateTests(unittest.TestCase):
    setUp=PilotFixture.setUp
    seed=PilotFixture.seed

    def test_tick_refreshes_idle_and_busy_state_and_dry_run_preserves_it(self):
        self.seed(1);row=read(self.registry/'ACTIVE_DESIGNS.json')['designs'][0]
        park=ensure_once(self.pilot.case_dir(row)/'PARKED.json',dict(reason='invented'))
        self.pilot.tick();path=self.registry/'LOOP_STATE.json';state=read(path)
        self.assertEqual(state['next_counts'],{'PARK':1});self.assertEqual(state['queued_jobs'],0)
        self.assertEqual(state['active_designs_sha256'],sha(self.registry/'ACTIVE_DESIGNS.json'))
        before=path.read_bytes();self.pilot.tick(dry_run=True);self.assertEqual(path.read_bytes(),before)
        self.pilot.tick();self.assertNotEqual(read(path)['time'],state['time'])
        park.unlink();self.pilot.tick();state=read(path)
        self.assertGreater(state['queued_jobs'],0);self.assertNotEqual(state['next_counts'],{'PARK':1})


class ParallelTests(unittest.TestCase):
    def run_check(self,fail=False,change=False):
        from scripts.l2_pipeline import registration
        barrier=threading.Barrier(2);commands=[]
        def run(command,**kwargs):
            commands.append(command);barrier.wait(timeout=5)
            return SimpleNamespace(returncode=int(fail and '-O' in command),stdout='invented checks',stderr='')
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/'TEST_REPORT.json'
            inventories=[{'a':'original'},{'a':'changed' if change else 'original'},{'a':'original'}]
            with patch.object(registration.subprocess,'run',side_effect=run),patch.object(registration,'code_inventory',side_effect=inventories):
                if fail or change:
                    with self.assertRaisesRegex(ValueError,'Code changed' if change else 'Regression checks failed'):
                        registration.check_tests(output,no_archival_fits=True)
                    self.assertFalse(output.exists())
                else:
                    registration.check_tests(output,no_archival_fits=True);value=read(output)
                    self.assertEqual(value['schema'],'l2_pipeline_test_evidence_v1')
                    self.assertTrue(value['passed']);self.assertEqual(len(value['runs']),2)
                    self.assertEqual([('-O' in r['command']) for r in value['runs']],[False,True])
            self.assertEqual(len(commands),2)
            self.assertTrue(all('--no-archival-fits' in cmd for cmd in commands))

    def test_both_modes_run_concurrently_same_schema(self):self.run_check()
    def test_one_failed_mode_prevents_report(self):self.run_check(fail=True)
    def test_code_change_prevents_report(self):self.run_check(change=True)


class EarlyCanaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.reg=self.root/'data/l2_pipeline/registry'
        self.pilot=SimpleNamespace(repo=self.root,registry=self.reg)
        self.marker=ensure_once(self.reg/'INGEST_GATE.json',dict(canary_designs=[]))
        self.start=datetime(2026,10,12,3,4,tzinfo=timezone.utc)
        self.receipt=ensure_once(self.root/'data/l2_pipeline/autopilot/c64/RESTART_RECEIPT.json',
                                dict(status='STARTED',time=self.start.isoformat()))
        self.paths=[ensure_once(self.root/(str(i)+'.json'),dict(design_id=str(i))) for i in range(3)]

    def test_boundary_records_owner_and_restart_hash(self):
        with self.assertRaisesRegex(ValueError,'INGEST_PAUSED'):
            ingest_gate.check(self.pilot,self.paths[:2],now=self.start+timedelta(hours=6,microseconds=-1))
        value=ingest_gate.check(self.pilot,self.paths[:2],now=self.start+timedelta(hours=6))
        self.assertEqual(value['phase'],'EARLY_CANARIES');self.assertEqual(value['canary_designs'],['0','1'])
        self.assertEqual(value['early_canary']['owner_decision_path'],ingest_gate.OWNER_DECISION)
        self.assertEqual(value['early_canary']['restart_receipt_sha256'],sha(self.receipt))

    def test_latest_successful_restart_controls_clock_not_launch_intent(self):
        later=ensure_once(self.receipt.parent.parent/'c70/RESTART_RECEIPT.json',
                          dict(status='STARTED',time=(self.start+timedelta(hours=2)).isoformat()))
        with self.assertRaisesRegex(ValueError,'INGEST_PAUSED'):
            ingest_gate.check(self.pilot,self.paths[:1],now=self.start+timedelta(hours=6))
        atomic(later,dict(status='LAUNCH_INTENT',time=(self.start+timedelta(hours=2)).isoformat()))
        self.assertEqual(ingest_gate.check(self.pilot,self.paths[:1],now=self.start+timedelta(hours=6))['phase'],'EARLY_CANARIES')

    def test_limit_duplicates_and_no_second_early_admission(self):
        now=self.start+timedelta(hours=7)
        for paths in [[],self.paths,[self.paths[0],self.paths[0]]]:
            with self.assertRaisesRegex(ValueError,'1-2 distinct'):ingest_gate.check(self.pilot,paths,now=now)
        value=ingest_gate.check(self.pilot,self.paths[:1],now=now);atomic(self.marker,value)
        with self.assertRaisesRegex(ValueError,'INGEST_PAUSED'):ingest_gate.check(self.pilot,self.paths[1:2],now=now)

    def test_full_batch_waits_for_estimate_and_existing_canaries(self):
        now=self.start+timedelta(hours=7)
        atomic(self.marker,ingest_gate.check(self.pilot,self.paths[:2],now=now))
        with self.assertRaisesRegex(ValueError,'INGEST_PAUSED'):ingest_gate.check(self.pilot,self.paths[2:],now=now)
        with patch.object(ingest_gate,'estimated_runs',return_value=[dict(design_id='unrelated')]):
            with self.assertRaisesRegex(ValueError,'canaries'):ingest_gate.check(self.pilot,self.paths[2:],now=now)
        with patch.object(ingest_gate,'estimated_runs',return_value=[dict(design_id='0'),dict(design_id='1')]):
            self.assertEqual(ingest_gate.check(self.pilot,self.paths[2:],now=now)['phase'],'BATCH_OPEN')

    def test_missing_or_invalid_restart_does_not_unlock(self):
        for value in [dict(status='LAUNCH_INTENT',time=self.start.isoformat()),dict(status='STARTED',time='bad'),
                      dict(status='STARTED',time='2026-10-12T03:04:00')]:
            atomic(self.receipt,value)
            with self.assertRaisesRegex(ValueError,'INGEST_PAUSED'):
                ingest_gate.check(self.pilot,self.paths[:1],now=self.start+timedelta(hours=7))
        self.receipt.unlink()
        with self.assertRaisesRegex(ValueError,'INGEST_PAUSED'):
            ingest_gate.check(self.pilot,self.paths[:1],now=self.start+timedelta(hours=7))


del PilotFixture
