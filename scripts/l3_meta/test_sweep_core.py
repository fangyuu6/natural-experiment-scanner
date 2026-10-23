"""Checks of sweep_core.py on INVENTED data (no repo data file is read, no L2 result is read, nothing is written).

They tie the operators of the outcome sweep to the L2 engine, which is imported read-only with bytecode writing off:
  * the operator path reproduces engine.runner.fit_primary / ri.permutation_reference / ri.one_reference on an invented panel;
  * with the primary's own cell pattern the aligned label bundle equals engine.ri.predraw_labels and the operators equal calibration_operators;
  * on a cell pattern that loses a state the aligned draws are the restriction of the primary's draws and the operators equal the engine
    run with explicitly given labels;
  * linearity (complement series), the support helpers, the eligibility rules, the draw statistics and the WONDER reader on an invented export.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/test_sweep_core.py
"""
import copy
import hashlib
import json
import math
import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import sweep_core as S  # noqa: E402

L = S.load_l2()
YEARS = list(range(2000, 2014))


def pycache_dirs():
    return sorted(str(p) for p in (ROOT / "scripts" / "l2_pipeline").rglob("__pycache__"))


def records(treated_cohorts, never):
    """Treatment records of an invented design: treated units start in their cohort year."""
    recs = []
    for u, g in treated_cohorts.items():
        recs.append(dict(unit=u, status="treated", start_period=g))
    for u in never:
        recs.append(dict(unit=u, status="never_treated", start_period=None))
    return recs


COHORTS = {**{u: 2006 for u in ("T1", "T2", "T3", "T4")}, **{u: 2009 for u in ("T5", "T6", "T7", "T8")}}
NEVER = ["N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9", "N10", "N11"]
UNITS = sorted(list(COHORTS) + NEVER)


def card(group="never_treated", rule=None):
    analysis = dict(min_cohorts=2, min_controls=5, min_pre_periods=3, min_treated=6, min_post_periods=2, controls=group)
    if rule:
        analysis["support_gap_rule"] = rule
    return dict(design_id="invented", track="DiD", analysis=analysis, outcome=dict(frequency="annual", years=[YEARS[0], YEARS[-1]]),
                treatment={}, transition_year_drop=False, comparison_group=group)


class FakeCtx:
    def __init__(self, group="never_treated", rule=None, recs=None):
        self.cards = {"primary": card(group, rule)}
        self.treatment = {"records": recs if recs is not None else records(COHORTS, NEVER)}
        self.adapter = None
        self.mode = S.STRICT
        self.card = self.cards["primary"]


def panel(seed, shift=0.0, noise=0.2, drop=()):
    """(unit, year) -> value: random walks with a treatment shift for treated units from their cohort year."""
    rng = random.Random(seed)
    out = {}
    for j, u in enumerate(UNITS):
        level = 20 + 2 * j
        for y in YEARS:
            level += rng.gauss(0, noise)
            if (u, y) in drop:
                continue
            out[(u, y)] = level + (shift if u in COHORTS and y >= COHORTS[u] else 0.0)
    return out


def build(ctx, values):
    b = S.Builder(ctx)
    dec, keys, design, summ = b.build(set(values))
    return b, dec, keys, design, summ


class Eligibility(unittest.TestCase):
    def test_strict_rule_matches_engine(self):
        meta = dict(available=1, annual_usable=1, linkage_certified=1, suppressed=False, unreliable=False)
        self.assertTrue(S.strict_eligible(meta))
        self.assertTrue(S.strict_eligible(dict(meta, suppressed="False")))
        self.assertFalse(S.strict_eligible(dict(meta, available=0)))
        self.assertFalse(S.strict_eligible(dict(meta, annual_usable=True)))          # the CSV writer would give 'True', which is not '1'
        self.assertTrue(S.semantic_eligible(dict(meta, annual_usable=True)))
        self.assertFalse(S.strict_eligible(dict(meta, unreliable=True)))
        self.assertFalse(S.strict_eligible(dict(meta, suppression_flag="1")))
        self.assertTrue(S.strict_eligible(dict(unit="AL", year=2000)))              # absent flags default as in the engine
        # the engine function on a real csv round trip gives the same answer
        import csv, io
        for m in (meta, dict(meta, annual_usable=True), dict(meta, suppressed=True), dict(meta, available=0)):
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=["unit", "year", *m])
            w.writeheader()
            w.writerow(dict(unit="AL", year=2000, **m))
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "metadata.csv"
                p.write_text(buf.getvalue())
                self.assertEqual(bool(L.panels.metadata_eligible_keys(p)), S.strict_eligible(m), m)


