"""Synthetic C76 queue transition and canary contracts."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts.l2_pipeline.agents.sonnet_queue import write_status,atomic
from scripts.l2_pipeline.autopilot import Autopilot
from scripts.l2_pipeline.ops.ingest_gate import check
from scripts.l2_pipeline.storage import ensure_once,sha

class QueueStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Path(self.tmp.name).resolve();self.registry=self.repo/'data/l2_pipeline/registry'
        self.root=self.registry/'SONNET_QUEUE';self.pilot=self.make_pilot()
    def make_pilot(self):return Autopilot(self.registry,backend=SimpleNamespace(repo=self.repo))
    def enqueue(self,name='a',at=100):return ensure_once(self.root/'pending'/name/'STATE.json',dict(enqueued_at=at)).parent
    def events(self):
        path=self.registry/'AUTOPILOT_LEDGER.jsonl'
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    def test_no_ledger_write_without_state_change_and_restart(self):
        self.enqueue()
        self.assertFalse(write_status(self.pilot,699)['starved']);self.assertEqual(self.events(),[])
        self.assertTrue(write_status(self.pilot,700)['starved'])
        path=self.registry/'AUTOPILOT_LEDGER.jsonl';before=path.read_bytes()
        for now in (701,900,1300,2000):write_status(self.make_pilot(),now)
        self.assertEqual(path.read_bytes(),before);self.assertEqual(len(self.events()),1)
        self.enqueue('b',1500);write_status(self.make_pilot(),2100);self.assertEqual(path.read_bytes(),before)
    def test_claim_recovers_and_next_starvation_waits_ten_minutes(self):
        packet=self.enqueue();write_status(self.pilot,700)
        atomic(packet/'LEASE.json',dict(claimed_at=710));packet.rename(self.root/'in_progress'/'a')
        self.enqueue('b',711)
        self.assertFalse(write_status(self.pilot,711)['starved'])
        self.assertFalse(write_status(self.make_pilot(),1310)['starved'])
        self.assertTrue(write_status(self.make_pilot(),1311)['starved'])
        self.assertEqual([e['starved'] for e in self.events()],[True,False,True])
        status=json.loads((self.root/'STATUS.json').read_text())
        self.assertEqual((status['pending'],status['in_progress'],status['last_claim_at']),(1,1,710))
        self.assertEqual(status['oldest_wait_seconds'],600)
    def test_done_and_expired_leases_survive_restart(self):
        self.enqueue(at=100)
        ensure_once(self.root/'done'/'old'/'LEASE.json',dict(claimed_at=500))
        ensure_once(self.root/'hub'/'expired'/'EXPIRED_1.json',dict(lease=dict(claimed_at=600)))
        status=write_status(self.make_pilot(),1199)
        self.assertFalse(status['starved']);self.assertEqual(status['last_claim_at'],600)
        self.assertTrue(write_status(self.make_pilot(),1200)['starved'])
    def test_healthy_empty_queue_does_not_make_ledger(self):
        status=write_status(self.pilot,100)
        self.assertFalse(status['starved']);self.assertIsNone(status['last_claim_at']);self.assertEqual(self.events(),[])
    def test_schedule_writes_status_after_enqueue_phase(self):
        self.pilot.backend.global_running=lambda:0;self.pilot.queue_reconciliations=lambda:None
        with patch('scripts.l2_pipeline.agents.sonnet_queue.write_status',return_value={}) as status:self.pilot.schedule()
        status.assert_called_once_with(self.pilot)

class CanaryExitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Path(self.tmp.name).resolve();self.registry=self.repo/'data/l2_pipeline/registry'
        self.gate=ensure_once(self.registry/'INGEST_GATE.json',dict(schema='l2_c63_ingest_gate_v1',canary_designs=['parked','working'],phase='CANARIES',c77={'retained':True}))
        self.pilot=SimpleNamespace(repo=self.repo,registry=self.registry,case_dir=lambda row:self.repo/row['design_id']/row['attempt_id'])
        self.card=ensure_once(self.repo/'new.json',dict(design_id='next'))
        self.rows=[dict(design_id=d,attempt_id='current',card_sha256='bound') for d in ('parked','working')]
        ensure_once(self.registry/'ACTIVE_DESIGNS.json',dict(designs=self.rows))
    def event(self,**kwargs):
        value=dict(design_id='parked',attempt_id='current',action='prescreen',decision='PRESCREEN_PARK');value.update(kwargs)
        with (self.registry/'AUTOPILOT_LEDGER.jsonl').open('a') as out:out.write(json.dumps(value)+'\n')
    def run_check(self,estimated=('working',)):
        with patch('scripts.l2_pipeline.ops.ingest_gate.estimated_runs',return_value=[dict(design_id=d,run_id=d) for d in estimated]):return check(self.pilot,[self.card])
    def test_ledger_park_removes_only_parked_and_preserves_current_state(self):
        self.event();before=self.gate.read_bytes();value=self.run_check()
        self.assertEqual(value['canary_designs'],['working']);self.assertEqual(value['phase'],'BATCH_OPEN')
        self.assertEqual(value['c77'],{'retained':True});self.assertEqual(self.gate.read_bytes(),before)
        self.assertEqual(value['prescreen_removed_canaries'],['parked'])
    def test_bound_receipt_removes_canary_without_ledger(self):
        path=ensure_once(self.pilot.case_dir(self.rows[0])/'PRESCREEN.json',dict(status='PRESCREEN_PARK',card_sha256='bound'))
        self.assertEqual(self.run_check()['canary_designs'],['working'])
        path.write_text(json.dumps(dict(status='PRESCREEN_PARK',card_sha256='stale')))
        self.assertEqual(self.run_check()['phase'],'BATCH_OPEN')
    def test_other_park_stale_attempt_and_unestimated_remaining_still_block(self):
        for event in (dict(decision='PARK'),dict(attempt_id='old')):
            self.event(**event)
            self.assertEqual(self.run_check()['phase'],'BATCH_OPEN')
        self.event()
        with self.assertRaisesRegex(ValueError,'canaries'):self.run_check(('unrelated',))
    def test_all_parked_requires_replacement_canaries(self):
        self.event();self.event(design_id='working');value=self.run_check(('unrelated',))
        self.assertEqual(value['canary_designs'],['next']);self.assertEqual(value['phase'],'CANARIES')
    def test_c77_already_pruned_gate_is_unchanged(self):
        original=json.loads(self.gate.read_text());original['canary_designs']=['working'];self.gate.write_text(json.dumps(original))
        self.assertEqual(self.run_check(),dict(original,phase='BATCH_OPEN'))

class OwnerBatchOpenTests(unittest.TestCase):
    """C82: a sha256-bound owner decision file opens the batch; anything else leaves the gate as it was."""
    DECISION='docs/plans/l2-pipeline/HUB_RULING_C82_BATCH_OPEN.md'
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name).resolve();self.repo=base/'repo';self.registry=self.repo/'data/l2_pipeline/registry'
        self.pilot=SimpleNamespace(repo=self.repo,registry=self.registry,case_dir=lambda row:self.repo/row['design_id']/row['attempt_id'])
        self.card=ensure_once(self.repo/'new.json',dict(design_id='next'))
        self.decision=self.repo/self.DECISION;self.decision.parent.mkdir(parents=True);self.decision.write_text('owner opens the batch\n')
        self.outside=base/'outside.md';self.outside.write_text('not part of the repository\n')
        self.gate=self.registry/'INGEST_GATE.json'
    def record(self,**changes):
        return dict(dict(decision_path=self.DECISION,sha256=sha(self.decision),time='2026-10-12T08:03:00+00:00'),**changes)
    def write_gate(self,record=None,**state):
        value=dict(dict(schema='l2_c63_ingest_gate_v1',canary_designs=[],phase='CANARIES',c77={'retained':True}),**state)
        if record is not None:value['owner_batch_open']=record
        self.gate.parent.mkdir(parents=True,exist_ok=True);self.gate.write_text(json.dumps(value));return value
    def run_check(self,estimated=()):
        with patch('scripts.l2_pipeline.ops.ingest_gate.estimated_runs',return_value=[dict(design_id=d,run_id=d) for d in estimated]):return check(self.pilot,[self.card])
    def outcome(self,estimated=()):
        try:return self.run_check(estimated)
        except ValueError as exc:return 'ValueError: '+str(exc)
    def test_valid_record_opens_batch_before_canary_logic(self):
        record=self.record();self.write_gate(record,canary_designs=['unestimated']);before=self.gate.read_bytes()
        with patch('scripts.l2_pipeline.ops.ingest_gate.estimated_runs',side_effect=AssertionError('canary logic ran')),\
             patch('scripts.l2_pipeline.ops.ingest_gate.prescreen_parked',side_effect=AssertionError('canary logic ran')):
            value=check(self.pilot,[self.card])
        self.assertEqual(value,dict(json.loads(before),phase='BATCH_OPEN'))
        self.assertEqual(value['canary_designs'],['unestimated']);self.assertEqual(value['owner_batch_open'],record)
        self.assertEqual(self.gate.read_bytes(),before)
    def test_missing_file_or_sha_mismatch_does_not_open_and_behaviour_is_unchanged(self):
        self.write_gate(canary_designs=['working']);paused=self.outcome();opened=self.outcome(('working',))
        self.assertTrue(paused.startswith('ValueError: C63 INGEST_PAUSED'));self.assertEqual(opened['phase'],'BATCH_OPEN')
        stale=self.record();self.decision.write_text('edited after the owner decision\n')
        for label,record in [('sha mismatch',stale),('wrong digest',self.record(sha256='0'*64)),
                             ('missing file',self.record(decision_path='docs/plans/l2-pipeline/NOT_THERE.md'))]:
            with self.subTest(label):
                self.write_gate(record,canary_designs=['working'])
                self.assertEqual(self.outcome(),paused);self.assertEqual(self.outcome(('working',)),dict(opened,owner_batch_open=record))
        self.decision.unlink();self.write_gate(stale,canary_designs=['working'])
        with self.subTest('file removed'):self.assertEqual(self.outcome(),paused)
    def test_unsafe_or_malformed_records_do_not_open(self):
        self.write_gate();paused=self.outcome()
        (self.repo/'link.md').symlink_to(self.outside);(self.repo/'auth.json').write_text('decoy\n')
        cases=[('absolute path',self.record(decision_path=str(self.decision))),
               ('parent traversal',self.record(decision_path='../outside.md',sha256=sha(self.outside))),
               ('symlink out of repo',self.record(decision_path='link.md',sha256=sha(self.outside))),
               ('credential-like name',self.record(decision_path='auth.json',sha256=sha(self.repo/'auth.json'))),
               ('directory',self.record(decision_path='docs/plans')),
               ('no path',dict(sha256=sha(self.decision))),('no digest',dict(decision_path=self.DECISION)),
               ('non-string digest',self.record(sha256=123)),('non-string path',self.record(decision_path=None)),
               ('record is a string',self.DECISION),('record is a list',[self.DECISION]),('record is false',False)]
        for label,record in cases:
            with self.subTest(label):
                self.write_gate(record);self.assertEqual(self.outcome(),paused)
        self.write_gate(self.record());self.assertEqual(self.outcome()['phase'],'BATCH_OPEN')

if __name__=='__main__':unittest.main()
