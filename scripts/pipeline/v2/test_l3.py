#!/usr/bin/env python3
"""Annual-value, unknown handling, random audit and real count-only corpus invariants."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('lean_l3', Path(__file__).with_name('l3.py'))
l3 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(l3)
ROOT = Path(__file__).resolve().parents[3]


def record(value, field='first_enactment_date', status='known', **kw):
    return dict(value=value, field_id=field, value_status=status, **kw)


class ValueTests(unittest.TestCase):
    def test_day_precision_and_layout_do_not_make_dispute(self):
        a = l3.analysis_value([record('2001-02-03')], 'start_year')
        b = l3.analysis_value([record('2001'), record('2001-12-31')], 'start_year')
        self.assertEqual(l3.compare(a, b)['analysis_agree'], 1)

    def test_opposite_years_are_disputed(self):
        self.assertEqual(l3.compare(l3.analysis_value([record('2001')], 'start_year'),
                                    l3.analysis_value([record('2002')], 'start_year'))['analysis_dispute'], 1)

    def test_unknown_and_rejected_negative_never_become_zero(self):
        a = l3.analysis_value([record('unknown', 'adoption_status', 'unknown')], 'adoption_status')
        b = l3.analysis_value([record('nonadoption_certified', 'adoption_status')], 'adoption_status')
        self.assertEqual(a[0], None)
        self.assertEqual(b[0], None)
        self.assertEqual(l3.compare(a, b)['analysis_agree'], 0)
        self.assertEqual(l3.compare(a, b)['both_unknown'], 1)

    def test_future_first_adoption_is_not_yet_at_target(self):
        rows = [record('2026'), record('adopted', 'adoption_status')]
        self.assertEqual(l3.analysis_value(rows, 'adoption_status')[0], 'not_yet')

    def test_multiple_distinct_clock_years_require_review(self):
        value = l3.analysis_value([record('1985'), record('1990')], 'start_year')
        self.assertEqual(value[1], 'ambiguous')
        self.assertEqual(l3.compare(value, value)['raw_agree'], 0)

    def test_one_sided_known_is_gap_not_opposite_fact(self):
        a = l3.analysis_value([record('2000')], 'start_year')
        b = l3.analysis_value([record('unknown', status='unknown')], 'start_year')
        self.assertEqual(l3.compare(a, b)['analysis_dispute'], 0)
        self.assertEqual(l3.compare(a, b)['evidence_gap'], 1)

    def test_random_audit_is_deterministic_and_representative(self):
        pairs = [dict(policy='test', state=f'S{i}', adopt_year=2000 if i < 24 else None, end=2010) for i in range(48)]
        a = l3.audit_draw(pairs)
        self.assertEqual(a, l3.audit_draw(list(reversed(pairs))))
        self.assertEqual(len(a), 10)
        self.assertEqual({r['stratum'] for r in a}, {'positive', 'absence'})
        self.assertTrue(all(r['stratum_sample'] >= 2 for r in a))
        tiny = [dict(policy='test', state=f'S{i}', adopt_year=2000 if i else None, end=2010) for i in range(48)]
        b = l3.audit_draw(tiny)
        self.assertEqual(len(b), 10)
        self.assertEqual(sum(r['stratum']=='absence' for r in b), 1)

    def test_lag_calendar_is_metadata_only(self):
        rule = dict(defensibility='candidate_objective_need', count_lag_years='2', first_year='1976', last_year='2024')
        self.assertEqual(l3.need_calendar(rule, 1980), 1978)
        self.assertIsNone(l3.need_calendar(rule, 1977))


class CorpusTests(unittest.TestCase):
    def test_real_corpus_invariants(self):
        output_root = ROOT / 'data/pipeline/v2'
        output_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.l3-v2-test-', dir=output_root) as folder:
            out = Path(folder)
            summary = l3.run(ROOT, out)
            first = {p.name:p.read_bytes() for p in out.glob('l3*')}
            self.assertEqual(summary['blind_cells'], 976)
            self.assertEqual(summary['analysis_agree'], 161)
            self.assertEqual(summary['both_known'], 163)
            self.assertEqual(summary['analysis_disputes'], 2)
            self.assertEqual(summary['remaining_analysis_disputes'], 1)
            self.assertEqual(summary['conditional_compiled_policies'], 10)
            self.assertEqual(summary['random_audit_drawn_units'], 100)
            self.assertEqual(summary['data_ready_policies'], 0)
            counts = {r['scope']:r for r in summary['pooled_counts']}
            self.assertEqual((counts['pilot10']['policies'], counts['pilot10']['events']), (10, 172))
            self.assertEqual((counts['fixed100']['policies'], counts['fixed100']['events']), (100, 1581))
            self.assertEqual((counts['author_spid_all']['policies'], counts['author_spid_all']['events']), (648, 13893))
            for row in counts.values():
                self.assertEqual(row['at_risk_rows'], row['convention_zero_rows']+row['events'])
                self.assertLessEqual(row['need_observed_risk_rows'], row['need_calendar_risk_rows'])
            l3.run(ROOT, out)
            self.assertEqual(first, {p.name:p.read_bytes() for p in out.glob('l3*')})
            self.assertEqual(summary['association_tables'], 0)
            self.assertEqual(summary['downloads_this_run'], [])
            self.assertEqual(sum(r['blind_eligible']=='0' for r in l3.read(out/'l3_analysis_cells.csv')), 112)
            sample = l3.read(out/'l3_audit_sample.csv')
            self.assertTrue(any(r['stratum']=='absence' and r['status_reviewed']=='0' for r in sample))
            policies = l3.read(out/'l3_policy_readiness.csv')
            self.assertTrue(all(r['audit_pass']=='0' for r in policies))
            self.assertTrue(all(float(r['unresolved_as_fail_status_screen_rate']) > .05 for r in policies))


if __name__ == '__main__':
    unittest.main()
