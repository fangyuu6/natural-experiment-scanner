"""Checks of selref_core.py and build_selref.py on INVENTED data (no repo data file is read; files are written only into a temporary folder).

  * the constructions of the amendment (2.4): lower median, average ranks, standardisation, ridge index against the normal equations, PC1, block sizes and K_eff, fixed
    units, region blocks, the noise covariates, determinism, timing blocks that group early and late adopters;
  * the permutations (2.5): inside blocks, fixed units fixed, the uniform reference reproduces the shuffle loop of SWEEP-V1 and the label bundles of the engine's
    predraw_labels, diagnostics, comparison with a SWEEP-V1 design file;
  * the closed form of the spread (3.2) against explicit enumeration, its bootstrap against explicit loops, the Q1 bootstrap, the paired change, the labels and the verdict
    table (4), residual widening (3.7);
  * a mechanism test: when the assignment depends on a covariate that also drives the outcome's change (true effect 0), the uniform reassignment test over-rejects and the
    block reassignment test is near 5%; without selection both are near 5%;
  * hash-record and write-once checks.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/test_build_selref.py
"""
import math
import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import build_selref as BS  # noqa: E402
import selref_core as R  # noqa: E402

STATES = ["AK", "AL", "AR", "AZ", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "IA", "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MS", "MT",
          "NC", "ND", "NE", "NH", "NJ", "NM", "NV", "NY", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY"]


def invented(n=40, seed=3, missing=()):
    """n state codes, adoption periods (None for never) that rise with the first covariate, and a covariate table for year 2010."""
    rng = random.Random(seed)
    units = sorted(STATES[:n])
    x1 = {u: rng.gauss(0, 1) for u in units}
    cov = {}
    for u in units:
        cov[(u, 2010)] = (float(x1[u] > 0), x1[u] + 0.3 * rng.gauss(0, 1), 0.5 * x1[u] + rng.gauss(0, 1), rng.gauss(0, 1))
    for u in missing:
        cov[(u, 2010)] = (None,) + cov[(u, 2010)][1:]
    order = sorted(units, key=lambda u: -x1[u])
    adopt = {}
    for rank, u in enumerate(order):
        adopt[u] = None if rank >= 25 else 2000 + rank // 5
    return units, [adopt[u] for u in units], cov, x1


def solve_plain(A, b):
    """Gauss-Jordan with partial pivoting."""
    n = len(A)
    M = [list(map(float, A[i])) + [float(b[i])] for i in range(n)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [x - f * y for x, y in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


class Pieces(unittest.TestCase):
    def test_lower_median(self):
        self.assertEqual(R.lower_median([3, 1, 2]), 2)
        self.assertEqual(R.lower_median([4, 1, 3, 2]), 2)
        self.assertEqual(R.lower_median([2007, 2001]), 2001)
        with self.assertRaises(ValueError):
            R.lower_median([])

    def test_average_ranks_with_ties_and_never(self):
        r = R.average_ranks([3, 1, 2, 2, math.inf, math.inf])
        self.assertEqual(r, [4.0, 1.0, 2.5, 2.5, 5.5, 5.5])
        self.assertEqual(R.average_ranks([5, 5, 5]), [2.0, 2.0, 2.0])

    def test_standardise_ddof1_and_drops_constant(self):
        X = [[1.0, 5.0, 2.0], [2.0, 5.0, 4.0], [3.0, 5.0, 9.0], [4.0, 5.0, 1.0]]
        Z, kept = R.standardise(X)
        self.assertEqual(kept, [0, 2])
        self.assertTrue(np.allclose(Z.mean(axis=0), 0))
        self.assertTrue(np.allclose(Z.std(axis=0, ddof=1), 1))

    def test_ridge_index_matches_normal_equations(self):
        rng = np.random.default_rng(5)
        Z = R.standardise(rng.normal(size=(30, 4)))[0]
        s = rng.normal(size=30)
        idx, beta = R.ridge_index(Z, s)
        A = (Z.T @ Z + np.eye(4)).tolist()
        b = (Z.T @ (s - s.mean())).tolist()
        self.assertTrue(np.allclose(beta, solve_plain(A, b), atol=1e-10))
        self.assertTrue(np.allclose(idx, Z @ beta))

    def test_pc1_finds_the_dominant_axis(self):
        rng = np.random.default_rng(8)
        t = rng.normal(size=60)
        X = np.column_stack([t + 0.1 * rng.normal(size=60), t + 0.1 * rng.normal(size=60), -t + 0.1 * rng.normal(size=60), rng.normal(size=60)])
        Z = R.standardise(X)[0]
        idx = R.pc1_index(Z)
        self.assertGreater(abs(np.corrcoef(idx, t)[0, 1]), 0.97)

    def test_split_sorted_sizes_and_effective_blocks(self):
        self.assertEqual([len(g) for g in R.split_sorted(list(range(47)), 5)], [10, 10, 9, 9, 9])
        self.assertEqual([len(g) for g in R.split_sorted(list(range(45)), 5)], [9] * 5)
        self.assertEqual(R.split_sorted([7, 3, 5, 1], 2), [[7, 3], [5, 1]])
        for K, n, want in ((5, 45, 5), (8, 45, 8), (8, 38, 7), (5, 18, 3), (5, 9, 1), (3, 17, 3), (3, 4, 1)):
            self.assertEqual(R.effective_blocks(K, n), want, (K, n))

    def test_seed_for(self):
        self.assertEqual(R.seed_for("U", "d"), 20261002)
        self.assertEqual(R.seed_for("P5", "d1"), "SELREF-V1|P5|d1")

    def test_region_table_is_complete_and_partitions(self):
        self.assertEqual(len(R.REGION), 51)
        self.assertEqual(R.REGION["DC"], "South")
        self.assertEqual(R.REGION["CA"], "West")
        self.assertEqual(R.REGION["IL"], "Midwest")
        self.assertEqual(R.REGION["NJ"], "Northeast")
        self.assertEqual(sorted(set(R.REGION.values())), sorted(R.REGION_ORDER))


class Construction(unittest.TestCase):
    def test_uniform_reference(self):
        units, adopt, cov, _ = invented()
        ref = R.build_reference("U", "d", units, adopt, 2010, cov)
        self.assertEqual(ref["blocks"], [list(range(len(units)))])
        self.assertEqual((ref["fixed"], ref["K_eff"], ref["seed"]), ([], 1, R.RI_SEED))

    def test_blocks_partition_the_free_units_and_fixed_units_are_those_with_a_blank(self):
        units, adopt, cov, _ = invented(n=44, missing=("AL",))
        for rid in ("P5", "P3", "P8", "PC5", "N5"):
            ref = R.build_reference(rid, "dX", units, adopt, 2010, cov)
            al = units.index("AL")
            self.assertEqual(ref["fixed"], [al])
            flat = sorted(p for b in ref["blocks"] for p in b)
            self.assertEqual(flat, [i for i in range(len(units)) if i != al])
            self.assertTrue(all(b == sorted(b) for b in ref["blocks"]))
            sizes = [len(b) for b in ref["blocks"]]
            self.assertLessEqual(max(sizes) - min(sizes), 1)
            self.assertGreaterEqual(min(sizes), R.MIN_BLOCK)
            self.assertEqual(ref["K_eff"], len(ref["blocks"]))
        self.assertEqual(R.build_reference("P5", "dX", units, adopt, 2010, cov)["K_eff"], 5)
        self.assertEqual(R.build_reference("P3", "dX", units, adopt, 2010, cov)["K_eff"], 3)
        self.assertEqual(R.build_reference("P8", "dX", units, adopt, 2010, cov)["K_eff"], 8)

    def test_deterministic(self):
        units, adopt, cov, _ = invented()
        a = R.build_reference("N5", "dX", units, adopt, 2010, cov)
        b = R.build_reference("N5", "dX", units, adopt, 2010, cov)
        self.assertEqual(a["blocks"], b["blocks"])
        c = R.build_reference("N5", "dY", units, adopt, 2010, cov)
        self.assertNotEqual(a["blocks"], c["blocks"])                       # the noise covariates depend on the design id
        self.assertEqual(R.build_reference("P5", "dX", units, adopt, 2010, cov)["blocks"], R.build_reference("P5", "dY", units, adopt, 2010, cov)["blocks"])

    def test_timing_blocks_group_early_and_late_adopters(self):
        units, adopt, cov, x1 = invented(n=50)
        ref = R.build_reference("P5", "dX", units, adopt, 2010, cov)
        mean_rank = []
        keys = [math.inf if a is None else a for a in adopt]
        ranks = R.average_ranks(keys)
        for b in ref["blocks"]:
            mean_rank.append(np.mean([ranks[i] for i in b]))
        self.assertTrue(np.all(np.diff(mean_rank) >= -1e-9))                # block 1 holds the earliest adopters, the last blocks the never-treated
        self.assertGreater(max(mean_rank) - min(mean_rank), 20)             # early adopters and never-treated sit in different blocks
        # the covariate-only blocks are close to the covariate axis, the noise blocks are not
        pc = R.build_reference("PC5", "dX", units, adopt, 2010, cov)
        xs = [np.mean([x1[units[i]] for i in b]) for b in pc["blocks"]]
        self.assertGreater(np.ptp(xs), 1.0)

    def test_constant_response_falls_back_to_pc1(self):
        units, _, cov, _ = invented()
        never = [None] * len(units)
        a = R.build_reference("P5", "dX", units, never, 2010, cov)
        b = R.build_reference("PC5", "dX", units, never, 2010, cov)
        self.assertEqual(a["blocks"], b["blocks"])

    def test_constant_covariates_are_an_error(self):
        units, adopt, _, _ = invented()
        cov = {(u, 2010): (1.0, 2.0, 3.0, 4.0) for u in units}
        with self.assertRaises(ValueError):
            R.build_reference("P5", "dX", units, adopt, 2010, cov)

    def test_region_blocks(self):
        units, adopt, cov, _ = invented(n=50, missing=("FL",))
        ref = R.build_reference("REG", "dX", units, adopt, 2010, cov)
        got = [sorted({R.REGION[units[i]] for i in b}) for b in ref["blocks"]]
        self.assertTrue(all(len(g) == 1 for g in got))
        self.assertEqual([g[0] for g in got], [r for r in R.REGION_ORDER])
        self.assertEqual(ref["fixed"], [units.index("FL")])
        self.assertNotIn(units.index("FL"), [p for b in ref["blocks"] for p in b])

    def test_design_inputs_uses_period_years_when_present(self):
        class P:
            pass
        class Ctx:
            pass
        ctx = Ctx()
        ctx.primary = P()
        ctx.primary.design = dict(units=["AA", "BB", "CC", "DD"], adoption={"AA": 4, "BB": None, "CC": 6, "DD": 5}, operative={"AA": 3, "BB": None, "CC": 5, "DD": 4})
        ctx.primary.declaration = dict(period_years=list(range(2000, 2012)))
        units, adoption, ystar = R.design_inputs(ctx)
        self.assertEqual((units, adoption), (["AA", "BB", "CC", "DD"], [4, None, 6, 5]))
        self.assertEqual(ystar, 2004)                                       # operative 3, 4, 5 -> 2003, 2004, 2005; lower median 2004
        ctx.primary.declaration = dict(period_years=None)
        ctx.primary.design["operative"] = {"AA": 2003, "BB": None, "CC": 2005, "DD": 2004}
        self.assertEqual(R.design_inputs(ctx)[2], 2004)


class Permutations(unittest.TestCase):
    def test_perms_are_permutations_inside_blocks_and_fixed_stay(self):
        blocks = [[0, 2, 4, 6], [1, 3, 5], [8, 9]]
        perms = R.make_perms(blocks, 11, 60, "seed")
        for perm in perms:
            self.assertEqual(sorted(perm), list(range(11)))
            self.assertEqual(perm[7], 7)
            self.assertEqual(perm[10], 10)
            for b in blocks:
                self.assertEqual(sorted(perm[p] for p in b), sorted(b))
        self.assertEqual(perms, R.make_perms(blocks, 11, 60, "seed"))
        self.assertNotEqual(perms, R.make_perms(blocks, 11, 60, "other"))

    def test_uniform_perms_equal_the_shuffle_loop_of_the_sweep(self):
        n = 23
        mine = R.make_perms([list(range(n))], n, 40, R.RI_SEED)
        rng = random.Random(R.RI_SEED)
        base = []
        for _ in range(40):
            idx = list(range(n))
            rng.shuffle(idx)
            base.append(idx)
        self.assertEqual(mine, base)

    def test_primary_bundles_equal_predraw_labels_for_the_uniform_blocks(self):
        units = ["AA", "BB", "CC", "DD", "EE"]
        design = dict(units=units, adoption={"AA": 5, "BB": None, "CC": 7, "DD": None, "EE": 5}, operative={"AA": 4, "BB": None, "CC": 6, "DD": None, "EE": 4},
                      exposure={u: {1: 0, 2: 1} for u in units}, whole_window_clean={"AA": False, "BB": True, "CC": False, "DD": True, "EE": False})
        # engine.ri.predraw_labels, reproduced
        labels = [(design["adoption"][u], None if design["adoption"][u] is None else design["operative"][u] - design["adoption"][u], design["exposure"][u],
                   design["whole_window_clean"][u]) for u in units]
        rng = random.Random(R.RI_SEED)
        want = []
        for _ in range(30):
            sh = list(labels)
            rng.shuffle(sh)
            want.append(sh)
        got = list(R.primary_bundles(design, [list(range(5))], R.RI_SEED, n_draws=30))
        self.assertEqual(got, want)

    def test_diagnostics(self):
        adoption = [1, 1, None, None]
        perms = [[0, 1, 2, 3], [1, 0, 2, 3], [2, 3, 0, 1]]
        d = R.perm_diagnostics(perms, adoption)
        self.assertAlmostEqual(d["share_identical_to_observed"], 2 / 3)
        self.assertAlmostEqual(d["mean_moved"], 4 / 3)
        self.assertAlmostEqual(d["mean_swapped_out"], 2 / 3)

    def test_block_references_move_fewer_treated_units_than_the_uniform_one_under_selection(self):
        units, adopt, cov, _ = invented(n=50)
        n = len(units)
        u = R.perm_diagnostics(R.make_perms([list(range(n))], n, 300, 1), adopt)
        p = R.perm_diagnostics(R.make_perms(R.build_reference("P5", "dX", units, adopt, 2010, cov)["blocks"], n, 300, 1), adopt)
        self.assertLess(p["mean_swapped_out"], 0.8 * u["mean_swapped_out"])

    def test_reference_summary(self):
        units, adopt, cov, _ = invented(n=30, missing=("AL",))
        ref = R.build_reference("P3", "dX", units, adopt, 2010, cov)
        s = R.reference_summary(ref, units, adopt)
        self.assertEqual(s["fixed_units"], ["AL"])
        self.assertEqual(sum(len(b["units"]) for b in s["blocks"]), 29)
        self.assertEqual(sum(b["treated"] for b in s["blocks"]), sum(1 for i, a in enumerate(adopt) if a is not None and units[i] != "AL"))


def fake_arrays(K=4, seed=0):
    rng = np.random.default_rng(seed)
    placebo = rng.normal(size=(K, 20))
    att = rng.normal(size=K)
    code = np.array(["SUPPORTED"] * (K - 1) + ["NO_CELLS"])
    placebo[-1] = np.nan
    att[-1] = np.nan
    return dict(series_ids=np.array([f"s{i}" for i in range(K)]), family=np.array(["a", "a", "b", "b"][:K]), code=code, fingerprint=np.array(["f"] * (K - 1) + [""]),
                att=att, sd=np.array([np.std(p[np.isfinite(p)], ddof=1) if np.isfinite(p).sum() > 1 else np.nan for p in placebo]), cutoff=np.array([1.5, 1.4, 1.3, np.nan][:K]), p2=np.array([0.1, 0.2, 0.3, np.nan][:K]),
                z=np.array([1.0, 2.0, 3.0, np.nan][:K]), placebo=placebo, n_feasible=np.array([20, 20, 20, 0][:K]))


class Comparison(unittest.TestCase):
    def test_identity_and_the_changes_it_must_catch(self):
        old = fake_arrays()
        same = {k: np.copy(v) for k, v in old.items()}
        self.assertTrue(R.compare_with_sweep(same, old, full=True)["ok"])
        bad_att = {k: np.copy(v) for k, v in old.items()}
        bad_att["att"][0] += 1e-6
        self.assertFalse(R.compare_with_sweep(bad_att, old, full=False)["ok"])
        bad_struct = {k: np.copy(v) for k, v in old.items()}
        bad_struct["code"][3] = "NO_DESIGN"
        self.assertFalse(R.compare_with_sweep(bad_struct, old, full=False)["ok"])
        # a change between SUPPORTED and RI_FEASIBLE_LT_90 is allowed unless the comparison is full (reference U)
        supp = {k: np.copy(v) for k, v in old.items()}
        supp["code"][1] = "RI_FEASIBLE_LT_90"
        self.assertTrue(R.compare_with_sweep(supp, old, full=False)["ok"])
        self.assertFalse(R.compare_with_sweep(supp, old, full=True)["ok"])
        drift = {k: np.copy(v) for k, v in old.items()}
        drift["placebo"][0, 3] += 1e-7
        self.assertTrue(R.compare_with_sweep(drift, old, full=False)["ok"])
        self.assertFalse(R.compare_with_sweep(drift, old, full=True)["ok"])

    def test_design_arrays_from_records(self):
        recs = {"s1": dict(family="a", code="SUPPORTED", detail="", fingerprint="f", n_cells=5, n_units=3, kept=1.0, n_feasible=18, support_pass_l2=True, strict_dropped=0,
                           att=0.5, placebo=np.concatenate([np.linspace(-1, 1, 18), [np.nan, np.nan]])),
                "s2": dict(family="b", code="NO_CELLS", detail="", fingerprint="", n_cells=0, n_units=0, kept=float("nan"), n_feasible=0, support_pass_l2=None, strict_dropped=0,
                           att=float("nan"), placebo=np.full(20, np.nan))}
        a = R.design_arrays_from_records(dict(records=recs))
        self.assertEqual(a["placebo"].shape, (2, 20))
        self.assertTrue(np.isnan(a["sd"][1]) and np.isfinite(a["sd"][0]))
        self.assertEqual(a["n_feasible"].tolist(), [18, 0])
        self.assertEqual(a["support_pass_l2"].tolist(), [1, -1])


def synthetic_blocks(n_designs, series_per_design, n_draws=300, att_scale=1.0, seed=0, shift_sd=0.0):
    """D2.nc_blocks-like blocks: z = att/sd and zstar = placebo/sd per series; optional between-series centre shift in the placebo draws."""
    rng = np.random.default_rng(seed)
    out = []
    for d in range(n_designs):
        k = series_per_design
        shift = rng.normal(0, shift_sd, size=(k, 1)) if shift_sd else np.zeros((k, 1))
        plc = shift + rng.normal(0, 1, size=(k, n_draws))
        att = shift[:, 0] + rng.normal(0, att_scale, size=k)
        sd = plc.std(axis=1, ddof=1)
        out.append(dict(design_id=f"d{d:03d}", sids=[f"s{j}" for j in range(k)], z=att / sd, zstar=plc / sd[:, None], n=n_draws,
                        beyond_own=np.zeros(k, bool), exp_own=np.full(k, 0.05)))
    return out


class Spread(unittest.TestCase):
    def test_closed_form_equals_explicit_enumeration(self):
        rng = np.random.default_rng(1)
        # two designs: 2 series with 3 common draws, 1 series with 4 draws
        z1, zs1 = rng.normal(size=2), rng.normal(size=(2, 3))
        z2, zs2 = rng.normal(size=1), rng.normal(size=(1, 4))
        stats = [BS.spread_design_stats(z1, zs1), BS.spread_design_stats(z2, zs2)]
        k, v_obs, v_ref = BS.spread_point(stats)
        zobs = np.concatenate([z1, z2])
        self.assertAlmostEqual(v_obs, float(np.var(zobs, ddof=1)), places=12)
        acc, cnt = 0.0, 0
        for b1 in range(3):
            for b2 in range(4):
                zz = np.concatenate([zs1[:, b1], zs2[:, b2]])
                acc += float(np.var(zz, ddof=1))
                cnt += 1
        self.assertAlmostEqual(v_ref, acc / cnt, places=12)
        self.assertAlmostEqual(k, math.sqrt(v_obs / v_ref), places=12)

    def test_bootstrap_matches_explicit_loops(self):
        rng = np.random.default_rng(2)
        stats, fams = [], []
        for d in range(9):
            kk = int(rng.integers(1, 4))
            stats.append(BS.spread_design_stats(rng.normal(size=kk), rng.normal(size=(kk, 12))))
            fams.append(["fa", "fb", "fc", "fd"][d % 4])
        B_ = 25
        ks, F = BS.bootstrap_spread(stats, fams, B_=B_, seed=[4, 5, 6])
        names = sorted(set(fams))
        draw = np.random.default_rng([4, 5, 6]).integers(0, F, size=(B_, F))
        for b in range(B_):
            chosen = [s for f in draw[b] for s, g in zip(stats, fams) if g == names[f]]
            k, _, _ = BS.spread_point(chosen) if sum(s["n_series"] for s in chosen) > 1 else (float("nan"), 0, 0)
            if math.isnan(k):
                self.assertTrue(math.isnan(ks[b]))
            else:
                self.assertAlmostEqual(ks[b], k, places=10)

    def test_spread_block_calibrated_null_and_inflated_null(self):
        pf = {f"d{d:03d}": f"pf{d % 12}" for d in range(60)}
        calm = BS.spread_block(synthetic_blocks(60, 3, att_scale=1.0, seed=6), pf, B_=400)
        self.assertLess(abs(calm["kappa_v"] - 1.0), 0.1)
        self.assertLess(abs(calm["kappa_closed_form"] - calm["kappa_v"]) / calm["kappa_v"], 0.02)
        lo, hi = calm["interval95"]
        self.assertTrue(lo < 1.0 < hi)
        wide = BS.spread_block(synthetic_blocks(60, 3, att_scale=1.4, seed=3), pf, B_=400)
        self.assertGreater(wide["kappa_v"], 1.25)
        self.assertGreater(wide["interval95"][0], 1.0)
        self.assertEqual(BS.spread_label(wide["interval95"]), "WIDER")
        self.assertEqual(BS.spread_label(calm["interval95"]), "NOT SEPARATED FROM 1")

    def test_a_shared_centre_shift_is_part_of_the_reference_not_of_the_excess(self):
        # observed ATTs and placebo draws are both shifted by the same series-specific amount: the ratio stays near 1 though z is widely spread
        pf = {f"d{d:03d}": f"pf{d % 12}" for d in range(60)}
        blocks = synthetic_blocks(60, 3, att_scale=1.0, seed=4, shift_sd=2.0)
        r = BS.spread_block(blocks, pf, B_=400)
        self.assertGreater(np.std(np.concatenate([b["z"] for b in blocks]), ddof=1), 1.8)
        self.assertLess(abs(r["kappa_v"] - 1.0), 0.15)
        self.assertLess(abs(r["centred"]["kappa_closed_form"] - 1.0), 0.15)

    def test_labels_and_verdict_table(self):
        self.assertEqual(BS.level_label([0.01, 0.1]), "OVER-REJECTION")
        self.assertEqual(BS.level_label([-0.01, 0.1]), "CALIBRATION")
        self.assertEqual(BS.level_label([-0.1, -0.01]), "CONSERVATISM")
        self.assertEqual(BS.change_label([0.01, 0.1]), "REDUCED")
        self.assertEqual(BS.change_label([-0.1, -0.01]), "INCREASED")
        self.assertEqual(BS.change_label([-0.1, 0.1]), "NOT SEPARATED")
        cases = [("CONSERVATISM", "REDUCED", "OVER-CORRECTED"), ("CONSERVATISM", "INCREASED", "OVER-CORRECTED"), ("OVER-REJECTION", "INCREASED", "WORSE: THE RESTRICTED REFERENCE IS NARROWER THAN THE DATA"),
                 ("CALIBRATION", "REDUCED", "THE SELECTION-AWARE REFERENCE CALIBRATES THE LINES"), ("OVER-REJECTION", "REDUCED", "PARTLY CALIBRATED"),
                 ("CALIBRATION", "NOT SEPARATED", "CALIBRATED, BUT NOT SEPARATED FROM THE UNIFORM REFERENCE"),
                 ("OVER-REJECTION", "NOT SEPARATED", "NO CHANGE: SELECTION ON THESE COVARIATES DOES NOT EXPLAIN THE EXCESS")]
        for level, change, want in cases:
            self.assertEqual(BS.verdict_text(level, change), want)
            self.assertEqual(BS.verdict_text(level, change, low_support=True), want + " (LOW SUPPORT)")
        self.assertEqual(BS.verdict_text(None, "REDUCED"), "NOT COMPUTABLE")
        self.assertEqual(BS.verdict_text("CALIBRATION", None), "NOT COMPUTABLE")


class Q1AndPaired(unittest.TestCase):
    def test_q1_bootstrap_matches_explicit_loops(self):
        rng = np.random.default_rng(9)
        n = 14
        blocks = [["fa", "fa", "fb", "fb", "fb", "fc", "fc", "fd", "fd", "fe", "fe", "ff", "ff", "ff"][i] for i in range(n)]
        bn = rng.random(n) < 0.2
        bo = rng.random(n) < 0.35
        B_ = 30
        out = BS.bootstrap_q1(bn, bo, blocks, B_=B_, seed=[1, 2, 3])
        names = sorted(set(blocks))
        F = len(names)
        draw = np.random.default_rng([1, 2, 3]).integers(0, F, size=(B_, F))
        rate, change = [], []
        for b in range(B_):
            idx = [i for f in draw[b] for i in range(n) if blocks[i] == names[f]]
            rate.append(sum(bool(bn[i]) for i in idx) / len(idx))
            change.append((sum(bool(bo[i]) for i in idx) - sum(bool(bn[i]) for i in idx)) / len(idx))
        self.assertTrue(np.allclose(out["rate"], np.percentile(rate, [2.5, 97.5])))
        self.assertTrue(np.allclose(out["excess"], np.percentile(np.array(rate) - 0.05, [2.5, 97.5])))
        self.assertTrue(np.allclose(out["change"], np.percentile(change, [2.5, 97.5])))

    def test_q1_roster_rule(self):
        ex1 = {"designs": [dict(design_id="a", status="estimated", b=1.0, noise_sd=0.5, noise_cutoff=0.9, support_pass=True, policy_family="pa", group3="registry"),
                           dict(design_id="b", status="estimated", b=1.0, noise_sd=0.0, noise_cutoff=0.9, support_pass=True, policy_family="pb", group3="registry"),
                           dict(design_id="c", status="not_evaluable", b=None, noise_sd=None, noise_cutoff=None, policy_family="pc", group3=None),
                           dict(design_id="d", status="estimated", b=-2.0, noise_sd=0.5, noise_cutoff=1.5, support_pass=False, policy_family="pd", group3="registry")]}
        self.assertEqual([r["design_id"] for r in BS.q1_roster(ex1)], ["a"])

    def test_q1_block_counts_and_label(self):
        roster = [dict(design_id=f"d{i}", policy_family=f"p{i % 3}", group3="registry", b=b, cut=1.0) for i, b in enumerate([2.0, 0.5, 1.5, 0.2, 3.0, 0.1])]
        refs = dict(designs=[dict(design_id=f"d{i}", status="OK", q1=dict(att=b, cutoff=cut, support_pass=sp, n_feasible=999)) for i, (b, cut, sp) in
                             enumerate([(2.0, 2.5, True), (0.5, 0.4, True), (1.5, 1.6, True), (0.2, 1.0, True), (3.0, 1.2, True), (0.1, 1.0, False)])])
        out, kept = BS.q1_block(roster, refs)
        self.assertEqual((out["n_recomputable"], out["n_kept"], out["dropped_for_support"]), (6, 5, ["d5"]))
        self.assertEqual((out["k_new"], out["k_old_same_designs"]), (2, 3))
        self.assertEqual(out["designs_beyond_new"], ["d1", "d4"])
        self.assertAlmostEqual(out["expected_nominal"], 0.25)
        self.assertIn(out["label"], ("ABOVE THE NEW LINE", "AT THE NEW LINE", "BELOW THE NEW LINE"))

    def test_paired_change_value_and_floor(self):
        def cell(d, f, pf, B, r):
            return dict(design_id=d, family=f, policy_family=pf, B=B, r=r)
        nc_u = [cell(f"d{i}", "fa", f"p{i % 10}", int(i % 4 == 0), 0.05) for i in range(60)]
        nc_v = [cell(f"d{i}", "fa", f"p{i % 10}", int(i % 8 == 0), 0.05) for i in range(60)]
        out = BS.paired_change(nc_u, nc_v)
        self.assertTrue(out["run"])
        self.assertAlmostEqual(out["result"]["theta"], np.mean([(c["B"] - c["r"]) - (v["B"] - v["r"]) for c, v in zip(nc_u, nc_v)]), places=12)
        self.assertEqual((out["m_common"], out["F"]), (60, 10))
        small = BS.paired_change(nc_u[:20], nc_v[:20])
        self.assertFalse(small["run"])

    def test_residual_widening(self):
        kept = [dict(b_new=2.0, cut_new=1.0), dict(b_new=1.2, cut_new=1.0), dict(b_new=0.5, cut_new=1.0)]
        cells = [dict(design_id="a", M=3.0, q=1.0), dict(design_id="a", M=1.1, q=1.0), dict(design_id="b", M=1.6, q=1.0)]
        eval_rows = [dict(design_id="a", mu=0.5, tau=1.0), dict(design_id="b", mu=0.2, tau=0.5)]
        out = BS.residual_widening(1.5, kept, eval_rows, cells)
        self.assertEqual((out["q1_k"], out["d1_T_D"]), (1, 2))
        self.assertAlmostEqual(out["d1_theta"], np.mean([(1 - 0.5) / 1.0, (1 - 0.2) / 0.5]))


def mechanism(selection, n_sets=250, n=45, n_draws=499, seed=11):
    """Rejection rates (two-sided, 5%) of the uniform and of the block reassignment test; the true effect is 0.

    Unit covariate x is fixed; the outcome's change is gamma x + noise.  With selection the 15 treated units are the top 15 of x + 1.5 noise, without it a random 15."""
    rng = np.random.default_rng(seed)
    x = np.sort(rng.normal(size=n))
    n_t = 15
    blocks = R.split_sorted(list(range(n)), 5)                              # units are sorted by x: consecutive blocks of the covariate
    perms_u = np.array(R.make_perms([list(range(n))], n, n_draws, "u"))
    perms_b = np.array(R.make_perms(blocks, n, n_draws, "b"))
    rej = {"u": 0, "b": 0}
    for _ in range(n_sets):
        if selection:
            order = np.argsort(-(x + 1.5 * rng.normal(size=n)))
        else:
            order = rng.permutation(n)
        label = np.zeros(n)
        label[order[:n_t]] = 1.0
        dy = 1.0 * x + 0.7 * rng.normal(size=n)
        obs = dy[label == 1].mean() - dy[label == 0].mean()
        for key, perms in (("u", perms_u), ("b", perms_b)):
            lab = label[perms]                                              # draw b: unit j carries the label of unit perm_b[j]
            t1 = (lab * dy).sum(axis=1) / lab.sum(axis=1)
            t0 = ((1 - lab) * dy).sum(axis=1) / (1 - lab).sum(axis=1)
            ref = t1 - t0
            p = (1 + np.sum(np.abs(ref) >= abs(obs))) / (n_draws + 1)
            rej[key] += int(p <= 0.05)
    return rej["u"] / n_sets, rej["b"] / n_sets


class Mechanism(unittest.TestCase):
    def test_selection_makes_the_uniform_test_over_reject_and_blocks_restore_the_level(self):
        u, b = mechanism(selection=True)
        self.assertGreater(u, 0.25)
        self.assertLess(b, 0.12)
        self.assertGreater(u - b, 0.15)

    def test_without_selection_both_tests_keep_their_level(self):
        u, b = mechanism(selection=False, seed=12)
        self.assertLess(abs(u - 0.05), 0.04)
        self.assertLess(abs(b - 0.05), 0.04)


class Records(unittest.TestCase):
    def test_hash_record_catches_changes(self):
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            amend = td / "A.md"
            amend.write_text("rules\n")
            listed = td / "input.json"
            listed.write_text("{}\n")
            sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
            rec = td / "REC.txt"
            rec.write_text(f"file: {BS.S.rel(amend)}\nsha256: {sha(amend)}\nbytes: 6\namended_utc (T_amend): 2026-10-16T00:00:00Z\ninputs:\n  {BS.S.rel(listed)} sha256 {sha(listed)}\n")
            probs, _ = BS.verify_selref_records(amend, rec)
            self.assertEqual(probs, [])
            amend.write_text("rules changed\n")
            probs, _ = BS.verify_selref_records(amend, rec)
            self.assertTrue(any("differs from its hash record" in p for p in probs))
            amend.write_text("rules\n")
            listed.write_text('{"x": 1}\n')
            probs, _ = BS.verify_selref_records(amend, rec)
            self.assertTrue(any("differs from the SELREF hash record" in p for p in probs))

    def test_write_once(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sub" / "R.json"
            BS.write_once_json(p, {"a": 1.0})
            with self.assertRaises(SystemExit):
                BS.write_once_json(p, {"a": 2.0})
            self.assertIn('"a": 1.0', p.read_text())

    def test_identity_checker(self):
        ident = {"x": (1.0, 1.0 + 1e-12), "y": (3, 3), "z": (["a"], ["a"])}
        self.assertEqual(BS.check_identities(ident), [])
        ident["w"] = (2.0, 2.1)
        self.assertEqual(len(BS.check_identities(ident)), 1)
        self.assertEqual(len(BS.check_identities({"v": (["a"], ["b"])})), 1)


class CheckerAgreement(unittest.TestCase):
    """The plain-loop checker (written from the amendment text) against the builder, on invented data only."""

    @classmethod
    def setUpClass(cls):
        global CHK, D1, D2, N1
        import check_selref_independent as CHK_
        import build_d1 as D1_
        import build_d2 as D2_
        import build_n1 as N1_
        CHK, D1, D2, N1 = CHK_, D1_, D2_, N1_

    def test_blocks_and_permutations_agree(self):
        units, adopt, cov, _ = invented(n=47, missing=("AL",))
        for rid in ("P5", "P3", "P8", "N5", "REG", "PC5"):
            mine = R.build_reference(rid, "dZ", units, adopt, 2010, cov)
            blocks, fixed = CHK.blocks_plain(rid, "dZ", units, adopt, 2010, cov)
            self.assertEqual(fixed, mine["fixed"], rid)
            if rid == "PC5":
                blocks_neg, _ = CHK.blocks_plain(rid, "dZ", units, adopt, 2010, cov, negate=True)
                self.assertTrue(blocks == mine["blocks"] or blocks_neg == mine["blocks"])      # the sign of the eigenvector is open: one of the two orientations
            else:
                self.assertEqual(blocks, mine["blocks"], rid)
            self.assertEqual(CHK.perms_plain(mine["blocks"], len(units), 25, mine["seed"]), R.make_perms(mine["blocks"], len(units), 25, mine["seed"]))

    def test_ridge_and_pc1_agree(self):
        rng = np.random.default_rng(31)
        Z = R.standardise(rng.normal(size=(40, 4)) + np.outer(rng.normal(size=40), [1, 1, -1, 0.5]))[0]
        s = rng.normal(size=40)
        self.assertTrue(np.allclose(CHK.ridge_plain(Z.tolist(), s.tolist()), R.ridge_index(Z, s)[0], atol=1e-9))
        a, b = np.array(CHK.pc1_plain(Z.tolist())), R.pc1_index(Z)
        self.assertTrue(np.allclose(a, b, atol=1e-7) or np.allclose(a, -b, atol=1e-7))

    def test_family_statistics_e1_null_width_and_estimate_agree(self):
        f = None
        rng = np.random.default_rng(41)
        sids, fam = [], []
        for fm in ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]:
            for j in range(2):
                sids.append(f"{fm}_{j}")
                fam.append(fm)
        K = len(sids)
        placebo = rng.normal(0, 1.0, (K, 999))
        placebo[1, :60] = np.nan                                            # an infeasible stretch for one series
        att = rng.normal(0, 1.3, K)
        sd = np.array([np.nanstd(placebo[i], ddof=1) for i in range(K)])
        st = D1.design_statistics(sids, fam, ["SUPPORTED"] * K, att, sd, placebo, ())
        pl = CHK.plain_design(sids, fam, ["SUPPORTED"] * K, att.tolist(), placebo.tolist())
        for k_ in ("N", "mu", "tau", "Z", "p"):
            self.assertAlmostEqual(pl[k_], st[k_], places=10)
        for fm, s in st["family_stats"].items():
            for a, b in ((pl["families"][fm]["M"], s["M"]), (pl["families"][fm]["q"], s["q"]), (pl["families"][fm]["r"], s["share_draws_beyond"])):
                self.assertAlmostEqual(a, b, places=10)
        y = list(rng.normal(size=30))
        famlist = [f"p{i % 7}" for i in range(30)]
        ids, F = N1.family_ids(famlist)
        a = N1.e1_core(y, ids, F, 400, np.random.default_rng([1, 2]))
        b = CHK.e1_plain(y, famlist, [1, 2], B=400)
        for k_, kk in (("theta", "theta"), ("se", "se"), ("t", "t"), ("c95", "c95"), ("p_two_sided", "p")):
            self.assertAlmostEqual(a[k_], b[kk], places=10)
        blocks = synthetic_blocks(6, 3, n_draws=50, att_scale=1.2, seed=5)
        nw_b = D2.null_width(blocks, R_=300, seed=[3, 4])
        nw_c = CHK.null_width_plain([dict(design_id=x["design_id"], z=list(x["z"]), zstar=x["zstar"].tolist(), n=x["n"]) for x in blocks], R=300, seed=[3, 4])
        self.assertAlmostEqual(nw_b["ratio_observed_to_reference_mean"], nw_c["ratio_observed_to_reference_mean"], places=10)
        cells = [dict(design_id=f"d{i}", policy_family=f"p{i % 3}", B=int(i % 4 == 0), r=0.05) for i in range(20)]
        e_b, e_c = D2.estimate(cells), CHK.estimate_plain(cells)
        for k_ in ("m", "n_beyond", "pi_NC", "rbar", "delta_D2", "expected_beyond", "n_designs", "F"):
            self.assertAlmostEqual(e_b[k_], e_c[k_], places=12)

    def test_spread_closed_form_and_bootstrap_agree(self):
        blocks = synthetic_blocks(10, 3, n_draws=40, att_scale=1.3, seed=8, shift_sd=0.8)
        pf = {b["design_id"]: f"pf{i % 4}" for i, b in enumerate(blocks)}
        stats_b = [BS.spread_design_stats(b["z"], b["zstar"]) for b in blocks]
        stats_c = [CHK.design_spread(list(b["z"]), b["zstar"].tolist()) for b in blocks]
        k_b = BS.spread_point(stats_b)
        k_c = CHK.kappa_closed(stats_c)
        for a, b in zip(k_b, k_c):
            self.assertAlmostEqual(a, b, places=10)
        ks_b, _ = BS.bootstrap_spread(stats_b, [pf[b["design_id"]] for b in blocks], B_=60, seed=SEED_T)
        plain = CHK.spread_plain([dict(design_id=b["design_id"], z=list(b["z"]), zstar=b["zstar"].tolist(), n=b["n"]) for b in blocks], pf, B=60, seed=SEED_T)
        self.assertTrue(np.allclose(np.percentile(ks_b[np.isfinite(ks_b)], [2.5, 97.5]), plain["interval95"]))

    def test_q1_bootstrap_verdict_and_labels_agree(self):
        rng = np.random.default_rng(12)
        n = 16
        blocks = [f"f{i % 6}" for i in range(n)]
        bn, bo = rng.random(n) < 0.2, rng.random(n) < 0.4
        a = BS.bootstrap_q1(bn, bo, blocks, B_=50, seed=SEED_T)
        b = CHK.bootstrap_q1_plain(bn.tolist(), bo.tolist(), blocks, B=50, seed=SEED_T)
        for k_ in ("rate", "excess", "rate_old", "change"):
            self.assertTrue(np.allclose(a[k_], b[k_]))
        for level in ("OVER-REJECTION", "CALIBRATION", "CONSERVATISM"):
            for change in ("REDUCED", "NOT SEPARATED", "INCREASED"):
                for low in (False, True):
                    self.assertEqual(BS.verdict_text(level, change, low_support=low), CHK.verdict_plain(level, change, low))
        for iv in ([0.1, 0.2], [-0.1, 0.1], [-0.2, -0.1]):
            self.assertEqual(BS.level_label(iv), CHK.level_label(iv))
            self.assertEqual(BS.change_label(iv), CHK.change_label(iv))
        for iv in ([1.1, 1.3], [0.9, 1.2], [0.7, 0.95]):
            self.assertEqual(BS.spread_label(iv), CHK.spread_label(iv))


SEED_T = [7, 8, 9]


if __name__ == "__main__":
    unittest.main(verbosity=1)
