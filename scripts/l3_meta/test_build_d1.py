"""Checks of build_d1.py on INVENTED arrays (no repo data file is read, nothing is written).

  * the statistics of one design (M_if, q_if, N_i, mu_i, tau_i, Z_i, p_i) against a plain-loop recomputation;
  * the rule for the draw set (series with too few feasible draws leave until 900 common draws remain), the SUPPORT cases, the SD_ZERO case;
  * a synthetic null: independent series without any effect give Z around 0 and about 5% of the series beyond their own line;
  * a synthetic effect in one family is seen in that family and raises Z;
  * the E1 floor.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/test_build_d1.py
"""
import math
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import build_d1 as D  # noqa: E402

FAMS = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]


def synthetic(seed, per_family=3, shift=None, n_draws=999, infeasible=None, scale=1.0):
    """Series ids, families, codes, att, own sd and placebo matrix of an invented design; every series has independent N(0, scale^2) placebo draws and an ATT drawn from the same law."""
    rng = np.random.default_rng(seed)
    sids, fam = [], []
    for f in FAMS:
        for j in range(per_family):
            sids.append(f"{f}_{j}")
            fam.append(f)
    K = len(sids)
    placebo = rng.normal(0, scale, (K, n_draws))
    att = rng.normal(0, scale, K)
    if shift:
        for i, f in enumerate(fam):
            if f in shift:
                att[i] += shift[f]
    placebo = placebo.copy()
    if infeasible:
        for i, cols in infeasible.items():
            placebo[i, cols] = np.nan
    sd = np.array([np.nanstd(placebo[i], ddof=1) for i in range(K)])
    return sids, fam, ["SUPPORTED"] * K, att, sd, placebo


def plain(sids, fam, code, att, placebo, reg=()):
    """Plain-loop recomputation of section 6.3 on the supported series with every draw feasible (no removal)."""
    idx = [i for i, c in enumerate(code) if c == "SUPPORTED"]
    n = len(placebo[0])
    sd = {}
    for i in idx:
        vals = [placebo[i][b] for b in range(n)]
        m = sum(vals) / n
        sd[i] = math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))
    out = {}
    nstar = [0] * n
    N = 0
    for f in sorted(set(fam[i] for i in idx)):
        members = [i for i in idx if fam[i] == f]
        M = max(abs(att[i] / sd[i]) for i in members)
        Mstar = [max(abs(placebo[i][b] / sd[i]) for i in members) for b in range(n)]
        q = sorted(Mstar)[math.ceil(0.95 * n) - 1]
        out[f] = (M, q, M > q)
        N += int(M > q)
        for b in range(n):
            nstar[b] += int(Mstar[b] > q)
    mu = sum(nstar) / n
    tau = math.sqrt(sum((v - mu) ** 2 for v in nstar) / (n - 1))
    p = (1 + sum(1 for v in nstar if v >= N)) / (n + 1)
    return out, N, mu, tau, (N - mu) / tau, p


class Formulas(unittest.TestCase):
    def test_statistics_equal_plain_loops(self):
        for seed, shift in ((1, None), (2, {"alpha": 4.0, "gamma": 5.0}), (3, {"beta": 3.2})):
            args = synthetic(seed, per_family=2, shift=shift, n_draws=999)
            st = D.design_statistics(*args)
            self.assertEqual(st["code"], "EVAL")
            sids, fam, code, att, sd, placebo = args
            out, N, mu, tau, Z, p = plain(sids, fam, code, att, placebo)
            self.assertEqual(st["N"], N)
            self.assertAlmostEqual(st["mu"], mu, places=12)
            self.assertAlmostEqual(st["tau"], tau, places=12)
            self.assertAlmostEqual(st["Z"], Z, places=10)
            self.assertAlmostEqual(st["p"], p, places=12)
            for f, (M, q, beyond) in out.items():
                self.assertAlmostEqual(st["family_stats"][f]["M"], M, places=12)
                self.assertAlmostEqual(st["family_stats"][f]["q"], q, places=12)
                self.assertEqual(st["family_stats"][f]["beyond"], beyond)

    def test_effect_in_one_family_is_seen(self):
        args = synthetic(5, per_family=3, shift={"delta": 8.0})
        st = D.design_statistics(*args)
        self.assertTrue(st["family_stats"]["delta"]["beyond"])
        self.assertGreaterEqual(st["N"], 1)
        self.assertGreater(st["Z"], 1.0)

    def test_registered_families_leave_the_off_count(self):
        args = synthetic(5, per_family=3, shift={"delta": 8.0})
        st = D.design_statistics(*args, registered_families=["delta"])
        self.assertEqual(st["N"] - st["N_off"], 1 if st["family_stats"]["delta"]["beyond"] else 0)
        self.assertEqual(st["families_registered"], ["delta"])


