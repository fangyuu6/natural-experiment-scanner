#!/usr/bin/env python3
"""Unit checks of build_q2r.py on SYNTHETIC inputs only (no replication file, no real estimate).

Run:  python3 -I -B scripts/l3_meta/test_build_q2r.py      (standard library only)
"""
from __future__ import annotations

import json
import math
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_q2r as B  # noqa: E402


def paper(pid, stage="matched", p=1.0, se=0.1, o=1.0, grade="TIGHT", reasons=(), rung="R1a", gate=(), verified=True, year=2010, spec="partial",
          independent=True, frozen=True, eligible=True, tfbe=True, tol=None, with_primary=True):
    if not with_primary:
        return {"paper_id": pid, "slug": pid, "year": year, "venue": "J", "stage": stage, "primary": None, "runs": []}
    pr = {"code_origin": "independent" if independent else "port_of_author_code", "frozen": frozen, "eligible_for_primary": eligible,
          "target_fixed_before_estimation": tfbe, "rung": rung, "ours": {"point": o, "se": 0.1}, "published": {"point": p, "se": se},
          "grade": grade, "grade_numeric": grade, "gate_reasons": list(gate), "target_verified": verified, "spec_completeness": spec,
          "numeric_checks": {"grade": grade, "reason_codes": list(reasons), "absolute_tolerance": tol if tol is not None else B.tolerance_at(p, se, 0.10)},
          "target": {"point": p, "se": se}}
    return {"paper_id": pid, "slug": pid, "year": year, "venue": "J", "stage": stage, "primary": pr, "primary_run_id": pid + "/r",
            "runs": [{"run_id": pid + "/r", "ours": {"point": o}, "code_origin": "independent"}]}


def cat(fam="fam", sources=("B1",), vs=None):
    return {"policy_family": fam, "sources": list(sources), "venue_strong": vs}


class GapTests(unittest.TestCase):
    def test_tolerance_matches_comparator_examples(self):
        self.assertEqual(B.tolerance_at(0.142, 0.017, 0.10), 0.0142)         # recorded tolerance of a confirmed paper
        self.assertEqual(B.tolerance_at(-8.0, None, 0.10), 0.8)               # no SE: 10% of |p|
        self.assertEqual(B.tolerance_at(0.02, 0.05, 0.10), 0.005)             # SE above |p| widens the base
        self.assertEqual(B.tolerance_at(1.0, -3.0, 0.10), 0.1)                # a negative SE counts as absent
        self.assertEqual(B.tolerance_at(1.0, None, 0), 0.0)

    def test_band_is_inclusive_and_decimal(self):
        self.assertEqual(B.bin_at(1.0, None, 0.9, 0.10), 0)                   # exactly 10%: within
        self.assertEqual(B.bin_at(1.0, None, 0.8999, 0.10), 1)
        self.assertEqual(B.bin_at(1.0, None, 1.1, 0.10), 0)
        self.assertEqual(B.bin_at(1.0, None, 1.1001, 0.10), -1)
        self.assertEqual(B.bin_at(0.3, None, 0.27, 0.10), 0)                  # float noise must not push 0.27 vs 0.3 out of the band
        self.assertEqual(B.bin_at(1.0, None, 1.0, 0), 0)
        self.assertEqual(B.bin_at(1.0, None, 0.999999, 0), 1)

    def test_orientation_for_negative_published(self):
        x = B.pair_numbers(-2.0, None, -1.0)
        self.assertEqual((x["a"], x["P"], x["O"], x["u"]), (-1, 2.0, 1.0, 1))
        self.assertAlmostEqual(x["d"], 1.0)
        self.assertEqual(x["c"], 1)                                            # |published| larger
        self.assertAlmostEqual(x["L"], math.log(2.0))
        y = B.pair_numbers(-1.0, None, -1.5)
        self.assertEqual((y["c"], y["d_sign"]), (-1, -1))                      # ours larger in magnitude
        self.assertAlmostEqual(y["L"], math.log(1.0 / 1.5))

    def test_sign_flip_and_zero_have_no_log_ratio_but_count(self):
        x = B.pair_numbers(0.5, 0.0, -0.1)
        self.assertEqual((x["u"], x["L"], x["c"]), (0, None, 1))
        z = B.pair_numbers(0.5, 0.0, 0.0)
        self.assertEqual((z["u"], z["L"], z["c"]), (0, None, 1))
        w = B.pair_numbers(0.01, 1.0, -0.05)                                   # SE base 1.0: band 0.1, gap 0.06 -> within
        self.assertEqual((w["u"], w["c"]), (0, 0))

    def test_relative_gap_scale_is_the_tolerance_base(self):
        x = B.pair_numbers(0.02, 0.05, 0.015)
        self.assertAlmostEqual(x["g"], 0.005 / 0.05)
        self.assertEqual(x["c"], 0)                                           # |g| = 0.10 -> boundary, within

    def test_log_ratio_sign_convention(self):
        x = B.pair_numbers(2.0, None, 1.0)
        self.assertAlmostEqual(x["L"], math.log(2.0))
        self.assertAlmostEqual(math.exp(-x["L"]), 0.5)                         # ours over published


