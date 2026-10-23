"""Invariant checks on data/l3_meta/l2_set_live.json (stdlib only; the file checks skip until build_live_set.py has run).

Run:  python3 -B scripts/l3_meta/test_build_live_set.py
"""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PATH = ROOT / "data/l3_meta/l2_set_live.json"
sys.path.insert(0, str(HERE))
import build_live_set as B  # noqa: E402  (pure functions only; importing does not touch any data file)


class GateRules(unittest.TestCase):
    def test_reason_to_gate(self):
        cases = {
            "S3 Disposition.STOPPED: Registered support insufficient": "support",
            "S2 Disposition.PARKED: No local public files declared in outcome.paths": "connector",
            "S0 Disposition.PARKED: first-order outcome unavailable": "connector",
            "V6.3 PARK: no same-construct public series for payroll_jobs in CURRENT V8": "connector",
            "STOPPED_PRE_RUN: RI199/RI999 feasibility below 90%": "ri",
            "V7 retained treated below minimum; Fresh V3 ten-treated audit unavailable after prior-sample exclusions": "history",
            "§7 contract replacement exhausted: V9 source id/URL invalid or duplicate": "history",
            "CONTRACT_EXHAUSTED: history universe; both attempts Codex, no C62 Sonnet exception": "history",
            "Fresh V3 ten-treated audit unavailable after prior-sample exclusions": "fresh",
            "ValueError: V3.1 fresh audit did not pass": "v3",
            "S0 Disposition.PARKED: first-order outcome registration incomplete": "other",
            "INSUFFICIENT_WORK: complete independent state history required": "insufficient_work",
        }
        for reason, gate in cases.items():
            self.assertEqual(B.classify_reason(reason), gate, reason)

    def test_generic_c43_text_uses_c53_category_and_unknown_is_unclassified(self):
        generic = "C43 metadata/support/fresh gates remain binding"
        self.assertEqual(B.classify_reason(generic, "fresh audit pool"), "fresh")
        self.assertEqual(B.classify_reason(generic, "support"), "support")
        self.assertIsNone(B.classify_reason(generic, None))
        self.assertIsNone(B.classify_reason("something new nobody has ruled on"))


class F1Fields(unittest.TestCase):
    def test_read_f1_absent_and_present(self):
        import tempfile
        self.assertEqual(B.read_f1(None), (None, None, None))
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "L3_EXPORT_DATA.json"
            p.write_text(json.dumps({"l3_export_v2": {"first_order": {"purpose_direction": None}}}))
            self.assertEqual(B.read_f1(p), (None, None, None))
            ev = {"codings": [{"recorded_at": "2026-10-12T12:00:00Z"}, {"recorded_at": "2026-10-13T12:00:00Z"}]}
            p.write_text(json.dumps({"l3_export_v2": {"first_order": {"purpose_direction": -1, "purpose_direction_evidence": ev}}}))
            self.assertEqual(B.read_f1(p), (-1, "2026-10-13T12:00:00Z", None))
            p.write_text(json.dumps({"l3_export_v2": {"first_order": {"purpose_direction": 0, "purpose_direction_coded_at": "X"}}}))
            self.assertEqual(B.read_f1(p)[:2], (0, "X"))

    def test_epoch(self):
        self.assertEqual(B.derive_epoch("2026-10-12T12:00:00Z", "2026-10-13T00:00:00Z", None), "prospective")
        self.assertEqual(B.derive_epoch("2026-10-14T12:00:00Z", "2026-10-13T00:00:00Z", None), "retrospective")
        self.assertIsNone(B.derive_epoch(None, "2026-10-13T00:00:00Z", None))
        self.assertEqual(B.derive_epoch(None, None, "retrospective"), "retrospective")


@unittest.skipUnless(PATH.exists(), "run scripts/l3_meta/build_live_set.py first")
class LiveSetInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v = json.loads(PATH.read_text())
        cls.designs = cls.v["designs"]

    def test_ids_unique(self):
        ids = [d["design_id"] for d in self.designs]
        self.assertEqual(len(ids), len(set(ids)))

    def test_counts_add_up(self):
        c, designs = self.v["counts"], self.designs
        self.assertEqual(c["processed"], len(designs))
        self.assertEqual(c["processed"], c["estimated"] + c["not_evaluable"])
        self.assertEqual(c["estimated"], sum(d["status"] == "estimated" for d in designs))
        self.assertEqual(c["not_evaluable"], sum(d["status"] == "not_evaluable" for d in designs))
        self.assertEqual(set(d["status"] for d in designs), {"estimated", "not_evaluable"})
        self.assertEqual(sum(c["by_gate"].values()), c["not_evaluable"])
        for gate, n in c["by_gate"].items():
            self.assertIn(gate, B.GATES)
            self.assertEqual(n, sum(d["gate"] == gate for d in designs), gate)
        self.assertEqual(sum(c["by_source"].values()), c["processed"])
        self.assertEqual(c["pipeline_only"]["processed"], c["pipeline_only"]["estimated"] + c["pipeline_only"]["not_evaluable"])

    def test_estimated_have_a_point_and_no_gate_unestimated_have_a_gate(self):
        for d in self.designs:
            if d["status"] == "estimated":
                self.assertIsInstance(d["point_estimate"], (int, float), d["design_id"])
                self.assertIsNone(d["gate"], d["design_id"])
            else:
                self.assertIn(d["gate"], B.GATES, d["design_id"])
                self.assertIsNone(d["point_estimate"], d["design_id"])
                self.assertTrue(d["gate_reason"], d["design_id"])

    def test_nothing_unclassified_or_counted_twice(self):
        counted = {d["design_id"] for d in self.designs}
        self.assertEqual(self.v["unclassified"], [], "designs the rule could not classify")
        for kind, rows in self.v["excluded_not_counted"].items():
            for r in rows:
                self.assertNotIn(r["design_id"], counted, f"{r['design_id']} is both excluded ({kind}) and counted")

    def test_reconciliation_arithmetic_against_1011_snapshot(self):
        r = self.v["reconciliation_vs_20261011"]
        b, live = r["baseline"], r["live"]
        self.assertEqual((b["processed"], b["estimated"], b["not_evaluable"]), (125, 48, 77))
        self.assertEqual(live["processed"], self.v["counts"]["processed"])
        self.assertEqual(b["processed"] + len(r["added_estimated"]) + len(r["added_not_evaluable"])
                         - len(r["removed_since_baseline"]), live["processed"])


if __name__ == "__main__":
    unittest.main()
