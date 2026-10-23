"""Checks of reconcile_d2.py and build_d2.py on INVENTED data (no repo data file is read; files are written only into a temporary folder).

  * agreement statistics (Cohen's kappa on a known table, weighted kappa, binary view) and the reconciliation (routing, final code, strict set, third-reader coverage);
  * the statistics of a design (B_if, r_if, z, z*) against plain loops, through build_d1.design_statistics and design_arrays;
  * the cell codes (NC, D2_PAIR_NOT_NC, CODING_ERROR, GROUP_NOT_CODED, NC_INVALID), the pair-level coding-error rule and its narrower variant, the NC set variants;
  * the E1 floor (40 cells in 8 policy families), a synthetic null (delta near 0, about 5% beyond the line) and a synthetic effect in negative-control families;
  * the width of the empirical null (observed SD inside the reference range under the null, outside it when the ATTs are inflated), the series-level share;
  * the design coverage table on synthetic sweep files, the registered-outcome rows, write-once.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/test_build_d2.py
"""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import build_d1 as D1  # noqa: E402
import build_d2 as D2  # noqa: E402
import reconcile_d2 as R  # noqa: E402
import check_d2_independent as CHK  # noqa: E402  (the plain-loop checker; compared with the builder on invented data only)

FAMS = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]


def synthetic(seed, per_family=2, shift=None, scale=1.0, att_scale=1.0, n_draws=999, fams=FAMS):
    """Design-file dict of an invented design: independent N(0, scale^2) placebo draws per series, ATT ~ N(0, (att_scale scale)^2) plus an optional shift per family."""
    rng = np.random.default_rng(seed)
    sids, fam = [], []
    for f in fams:
        for j in range(per_family):
            sids.append(f"{f}_{j}")
            fam.append(f)
    K = len(sids)
    placebo = rng.normal(0, scale, (K, n_draws))
    att = rng.normal(0, scale * att_scale, K)
    for i, f in enumerate(fam):
        if shift and f in shift:
            att[i] += shift[f]
    sd = np.array([np.std(placebo[i], ddof=1) for i in range(K)])
    return dict(sids=sids, fam=fam, code=["SUPPORTED"] * K, att=att, sd=sd, placebo=placebo, meta={"scale_mode": "rate"})


def stats_of(f, reg=()):
    return D1.design_statistics(f["sids"], f["fam"], f["code"], f["att"], f["sd"], f["placebo"], reg)


def plain_family(f):
    """Plain-loop B_if, r_if of a design with every draw feasible and every series supported."""
    n = len(f["placebo"][0])
    sd = []
    for row in f["placebo"]:
        m = sum(row) / n
        sd.append(math.sqrt(sum((v - m) ** 2 for v in row) / (n - 1)))
    out = {}
    for fam in sorted(set(f["fam"])):
        mem = [i for i, x in enumerate(f["fam"]) if x == fam]
        M = max(abs(f["att"][i] / sd[i]) for i in mem)
        Ms = [max(abs(f["placebo"][i][b] / sd[i]) for i in mem) for b in range(n)]
        q = sorted(Ms)[math.ceil(0.95 * n) - 1]
        out[fam] = (M, q, M > q, sum(1 for v in Ms if v > q) / n)
    return out


def design_row(did, pfam, f, reg=()):
    st = stats_of(f, reg)
    assert st["code"] == "EVAL"
    return dict(design_id=did, policy_family=pfam, code="EVAL", family_stats=st["family_stats"], n_draws=st["n_draws"]), st


def many_designs(n_designs, seed0, shift=None, att_scale=1.0, per_family=2):
    """EVAL design rows (each its own policy family), arrays and the groups map of invented designs."""
    rows, arrays, groups = [], {}, {}
    for k in range(n_designs):
        f = synthetic(seed0 + k, per_family=per_family, shift=shift, att_scale=att_scale)
        did = f"d{k:03d}"
        row, st = design_row(did, f"pf{k:03d}", f)
        rows.append(row)
        arrays[did] = D2.design_arrays(f, st)
        groups[did] = "g_all"
    return rows, arrays, groups