class Statistics(unittest.TestCase):
    def test_draw_statistics_equal_engine_noise_rules(self):
        rng = np.random.default_rng(3)
        draws = rng.normal(0, 2, 999)
        att = 3.1
        st = S.draw_statistics(att, draws)
        from scripts.l2_pipeline.engine import noise
        feas = dict(requested_draws=999, feasible_draws=999, infeasible_count=0, infeasible_draws=[], feasible_fraction=1.0, support_pass=True)
        nf = noise.noise_summary(dict(overall=list(map(float, draws)), feasibility=feas, draws=999, draw_sha256="x"), att)
        self.assertAlmostEqual(st["sd"], nf["sample_sd"], places=12)
        self.assertAlmostEqual(st["cutoff"], nf["absolute_cutoff"], places=12)
        self.assertAlmostEqual(st["p2"], nf["p_two_sided"], places=12)
        with_nan = np.concatenate([draws[:900], np.full(99, np.nan)])
        self.assertEqual(S.draw_statistics(att, with_nan)["n"], 900)

    def test_scaled_agreement(self):
        self.assertTrue(S.agree(1e9, 1e9 + 5))
        self.assertFalse(S.agree(1.0, 1.0 + 1e-6))
        self.assertAlmostEqual(S.max_scaled_diff([1e6], [1e6 + 1]), 1 / (1e6 + 1), places=12)


class OperatorsEqualEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = pycache_dirs()
        cls.values = panel(11, shift=-2.0)
        cls.ctx = FakeCtx()
        cls.b, cls.dec, cls.keys, cls.design, cls.summ = build(cls.ctx, cls.values)
        cls.ops = S.build_ops(cls.dec, cls.design, S.own_bundles(cls.design), with_cells=True)
        cls.y = np.array([cls.values[k] for k in cls.ops.keys])

    def test_design_is_nontrivial(self):
        self.assertEqual(len(self.design["units"]), 19)
        self.assertEqual(self.design["groups"], [2006, 2009])
        self.assertGreater(len(self.design["cells"]), 20)
        self.assertEqual(S.family_of(self.design["cells"]), L.ri.pre_family(self.design))

    def test_att_and_cells_equal_fit_primary(self):
        rows = [(u, t, v, None) for (u, t), v in sorted(self.values.items())]
        from scripts.l2_pipeline.engine.runner import fit_primary
        dec = dict(self.dec, variant="sweep")                        # not 'primary': no certified interval is requested
        payload, design, outcomes = fit_primary(dec, rows, B=2)
        res = payload["result"]
        att, _ = S.project(self.ops, self.y)
        self.assertAlmostEqual(att, res["overall"]["att"], places=11)
        ca = S.cell_atts(self.ops, self.y)
        for a, c in zip(ca, res["group_time"]):
            self.assertAlmostEqual(a, c["att"], places=11)
        self.assertEqual([(c["g"], c["t"], c["base_period"]) for c in self.design["cells"]], [(c["g"], c["t"], c["base_period"]) for c in res["group_time"]])

    def test_draws_equal_permutation_reference(self):
        rows = [(u, t, v, None) for (u, t), v in sorted(self.values.items())]
        outcomes = {(u, t): v for u, t, v, _ in L.design.structural_rows(self.dec, rows)}
        ref = L.ri.permutation_reference(self.dec, self.design, outcomes, count=999)
        att, draws = S.project(self.ops, self.y)
        self.assertEqual(self.ops.feasibility["feasible_draws"], ref["feasibility"]["feasible_draws"])
        bad = {x["draw_index"] for x in ref["feasibility"]["infeasible_draws"]}
        self.assertEqual(self.ops.draw_index, [i for i in range(999) if i not in bad])
        self.assertLess(S.max_scaled_diff(draws[self.ops.draw_index], ref["overall"]), 1e-11)

    def test_operators_equal_calibration_operators(self):
        rows = [(u, t, 0.0, None) for u, t in sorted(self.keys)]
        from scripts.l2_pipeline.engine.calibration import calibration_operators
        cal = calibration_operators(self.dec, L.design.structural_rows(self.dec, rows), L.design.treatment_rows(self.dec), permutations=999)
        self.assertEqual(cal["keys"], self.ops.keys)
        self.assertLess(float(np.max(np.abs(cal["overall"] - self.ops.overall))), 1e-13)
        self.assertEqual(cal["ri_overall"].shape, self.ops.ri_overall.shape)
        self.assertLess(float(np.max(np.abs(cal["ri_overall"] - self.ops.ri_overall))), 1e-13)
        self.assertEqual(cal["ri_feasibility"]["infeasible_draws"], self.ops.feasibility["infeasible_draws"])

    def test_aligned_bundle_of_the_primary_equals_engine_bundle(self):
        al = S.AlignedLabels(self.dec, self.design, YEARS)
        self.assertTrue(S.aligned_equals_engine(al, self.design))
        att, draws = S.project(S.build_ops(self.dec, self.design, al.bundles(self.design)), self.y)
        att0, draws0 = S.project(self.ops, self.y)
        self.assertEqual(att, att0)
        self.assertTrue(np.array_equal(draws, draws0, equal_nan=True))

    def test_no_pycache_written_under_l2(self):
        self.assertEqual(pycache_dirs(), self.before)


