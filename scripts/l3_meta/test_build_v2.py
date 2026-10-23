"""Invariant checks on data/l3_meta/l2_set_v2.json (stdlib only; skipped until the builder has been run)."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "data/l3_meta/l2_set_v2.json"


@unittest.skipUnless(PATH.exists(), "run scripts/l3_meta/build_v2.py first")
class V2Invariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v = json.loads(PATH.read_text())

    def test_labels_and_no_tests(self):
        v = self.v
        self.assertEqual(v["evidence_status"], "DEVELOPMENT")
        self.assertEqual(v["confirmatory_design_n"], 0)
        self.assertEqual(v["scientific_primary_tests_computed"], 0)
        for q in v["questions"].values():
            self.assertIsNone(q.get("p_value", None))
        for s in v["sentence_decisions"].values():
            self.assertFalse(s["true_template_licensed"] or s["false_template_licensed"])

    def test_each_design_counted_once(self):
        ids = [d["design_id"] for d in self.v["designs"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), self.v["counts"]["design_D"])
        row_designs = {r["design_id"] for r in self.v["rows"]}
        self.assertEqual(set(ids), row_designs)
        # superseded originals never appear as a counted run
        counted = {d.get("latest_run_id") for d in self.v["designs"]}
        for s in self.v["superseded_runs"]:
            self.assertNotIn(s["run_id"], counted)

    def test_registry_summary_matches(self):
        reg = [d for d in self.v["designs"] if d["source_group"] == "pipeline" and d["origin"] != "terminal S3 support stop (carried from v1; not in registry)"]
        self.assertEqual(len(reg), 29)
        st = {}
        for d in reg:
            st[d["status"]] = st.get(d["status"], 0) + 1
        self.assertEqual(st, {"COMPLETED": 24, "STOPPED": 3, "PRE_RUN_STOP": 2})

    def test_q1_recomputes_from_rows(self):
        rows = self.v["rows"]
        elig = [r for r in rows if r["Q1_descriptive_eligible"]]
        k = sum(1 for r in elig if r["noise_exceedance"])
        q1 = self.v["questions"]["Q1"]
        self.assertEqual((q1["k"], q1["n"], q1["D"]), (k, len(elig), self.v["counts"]["design_D"]))
        for r in elig:
            self.assertEqual(r["noise_exceedance"], abs(r["b"]) > r["noise"]["absolute_cutoff"])
        lo, hi = q1["full_roster_bounds"]
        self.assertAlmostEqual(lo, k / q1["D"])
        self.assertAlmostEqual(hi, (k + q1["u"]) / q1["D"])

    def test_forecast_locks_verified_for_every_scored_slot(self):
        for r in self.v["rows"]:
            for f in r.get("forecast_slots") or []:
                if r["source_group"] == "pipeline":
                    self.assertTrue(f["lock_verified"], r["design_id"])

    def test_q2_q4_unavailable(self):
        self.assertEqual(self.v["questions"]["Q2"]["eligible_n"], 0)
        self.assertEqual(self.v["questions"]["Q4"]["eligible_n"], 0)
        self.assertEqual(self.v["questions"]["Q5"]["age60_eligible_n"], 0)


if __name__ == "__main__":
    unittest.main()