class Reconcile(unittest.TestCase):
    def grid(self, seed=0):
        rng = np.random.default_rng(seed)
        keys = sorted((f"g{g:02d}", f"f{f:02d}") for g in range(35) for f in range(16))
        mk = lambda: [dict(policy_group=k[0], outcome_family=k[1], code=R.CODES[int(rng.integers(0, 3))], reason="r") for k in keys]
        return keys, mk(), mk()

    def test_kappa_known_table(self):
        a = ["y"] * 25 + ["n"] * 25
        b = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
        k = R.cohen_kappa(a, b)
        self.assertAlmostEqual(k["po"], 0.7)
        self.assertAlmostEqual(k["pe"], 0.5)
        self.assertAlmostEqual(k["kappa"], 0.4)
        self.assertAlmostEqual(R.cohen_kappa(a, a)["kappa"], 1.0)
        self.assertIsNone(R.cohen_kappa(["x"] * 5, ["x"] * 5)["kappa"])

    def test_weighted_kappa_is_one_for_identical_codings(self):
        keys, r1, _ = self.grid(1)
        st = R.agreement_stats(r1, r1)
        self.assertAlmostEqual(st["kappa_3_codes"]["kappa"], 1.0)
        self.assertAlmostEqual(st["kappa_3_codes_linear_weights_no_channel_unsure_channel"]["kappa"], 1.0)
        self.assertEqual(st["binary_no_channel_vs_other"]["only_coder1_no_channel"], 0)

    def test_routing_final_and_strict_sets(self):
        keys, r1, r2 = self.grid(2)
        third = [dict(policy_group=k[0], outcome_family=k[1], third_code="unsure", third_reason="because") for k, x, y in zip(keys, r1, r2) if x["code"] != y["code"]]
        routes, final = R.reconcile(keys, r1, r2, third)
        n_diff = sum(1 for x, y in zip(r1, r2) if x["code"] != y["code"])
        self.assertEqual(sum(1 for v in routes.values() if v == "third_reader"), n_diff)
        for k, x, y in zip(keys, r1, r2):
            fin = final[k]
            if x["code"] == y["code"]:
                self.assertEqual(fin["final_code"], x["code"])
                self.assertEqual(fin["route"], "agreed")
            else:
                self.assertEqual(fin["final_code"], "unsure")
            self.assertEqual(fin["strict_nc"], int(x["code"] == y["code"] == "no_channel"))
            self.assertEqual(fin["nc"], int(fin["final_code"] == "no_channel"))

    def test_third_reader_may_overrule_to_no_channel(self):
        keys, r1, r2 = self.grid(3)
        third = [dict(policy_group=k[0], outcome_family=k[1], third_code="no_channel", third_reason="no path") for k, x, y in zip(keys, r1, r2) if x["code"] != y["code"]]
        _, final = R.reconcile(keys, r1, r2, third)
        n_nc = sum(v["nc"] for v in final.values())
        self.assertEqual(n_nc, sum(1 for x, y in zip(r1, r2) if x["code"] == y["code"] == "no_channel") + len(third))

    def test_third_reader_file_must_cover_exactly_the_differing_pairs(self):
        keys, r1, r2 = self.grid(4)
        third = [dict(policy_group=k[0], outcome_family=k[1], third_code="unsure", third_reason="x") for k, x, y in zip(keys, r1, r2) if x["code"] != y["code"]]
        with self.assertRaises(ValueError):
            R.reconcile(keys, r1, r2, third[:-1])
        extra = third + [dict(policy_group=keys[0][0], outcome_family=keys[0][1], third_code="unsure", third_reason="x")] if r1[0]["code"] == r2[0]["code"] else third
        if extra is not third:
            with self.assertRaises(ValueError):
                R.reconcile(keys, r1, r2, extra)
        bad = [dict(t) for t in third]
        bad[0]["third_code"] = "maybe"
        with self.assertRaises(ValueError):
            R.reconcile(keys, r1, r2, bad)
        bad = [dict(t) for t in third]
        bad[0]["third_reason"] = " "
        with self.assertRaises(ValueError):
            R.reconcile(keys, r1, r2, bad)

    def test_check_coder_rejects_incomplete_grids_and_bad_codes(self):
        keys, r1, _ = self.grid(5)
        with self.assertRaises(ValueError):
            R.check_coder(r1[:-1], "x")
        bad = [dict(r) for r in r1]
        bad[3]["code"] = "maybe"
        with self.assertRaises(ValueError):
            R.check_coder(bad, "x")
        bad = [dict(r) for r in r1]
        bad[3] = dict(bad[2])
        with self.assertRaises(ValueError):
            R.check_coder(bad, "x")
        self.assertEqual(R.check_coder(r1, "x"), keys)


