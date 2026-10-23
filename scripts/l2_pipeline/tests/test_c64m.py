"""C64M owner window and C67 durable scheduler contracts (invented inputs)."""
import json
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from scripts.l2_pipeline.tests.test_c63 import RestartTests as BaseRestart
from scripts.l2_pipeline.tests.test_c54 import C54Tests as BaseClosure
from scripts.l2_pipeline.tests.test_autopilot import AutopilotTests as BasePilot
from scripts.l2_pipeline.autopilot import Autopilot, Backend, atomic, read
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.population_resolution import bind
from scripts.l2_pipeline.registration import finding_sha256
from scripts.l2_pipeline.c54 import apply_open_queue, valid_hub_closure
from scripts.l2_pipeline.ops import watchdog as w

class WindowTests(unittest.TestCase):
    setUp = BaseRestart.setUp
    def test_additional_override_matches_day_task_and_is_consumed_once(self):
        now=datetime(2026,10,12,2,tzinfo=timezone.utc)
        atomic(self.reg/'MAINTENANCE_WINDOWS.json',dict(windows=[dict(day='2026-10-11',task=str(i)) for i in range(4)]))
        extra=dict(day='2026-10-11',task='C72',extra_windows=1)
        override=dict(schema='maintenance_window_override_v1',day='2026-10-11',task='C64M',extra_windows=1,additional=[extra])
        atomic(self.reg/'MAINTENANCE_WINDOW_OVERRIDE.json',override)
        with self.assertRaisesRegex(ValueError,'limit'):w.begin_code_task('other',self.root,now)
        extra['day']='2026-10-10';atomic(self.reg/'MAINTENANCE_WINDOW_OVERRIDE.json',override)
        with self.assertRaisesRegex(ValueError,'limit'):w.begin_code_task('C72',self.root,now)
        extra['day']='2026-10-11';atomic(self.reg/'MAINTENANCE_WINDOW_OVERRIDE.json',override)
        lock=w.begin_code_task('C72',self.root,now)
        self.assertTrue(lock['override_consumed'])
        self.assertEqual(lock['override']['task'],'C72')
        with patch('scripts.l2_pipeline.registration._check_evidence',return_value=self.evidence):w.end_code_task(lock['token'],self.root)
        with self.assertRaisesRegex(ValueError,'limit'):w.begin_code_task('C72',self.root,now)
        self.assertEqual(len(read(self.reg/'MAINTENANCE_WINDOWS.json')['windows']),5)

    def test_override_named_task_la_day_and_one_use(self):
        now=datetime(2026,10,12,2,tzinfo=timezone.utc)
        atomic(self.reg/'MAINTENANCE_WINDOWS.json',dict(windows=[dict(day='2026-10-11',task=str(i)) for i in range(4)]))
        override=dict(schema='maintenance_window_override_v1',day='2026-10-11',task='C64M',extra_windows=1)
        atomic(self.reg/'MAINTENANCE_WINDOW_OVERRIDE.json',override)
        with self.assertRaisesRegex(ValueError,'limit'):w.begin_code_task('other',self.root,now)
        override['day']='2026-10-10';atomic(self.reg/'MAINTENANCE_WINDOW_OVERRIDE.json',override)
        with self.assertRaisesRegex(ValueError,'limit'):w.begin_code_task('C64M',self.root,now)
        override['day']='2026-10-11';override['schema']='wrong';atomic(self.reg/'MAINTENANCE_WINDOW_OVERRIDE.json',override)
        with self.assertRaisesRegex(ValueError,'limit'):w.begin_code_task('C64M',self.root,now)
        override['schema']='maintenance_window_override_v1';atomic(self.reg/'MAINTENANCE_WINDOW_OVERRIDE.json',override)
        lock=w.begin_code_task('C64M',self.root,now)
        self.assertTrue(lock['override_consumed'])
        with patch('scripts.l2_pipeline.registration._check_evidence',return_value=self.evidence):w.end_code_task(lock['token'],self.root)
        with self.assertRaisesRegex(ValueError,'limit'):w.begin_code_task('C64M',self.root,now)
        self.assertEqual(len(read(self.reg/'MAINTENANCE_WINDOWS.json')['windows']),5)

