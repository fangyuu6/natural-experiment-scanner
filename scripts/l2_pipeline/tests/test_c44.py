"""C44 intake and one-shot worker regressions; synthetic data only."""
import copy
from datetime import datetime, timedelta, timezone
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts.l2_pipeline.autopilot import Autopilot, Backend, atomic, ingest, read
from scripts.l2_pipeline.agents import liveness
from scripts.l2_pipeline.intake.history_routing import planning, emit_pair
from scripts.l2_pipeline.storage import sha

ROOT=Path(__file__).resolve().parents[3]

class C44Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name).resolve()
        self.registry=self.repo/'data/l2_pipeline/registry'

    def card(self):
        value=read(ROOT/'scripts/l2_pipeline/examples/design.json')
        value['analysis']['min_post_periods']=2
        value['treatment']['universe']=['U'+str(n) for n in range(15)]
        value['mechanism']='Human readable mechanism label'
        value['treatment']['predicate']='Exact binary legal predicate'
        value['treatment'].pop('seed',None)
        return value

    def test_label_predicate_and_optional_seed_survive_ingest_and_ab_packets(self):
        card=self.card();cards=self.repo/'data/l2_pipeline/cards'
        path=cards/'card.json';atomic(path,card);before=sha(path)
        backend=Backend(self.repo);pilot=Autopilot(self.registry,backend)
        self.assertEqual(ingest(cards,pilot)['ingested'],1)
        row=read(self.registry/'ACTIVE_DESIGNS.json')['designs'][0]
        policy=row['fresh_policy']
        plan=planning(card,card['treatment']['universe'],attempt_id='c44',fresh_audit=policy)
        jobs=emit_pair(card,card['treatment']['universe'],root=self.repo/'route',attempt_id='c44',plan=plan)
        for job in jobs:
            self.assertEqual(job.contract['mechanism'],card['treatment']['predicate'])
            self.assertEqual(job.contract['manual']['predicate'],card['treatment']['predicate'])
            self.assertEqual(job.contract['manual']['seed'],policy['seed'])
            self.assertEqual(job.contract['card']['mechanism'],card['treatment']['predicate'])
        self.assertEqual(before,sha(path))

    def reserve(self,value):
        p=self.repo/'data/l2_pipeline/jobs/worker/DISPATCH_ONCE.json';atomic(p,value);return p

    def test_live_legacy_output_identity_survives_restart(self):
        p=self.reserve(dict(argv=['codex','exec','-o','/tmp/exact-result','-']))
        table={92:dict(start='today',argv=['codex','exec','-o','/tmp/exact-result','-'])}
        with patch.object(liveness,'process_table',return_value=table):
            self.assertEqual(liveness.running(self.repo),1)
        self.assertFalse(liveness.receipt_path(self.repo,p).exists())

    def test_dead_and_recycled_pid_reconcile_without_touching_job_bytes(self):
        p=self.reserve(dict(host_pid=92,host_start='yesterday',argv=['codex','-o','/tmp/exact-result']))
        before=sha(p)
        with patch.object(liveness,'process_table',return_value={92:dict(start='today',argv=['other'])}):
            self.assertEqual(liveness.running(self.repo),0)
            self.assertEqual(liveness.status(self.repo,p),'failed')
        self.assertEqual(before,sha(p));self.assertFalse((p.parent/'DISPATCH_RESULT.json').exists())
        pilot=Autopilot(self.registry,Backend(self.repo));pilot.queue_reconciliations()
        self.assertEqual(len(read(self.registry/'HUB_QUEUE.json')['entries']),1)
        pilot.queue_reconciliations();self.assertEqual(len(read(self.registry/'HUB_QUEUE.json')['entries']),1)
        self.assertEqual(read(liveness.receipt_path(self.repo,p))['retries'],0)

    def test_ambiguous_is_capped_at_three_hours_and_never_retried(self):
        started=datetime.now(timezone.utc)-timedelta(hours=2)
        p=self.reserve(dict(started_at=started.isoformat(),argv=['codex','-o','/tmp/result']))
        with patch.object(liveness,'process_table',return_value=None):
            self.assertEqual(liveness.running(self.repo),1)
            with patch.object(liveness,'time') as clock:
                clock.time.return_value=started.timestamp()+10801
                self.assertEqual(liveness.running(self.repo),0)
        self.assertEqual(read(liveness.receipt_path(self.repo,p))['liveness'],'ambiguous')

    def test_sandbox_cannot_be_asserted_as_host(self):
        from scripts.l2_pipeline.agents.dispatch import require_host
        with patch.dict('os.environ',{'CODEX_SANDBOX':'seatbelt'}):
            with self.assertRaisesRegex(ValueError,'sandbox'):require_host(True)

    def test_seven_seeded_declarations_are_bound_and_leave_gates_unchanged(self):
        cards=sorted(c for c in (ROOT/'data/l2_pipeline/autopilot/c44/seeded/cards').glob('*.json') if not re.search(r'_v\d+$',c.stem))  # *_vN.json are later requeue cards (INTAKE Q4, 2026-10-17), not seeded declarations
        self.assertEqual(len(cards),7)
        from scripts.l2_pipeline.c42 import completeness
        from scripts.l2_pipeline.first_order import _same_construct
        for path in cards:
            card=read(path);old=read(card['supersedes'])
            self.assertEqual(completeness(card),[],str(path))
            for key in ['treatment','outcome','analysis','population','sensitivities','transition_year_drop']:
                self.assertEqual(card.get(key),old.get(key),(str(path),key))
            self.assertTrue(_same_construct(card['outcome'],card['first_order']['target_outcome'],card['outcome']['measure_id']))
            self.assertTrue(all(card['lineage'].get(k) for k in ['source_text_ids','screen_version','queue_version']))

    def test_health_queue_recovery_keeps_old_stop_and_records_supersession(self):
        root=ROOT/'data/l2_pipeline'
        receipt=read(root/'autopilot/c44/UPGRADE_RECEIPT.json')
        self.assertEqual(receipt['health_recovered'],11)
        health=[v for v in receipt['superseded'] if v['design_id'].startswith('bulk10_health_')]
        self.assertEqual(len(health),11)
        current=read(root/'registry/HUB_QUEUE.json')['entries']
        for item in health:
            self.assertTrue((root/'autopilot'/item['design_id']/item['attempt_id']/'STOP.json').exists())
            self.assertFalse(any(v['design_id']==item['design_id'] and v['attempt_id']==item['attempt_id'] for v in current))
        self.assertEqual(receipt['actual_data_estimation_calls'],0)

    def test_launch_window_identity_cannot_count_a_recycled_pid(self):
        backend=Backend(self.repo)
        launched=dict(pid=92,argv=['python','-B','-m','hub','--job','/tmp/exact-job'])
        with patch.object(liveness,'process_table',return_value={92:dict(start='today',argv=['other'])}):
            self.assertEqual(backend.launch_liveness(launched),'dead')
        with patch.object(liveness,'process_table',return_value=None):
            self.assertEqual(backend.launch_liveness(launched),'ambiguous')
        with patch.object(liveness,'process_table',return_value={92:dict(start='today',argv=['python','--job','/tmp/exact-job'])}):
            self.assertEqual(backend.launch_liveness(launched),'alive')

    def test_live_wrapper_without_codex_is_ambiguous_and_capped(self):
        started=datetime.now(timezone.utc)-timedelta(hours=2)
        p=self.reserve(dict(host_pid=92,host_start='today',started_at=started.isoformat(),argv=['codex','-o','/tmp/result']))
        table={92:dict(start='today',argv=['python','--job','/tmp/job'])}
        with patch.object(liveness,'process_table',return_value=table):
            self.assertEqual(liveness.status(self.repo,p),'ambiguous')
            with patch.object(liveness,'time') as clock:
                clock.time.return_value=started.timestamp()+10801
                self.assertEqual(liveness.running(self.repo),0)
        self.assertEqual(read(liveness.receipt_path(self.repo,p))['liveness'],'ambiguous')