class CellStatistics(unittest.TestCase):
    def test_family_statistics_equal_plain_loops(self):
        for seed, shift in ((1, None), (2, {"alpha": 5.0}), (3, {"beta": 4.0, "zeta": 6.0})):
            f = synthetic(seed, shift=shift)
            st = stats_of(f)
            ref = plain_family(f)
            for fam, (M, q, beyond, r) in ref.items():
                s = st["family_stats"][fam]
                self.assertAlmostEqual(s["M"], M, places=11)
                self.assertAlmostEqual(s["q"], q, places=11)
                self.assertEqual(bool(s["beyond"]), beyond)
                self.assertAlmostEqual(s["share_draws_beyond"], r, places=12)

    def test_share_of_draws_beyond_the_line_is_about_five_percent(self):
        st = stats_of(synthetic(7))
        for s in st["family_stats"].values():
            self.assertTrue(0.045 <= s["share_draws_beyond"] <= 0.0501)             # 49/999 when no draw ties the line

    def test_design_arrays_equal_plain_loops(self):
        f = synthetic(4, shift={"gamma": 3.0})
        st = stats_of(f)
        a = D2.design_arrays(f, st)
        n = len(f["placebo"][0])
        for i, sid in enumerate(a["sids"]):
            k = f["sids"].index(sid)
            row = f["placebo"][k]
            m = sum(row) / n
            sd = math.sqrt(sum((v - m) ** 2 for v in row) / (n - 1))
            self.assertAlmostEqual(a["z"][i], f["att"][k] / sd, places=11)
            self.assertAlmostEqual(a["zstar"][i][5], row[5] / sd, places=11)
            cut = sorted(abs(v) for v in row)[math.ceil(0.95 * n) - 1]
            self.assertEqual(bool(a["beyond_own"][i]), abs(f["att"][k]) > cut)
            self.assertAlmostEqual(a["exp_own"][i], sum(1 for v in row if abs(v) > cut) / n, places=12)
        # z* has sample SD 1 by construction
        self.assertTrue(np.allclose(a["zstar"].std(axis=1, ddof=1), 1.0))

    def test_design_arrays_use_the_common_draws(self):
        f = synthetic(5)
        f["placebo"][2, :60] = np.nan
        f["sd"][2] = np.nanstd(f["placebo"][2], ddof=1)
        st = stats_of(f)
        a = D2.design_arrays(f, st)
        self.assertEqual(a["n"], st["n_draws"])
        self.assertEqual(a["zstar"].shape[1], 939)