class ClassifyTests(unittest.TestCase):
    def test_codes_in_order(self):
        self.assertEqual(B.classify(paper("a")), "EVAL")
        self.assertEqual(B.classify(paper("a", independent=False)), "PRIMARY_INELIGIBLE")
        self.assertEqual(B.classify(paper("a", frozen=False)), "PRIMARY_INELIGIBLE")
        self.assertEqual(B.classify(paper("a", tfbe=False)), "PRIMARY_INELIGIBLE")
        self.assertEqual(B.classify(paper("a", rung="R0")), "RUNG_NOT_COUNTED")
        self.assertEqual(B.classify(paper("a", rung="R2")), "RUNG_NOT_COUNTED")
        self.assertEqual(B.classify(paper("a", o=None)), "NO_NUMBER")
        self.assertEqual(B.classify(paper("a", p=0.0)), "ZERO_PUBLISHED")
        self.assertEqual(B.classify(paper("a", grade=None)), "GRADE_MISSING")

    def test_not_comparable_rule(self):
        ok = paper("a", p=1.0, o=0.5, grade="NOT_COMPARABLE", reasons=[B.NC_OK_REASON])
        self.assertEqual(B.classify(ok), "EVAL")
        other = paper("a", p=1.0, o=0.5, grade="NOT_COMPARABLE", reasons=[B.NC_OK_REASON, "timing_unverified"])
        self.assertEqual(B.classify(other), "NC_OTHER_REASON")
        other2 = paper("a", p=1.0, o=0.5, grade="NOT_COMPARABLE", reasons=["point_unavailable"])
        self.assertEqual(B.classify(other2), "NC_OTHER_REASON")
        none = paper("a", p=1.0, o=0.5, grade="NOT_COMPARABLE", reasons=[])
        self.assertEqual(B.classify(none), "NC_OTHER_REASON")                  # NOT_COMPARABLE without the one accepted reason is out

    def test_other_grades_enter_whatever_the_gates(self):
        for g in ("TIGHT", "DIRECTIONAL", "NOT_CONSISTENT"):
            self.assertEqual(B.classify(paper("a", grade=g, gate=["timing_identity_unverified", "spec_partial", "version_target_unverified"])), "EVAL")

    def test_no_primary_codes_follow_the_stage(self):
        for st, code in [("closed_restricted", "CLOSED_TRIAGE"), ("closed_duplicate", "CLOSED_TRIAGE"), ("waiting_login", "WAITING_ACCESS"),
                         ("waiting_journal_table", "WAITING_ACCESS"), ("not_started", "NOT_STARTED"), ("in_progress", "IN_PROGRESS"),
                         ("finished_uncounted", "FINISHED_UNCOUNTED"), ("weird", "NO_PRIMARY_OTHER")]:
            self.assertEqual(B.classify(paper("a", stage=st, with_primary=False)), code)

    def test_every_code_is_listed(self):
        self.assertEqual(len(B.CODES), len(set(B.CODES)))


class CovariateTests(unittest.TestCase):
    def test_family_field_journal_age(self):
        self.assertEqual(B.family_of({"paper_id": "x"}, {"policy_family": "f"}), "f")
        self.assertEqual(B.family_of({"paper_id": "x"}, {"policy_family": None}), "__own__:x")
        self.assertEqual(B.family_of({"paper_id": "x"}, None), "__own__:x")
        self.assertEqual(B.field_of(["B3", "GAP"]), "crime_guns_traffic")
        self.assertEqual(B.field_of(["GAP", "B2"]), "health")
        self.assertEqual(B.field_of(["GAP"]), "other")
        self.assertEqual(B.field_of([]), "other")
        self.assertEqual(B.journal_group("AER"), "top5")
        self.assertEqual(B.journal_group("JHE"), "other_listed")
        self.assertEqual(B.journal_group(None), "not_listed")
        self.assertEqual(B.age_group(2015), "published_2015_or_earlier")
        self.assertEqual(B.age_group(2016), "published_2016_or_later")
        self.assertEqual(B.age_group(None), "unknown")