class ClosureTests(unittest.TestCase):
    setUp = BaseClosure.setUp
    ledger = BaseClosure.ledger
    ctx = BaseClosure.ctx
    review_fixture = BaseClosure.review_fixture
    def closure(self):
        ctx,attempt,job,path=self.review_fixture('Unlisted SHOULD requiring a hub judgment')
        evidence=ensure_once(self.root/'evidence/EVIDENCE.json',dict(invented=True))
        value=dict(schema='l2_draft_resolution_v1',design_id=self.row['design_id'],review_attempt=attempt,
            review_sha256=sha(path),source_spec=bind(self.root/'SPEC.json'),target_spec_sha256=sha(self.root/'SPEC.json'),
            target_unchanged=True,hub_signer='invented hub',execution_authorized=False,outcome_associations_read=False,
            resolutions=[dict(bucket='should',id=f['id'],finding_sha256=finding_sha256(f),status='declined',
                reason='Evidence-backed annotation',evidence=[bind(evidence)]) for f in read(path)['should']])
        closure=ensure_once(self.root/('DRAFT_RESOLUTION_'+attempt+'.json'),value)
        atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=[self.row]))
        self.p.stop(self.row,'draft','review SHOULD requires evidence-backed hub closure')
        return ctx,attempt,job,path,closure,evidence

    def test_two_writers_five_ticks_restart_changed_findings_and_resume(self):
        ctx,attempt,job,path=self.review_fixture('Unlisted SHOULD')
        atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=[self.row]))
        reason='review SHOULD requires evidence-backed hub closure'
        self.p.stop(self.row,'draft',reason)
        for _ in range(5):
            apply_open_queue(self.p,scheduler_owned=True);self.p.stop(self.row,'draft',reason)
        self.assertEqual(len(self.ledger()),2)
        self.p=Autopilot(self.registry,Backend(self.repo));self.p.backend.ctx=lambda row:ctx;self.p.backend.root=lambda row:self.root
        apply_open_queue(self.p,scheduler_owned=True);self.p.stop(self.row,'draft',reason)
        self.assertEqual(len(self.ledger()),2)
        review=read(path);review['should'][0]['description']='Changed finding';atomic(path,review)
        apply_open_queue(self.p,scheduler_owned=True);self.assertEqual(len(self.ledger()),3)
        self.p.ledger(self.row,'resume','RESUME','record')
        apply_open_queue(self.p,scheduler_owned=True);self.assertEqual(len(self.ledger()),5)

    def test_hub_closure_both_paths_idempotent_no_redispatch(self):
        ctx,attempt,job,path,closure,evidence=self.closure();original=closure.read_bytes()
        self.p.next_action=lambda row:'prep'
        self.assertTrue(valid_hub_closure(self.p,self.row,job))
        apply_open_queue(self.p,scheduler_owned=True)
        self.assertIsNone(self.p.stop_record(self.row));self.assertTrue(self.p.checkpoint(self.row,'draft').exists())
        before=self.ledger();apply_open_queue(self.p,scheduler_owned=True)
        from scripts.l2_pipeline.maintenance import resume_should
        self.assertFalse(resume_should(self.p,self.row));self.assertEqual(self.ledger(),before)
        self.assertEqual(closure.read_bytes(),original)
        self.assertEqual(read(self.registry/'HUB_QUEUE.json')['entries'][0]['status'],'SUPERSEDED_C54')

    def test_maintenance_path_honors_closure_once(self):
        ctx,attempt,job,path,closure,evidence=self.closure()
        from scripts.l2_pipeline.maintenance import resume_should
        self.assertTrue(resume_should(self.p,self.row));self.assertFalse(resume_should(self.p,self.row))
        self.assertTrue(self.p.checkpoint(self.row,'draft').exists())

    def test_invalid_closures_fail_closed(self):
        ctx,attempt,job,path,closure,evidence=self.closure();original=closure.read_bytes()
        value=read(closure);value['resolutions']=[];atomic(closure,value)
        self.assertFalse(valid_hub_closure(self.p,self.row,job))
        apply_open_queue(self.p,scheduler_owned=True);self.assertIsNotNone(self.p.stop_record(self.row))
        closure.write_bytes(original);evidence.write_text('{}')
        self.assertFalse(valid_hub_closure(self.p,self.row,job))
        ensure_once(self.root/'FREEZE_PREP.json',dict(frozen=True))
        self.assertFalse(valid_hub_closure(self.p,self.row,job))
        self.assertEqual(sum(r['decision']=='SHOULD_OPEN_C54' for r in self.ledger()),1)

    def test_spec_changed_after_closure_returns_false(self):
        ctx,attempt,job,path,closure,evidence=self.closure()
        (self.root/'SPEC.json').write_text('{}')
        self.assertFalse(valid_hub_closure(self.p,self.row,job))

class ObservationTests(unittest.TestCase):
    setUp = BasePilot.setUp
    seed = BasePilot.seed
    def rows(self):
        return [json.loads(s) for s in (self.registry/'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()]
    def test_offline_park_and_idle_deduped_across_restart(self):
        self.seed(1);row=read(self.registry/'ACTIVE_DESIGNS.json')['designs'][0]
        path=ensure_once(self.pilot.case_dir(row)/'PARKED.json',dict(reason='offline park'))
        for _ in range(3):self.pilot.tick()
        self.pilot=Autopilot(self.registry,self.backend);self.pilot.tick()
        self.assertEqual(sum(r['decision']=='OBSERVED_PARK' for r in self.rows()),1)
        idle=[r for r in self.rows() if r['decision']=='IDLE'];self.assertEqual(len(idle),1);self.assertEqual(idle[0]['blockers'],{'PARK':1})
        before=len(self.rows());self.pilot.tick(dry_run=True);self.assertEqual(len(self.rows()),before)
        atomic(path,dict(reason='new park'));self.pilot.tick()
        self.assertEqual(sum(r['decision']=='OBSERVED_PARK' for r in self.rows()),2)
        self.pilot.stop(row,'draft','unlisted STOP');self.pilot.tick()
        self.assertEqual([r for r in self.rows() if r['decision']=='IDLE'][-1]['blockers'],{'STOP':1})
    def test_pending_job_has_no_idle_row(self):
        self.seed(1);self.pilot.tick()
        self.assertTrue(self.pilot.pending);self.assertFalse(any(r['decision']=='IDLE' for r in self.rows()))

del BaseRestart, BaseClosure, BasePilot