class CellCodes(unittest.TestCase):
    def setUp(self):
        self.f1, self.f2 = synthetic(11), synthetic(12)
        self.row1, _ = design_row("d_a", "pf_a", self.f1)
        self.row2, _ = design_row("d_b", "pf_b", self.f2)
        P = lambda final, c1=None, c2=None, strict=False: dict(final=final, c1=c1 or final, c2=c2 or final, route="agreed", third="", strict=strict)
        self.pairs = {("g1", "alpha"): P("no_channel", strict=True), ("g1", "beta"): P("unsure"), ("g1", "gamma"): P("no_channel", "no_channel", "unsure"),
                      ("g1", "delta"): P("channel"), ("g1", "epsilon"): P("no_channel", strict=True), ("g1", "zeta"): P("no_channel", "unsure", "no_channel")}
        self.reg_rows = [dict(design_id="d_a", policy_group="g1", outcome_key="x", role="primary", family="epsilon"),
                         dict(design_id="d_z", policy_group="g1", outcome_key="y", role="secondary", family="zeta")]       # d_z is some other design of the group
        self.by_group, self.by_design = D2.registered_maps(self.reg_rows)
        self.groups = {"d_a": "g1", "d_b": "g1"}

    def cells(self, invalid=frozenset(), groups=None):
        return {(c["design_id"], c["family"]): c for c in D2.build_cells([self.row1, self.row2], groups or self.groups, self.pairs, self.by_group, self.by_design, invalid)}

    def test_codes(self):
        c = self.cells()
        self.assertEqual(c[("d_a", "alpha")]["code"], "NC")
        self.assertEqual(c[("d_a", "beta")]["code"], "D2_PAIR_NOT_NC")
        self.assertEqual(c[("d_a", "delta")]["code"], "D2_PAIR_NOT_NC")
        self.assertEqual(c[("d_a", "gamma")]["code"], "NC")                       # final no_channel (third reader) although coder 2 wrote unsure
        self.assertEqual(c[("d_a", "epsilon")]["code"], "CODING_ERROR")           # d_a's own registered family
        self.assertEqual(c[("d_b", "epsilon")]["code"], "CODING_ERROR")           # pair-level: another design of the group registered it
        self.assertEqual(c[("d_b", "zeta")]["code"], "CODING_ERROR")              # registered by d_z, which is not in the sweep
        self.assertEqual(len(c), 12)

    def test_group_not_coded_and_invalid(self):
        c = self.cells(groups={"d_a": "g1", "d_b": "no_such_group"})
        self.assertEqual(c[("d_b", "alpha")]["code"], "GROUP_NOT_CODED")
        self.assertFalse(any(c[("d_b", f)]["flags"]["final"] for f in FAMS))
        c = self.cells(groups={"d_a": "g1"})                                      # a design without a group
        self.assertEqual(c[("d_b", "alpha")]["code"], "GROUP_NOT_CODED")
        c = self.cells(invalid=frozenset({("g1", "alpha")}))
        self.assertEqual(c[("d_a", "alpha")]["code"], "NC_INVALID")
        self.assertFalse(c[("d_a", "alpha")]["flags"]["final_no_exclusion"])

    def test_variants(self):
        c = self.cells()
        fl = lambda d, f: c[(d, f)]["flags"]
        self.assertTrue(fl("d_a", "alpha")["strict"])
        self.assertTrue(fl("d_a", "gamma")["final"])
        self.assertFalse(fl("d_a", "gamma")["strict"])                           # coder 2 wrote unsure
        self.assertTrue(fl("d_a", "gamma")["coder1"])
        self.assertFalse(fl("d_a", "gamma")["coder2"])
        self.assertTrue(fl("d_a", "gamma")["either"])
        self.assertFalse(fl("d_a", "zeta")["coder2"])                            # zeta is a coding error for the whole group: no variant keeps it
        self.assertFalse(fl("d_a", "zeta")["final"])
        self.assertFalse(fl("d_b", "zeta")["either"])
        # narrower reading: only the design's own registered families are removed from its own cells
        self.assertFalse(fl("d_a", "epsilon")["final_narrow_exclusion"])
        self.assertTrue(fl("d_b", "epsilon")["final_narrow_exclusion"])
        self.assertTrue(fl("d_b", "zeta")["final_narrow_exclusion"])             # d_z registered it, not d_b
        # no exclusion at all
        self.assertTrue(fl("d_a", "epsilon")["final_no_exclusion"])
        self.assertTrue(fl("d_b", "zeta")["final_no_exclusion"])

    def test_cell_values_are_the_d1_family_statistics(self):
        c = self.cells()
        st = stats_of(self.f1)["family_stats"]
        for f in FAMS:
            cell = c[("d_a", f)]
            self.assertEqual(cell["B"], int(st[f]["beyond"]))
            self.assertEqual(cell["r"], st[f]["share_draws_beyond"])
            self.assertEqual(cell["M"], st[f]["M"])

    def test_coding_error_list(self):
        errs = D2.coding_errors(self.pairs, self.by_group)
        self.assertEqual([(e["policy_group"], e["family"]) for e in errs], [("g1", "epsilon"), ("g1", "zeta")])
        self.assertEqual(errs[0]["contradicting"], [dict(design_id="d_a", outcome_key="x", role="primary")])

    def test_registered_rows_and_maps(self):
        rows = [dict(row_type="usage", unit_kind="design", role="primary", unit_id="d1", policy_group="g1", family="fam_a", outcome_key="k1"),
                dict(row_type="usage", unit_kind="design", role="secondary", unit_id="d1", policy_group="g1", family="", outcome_key="k2"),
                dict(row_type="usage", unit_kind="design", role="first_order_target", unit_id="d1", policy_group="g1", family="fam_c", outcome_key="k3"),
                dict(row_type="usage", unit_kind="paper", role="primary", unit_id="p1", policy_group="g1", family="fam_d", outcome_key="k4"),
                dict(row_type="usage", unit_kind="design", role="primary", unit_id="d2", policy_group="g2", family="", outcome_key="unknown")]
        table = {"k2": {"family": "fam_b"}}
        rr = D2.registered_rows(rows, table)
        self.assertEqual([(x["design_id"], x["family"]) for x in rr], [("d1", "fam_a"), ("d1", "fam_b")])
        self.assertEqual(D1.registered_families(rows, table), {"d1": {"fam_a", "fam_b"}})        # same rule as D1-V1
        by_group, by_design = D2.registered_maps(rr)
        self.assertEqual(sorted(by_group["g1"]), ["fam_a", "fam_b"])
        self.assertEqual(by_design["d1"], {"fam_a", "fam_b"})

    def test_design_groups_unique(self):
        rows = [dict(row_type="usage", unit_kind="design", unit_id="d1", policy_group="g1"), dict(row_type="usage", unit_kind="design", unit_id="d1", policy_group="g1"),
                dict(row_type="usage", unit_kind="design", unit_id="d2", policy_group=""), dict(row_type="usage", unit_kind="paper", unit_id="p", policy_group="g9")]
        self.assertEqual(D2.design_groups(rows), {"d1": "g1", "d2": None})
        rows.append(dict(row_type="usage", unit_kind="design", unit_id="d1", policy_group="g2"))
        with self.assertRaises(ValueError):
            D2.design_groups(rows)