class NumericsTests(unittest.TestCase):
    def test_quantile_type7(self):
        xs = [1.0, 2.0, 3.0, 4.0]
        self.assertAlmostEqual(B.quantile7(xs, 0.5), 2.5)
        self.assertAlmostEqual(B.quantile7(xs, 0.1), 1.3)
        self.assertEqual(B.quantile7(xs, 0.0), 1.0)
        self.assertEqual(B.quantile7(xs, 1.0), 4.0)
        self.assertEqual(B.quantile7([5.0], 0.9), 5.0)
        self.assertIsNone(B.quantile7([], 0.5))

    def test_exact_interval_known_values(self):
        lo, hi = B.cp_interval(9, 10)
        self.assertAlmostEqual(lo, 0.5550, places=3)
        self.assertAlmostEqual(hi, 0.9975, places=3)
        lo, hi = B.cp_interval(5, 10)
        self.assertAlmostEqual(lo, 0.1871, places=3)
        self.assertAlmostEqual(hi, 0.8129, places=3)
        lo, hi = B.cp_interval(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.3085, places=3)
        lo, hi = B.cp_interval(10, 10)
        self.assertEqual(hi, 1.0)
        self.assertAlmostEqual(lo, 0.6915, places=3)
        self.assertIsNone(B.cp_interval(0, 0))

    def test_sign_test(self):
        self.assertAlmostEqual(B.sign_test_p(9, 10), 22 / 1024)
        self.assertAlmostEqual(B.sign_test_p(1, 10), 22 / 1024)
        self.assertEqual(B.sign_test_p(5, 10), 1.0)
        self.assertEqual(B.sign_test_p(10, 10), 2 / 1024)
        self.assertIsNone(B.sign_test_p(0, 0))

    def test_planning_half_widths_shrink(self):
        w = B.planning_half_widths()
        self.assertEqual(list(w), ["10", "20", "40", "80"])
        self.assertTrue(w["10"] > w["20"] > w["40"] > w["80"])
        self.assertAlmostEqual(w["10"], (0.8129 - 0.1871) / 2, places=3)

    def test_percentile_ranks(self):
        vals = [float(i) for i in range(1, 10000)]                              # m = 9,999
        self.assertEqual(B.percentile_interval(vals), [250.0, 9750.0])
        self.assertEqual(B.percentile_interval([1.0, 2.0, 3.0]), [1.0, 3.0])     # ranks kept inside 1..m
        self.assertIsNone(B.percentile_interval([]))

    def test_floor_and_reading(self):
        self.assertTrue(B.floor_met(20, 20))
        self.assertFalse(B.floor_met(19, 20))
        self.assertFalse(B.floor_met(40, 19))
        self.assertEqual(B.reading([0.05, 0.3], 42, 27), "published larger")
        self.assertEqual(B.reading([-0.3, -0.05], 42, 27), "ours larger")
        self.assertEqual(B.reading([-0.1, 0.3], 42, 27), "no asymmetry seen")
        self.assertEqual(B.reading([0.0, 0.3], 42, 27), "no asymmetry seen")       # lower end must be strictly above 0
        self.assertEqual(B.reading([0.05, 0.3], 19, 27), "descriptive only")
        self.assertEqual(B.reading(None, 42, 27), "descriptive only")


def pairs_from(specs):
    """specs: list of (pid, family, p, se, o) -> pair rows through the real builder path."""
    out = []
    for pid, fam, p, se, o in specs:
        pa = paper(pid, p=p, se=se, o=o)
        out.append(B.build_pair(pa, cat(fam)))
    return out