class LostState(unittest.TestCase):
    """A series that lacks every cell of one state: the aligned draws are the restriction of the primary's, not the engine's own shuffle."""

    @classmethod
    def setUpClass(cls):
        cls.values0 = panel(21, shift=-1.5)
        cls.ctx = FakeCtx()
        cls.b, cls.dec0, cls.keys0, cls.design0, _ = build(cls.ctx, cls.values0)
        cls.al = S.AlignedLabels(cls.dec0, cls.design0, YEARS)
        lost = "T3"                                                         # a treated state
        cls.values1 = {k: v for k, v in panel(22, shift=2.0).items() if k[0] != lost}
        cls.dec1, cls.keys1, cls.design1, _ = cls.b.build(set(cls.values1), "lost")
        cls.lost = lost

    def test_pattern_loses_exactly_the_state(self):
        self.assertEqual(set(self.design0["units"]) - set(self.design1["units"]), {self.lost})
        self.assertNotEqual(S.mask_fingerprint(self.design0), S.mask_fingerprint(self.design1))

    def test_engine_own_shuffle_differs_from_aligned_draws(self):
        own = S.own_bundles(self.design1, 5)
        aligned = list(self.al.bundles(self.design1))[:5]
        self.assertNotEqual([[l[0] for l in b] for b in own], [[l[0] for l in b] for b in aligned])

    def test_aligned_draws_equal_restriction_of_the_primary_bundle(self):
        full = [b for _, b in zip(range(30), self.al.bundles(self.design0))]
        part = [b for _, b in zip(range(30), self.al.bundles(self.design1))]
        pos = {u: i for i, u in enumerate(self.design0["units"])}
        for bf, bp in zip(full, part):
            for u, lab in zip(self.design1["units"], bp):
                self.assertEqual(lab[0], bf[pos[u]][0])
                self.assertEqual(lab[1], bf[pos[u]][1])
                self.assertEqual(lab[3], bf[pos[u]][3])

    def test_operators_equal_engine_one_reference_with_the_same_labels(self):
        ops = S.build_ops(self.dec1, self.design1, self.al.bundles(self.design1))
        y = np.array([self.values1[k] for k in ops.keys])
        att, draws = S.project(ops, y)
        outcomes = dict(zip(ops.keys, y))
        family = L.ri.pre_family(self.design1)
        got = []
        for b, lab in enumerate(self.al.bundles(self.design1)):
            r = L.ri.one_reference(lab, (self.dec1, self.design1, outcomes, family))
            if not r.get("infeasible"):
                got.append((b, r["overall"]))
        self.assertEqual([b for b, _ in got], ops.draw_index)
        self.assertLess(S.max_scaled_diff(draws[ops.draw_index], [v for _, v in got]), 1e-11)
        self.assertGreater(ops.feasibility["feasible_draws"], 900)

    def test_full_rectangle_exposure_agrees_with_the_primary(self):
        exp = S.full_exposure(self.dec0, self.design0["units"], YEARS)
        for u in self.design0["units"]:
            for t, cell in self.design0["exposure"][u].items():
                self.assertEqual(exp[u][t], cell)