class Inference(unittest.TestCase):
    def cells_from(self, rows, groups):
        pairs = {(groups[r["design_id"]], f): dict(final="no_channel", c1="no_channel", c2="no_channel", route="agreed", third="", strict=True) for r in rows for f in r["family_stats"]}
        bg, bd = D2.registered_maps([])
        return D2.build_cells(rows, groups, pairs, bg, bd)

    def test_floor(self):
        mk = lambda m, F: [dict(design_id=f"d{i}", policy_family=f"pf{i % F}", B=0, r=0.049) for i in range(m)]
        self.assertFalse(D2.e1_decision(mk(39, 10))["run"])
        self.assertFalse(D2.e1_decision(mk(60, 7))["run"])
        e = D2.e1_decision(mk(40, 8))
        self.assertTrue(e["run"])
        self.assertEqual(e["seed"], [20261011, 32])
        self.assertEqual(e["result"]["status"], "OK")
        self.assertEqual(e["B"], 9999)
        self.assertIsNone(D2.reading_label(D2.e1_decision(mk(10, 3))))

    def test_estimate_arithmetic(self):
        cells = [dict(design_id="a", policy_family="p", B=1, r=0.05), dict(design_id="a", policy_family="p", B=0, r=0.05), dict(design_id="b", policy_family="q", B=0, r=0.04)]
        e = D2.estimate(cells)
        self.assertEqual((e["m"], e["F"], e["n_designs"], e["n_beyond"]), (3, 2, 2, 1))
        self.assertAlmostEqual(e["pi_NC"], 1 / 3)
        self.assertAlmostEqual(e["rbar"], 0.14 / 3)
        self.assertAlmostEqual(e["delta_D2"], (0.95 - 0.05 - 0.04) / 3)
        self.assertEqual(D2.estimate([])["m"], 0)

    def test_planning_detectable(self):
        self.assertAlmostEqual(D2.planning(100)["planning_detectable_delta_2.8_sqrt(.05*.95/m)"], 0.061, places=3)
        self.assertAlmostEqual(D2.planning(200)["planning_detectable_delta_2.8_sqrt(.05*.95/m)"], 0.043, places=3)

    def test_null_gives_delta_near_zero_and_five_percent_beyond(self):
        rows, arrays, groups = many_designs(60, 100)
        cells = [c for c in self.cells_from(rows, groups) if c["code"] == "NC"]
        e = D2.estimate(cells)
        self.assertEqual(e["m"], 60 * 6)
        self.assertLess(abs(e["delta_D2"]), 0.04)                                      # standard error sqrt(.05 * .95 / 360) = 0.011
        self.assertTrue(0.02 < e["pi_NC"] < 0.09, e["pi_NC"])
        self.assertTrue(0.048 < e["rbar"] <= 0.0501)
        e1 = D2.e1_decision(cells)
        self.assertTrue(e1["run"])
        lo, hi = e1["result"]["interval95"]
        self.assertTrue(lo < 0 < hi, (lo, hi))
        self.assertTrue(D2.reading_label(e1).startswith("CALIBRATION"))

    def test_effect_in_negative_control_families_is_seen(self):
        rows, arrays, groups = many_designs(40, 300, shift={"alpha": 9.0, "beta": 9.0})
        cells = [c for c in self.cells_from(rows, groups) if c["code"] == "NC"]
        e = D2.estimate(cells)
        self.assertGreater(e["delta_D2"], 0.25)
        e1 = D2.e1_decision(cells)
        self.assertTrue(e1["result"]["interval95"][0] > 0)
        self.assertTrue(D2.reading_label(e1).startswith("OVER-REJECTION"))
        # the same designs, but the shifted families are not negative controls: delta back near 0
        pairs = {("g_all", f): dict(final=("no_channel" if f not in ("alpha", "beta") else "channel"), c1="x", c2="x", route="agreed", third="", strict=False) for f in FAMS}
        bg, bd = D2.registered_maps([])
        cells2 = [c for c in D2.build_cells(rows, groups, pairs, bg, bd) if c["code"] == "NC"]
        self.assertLess(abs(D2.estimate(cells2)["delta_D2"]), 0.06)

    def test_conservative_line_gives_negative_delta(self):
        # ATTs much smaller than the placebo spread: nothing crosses the line
        rows, arrays, groups = many_designs(40, 500, att_scale=0.05)
        cells = [c for c in self.cells_from(rows, groups) if c["code"] == "NC"]
        e = D2.estimate(cells)
        self.assertLess(e["delta_D2"], -0.03)
        self.assertTrue(D2.reading_label(D2.e1_decision(cells)).startswith("CONSERVATISM"))

    def test_tabulate(self):
        cells = [dict(design_id="a", policy_family="p", policy_group="g1", family="f1", B=1, r=0.05), dict(design_id="b", policy_family="q", policy_group="g1", family="f2", B=0, r=0.05),
                 dict(design_id="b", policy_family="q", policy_group="g2", family="f1", B=0, r=0.05)]
        t = D2.tabulate(cells, "policy_group")
        self.assertEqual(t["g1"]["cells"], 2)
        self.assertEqual(t["g1"]["beyond"], 1)
        self.assertEqual(list(D2.tabulate(cells, "family")), ["f1", "f2"])


