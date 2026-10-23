"""Unit checks for build_ex1.py (synthetic inputs only; no L2 file is read or written).

Run:  .venv_did/bin/python -B scripts/l3_meta/test_build_ex1.py
"""
import hashlib
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_ex1 as E  # noqa: E402  (importing touches no data file)


def sha(b):
    return hashlib.sha256(b).hexdigest()


class Statistics(unittest.TestCase):
    def test_partial_spearman_matches_manual(self):
        x = [1.0, 3.0, 2.0, 5.0, 4.0, 7.0, 6.0]
        y = [2.0, 1.0, 4.0, 3.0, 7.0, 5.0, 6.0]
        z = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        # manual: ranks are the values themselves; residualise on z (rank = z)
        rx, ry, rz = (np.array(v) for v in (x, y, z))
        X = np.column_stack([np.ones(7), rz])
        ex = rx - X @ np.linalg.lstsq(X, rx, rcond=None)[0]
        ey = ry - X @ np.linalg.lstsq(X, ry, rcond=None)[0]
        self.assertAlmostEqual(E.partial_spearman(x, y, z), float(np.corrcoef(ex, ey)[0, 1]), places=12)

    def test_partial_spearman_invariant_to_monotone_transform_and_ties(self):
        x = [1.0, 3.0, 3.0, 5.0, 4.0, 7.0]
        y = [2.0, 1.0, 4.0, 3.0, 7.0, 5.0]
        z = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        a = E.partial_spearman(x, y, z)
        b = E.partial_spearman([math.exp(v) for v in x], [v ** 3 for v in y], [10 * v + 5 for v in z])
        self.assertAlmostEqual(a, b, places=12)

    def test_partial_spearman_undefined_cases(self):
        self.assertTrue(math.isnan(E.partial_spearman([1, 1, 1, 1], [1, 2, 3, 4], [1, 2, 3, 5])))
        self.assertTrue(math.isnan(E.partial_spearman([1, 2], [1, 2], [1, 2])))
        # x perfectly explained by z -> zero residual -> undefined
        self.assertTrue(math.isnan(E.partial_spearman([1, 2, 3, 4, 5], [5, 3, 4, 1, 2], [1, 2, 3, 4, 5])))

    def test_family_bootstrap_deterministic_and_bounded(self):
        flags = [1, 0, 0, 1, 0, 0, 0, 0]
        fam = ["a", "a", "b", "c", "d", "e", "f", "g"]
        r1 = E.family_bootstrap_proportion(flags, fam, B=500, seed=7)
        r2 = E.family_bootstrap_proportion(flags, fam, B=500, seed=7)
        self.assertTrue(np.array_equal(r1, r2))
        self.assertTrue(((r1 >= 0) & (r1 <= 1)).all())
        self.assertAlmostEqual(float(np.mean(r1)), 0.25, delta=0.04)
        one = E.family_bootstrap_proportion([1, 0, 1, 0], ["x"] * 4, B=50, seed=1)
        self.assertTrue(np.allclose(one, 0.5))

    def test_e4_summary_hides_p_when_many_draws_undefined(self):
        star = np.array([0.2] * 80 + [float("nan")] * 20)
        out = E.e4_summary(star, 0.1)
        self.assertEqual(out["B_defined"], 80)
        self.assertIsNone(out["p_value"])
        star = np.array([0.5, -0.1, 0.3, 0.4, 0.6, 0.7, 0.2, 0.1, 0.35, 0.45])
        out = E.e4_summary(star, 0.3)
        self.assertEqual(out["B_defined"], 10)
        self.assertAlmostEqual(out["p_value"], min(1.0, 2 * min((1 + 1) / 11, (1 + 9) / 11)))

    def test_e4_bootstrap_handles_degenerate_draws(self):
        disp = [0.1, 0.5, 0.3, 0.9]
        err = [0.2, 0.4, 0.1, 0.8]
        g = [1.0, 2.0, 3.0, 4.0]
        star = E.e4_pairs_bootstrap(disp, err, g, ["a", "b", "c", "d"], B=300, seed=[1, 2])
        self.assertEqual(star.size, 300)
        self.assertTrue(np.isnan(star).any())  # draws that repeat one family are undefined
        star2 = E.e4_pairs_bootstrap(disp, err, g, ["a", "b", "c", "d"], B=300, seed=[1, 2])
        self.assertTrue(np.array_equal(np.nan_to_num(star, nan=9), np.nan_to_num(star2, nan=9)))

    def test_brier_and_scores(self):
        slots = [{"p_positive": 0.8}, {"p_positive": 0.6}]
        self.assertAlmostEqual(E.brier_skill(slots, 1.0), 0.25 - ((0.2 ** 2 + 0.4 ** 2) / 2))
        self.assertAlmostEqual(E.brier_skill(slots, -1.0), 0.25 - ((0.8 ** 2 + 0.6 ** 2) / 2))
        sl = [{"center": 1.0, "interval": [-1, 2], "interval_level": 0.8}, {"center": 0.0, "interval": [0, 1], "interval_level": 0.8},
              {"center": -2.0, "interval": [-3, -1], "interval_level": 0.9}]
        sc = E.design_scores(0.5, sl, 2.0)
        self.assertAlmostEqual(sc["slot_hit_rate"], 1 / 3)           # only the first centre has the right sign; zero is a miss
        self.assertEqual(sc["median"], 0.0)
        self.assertEqual(sc["median_hit"], 0.0)                      # zero median is a miss
        self.assertEqual(sc["n_cov_slots"], 2)                       # the 0.9 interval is not scored
        self.assertAlmostEqual(sc["coverage80"], 1.0)
        # O: sign(m)(m-b)/s : (1)(0.5)/2 = 0.25; 0; (-1)(-2.5)/2 = 1.25
        self.assertAlmostEqual(sc["overstatement"], (0.25 + 0 + 1.25) / 3)
        self.assertEqual(sc["zero_centers"], 1)

    def test_summarize_scores_table(self):
        rows = [dict(E.design_scores(b, [{"center": m, "interval": [m - 1, m + 1], "interval_level": 0.8}] * 3, 1.0), b=b)
                for m, b in [(1.0, 2.0), (-1.0, -3.0), (1.0, -1.0), (-2.0, 1.0), (0.0, 1.0)]]
        out = E.summarize_scores(rows)
        t = out["median_vs_b_sign_table"]
        self.assertEqual((t["median_pos_b_pos"], t["median_neg_b_neg"], t["median_pos_b_neg"], t["median_neg_b_pos"], t["median_zero"]),
                         (1, 1, 1, 1, 1))
        self.assertEqual(out["median_hits"], 2)
        self.assertEqual(out["naive_always_negative_hits"], 2)


