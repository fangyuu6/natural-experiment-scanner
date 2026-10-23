"""Unit checks for build_d3.py (synthetic inputs only; no repo data file is read, no L2 file is read or written).

Run:  .venv_did/bin/python -B scripts/l3_meta/test_build_d3.py
"""
import csv
import hashlib
import json
import math
import random
import statistics
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import build_d3 as D  # noqa: E402  (importing touches no data file)


# ---------------------------------------------------------------------------------------------
# synthetic L2-like entries
# ---------------------------------------------------------------------------------------------
def base_cells():
    return [dict(g=2005 + (j // 4), t=2000 + j, base_period=1999 + (j // 8), n_treated=1 + (j % 3), n_control=15 + j, post=(j >= 8), att=0.0)
            for j in range(16)]


def overall_of(cells):
    post = [c for c in cells if c["post"]]
    return sum(c["n_treated"] * c["att"] for c in post) / sum(c["n_treated"] for c in post)


def make_entry(att_by_cell, draws, requested=999, drop=(), settings=None, inventory=None, tamper_sd=False, tamper_overall=0.0, nf_override=None):
    cells = base_cells()
    for c, v in zip(cells, att_by_cell):
        c["att"] = v
    overall = overall_of(cells)
    st = D.draw_stats(overall + tamper_overall, draws)
    stored_overall = overall + tamper_overall
    nf = {"permutation_draws": list(draws), "feasibility": {"requested_draws": requested, "infeasible_draws": [{"draw_index": i, "reason": "x"} for i in drop]},
          "sample_sd": st["sd"] * (1.5 if tamper_sd else 1.0), "absolute_cutoff": st["cutoff"], "p_two_sided": st["p2"]}
    if nf_override:
        nf.update(nf_override)
    return {"status": "ESTIMATED", "inventory": inventory or {"cells": 100, "construction_cells": 120, "metadata_sha256": "m"},
            "result": {"group_time": cells, "overall": {"att": stored_overall, "n_cells": sum(1 for c in cells if c["post"])},
                       "settings": settings or {"control_group": "never_treated"}},
            "noise_floor": nf}


def make_pair(seed, a_post=None, c_post=None, n_draws=999, drop=(), sd_a=0.5, sd_c=0.5, corr=0.0, settings_t=None, inventory_t=None):
    """A and T entries with T = A + C cell by cell and draw by draw.  a_post / c_post fix the post cells' att (else random)."""
    rng = random.Random(seed)
    nd = n_draws - len(drop)
    cells_a = [rng.gauss(0, .3) for _ in range(8)] + [(a_post if a_post is not None else rng.gauss(0, sd_a)) + rng.gauss(0, .05) * (a_post is not None) for _ in range(8)]
    cells_c = [rng.gauss(0, .3) for _ in range(8)] + [(c_post if c_post is not None else rng.gauss(0, sd_c)) + rng.gauss(0, .05) * (c_post is not None) for _ in range(8)]
    da = [rng.gauss(0, sd_a) for _ in range(nd)]
    dc = [corr * x / sd_a * sd_c + math.sqrt(1 - corr ** 2) * rng.gauss(0, sd_c) for x in da]
    A = make_entry(cells_a, da, n_draws, drop)
    T = make_entry([x + y for x, y in zip(cells_a, cells_c)], [x + y for x, y in zip(da, dc)], n_draws, drop, settings=settings_t, inventory=inventory_t)
    C_overall = overall_of([dict(c, att=v) for c, v in zip(base_cells(), cells_c)])
    return A, T, C_overall, dc


def design_row(did="d1", family="fam", group="autopilot_estimate", status="estimated", primary=("fars_motor_vehicle_occupant_fatality_rate",)):
    return {"design_id": did, "status": status, "gate": None if status == "estimated" else "support", "domain": "x", "policy_family": family,
            "source_group": group, "source_path": f"synthetic/{did}/RESULT.json", "source_sha256": "s", "primary_ids": list(primary), "secondary": []}


CATALOG = {"fars_motor_vehicle_occupant_fatality_rate": {"units": "deaths per 100,000 residents", "artifact": "a"},
           "fars_all_road_fatality_rate": {"units": "deaths per 100,000 residents", "artifact": "b"},
           "wonder_firearm_homicide_deaths": {"units": "deaths", "artifact": "c"}, "wonder_homicide_deaths": {"units": "deaths", "artifact": "d;e"}}
P3 = D.PAIR_TABLE[2]
P1 = D.PAIR_TABLE[0]


def panels_ok(a_vals=None, t_vals=None, dens=None):
    keys = [("S%02d" % i, 2000 + j) for i in range(5) for j in range(4)]
    pa = {k: ((a_vals or (lambda i: 1.0 + .1 * i))(n), (dens or (lambda i: 1000.0 + i))(n)) for n, k in enumerate(keys)}
    pt = {k: ((t_vals or (lambda i: 2.0 + .1 * i))(n), (dens or (lambda i: 1000.0 + i))(n)) for n, k in enumerate(keys)}
    return pa, pt


# ---------------------------------------------------------------------------------------------
class Basics(unittest.TestCase):
    def test_agree_sgn_mean(self):
        self.assertTrue(D.agree(1.0, 1.0 + 1e-10))
        self.assertFalse(D.agree(1.0, 1.0 + 1e-8))
        self.assertTrue(D.agree(1e6, 1e6 + 1e-4))                 # scaled by max(1, |x|, |y|)
        self.assertTrue(D.agree(0.0, 5e-10))
        self.assertEqual([D.sgn(-2.5), D.sgn(0.0), D.sgn(3)], [-1, 0, 1])
        self.assertAlmostEqual(D.mean([1, 2, 3, 4]), 2.5)

    def test_clean_and_rel(self):
        out = D.clean({"a": float("nan"), "b": np.float64(2.5), "c": (1, np.int64(3)), "d": np.array([1.0, float("inf")])})
        self.assertEqual(out, {"a": None, "b": 2.5, "c": [1, 3], "d": [1.0, None]})
        self.assertEqual(D.rel("data/l2_pipeline/x.json"), "data/l2_pipeline/x.json")      # relative input returned as written
        self.assertEqual(D.rel(D.ROOT / "docs" / "a.md"), "docs/a.md")

    def test_draw_stats_matches_engine_expression(self):
        draws = [(-1) ** i * (i + 1) / 100 for i in range(999)]
        st = D.draw_stats(3.2, draws)
        n = len(draws)
        self.assertEqual(st["cutoff"], sorted(abs(v) for v in draws)[math.ceil(.95 * n) - 1])
        self.assertEqual(st["sd"], statistics.stdev(draws))
        self.assertAlmostEqual(st["p2"], (1 + sum(abs(v) >= 3.2 for v in draws)) / (n + 1))
        self.assertFalse(D.draw_stats(0.1, draws)["beyond"])
        self.assertTrue(D.draw_stats(max(abs(v) for v in draws) + 1.0, draws)["beyond"])
        # strict inequality: exactly the cutoff is not beyond
        self.assertFalse(D.draw_stats(st["cutoff"], draws)["beyond"])
        self.assertTrue(D.draw_stats(st["cutoff"] + 1e-9, draws)["beyond"])

    def test_pearson(self):
        x = [1, 2, 3, 4, 5]
        self.assertAlmostEqual(D.pearson(x, [2 * v + 1 for v in x]), 1.0)
        self.assertAlmostEqual(D.pearson(x, [-v for v in x]), -1.0)
        self.assertIsNone(D.pearson(x, [1, 1, 1, 1, 1]))


class Entries(unittest.TestCase):
    def test_section_hash_rule(self):
        text = "head\n\n## 0. Common\nx\n## D1. One\nbody1\n\n## D2. Two\nbody2\n## D3. Three\nbody3\n## 2. Stays\nend\n"
        self.assertEqual(D.entry_text(text, "D1"), "## D1. One\nbody1\n\n")
        self.assertEqual(D.entry_text(text, "D3"), "## D3. Three\nbody3\n")
        self.assertIsNone(D.entry_text(text, "D4"))
        self.assertEqual(D.entry_sha256(text, "D2"), hashlib.sha256("## D2. Two\nbody2\n".encode()).hexdigest())

    def test_record_parse_and_verify_detect_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            q = tmp / "q.md"
            b = tmp / "b.md"
            q.write_text("## D1. One\na\n## D2. Two\nb\n## D3. Three\nc\n## 2. end\n", encoding="utf-8")
            b.write_text("build rules\n", encoding="utf-8")
            qs, bs = D.sha256_file(q), D.sha256_file(b)
            rec_text = (f"header\nfile: {D.rel(q)}\nsha256: {qs}\nbytes: 1\nfile: {D.rel(b)}\nsha256: {bs}\nbytes: 1\n"
                        "entry sha256 (rule):\n  D1 %s\n  D2 %s\n  D3 %s\namended_utc (T_amend): 2026-10-15T00:00:00Z\n"
                        "inputs:\n  some/input.json sha256 %s\n" % (D.entry_sha256(q.read_text(), "D1"), D.entry_sha256(q.read_text(), "D2"),
                                                                      D.entry_sha256(q.read_text(), "D3"), "a" * 64))
            rp = tmp / "rec.txt"
            rp.write_text(rec_text, encoding="utf-8")
            rec = D.parse_hash_record(rp)
            self.assertEqual(rec["files"], {D.rel(q): qs, D.rel(b): bs})
            self.assertEqual(sorted(rec["entries"]), ["D1", "D2", "D3"])
            self.assertEqual(rec["t_amend_utc"], "2026-10-15T00:00:00Z")
            self.assertEqual(rec["listed"]["some/input.json"], "a" * 64)
            self.assertEqual(D.verify_records(rec, q, b), [])
            # change inside entry D2 only: the file hash and the D2 entry hash fail, D1 and D3 do not
            q.write_text("## D1. One\na\n## D2. Two\nB\n## D3. Three\nc\n## 2. end\n", encoding="utf-8")
            problems = D.verify_records(rec, q, b)
            self.assertTrue(any("entry D2" in p for p in problems) and not any("entry D1" in p or "entry D3" in p for p in problems))
            self.assertTrue(any("q.md" in p or str(q) in p for p in problems))
            # change outside the entries: only the file hash fails
            q.write_text("## D1. One\na\n## D2. Two\nb\n## D3. Three\nc\n## 2. end changed\n", encoding="utf-8")
            problems = D.verify_records(rec, q, b)
            self.assertEqual(len(problems), 1)


class Outcomes(unittest.TestCase):
    def test_outcomes_of(self):
        A, T, _, _ = make_pair(1)
        res = {"registered_results": {
            "primary": A, "secondary_fars_all_road_fatality_rate": T,
            "start_plus_one": A, "drop_x": A,
            "secondary_unsupported_series": {"status": "UNSUPPORTED", "result": None},
            "secondary_no_draws": dict(A, noise_floor={"status": "NOT_ASSESSABLE"})}}
        out = D.outcomes_of(res, ["fars_motor_vehicle_occupant_fatality_rate"])
        self.assertEqual(list(out), ["fars_motor_vehicle_occupant_fatality_rate", "fars_all_road_fatality_rate"])
        self.assertEqual(out["fars_all_road_fatality_rate"][0], "secondary_fars_all_road_fatality_rate")
        self.assertEqual(D.outcomes_of({}, ["x"]), {})
        self.assertEqual(D.outcomes_of(res, []).keys(), {"fars_all_road_fatality_rate"})      # primary without an id is skipped

    def test_feasible_indices(self):
        nf = {"feasibility": {"requested_draws": 10, "infeasible_draws": [{"draw_index": 2}, {"draw_index": 7}]}}
        self.assertEqual(D.feasible_indices(nf), [0, 1, 3, 4, 5, 6, 8, 9])
        self.assertIsNone(D.feasible_indices({"feasibility": {}}))

    def test_design_code_order(self):
        self.assertEqual(D.design_code([]), "NO_PAIR")
        self.assertEqual(D.design_code(["NEEDS_COMPLEMENT_ESTIMATE", "EVAL"]), "EVAL")
        self.assertEqual(D.design_code(["NEEDS_COMPLEMENT_ESTIMATE", "CELL_SETS_DIFFER"]), "CELL_SETS_DIFFER")
        self.assertEqual(D.design_code(["SUPPORT", "OTHER"]), "OTHER")
        self.assertEqual(D.design_code(["NEEDS_COMPLEMENT_ESTIMATE"]), "NEEDS_COMPLEMENT_ESTIMATE")


class PairNumbers(unittest.TestCase):
    def test_identity_complement_equals_difference(self):
        A, T, c_overall, dc = make_pair(11)
        nums, problems = D.pair_numbers(A, T)
        self.assertEqual(problems, [])
        self.assertAlmostEqual(nums["c"], c_overall, places=12)                    # ATT(C) = ATT(T) - ATT(A) = ATT of the C cells
        self.assertAlmostEqual(nums["sd_c"], statistics.stdev(dc), places=12)       # draws of C are the differences of the draws
        self.assertAlmostEqual(nums["cutoff_c"], sorted(abs(v) for v in dc)[math.ceil(.95 * len(dc)) - 1], places=12)

    def test_z_matches_explicit_formula(self):
        A, T, c_overall, dc = make_pair(12, a_post=-1.0, c_post=0.6)
        nums, problems = D.pair_numbers(A, T)
        self.assertEqual(problems, [])
        da = A["noise_floor"]["permutation_draws"]
        a, c = A["result"]["overall"]["att"], nums["c"]
        sd_c = statistics.stdev(dc)
        s = -(1 if a > 0 else -1 if a < 0 else 0) * c / sd_c
        star = [-(1 if x > 0 else -1 if x < 0 else 0) * y / sd_c for x, y in zip(da, dc)]
        mu, tau = sum(star) / len(star), statistics.stdev(star)
        self.assertAlmostEqual(nums["s"], s, places=12)
        self.assertAlmostEqual(nums["Z"], (s - mu) / tau, places=10)
        self.assertAlmostEqual(nums["p_sub"], (1 + sum(v >= s for v in star)) / (len(star) + 1), places=12)
        self.assertAlmostEqual(nums["omega"], -c / a, places=12)
        self.assertAlmostEqual(nums["net_ratio"], (a + c) / a, places=12)

    def test_substitution_and_comovement_have_opposite_signs(self):
        A, T, *_ = make_pair(13, a_post=-2.0, c_post=2.0)           # target falls, substitute rises by as much
        n1, p1 = D.pair_numbers(A, T)
        self.assertEqual(p1, [])
        self.assertGreater(n1["Z"], 3.0)
        self.assertTrue(n1["opposite"] and n1["beyond_a"] and n1["beyond_c"] and n1["signal"])
        self.assertAlmostEqual(n1["omega"], 1.0, delta=0.1)
        self.assertAlmostEqual(n1["net_ratio"], 0.0, delta=0.1)
        A, T, *_ = make_pair(13, a_post=-2.0, c_post=-2.0)          # both fall
        n2, p2 = D.pair_numbers(A, T)
        self.assertEqual(p2, [])
        self.assertLess(n2["Z"], -3.0)
        self.assertFalse(n2["opposite"] or n2["signal"])
        A, T, *_ = make_pair(14, a_post=2.0, c_post=-2.0)           # target rises, substitute falls: substitution too (sign-aligned)
        n3, _ = D.pair_numbers(A, T)
        self.assertGreater(n3["Z"], 3.0)

    def test_null_pairs_center_near_zero(self):
        rng = random.Random(77)
        zs = []
        for k in range(150):          # observed a and c drawn from the same spread as their placebo draws: Z is about N(0, 1)
            A, T, *_ = make_pair(1000 + k, a_post=rng.gauss(0, .5), c_post=rng.gauss(0, .5), n_draws=400)
            nums, problems = D.pair_numbers(A, T)
            self.assertEqual(problems, [])
            zs.append(nums["Z"])
        self.assertLess(abs(statistics.mean(zs)), 0.3)
        self.assertGreater(statistics.stdev(zs), 0.7)
        self.assertLess(statistics.stdev(zs), 1.4)

    def test_zero_effect_target_gives_zero_s(self):
        rng = random.Random(15)
        da = [rng.gauss(0, .5) for _ in range(999)]
        dc = [rng.gauss(0, .5) for _ in range(999)]
        cells_c = [rng.gauss(0, .3) for _ in range(16)]
        A = make_entry([0.0] * 16, da)                                   # a = 0 exactly
        T = make_entry(cells_c, [x + y for x, y in zip(da, dc)])
        nums, problems = D.pair_numbers(A, T)
        self.assertEqual(problems, [])
        self.assertEqual(nums["a"], 0.0)
        self.assertEqual(nums["s"], 0.0)
        self.assertIsNone(nums["omega"])
        self.assertIsNone(nums["net_ratio"])
        self.assertFalse(nums["opposite"] or nums["signal"])
        self.assertTrue(math.isfinite(nums["Z"]))

    def test_cross_checks_flag_inconsistent_inputs(self):
        A, T, *_ = make_pair(16)
        A_bad = dict(A, noise_floor=dict(A["noise_floor"], sample_sd=A["noise_floor"]["sample_sd"] * 1.5))
        _, problems = D.pair_numbers(A_bad, T)
        self.assertTrue(any("sample_sd of A" in why for _, why in problems))
        T_bad = make_entry([c["att"] for c in T["result"]["group_time"]], T["noise_floor"]["permutation_draws"], tamper_overall=0.01)
        _, problems = D.pair_numbers(A, T_bad)
        self.assertTrue(any("overall.att of T" in why or "post cells" in why for _, why in problems))
        nan_entry = make_entry([c["att"] for c in A["result"]["group_time"]], [float("nan")] + A["noise_floor"]["permutation_draws"][1:])
        nums, problems = D.pair_numbers(nan_entry, T)
        self.assertIsNone(nums)
        self.assertEqual(problems[0][0], "OTHER")

    def test_support_when_complement_has_no_spread(self):
        A, T, *_ = make_pair(17)
        T_same = make_entry([c["att"] for c in A["result"]["group_time"]], A["noise_floor"]["permutation_draws"])      # T = A, so C = 0
        nums, problems = D.pair_numbers(A, T_same)
        self.assertEqual(problems[0][0], "SUPPORT")


class Codes(unittest.TestCase):
    def go(self, A, T, panels=None, catalog=CATALOG, pair=P3, t_present=True):
        design = design_row()
        panels = panels or (lambda *a: panels_ok())
        return D.analyze_pair(design, pair, "primary", A, ("secondary_" + pair[2], T) if t_present else None, panels, catalog)

    def test_eval(self):
        A, T, *_ = make_pair(21)
        rec = self.go(A, T)
        self.assertEqual(rec["code"], "EVAL")
        self.assertEqual(rec["reasons"], [])
        self.assertIn("Z", rec["numbers"])

    def test_needs_complement(self):
        A, T, *_ = make_pair(22)
        rec = self.go(A, T, t_present=False)
        self.assertEqual(rec["code"], "NEEDS_COMPLEMENT_ESTIMATE")

    def test_cell_sets_differ(self):
        A, T, *_ = make_pair(23)
        T["result"]["group_time"][3]["n_control"] += 1
        self.assertEqual(self.go(A, T)["code"], "CELL_SETS_DIFFER")
        A, T, *_ = make_pair(23, inventory_t={"cells": 99, "construction_cells": 120, "metadata_sha256": "m"})
        self.assertEqual(self.go(A, T)["code"], "CELL_SETS_DIFFER")

    def test_settings_differ(self):
        A, T, *_ = make_pair(24, settings_t={"control_group": "not_yet_treated"})
        self.assertEqual(self.go(A, T)["code"], "SETTINGS_DIFFER")

    def test_draws_not_aligned(self):
        A, T, *_ = make_pair(25)
        T["noise_floor"]["feasibility"]["infeasible_draws"] = [{"draw_index": 4}]
        T["noise_floor"]["permutation_draws"] = T["noise_floor"]["permutation_draws"][:-1]
        self.assertEqual(self.go(A, T)["code"], "DRAWS_NOT_ALIGNED")
        A, T, *_ = make_pair(26, drop=tuple(range(0, 120)))                  # 879 feasible draws: below 900
        self.assertEqual(self.go(A, T)["code"], "DRAWS_NOT_ALIGNED")
        A, T, *_ = make_pair(27, drop=(3, 40, 41))                           # same infeasible set in both: aligned
        self.assertEqual(self.go(A, T)["code"], "EVAL")
        A, T, *_ = make_pair(28)
        T["noise_floor"]["permutation_draws"].pop()                          # stored count disagrees with the inventory
        self.assertEqual(self.go(A, T)["code"], "DRAWS_NOT_ALIGNED")

    def test_panel_tests(self):
        A, T, *_ = make_pair(29)
        pa, pt = panels_ok(dens=lambda i: 1000.0 + i)
        pt2 = {k: (v[0], v[1] * 1.01) for k, v in pt.items()}
        self.assertEqual(self.go(A, T, panels=lambda *a: (pa, pt2))["code"], "DENOMINATORS_DIFFER")
        pa3, pt3 = panels_ok(a_vals=lambda i: 5.0, t_vals=lambda i: 4.0)        # total below the target
        self.assertEqual(self.go(A, T, panels=lambda *a: (pa3, pt3))["code"], "NOT_NESTED")
        bad_units = dict(CATALOG, fars_all_road_fatality_rate={"units": "deaths", "artifact": "b"})
        self.assertEqual(self.go(A, T, catalog=bad_units)["code"], "NOT_NESTED")

        def boom(*a):
            raise OSError("missing")
        rec = self.go(A, T, panels=boom)
        self.assertEqual(rec["code"], "OTHER")
        self.assertIn("PANEL_UNREADABLE", rec["reasons"][0])
        # missing values are ignored in the nesting and denominator tests
        pa4, pt4 = panels_ok()
        for k in list(pa4)[:5]:
            pa4[k] = (None, None)
        self.assertEqual(self.go(A, T, panels=lambda *a: (pa4, pt4))["code"], "EVAL")

    def test_other_and_support_and_order(self):
        A, T, *_ = make_pair(30)
        T_bad = make_entry([c["att"] for c in T["result"]["group_time"]], T["noise_floor"]["permutation_draws"], tamper_sd=True)
        rec = self.go(A, T_bad)
        self.assertEqual(rec["code"], "OTHER")
        # several failures: all listed, the first in the build-file order is the code
        A2, T2, *_ = make_pair(31, settings_t={"control_group": "not_yet_treated"})
        T2["result"]["group_time"][0]["n_treated"] += 1
        rec = self.go(A2, T2)
        self.assertEqual(rec["code"], "CELL_SETS_DIFFER")
        self.assertEqual(len(rec["reasons"]), 2)
        self.assertTrue(rec["reasons"][0].startswith("CELL_SETS_DIFFER"))


class DesignLevel(unittest.TestCase):
    def loader(self, results):
        def f(design):
            return results[design["design_id"]], True
        return f

    def test_roster_paths(self):
        A, T, *_ = make_pair(41)
        res_eval = {"registered_results": {"primary": A, "secondary_fars_all_road_fatality_rate": T}}
        A2, _, *_ = make_pair(42)
        res_needs = {"registered_results": {"primary": make_pair(43, n_draws=999)[0]}}
        res_cells = {"registered_results": {"primary": make_pair(45)[0], "secondary_wonder_homicide_deaths": make_pair(46)[0]}}
        res_cells["registered_results"]["primary"]["inventory"] = {"cells": 50, "construction_cells": 60, "metadata_sha256": "m1"}
        res_cells["registered_results"]["secondary_wonder_homicide_deaths"]["inventory"] = {"cells": 70, "construction_cells": 60, "metadata_sha256": "m2"}
        results = {"ev": res_eval, "nd": res_needs, "cs": res_cells, "np": {"registered_results": {"primary": A2}}}
        rows, pairs, needs = [], [], []
        roster = [design_row("ev", "f1"), design_row("nd", "f2", primary=("wonder_firearm_homicide_deaths",)),
                  design_row("cs", "f3", primary=("wonder_firearm_homicide_deaths",)),
                  design_row("np", "f4", primary=("fars_all_road_fatality_rate",)),
                  design_row("st", "f5", status="not_evaluable"), design_row("ar", "f6", group="prototype"),
                  design_row("un", "f7", group="other_group")]
        for d in roster:
            r, ps, nd = D.analyze_design(d, self.loader(results), lambda *a: panels_ok(), CATALOG)
            rows.append(r), pairs.extend(ps), needs.extend(nd)
        codes = {r["design_id"]: r["code"] for r in rows}
        self.assertEqual(codes, {"ev": "EVAL", "nd": "NEEDS_COMPLEMENT_ESTIMATE", "cs": "CELL_SETS_DIFFER", "np": "NO_PAIR", "st": "STOP_PRE_RUN",
                                 "ar": "FIELD_MISSING", "un": "OTHER"})
        self.assertEqual({(p["design_id"], p["pair"], p["code"]) for p in pairs},
                         {("ev", "P3", "EVAL"), ("nd", "P1", "NEEDS_COMPLEMENT_ESTIMATE"), ("cs", "P1", "CELL_SETS_DIFFER")})
        self.assertEqual(len(needs), 2)
        by = {n["design_id"]: n for n in needs}
        self.assertEqual(by["nd"]["state_of_total"], "not registered")
        self.assertTrue(by["cs"]["state_of_total"].startswith("registered on another mask: cells=70"))
        self.assertEqual(by["cs"]["mask_cells"], 50)
        self.assertEqual(by["cs"]["total_catalog_artifact"], "d;e")
        self.assertIn("999 fixed draws", by["nd"]["call"])

    def test_sha_mismatch_and_unreadable(self):
        d = design_row("x")
        r, ps, nd = D.analyze_design(d, lambda design: ({}, False), lambda *a: panels_ok(), CATALOG)
        self.assertEqual(r["code"], "OTHER")
        self.assertIn("sha256 differs", r["reasons"][0])

        def bad(design):
            raise OSError("gone")
        r, ps, nd = D.analyze_design(d, bad, lambda *a: panels_ok(), CATALOG)
        self.assertEqual(r["code"], "OTHER")
        self.assertEqual((ps, nd), ([], []))

    def test_live_designs_registered_but_unsupported_total(self):
        A, *_ = make_pair(47)
        res = {"registered_results": {"primary": A, "secondary_wonder_homicide_deaths": {"status": "UNSUPPORTED", "result": None}}}
        d = design_row("u", primary=("wonder_firearm_homicide_deaths",))
        r, ps, nd = D.analyze_design(d, lambda design: (res, True), lambda *a: panels_ok(), CATALOG)
        self.assertEqual(r["code"], "NEEDS_COMPLEMENT_ESTIMATE")
        self.assertEqual(nd[0]["state_of_total"], "registered but not estimated (status UNSUPPORTED)")


class Inference(unittest.TestCase):
    def pairs(self, n, F, seed=3):
        rng = random.Random(seed)
        return [{"policy_family": "f%d" % (i % F), "numbers": {"Z": rng.gauss(1.0, 1.0), "beyond_a": i % 2 == 0, "opposite": i % 4 != 1,
                                                                    "beyond_c": i % 3 == 0, "omega": rng.random(), "signal": False}} for i in range(n)]

    def test_floor(self):
        self.assertFalse(D.e1_summary(self.pairs(7, 7))["run"])
        self.assertFalse(D.e1_summary(self.pairs(10, 4))["run"])
        self.assertFalse(D.e1_summary([])["run"])
        e = D.e1_summary(self.pairs(10, 5))
        self.assertTrue(e["run"])
        self.assertEqual(e["seed"], [20261011, 33])

    def test_e1_same_as_direct_call(self):
        ps = self.pairs(24, 8)
        e = D.e1_summary(ps)["result"]
        ids, F = D.N1.family_ids([p["policy_family"] for p in ps])
        direct = D.N1.e1_core([p["numbers"]["Z"] for p in ps], ids, F, 9999, np.random.default_rng([20261011, 33]))
        self.assertEqual(e["theta"], direct["theta"])
        self.assertEqual(e["interval95"], direct["interval95"])
        self.assertAlmostEqual(e["theta"], statistics.mean(p["numbers"]["Z"] for p in ps), places=12)

    def test_not_run_message_names_the_floor(self):
        e = D.e1_summary(self.pairs(2, 2))
        self.assertIn("engine floor not met", e["reason"])
        self.assertNotIn("result", e)

    def test_pattern_counts(self):
        ps = self.pairs(12, 6)
        pc = D.pattern_counts(ps)
        beyond = [p for p in ps if p["numbers"]["beyond_a"]]
        self.assertEqual(pc["pairs_with_target_beyond_line"], len(beyond))
        self.assertEqual(pc["of_those_substitute_opposite"], sum(1 for p in beyond if p["numbers"]["opposite"]))
        self.assertEqual(pc["median_offset_share_of_pairs_with_target_beyond_line"], statistics.median(p["numbers"]["omega"] for p in beyond))


class Io(unittest.TestCase):
    def test_read_panel_and_needs_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            p = tmp / "panel.csv"
            p.write_text("unit,year,outcome_value,population_denominator,other\nA,2000,1.5,100,zz\nA,2001,,200,zz\nB,2000,2.5,,zz\nB,2001,x,300,zz\n", encoding="utf-8")
            got = D.read_panel(p)
            self.assertEqual(got[("A", 2000)], (1.5, 100.0))
            self.assertEqual(got[("A", 2001)], (None, 200.0))
            self.assertEqual(got[("B", 2000)], (2.5, None))
            self.assertEqual(got[("B", 2001)], (None, 300.0))
            (tmp / "panels" / "primary").mkdir(parents=True)
            (tmp / "panels" / "primary" / "panel.csv").write_text("unit,year\n", encoding="utf-8")
            (tmp / "secondary_panels" / "s1").mkdir(parents=True)
            (tmp / "secondary_panels" / "s1" / "panel.csv").write_text("unit,year\n", encoding="utf-8")
            self.assertEqual(D.panel_path(tmp, "primary", "x"), tmp / "panels" / "primary" / "panel.csv")
            self.assertEqual(D.panel_path(tmp, "secondary_s1", "s1"), tmp / "secondary_panels" / "s1" / "panel.csv")
            self.assertIsNone(D.panel_path(tmp, "secondary_s2", "s2"))
            out = tmp / "needs.csv"
            D.write_needs_csv(out, [{"design_id": "d", "pair": "P1", "call": "c, with comma"}])
            with open(out, encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows[0]["design_id"], "d")
            self.assertEqual(rows[0]["call"], "c, with comma")
            self.assertEqual(rows[0]["mask_cells"], "")

    def test_pair_table_shape(self):
        ids = [p[0] for p in D.PAIR_TABLE]
        self.assertEqual(ids, ["P1", "P2", "P3", "P4", "P5", "P6", "P7"])
        self.assertTrue(all(p[1] != p[2] for p in D.PAIR_TABLE))
        self.assertEqual(D.SEED_E1, [20261011, 33])
        self.assertEqual((D.FLOOR_N, D.FLOOR_F), (8, 5))


if __name__ == "__main__":
    unittest.main(verbosity=1)
