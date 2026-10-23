"""S1 quality rejects fast, blanket unknown outputs before promotion."""
from __future__ import annotations
import contextlib, io, json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts.l2_pipeline.agents.collect import collect_job, coder_quality, main
from scripts.l2_pipeline.storage import sha, write_once


class CoderQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.original=self.root/"attempt"/"submission.json"
        self.job=SimpleNamespace(stage="S1",design_id="fixture",role="A",job_path="fixture.md",
            job_sha256="jobhash",output_path=str(self.original),promote_path=str(self.root/"canonical"/"submission.json"),
            contract={"source_sha256":"sourcehash","mechanism":"one","manual":{"universe":["AL","AK","AZ","AR"]}})
    def tearDown(self): self.tmp.cleanup()
    def rows(self,known):
        return [{"unit":u,"status":"treated" if i<known else "unknown",
                 "start_period":2001 if i<known else None,"mechanism":"one","locator":"table1",
                 "reason":"No information in embedded catalog"}
                for i,u in enumerate(self.job.contract["manual"]["universe"])]
    def put(self,rows):
        write_once(self.original,{"design_id":"fixture","role":"A","source_sha256":"sourcehash","records":rows,
                   "blindness_attestation":{"other_submissions_read":False,"outcome_associations_read":False}})
    def collect(self):
        with patch("scripts.l2_pipeline.agents.collect.load_job",return_value=self.job):
            return collect_job("fixture.md")
    def pack(self,complete):
        manifest=self.root/"manifest.json";write_once(manifest,{"complete_list_complete":complete})
        self.job.contract["source_pack"]={"manifest_path":str(manifest),"manifest_sha256":sha(manifest),
                                           "complete_list_complete":complete}
    def blockers(self,rows):
        details={"AL":"AL official code points to a repealed clause but session law archive 2001 is unavailable",
                 "AK":"AK agency FAQ and statute disagree on initial enforcement year 2002",
                 "AZ":"AZ code version has a missing commencement section 2003",
                 "AR":"AR legislature index omits the effective-date page of Act 14"}
        for row in rows:
            if row["status"]=="unknown":
                u=row["unit"];row["reason"]=details[u]
                row["blocker"]={"unit":u,"detail":details[u],"official_locator":f"https://{u.lower()}.gov/statutes/14",
                                "search_query":f"{u} policy session law effective date"}
    def test_blanket_unknown_rejected_without_canonical_promotion(self):
        self.put(self.rows(0));receipt=self.collect()
        self.assertEqual(receipt["status"],"INSUFFICIENT_WORK");self.assertFalse(receipt["validated"])
        self.assertEqual(receipt["quality_gate"]["known_share"],0)
        self.assertTrue((self.original.parent/"QUALITY_GATE.json").is_file())
        self.assertFalse(Path(self.job.promote_path).exists())
        self.assertFalse((self.original.parent/"COLLECTION.json").exists())
        self.assertEqual(self.collect(),receipt)
    def test_exact_floor_passes_and_records_measured_time(self):
        self.put(self.rows(2));write_once(self.original.parent/"DISPATCH_RESULT.json",{"elapsed_seconds":127.5})
        receipt=self.collect();self.assertTrue(receipt["validated"])
        self.assertEqual(receipt["quality_gate"]["known_share"],.5)
        self.assertEqual(receipt["quality_gate"]["elapsed_seconds"],127.5)
        self.assertEqual(receipt["quality_gate"]["time_source"],"host_dispatch")
        self.assertEqual(self.collect(),receipt)
    def test_complete_pack_cannot_excuse_low_coverage(self):
        self.pack(True);rows=self.rows(1);self.blockers(rows);self.put(rows)
        self.assertEqual(self.collect()["status"],"INSUFFICIENT_WORK")
    def test_hash_bound_incomplete_pack_with_distinct_unit_evidence_passes(self):
        self.pack(False);rows=self.rows(1);self.blockers(rows);self.put(rows)
        receipt=self.collect();self.assertTrue(receipt["validated"])
        self.assertTrue(receipt["quality_gate"]["low_share_exception"])
    def test_current_stock_history_gap_can_excuse_only_specific_low_coverage(self):
        manifest=self.root/"manifest.json"
        write_once(manifest,{"complete_list_complete":True,"historical_coverage_complete":False,"missing_units":[]})
        self.job.contract["source_pack"]={"manifest_path":str(manifest),"manifest_sha256":sha(manifest),"complete_list_complete":True}
        rows=self.rows(1);self.blockers(rows);self.put(rows)
        self.assertTrue(self.collect()["quality_gate"]["low_share_exception"])
    def test_known_share_floor_does_not_excuse_blank_unknown_blockers_in_new_pack_jobs(self):
        self.pack(True);self.put(self.rows(2))
        with self.assertRaisesRegex(ValueError,"unit-specific blocker"): self.collect()
    def test_generic_blocker_unit_mismatch_or_absent_search_cannot_excuse(self):
        self.pack(False)
        for defect in ("copied","unit","query","locator"):
            with self.subTest(defect=defect):
                rows=self.rows(1);self.blockers(rows)
                if defect=="copied":
                    for row in rows[1:]: row["blocker"]["detail"]=row["unit"]+" catalog contains no dates"
                elif defect=="unit":rows[1]["blocker"]["unit"]="AL"
                elif defect=="query":rows[1]["blocker"]["search_query"]="policy"
                else:rows[1]["blocker"]["official_locator"]="embedded catalog"
                self.assertEqual(coder_quality(self.job,rows,self.original)["status"],"INSUFFICIENT_WORK")
    def test_pack_hash_and_completeness_tamper_refused(self):
        self.pack(False);self.job.contract["source_pack"]["complete_list_complete"]=True
        with self.assertRaisesRegex(ValueError,"binding changed"):coder_quality(self.job,self.rows(0),self.original)
        self.job.contract["source_pack"]["complete_list_complete"]=False
        (self.root/"manifest.json").write_text('{}')
        with self.assertRaisesRegex(ValueError,"manifest changed"):coder_quality(self.job,self.rows(0),self.original)
    def test_legacy_time_is_marked_estimate_and_invalid_host_time_refused(self):
        self.put(self.rows(2));write_once(self.original.parent/"DISPATCH_ONCE.json",{})
        result=self.original.parent/"DISPATCH_RESULT.json";write_once(result,{"status":"COMPLETED"})
        quality=coder_quality(self.job,self.rows(2),self.original)
        self.assertIn("estimate",quality["time_source"]);self.assertGreaterEqual(quality["elapsed_seconds"],0)
        result.write_text('{"elapsed_seconds":-1}')
        with self.assertRaisesRegex(ValueError,"elapsed time"):coder_quality(self.job,self.rows(2),self.original)
    def test_cli_nonzero_for_insufficient_work(self):
        self.put(self.rows(0))
        with patch("scripts.l2_pipeline.agents.collect.load_job",return_value=self.job),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--job","fixture.md"]),1)
    def test_blindness_is_checked_before_quality(self):
        self.put(self.rows(0));value=json.loads(self.original.read_text())
        value["blindness_attestation"]["other_submissions_read"]=True;self.original.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,"blindness"):self.collect()
        self.assertFalse((self.original.parent/"QUALITY_GATE.json").exists())


if __name__=="__main__":unittest.main()