class Registry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write_run(self, name, att, status="COMPLETED", rows=None):
        d = self.root / name
        d.mkdir()
        res = {"design_id": "X", "status": status}
        if att is not None:
            res["registered_results"] = {"primary": {"result": {"overall": {"att": att, "se": 1.0}}}}
            res["noise_floor"] = {"sample_sd": 2.0, "absolute_cutoff": 4.0, "strictly_exceeds_cutoff": abs(att) > 4.0,
                                  "feasibility": {"support_pass": True, "feasible_draws": 999}}
        res["forecast_comparison"] = {"rows": rows or []}
        text = json.dumps(res)
        (d / "RESULT.json").write_text(text)
        return {"design_id": "X", "run_id": name, "status": status, "package_path": str(d), "result_path": str(d / "RESULT.json"),
                "result_sha256": sha(text.encode())}

    def test_first_certified_run_rules(self):
        failed = self.write_run("orig_failed", None, "FAILED_NO_RETRY")
        failed["study_estimated"] = False
        original = self.write_run("orig_done", 1.5)
        deviation = self.write_run("erpo_20261009_deviation002", -9.0)
        ch = E.run_chains({"runs": [failed, original, deviation]})["X"]
        self.assertEqual(ch["first"]["run"]["run_id"], "orig_done")
        self.assertEqual([c["run"]["run_id"] for c in ch["not_certified_before"]], ["orig_failed"])
        self.assertEqual([c["run"]["run_id"] for c in ch["later_certified"]], ["erpo_20261009_deviation002"])
        self.assertEqual(E.run_listing(ch["later_certified"][0])["reason_code"], "TIMING_CORRECTION")
        self.assertEqual(E.run_listing(ch["not_certified_before"][0])["reason_code"], "NOT_CERTIFIED")
        # only a failed run -> no certified run
        ch2 = E.run_chains({"runs": [failed]})["X"]
        self.assertIsNone(ch2["first"])

    def test_read_lock_slots_verifies_hash_and_scored_row(self):
        pk = self.root / "pkg"
        files, rows = {}, []
        for persona, point in (("A", 1.0), ("B", -2.0)):
            d = pk / "predictions" / persona
            d.mkdir(parents=True)
            sub = {"persona": persona, "point": point, "interval": [point - 1, point + 1], "interval_level": 0.8}
            text = json.dumps(sub)
            (d / "submission.json").write_text(text)
            files[f"predictions/{persona}/submission.json"] = sha(text.encode())
            rows.append({"persona": persona, "point": point, "interval": [point - 1, point + 1]})
        (pk / "PREDICTIONS_LOCK.json").write_text(json.dumps({"personas": ["A", "B"], "files": files}))
        led = E.read_lock_slots(pk, rows)
        self.assertTrue(led["verified"])
        self.assertFalse(led["three_slot_key"])
        # tamper with one submission: hash fails
        (pk / "predictions" / "A" / "submission.json").write_text(json.dumps({"persona": "A", "point": 9.0, "interval": [8, 10], "interval_level": 0.8}))
        self.assertFalse(E.read_lock_slots(pk, rows)["verified"])
        # a scored row that disagrees with the lock fails even when the hash is right
        rows[1]["point"] = 5.0
        (pk / "predictions" / "A" / "submission.json").write_text(json.dumps({"persona": "A", "point": 1.0, "interval": [0, 2], "interval_level": 0.8}))
        self.assertFalse(E.read_lock_slots(pk, rows)["verified"])

    def test_keyless_three_persona_lock_is_three_slot_and_sidecar_completes_it(self):
        """Q3 ruling (HUB_RULING_20261023_PARK_REOPEN.md section 4): keyless rebound listed as three-slot; N10-3 membership only via the hash-checked sidecar."""
        from unittest.mock import patch
        from scripts.l2_pipeline.forecast_slots import CARRY_NAME, build_carry
        from scripts.l2_pipeline.predictions import import_unchanged_predictions, lock_predictions
        from scripts.l2_pipeline.storage import ensure_once
        from scripts.l2_pipeline.tests import test_t277_independence_carry_20261023 as T
        three = {p: "worker_" + p for p in T.PERSONAS}
        one = {p: "worker_one" for p in T.PERSONAS}
        rows = [{"persona": p, "point": T.POINTS[p], "interval": [-10, 10]} for p in T.PERSONAS]
        recs = []
        for name, workers in (("three", three), ("one", one)):
            old, new = self.root / name / "old", self.root / name / "new"
            T.make_source(old, workers, keys="old")
            ensure_once(new / "SPEC.json", dict(json.loads((old / "SPEC.json").read_text()), version="new"))
            with patch("scripts.l2_pipeline.forecast_slots.carried_independence", return_value=None):      # a rebound as written before the fix
                import_unchanged_predictions(old, new, T.PERSONAS)
            lock_predictions(new, T.PERSONAS, "invented", T.sha(new / "SPEC.json"))
            led = E.read_lock_slots(new, rows)
            self.assertTrue(led["three_slot_key"])                                    # len(personas) == 3 fallback; the lock has no forecast_slots
            self.assertTrue(led["verified"])
            self.assertEqual((led["independence_from"], led["n10_three_slot_eligible"], led["n10_old_rule"]), (None, False, False))
            ensure_once(new / CARRY_NAME, build_carry(new))
            led = E.read_lock_slots(new, rows)
            self.assertEqual(led["independence_from"], "sidecar")
            self.assertEqual(led["n10_three_slot_eligible"], workers is three)       # 8 one-worker rows stay out, rows 9-10 style three-worker rows enter
            self.assertFalse(led["n10_three_slot_lock_only"])
            recs.append({"design_id": name, "status": "estimated", "ledger": led})
            tampered = led["sidecar_problems"]
            self.assertEqual(tampered, [])
        # a lock with its own old-style keys and ONE worker id: the old reader rule says yes, the Q3 rule no (3c)
        keyed = self.root / "keyed"
        T.make_source(keyed, one, keys="old")
        led = E.read_lock_slots(keyed, rows)
        self.assertTrue(led["three_slot_key"] and led["n10_old_rule"] and not led["n10_three_slot_eligible"])
        recs.append({"design_id": "keyed_one", "status": "estimated", "ledger": led})
        m = E.n10_three_slot_membership(recs)
        self.assertEqual(m["estimated_three_slot_designs"], 3)
        self.assertEqual(m["before_ruling_reader_rule"]["designs"], ["keyed_one"])
        self.assertEqual(m["after_ruling_lock_keys_only"]["n"], 0)
        self.assertEqual(m["after_ruling_with_sidecar"]["designs"], ["three"])
        self.assertEqual(m["sidecar_designs"], ["one", "three"])
        # a moved hash drops the design back out
        (self.root / "three" / "new" / "PREDICTIONS_LOCK.json").write_text((self.root / "three" / "new" / "PREDICTIONS_LOCK.json").read_text() + " ")
        led = E.read_lock_slots(self.root / "three" / "new", rows)
        self.assertEqual(led["independence_from"], None)
        self.assertTrue(led["sidecar_problems"])
        self.assertFalse(led["n10_three_slot_eligible"])


