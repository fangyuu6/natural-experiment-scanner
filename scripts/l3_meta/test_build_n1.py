"""Unit checks for build_n1.py (synthetic inputs only; no repo data file is read, no L2 file is read or written).

Run:  .venv_did/bin/python -B scripts/l3_meta/test_build_n1.py
"""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_n1 as N  # noqa: E402  (importing touches no data file)


# ---------------------------------------------------------------------------------------------
# synthetic fixtures
# ---------------------------------------------------------------------------------------------
def make_cov(n_states=40, y0=2000, n_years=12, seed=5, signal_idx=None, signal=0.0):
    """A covariate array V[c, state, year] of independent normals; optional shift of `signal` SDs for the states in signal_idx."""
    rng = np.random.default_rng(seed)
    states = [f"S{i:02d}" for i in range(n_states)]
    V = rng.normal(size=(len(N.COVARIATES), n_states, n_years))
    if signal_idx is not None:
        V[:, list(signal_idx), :] += signal
    return {"V": V, "states": states, "state_index": {s: i for i, s in enumerate(states)}, "year0": y0, "year_last": y0 + n_years - 1}


def make_rec(cov, did="d1", t_cohorts=None, onset=None, r_extra_in_force=(), with_dc=False, audit=True, R_resolved=True):
    """Sets-file style record on the synthetic states.  t_cohorts: {g: [states]}; onset: {state: year} (default g)."""
    states = list(cov["states"])
    t_cohorts = t_cohorts or {"2003": ["S00", "S01", "S02"], "2005": ["S03", "S04"]}
    T = sorted(s for ms in t_cohorts.values() for s in ms)
    onset = onset or {s: int(g) for g, ms in t_cohorts.items() for s in ms}
    U = list(states) + (["DC"] if with_dc else [])
    inforce = sorted(set(T) | set(r_extra_in_force))
    R = sorted(set(states) - set(inforce))
    T_full = T + (["DC"] if with_dc and "DC" not in T else [])
    return {"design_id": did, "U": sorted(U), "T": T_full, "R": R if R_resolved else None, "R_resolved": R_resolved,
            "treatment_audit_passed": audit, "T_cohorts": t_cohorts, "T_operative_onset_year": onset,
            "in_force_last_panel_year": inforce, "weighted_lower_median_adoption_year_T": N.weighted_lower_median(list(onset.values())),
            "support_passed": True, "panel_years": [2000, 2011]}