class EmpiricalNull(unittest.TestCase):
    def blocks(self, att_scale, seed0=700, n=25):
        rows, arrays, groups = many_designs(n, seed0, att_scale=att_scale)
        nc = {(d, f) for d in arrays for f in FAMS}
        return D2.nc_blocks(arrays, nc)

    def test_observed_sd_inside_the_reference_under_the_null(self):
        w = D2.null_width(self.blocks(1.0))
        self.assertEqual(w["n_series"], 25 * 12)
        self.assertTrue(w["reference_p2.5"] <= w["observed_sd_z"] <= w["reference_p97.5"], w)
        self.assertTrue(0.9 < w["reference_mean_sd"] < 1.1)
        self.assertEqual(w["seed"], [20261011, 32, 7])

    def test_inflated_atts_widen_the_observed_sd(self):
        w = D2.null_width(self.blocks(2.5))
        self.assertGreater(w["observed_sd_z"], w["reference_p97.5"])
        self.assertLess(w["share_reference_sd_ge_observed"], 0.001)
        self.assertGreater(w["ratio_observed_to_reference_mean"], 2.0)

    def test_null_width_is_deterministic_and_uses_one_draw_per_design(self):
        b = self.blocks(1.0, n=6)
        w1, w2 = D2.null_width(b), D2.null_width(b)
        self.assertEqual(w1, w2)
        # plain-loop reference for a few replicates with the same random numbers
        rng = np.random.default_rng([20261011, 32, 7])
        U = rng.random((50, len(b)))
        manual = []
        for r in range(50):
            vals = []
            for j, blk in enumerate(b):
                n = blk["n"]
                idx = min(int(U[r, j] * n), n - 1)
                vals += [blk["zstar"][k][idx] for k in range(len(blk["z"]))]
            m = sum(vals) / len(vals)
            manual.append(math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1)))
        w3 = D2.null_width(b, R_=50)
        self.assertAlmostEqual(w3["reference_mean_sd"], sum(manual) / 50, places=10)

    def test_series_level_share(self):
        s = D2.series_level(self.blocks(1.0, n=60))
        self.assertTrue(0.02 < s["share_beyond"] < 0.09, s)
        self.assertTrue(0.045 < s["share_expected"] <= 0.0501)
        self.assertIsNone(D2.series_level([]))
        self.assertIsNone(D2.null_width([]))

    def test_nc_blocks_select_only_nc_series(self):
        rows, arrays, groups = many_designs(3, 900)
        blocks = D2.nc_blocks(arrays, {("d000", "alpha"), ("d002", "gamma")})
        self.assertEqual([b["design_id"] for b in blocks], ["d000", "d002"])
        self.assertEqual(blocks[0]["sids"], ["alpha_0", "alpha_1"])