class Questions(unittest.TestCase):
    @staticmethod
    def rec(i, b, cutoff, sd=1.0, fam=None, status="estimated", gate=None, group="registry", domain="labor"):
        return {"design_id": f"d{i}", "domain": domain, "policy_family": fam or f"f{i}", "source_group": "registry_terminal", "status": status,
                "gate": gate, "reportable": True, "b": b, "noise_sd": sd, "noise_cutoff": cutoff, "support_pass": True,
                "recorded_exceed": abs(b) > cutoff if cutoff else None, "group3": group, "outcome_order": "unknown"}

    def test_has_noise_line(self):
        self.assertTrue(E.has_noise_line(self.rec(1, 1.0, 2.0)))
        self.assertFalse(E.has_noise_line(self.rec(1, 1.0, None, sd=None)))
        r = self.rec(1, 1.0, 2.0)
        r["support_pass"] = False
        self.assertFalse(E.has_noise_line(r))
        r["support_pass"] = None  # archive rows carry no feasibility block: not false
        self.assertTrue(E.has_noise_line(r))

    def test_question_1_counts_and_bounds(self):
        recs = [self.rec(1, 3.0, 2.0), self.rec(2, -1.0, 2.0), self.rec(3, -2.0, 2.0), self.rec(4, 0.5, 2.0),
                {"design_id": "n1", "domain": "labor", "policy_family": "n1", "source_group": "autopilot_park", "status": "not_evaluable",
                 "gate": "support", "reportable": False},
                dict(self.rec(5, 1.0, None, sd=None), status="estimated")]
        out = E.question_1(recs, D=6)
        self.assertEqual((out["n"], out["k"], out["u"]), (4, 1, 2))   # |-2| is not strictly greater than 2
        self.assertAlmostEqual(out["full_roster_bounds"][0], 1 / 6)
        self.assertAlmostEqual(out["full_roster_bounds"][1], 3 / 6)
        self.assertEqual(out["coverage_codes"], {"EVAL": 4, "NO_NOISE_REF": 1, "STOP_PRE_RUN:support": 1})
        self.assertEqual(out["recorded_flag_mismatches"], [])
        self.assertIsNone(out["p_value"])

    def test_three_slot_variant_is_empty_without_sets(self):
        recs = [self.rec(1, 3.0, 2.0)]
        out = E.three_slot_blank("Q3-3", recs, 1, [])
        self.assertEqual(out["n"], 0)
        self.assertIn("FIELD_MISSING (no three-slot set)", out["coverage_codes"])

    def test_n10_result_needs_four_designs(self):
        rows = [{"policy_family": "a", "disp": 1.0, "err": 1.0, "g": 1.0}] * 3
        self.assertIsNone(E.n10_result(rows, [1, 2])["r10"])


if __name__ == "__main__":
    unittest.main()
