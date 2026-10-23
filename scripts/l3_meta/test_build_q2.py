#!/usr/bin/env python3
"""Unit checks for reconcile_q2.py and build_q2.py on SYNTHETIC inputs only (no L2 file, no real estimate).

Run:  python3 -I -B scripts/l3_meta/test_build_q2.py      (standard library only)
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_q2 as B  # noqa: E402
import reconcile_q2 as R  # noqa: E402

DIMS = R.DIMS


def cells(core="match", noncore="partial", **over):
    c = {d: core for d in R.CORE}
    c.update({d: noncore for d in R.NONCORE})
    c.update(over)
    return c


def coder_row(pid, design, cs, coder="c1"):
    r = {"pair_id": pid, "design_id": design, "paper_id": pid.split("|")[0], "coder_id": coder, **cs}
    r["strict_core_yes_no"] = "yes" if all(cs[d] in R.PASS for d in R.CORE) else "no"
    r["notes"] = ""
    return r


class ReconcileTests(unittest.TestCase):
    def test_kappa(self):
        self.assertAlmostEqual(R.cohen_kappa(["yes", "no", "no", "no"], ["yes", "yes", "no", "no"]), 0.5)
        self.assertAlmostEqual(R.cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]), 1.0)
        self.assertIsNone(R.cohen_kappa(["no", "no"], ["no", "no"]))
        self.assertIsNone(R.cohen_kappa([], []))

    def test_final_from_cells(self):
        self.assertEqual(R.final_from_cells(cells()), "STRICT")
        self.assertEqual(R.final_from_cells(cells(clock="mismatch")), "CORE_ONLY")
        self.assertEqual(R.final_from_cells(cells(population="partial")), "NOT_STRICT")
        self.assertEqual(R.final_from_cells(cells(units="arith_convertible", comparison="unknown")), "STRICT")

    def test_routes(self):
        yes = cells()
        self.assertEqual(R.route(coder_row("p|d", "d", yes), coder_row("p|d", "d", dict(yes))), "both_yes")
        # both yes but one non-core cell differs -> the lock needs a third read
        self.assertEqual(R.route(coder_row("p|d", "d", yes), coder_row("p|d", "d", cells(clock="mismatch"))), "third_reader")
        # both no on a common failing core dimension -> settled
        a = cells(population="partial", outcome="match")
        b = cells(population="mismatch", units="mismatch")
        self.assertEqual(R.route(coder_row("p|d", "d", a), coder_row("p|d", "d", b)), "settled_no")
        # both no but disjoint failing dimensions
        a = cells(policy_scope_dose="partial")
        b = cells(population="mismatch", outcome="partial")
        self.assertEqual(R.route(coder_row("p|d", "d", a), coder_row("p|d", "d", b)), "third_reader")
        # one yes one no
        self.assertEqual(R.route(coder_row("p|d", "d", cells()), coder_row("p|d", "d", cells(population="partial"))), "third_reader")
        # undetermined (unknown, no failing core cell) against a decided no
        u = cells(population="unknown", outcome="unknown", units="unknown")
        n = cells(population="mismatch")
        self.assertEqual(R.route(coder_row("p|d", "d", u), coder_row("p|d", "d", n)), "third_reader")
        self.assertEqual(R.route(coder_row("p|d", "d", u), coder_row("p|d", "d", dict(u))), "third_reader")

    def test_vocabulary_and_flag_checks(self):
        good = coder_row("p|d", "d", cells())
        R.check_vocab([good], "x")
        bad = dict(good, outcome="maybe")
        with self.assertRaises(ValueError):
            R.check_vocab([bad], "x")
        wrong_flag = dict(good, strict_core_yes_no="no")
        with self.assertRaises(ValueError):
            R.check_vocab([wrong_flag], "x")

    def _world(self):
        sheet = [{"pair_id": f"p{i}|d{i}", "design_id": f"d{i}", "paper_id": f"p{i}"} for i in range(5)]
        yes = cells()
        rows1 = [coder_row("p0|d0", "d0", yes), coder_row("p1|d1", "d1", cells(population="partial")),
                 coder_row("p2|d2", "d2", cells(population="unknown", outcome="unknown", units="unknown")),
                 coder_row("p3|d3", "d3", cells(policy_scope_dose="partial")), coder_row("p4|d4", "d4", cells(outcome="mismatch"))]
        rows2 = [coder_row("p0|d0", "d0", dict(yes), "c2"), coder_row("p1|d1", "d1", cells(population="mismatch"), "c2"),
                 coder_row("p2|d2", "d2", cells(population="unknown", outcome="mismatch"), "c2"),
                 coder_row("p3|d3", "d3", cells(), "c2"), coder_row("p4|d4", "d4", cells(outcome="partial"), "c2")]
        return sheet, rows1, rows2

    def third(self, pid, design, cs, final, access="open_fulltext_read"):
        t = {"pair_id": pid, "design_id": design, "paper_id": pid.split("|")[0], "reason_for_third_read": "x", "tr_final_class": final,
             "tr_strict_core_yes_no": "no", "tr_evidence_access": access, "tr_evidence_locator": "loc", "tr_basis": "basis"}
        t.update({"tr_" + d: cs[d] for d in DIMS})
        return t

    def test_reconcile_end_to_end(self):
        sheet, rows1, rows2 = self._world()
        ids, routes, final = R.reconcile(sheet, rows1, rows2, [
            self.third("p2|d2", "d2", cells(population="unknown", outcome="unknown", units="unknown"), "UNAVAILABLE"),
            self.third("p3|d3", "d3", cells(policy_scope_dose="partial"), "NOT_STRICT")])
        self.assertEqual([routes[i] for i in ids], ["both_yes", "settled_no", "third_reader", "third_reader", "settled_no"])
        self.assertEqual([final[i]["final_class"] for i in ids], ["STRICT", "NOT_STRICT", "UNAVAILABLE", "NOT_STRICT", "NOT_STRICT"])
        self.assertEqual(final["p1|d1"]["common_failing_core_dims"], "population")
        self.assertEqual(final["p4|d4"]["common_failing_core_dims"], "outcome")

    def test_third_reader_coverage_and_consistency(self):
        sheet, rows1, rows2 = self._world()
        un = self.third("p2|d2", "d2", cells(population="unknown", outcome="unknown", units="unknown"), "UNAVAILABLE")
        ok3 = self.third("p3|d3", "d3", cells(policy_scope_dose="partial"), "NOT_STRICT")
        with self.assertRaises(ValueError):  # a needed pair is missing
            R.reconcile(sheet, rows1, rows2, [un])
        with self.assertRaises(ValueError):  # an unneeded pair is present
            R.reconcile(sheet, rows1, rows2, [un, ok3, self.third("p0|d0", "d0", cells(), "STRICT")])
        with self.assertRaises(ValueError):  # UNAVAILABLE without an unverifiable core cell
            R.reconcile(sheet, rows1, rows2, [self.third("p2|d2", "d2", cells(), "UNAVAILABLE"), ok3])
        with self.assertRaises(ValueError):  # class contradicts its cells
            R.reconcile(sheet, rows1, rows2, [un, self.third("p3|d3", "d3", cells(policy_scope_dose="partial"), "STRICT")])
        bad = dict(ok3, tr_evidence_locator=" ")
        with self.assertRaises(ValueError):  # no evidence locator
            R.reconcile(sheet, rows1, rows2, [un, bad])

    def test_agreement_stats(self):
        sheet, rows1, rows2 = self._world()
        st = R.agreement_stats(rows1, rows2)
        self.assertEqual(st["strict_core"]["coder1_yes"], 1)
        self.assertEqual(st["strict_core"]["coder2_yes"], 2)
        self.assertEqual(st["strict_core"]["coder1_no_coder2_yes"], 1)
        self.assertEqual(st["strict_core"]["n_agree"], 4)
        self.assertAlmostEqual(st["strict_core"]["raw_agreement"], 0.8)
        self.assertEqual(st["dimensions"]["clock"]["n_agree"], 5)


# ---------------------------------------------------------------------------------------------
# build_q2
# ---------------------------------------------------------------------------------------------
def cand(rank, year, peer, strict_core, point=0.1, units="log points (ATT)", direction="+1", match_units="match", conv=None, version="journal"):
    return {"rank": rank, "year": year, "peer_reviewed": peer, "strict_core": strict_core, "citation": f"Auth{rank} ({year})", "version": version,
            "direction_a_i": direction, "estimate": {"point": point, "point_as_printed": str(point), "units": units, "locator": "Table 1"},
            "conversion": conv, "match": {**{d: "match" for d in DIMS[:4]}, "units": match_units, "comparison": "partial", "clock": "partial",
                                           "aggregation": "match", "horizon": "partial"}}


def entry(tid, cls, cands=(), chosen=1):
    return {"design_id": tid, "q2_class": cls, "chosen_candidate_rank": chosen if cls == "strict_possible" else None, "candidates": list(cands)}


def live_row(did, status="estimated", gate=None, fam=None, unit="log points", point=0.05, tid=None, cls=None, group="autopilot", reportable=True):
    return {"design_id": did, "status": status, "gate": gate, "domain": "health", "policy_family": fam or did, "source_group": group,
            "reportable": reportable, "outcome_unit": unit, "point_estimate": point if status == "estimated" else None,
            "benchmark": {"target_id": tid or did, "q2_class": cls, "published_estimate": {"point": None}} if cls else None,
            "benchmark_first_order_variant": None}


def v3rec(did, b=0.05, noise=0.02, s_pre=0.2):
    return {"design_id": did, "b": b, "noise_sd": noise, "s_pre": s_pre}


class BuildTests(unittest.TestCase):
    def test_peer_review_flag(self):
        self.assertTrue(B.venue_is_peer_reviewed("Journal of Law and Economics 64(1):29-51 (NBER Working Paper 26364)"))
        self.assertTrue(B.venue_is_peer_reviewed("JAMA Pediatrics 171(4):350-356"))
        self.assertFalse(B.venue_is_peer_reviewed("SSRN Working Paper (posted Feb 2026, abstract_id 6172939)"))
        self.assertFalse(B.venue_is_peer_reviewed("BFI Working Paper 2019-62"))
        self.assertFalse(B.venue_is_peer_reviewed(None))

    def test_candidate_class(self):
        self.assertEqual(B.classify_targets_candidate("strict_possible", {"strict_core": True}), "STRICT")
        self.assertEqual(B.classify_targets_candidate("strict_possible", {"strict_core": False}), "NOT_STRICT")
        self.assertEqual(B.classify_targets_candidate("non_strict", {"strict_core": True}), "CORE_ONLY")
        self.assertEqual(B.classify_targets_candidate("non_strict", {"strict_core": False}), "NOT_STRICT")
        self.assertEqual(B.classify_targets_candidate("unavailable", {"strict_core": False}), "UNAVAILABLE")
        self.assertEqual(B.classify_known_pair("strict_possible", "1"), "STRICT")
        self.assertEqual(B.classify_known_pair("non_strict", "0"), "NOT_STRICT")

    def test_number_orientation_units(self):
        c = cand(1, 2020, True, True, point=0.0261)
        self.assertEqual(B.published_number(c), (0.0261, "log points (ATT)", "as printed"))
        conv = cand(1, 2020, True, True, point=-5.05, units="labs per state-month", match_units="arith_convertible",
                    conv={"converted_point": -60.6, "to_units": "labs per state-year"})
        self.assertEqual(B.published_number(conv), (-60.6, "labs per state-year", "converted"))
        no_conv = cand(1, 2020, True, True, match_units="arith_convertible")  # convertible but no recorded conversion
        self.assertEqual(B.published_number(no_conv)[0], None)
        self.assertEqual(B.orientation({"direction_a_i": "+1"}), 1)
        self.assertEqual(B.orientation({"direction_a_i": "-1"}), -1)
        self.assertIsNone(B.orientation({"direction_a_i": None}))
        self.assertIsNone(B.orientation({"direction_a_i": "0"}))
        self.assertTrue(B.unit_label_ok("log points", "Log points (ATT on log income)"))
        self.assertFalse(B.unit_label_ok("percentage points", "log points"))
        self.assertFalse(B.unit_label_ok("", "log points"))

    def test_choice_rule(self):
        mk = lambda src, order, year, peer: {"source": src, "order": order, "year": year, "peer_reviewed": peer}
        a = mk("targets_r2", 0, 2017, True)
        b = mk("reconciled", 1000, 2021, True)
        self.assertIs(B.choose_candidate([b, a])[0], a)           # earlier peer-reviewed wins
        wp = mk("targets_r2", 0, 2007, False)
        self.assertIs(B.choose_candidate([wp, b])[0], b)          # peer-reviewed before a working paper even if later
        same = mk("targets_r2", 5, 2021, True)
        self.assertIs(B.choose_candidate([b, same])[0], same)      # tie: the TARGETS candidate first
        self.assertEqual(B.choose_candidate([]), (None, []))

    def test_gap_sign(self):
        self.assertAlmostEqual(B.gap(1, 0.0946, 0.05, 0.1), 0.446)
        self.assertAlmostEqual(B.gap(-1, -0.6, -0.3, 1.0), 0.3)        # published larger in magnitude along its direction
        self.assertAlmostEqual(B.gap(1, 0.02, 0.05, 0.01), -3.0)       # published smaller: negative gap (ours larger)
        self.assertAlmostEqual(B.gap(-1, 0.4, 0.1, 0.1), -3.0)         # wrong-direction published value flips with a

    def test_codes(self):
        e_ok = entry("t1", "strict_possible", [cand(1, 2013, True, True, point=0.0946)])
        row = B.evaluate_design(live_row("d1", tid="t1", cls="strict_possible"), v3rec("d1"), e_ok, [])
        self.assertEqual(row["code"], "EVAL")
        self.assertAlmostEqual(row["g"], (0.0946 - 0.05) / 0.2)
        self.assertAlmostEqual(row["g_noise"], (0.0946 - 0.05) / 0.02)
        self.assertAlmostEqual(row["raw_gap"], 0.0946 - 0.05)
        # no s_pre -> scale undefined, but the noise-scale variant exists
        row = B.evaluate_design(live_row("d2", tid="t1", cls="strict_possible"), v3rec("d2", s_pre=None), e_ok, [])
        self.assertEqual(row["code"], "SCALE_UNDEFINED")
        self.assertNotIn("g", row)
        self.assertAlmostEqual(row["g_noise"], (0.0946 - 0.05) / 0.02)
        # neither scale
        row = B.evaluate_design(live_row("d3", tid="t1", cls="strict_possible"), v3rec("d3", s_pre=None, noise=None), e_ok, [])
        self.assertEqual(row["code"], "SCALE_UNDEFINED")
        self.assertNotIn("g_noise", row)
        self.assertIsNotNone(row["raw_gap"])
        # strict but not estimated
        row = B.evaluate_design(live_row("d4", status="not_evaluable", gate="support", tid="t1", cls="strict_possible"), None, e_ok, [])
        self.assertEqual(row["code"], "STRICT_NOT_ESTIMATED:support")
        # unit mismatch
        row = B.evaluate_design(live_row("d5", unit="percentage points", tid="t1", cls="strict_possible"), v3rec("d5"), e_ok, [])
        self.assertEqual(row["code"], "UNIT_MISMATCH")
        # orientation missing
        e_dir = entry("t2", "strict_possible", [cand(1, 2013, True, True, direction=None)])
        row = B.evaluate_design(live_row("d6", tid="t2", cls="strict_possible"), v3rec("d6"), e_dir, [])
        self.assertEqual(row["code"], "ORIENTATION_MISSING")
        # no comparable number (units not match, no conversion)
        e_num = entry("t3", "strict_possible", [cand(1, 2013, True, True, match_units="arith_convertible")])
        row = B.evaluate_design(live_row("d7", tid="t3", cls="strict_possible"), v3rec("d7"), e_num, [])
        self.assertEqual(row["code"], "NO_COMPARABLE_NUMBER")
        # estimate missing
        row = B.evaluate_design(live_row("d8", tid="t1", cls="strict_possible"), v3rec("d8", b=None), e_ok, [])
        self.assertEqual(row["code"], "ESTIMATE_MISSING")

    def test_new_pairs(self):
        e_ok = entry("t1", "strict_possible", [cand(1, 2017, True, True, point=-0.6, direction="-1")])
        later = {"pair_id": "a|t1", "final_class": "STRICT", "first_author": "Anderson", "year": 2021,
                 "venue": "Journal of Law and Economics 64(1):29-51", **{d: "match" for d in DIMS}}
        row = B.evaluate_design(live_row("d1", tid="t1", cls="strict_possible", unit="log points"), v3rec("d1"), e_ok, [later])
        self.assertEqual(row["chosen"]["source"], "targets_r2")             # the earlier peer-reviewed article wins
        self.assertEqual([a["pair_id"] for a in row["alternatives"]], ["a|t1"])
        earlier = dict(later, year=2010)
        row = B.evaluate_design(live_row("d1", tid="t1", cls="strict_possible"), v3rec("d1"), e_ok, [earlier])
        self.assertEqual(row["chosen"]["source"], "reconciled")
        self.assertEqual(row["code"], "STRICT_NO_NUMBER")                    # a winning new pair has no locked number
        only_new = B.evaluate_design(live_row("d9", tid="x", cls="none_found"), v3rec("d9"), entry("x", "none_found"), [later])
        self.assertTrue(only_new["has_strict"])
        self.assertEqual(only_new["code"], "STRICT_NO_NUMBER")
        # a new UNAVAILABLE pair turns a non_strict design into UNAVAILABLE; a NOT_STRICT pair changes nothing
        ns = entry("t5", "non_strict", [cand(1, 2013, True, False)])
        un = dict(later, final_class="UNAVAILABLE")
        self.assertEqual(B.evaluate_design(live_row("d10", tid="t5", cls="non_strict"), v3rec("d10"), ns, [un])["code"], "UNAVAILABLE")
        self.assertEqual(B.evaluate_design(live_row("d10", tid="t5", cls="non_strict"), v3rec("d10"), ns, [un])["unavailable_from"], ["a|t1"])
        self.assertEqual(B.evaluate_design(live_row("d10", tid="t5", cls="non_strict"), v3rec("d10"), ns, [dict(later, final_class="NOT_STRICT")])["code"], "NON_STRICT")
        self.assertEqual(B.evaluate_design(live_row("d11", tid="t6", cls="unavailable"), v3rec("d11"), entry("t6", "unavailable", [cand(1, 2013, True, False)]), [])["code"], "UNAVAILABLE")
        self.assertEqual(B.evaluate_design(live_row("d12", tid="t7", cls="none_found"), v3rec("d12"), entry("t7", "none_found"), [])["code"], "NONE_FOUND")
        no_entry = dict(live_row("d13"), benchmark=None)
        self.assertEqual(B.evaluate_design(no_entry, v3rec("d13"), None, [])["code"], "NO_TARGETS_ENTRY")

    def test_core_only_is_not_strict(self):
        e = entry("t8", "non_strict", [cand(1, 2013, True, True)])   # core passes, a documented mismatch elsewhere (protocol: non_strict)
        row = B.evaluate_design(live_row("d14", tid="t8", cls="non_strict"), v3rec("d14"), e, [])
        self.assertEqual(row["code"], "NON_STRICT")
        self.assertTrue(row["has_core_only"])

    def test_decision_rule(self):
        d0 = B.decide(0, 0)
        self.assertEqual(d0["status"], "NOT_COMPUTABLE")
        self.assertIsNone(d0["p_value"])
        d1 = B.decide(5, 5)
        self.assertEqual(d1["status"], "DESCRIPTIVE_GAP_ONLY")
        self.assertFalse(d1["families_ge_min"])
        d2 = B.decide(30, 25)
        self.assertTrue(d2["families_ge_min"])
        self.assertEqual(d2["status"], "DESCRIPTIVE_GAP_ONLY")     # the engine is unavailable whatever n is
        self.assertIsNone(d2["interval"])
        self.assertIsNone(d2["p_value"])
        self.assertIn("not run", d2["engine_status"])

    def _world(self, n_matched=25, with_spre=True):
        designs, v3, entries, pairs = [], [], [], []
        for i in range(n_matched):
            did = f"d{i}"
            entries.append(entry(f"t{i}", "strict_possible", [cand(1, 2013, True, True, point=0.10, direction="+1")]))
            designs.append(live_row(did, fam=f"fam{i}", tid=f"t{i}", cls="strict_possible", point=0.04))
            v3.append(v3rec(did, b=0.04, noise=0.02, s_pre=0.2 if with_spre else None))
        designs.append(live_row("dx", status="not_evaluable", gate="history", tid="tx", cls="non_strict"))
        entries.append(entry("tx", "non_strict", [cand(1, 2013, True, False)]))
        live = {"designs": designs}
        return live, {"designs": v3}, {"designs": entries}, pairs

    def test_assemble_with_enough_families(self):
        live, v3, targets, pairs = self._world(25)
        res, rows, checks = B.assemble(live, v3, targets, pairs, [])
        self.assertEqual((res["D"], res["n"], res["F"]), (26, 25, 25))
        self.assertAlmostEqual(res["G"]["value"], (0.10 - 0.04) / 0.2)
        self.assertAlmostEqual(res["G_noise_variant"]["value"], (0.10 - 0.04) / 0.02)
        self.assertEqual(res["coverage_codes"], {"EVAL": 25, "NON_STRICT": 1})
        self.assertEqual(res["designs_by_targets_class_and_live_status"], {"non_strict | not_evaluable": 1, "strict_possible | estimated": 25})
        self.assertEqual(res["unavailable_designs"], {})
        self.assertEqual(res["reconciliation"]["pairs"], 0)
        self.assertTrue(res["decision"]["families_ge_min"])
        self.assertIsNone(res["decision"]["p_value"])
        self.assertEqual(checks["v3_b_vs_live_point_estimate_mismatch"], [])

    def test_assemble_two_designs_share_a_family(self):
        live, v3, targets, pairs = self._world(3)
        for d in live["designs"][:2]:
            d["policy_family"] = "same"
        res, rows, checks = B.assemble(live, v3, targets, pairs, [])
        self.assertEqual((res["n"], res["F"]), (3, 2))

    def test_assemble_without_scale_gives_not_computable(self):
        live, v3, targets, pairs = self._world(4, with_spre=False)
        res, rows, checks = B.assemble(live, v3, targets, pairs, [])
        self.assertEqual(res["n"], 0)
        self.assertIsNone(res["G"]["value"])
        self.assertEqual(res["decision"]["status"], "NOT_COMPUTABLE")
        self.assertEqual(res["coverage_codes"]["SCALE_UNDEFINED"], 4)
        self.assertEqual(res["G_noise_variant"]["n"], 4)
        self.assertEqual(len(res["descriptive_raw_gaps"]), 4)
        self.assertAlmostEqual(res["descriptive_raw_gaps"][0]["raw_gap_a_times_blit_minus_b"], 0.06)

    def test_assemble_pairs_and_flips(self):
        live, v3, targets, _ = self._world(2, with_spre=False)
        live["designs"].append(live_row("dz", fam="famz", tid="tz", cls="none_found"))
        v3["designs"].append(v3rec("dz", s_pre=0.3))
        targets["designs"].append(entry("tz", "none_found"))
        pid_new = "w|dz"
        pairs = [
            {"pair_id": "known1|d0", "known_in_targets": "1", "targets_q2_class": "strict_possible", "targets_strict_core": "1", "live_design_id": "d0", "l2_state": "estimated", "fo_variant_base_design": ""},
            {"pair_id": "known2|d1", "known_in_targets": "1", "targets_q2_class": "non_strict", "targets_strict_core": "0", "live_design_id": "d1", "l2_state": "estimated", "fo_variant_base_design": ""},
            {"pair_id": pid_new, "known_in_targets": "0", "targets_q2_class": "none_found", "targets_strict_core": "", "live_design_id": "dz", "l2_state": "estimated", "fo_variant_base_design": "",
             "paper_first_author": "W", "paper_year": "2016", "paper_venue": "Ann 1(2):3-4"},
            {"pair_id": "lost|nowhere", "known_in_targets": "0", "targets_q2_class": "", "targets_strict_core": "", "live_design_id": "", "l2_state": "not_in_live_set:queued", "fo_variant_base_design": ""},
        ]
        recon = [{"pair_id": pid_new, "final_class": "NOT_STRICT", **{d: "" for d in DIMS}},
                 {"pair_id": "lost|nowhere", "final_class": "UNAVAILABLE", **{d: "" for d in DIMS}}]
        orig = list(B.FLIP_PAIRS)
        try:
            B.FLIP_PAIRS[:] = [pid_new, "absent|pair"]
            res, rows, checks = B.assemble(live, v3, targets, pairs, recon)
        finally:
            B.FLIP_PAIRS[:] = orig
        pc = res["pairs"]["known_pairs_and_new_pairs_by_class"]
        self.assertEqual(pc, {"known:NOT_STRICT": 1, "known:STRICT": 1, "new:NOT_STRICT": 1, "new:UNAVAILABLE": 1})
        self.assertEqual(res["pairs"]["pairs_unaccounted"], [])
        flips = {f["pair_id"]: f for f in res["sensitivities"]["reading_flips"]["pairs"]}
        self.assertTrue(flips[pid_new]["would_enter_G"])             # dz has s_pre
        self.assertFalse(flips["absent|pair"]["found"])
        self.assertTrue(res["sensitivities"]["reading_flips"]["any_would_enter_G"])

    def test_first_order_pairs_are_not_attached_to_the_executed_design(self):
        live, v3, targets, _ = self._world(1, with_spre=True)
        pairs = [{"pair_id": "fo|d0", "known_in_targets": "0", "targets_q2_class": "", "targets_strict_core": "", "live_design_id": "d0",
                  "l2_state": "estimated", "fo_variant_base_design": "d0", "paper_first_author": "F", "paper_year": "2010", "paper_venue": "J 1(2):3-4"}]
        recon = [{"pair_id": "fo|d0", "final_class": "STRICT", **{d: "match" for d in DIMS}}]
        res, rows, checks = B.assemble(live, v3, targets, pairs, recon)
        d0 = next(r for r in rows if r["design_id"] == "d0")
        self.assertEqual(d0["n_new_pairs"], 0)

    def test_hash_record_parser(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "h.txt"
            p.write_text("file: x\nsha256: " + "a" * 64 + "\namended_utc (T_amend): 2026-10-15T18:24:03Z\ninputs:\n  data/foo.json sha256 " + "b" * 64 + " (note)\n", encoding="utf-8")
            rec = B.parse_hash_record(p)
            self.assertEqual(rec["sha256"], "a" * 64)
            self.assertEqual(rec["t_amend_utc"], "2026-10-15T18:24:03Z")
            self.assertEqual(rec["listed"], {"data/foo.json": "b" * 64})

    def test_clean_and_mean(self):
        self.assertEqual(B.clean({"a": float("nan"), "b": [1.0, float("inf")]}), {"a": None, "b": [1.0, None]})
        self.assertIsNone(B.mean([]))
        self.assertEqual(B.mean([1, 2, 3]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
