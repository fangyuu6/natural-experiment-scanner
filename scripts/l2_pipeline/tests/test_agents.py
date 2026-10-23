from __future__ import annotations
import contextlib, copy, io, json, os, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts.l2_pipeline.agents import emit_stage_jobs, emit_job, load_job, collect_job
from scripts.l2_pipeline.agents.dispatch import main as dispatch_main, command, preflight, _run
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.storage import sha, write_once
from scripts.l2_pipeline.tests.test_pipeline import card, tuple_row
REPO=Path(__file__).resolve().parents[3]

class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        c=card();c["mechanism"]="one";write_once(self.root/"source.json",{"source":"invented complete list"})
        c["sources"][0].update(path="source.json",sha256=sha(self.root/"source.json"),complete_list=True,coverage_end="2007-12-31")
        c["treatment"]={"universe":[str(i) for i in range(10)],"seed":42,"complete_list_source_id":"source1","predicate":"invented", "coverage_end":"2007-12-31","historical_convention":"cumulative","records":[{"unit":"secret-answer"}]}
        c["sensitivities"]=[{"name":"s","changes":{"treatment":{"records":[{"unit":"secret-answer"}]}}}]
        write_once(self.root/"card.json",c);self.ctx=StageContext(load_card(self.root/"card.json"),self.root/"work",REPO);self.jobs=self.root/"jobs"
        write_once(self.ctx.design_dir/"SPEC.json",{"fixture":"answer-free spec","full_variant_inventory":["primary","s"]})
        write_once(self.ctx.design_dir/"SUPPORT.json",{"outcome_values_parsed":0})
    def tearDown(self): self.tmp.cleanup()
    def emit(self,stage,role,**kwargs):return emit_job(self.ctx,stage,role,jobs_root=self.jobs,**kwargs)
    def put(self,job,value):write_once(Path(job.output_path),value)
    def forecast(self,job):
        return {"persona":job.role,"design_id":job.design_id,"spec_sha256":job.contract["spec_sha256"],"point":-1,"interval":[-3,1],"interval_level":.8,"rationale":"invented primary ATT original units","disclosures":[],"blindness_attestation":{"other_submissions_read":False,"outcome_associations_read":False}}
    def test_blind_coders_self_contained_deterministic(self):
        jobs=emit_stage_jobs(self.ctx,"S1",jobs_root=self.jobs);self.assertEqual([j.role for j in jobs],["A","B"])
        self.assertNotEqual(jobs[0].output_dir,jobs[1].output_dir)
        text=Path(jobs[0].job_path).read_text();self.assertIn("invented complete list",text);self.assertNotIn("secret-answer",text)
        self.assertIn("do not",text.lower());self.assertEqual(self.emit("S1","A"),jobs[0]);self.assertEqual(load_job(Path(jobs[0].job_path)),jobs[0])
    def test_sidecar_prompt_source_tamper_refuse(self):
        job=self.emit("S1","A");side=Path(job.job_path).with_suffix(".job.json");v=json.loads(side.read_text());v["contract"]["manual"]["predicate"]="spoof";side.write_text(json.dumps(v))
        with self.assertRaisesRegex(ValueError,"metadata changed"):load_job(Path(job.job_path))
        side.write_text(json.dumps(job.__dict__))
        (self.root/"source.json").write_text('{}')
        with self.assertRaisesRegex(ValueError,"input changed"):load_job(Path(job.job_path))
    def test_collector_coder_requires_full_universe_attestation(self):
        job=self.emit("S1","A");value={"role":"A","design_id":job.design_id,"source_sha256":job.contract["source_sha256"],"records":[tuple_row(str(i)) for i in range(10)],"blindness_attestation":{"other_submissions_read":False,"outcome_associations_read":False}}
        self.put(job,value);self.assertTrue(collect_job(job.job_path)["validated"]);self.assertEqual(collect_job(job.job_path),collect_job(job.job_path))
        value["blindness_attestation"]["other_submissions_read"]=True;Path(job.output_path).write_text(json.dumps(value))
        with self.assertRaises(ValueError):collect_job(job.job_path)
    def test_audit_requires_originals_locked_seeded_and_validates(self):
        with self.assertRaises(FileNotFoundError):self.emit("S1","auditor")
        paths=[]
        for role in ("A","B"):
            p=self.ctx.design_dir/"agents"/role/"submission.json";write_once(p,{"records":[tuple_row(str(i)) for i in range(10)]});paths.append(p)
        write_once(self.ctx.design_dir/"CODING_LOCK.json",{"files":{str(p):sha(p) for p in paths}})
        write_once(self.ctx.design_dir/"AUDIT_SELECTION.json",{"seed":42,"sample":__import__("scripts.l2_pipeline.stages.s1_treatment",fromlist=["audit_sample"]).audit_sample([tuple_row(str(i)) for i in range(10)],42),"source_sha256":sha(self.root/"source.json")})
        job=self.emit("S1","auditor");value={"role":"auditor","design_id":job.design_id,"source_sha256":job.contract["source_sha256"],"selection_sha256":job.contract["selection_sha256"],"checks":[{"unit":str(i),"status":"match","official_locator":"invented official"} for i in range(10)],"blindness_attestation":{"other_submissions_read":False,"outcome_associations_read":False}}
        self.put(job,value);collect_job(job.job_path);self.assertTrue(json.loads(Path(job.promote_path).read_text())["audit"]["passed"])
    def test_three_default_persona_jobs_filled_originals_and_immutable_promotion(self):
        jobs=emit_stage_jobs(self.ctx,"S5",jobs_root=self.jobs);self.assertEqual(len(jobs),3)
        job=jobs[0];self.assertTrue(job.output_path.endswith("Card.filled.json"));self.put(job,self.forecast(job));receipt=collect_job(job.job_path)
        self.assertTrue(receipt["validated"]);self.assertEqual(receipt,collect_job(job.job_path))
        value=self.forecast(job);value["point"]=2;Path(job.output_path).write_text(json.dumps(value))
        with self.assertRaises(ValueError):collect_job(job.job_path)
    def test_forecast_invalid_probability_or_blindness_rejected(self):
        for field,new in (("point",True),("interval",[3,-1]),("interval_level",1),("blindness_attestation",None)):
            job=self.emit("S5","Card");value=self.forecast(job);value[field]=new
            p=Path(job.output_path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value))
            with self.assertRaises(ValueError):collect_job(job.job_path)
    def test_draft_review_not_approval_and_must_definition(self):
        with self.assertRaises(ValueError):self.emit("S6","reviewer")
        job=self.emit("S6","reviewer",allow_draft=True);value={"design_id":job.design_id,"candidate_sha256":job.contract["candidate_sha256"],"review_scope":"draft_readiness","complete_sweeps":1,"fix_cycles":0,"must":[],"should":[],"verdict":"PASS","independence_attestation":{"wrote_implementation":False,"wrote_fixes":False,"real_outcome_associations_read":False}}
        self.put(job,value);collect_job(job.job_path);promoted=json.loads(Path(job.promote_path).read_text());self.assertFalse(promoted["execution_authorized"]);self.assertTrue(promoted["sealed_review_required"])
        value["must"]=[{"id":"1","description":"cost","evidence":"fixture","impact":"slow"}];value["verdict"]="STOP";Path(job.output_path).write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,"MUST"):collect_job(job.job_path)
    def test_dispatch_dry_run_and_host_guard(self):
        job=self.emit("S5","Card");buf=io.StringIO()
        with patch("scripts.l2_pipeline.agents.dispatch.preflight"),contextlib.redirect_stdout(buf):self.assertEqual(dispatch_main(["--job",job.job_path,"--dry-run"]),0)
        self.assertIn("codex exec -C",buf.getvalue());self.assertIn(" < ",buf.getvalue());self.assertFalse(Path(job.output_dir).exists())
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):dispatch_main(["--job",job.job_path])
        with patch.dict(os.environ,{"CODEX_SANDBOX":"sandbox"}),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):dispatch_main(["--job",job.job_path,"--hub-outside-sandbox"])
    def test_dispatch_failure_no_retry_mock_only(self):
        job=self.emit("S5","Card")
        with patch("scripts.l2_pipeline.agents.liveness.process_table",return_value=None), patch("scripts.l2_pipeline.agents.dispatch.subprocess.run",side_effect=OSError("invented launch failure")) as run:
            result=_run(Path(job.job_path),"invented-codex");self.assertEqual(result["status"],"FAILED_NO_RETRY");self.assertEqual(run.call_count,1)
            with self.assertRaises(FileExistsError):_run(Path(job.job_path),"invented-codex")
            self.assertEqual(run.call_count,1)
    def test_network_optional_only_source_jobs_and_concurrency_bound(self):
        source=self.emit("S1","A");persona=self.emit("S5","Card");self.assertIn("--search",command(source));self.assertNotIn("--search",command(persona))
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):dispatch_main(["--job",persona.job_path,"--dry-run","--max-concurrency","5"])

    def test_exact_dispatch_argv_for_every_role(self):
        from dataclasses import replace
        source=self.emit("S1","A")
        network=[source,self.emit("S1","B"),replace(source,role="auditor")]
        offline=emit_stage_jobs(self.ctx,"S5",jobs_root=self.jobs)+[self.emit("S6","reviewer",allow_draft=True)]
        for job in network:
            self.assertEqual(command(job,"codex"),["codex","--search","exec","-C",job.repo,"-s","workspace-write","-c","sandbox_workspace_write.network_access=true","--color","never","-o",job.result_path,"-"])
        for job in offline:
            self.assertEqual(command(job,"codex"),["codex","exec","-C",job.repo,"-s","workspace-write","--color","never","-o",job.result_path,"-"])

    def help_result(self,argv,**kwargs):
        text="Usage: codex [OPTIONS]\n      --search\n" if argv==["codex","--help"] else "Usage: codex exec [OPTIONS] [PROMPT]\n  -C, --cd <DIR>\n  -s, --sandbox <MODE>\n  -c, --config <VALUE>\n      --color <COLOR>\n  -o, --output-last-message <FILE>\n"
        return SimpleNamespace(returncode=0,stdout=text,stderr="")

    def test_preflight_only_help_parses_both_shapes_no_reservations(self):
        import subprocess
        source=self.emit("S1","A");offline=self.emit("S5","Card")
        with patch("scripts.l2_pipeline.agents.dispatch.subprocess.run",side_effect=self.help_result) as run:
            preflight([source,self.emit("S1","B"),offline],"codex")
        self.assertEqual([c.args[0] for c in run.call_args_list],[["codex","--help"],["codex","exec","--help"],command(source,"codex")[:-1]+["--help","-"],command(offline,"codex")[:-1]+["--help","-"]])
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["stdin"],subprocess.DEVNULL)
            self.assertIn("--help",call.args[0])
        self.assertFalse(Path(source.output_dir).exists());self.assertFalse(Path(offline.output_dir).exists())

    def test_preflight_refuses_missing_scope_and_parse_errors(self):
        job=self.emit("S1","A")
        missing=SimpleNamespace(returncode=0,stdout="Usage: codex [OPTIONS]\n",stderr="")
        bad=SimpleNamespace(returncode=2,stdout="",stderr="unexpected argument")
        with patch("scripts.l2_pipeline.agents.dispatch.subprocess.run",side_effect=[missing,self.help_result(["codex","exec","--help"])]) as run:
            with self.assertRaisesRegex(ValueError,"top-level --search missing"):preflight([job],"codex")
            self.assertEqual(run.call_count,2)
        with patch("scripts.l2_pipeline.agents.dispatch.subprocess.run",side_effect=[self.help_result(["codex","--help"]),self.help_result(["codex","exec","--help"]),bad]):
            with self.assertRaisesRegex(ValueError,"rejected"):preflight([job],"codex")

    def test_wave_preflight_failure_launches_nothing(self):
        job=self.emit("S1","A")
        env={k:v for k,v in os.environ.items() if k not in {"CODEX_SANDBOX","CODEX_SANDBOX_NETWORK_DISABLED","L2_PIPELINE_SANDBOX"}}
        with patch.dict(os.environ,env,clear=True),patch("scripts.l2_pipeline.agents.dispatch.preflight",side_effect=ValueError("invented incompatible CLI")) as check,patch("scripts.l2_pipeline.agents.dispatch._run") as run,contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            dispatch_main(["--job",job.job_path,"--hub-outside-sandbox"])
        check.assert_called_once();run.assert_not_called();self.assertFalse(Path(job.output_dir).exists())

    def test_preflight_only_is_allowed_in_sandbox_without_launch(self):
        job=self.emit("S1","A");buf=io.StringIO()
        with patch.dict(os.environ,{"CODEX_SANDBOX":"sandbox"}),patch("scripts.l2_pipeline.agents.dispatch.subprocess.run",side_effect=self.help_result),patch("scripts.l2_pipeline.agents.dispatch._run") as run,contextlib.redirect_stdout(buf):
            self.assertEqual(dispatch_main(["--job",job.job_path,"--codex","codex","--preflight-only"]),0)
        self.assertEqual(json.loads(buf.getvalue()),{"preflight":"PASS","jobs":1,"model_calls":0})
        run.assert_not_called();self.assertFalse(Path(job.output_dir).exists())

    def test_original_tamper_refuses_lock_and_original_inventory_required(self):
        from scripts.l2_pipeline.predictions import lock_predictions
        from scripts.l2_pipeline.freeze import build_manifest
        job=self.emit("S5","Card");self.put(job,self.forecast(job));collect_job(job.job_path)
        value=self.forecast(job);value["point"]=4;Path(job.output_path).write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,"collection binding mismatch"):lock_predictions(self.ctx.design_dir,["Card"],job.design_id,job.contract["spec_sha256"])
        Path(job.output_path).write_bytes(json.dumps(self.forecast(job),sort_keys=True,separators=(",",":"),allow_nan=False).encode()+b"\n")
        lock_predictions(self.ctx.design_dir,["Card"],job.design_id,job.contract["spec_sha256"])
        from scripts.l2_pipeline.freeze import ROLES
        inventory={"PREDICTIONS_LOCK.json":"predictions","predictions/Card/submission.json":"predictions"}
        for role in ROLES:
            name="fixture_"+role+".json"
            write_once(self.ctx.design_dir/name,card() if role=="card" else {"fixture":role});inventory[name]=role
        with self.assertRaisesRegex(ValueError,"Original forecast"):build_manifest(self.ctx.design_dir,"prep",inventory,["Card"],"invented_hub")
    def test_strict_false_and_archival_coding_refusal(self):
        job=self.emit("S5","Card");value=self.forecast(job);value["blindness_attestation"]["outcome_associations_read"]=0;self.put(job,value)
        with self.assertRaisesRegex(ValueError,"blindness"):collect_job(job.job_path)
        c=copy.deepcopy(self.ctx.card.payload);c["automation"]={"archival_s1_import":True}
        from scripts.l2_pipeline.contracts import DesignCard
        ctx=StageContext(DesignCard.from_mapping(c,self.ctx.card.path),self.ctx.workspace,REPO)
        with self.assertRaisesRegex(ValueError,"blind rerun"):emit_job(ctx,"S1","A",jobs_root=self.jobs)

    def test_batch4_primary_ledger_template_bound_and_normalized(self):
        from scripts.l2_pipeline.contracts import DesignCard
        template=REPO/"scripts/l2_batch4/specs/predictions/pay_secrecy/Card.template.json"
        c=copy.deepcopy(self.ctx.card.payload);c["automation"]={"prediction_templates":{"Card":str(template)}}
        ctx=StageContext(DesignCard.from_mapping(c,self.ctx.card.path),self.ctx.workspace,REPO)
        job=emit_job(ctx,"S5","Card",jobs_root=self.jobs)
        value=json.loads(template.read_text());before=sha(template)
        value.update(status="FILLED_PRE_ESTIMATION",entered_at="2026-10-07T00:00:00Z",central_ATT=.1,interval_80=[-.1,.3],prob_positive=.6,prob_negative=.2,prob_near_zero=.2,near_zero_threshold=.01,prob_diagnostics_pass=.7,direction="positive",mechanism="Invented registered primary mechanism",assumptions=["Invented fixture assumption"],outcome_associations_seen_attestation=False,signature="Internal AI Card",disclosures=[],blindness_attestation={"other_submissions_read":False,"outcome_associations_read":False})
        self.put(job,value);collect_job(job.job_path)
        normalized=json.loads(Path(job.promote_path).read_text())
        self.assertEqual(normalized["point"],.1);self.assertEqual(normalized["interval_level"],.8);self.assertEqual(normalized["design_id"],"invented");self.assertEqual(sha(template),before)
        self.assertEqual(json.loads(Path(job.output_path).read_text()),value);self.assertTrue((Path(job.output_dir)/"NORMALIZATION.json").is_file());self.assertTrue(normalized["disclosures"])
        value["prob_positive"]=.9;Path(job.output_path).write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,"sum to one"):collect_job(job.job_path)

    def test_s5_template_exact_disclosure_and_blindness_fields(self):
        job=self.emit("S5","Card");prompt=Path(job.job_path).read_text()
        self.assertIn('disclosures: an array of strings',prompt)
        self.assertIn('[] is valid',prompt)
        self.assertIn('{"other_submissions_read":false,"outcome_associations_read":false}',prompt)
        self.assertIn('required even if absent from the blank template',prompt)
        for disclosure in (None,{},[""],[" "],[1]):
            value=self.forecast(job);value["disclosures"]=disclosure
            p=Path(job.output_path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,"disclosures missing/invalid"): collect_job(job.job_path)
        value=self.forecast(job);value.pop("disclosures");p.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,"disclosures missing/invalid"): collect_job(job.job_path)
        value=self.forecast(job);value["disclosures"]=["Internal invented persona"]
        p.write_text(json.dumps(value));self.assertTrue(collect_job(job.job_path)["validated"])

    def test_attempt_archives_original_and_collects_canonical_binding(self):
        from scripts.l2_pipeline.agents.jobs import archive_outputs
        from scripts.l2_pipeline.predictions import lock_predictions
        old=self.emit("S5","Card");invalid=self.forecast(old);invalid["disclosures"]={"wrong":"type"};self.put(old,invalid)
        original=Path(old.output_path).read_bytes();archive=archive_outputs(Path(old.job_path),"original_invalid","Invalid disclosures")
        entries=json.loads(archive.read_text())["files"]
        self.assertTrue(all(Path(r["archive_path"]).read_bytes()==Path(r["original_path"]).read_bytes() for r in entries))
        new=self.emit("S5","Card",attempt_id="attempt2")
        self.assertNotEqual(old.job_path,new.job_path);self.assertNotEqual(old.output_dir,new.output_dir)
        self.assertEqual(new.promote_path,old.promote_path);self.assertEqual(load_job(Path(new.job_path)),new)
        self.put(new,self.forecast(new));receipt=collect_job(new.job_path)
        self.assertEqual(Path(old.output_path).read_bytes(),original)
        canonical=Path(new.promote_path).parent/"COLLECTION.json";self.assertEqual(json.loads(canonical.read_text()),receipt)
        lock=lock_predictions(self.ctx.design_dir,["Card"],new.design_id,new.contract["spec_sha256"])
        inventory=json.loads(lock.read_text())["original_files"]
        self.assertIn("predictions/Card/attempts/attempt2/Card.filled.json",inventory)
        self.assertNotIn("predictions/Card/Card.filled.json",inventory)
        self.assertEqual(collect_job(new.job_path),receipt)

    def test_template_bound_attempt_keeps_normalization_and_review_stop(self):
        from scripts.l2_pipeline.contracts import DesignCard
        template=REPO/"scripts/l2_batch4/specs/predictions/pay_secrecy/Card.template.json"
        c=copy.deepcopy(self.ctx.card.payload);c["automation"]={"prediction_templates":{"Card":str(template)}}
        ctx=StageContext(DesignCard.from_mapping(c,self.ctx.card.path),self.ctx.workspace,REPO)
        job=emit_job(ctx,"S5","Card",jobs_root=self.jobs,attempt_id="attempt2")
        value=json.loads(template.read_text())
        value.update(status="FILLED_PRE_ESTIMATION",entered_at="2026-10-07T00:00:00Z",central_ATT=.1,interval_80=[-.1,.3],prob_positive=.6,prob_negative=.2,prob_near_zero=.2,near_zero_threshold=.01,prob_diagnostics_pass=.7,direction="positive",mechanism="Invented mechanism",assumptions=["Invented assumption"],outcome_associations_seen_attestation=False,signature="Internal AI Card",disclosures=[],blindness_attestation={"other_submissions_read":False,"outcome_associations_read":False})
        self.put(job,value);collect_job(job.job_path)
        canonical=Path(job.promote_path).parent/"NORMALIZATION.json"
        self.assertEqual(canonical.read_bytes(),(Path(job.output_dir)/"NORMALIZATION.json").read_bytes())
        old=self.emit("S6","reviewer",allow_draft=True)
        write_once(Path(old.promote_path),{"verdict":"STOP","fixture":"original"});before=Path(old.promote_path).read_bytes()
        new=self.emit("S6","reviewer",allow_draft=True,attempt_id="attempt2")
        self.assertNotEqual(new.promote_path,old.promote_path);self.assertIn("attempt2",new.output_dir)
        review={"design_id":new.design_id,"candidate_sha256":new.contract["candidate_sha256"],"review_scope":"draft_readiness","complete_sweeps":1,"fix_cycles":0,"must":[],"should":[],"verdict":"PASS","independence_attestation":{"wrote_implementation":False,"wrote_fixes":False,"real_outcome_associations_read":False}}
        self.put(new,review);collect_job(new.job_path)
        self.assertEqual(Path(old.promote_path).read_bytes(),before)
        self.assertEqual(json.loads(Path(new.promote_path).read_text())["verdict"],"PASS")

    def test_invalid_attempt_identity_refuses_before_write(self):
        for identity in ("../escape","bad/name",""):
            with self.assertRaisesRegex(ValueError,"Unsafe job identity"):self.emit("S5","Card",attempt_id=identity)
        with self.assertRaisesRegex(ValueError,"archived CLI parse failure"):self.emit("S1","A",attempt_id="attempt2")

    def s1_parse_failure(self,role="A"):
        from scripts.l2_pipeline.agents.jobs import archive_outputs
        old=self.emit("S1",role);out=Path(old.output_dir)
        write_once(out/"DISPATCH_ONCE.json",{"job_sha256":old.job_sha256,"argv":["codex","exec","--search"]})
        write_once(out/"DISPATCH_RESULT.json",{"status":"FAILED_NO_RETRY","exit_code":2,"job_path":old.job_path,"role":role})
        (out/"dispatch.log").write_text("error: unexpected argument '--search' found\n")
        archive_outputs(Path(old.job_path),"attempt1_cli_parse_failure","CLI parse failure before model call")
        return old

    def test_s1_attempt_archives_isolates_selects_and_promotes(self):
        old=self.s1_parse_failure();before={str(p):sha(p) for p in Path(old.output_dir).rglob("*") if p.is_file()}
        new=self.emit("S1","A",attempt_id="attempt2")
        self.assertNotEqual(old.output_dir,new.output_dir);self.assertNotEqual(old.job_path,new.job_path)
        self.assertEqual(new.promote_path,old.output_path);self.assertEqual(new.retries,0)
        self.assertEqual(load_job(Path(new.job_path)),new)
        buf=io.StringIO()
        with patch("scripts.l2_pipeline.agents.dispatch.preflight") as check,contextlib.redirect_stdout(buf):
            self.assertEqual(dispatch_main(["--jobs-root",str(self.jobs),"--stage","S1","--attempt-id","attempt2","--dry-run"]),0)
        check.assert_called_once();self.assertEqual(len(buf.getvalue().splitlines()),1);self.assertIn(new.job_path,buf.getvalue())
        self.put(new,{"role":"A","design_id":new.design_id,"source_sha256":new.contract["source_sha256"],"records":[tuple_row(str(i)) for i in range(10)],"blindness_attestation":{"other_submissions_read":False,"outcome_associations_read":False}})
        receipt=collect_job(new.job_path);self.assertTrue(receipt["validated"])
        self.assertEqual(Path(new.promote_path).read_bytes(),Path(new.output_path).read_bytes())
        self.assertTrue(all(sha(Path(p))==h for p,h in before.items()))
        (Path(old.output_dir)/"dispatch.log").write_text("changed")
        with self.assertRaisesRegex(ValueError,"evidence changed"):load_job(Path(new.job_path))

    def test_s1_attempt_refuses_model_output_nonparse_failure_and_missing_archive(self):
        old=self.s1_parse_failure();out=Path(old.output_dir)
        Path(old.result_path).write_text("existing model output")
        with self.assertRaisesRegex(ValueError,"existing coding/model output"):self.emit("S1","A",attempt_id="attempt2")
        other=self.s1_parse_failure("B");result=Path(other.output_dir)/"DISPATCH_RESULT.json"
        value=json.loads(result.read_text());value["exit_code"]=1;result.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,"archived CLI parse failure"):self.emit("S1","B",attempt_id="attempt2")
        with self.assertRaisesRegex(ValueError,"limited to coders"):self.emit("S1","auditor",attempt_id="attempt2")
        value["exit_code"]=2;result.write_text(json.dumps(value))
        (Path(other.output_dir)/"archive/attempt1_cli_parse_failure/ARCHIVE.json").unlink()
        with self.assertRaisesRegex(ValueError,"archived CLI parse failure"):self.emit("S1","B",attempt_id="attempt2")

    def test_pipeline_cli_emits_both_s1_attempt_jobs_without_dispatch(self):
        from scripts.l2_pipeline.cli import main as cli_main
        for role in ("A","B"):self.s1_parse_failure(role)
        write_once(self.ctx.design_dir/"receipts/S0/1.json",{"card_sha256":sha(self.ctx.card.path),"result":{"disposition":"ready","artifacts":[]}})
        buf=io.StringIO()
        with patch("scripts.l2_pipeline.agents.dispatch._run") as run,contextlib.redirect_stdout(buf):
            self.assertEqual(cli_main(["jobs","--stage","S1","--design",str(self.ctx.card.path),"--work-dir",str(self.ctx.workspace),"--jobs-root",str(self.jobs),"--attempt-id","attempt2"]),0)
        payload=json.loads(buf.getvalue());self.assertFalse(payload["dispatched"])
        self.assertEqual([j["role"] for j in payload["jobs"]],["A","B"])
        self.assertTrue(all(j["contract"]["attempt_id"]=="attempt2" for j in payload["jobs"]))
        run.assert_not_called()

    def test_review_attempt_binds_prior_stop_and_fix_cycle(self):
        previous=self.ctx.design_dir/"reviewer"/"readiness_review.json"
        old={"design_id":self.ctx.card.design_id,"review_scope":"draft_readiness","verdict":"STOP","must":[{"id":"M01","description":"Invented registered result missing","evidence":"invented code","impact":"missing_registered_result"}],"should":[]}
        write_once(previous,old)
        job=self.emit("S6","reviewer",allow_draft=True,attempt_id="attempt2",previous_review_path=previous)
        self.assertEqual(job.contract["expected_fix_cycles"],1);self.assertEqual(job.contract["previous_must_ids"],["M01"])
        self.assertEqual(job.contract["previous_review_sha256"],sha(previous))
        prompt=Path(job.job_path).read_text();transport=Path(job.job_path).with_suffix(".review-inputs.json")   # above REVIEW_PROMPT_CHAR_LIMIT the prior MUST text is in the hash-bound transport file (jobs.emit_job)
        self.assertIn("Invented registered result missing",prompt+(transport.read_text() if transport.exists() else ""))
        self.assertIn(".venv_did/bin/python -B -O",prompt);self.assertIn("Do not install packages",prompt)
        value={"design_id":job.design_id,"candidate_sha256":job.contract["candidate_sha256"],"review_scope":"draft_readiness","complete_sweeps":1,"fix_cycles":0,"must":[],"should":[],"verdict":"PASS","independence_attestation":{"wrote_implementation":False,"wrote_fixes":False,"real_outcome_associations_read":False}}
        self.put(job,value)
        with self.assertRaisesRegex(ValueError,"fix cycle differs"):collect_job(job.job_path)
        value["fix_cycles"]=1;Path(job.output_path).write_text(json.dumps(value))
        self.assertTrue(collect_job(job.job_path)["validated"])
        self.assertEqual(json.loads(previous.read_text()),old)
        previous.write_text('{}')
        with self.assertRaisesRegex(ValueError,"input changed"):load_job(Path(job.job_path))

if __name__=="__main__":unittest.main()