class Linearity(unittest.TestCase):
    def test_complement_series_identity(self):
        ctx = FakeCtx()
        a, c = panel(31, shift=-2.0), panel(32, shift=+1.0)
        t = {k: a[k] + c[k] for k in a}
        b, dec, keys, design, _ = build(ctx, a)
        ops = S.build_ops(dec, design, S.own_bundles(design))
        ya, yc, yt = (np.array([m[k] for k in ops.keys]) for m in (a, c, t))
        att_a, da = S.project(ops, ya)
        att_c, dc = S.project(ops, yc)
        att_t, dt_ = S.project(ops, yt)
        self.assertAlmostEqual(att_t, att_a + att_c, places=11)
        ok = np.isfinite(da)
        self.assertLess(float(np.max(np.abs(dt_[ok] - da[ok] - dc[ok]))), 1e-10)


class BuilderSupport(unittest.TestCase):
    def test_gap_rule_drops_the_state_with_a_missing_year(self):
        ctx = FakeCtx(rule="drop_affected_state_v7")
        values = panel(41, drop={("N2", 2004)})
        b, dec, keys, design, summ = build(ctx, values)
        self.assertNotIn("N2", design["units"])
        self.assertIn("N2", summ["support_gap_excluded_units"])
        self.assertFalse(any(u == "N2" for u, _ in keys))

    def test_no_gap_rule_keeps_the_state_and_its_other_years(self):
        ctx = FakeCtx(rule=None)
        values = panel(42, drop={("N2", 2004)})
        b, dec, keys, design, summ = build(ctx, values)
        self.assertIn("N2", design["units"])
        self.assertNotIn(("N2", 2004), keys)

    def test_engine_refusal_is_a_value_error(self):
        ctx = FakeCtx()
        values = {k: v for k, v in panel(43).items() if k[1] <= 2005}           # no post-period cell for any cohort
        with self.assertRaises(ValueError):
            build(ctx, values)

    def test_legacy_mode_keeps_a_cohort_with_one_post_period(self):
        values = {k: v for k, v in panel(47).items() if k[1] <= 2009}           # the 2009 cohort has one post year
        strict = FakeCtx()
        _, dec_s, _, design_s, _ = build(strict, values)
        self.assertEqual(design_s["groups"], [2006])                              # the current rule needs two post periods
        self.assertEqual(dec_s["case"]["min_post_periods"], 2)
        legacy = FakeCtx()
        legacy.mode = S.LEGACY
        _, dec_l, _, design_l, summ_l = build(legacy, values)
        self.assertEqual(design_l["groups"], [2006, 2009])                        # the historical geometry has no such minimum
        self.assertNotIn("min_post_periods", dec_l["case"])
        self.assertEqual(summ_l["post_support_excluded_units"], [])

    def test_not_yet_treated_design_builds(self):
        recs = records(COHORTS, NEVER[:3]) + [dict(unit=u, status="not_yet_treated", start_period=2012) for u in NEVER[3:8]]
        ctx = FakeCtx(group="not_yet_treated", recs=recs)
        ctx.cards["primary"]["analysis"]["controls"] = "not_yet_treated"
        values = panel(44)
        b, dec, keys, design, summ = build(ctx, values)
        self.assertEqual(design["control_group"], "not_yet_treated")

    def test_treated_units_and_kept_share(self):
        ctx = FakeCtx()
        b, dec, keys, design, _ = build(ctx, panel(45))
        T0 = S.treated_units(design)
        self.assertEqual(T0, set(COHORTS))
        values = {k: v for k, v in panel(46).items() if k[0] not in ("T1", "T2")}
        _, _, _, design2, _ = build(ctx, values)
        self.assertEqual(len(S.treated_units(design2) & T0) / len(T0), 0.75)


