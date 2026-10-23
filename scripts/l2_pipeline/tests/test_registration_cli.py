"""Invented-only CLI registration, signature and pre-adoption gate regressions."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from scripts.l2_pipeline import registration as reg
from scripts.l2_pipeline.cli import main
from scripts.l2_pipeline.contracts import StageContext, DesignCard
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.engine.calibration import blind_anchor, calibration_operators
from scripts.l2_pipeline.engine.design import build_design, structural_rows, treatment_rows
from scripts.l2_pipeline.engine.inference import design_binding
from scripts.l2_pipeline.tests.test_equivalence import invented_fixture


class RegistrationCLI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.card=DesignCard.from_mapping(dict(design_id='invented',version='v1',policy_family='labor',mechanism='law',
            track='DiD',unit='state',outcome_family='wage'),self.root/'card.json')
        self.ctx=StageContext(self.card,self.root,Path(__file__).resolve().parents[3])
        self.ctx.design_dir.mkdir(parents=True)

    def test_approve_requires_explicit_matching_identity_and_utc(self):
        ensure_once(self.ctx.design_dir/'REGISTRATION.json',dict(hub_signer='hub'))
        for signer,stamp in [(None,'2026-10-07T00:00:00Z'),('wrong','2026-10-07T00:00:00Z'),('hub',None),('hub','2026-10-07T00:00:00')]:
            with self.subTest(signer=signer,stamp=stamp), self.assertRaises(ValueError):
                reg.approve(self.ctx,SimpleNamespace(signer=signer,signed_at=stamp),'prep')
        self.assertFalse((self.ctx.design_dir/'APPROVAL_PREP.json').exists())

    def test_final_signature_refuses_draft_and_never_reserves(self):
        root=self.ctx.design_dir
        ensure_once(root/'REGISTRATION.json',dict(hub_signer='hub'))
        ensure_once(root/'EXECUTION_REVIEW.json',dict(verdict='PASS',must=[],review_scope='draft_readiness'))
        with patch.object(reg,'verify',return_value=dict(ok=True,manifest_sha256='a'*64)), self.assertRaisesRegex(ValueError,'Sealed'):
            reg.approve(self.ctx,SimpleNamespace(signer='hub',signed_at='2026-10-07T00:00:00Z'),'final')
        self.assertFalse((root/'APPROVAL_FINAL.json').exists())
        self.assertFalse((root/'EXECUTION_ONCE.json').exists())

    def test_calibration_refuses_unapproved_before_reading_any_panel(self):
        with patch.object(reg,'require_verified',side_effect=RuntimeError('approval refused')):
            with self.assertRaisesRegex(RuntimeError,'approval refused'):reg.calibration(self.ctx)
        self.assertEqual(list(self.ctx.design_dir.iterdir()),[])

    def test_anchor_does_not_decode_exposed_garbage(self):
        panel=self.root/'panel.csv'
        panel.write_text('unit,year,outcome_value\nA,1,1\nA,2,2\nB,1,4\nB,2,8\nA,3,DO_NOT_DECODE\nB,3,DO_NOT_DECODE\n')
        value=blind_anchor(panel,units=['A','B'],periods=[1,2],earliest_operative_period=3,value_column='outcome_value')
        self.assertEqual(value['outcome_values_converted'],4)
        self.assertEqual(value['treated_post_outcome_values_converted'],0)
        with self.assertRaises(ValueError):blind_anchor(panel,units=['A','B'],periods=[1,3],earliest_operative_period=3,value_column='outcome_value')

    def test_calibration_operator_equals_execution_and_ri_gate(self):
        declaration,rows=invented_fixture()
        op=calibration_operators(declaration,structural_rows(declaration,rows),treatment_rows(declaration))
        self.assertEqual(design_binding(op['design']),design_binding(build_design(declaration,rows)))
        self.assertEqual(op['ri_feasibility']['requested_draws'],199)
        self.assertGreaterEqual(op['ri_feasibility']['feasible_fraction'],.9)
        self.assertEqual(op['ri_feasibility']['redrawn'],0)

    def test_unsigned_cli_hook_does_not_invent_signer(self):
        with patch.object(reg,'command',return_value={'ok':True}) as command:
            self.assertEqual(main(['registration','--action','approve-final','--design','invented.json','--work-dir',str(self.root)]),0)
            self.assertIsNone(command.call_args.args[0].signer)
            self.assertIsNone(command.call_args.args[0].signed_at)

    def test_sealed_jobs_cli_exposes_narrow_root_and_attempt(self):
        with patch('scripts.l2_pipeline.cli.load_card',return_value=self.card), patch('scripts.l2_pipeline.cli.preflight'), patch('scripts.l2_pipeline.agents.emit_stage_jobs',return_value=[]) as emit:
            self.assertEqual(main(['jobs','--stage','S6','--design','invented.json','--work-dir',str(self.root),
                '--jobs-root',str(self.root/'jobs'),'--attempt-id','sealed']),0)
            self.assertEqual(emit.call_args.kwargs['attempt_id'],'sealed')
            self.assertFalse(emit.call_args.kwargs['allow_draft'])

    def test_tests_evidence_rejects_stale_or_invented_pass_flags(self):
        path=self.root/'evidence.json';ensure_once(path,dict(schema='l2_pipeline_test_evidence_v1',passed=True,runs=[],code_inventory={}))
        with self.assertRaisesRegex(ValueError,'evidence'):reg._check_evidence(path)
