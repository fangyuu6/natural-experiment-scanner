"""Invented-only host workflow checks; no dispatch, real fit or study signature."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.l2_pipeline import hub
from scripts.l2_pipeline.contracts import StageResult, Disposition
from scripts.l2_pipeline.storage import sha, write_once
from scripts.l2_pipeline.tests import test_pipeline


class HubTests(unittest.TestCase):
    def test_advance_skips_missing_pass_and_continues_neighbor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cards = root / "cards"
            cards.mkdir()
            for name in ("first", "second"):
                value = test_pipeline.card()
                value["design_id"] = name
                write_once(cards / (name + ".json"), value)
            args = SimpleNamespace(design=None, batch=cards, work_dir=root / "work", only_pass=True)
            def check(ctx, stage):
                if ctx.card.design_id == "first":
                    raise ValueError("Missing ready S1")
            def single(ctx, stage):
                return StageResult(stage, ctx.card.design_id, Disposition.READY, "invented ready")
            with patch.object(hub, "preflight", side_effect=check), patch.object(hub, "run_stage", side_effect=single) as runner:
                report = hub.advance(args)
            self.assertEqual([r["status"] for r in report["results"]], ["SKIPPED_NOT_PASS", "PREPARED"])
            self.assertEqual([call.args[1] for call in runner.call_args_list], ["S2", "S3", "S4"])
            self.assertEqual(report["actual_data_estimation_calls"], 0)

    def test_advance_stops_at_parked_outcomes(self):
        self.args.only_pass = True
        with patch.object(hub, "preflight"), patch.object(hub, "run_stage", side_effect=lambda ctx, stage: StageResult(stage, ctx.card.design_id, Disposition.PARKED, "missing public files")) as runner:
            report = hub.advance(self.args)
        self.assertEqual(runner.call_count, 2)
        self.assertTrue(all(r["stages"][0]["stage"] == "S2" for r in report["results"]))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cards = self.root / "cards"
        self.cards.mkdir()
        for name in ("first", "second"):
            value = test_pipeline.card()
            value["design_id"] = name
            write_once(self.cards / (name + ".json"), value)
        self.args = SimpleNamespace(design=None, batch=self.cards, work_dir=self.root / "work", stage="S6")

    def test_failed_execution_does_not_cancel_later_design(self):
        def single(ctx, stage):
            if ctx.card.design_id == "first":
                write_once(ctx.design_dir / "FAILURE.json", {"message": "invented execution failed"})
                return StageResult(stage, "first", Disposition.FAILED, "invented failure")
            return StageResult(stage, "second", Disposition.READY, "invented completed")
        with patch.object(hub, "run_stage", side_effect=single) as runner:
            report = hub.run(self.args)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual([r["disposition"] for r in report["results"]], ["failed_no_retry", "ready"])

    def test_malformed_card_and_unexpected_failure_isolated(self):
        (self.cards / "00_bad.json").write_text("{")
        with patch.object(hub, "run_stage", side_effect=[RuntimeError("invented"), StageResult("S6", "second", Disposition.READY, "ok")]) as runner:
            report = hub.run(self.args)
        self.assertEqual(len(report["results"]), 3)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(report["results"][-1]["disposition"], "ready")

    def test_prepare_failure_does_not_abort_later_card(self):
        self.args.stage = "S1"
        self.args.allow_draft = False
        self.args.jobs_root = self.root / "jobs"
        self.args.attempt_id = self.args.source_pack = None
        with patch.object(hub, "preflight"), patch.object(hub, "emit_stage_jobs", side_effect=[ValueError("incomplete source"), []]) as emitter:
            report = hub.prepare(self.args)
        self.assertEqual(emitter.call_count, 2)
        self.assertEqual([r["status"] for r in report["results"]], ["FAILED_NO_RETRY", "PREPARED"])
        self.assertFalse(report["dispatched"])

    def test_collect_failure_isolated(self):
        args = SimpleNamespace(allow_historical_draft=False)
        job = SimpleNamespace(design_id="invented", stage="S1", role="A")
        with patch.object(hub, "selected_jobs", return_value=[(self.root / "bad.md", job, None), (self.root / "ok.md", job, None)]), \
                patch.object(hub, "collect_job", side_effect=[ValueError("malformed output"), {"validated": True}]) as collector:
            report = hub.collect(args)
        self.assertEqual(collector.call_count, 2)
        self.assertFalse(report["results"][0]["validated"])
        self.assertTrue(report["results"][1]["validated"])

    def test_s5_ri_refuses_before_emission_and_keeps_passing_neighbor(self):
        self.args.stage = 'S5'
        self.args.allow_draft = False
        self.args.jobs_root = self.root/'jobs'
        self.args.attempt_id = 'invented'
        self.args.source_pack = None
        failed = dict(passed=False, checks={'RI199': {'feasible_fraction': .89}})
        passed = dict(passed=True, checks={'RI199': {'feasible_fraction': .9}})
        with patch.object(hub, 'preflight'), patch.object(hub, 'precheck_s5', side_effect=[failed, passed]), \
                patch.object(hub, 'sha', return_value='a'*64), patch.object(hub, 'emit_stage_jobs', return_value=[]) as emit:
            report = hub.prepare(self.args)
        self.assertEqual([r['status'] for r in report['results']], ['STOPPED_PRE_RUN', 'PREPARED'])
        self.assertEqual(emit.call_count, 1)
        self.assertEqual(emit.call_args.args[0].card.design_id, 'second')
        self.assertEqual(report['results'][0]['jobs'], [])
        with patch.object(hub, 'prepare', return_value=report), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(hub.main(['prepare', 'S5', '--batch', str(self.cards),
                '--work-dir', str(self.root/'work'), '--jobs-root', str(self.root/'jobs')]), 2)

    def test_s5_explicit_hub_override_retains_failure_and_no_execution_authority(self):
        self.args.stage = 'S5'
        self.args.allow_draft = False
        self.args.jobs_root = self.root/'jobs'
        self.args.attempt_id = 'invented'
        self.args.source_pack = None
        self.args.hub_override_ri_feasibility = 'Invented separately authorized scientific review'
        with patch.object(hub, 'preflight'), patch.object(hub, 'precheck_s5', return_value={'passed': False}), \
                patch.object(hub, 'sha', return_value='a'*64), patch.object(hub, 'emit_stage_jobs', return_value=[]) as emit:
            report = hub.prepare(self.args)
        self.assertEqual(emit.call_count, 2)
        for name in ('first', 'second'):
            record = json.loads((self.root/'work'/name/'v1'/'RI_PRE_S5_HUB_OVERRIDE.json').read_text())
            self.assertFalse(record['execution_authorized'])
            self.assertFalse(record['changes_registered_stop_rule'])
        self.assertTrue(all(r['status'] == 'PREPARED' for r in report['results']))

    def test_geometry_check_matches_execution_and_calibration_without_outcomes(self):
        from scripts.l2_pipeline.tests.test_equivalence import invented_fixture
        from scripts.l2_pipeline.engine.design import build_design, structural_rows, treatment_rows
        from scripts.l2_pipeline.engine.ri import permutation_reference
        from scripts.l2_pipeline.engine.calibration import calibration_operators
        declaration, rows = invented_fixture()
        geometry = build_design(declaration, rows)
        expected199 = calibration_operators(declaration, structural_rows(declaration, rows),
                                            treatment_rows(declaration))['ri_feasibility']
        expected999 = permutation_reference(declaration, geometry, {k: 0. for k in geometry['mask']})['feasibility']
        with patch('scripts.l2_pipeline.engine.ri.one_reference', side_effect=AssertionError('Outcome operator called')):
            check = hub.ri_geometry_feasibility(declaration, geometry)
        self.assertEqual(check['checks']['RI199']['feasible_draws'], expected199['feasible_draws'])
        self.assertEqual(check['checks']['RI999']['feasible_draws'], expected999['feasible_draws'])
        self.assertTrue(check['passed'])
        self.assertEqual(check['outcome_values_read'], 0)
        self.assertEqual(check['actual_data_estimation_calls'], 0)
        self.assertEqual(check['redrawn'], 0)

    def test_geometry_requires_both_fixed_counts_and_retains_failed_indices(self):
        from scripts.l2_pipeline.engine import ri
        for failed in (set(range(20)), set(range(199, 299))):
            index = iter(range(999))
            def fake(*args):
                if next(index) in failed:
                    raise ValueError('RI lead inventory changed under relabeling')
                return {}
            with patch.object(ri, 'predraw_labels', return_value=[[]]*999) as draws, \
                    patch.object(ri, 'pre_family', return_value=[-2, -1]), \
                    patch.object(ri, 'relabeled_design', side_effect=fake) as relabel:
                check = hub.ri_geometry_feasibility({}, {})
            self.assertFalse(check['passed'])
            self.assertEqual(relabel.call_count, 999)
            draws.assert_called_once_with({}, 999)
            self.assertEqual(check['checks']['RI999']['infeasible_count'], len(failed))
            self.assertEqual([r['draw_index'] for r in check['checks']['RI999']['infeasible_draws']], sorted(failed))
            self.assertEqual(check['checks']['RI199']['support_pass'], min(failed) >= 199)
            self.assertEqual(check['redrawn'], 0)

    def test_s5_metadata_check_never_opens_a_panel_or_raw_outcome(self):
        from scripts.l2_pipeline.tests.test_equivalence import invented_fixture
        from scripts.l2_pipeline.engine.design import build_design
        declaration, rows = invented_fixture()
        ctx = next(ctx for ctx, error in hub.contexts(self.args) if ctx is not None)
        ctx.design_dir.mkdir(parents=True)
        geometry = build_design(declaration, rows)
        support = dict(variant='primary', passed=True, cell_inventory=geometry['cells'])
        write_once(ctx.design_dir/'SUPPORT.json', dict(variants=[support]))
        write_once(ctx.design_dir/'TREATMENT.json', {})
        write_once(ctx.design_dir/'SPEC.json', {'invented': True})
        metadata = ctx.design_dir/'panels/primary/metadata.csv'
        metadata.parent.mkdir(parents=True)
        metadata.write_text('invented metadata only')
        original_open = Path.open
        def guarded(path, *args, **kwargs):
            if path.name == 'panel.csv' or 'raw' in path.parts:
                raise AssertionError('Real outcome source opened')
            return original_open(path, *args, **kwargs)
        with patch('scripts.l2_pipeline.registered.numerical_declaration', return_value=declaration), \
                patch('scripts.l2_pipeline.registered.registered_panel_keys', return_value={(r[0], r[1]) for r in rows}), \
                patch.object(Path, 'open', guarded):
            check = hub.precheck_s5(ctx)
        self.assertTrue(check['passed'])
        self.assertEqual(check['outcome_values_read'], 0)
        self.assertTrue((ctx.design_dir/'RI_PRE_S5.json').is_file())

    def test_dispatch_forwards_flags_and_bounds_before_launch(self):
        with patch.object(hub.dispatcher, "main", return_value=0) as dispatch:
            self.assertEqual(hub.main(["dispatch", "S1", "--jobs-root", str(self.root), "--max-concurrency", "1", "--dry-run", "--design", "first"]), 0)
        forwarded = dispatch.call_args.args[0]
        self.assertEqual(forwarded[forwarded.index("--stage") + 1], "S1")
        self.assertEqual(forwarded[forwarded.index("--max-concurrency") + 1], "1")
        self.assertIn("--dry-run", forwarded)
        for n in ("0", "5", "-1"):
            with self.subTest(n=n), patch.object(hub.dispatcher, "main") as dispatch, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                hub.main(["dispatch", "S5", "--max-concurrency", n])
            dispatch.assert_not_called()

    def bundle(self):
        root = self.root / "bundle"
        root.mkdir()
        test_pipeline.FinalApprovalTests().make_bundle(root)
        (root / "APPROVAL_FINAL.json").unlink()
        ctx = SimpleNamespace(design_dir=root, card=SimpleNamespace(design_id="invented", path=self.root / "card.json",
                              payload={"automation": {"hub_signer": "fixture_hub"}}),
                              read=lambda name: json.loads((root / name).read_text()))
        args = SimpleNamespace(only_pass=True, signer="fixture_hub", signed_at="2026-10-09T00:00:00Z")
        return root, ctx, args

    def test_approve_pass_uses_real_guard_and_does_not_fit(self):
        root, ctx, args = self.bundle()
        before = (root / "EXECUTION_REVIEW.json").read_bytes()
        with patch.object(hub, "contexts", return_value=[(ctx, None)]), patch.object(hub, "effective_context", side_effect=lambda c: c):
            report = hub.approve_run(args)
        self.assertEqual(report["results"][0]["status"], "APPROVED", report)
        value = json.loads((root / "APPROVAL_FINAL.json").read_text())
        self.assertEqual(value["review_sha256"], sha(root / "EXECUTION_REVIEW.json"))
        self.assertEqual((root / "EXECUTION_REVIEW.json").read_bytes(), before)
        self.assertFalse((root / "EXECUTION_ONCE.json").exists())
        self.assertEqual(report["actual_data_estimation_calls"], 0)

    def test_approve_invalid_or_stale_sealed_review_never_signs(self):
        root, ctx, args = self.bundle()
        original = json.loads((root / "EXECUTION_REVIEW.json").read_text())
        changes = [dict(verdict="STOP"), dict(must=[{"id": "M1"}]), dict(must=None),
                   dict(must=0), dict(review_scope="draft_readiness"), dict(candidate_sha256="a" * 64),
                   dict(complete_sweeps=True), dict(fix_cycles=True),
                   dict(independence_attestation={"wrote_implementation": True}), dict(design_id="other")]
        for change in changes:
            with self.subTest(change=change):
                value = copy.deepcopy(original)
                value.update(change)
                (root / "EXECUTION_REVIEW.json").write_text(json.dumps(value))
                with patch.object(hub, "contexts", return_value=[(ctx, None)]), patch.object(hub, "effective_context", side_effect=lambda c: c):
                    report = hub.approve_run(args)
                self.assertFalse(report["results"][0]["signed"], report)
                self.assertFalse((root / "APPROVAL_FINAL.json").exists())
                self.assertFalse((root / "EXECUTION_ONCE.json").exists())

    def test_approve_stop_and_missing_design_do_not_cancel_pass(self):
        root, ctx, args = self.bundle()
        stop_root = self.root / "stop"
        stop_root.mkdir()
        (stop_root / "EXECUTION_REVIEW.json").write_text(json.dumps(dict(verdict="STOP", must=["M1"], review_scope="sealed_candidate")))
        stop = SimpleNamespace(design_dir=stop_root, card=SimpleNamespace(design_id="stop", path=self.root / "stop.json"),
                               read=lambda n: json.loads((stop_root / n).read_text()))
        with patch.object(hub, "contexts", return_value=[(None, hub.failure("bad.json", ValueError("bad"))), (stop, None), (ctx, None)]), \
                patch.object(hub, "effective_context", side_effect=lambda c: c):
            report = hub.approve_run(args)
        self.assertEqual([r["status"] for r in report["results"]], ["FAILED_NO_RETRY", "SKIPPED_NOT_PASS", "APPROVED"])
        self.assertFalse((stop_root / "APPROVAL_FINAL.json").exists())

    def test_approve_requires_only_pass(self):
        with self.assertRaisesRegex(ValueError, "only-pass"):
            hub.approve_run(SimpleNamespace(only_pass=False))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            hub.main(["approve-run", "--batch", str(self.cards), "--work-dir", str(self.root), "--signer", "fixture", "--signed-at", "2026-10-09T00:00:00Z"])

    def test_status_is_read_only_and_isolates_broken_receipt(self):
        first = self.root / "work" / "first" / "v1" / "receipts" / "S6" / "1.json"
        first.parent.mkdir(parents=True)
        first.write_text("{")
        with patch.object(hub, "run_stage", side_effect=AssertionError("status tried execution")):
            report = hub.status(self.args)
        self.assertEqual(len(report["results"]), 2)
        self.assertEqual(report["results"][0]["status"], "FAILED_NO_RETRY")
        self.assertEqual(report["results"][1]["design_id"], "second")
        self.assertFalse((self.root / "work" / "second").exists())