class Coverage(unittest.TestCase):
    def test_design_table_codes_on_synthetic_sweep_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            f = synthetic(21)
            thin = synthetic(22, fams=FAMS[:4])                                    # only four families: D1 SUPPORT
            for name, g in (("ok", f), ("thin", thin)):
                np.savez(tmp / f"{name}.npz", series_ids=np.array(g["sids"]), family=np.array(g["fam"]), code=np.array(g["code"]), att=g["att"], sd=g["sd"],
                         placebo=g["placebo"], meta_json=np.array(json.dumps(g["meta"])))
            mk = lambda did, status="estimated", group="registry_terminal": dict(design_id=did, status=status, gate="g", domain="x", policy_family=None, source_group=group)
            roster = [mk("ok"), mk("thin"), mk("pre", "not_evaluable"), mk("arch", group="prototype"), mk("nogate"), mk("failed"), mk("fullyear"), mk("missing")]
            gate_by = {"ok": {"status": "PASS"}, "thin": {"status": "PASS"}, "arch": {"status": "PASS"}, "failed": {"status": "REPRO_FAIL", "reasons": ["draws"]},
                       "fullyear": {"status": "PASS", "adapter": "annual_full_year_v1"}, "missing": {"status": "PASS"}, "pre": {"status": "PASS"}}
            swept = {"ok": {"file": "ok.npz"}, "thin": {"file": "thin.npz"}}
            rows, arrays = D2.design_table(roster, gate_by, swept, {"ok": {"alpha"}}, tmp)
            codes = {r["design_id"]: r["code"] for r in rows}
            self.assertEqual(codes, {"ok": "EVAL", "thin": "D1_SUPPORT", "pre": "STOP_PRE_RUN", "arch": "FIELD_MISSING", "nogate": "OTHER", "failed": "REPRO_FAIL",
                                     "fullyear": "NO_SECONDARY_ADAPTER", "missing": "OTHER"})
            self.assertEqual(list(arrays), ["ok"])
            ok = [r for r in rows if r["design_id"] == "ok"][0]
            self.assertEqual(sorted(ok["family_stats"]), sorted(FAMS))
            self.assertEqual(ok["policy_family"], "ok")                           # a design without a policy family is its own family


