"""S1 hub convention regressions: stock/history separation and scoped jobs."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.l2_pipeline.stages.s1_reconciliation import apply_convention, window_check
from scripts.l2_pipeline.stages.s1_treatment import reconcile
from scripts.l2_pipeline.tests.test_pipeline import card, tuple_row, REPO
from scripts.l2_pipeline.storage import sha, write_once
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.cli import run_stage
from scripts.l2_pipeline.source_packs import build_source_pack
from scripts.l2_pipeline.agents.jobs import emit_job, load_job
from scripts.l2_pipeline.agents.collect import collect_job


class ConventionTests(unittest.TestCase):
    def setUp(self):
        self.pack={"sources":[{"source_id":"list", "complete_list":True, "sha256":"hash", "universe":["a","b","c"],
                              "history_type":"cumulative_ever", "as_of":"2007-12-31", "coverage_end":"2007-12-31"}]}
        self.declarations=[{"source_id":"list", "source_sha256":"hash", "listed_units":["a"], "locator":"complete table"}]
        self.rows=[tuple_row("a"),tuple_row("b","unknown",None),tuple_row("c","unknown",None)]
    def test_symmetric_cumulative_unknowns_and_immutable_originals(self):
        before=copy.deepcopy(self.rows)
        a,af=apply_convention(self.rows,["a","b","c"],"one",self.pack,self.declarations)
        b,bf=apply_convention(self.rows,["a","b","c"],"one",self.pack,self.declarations)
        self.assertEqual(self.rows,before);self.assertEqual(af,bf)
        self.assertEqual([r["unit"] for r in af],["b","c"])
        self.assertEqual(reconcile(a,b,["a","b","c"])["counts"],{"known_match":3})
    def test_current_stock_unknown_history_retained_and_bounded_fills(self):
        self.pack["sources"][0]["history_type"]="current_stock"
        rows,fills=apply_convention(self.rows,["a","b","c"],"one",self.pack,self.declarations)
        self.assertEqual(rows[1]["status"],"unknown")
        self.assertEqual(rows[1]["complete_list_boundary"][0]["through"],"2007-12-31")
        self.assertTrue(all(f["scope"]=="stock_boundary_only" for f in fills))
    def test_positive_outside_stock_is_never_erased(self):
        self.rows[1]=tuple_row("b",start=2003)
        rows,fills=apply_convention(self.rows,["a","b","c"],"one",self.pack,self.declarations)
        self.assertEqual(rows[1],self.rows[1]);self.assertEqual([f["unit"] for f in fills],["c"])
    def test_uncertified_undated_and_invalid_rosters_refused(self):
        for change in ({"complete_list":False},{"as_of":None},{"sha256":"changed"}):
            p=copy.deepcopy(self.pack);p["sources"][0].update(change)
            with self.assertRaises(ValueError):apply_convention(self.rows,["a","b","c"],"one",p,self.declarations)
        d=copy.deepcopy(self.declarations);d[0]["listed_units"]=["outside"]
        with self.assertRaises(ValueError):apply_convention(self.rows,["a","b","c"],"one",self.pack,d)


class ReconciliationStageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        c=card();c["mechanism"]="one";c["outcome"]["years"]=[2000,2007]
        write_once(self.root/"source.json",{"fixture":"invented"})
        c["sources"][0].update(path="source.json",sha256=sha(self.root/"source.json"),complete_list=True,coverage_end="2007-12-31")
        c["treatment"]={"universe":[str(i) for i in range(20)],"seed":42,"complete_list_source_id":"source1", "predicate":"fixture", "coverage_end":"2007-12-31", "historical_convention":"cumulative"}
        c["analysis"]={"min_pre_periods":3}
        write_once(self.root/"card.json",c);self.card=load_card(self.root/"card.json")
        candidate={"source_id":"list","url":"https://example.org/list","license":"invented", "as_of":"2007-12-31","coverage_end":"2007-12-31","predicate":"fixture", "universe":c["treatment"]["universe"],"history_type":"current_stock","complete_list":True,
                   "verification":{"kind":"text_anchors","anchors":["complete stock as of 2007-12-31"],"locator":"table","date_basis":"fixture date","scope_basis":"fixture scope"}}
        self.candidate=candidate
        pack=build_source_pack(self.card,repo=REPO,pack_dir=self.root/"pack",candidates=[candidate],fetcher=lambda url:(("complete stock as of 2007-12-31 "+"fixture evidence "*30).encode(),url,"text/plain"))
        self.mp=self.root/"pack/manifest.json"
        old=StageContext(self.card,self.root/"originals",REPO)
        self.jobs=[]
        for role in ("A","B"):
            j=emit_job(old,"S1",role,jobs_root=self.root/"coder_jobs",source_pack_path=self.mp)
            rows=[tuple_row(str(i),start=2002+(role=="B" and i==0)) for i in range(16)]
            rows += [dict(tuple_row(str(i),"unknown",None),reason="history unresolved",blocker={"unit":str(i),"detail":f"{i} different scope issue {i}","search_query":f"{i} official scope","official_locator":"https://example.org/law"}) for i in range(16,20)]
            write_once(Path(j.output_path),{"design_id":"invented","role":role,"source_sha256":sha(self.mp),"records":rows,"blindness_attestation":{"other_submissions_read":False,"outcome_associations_read":False}})
            self.jobs.append(j)
        ruling=write_once(self.root/"ruling.json",{"ruling_id":"HUB_RULING_V2_LIST","cards":{"invented":[{"source_id":"list","source_sha256":pack["sources"][0]["sha256"],"listed_units":[str(i) for i in range(16)],"locator":"fixture table"}]}})
        self.ctx=StageContext(self.card,self.root/"reconciliation",REPO)
        write_once(self.ctx.design_dir/"S1_RECONCILIATION_INPUTS.json",{"coder_jobs":dict(zip(("A","B"),[j.job_path for j in self.jobs])),"ruling_path":str(ruling),"ruling_sha256":sha(ruling),"followup_jobs_root":str(self.root/"followups")})
    def tearDown(self): self.tmp.cleanup()
    def run_s1(self):
        self.assertEqual(run_stage(self.ctx,"S0").disposition,"ready")
        result=run_stage(self.ctx,"S1")
        self.assertNotEqual(result.disposition,"failed_no_retry",result.reason)
        return result
    def test_stock_window_parks_but_emits_seeded_audit_and_one_conflict_job(self):
        before={j.output_path:sha(Path(j.output_path)) for j in self.jobs}
        result=self.run_s1();self.assertEqual(result.metrics["s1_status"],"parked")
        jobs=[load_job(Path(j["job_path"])) for j in self.ctx.read("S1_FOLLOWUP_JOBS.json")["jobs"]]
        self.assertEqual({j.role for j in jobs},{"auditor","third_reader"})
        third=next(j for j in jobs if j.role=="third_reader");audit=next(j for j in jobs if j.role=="auditor")
        self.assertEqual(third.contract["units"],["0"]);self.assertEqual(len(audit.contract["sample"]),10)
        self.assertNotIn("0",audit.contract["sample"])
        self.assertEqual(before,{j.output_path:sha(Path(j.output_path)) for j in self.jobs})
        self.assertFalse((self.ctx.design_dir/"TREATMENT.json").exists())
    def test_third_read_collector_exact_units_official_evidence_and_separate_artifact(self):
        self.run_s1();jobs=self.ctx.read("S1_FOLLOWUP_JOBS.json")["jobs"]
        j=load_job(Path(next(j for j in jobs if j["role"]=="third_reader")["job_path"]))
        check={"unit":"0","resolution":"A","status":"treated","start_period":2002,"mechanism":"one","official_locator":"https://example.org/law","rationale":"fixture official annual-clock proof"}
        value={"design_id":"invented","role":"third_reader","source_sha256":sha(self.mp),"reconciliation_sha256":j.contract["reconciliation_sha256"],"checks":[check],"blindness_attestation":{"other_submissions_read":False,"outcome_associations_read":False}}
        write_once(Path(j.output_path),value);self.assertTrue(collect_job(j.job_path)["validated"])
        self.assertFalse(self.ctx.read("THIRD_READ.json")["s1_ready"])
        value["checks"][0]["start_period"]=2003;Path(j.output_path).write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,"chosen coder"):collect_job(j.job_path)
    def test_followup_refuses_original_tampering(self):
        self.run_s1();j=self.ctx.read("S1_FOLLOWUP_JOBS.json")["jobs"][0]
        Path(self.jobs[0].output_path).write_text('{}')
        with self.assertRaisesRegex(ValueError,"original/artifact changed"):load_job(Path(j["job_path"]))
    def test_v3_collector_accepts_exact_locked_ten_without_granting_s1(self):
        self.run_s1();jobs=self.ctx.read("S1_FOLLOWUP_JOBS.json")["jobs"]
        j=load_job(Path(next(j for j in jobs if j["role"]=="auditor")["job_path"]))
        value={"design_id":"invented","role":"auditor","source_sha256":sha(self.mp),"selection_sha256":j.contract["selection_sha256"],
               "checks":[{"unit":u,"status":"match","official_locator":"https://example.org/law"} for u in j.contract["sample"]],
               "blindness_attestation":{"other_submissions_read":False,"outcome_associations_read":False}}
        write_once(Path(j.output_path),value);self.assertTrue(collect_job(j.job_path)["validated"])
        self.assertTrue(self.ctx.read("OFFICIAL_AUDIT.json")["audit"]["passed"])
        self.assertEqual(run_stage(self.ctx,"S1").disposition,"parked")
        self.assertFalse((self.ctx.design_dir/"TREATMENT.json").exists())
    def test_stock_cannot_certify_long_window(self):
        self.run_s1();r=self.ctx.read("RECONCILIATION.json")
        self.assertFalse(r["window"]["feasible"]);self.assertIsNone(r["window"]["candidate_full_year_window"])
        self.assertEqual(len(r["filled_by_convention"]["A"]),4)
        self.assertEqual(r["historical_comparison"]["counts"]["both_unknown"],4)
    def test_cumulative_pack_can_reach_ready_for_audit_after_symmetric_fills(self):
        historical=build_source_pack(self.card,repo=REPO,pack_dir=self.root/"historical_pack",candidates=[dict(self.candidate,history_type="cumulative_ever")],
                                    fetcher=lambda url:(("complete stock as of 2007-12-31 "+"fixture evidence "*30).encode(),url,"text/plain"))
        mp=self.root/"historical_pack/manifest.json"
        originals=StageContext(self.card,self.root/"historical_originals",REPO);paths={}
        for role,old in zip(("A","B"),self.jobs):
            j=emit_job(originals,"S1",role,jobs_root=self.root/"historical_coders",source_pack_path=mp)
            value=json.loads(Path(old.output_path).read_text());value["source_sha256"]=sha(mp);value["records"][0]["start_period"]=2002
            write_once(Path(j.output_path),value);paths[role]=j.job_path
        ruling=write_once(self.root/"historical_ruling.json",{"ruling_id":"HUB_RULING_V2_LIST","cards":{"invented":[{
            "source_id":"list","source_sha256":historical["sources"][0]["sha256"],"listed_units":[str(i) for i in range(16)],"locator":"fixture"}]}})
        self.ctx=StageContext(self.card,self.root/"historical_reconciliation",REPO)
        write_once(self.ctx.design_dir/"S1_RECONCILIATION_INPUTS.json",{"coder_jobs":paths,"ruling_path":str(ruling),"ruling_sha256":sha(ruling),"followup_jobs_root":str(self.root/"historical_followups")})
        result=self.run_s1();self.assertEqual(result.metrics["s1_status"],"ready_for_audit")
        r=self.ctx.read("RECONCILIATION.json");self.assertEqual(r["historical_comparison"]["counts"],{"known_match":20})
        self.assertTrue(r["window"]["feasible"])
        self.assertTrue(all(f["scope"]=="history_through_boundary" for f in r["filled_by_convention"]["A"]))

if __name__=="__main__": unittest.main()