class StatisticsTests(unittest.TestCase):
    def test_summary_bins_and_primary(self):
        # published larger by > 10% twice, within once, ours larger once; one sign flip counted as published larger
        ps = pairs_from([("a", "f1", 1.0, None, 0.5), ("b", "f1", 1.0, None, 0.7), ("c", "f2", 1.0, None, 1.05), ("d", "f3", 1.0, None, 1.5), ("e", "f4", 1.0, None, -0.2)])
        s = B.summarize(ps)
        self.assertEqual((s["n"], s["F"]), (5, 4))
        self.assertEqual(dict(s["bins"]), {"n_plus": 3, "n_zero": 1, "n_minus": 1})
        self.assertAlmostEqual(s["D"], (3 - 1) / 5)
        self.assertEqual((s["n_dec"], s["P_dec"]), (4, 0.75))
        self.assertEqual(s["sign_agreement"]["n_flip_or_zero"], 1)
        self.assertEqual(s["log_ratio"]["n"], 4)
        self.assertAlmostEqual(s["log_ratio"]["mean"], (math.log(2) + math.log(1 / 0.7) + math.log(1 / 1.05) + math.log(1 / 1.5)) / 4)
        self.assertEqual((s["strict"]["n_published_larger"], s["strict"]["n_ours_larger"], s["strict"]["n_exact_ties"]), (3, 2, 0))

    def test_band_curve_m0_is_strict_direction(self):
        ps = pairs_from([("a", "f1", 1.0, None, 0.99), ("b", "f2", 1.0, None, 1.01), ("c", "f3", 1.0, None, 1.0)])
        bc = {r["band_multiplier"]: r for r in B.band_curve(ps)}
        self.assertEqual((bc[0]["n_plus"], bc[0]["n_zero"], bc[0]["n_minus"]), (1, 1, 1))
        self.assertEqual((bc[0.1]["n_plus"], bc[0.1]["n_zero"], bc[0.1]["n_minus"]), (0, 3, 0))

    def test_empty_set_is_safe(self):
        s = B.analyze_set([])
        self.assertEqual((s["n"], s["F"], s["reading"]), (0, 0, "descriptive only"))
        self.assertIsNone(s["D"])

    def test_floor_gates_the_engine(self):
        below = pairs_from([(f"p{i}", f"f{i}", 1.0, None, 0.5) for i in range(19)])
        s = B.analyze_set(below)
        self.assertFalse(s["floor"]["met"])
        self.assertNotIn("bootstrap", s)
        self.assertEqual(s["reading"], "descriptive only")
        met = pairs_from([(f"p{i}", f"f{i}", 1.0, None, 0.5) for i in range(20)])
        s = B.analyze_set(met)
        self.assertTrue(s["floor"]["met"])
        self.assertEqual(s["bootstrap"]["D"]["interval"], [1.0, 1.0])
        self.assertEqual(s["reading"], "published larger")
        self.assertIn("cp95", s["direction_among_departures"])
        # 20 pairs but only 10 families: below the floor
        few = pairs_from([(f"p{i}", f"f{i % 10}", 1.0, None, 0.5) for i in range(20)])
        self.assertFalse(B.analyze_set(few)["floor"]["met"])

    def test_bootstrap_is_family_block_and_deterministic(self):
        ps = pairs_from([("a1", "A", 1.0, None, 0.5), ("a2", "A", 1.0, None, 0.5), ("b1", "B", 1.0, None, 1.5)])
        r1 = B.family_bootstrap(ps, B=500, seed=7)
        r2 = B.family_bootstrap(ps, B=500, seed=7)
        r3 = B.family_bootstrap(ps, B=500, seed=8)
        self.assertEqual(r1["D"], r2["D"])
        # only whole families can be drawn: pooled D is one of 1 (AA), 1/3 (AB or BA), -1 (BB)
        lo, hi = r1["D"]["interval"]
        self.assertTrue(lo >= -1.0 and hi <= 1.0)
        self.assertIn(round(lo, 6), (-1.0, round(1 / 3, 6), 1.0))
        self.assertIn(round(hi, 6), (-1.0, round(1 / 3, 6), 1.0))
        self.assertEqual(r1["D"]["m_resamples_used"], 500)
        self.assertEqual(r3["D"]["m_resamples_used"], 500)

    def test_stream_spec(self):
        """Amendment 6.1: family index = int(r F) with r from random.Random(seed).random(), families sorted by name."""
        import random
        ps = pairs_from([("a1", "A", 1.0, None, 0.5), ("b1", "B", 1.0, None, 1.5), ("c1", "C", 1.0, None, 1.0)])
        rng = random.Random(B.SEED)
        D = []
        c = {"A": 1, "B": -1, "C": 0}
        names = ["A", "B", "C"]
        for _ in range(200):
            drawn = [names[int(rng.random() * 3)] for _ in range(3)]
            D.append(sum(c[f] for f in drawn) / 3)
        got = B.family_bootstrap(ps, B=200, seed=B.SEED)["D"]["interval"]
        D.sort()
        self.assertEqual(got, B.percentile_interval(D))

    def test_family_weighted_and_leave_one_out(self):
        ps = pairs_from([("a1", "A", 1.0, None, 0.5), ("a2", "A", 1.0, None, 0.5), ("a3", "A", 1.0, None, 0.5), ("b1", "B", 1.0, None, 1.5)])
        fw = B.family_weighted(ps)
        self.assertAlmostEqual(fw["D_family_weighted"], (1.0 + -1.0) / 2)
        self.assertAlmostEqual(B.summarize(ps)["D"], (3 - 1) / 4)
        lo = B.leave_one_family_out(ps)
        self.assertEqual(lo["D_range"]["min"], -1.0)
        self.assertEqual(lo["D_range"]["min_when_dropped"], "A")
        self.assertEqual(lo["D_range"]["max"], 1.0)
        self.assertEqual(B.leave_one_family_out(ps[:1])["rows"], [])

    def test_split_table_flags_small_arms(self):
        ps = pairs_from([(f"p{i}", f"f{i}", 1.0, None, 0.5) for i in range(6)])
        for i, x in enumerate(ps):
            x["rung"] = "R1a" if i < 5 else "R1b"
        t = B.split_table(ps, lambda x: x["rung"], ["R1a", "R1b"])
        self.assertEqual((t["R1a"]["n"], t["R1a"]["too_few"], t["R1b"]["n"], t["R1b"]["too_few"]), (5, False, 1, True))
        self.assertEqual(t["R1a"]["n_plus"], 5)

    def test_fingerprint(self):
        a = pairs_from([("a", "f1", 1.0, 0.1, 0.5), ("b", "f2", 1.0, 0.1, 0.7)])
        b = list(reversed(a))
        self.assertEqual(B.fingerprint(a), B.fingerprint(b))                    # order does not matter
        c = pairs_from([("a", "f1", 1.0, 0.1, 0.5), ("b", "f2", 1.0, 0.1, 0.71)])
        self.assertNotEqual(B.fingerprint(a), B.fingerprint(c))                 # a number does
        d = [dict(x) for x in a]
        d[0]["stage"] = "confirmed"
        self.assertNotEqual(B.fingerprint(a), B.fingerprint(d))                 # so does a stage


