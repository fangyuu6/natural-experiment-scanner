"""T258: stacked-WLS identity check is rounding-aware; invented cells only, no real data."""
from __future__ import annotations
import unittest
import numpy as np
from scripts.l2_pipeline.engine import inference

CONTROLS = ['n1', 'n2', 'n3', 'n4', 'n5']
UNITS = ['a', 'b', 'c', 'd'] + CONTROLS
PERIODS = list(range(8))


def cell(t, treated):
    return dict(g=2, t=t, event_time=t - 2, base_period=1, post=True, n_treated=len(treated),
                n_control=len(CONTROLS), treated=treated, controls=CONTROLS)


def geometry(unequal):
    cells = [cell(t, ['a', 'b', 'c', 'd']) for t in (2, 3, 4)]
    cells += [cell(t, ['a', 'b', 'c'] if unequal else ['a', 'b', 'c', 'd']) for t in (5, 6, 7)]
    design = dict(units=UNITS, periods=PERIODS, cells=cells, whole_window_clean={u: True for u in UNITS})
    return inference.geometry(design)


def outcomes(scale, seed):
    return np.cumsum(np.random.default_rng(seed).normal(0, scale, (len(UNITS), len(PERIODS))), axis=1)


def run(g, Y):
    return inference.candidates(g, Y, inference.draws(g, 20, 1), methods=())


class StackedIdentityTolerance(unittest.TestCase):
    def test_large_scale_outcomes_do_not_trip_on_rounding(self):
        for unequal in (False, True):          # equal and R-S3C1-style unequal cohort membership
            g = geometry(unequal)
            for seed in range(40):
                att = run(g, outcomes(1e8, seed))['att']
                self.assertTrue(np.isfinite(att))

    def test_att_value_is_unchanged_by_tolerance(self):
        g = geometry(True); Y = outcomes(1.0, 5)
        self.assertEqual(run(g, Y)['att'], float(np.sum(g['H'] * Y)))

    def test_real_weight_mismatch_still_raises(self):
        for scale in (1.0, 1e8):
            g = geometry(True); g['A'][0, 0] *= 1.01
            with self.assertRaisesRegex(ValueError, 'Stacked WLS differs'):
                run(g, outcomes(scale, 3))

    def test_tolerance_never_below_old_absolute_bound(self):
        g = geometry(True); g['A'][0, 0] += 1e-6          # tiny but real error at unit scale
        with self.assertRaisesRegex(ValueError, 'Stacked WLS differs'):
            run(g, outcomes(1.0, 2))


if __name__ == '__main__':
    unittest.main()