class Basics(unittest.TestCase):
    def test_weighted_lower_median(self):
        self.assertEqual(N.weighted_lower_median([3, 1, 2]), 2)
        self.assertEqual(N.weighted_lower_median([1, 2, 3, 4]), 2)           # lower of the two middle values
        self.assertEqual(N.weighted_lower_median([2010, 2000, 2010, 2000]), 2000)
        self.assertEqual(N.weighted_lower_median([5]), 5)
        self.assertIsNone(N.weighted_lower_median([]))
        # at least half of the values are <= the median
        v = [1, 1, 2, 7, 9, 9, 9]
        m = N.weighted_lower_median(v)
        self.assertGreaterEqual(sum(1 for x in v if x <= m), len(v) / 2)

    def test_crc_and_clean(self):
        self.assertEqual(N.crc("abc"), 891568578)
        out = N.clean({"a": float("nan"), "b": np.float64(1.5), "c": np.arange(3), "d": (1, np.int64(2)), "e": float("inf")})
        self.assertEqual(out, {"a": None, "b": 1.5, "c": [0, 1, 2], "d": [1, 2], "e": None})
        json.dumps(out, allow_nan=False)

    def test_load_covariates_blank_is_nan(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cov.csv"
            cols = [c for c, _ in N.COVARIATES]
            lines = ["state,year," + ",".join(cols), "AL,2000,1,50.0,10.0,0.5", "AL,2001,0,,10.1,0.5", "DC,2000,,,10.5,1.0", "DC,2001,,,10.6,1.0"]
            p.write_text("\n".join(lines) + "\n")
            cov = N.load_covariates(p)
        V = cov["V"]
        self.assertEqual(V.shape, (4, 2, 2))
        ia, idc = cov["state_index"]["AL"], cov["state_index"]["DC"]
        self.assertEqual(V[0, ia, 0], 1.0)
        self.assertTrue(math.isnan(V[1, ia, 1]))
        self.assertTrue(math.isnan(V[0, idc, 0]))
        self.assertEqual(V[2, idc, 1], 10.6)
        self.assertEqual(N.year_idx(cov, 2001), 1)
        with self.assertRaises(KeyError):
            N.year_idx(cov, 2005)

    def test_live_loader_keeps_only_whitelisted_fields(self):
        live = {"as_of": "2026-01-01T00:00:00Z", "counts": {"processed": 1, "estimated": 1, "not_evaluable": 0, "other": 9},
                "excluded_not_counted": {"in_flight": [1, 2]},
                "designs": [{"design_id": "x", "status": "estimated", "gate": None, "domain": "health", "policy_family": "f", "source_group": "prototype",
                             "point_estimate": 1.23, "se": 0.4, "ci95": [0, 1], "benchmark": {"a": 1}}]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "live.json"
            p.write_text(json.dumps(live))
            designs, meta = N.load_live(p)
        self.assertEqual(set(designs[0]), set(N.LIVE_FIELDS))
        self.assertNotIn("point_estimate", designs[0])
        self.assertEqual(meta["outside_D"], {"in_flight": 2})
        self.assertEqual(set(meta["counts"]), {"processed", "estimated", "not_evaluable"})

    def test_parse_hash_record(self):
        sha = "a" * 64
        other = "b" * 64
        text = (f"title\nfile: x.md\nsha256: {sha}\nbytes: 10\namended_utc (T_amend): 2026-10-15T17:04:06Z\ninputs at amendment (read-only):\n"
                f"  data/l3_meta/n1_sets/n1_sets_v1.json sha256 {other}\n  data/l3_meta/l2_set_live.json sha256 {other} (as_of x)\n")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h.txt"
            p.write_text(text)
            rec = N.parse_hash_record(p)
        self.assertEqual(rec["sha256"], sha)
        self.assertEqual(rec["t_amend_utc"], "2026-10-15T17:04:06Z")
        self.assertEqual(rec["listed"]["data/l3_meta/n1_sets/n1_sets_v1.json"], other)
        self.assertEqual(len(rec["listed"]), 2)


class Preparation(unittest.TestCase):
    def setUp(self):
        self.cov = make_cov()

    def test_eval_design_slots_ystar_and_E(self):
        rec = make_rec(self.cov, t_cohorts={"10": ["S05", "S06"], "9": ["S00", "S01", "S02"], "11": ["S07"]},
                       onset={"S05": 2004, "S06": 2004, "S00": 2003, "S01": 2003, "S02": 2003, "S07": 2006}, r_extra_in_force=["S20", "S21"])
        prep = N.prepare_design(rec)
        self.assertEqual(prep["reasons"], [])
        # cohorts in ascending NUMERIC order of g (9 before 10), states in code order inside a cohort
        self.assertEqual([(s, g) for s, g, _ in prep["slots"]], [("S00", 9), ("S01", 9), ("S02", 9), ("S05", 10), ("S06", 10), ("S07", 11)])
        self.assertEqual([y for _, _, y in prep["slots"]], [2003, 2003, 2003, 2004, 2004, 2006])
        self.assertEqual(prep["ystar"], 2003)                       # lower median of 3,3,3,4,4,6
        self.assertEqual(prep["E"], ["S20", "S21"])
        self.assertEqual(len(prep["R"]), len(prep["U"]) - 6 - 2)
        self.assertEqual(prep["mixed_cohorts"], [])

    def test_mixed_cohort_is_flagged_and_keeps_member_years(self):
        rec = make_rec(self.cov, t_cohorts={"18": ["S00", "S01", "S02"]}, onset={"S00": 2014, "S01": 2014, "S02": 2015})
        prep = N.prepare_design(rec)
        self.assertEqual(prep["mixed_cohorts"], [18])
        self.assertEqual([y for _, _, y in prep["slots"]], [2014, 2014, 2015])

    def test_dc_is_removed_before_thresholds(self):
        # 20 states + DC: U = 20 after the rule (passes); T of 3 including DC -> 2 (fails)
        cov = make_cov(n_states=20)
        rec = make_rec(cov, t_cohorts={"2003": ["S00", "S01"]}, with_dc=True)
        rec["T_cohorts"] = {"2003": ["S00", "S01", "DC"]}
        rec["T_operative_onset_year"]["DC"] = 2003
        prep = N.prepare_design(rec)
        self.assertEqual(prep["reasons"], [("SUPPORT", "T_lt3")])
        self.assertTrue(prep["dc_in_U"] and prep["dc_in_T"])
        self.assertNotIn("DC", prep["U"])
        # 19 states + DC: |U| = 19 after the rule
        cov2 = make_cov(n_states=19)
        rec2 = make_rec(cov2, with_dc=True)
        self.assertIn(("SUPPORT", "U_lt20"), N.prepare_design(rec2)["reasons"])
        # DC in T is dropped from the slots and the cohort sizes
        cov3 = make_cov(n_states=30)
        rec3 = make_rec(cov3, t_cohorts={"2003": ["S00", "S01", "S02", "DC"]}, with_dc=True)
        p3 = N.prepare_design(rec3)
        self.assertEqual(p3["reasons"], [])
        self.assertEqual(len(p3["slots"]), 3)
        self.assertEqual(p3["cohorts"], {2003: ["S00", "S01", "S02"]})

    def test_first_failing_reason_is_the_code_and_all_are_listed(self):
        cov = make_cov(n_states=10)
        rec = make_rec(cov, audit=False)
        prep = N.prepare_design(rec)
        names = [b for _, b in prep["reasons"]]
        self.assertEqual(names[0], "treatment_audit_not_passed")
        self.assertIn("U_lt20", names)
        self.assertEqual(N.coverage_code(prep["reasons"]), "FIELD_MISSING:treatment_audit_not_passed")
        self.assertEqual(N.coverage_code([]), "EVAL")
        # R unresolved
        rec2 = make_rec(self.cov, R_resolved=False)
        self.assertEqual(N.prepare_design(rec2)["reasons"][0], ("FIELD_MISSING", "R_unresolved"))
        # R below 3
        rec3 = make_rec(self.cov, r_extra_in_force=[f"S{i:02d}" for i in range(5, 38)])
        self.assertIn(("SUPPORT", "R_lt3"), N.prepare_design(rec3)["reasons"])

    def test_inconsistent_sets_are_named_not_silently_used(self):
        rec = make_rec(self.cov)
        bad = dict(rec, R=sorted(rec["R"] + ["S00"]))                       # T and R overlap
        self.assertIn(("OTHER", "T_R_overlap"), N.prepare_design(bad)["reasons"])
        bad2 = dict(rec, in_force_last_panel_year=sorted(rec["in_force_last_panel_year"] + ["S30"]))   # R != U - in force
        self.assertIn(("OTHER", "R_not_U_minus_in_force_at_end"), N.prepare_design(bad2)["reasons"])
        bad3 = dict(rec, T_operative_onset_year={k: v for k, v in rec["T_operative_onset_year"].items() if k != "S00"})
        self.assertIn(("OTHER", "T_state_without_onset_year"), N.prepare_design(bad3)["reasons"])
        bad4 = dict(rec, T_cohorts={"2003": ["S00", "S01"], "2005": ["S03", "S04"]})                  # S02 in T but in no cohort
        self.assertIn(("OTHER", "T_not_equal_to_cohort_members"), N.prepare_design(bad4)["reasons"])


class DAndReassignment(unittest.TestCase):
    def test_gaps_and_D_by_hand_and_available_case(self):
        rng = np.random.default_rng(1)
        V = rng.normal(size=(4, 6, 3))
        sd = np.array([1.0, 2.0, 3.0, 4.0])
        T_idx, slot_years, R_idx, ystar = np.array([0, 1]), np.array([0, 1]), np.array([4, 5]), 1
        gaps, D = N.gaps_and_D(V, T_idx, slot_years, R_idx, ystar, sd)
        exp = []
        for c in range(4):
            mt = (V[c, 0, 0] + V[c, 1, 1]) / 2          # each treated state read in its own slot year
            mr = (V[c, 4, 1] + V[c, 5, 1]) / 2          # at-risk states read in the reference year
            exp.append((mt - mr) / sd[c])
        self.assertTrue(np.allclose(gaps[0], exp))
        self.assertAlmostEqual(D[0], math.sqrt(sum(e * e for e in exp) / 4))
        # a missing cell is dropped from that covariate's mean only
        V2 = V.copy()
        V2[2, 0, 0] = np.nan
        gaps2, D2 = N.gaps_and_D(V2, T_idx, slot_years, R_idx, ystar, sd)
        self.assertAlmostEqual(gaps2[0, 2], (V[2, 1, 1] - (V[2, 4, 1] + V[2, 5, 1]) / 2) / sd[2])
        self.assertTrue(np.allclose(np.delete(gaps2[0], 2), np.delete(gaps[0], 2)))
        # a group with no value at all is undefined
        V3 = V.copy()
        V3[1, 4, 1] = V3[1, 5, 1] = np.nan
        _, D3 = N.gaps_and_D(V3, T_idx, slot_years, R_idx, ystar, sd)
        self.assertTrue(math.isnan(D3[0]))

    def test_D_is_invariant_to_a_common_rescaling_of_Z(self):
        # Z = (D - mu)/tau does not change if all four covariates are multiplied by the same constant
        cov = make_cov(n_states=30, seed=9)
        rec = make_rec(cov)
        prep = N.prepare_design(rec)
        ev1 = N.evaluate_design(prep, cov)
        cov2 = dict(cov, V=cov["V"] * 7.5)
        prep2 = N.prepare_design(rec)
        ev2 = N.evaluate_design(prep2, cov2)
        self.assertAlmostEqual(ev1["primary"]["Z"], ev2["primary"]["Z"], places=9)
        self.assertAlmostEqual(ev1["S1"]["Z"], ev2["S1"]["Z"], places=9)

    def test_permutations_are_uniform_permutations(self):
        rng = np.random.default_rng(3)
        p = N.draw_permutations(8, 50, rng)
        self.assertEqual(p.shape, (50, 8))
        self.assertTrue(all(sorted(row) == list(range(8)) for row in p))
        q = N.draw_permutations(8, 50, np.random.default_rng(3))
        self.assertTrue(np.array_equal(p, q))                                # deterministic given the seed
        big = N.draw_permutations(6, 60000, np.random.default_rng(11))
        for pos in (0, 3, 5):                                                # each state is equally likely at any position
            freq = np.bincount(big[:, pos], minlength=6) / 60000
            self.assertTrue(np.all(np.abs(freq - 1 / 6) < 0.01), freq)

    def test_reassignment_structure_primary_and_S1(self):
        cov = make_cov(n_states=30, seed=2)
        rec = make_rec(cov, r_extra_in_force=["S10", "S11", "S12", "S13"])
        prep = N.prepare_design(rec)
        arr = N.design_arrays(prep, cov)
        k = len(arr["T_idx"])
        self.assertEqual(k, 5)
        self.assertEqual(len(arr["pool_s1_idx"]), 30 - 4)
        e_idx = {cov["state_index"][s] for s in prep["E"]}
        self.assertFalse(e_idx & set(arr["pool_s1_idx"].tolist()))
        for mode, pool, r_size in (("primary", arr["U_idx"], 30 - k), ("S1", arr["pool_s1_idx"], len(prep["R"]))):
            perms = N.draw_permutations(len(pool), 40, np.random.default_rng(4))
            T_idx, R_idx = pool[perms[:, :k]], pool[perms[:, k:]]
            self.assertEqual(R_idx.shape[1], r_size)
            for t, r in zip(T_idx, R_idx):
                self.assertEqual(len(set(t.tolist())), k)                       # no state drawn twice
                self.assertFalse(set(t.tolist()) & set(r.tolist()))             # R* is the complement of the drawn set
                self.assertEqual(set(t.tolist()) | set(r.tolist()), set(pool.tolist()))
            _, Dstar = N.reassigned_D(arr, cov["V"], mode, 40, np.random.default_rng(4))
            self.assertEqual(Dstar.shape, (40,))
            self.assertTrue(np.all(np.isfinite(Dstar)))
        # S1 equals the primary when no other adopter exists
        rec0 = make_rec(cov)
        prep0 = N.prepare_design(rec0)
        arr0 = N.design_arrays(prep0, cov)
        self.assertEqual(prep0["E"], [])
        a = N.reassigned_D(arr0, cov["V"], "primary", 30, np.random.default_rng(8))[1]
        b = N.reassigned_D(arr0, cov["V"], "S1", 30, np.random.default_rng(8))[1]
        self.assertTrue(np.array_equal(a, b))

    def test_moments_z_and_p(self):
        d = np.array([1.0, 2.0, 3.0, 4.0, float("nan")])
        mom = N.null_moments(d)
        self.assertAlmostEqual(mom["mu"], 2.5)
        self.assertAlmostEqual(mom["tau"], math.sqrt(5 / 3))            # sample SD (n - 1)
        self.assertEqual((mom["M_defined"], mom["M_undefined"]), (4, 1))
        z, p = N.z_and_p(3.5, d, mom)
        self.assertAlmostEqual(z, (3.5 - 2.5) / math.sqrt(5 / 3))
        self.assertAlmostEqual(p, (1 + 1) / (4 + 1))                    # only D* = 4 is >= 3.5
        _, p_tie = N.z_and_p(3.0, d, mom)
        self.assertAlmostEqual(p_tie, (1 + 2) / 5)                      # ties count

    def test_signal_gives_large_positive_Z_and_null_gives_centred_Z(self):
        # treated states are 2.5 SD above the rest on every covariate: Z far above 0
        cov = make_cov(n_states=30, seed=6, signal_idx=[0, 1, 2, 3, 4], signal=2.5)
        rec = make_rec(cov, t_cohorts={"2003": ["S00", "S01", "S02"], "2005": ["S03", "S04"]})
        ev = N.evaluate_design(N.prepare_design(rec), cov)
        self.assertGreater(ev["primary"]["Z"], 4.0)
        self.assertLess(ev["primary"]["p"], 0.01)
        # random treated sets: Z is centred near 0 with SD near 1 (no extra adopters, so primary = S1)
        zs = []
        for i in range(150):
            cv = make_cov(n_states=30, seed=100 + i)
            rng = np.random.default_rng(500 + i)
            pick = rng.permutation(30)[:6]
            rc = make_rec(cv, did=f"r{i}", t_cohorts={"2003": [cv["states"][j] for j in pick[:4]], "2005": [cv["states"][j] for j in pick[4:]]})
            zs.append(N.evaluate_design(N.prepare_design(rc), cv)["primary"]["Z"])
        self.assertLess(abs(np.mean(zs)), 0.2)
        self.assertTrue(0.8 < np.std(zs, ddof=1) < 1.2)

    def test_literal_null_is_biased_when_other_adopters_are_left_out_and_S1_is_not(self):
        # Pseudo-assignments with |E| = 18 other adopters: R has 8 states, the literal null (R* = U - drawn set, 26 states) is too tight.
        n_states, k, nE = 30, 4, 18
        z_lit, z_s1 = [], []
        for i in range(150):
            cv = make_cov(n_states=n_states, seed=300 + i)
            rng = np.random.default_rng(900 + i)
            perm = [cv["states"][j] for j in rng.permutation(n_states)]
            T, E = perm[:k], perm[k:k + nE]
            rec = make_rec(cv, did=f"b{i}", t_cohorts={"2003": T[:2], "2005": T[2:]}, r_extra_in_force=E)
            ev = N.evaluate_design(N.prepare_design(rec), cv)
            z_lit.append(ev["primary"]["Z"])
            z_s1.append(ev["S1"]["Z"])
        z_lit, z_s1 = np.array(z_lit), np.array(z_s1)
        self.assertGreater(np.mean(z_lit), 0.15)          # upward bias of the literal null
        self.assertLess(abs(np.mean(z_s1)), 0.2)          # the size-matched null is centred
        self.assertGreater(np.mean(z_lit - z_s1), 0.2)    # paired: same D, different null

    def test_evaluate_design_codes_constant_covariate_and_missing_group(self):
        cov = make_cov(n_states=30, seed=1)
        cov["V"][0, :, :] = 1.0                                         # first covariate constant everywhere
        prep = N.prepare_design(make_rec(cov))
        self.assertIsNone(N.evaluate_design(prep, cov))
        self.assertEqual(prep["reasons"][-1], ("SUPPORT", "covariate_constant"))
        cov2 = make_cov(n_states=30, seed=1)
        rec = make_rec(cov2)
        prep2 = N.prepare_design(rec)
        for s in prep2["R"]:
            cov2["V"][1, cov2["state_index"][s], N.year_idx(cov2, prep2["ystar"])] = np.nan
        self.assertIsNone(N.evaluate_design(prep2, cov2))
        self.assertEqual(prep2["reasons"][-1], ("FIELD_MISSING", "covariate_without_value_in_group"))


class E1(unittest.TestCase):
    def test_se_by_hand_singletons_and_clusters(self):
        r = N.e1_family_bootstrap_t([1.0, 2.0, 3.0, 4.0], ["a", "b", "c", "d"], B=199, seed=[1, 2])
        self.assertAlmostEqual(r["theta"], 2.5)
        self.assertAlmostEqual(r["se"], math.sqrt(4 / 3 * 5) / 4)
        self.assertAlmostEqual(r["t"], 2.5 / r["se"])
        # two families {1,2} and {3,4}: cluster sums of deviations are -2 and +2, F/(F-1) = 2 -> SE = sqrt(2 * 8)/4 = 1
        r2 = N.e1_family_bootstrap_t([1.0, 2.0, 3.0, 4.0], ["a", "a", "b", "b"], B=199, seed=[1, 2])
        self.assertAlmostEqual(r2["se"], 1.0)
        self.assertGreater(r2["se"], r["se"])                            # clustering raises the SE when families are homogeneous

    def test_bootstrap_matches_a_slow_loop_with_the_same_draws(self):
        y = np.array([0.5, 1.7, -0.3, 2.2, 0.9, 1.4, -1.1, 0.2])
        fam = ["a", "a", "b", "c", "c", "c", "d", "e"]
        B, seed = 400, [20261011, 1]
        res = N.e1_family_bootstrap_t(y, fam, B=B, seed=seed, return_t=True)
        names = sorted(set(fam))
        members = {f: [i for i, x in enumerate(fam) if x == f] for f in names}
        F = len(names)
        theta = y.mean()
        draws = np.random.default_rng(seed).integers(0, F, size=(B, F))
        t_ref = []
        for row in draws:
            idx = [i for k in row for i in members[names[k]]]
            th = y[idx].mean()
            ss = 0.0
            for k in row:                                                 # each draw is its own cluster
                ss += sum(y[i] - th for i in members[names[k]]) ** 2
            se = math.sqrt(F / (F - 1) * ss) / len(idx)
            t_ref.append((th - theta) / se if se > 1e-9 * np.abs(y).max() else 0.0)     # an SE of floating-point zero counts as 0
        t_ref = np.array(t_ref)
        self.assertTrue(np.allclose(res["t_star"], t_ref))
        self.assertAlmostEqual(res["p_two_sided"], (1 + np.sum(np.abs(t_ref) >= abs(res["t"]))) / (B + 1))
        c = np.percentile(np.abs(t_ref), 95)
        self.assertAlmostEqual(res["interval95"][0], res["theta"] - c * res["se"])
        self.assertAlmostEqual(res["interval95"][1], res["theta"] + c * res["se"])

    def test_deterministic_sign_symmetry_and_bounds(self):
        y = [0.9, 1.4, 0.2, 2.0, 1.1, 0.7, 1.9, -0.4, 0.8, 1.3]
        fam = list("aabbcdefgh")
        a = N.e1_family_bootstrap_t(y, fam, B=999, seed=[7, 7])
        b = N.e1_family_bootstrap_t(y, fam, B=999, seed=[7, 7])
        self.assertEqual(a["p_two_sided"], b["p_two_sided"])
        neg = N.e1_family_bootstrap_t([-v for v in y], fam, B=999, seed=[7, 7])
        self.assertAlmostEqual(a["p_two_sided"], neg["p_two_sided"])
        self.assertAlmostEqual(a["interval95"][0], -neg["interval95"][1])
        self.assertGreaterEqual(a["p_two_sided"], 1 / 1000)
        self.assertLessEqual(a["p_two_sided"], 1.0)

    def test_floating_point_zero_se_is_zero(self):
        r = N.e1_family_bootstrap_t([0.1, 0.1, 0.1, 0.1, 0.1, 0.1], list("aabbcc"), B=50)
        self.assertIsNone(r["p_two_sided"])                               # constant outcome: no t statistic
        # a replicate that draws one family F times has SE* = 0 and t* = 0, however the rounding falls
        res = N.e1_family_bootstrap_t([0.5, 1.7, -0.3, 2.2, 0.9, 1.4, -1.1, 0.2], ["a", "a", "b", "c", "c", "c", "d", "e"], B=400,
                                      seed=[20261011, 1], return_t=True)
        self.assertLess(np.abs(res["t_star"]).max(), 1e6)

    def test_degenerate_cases(self):
        self.assertEqual(N.e1_family_bootstrap_t([1.0, 2.0, 3.0], ["a", "a", "a"], B=50)["status"], "NOT_COMPUTABLE")
        r = N.e1_family_bootstrap_t([2.0, 2.0, 2.0, 2.0], list("abcd"), B=50)
        self.assertEqual(r["se"], 0.0)
        self.assertIsNone(r["p_two_sided"])

    def test_size_under_a_true_null_is_near_nominal(self):
        rng = np.random.default_rng(42)
        fam = [f"f{i // 2}" for i in range(40)]                          # 20 families of 2
        ids, F = N.family_ids(fam)
        boot = np.random.default_rng(43)
        rej = 0
        reps = 300
        for _ in range(reps):
            y = rng.normal(size=40)
            r = N.e1_core(y, ids, F, 399, boot)
            rej += r["p_two_sided"] <= 0.05
        self.assertTrue(0.02 < rej / reps < 0.09, rej / reps)


class Calibration(unittest.TestCase):
    def test_pseudo_Z_and_calibration_shapes(self):
        cov = make_cov(n_states=30, seed=3)
        evals, fams = [], []
        for i in range(8):
            rec = make_rec(cov, did=f"c{i}", r_extra_in_force=["S10", "S11", "S12"] if i % 2 else [])
            prep = N.prepare_design(rec)
            evals.append((f"c{i}", N.evaluate_design(prep, cov)))
            fams.append(f"f{i // 2}")
        z = N.pseudo_Z(evals[0][1], cov["V"], "c0", 25)
        self.assertEqual(z["primary"].shape, (25,))
        self.assertTrue(np.array_equal(z["primary"], z["S1"]))             # design c0 has no other adopter: S1 = primary
        out = N.calibration(evals, cov["V"], fams, P=40, B=99, seed=[1, 2, 3])
        for mode in ("primary", "S1"):
            self.assertEqual(out[mode]["datasets_used"], 40)
            self.assertTrue(0 <= out[mode]["rejection_rate_at_05"] <= 1)
        # deterministic
        out2 = N.calibration(evals, cov["V"], fams, P=40, B=99, seed=[1, 2, 3])
        self.assertEqual(out["primary"]["mean_theta1"], out2["primary"]["mean_theta1"])


class Summaries(unittest.TestCase):
    def test_blocks(self):
        rows = [{"family": "a", "primary": {"Z": 1.0, "p": 0.04}, "gaps": np.array([1.0, -1.0, 0.5, 0.0])},
                {"family": "a", "primary": {"Z": -0.5, "p": 0.5}, "gaps": np.array([0.5, 0.5, 0.5, 0.5])},
                {"family": "b", "primary": {"Z": 2.0, "p": 0.08}, "gaps": np.array([-1.0, 1.0, 0.5, 0.5])}]
        zb = N.z_block(rows, "primary")
        self.assertEqual((zb["n"], zb["F"]), (3, 2))
        self.assertAlmostEqual(zb["mean_Z"], (1.0 - 0.5 + 2.0) / 3)
        self.assertAlmostEqual(zb["share_positive"], 2 / 3)
        pc = N.p_counts(rows, "primary")
        self.assertEqual((pc["p_le_05"], pc["p_le_10"]), (1, 2))
        self.assertAlmostEqual(pc["expected_p_le_05"], 0.15)
        cg = N.covariate_gaps(rows)
        self.assertAlmostEqual(cg["dem_unified_jan1"]["mean_signed_gap"], (1.0 + 0.5 - 1.0) / 3)
        self.assertAlmostEqual(cg["citizen_ideology"]["share_above_0"], 2 / 3)
        self.assertEqual(N.z_block([], "primary"), {"n": 0})


if __name__ == "__main__":
    unittest.main()
