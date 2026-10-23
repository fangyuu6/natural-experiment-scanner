"""Synthetic preparation recovery; no agents, downloads or real fits."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile,unittest
from unittest.mock import patch
from scripts.l2_pipeline.autopilot import Autopilot,Backend,read
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.preparation_recovery import replay_public_panel
from scripts.l2_pipeline.registration import _retain_cards


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Path(self.tmp.name).resolve();self.root=self.repo/'data/l2_pipeline/work/d/v2'
        self.root.mkdir(parents=True);self.card=ensure_once(self.repo/'card.json',dict(design_id='d'))
        self.ctx=SimpleNamespace(repo=self.repo,design_dir=self.root,
            card=SimpleNamespace(design_id='d',path=self.card,payload=dict(design_id='d',treatment=dict(excluded_units=['AA']))))
        self.registry=self.repo/'data/l2_pipeline/registry';self.registry.mkdir(parents=True)
        (self.registry/'AUTOPILOT_STOP').touch()
        self.failure=ensure_once(self.root/'receipts/S2/1.json',dict(card_sha256=sha(self.card),result=dict(stage='S2',disposition='failed_no_retry',reason='old binding')))
        self.pointer=dict(path=str(self.failure),sha256=sha(self.failure))
        self.auth=ensure_once(self.repo/'authorization.json',dict(schema='l2_public_panel_replay_authorization_v1',stage='S2',designs=['d'],execution_authorized=False,failure_reason='old binding'))

    def test_replay_retains_failure_and_crash_reuses_ready(self):
        before=self.failure.read_bytes()
        def run(ctx,stage):
            artifact=ensure_once(self.root/'panel.json',dict(synthetic=True))
            result=dict(stage='S2',disposition='ready',artifacts=[dict(path=str(artifact),sha256=sha(artifact))])
            ensure_once(self.root/'receipts/S2/2.json',dict(card_sha256=sha(self.card),result=result))
            return SimpleNamespace(as_dict=lambda:result)
        with patch('scripts.l2_pipeline.cli.run_stage',side_effect=run) as replay:
            first=replay_public_panel(self.ctx,self.pointer,self.auth)
            self.assertEqual(replay_public_panel(self.ctx,self.pointer,self.auth),first)
            self.assertEqual(replay.call_count,1)
        self.assertEqual(self.failure.read_bytes(),before)
        (self.root/'panel.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'artifact changed'):replay_public_panel(self.ctx,self.pointer,self.auth)

    def test_frozen_or_consumed_never_replays(self):
        for name in ['FREEZE_PREP.json','TERMINAL.json','EXECUTION_ONCE.json']:
            path=ensure_once(self.root/name,{})
            with patch('scripts.l2_pipeline.cli.run_stage') as run:
                with self.assertRaisesRegex(ValueError,'Frozen or consumed'):replay_public_panel(self.ctx,self.pointer,self.auth)
                run.assert_not_called()
            path.unlink()

    def test_unapproved_live_or_changed_failure_cannot_replay(self):
        (self.registry/'AUTOPILOT_STOP').unlink()
        with self.assertRaisesRegex(ValueError,'Stopped'):replay_public_panel(self.ctx,self.pointer,self.auth)
        (self.registry/'AUTOPILOT_STOP').touch()
        with self.assertRaisesRegex(ValueError,'binding changed'):replay_public_panel(self.ctx,dict(self.pointer,sha256='bad'),self.auth)
        other=ensure_once(self.repo/'other.json',dict(schema='l2_public_panel_replay_authorization_v1',stage='execute',designs=['d'],execution_authorized=False))
        with self.assertRaisesRegex(ValueError,'authorization'):replay_public_panel(self.ctx,self.pointer,other)

    def test_later_failed_receipt_is_not_retried(self):
        ensure_once(self.root/'receipts/S2/2.json',read(self.failure))
        with patch('scripts.l2_pipeline.cli.run_stage') as run:
            with self.assertRaisesRegex(ValueError,'Later preparation'):replay_public_panel(self.ctx,self.pointer,self.auth)
            run.assert_not_called()

    def test_raw_and_effective_s0_cards_are_both_preserved(self):
        raw=dict(design_id='d');effective=deepcopy(self.ctx.card.payload)
        for retained in (raw,effective):
            path=ensure_once(self.root/'CARD.json',retained);before=path.read_bytes()
            with patch('scripts.l2_pipeline.cards.load_card',return_value=SimpleNamespace(payload=raw)):
                _retain_cards(self.ctx)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(read(self.root/'SCIENTIFIC_CARD.json'),effective)
            path.unlink()

    def test_unrelated_card_is_not_accepted(self):
        ensure_once(self.root/'CARD.json',dict(design_id='d',treatment=dict(excluded_units=['BB'])))
        with patch('scripts.l2_pipeline.cards.load_card',return_value=SimpleNamespace(payload=dict(design_id='d'))):
            with self.assertRaisesRegex(ValueError,'artifact changed'):_retain_cards(self.ctx)
        self.assertFalse((self.root/'SCIENTIFIC_CARD.json').exists())

    def test_supersession_resumes_but_a_new_stop_remains_blocking(self):
        p=Autopilot(self.registry,Backend(self.repo));row=dict(design_id='d',attempt_id='test')
        p.stop(row,'S2','old');old=p.case_dir(row)/'STOP.json'
        ensure_once(p.case_dir(row)/'STOP_SUPERSEDED_C52.json',dict(stop=dict(path=str(old),sha256=sha(old)),evidence=[dict(path=str(self.auth),sha256=sha(self.auth))]))
        self.assertEqual(p.next_action(row),'history')
        p.stop(row,'S3','new');self.assertEqual(p.stop_record(row)['reason'],'new')
        self.auth.write_text('{}')
        with self.assertRaisesRegex(ValueError,'supersession bytes changed'):p.next_action(row)