class SnapshotTests(unittest.TestCase):
    def snap(self):
        return {"schema_version": 5, "as_of": "2026-10-15T00:00:00.000000Z", "stage_counts": {"matched": 1}, "papers": [
            paper("p1", stage="matched", p=1.0, o=1.0), paper("p2", stage="confirmed", p=1.0, o=0.5, grade="DIRECTIONAL"),
            paper("p3", stage="not_started", with_primary=False), paper("p4", stage="not_comparable", p=1.0, o=0.2, grade="NOT_COMPARABLE", reasons=[B.NC_OK_REASON]),
            paper("p5", stage="closed_restricted", with_primary=False), paper("p6", stage="matched", rung="R0")]}

    def test_evaluate_snapshot_codes_and_pairs(self):
        catalog = {"p1": cat("f1", ["B1"], "AER"), "p2": cat("f2", ["B2", "GAP"]), "p4": cat("f1", ["B3"], "JHE")}
        rows, pairs = B.evaluate_snapshot(self.snap(), catalog)
        self.assertEqual([r["code"] for r in rows], ["EVAL", "EVAL", "NOT_STARTED", "EVAL", "CLOSED_TRIAGE", "RUNG_NOT_COUNTED"])
        self.assertEqual([x["paper_id"] for x in pairs], ["p1", "p2", "p4"])
        self.assertEqual([x["family"] for x in pairs], ["f1", "f2", "f1"])
        by = {x["paper_id"]: x for x in pairs}
        self.assertEqual((by["p1"]["journal_group"], by["p2"]["field"], by["p4"]["journal_group"]), ("top5", "health", "other_listed"))
        self.assertTrue(by["p2"]["certified"])
        self.assertTrue(by["p4"]["catalogue_missing"] is False)

    def test_pinned_value_variant(self):
        sn = self.snap()
        sn["papers"][0]["primary"]["published"] = {"point": 1.0, "se": 0.1}
        sn["papers"][0]["primary"]["target"] = {"point": 2.0, "se": 0.3}
        x = B.build_pair(sn["papers"][0], cat("f1"), use_pinned=True)
        self.assertEqual((x["p"], x["se_pub"]), (2.0, 0.3))
        y = B.build_pair(sn["papers"][0], cat("f1"))
        self.assertEqual((y["p"], y["se_pub"]), (1.0, 0.1))

    def test_checks_flag_inconsistent_bins_and_missing_runs(self):
        sn = self.snap()
        catalog = {p["paper_id"]: cat("f") for p in sn["papers"]}
        rows, pairs = B.evaluate_snapshot(sn, catalog)
        ok = B.run_checks(sn, rows, pairs, catalog)
        self.assertEqual((ok["pairs_where_band_and_numeric_grade_disagree"], ok["pairs_where_primary_run_missing_or_not_independent"]), ([], []))
        sn["papers"][0]["primary"]["grade_numeric"] = "DIRECTIONAL"            # p1 is within the band but labelled DIRECTIONAL
        sn["papers"][1]["runs"] = []
        rows, pairs = B.evaluate_snapshot(sn, catalog)
        bad = B.run_checks(sn, rows, pairs, catalog)
        self.assertIn("p1", bad["pairs_where_band_and_numeric_grade_disagree"])
        self.assertIn("p2", bad["pairs_where_primary_run_missing_or_not_independent"])

    def test_trajectory_marks_new_states(self):
        sn = self.snap()
        sn2 = json.loads(json.dumps(sn))
        sn2["as_of"] = "2026-10-15T01:00:00.000000Z"
        sn3 = json.loads(json.dumps(sn))
        sn3["as_of"] = "2026-10-15T02:00:00.000000Z"
        sn3["papers"][0]["primary"]["ours"]["point"] = 0.9
        sn4 = json.loads(json.dumps(sn))
        sn4["as_of"] = "2026-10-16T00:00:00.000000Z"                             # later than the main as_of: left out
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i, s in enumerate([sn, sn2, sn3, sn4]):
                p = Path(d) / f"snapshot_{i}.json"
                p.write_text(json.dumps(s))
                paths.append(p)
            catalog = {p["paper_id"]: cat("f") for p in sn["papers"]}
            rows = B.trajectory(paths, catalog, "2026-10-15T02:00:00.000000Z", {})
        self.assertEqual([r["new_state"] for r in rows], [True, False, True])
        self.assertEqual([r["n"] for r in rows], [3, 3, 3])


