"""Preparation regression: real pack bindings and preserved inadequate originals."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from scripts.l2_pipeline.agents.jobs import emit_job, load_job
from scripts.l2_pipeline.agents.collect import collect_job
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.source_packs import build_source_pack
from scripts.l2_pipeline.storage import sha, write_once
from scripts.l2_pipeline.tests.test_pipeline import card, tuple_row

REPO=Path(__file__).resolve().parents[3]

class SourceRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        c=card();c["mechanism"]="one"
        write_once(self.root/"source.json",{"source":"invented complete list"})
        c["sources"][0].update(path="source.json",sha256=sha(self.root/"source.json"),complete_list=True,coverage_end="2007-12-31")
        c["treatment"]={"universe":[str(i) for i in range(10)],"seed":42,"complete_list_source_id":"source1","predicate":"invented","coverage_end":"2007-12-31","historical_convention":"cumulative"}
        write_once(self.root/"card.json",c);self.card=load_card(self.root/"card.json")
        self.ctx=StageContext(self.card,self.root/"old",REPO);self.jobs=self.root/"jobs"
        candidate={"source_id":"dated_list","url":"https://example.org/list","license":"Invented public-domain test fixture","as_of":"2007-12-31","coverage_end":"2007-12-31","predicate":"invented","universe":c["treatment"]["universe"],"history_type":"cumulative_ever","complete_list":True,"verification":{"kind":"text_anchors","anchors":["Complete list as of 2007-12-31"],"locator":"fixture table","date_basis":"Dated fixture","scope_basis":"Explicit universe in test"}}
        self.pack_dir=self.root/"pack"
        build_source_pack(self.card,repo=REPO,pack_dir=self.pack_dir,candidates=[candidate],fetcher=lambda url:(("Complete list as of 2007-12-31 " + "test evidence "*30).encode(),url,"text/plain"))
        self.pack=self.pack_dir/"manifest.json"
    def tearDown(self): self.tmp.cleanup()
    def old_attempt(self):
        # A historical packet fixture; no CLI/model call is made.
        old=emit_job(self.ctx,"S1","A",jobs_root=self.jobs)
        from scripts.l2_pipeline.agents.jobs import archive_outputs
        out=Path(old.output_dir)
        write_once(out/"DISPATCH_ONCE.json",{"job_sha256":old.job_sha256})
        write_once(out/"DISPATCH_RESULT.json",{"status":"FAILED_NO_RETRY","exit_code":2,"job_path":old.job_path,"role":"A"})
        (out/"dispatch.log").write_text("error: unexpected argument '--search' found\n")
        archive_outputs(Path(old.job_path),"cli_parse_failure","Test fixture")
        job=emit_job(self.ctx,"S1","A",jobs_root=self.jobs,attempt_id="attempt2")
        rows=[dict(tuple_row(str(i)),status="unknown",start_period=None,reason="Blank catalogue") for i in range(10)]
        write_once(Path(job.output_path),{"design_id":job.design_id,"role":"A","source_sha256":job.contract["source_sha256"],"records":rows,"blindness_attestation":{"other_submissions_read":False,"outcome_associations_read":False}})
        return job
    def test_source_repair_preserves_old_output_and_promotes_only_in_fresh_workspace(self):
        old=self.old_attempt();before=Path(old.output_path).read_bytes()
        # Simulate the old collector's canonical promotion without replacing it.
        write_once(Path(old.promote_path),json.loads(before))
        with self.assertRaisesRegex(ValueError,"fresh work directory"):
            emit_job(self.ctx,"S1","A",jobs_root=self.jobs,attempt_id="attempt3",source_pack_path=self.pack)
        fresh=StageContext(self.card,self.root/"attempt3_work",REPO)
        job=emit_job(fresh,"S1","A",jobs_root=self.jobs,attempt_id="attempt3",source_pack_path=self.pack)
        self.assertEqual(load_job(Path(job.job_path)),job)
        self.assertNotEqual(job.promote_path,old.promote_path)
        self.assertNotIn("Blank catalogue",Path(job.job_path).read_text())
        write_once(Path(job.output_path),{"design_id":job.design_id,"role":"A","source_sha256":job.contract["source_sha256"],"records":[tuple_row(str(i)) for i in range(10)],"blindness_attestation":{"other_submissions_read":False,"outcome_associations_read":False}})
        self.assertTrue(collect_job(job.job_path)["validated"])
        self.assertEqual(Path(old.output_path).read_bytes(),before)
    def test_catalogue_refused_before_job_emission(self):
        c=copy.deepcopy(self.card.payload);c["sources"][0]["complete_list"]=False
        from scripts.l2_pipeline.contracts import DesignCard
        ctx=StageContext(DesignCard.from_mapping(c,self.card.path),self.root/"new",REPO)
        with self.assertRaisesRegex(ValueError,"source pack incomplete"): emit_job(ctx,"S1","A",jobs_root=self.jobs)
        self.assertFalse((self.jobs/"S1").exists())
    def test_pack_bound_job_search_blindness_and_archive_tamper(self):
        job=emit_job(self.ctx,"S1","A",jobs_root=self.jobs,source_pack_path=self.pack)
        self.assertEqual(load_job(Path(job.job_path)),job)
        self.assertEqual(job.contract["source_sha256"],sha(self.pack))
        text=Path(job.job_path).read_text()
        self.assertIn("web search is mandatory",text.lower());self.assertIn("official statutes",text)
        self.assertIn("Attempt evidence paths are hash provenance",text)
        archive=next(iter(job.contract["source_pack_inventory"]))
        Path(archive).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError,"source pack changed"): load_job(Path(job.job_path))

if __name__=="__main__": unittest.main()
