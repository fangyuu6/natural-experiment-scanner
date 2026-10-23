"""V4 regression fixtures use invented dates and opaque outcome metadata only."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import DesignCard, StageContext
from scripts.l2_pipeline.storage import sha, write_once
from scripts.l2_pipeline.source_packs import build_source_pack
from scripts.l2_pipeline.agents.jobs import emit_job, load_job
from scripts.l2_pipeline.agents.collect import collect_job
from scripts.l2_pipeline.cli import run_stage
from scripts.l2_pipeline.tests.test_pipeline import card, tuple_row, REPO
from scripts.l2_pipeline.stages.s1_treatment import reconcile
from scripts.l2_pipeline.stages.s1_reconciliation import RULING, apply_convention, window_check, emit_followup


class V4GeometryTests(unittest.TestCase):
    def setUp(self):
        self.pack={"sources":[dict(source_id="list",complete_list=True,sha256="hash",
            universe=["a","b","c"],history_type="current_stock",as_of="2025-01-01",coverage_end="2025-01-01")]}
        self.declarations=[dict(source_id="list",source_sha256="hash",listed_units=["a"],locator="official complete table",
                                withdrawal_history={"no_documented_path":True})]
        self.rows=[tuple_row("a",start=2020),tuple_row("b","unknown",None),tuple_row("c","unknown",None)]
    def test_stock_absence_certifies_window_without_erasing_originals(self):
        old=copy.deepcopy(self.rows)
        rows,fills=apply_convention(self.rows,["a","b","c"],"one",self.pack,self.declarations,ruling_id=RULING)
        self.assertEqual(old,self.rows)
        self.assertEqual([r["status"] for r in rows],["treated","never_treated","never_treated"])
        self.assertTrue(all(f["scope"]=="v4_whole_window_absence" for f in fills))
    def test_published_withdrawals_also_certify_absence(self):
        self.declarations[0]["withdrawal_history"]={"administrator_publishes_withdrawals":True}
        rows,_=apply_convention(self.rows,["a","b","c"],"one",self.pack,self.declarations,ruling_id=RULING)
        self.assertEqual(rows[1]["status"],"never_treated")
    def test_possible_unpublished_histories_are_sensitivity_units(self):
        self.declarations[0]["withdrawal_history"]={"possible":True}
        rows,_=apply_convention(self.rows,["a","b","c"],"one",self.pack,self.declarations,ruling_id=RULING)
        self.assertEqual(rows[0]["start_period"],2020)
        self.assertTrue(all(r["history_uncertain"] for r in rows))
        self.assertEqual(rows[1]["history_sensitivity"],"drop_uncertain_history_states")
        self.assertEqual(rows[1]["status"],"never_treated")
        self.assertFalse(rows[1]["history_certified"])
        self.assertEqual(rows[1]["primary_history_assumption"],"stock_absence")
    def geometry(self,rows):
        c=card();c["analysis"]={"min_pre_periods":3,"min_controls":2,"min_treated":2,"min_cohorts":2}
        c["outcome"]["years"]=[2000,2025]
        c["treatment"]={"universe":[r["unit"] for r in rows]}
        return window_check(DesignCard.from_mapping(c,Path("invented.json")),self.pack,reconcile(rows,rows,c["treatment"]["universe"]),
                            ruling_id=RULING,inventory_window=[2014,2024])
    def test_dated_cohorts_drop_ineligible_and_2024_has_post(self):
        rows=[tuple_row("early",start=2001),tuple_row("g1",start=2019),tuple_row("g2",start=2024),tuple_row("future",start=2026),
              tuple_row("n1","never_treated",None),tuple_row("n2","never_treated",None)]
        w=self.geometry(rows)
        self.assertTrue(w["feasible"])
        self.assertEqual(w["dated_treated_count"],4)
        self.assertEqual(w["retained_treated_units"],["g1","g2"])
        g2=next(c for c in w["treated_cohorts"] if c["start_period"]==2024)
        self.assertEqual(g2["post_periods"],[2024]);self.assertNotIn(2023,g2["clean_pre_periods"])
        self.assertEqual(g2["omitted_transition_period"],2023)
    def test_recent_only_parks_for_actual_post_window(self):
        w=self.geometry([tuple_row("a",start=2025),tuple_row("b",start=2026),tuple_row("n1","never_treated",None),tuple_row("n2","never_treated",None)])
        self.assertFalse(w["feasible"]);self.assertIn("Post-period beyond outcome data ending 2024",w["reason"])
    def test_binding_whole_window_absence_uses_v4_judgment(self):
        rows=[dict(tuple_row("n1","never_treated",None),absence_certified_through="2023-12-31"),tuple_row("n2","never_treated",None),
              tuple_row("a",start=2019),tuple_row("b",start=2020)]
        w=self.geometry(rows);self.assertTrue(w["feasible"]);self.assertEqual(w["never_treated_count"],2)


class V4CollectedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        c=card();c["mechanism"]="one";c["analysis"]={"min_pre_periods":3,"min_controls":5,"min_treated":6,"min_cohorts":2}
        c["treatment"]={"universe":[str(i) for i in range(25)],"seed":42,"predicate":"fixture","complete_list_source_id":"source1","coverage_end":"2025-01-01","historical_convention":"V4"}
        c["outcome"]["years"]=[1990,2025]
        write_once(self.root/"source.json",{"fixture":"invented"})
        c["sources"][0].update(path="source.json",sha256=sha(self.root/"source.json"))
        write_once(self.root/"card.json",c);self.card=load_card(self.root/"card.json")
        candidate=dict(source_id="list",url="https://example.org/list",license="invented",as_of="2025-01-01",coverage_end="2025-01-01",
            predicate="fixture",universe=c["treatment"]["universe"],history_type="current_stock",complete_list=True,
            verification=dict(kind="text_anchors",anchors=["complete stock 2025-01-01"],locator="table",date_basis="fixture",scope_basis="fixture"))
        pack=build_source_pack(self.card,repo=REPO,pack_dir=self.root/"pack",candidates=[candidate],
            fetcher=lambda u:(("complete stock 2025-01-01 "+"fixture evidence "*30).encode(),u,"text/plain"))
        self.mp=self.root/"pack/manifest.json";self.jobs={}
        for role in ("A","B"):
            job=emit_job(StageContext(self.card,self.root/"originals",REPO),"S1",role,jobs_root=self.root/"jobs",source_pack_path=self.mp)
            rows=[tuple_row(str(i),start=2002+i%2+(role=="B" and i==0)) for i in range(20)]
            rows += [dict(tuple_row(str(i),"unknown",None),reason="unresolved fixture history") for i in range(20,25)]
            write_once(Path(job.output_path),dict(design_id="invented",role=role,source_sha256=sha(self.mp),records=rows,
                blindness_attestation=dict(other_submissions_read=False,outcome_associations_read=False)))
            self.jobs[role]=job
        declaration=dict(source_id="list",source_sha256=pack["sources"][0]["sha256"],listed_units=[str(i) for i in range(20)],locator="fixture",
                         withdrawal_history=dict(no_documented_path=True))
        self.ruling=write_once(self.root/"ruling.json",dict(ruling_id=RULING,cards={"invented":[declaration]}))
    def tearDown(self): self.tmp.cleanup()
    def run_ctx(self,name,**extra):
        ctx=StageContext(self.card,self.root/name,REPO)
        write_once(ctx.design_dir/"S1_RECONCILIATION_INPUTS.json",dict(coder_jobs={r:j.job_path for r,j in self.jobs.items()},ruling_path=str(self.ruling),
            ruling_sha256=sha(self.ruling),followup_jobs_root=str(self.root/(name+"_followups")),outcome_inventory_window=[1990,2024],**extra))
        self.assertEqual(run_stage(ctx,"S0").disposition,"ready")
        result=run_stage(ctx,"S1");self.assertNotEqual(result.disposition,"failed_no_retry",result.reason)
        return ctx,result
    def test_collected_audit_and_thirdread_promote_fresh_derived_ready(self):
        old,_=self.run_ctx("initial")
        jobs={j["role"]:load_job(Path(j["job_path"])) for j in old.read("S1_FOLLOWUP_JOBS.json")["jobs"]}
        for role,j in jobs.items():
            value=dict(design_id="invented",role=role,source_sha256=sha(self.mp),blindness_attestation=dict(other_submissions_read=False,outcome_associations_read=False))
            if role=="auditor":
                value.update(selection_sha256=j.contract["selection_sha256"],checks=[dict(unit=u,status="match",official_locator="https://example.org/law") for u in j.contract["sample"]])
            else:
                value.update(reconciliation_sha256=j.contract["reconciliation_sha256"],checks=[dict(unit="0",resolution="A",status="treated",start_period=2002,
                    mechanism="one",official_locator="https://example.org/law",rationale="Official fixture scope and dated first full year")])
            write_once(Path(j.output_path),value);self.assertTrue(collect_job(j.job_path)["validated"])
        hashes={j.output_path:sha(Path(j.output_path)) for j in [*self.jobs.values(),*jobs.values()]}
        new,result=self.run_ctx("fresh",followup_jobs={r:j.job_path for r,j in jobs.items()},emit_missing_followups=False)
        self.assertEqual(result.disposition,"ready",result.reason)
        self.assertEqual(new.read("TREATMENT.json")["audit"]["sample_size"],10)
        self.assertEqual(new.read("S1_FOLLOWUP_JOBS.json")["jobs"],[])
        self.assertEqual(hashes,{p:sha(Path(p)) for p in hashes})
    def test_rejected_thirdread_attempt2_keeps_original_job_and_new_binding(self):
        old,_=self.run_ctx("initial")
        before=sha(old.design_dir/"RECONCILIATION.json")
        job=emit_followup(old,"third_reader",self.root/"attempt2_jobs",old.design_dir/"RECONCILIATION.json",old.design_dir/"CODING_LOCK.json",self.mp,attempt_id="attempt2")
        self.assertIn("third_reader.attempt2.md",job.job_path)
        self.assertIn("attempts/attempt2/",job.output_path)
        self.assertEqual(load_job(Path(job.job_path)).contract["attempt_id"],"attempt2")
        self.assertEqual(before,sha(old.design_dir/"RECONCILIATION.json"))
        self.assertIn("nonempty rationale",Path(job.job_path).read_text())

if __name__=="__main__": unittest.main()
