"""Mechanical failures stay local; contract failures stop a wave before launch."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.l2_pipeline.agents import collect
from scripts.l2_pipeline.agents import dispatch
from scripts.l2_pipeline.agents.jobs import host_plan
from scripts.l2_pipeline.agents.literature import emit_literature_jobs


class PipelineIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.env={k:v for k,v in os.environ.items() if k not in {
            "CODEX_SANDBOX","CODEX_SANDBOX_NETWORK_DISABLED","L2_PIPELINE_SANDBOX"}}
    def tearDown(self): self.temp.cleanup()

    def job(self, design):
        path=self.root/(design+".md")
        path.write_text("synthetic packet")
        path.with_suffix(".job.json").write_text(json.dumps({"stage":"S5","design_id":design}))
        out=self.root/design
        return SimpleNamespace(job_path=str(path),stage="S5",design_id=design,role="Card",
            output_dir=str(out),output_path=str(out/"forecast.json"),result_path=str(out/"result.txt"),
            repo=str(self.root),network_access=False,contract={})

    def call_dispatch(self, jobs, loader, runner, **options):
        argv=[arg for job in jobs for arg in ("--job",job.job_path)]+["--hub-outside-sandbox"]
        buf=io.StringIO()
        with patch.dict(os.environ,self.env,clear=True), \
             patch.object(dispatch,"load_job",side_effect=loader), \
             patch.object(dispatch,"check_job_types"), \
             patch.object(dispatch,"preflight"), \
             patch.object(dispatch,"_run",side_effect=runner) as run, \
             contextlib.redirect_stdout(buf):
            code=dispatch.main(argv)
        return code,json.loads(buf.getvalue()),run

    def test_invalid_job_does_not_abort_other_design(self):
        bad=self.job("bad"); good=self.job("good")
        def load(path):
            if str(path)==bad.job_path: raise ValueError("changed binding")
            return good
        code,payload,run=self.call_dispatch([bad,good],load,
            lambda *args:{"job_path":good.job_path,"status":"COMPLETED"})
        self.assertEqual(code,1)
        self.assertEqual([r["status"] for r in payload["jobs"]],
                         ["FAILED_VALIDATION_NO_RETRY","COMPLETED"])
        run.assert_called_once()

    def test_reserved_job_does_not_abort_other_design(self):
        bad=self.job("reserved"); good=self.job("good")
        Path(bad.output_dir).mkdir();Path(bad.output_dir,"DISPATCH_ONCE.json").write_text("{}")
        mapping={bad.job_path:bad,good.job_path:good}
        code,payload,run=self.call_dispatch([bad,good],lambda p:mapping[str(p)],
            lambda *args:{"job_path":good.job_path,"status":"COMPLETED"})
        self.assertEqual(code,1);self.assertIn("Reserved/existing",payload["jobs"][0]["error"])
        run.assert_called_once()

    def test_future_failure_does_not_discard_completed_design(self):
        bad=self.job("bad");good=self.job("good")
        mapping={bad.job_path:bad,good.job_path:good}
        def run(path,*args):
            if str(path)==bad.job_path: raise FileExistsError("reservation race")
            return {"job_path":good.job_path,"status":"COMPLETED"}
        code,payload,mock=self.call_dispatch([bad,good],lambda p:mapping[str(p)],run)
        self.assertEqual(code,1);self.assertEqual(mock.call_count,2)
        self.assertEqual([r["status"] for r in payload["jobs"]],["FAILED_NO_RETRY","COMPLETED"])

    def test_contract_failure_refuses_every_launch_and_help_call(self):
        job=self.job("good")
        with patch.object(dispatch,"load_job",return_value=job), \
             patch.object(dispatch,"check_job_types",side_effect=ValueError("S5 failed golden")), \
             patch.object(dispatch,"preflight") as preflight, \
             patch.object(dispatch,"_run") as run, \
             contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            dispatch.main(["--job",job.job_path,"--hub-outside-sandbox"])
        run.assert_not_called();preflight.assert_not_called()
        self.assertFalse(Path(job.output_dir).exists())

    def test_collection_continues_after_missing_artifact(self):
        good={"validated":True,"job_path":"good"}
        with patch.object(collect,"collect_job",side_effect=[FileNotFoundError("missing"),good]):
            receipts=collect.collect_jobs(["bad","good"])
        self.assertFalse(receipts[0]["validated"]);self.assertEqual(receipts[1],good)
        self.assertFalse(receipts[0]["execution_authorized"])

    def test_every_host_plan_uses_hub_and_supported_bound(self):
        for stage in ("S0","S0L","S1","S5","S6"):
            plan=host_plan(stage,self.root,"design")
            self.assertIn("scripts.l2_pipeline.hub dispatch "+stage,plan)
            self.assertIn("--max-concurrency 1..4",plan)
            self.assertIn("--max-concurrency 4",plan)

    def test_literature_preparation_continues_after_one_packet_fails(self):
        registry=self.root/"registry.json"
        registry.write_text(json.dumps({"studies":[{"study_id":"bad"},{"study_id":"good"}]}))
        from scripts.l2_pipeline.agents import literature
        write=literature._write_text_once
        def broken(path,text):
            if path.parent.name=="bad": raise OSError("one directory unavailable")
            return write(path,text)
        with patch.object(literature,"_write_text_once",side_effect=broken):
            jobs=emit_literature_jobs(registry,jobs_root=self.root/"jobs")
        self.assertEqual([j.design_id for j in jobs],["good"])
        manifest=json.loads((self.root/"jobs/S0L/MANIFEST.json").read_text())
        self.assertEqual(manifest["failures"][0]["design_id"],"bad")
        self.assertEqual(manifest["max_concurrency"],{"min":1,"max":4,"default":4})


if __name__=="__main__": unittest.main()