class CheckerAgreesWithBuilder(unittest.TestCase):
    """The plain-loop functions of check_d2_independent.py give the builder's numbers on invented data (so a disagreement on the real sweep points to the data)."""

    def test_family_statistics_and_series_arrays(self):
        for seed, shift in ((31, None), (32, {"beta": 5.0})):
            f = synthetic(seed, shift=shift)
            f["placebo"][1, :40] = np.nan                                          # one series loses 40 draws: the common draw set is 959
            f["sd"][1] = np.nanstd(f["placebo"][1], ddof=1)
            st = stats_of(f)
            a = D2.design_arrays(f, st)
            out = CHK.plain_design(f["sids"], f["fam"], f["code"], f["att"].tolist(), f["placebo"].tolist())
            self.assertEqual(out["n"], st["n_draws"])
            for fam, s in st["family_stats"].items():
                c = out["families"][fam]
                self.assertAlmostEqual(c["M"], s["M"], places=10)
                self.assertAlmostEqual(c["q"], s["q"], places=10)
                self.assertEqual(c["beyond"], bool(s["beyond"]))
                self.assertAlmostEqual(c["r"], s["share_draws_beyond"], places=12)
                self.assertEqual(c["top_series"], s["top_series"])
                self.assertAlmostEqual(c["top_z"], s["top_z"], places=10)
            for i, sid in enumerate(a["sids"]):
                x = out["series"][sid]
                self.assertAlmostEqual(x["z"], a["z"][i], places=10)
                self.assertTrue(np.allclose(x["zstar"], a["zstar"][i]))
                self.assertEqual(x["beyond_own"], bool(a["beyond_own"][i]))
                self.assertAlmostEqual(x["exp_own"], a["exp_own"][i], places=12)

    def test_not_eval_cases_agree(self):
        f = synthetic(33, fams=FAMS[:4])
        self.assertEqual(stats_of(f)["code"], "SUPPORT")
        self.assertIsNone(CHK.plain_design(f["sids"], f["fam"], f["code"], f["att"].tolist(), f["placebo"].tolist()))

    def test_e1_estimate_and_null_width(self):
        rows, arrays, groups = many_designs(40, 1200, shift={"alpha": 2.5})
        pairs = {("g_all", f): dict(final="no_channel", c1="no_channel", c2="no_channel", route="agreed", third="", strict=True) for f in FAMS}
        bg, bd = D2.registered_maps([])
        cells = [c for c in D2.build_cells(rows, groups, pairs, bg, bd) if c["code"] == "NC"]
        e = D2.e1_decision(cells)["result"]
        c = CHK.e1([x["B"] - x["r"] for x in cells], [x["policy_family"] for x in cells])
        for k, kk in (("theta", "theta"), ("se", "se"), ("t", "t"), ("c95", "c95"), ("p_two_sided", "p")):
            self.assertAlmostEqual(e[k], c[kk], places=10)
        self.assertAlmostEqual(e["interval95"][0], c["lo"], places=10)
        est, mine = D2.estimate(cells), CHK.estimate([dict(x, B=x["B"]) for x in cells])
        for k in ("m", "n_beyond", "pi_NC", "rbar", "delta_D2", "expected_beyond", "F", "n_designs"):
            self.assertAlmostEqual(est[k], mine[k], places=12)
        blocks = D2.nc_blocks(arrays, {(d, f) for d in arrays for f in FAMS})
        plain_blocks = [dict(z=b["z"].tolist(), zstar=b["zstar"].tolist(), n=b["n"]) for b in blocks]
        w1, w2 = D2.null_width(blocks, R_=400), CHK.null_width(plain_blocks, R=400)
        for k, kk in (("observed_sd_z", "observed_sd_z"), ("reference_mean_sd", "reference_mean_sd"), ("reference_p2.5", "reference_p25"), ("reference_p97.5", "reference_p975"),
                      ("share_reference_sd_ge_observed", "share_reference_sd_ge_observed"), ("ratio_observed_to_reference_mean", "ratio_observed_to_reference_mean")):
            self.assertAlmostEqual(w1[k], w2[kk], places=10)


class Output(unittest.TestCase):
    def test_write_once_refuses_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a" / "x.json"
            D2.write_once(p, "{}\n")
            self.assertEqual(p.read_text(), "{}\n")
            with self.assertRaises(SystemExit):
                D2.write_once(p, "{}\n")

    def test_csv_text(self):
        self.assertEqual(D2.csv_text(["a", "b"], [[1, None], ["x,y", 2]]), 'a,b\n1,\n"x,y",2\n')


if __name__ == "__main__":
    unittest.main(verbosity=2)