class WonderReader(unittest.TestCase):
    """An invented TSV export in the layout of CDC WONDER; the reader must follow engine rates.apply_scale."""

    HEADER = "Notes\tState\tState Code\tYear\tYear Code\tDeaths\tPopulation\tCrude Rate\n"

    def spec(self, tmp, rows):
        p = Path(tmp) / "invented_deaths__D76_2000-2001.tsv"
        p.write_text(self.HEADER + "\n".join(rows) + "\n", encoding="utf-8")
        return dict(value_field="Deaths", units="deaths", exports=[dict(raw_path=str(p), sha256=S.sha256_file(p))])

    def test_rate_and_count_versions(self):
        rows = ['\t"Alabama"\t"01"\t"2000"\t"2000"\t500\t4000000\t12.5',
                '\t"Alabama"\t"01"\t"2001"\t"2001"\tSuppressed\t4010000\tSuppressed',
                '\t"Alaska"\t"02"\t"2000"\t"2000"\t30\t600000\t5.0 (Unreliable)',
                '\t"Alaska"\t"02"\t"2001"\t"2001"\t40\t610000\t6.6',
                '\t"Alaska"\t"02"\t"2002"\t"2002"\t41 (Unreliable)\t610000\t6.6']
        with tempfile.TemporaryDirectory() as tmp:
            spec = self.spec(tmp, rows)
            # the export has a year the receipt would refuse; the reader itself only reads cells
            rate = S.build_wonder_series("wonder_homicide_deaths", spec, "rate")
            count = S.build_wonder_series("wonder_homicide_deaths", spec, "count")
        self.assertAlmostEqual(rate["val"][("AL", 2000)], 500 / 4000000 * 100000, places=12)
        self.assertTrue(rate["ok"][("AL", 2000)])
        self.assertFalse(rate["ok"][("AL", 2001)])                 # suppressed
        self.assertFalse(rate["ok"][("AK", 2000)])                 # crude rate flagged unreliable
        self.assertTrue(rate["ok"][("AK", 2001)])
        self.assertFalse(rate["ok"][("AK", 2002)])                 # count flagged unreliable
        self.assertEqual(count["val"][("AL", 2000)], 500.0)
        self.assertTrue(count["ok"][("AK", 2000)])                 # the count version has no rate-reliability rule
        self.assertFalse(count["ok"][("AL", 2001)])
        self.assertFalse(count["ok"][("AK", 2002)])
        self.assertTrue(math.isnan(count["den"][("AL", 2000)]))
        self.assertEqual(rate["den"][("AL", 2000)], 4000000.0)

    def test_count_version_refused_for_a_published_rate_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = self.spec(tmp, ['\t"Alabama"\t"01"\t"2000"\t"2000"\t500\t4000000\t12.5'])
            spec["value_field"], spec["units"] = "Crude Rate", "deaths per 100,000"
            with self.assertRaises(ValueError):
                S.build_wonder_series("wonder_homicide_rate", spec, "count")


class StoreRoundTrip(unittest.TestCase):
    def test_arrays_and_views(self):
        s1 = dict(val={("AL", 2000): 1.5, ("AK", 2000): float("nan")}, ok={("AL", 2000): True, ("AK", 2000): False}, sem={("AL", 2000): True, ("AK", 2000): False},
                  den={("AL", 2000): 10.0, ("AK", 2000): float("nan")})
        s2 = dict(val={("AL", 2000): 15.0}, ok={("AL", 2000): True}, sem={("AL", 2000): True}, den={})
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.npz", Path(tmp) / "b.npz"
            arrays = S.store_arrays({"x": s1}, dict(k=1))
            S.save_series_store(a, {"x": s1}, dict(k=1))
            S.save_series_store(b, {"x": s2}, dict(k=2))
            self.assertTrue(S.arrays_equal(arrays, a))
            self.assertFalse(S.arrays_equal(S.store_arrays({"x": s2}, dict(k=1)), a))
            store = S.SeriesStore(a, b)
            self.assertEqual(store.sids, ["x"])
            self.assertEqual(store.count_sids, ["x"])
            self.assertEqual(store.eligible_keys("x", ["AL", "AK"], [2000]), {("AL", 2000)})
            self.assertEqual(store.view("rate").values("x", [("AL", 2000)])[0], 1.5)
            self.assertEqual(store.view("count").values("x", [("AL", 2000)])[0], 15.0)
            self.assertEqual(store.view("rate").eligible_keys("x", ["AL"], [2000]), {("AL", 2000)})


if __name__ == "__main__":
    unittest.main(verbosity=2)