class GuardTests(unittest.TestCase):
    def test_out_dir_guard(self):
        self.assertFalse(B.is_safe_out_dir(B.ROOT / "data/replication/q2r"))
        self.assertFalse(B.is_safe_out_dir(B.ROOT / "scripts/l2_pipeline/x"))
        self.assertFalse(B.is_safe_out_dir(B.ROOT / "data/l2_pipeline"))
        self.assertTrue(B.is_safe_out_dir(B.ROOT / "data/l3_meta/q2r_v9"))

    def test_main_refuses_a_changed_amendment(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "amend.md"
            fake.write_text("changed")
            old = (B.AMEND, B.parse_hash_record)
            try:
                B.AMEND = fake
                B.parse_hash_record = lambda path=None: {"sha256": "0" * 64, "t_amend_utc": "x", "listed": {}}
                with self.assertRaises(SystemExit) as cm:
                    B.main(["--dry-run"])
                self.assertIn("amendment file differs", str(cm.exception))
            finally:
                B.AMEND, B.parse_hash_record = old

    def test_version_above_one_needs_a_snapshot(self):
        with self.assertRaises(SystemExit):
            B.main(["--dry-run", "--version", "2"])

    def test_source_has_no_network_and_no_forbidden_writes(self):
        src = Path(B.__file__).read_text()
        for word in ("urllib", "requests", "socket", "http.client", "subprocess", "os.system"):
            self.assertNotIn(word, src)
        writes = [ln.strip() for ln in src.splitlines() if re.search(r"\.write_(text|bytes)\(|open\(.*['\"][wa]b?['\"]", ln)]
        self.assertEqual(len(writes), 7)                                         # 2 copies, results, csv writer, run log, ledger, dry-run dump
        for ln in writes:
            self.assertTrue(any(k in ln for k in ("snap_copy", "cat_copy", "results.write_text", "open(path, \"w\"", "RUN_LOG", "LEDGER", "Path(args.dump)")), ln)
            self.assertNotIn("replication", ln)
            self.assertNotIn("l2_pipeline", ln)


if __name__ == "__main__":
    unittest.main(verbosity=1)