class DrawSet(unittest.TestCase):
    def test_series_with_few_feasible_draws_leave_until_900_remain(self):
        sids, fam, code, att, sd, placebo = synthetic(7, per_family=3)
        bad = {0: list(range(0, 150)), 5: list(range(100, 300))}       # 150 and 200 infeasible draws, overlapping 50: 300 uncommon in all
        sids, fam, code, att, sd, placebo = synthetic(7, per_family=3, infeasible=bad)
        feas = np.isfinite(placebo)
        self.assertLess(int(feas.all(axis=0).sum()), 900)
        st = D.design_statistics(sids, fam, code, att, sd, placebo)
        self.assertEqual(st["code"], "EVAL")
        self.assertGreaterEqual(st["n_draws"], 900)
        self.assertEqual(set(st["removed_series"].values()), {"DRAWS_COMMON"})
        self.assertIn(sids[5], st["removed_series"])                     # the series with the fewest feasible draws goes first
        self.assertNotIn(sids[0], st["removed_series"]) if st["n_draws"] >= 900 and len(st["removed_series"]) == 1 else None

    def test_tie_removes_the_larger_series_id(self):
        bad = {0: list(range(0, 120)), 1: list(range(500, 620)), 2: list(range(700, 820))}
        sids, fam, code, att, sd, placebo = synthetic(8, per_family=3, infeasible=bad)
        st = D.design_statistics(sids, fam, code, att, sd, placebo)
        self.assertEqual(st["code"], "EVAL")
        first = list(st["removed_series"])[0]
        self.assertEqual(first, max(sids[:3]))

    def test_support_when_too_few_common_draws_cannot_be_repaired(self):
        # five families only; every series loses a different block of draws: removal stops at five families
        per = 1
        sids, fam, code, att, sd, placebo = synthetic(9, per_family=per)
        keep = FAMS[:5]
        sel = [i for i, f in enumerate(fam) if f in keep]
        sids, fam, code, att, sd, placebo = ([x[i] for i in sel] if not isinstance(x, np.ndarray) else x[sel] for x in (sids, fam, code, att, sd, placebo))
        placebo = placebo.copy()
        for k in range(len(sids)):
            placebo[k, k * 100:(k + 1) * 100 + 50] = np.nan
        st = D.design_statistics(sids, fam, code, att, sd, placebo)
        self.assertEqual(st["code"], "SUPPORT")
        self.assertTrue(st["reason"].startswith("COMMON_DRAWS") or st["reason"].startswith("FAMILIES_LT_5"))

    def test_fewer_than_five_families(self):
        args = synthetic(10, per_family=2)
        sids, fam, code, att, sd, placebo = args
        code = [c if f in FAMS[:4] else "NO_CELLS" for c, f in zip(code, fam)]
        st = D.design_statistics(sids, fam, code, att, sd, placebo)
        self.assertEqual(st["code"], "SUPPORT")
        self.assertTrue(st["reason"].startswith("FAMILIES_LT_5"))

    def test_sd_zero_series_leave(self):
        sids, fam, code, att, sd, placebo = synthetic(11, per_family=2)
        placebo = placebo.copy()
        placebo[3, :] = 1.25
        sd = sd.copy()
        sd[3] = 0.0
        st = D.design_statistics(sids, fam, code, att, sd, placebo)
        self.assertEqual(st["removed_series"], {sids[3]: "SD_ZERO"})
        self.assertEqual(st["code"], "EVAL")

    def test_unsupported_codes_are_ignored(self):
        sids, fam, code, att, sd, placebo = synthetic(12, per_family=2)
        code = list(code)
        code[0] = "KEPT_LT_80"
        placebo = placebo.copy()
        placebo[0, :] = np.nan
        st = D.design_statistics(sids, fam, code, att, sd, placebo)
        self.assertEqual(st["n_series"], len(sids) - 1)


class Calibration(unittest.TestCase):
    def test_null_designs_give_z_around_zero_and_five_percent_beyond(self):
        zs, beyond, total, shares = [], 0, 0, []
        for seed in range(150):
            st = D.design_statistics(*synthetic(1000 + seed, per_family=3, n_draws=999))
            zs.append(st["Z"])
            beyond += st["series_beyond_line"]
            total += st["n_series"]
            shares.append(st["placebo_share_beyond_own_line_mean"])
        self.assertLess(abs(float(np.mean(zs))), 0.25)               # standard error 1/sqrt(150) = 0.08
        self.assertTrue(0.02 < beyond / total < 0.09, beyond / total)
        self.assertTrue(0.04 < float(np.mean(shares)) <= 0.0501)

    def test_e1_floor(self):
        rows = [dict(Z=0.1 * i, policy_family=f"f{i % 3}") for i in range(10)]
        self.assertFalse(D.e1_summary(rows)["run"])                  # 3 families < 5
        rows = [dict(Z=0.1 * i - 0.4, policy_family=f"f{i}") for i in range(8)]
        e = D.e1_summary(rows)
        self.assertTrue(e["run"])
        self.assertEqual(e["result"]["status"], "OK")
        self.assertEqual(e["seed"], [20261011, 31])
        self.assertFalse(D.e1_summary(rows[:7])["run"])


class Patterns(unittest.TestCase):
    def test_pattern_counts(self):
        rows = [dict(design_id=f"d{i}", N=i % 4, mu=0.3, p=0.01 if i == 0 else 0.5, P_Nstar_ge={"1": 0.25, "2": 0.05, "3": 0.01}) for i in range(10)]
        pc = D.pattern_counts(rows)
        self.assertEqual(pc["designs_p_le_05"], 1)
        self.assertAlmostEqual(pc["designs_p_le_05_expected"], 0.5)
        self.assertEqual(pc["designs_N_ge"][1], 7)
        self.assertAlmostEqual(pc["designs_N_ge_expected"][1], 2.5)
        self.assertEqual(len(pc["top5_designs"]), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
