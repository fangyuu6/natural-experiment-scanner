"""Release regressions: preserve stopped results and fixed, source-bound counts."""
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.viz_site import make_l2_rows as rows


class ReleaseRowsTests(unittest.TestCase):
    def test_fixed_snapshot_excludes_unfinished_history_work(self):
        data = rows.snapshot()
        gates = data['not_evaluable_gate_members']
        counted = {key for ids in gates.values() for key in ids}
        self.assertEqual(len(counted), 77)
        self.assertEqual(data['counts']['overall'],
                         dict(processed=125, estimated=48, not_evaluable=77))
        for pending in data['parked_gate_unresolved_rows']:
            self.assertNotIn(pending['design_id'], counted)

    def test_all_snapshot_runs_included_and_postrun_stops_retained(self):
        actual = rows.load_pipeline_rows()
        expected = {r['registry_run_id'] for r in rows.snapshot()['estimated_rows']
                    if r['group'] == 'registry_terminal'}
        self.assertEqual({r['run_id'] for r in actual}, expected)
        self.assertEqual({r['case'] for r in actual if r['stopped']},
                         {'pay_secrecy', 'run7_refundable_eitc', 'run8_gunshow_background_check'})
        self.assertEqual([r['case'] for r in actual if r['exceeds']],
                         ['run4_lit_family_planning_income_waiver'])

    def test_manual_sources_bind_fixed_snapshot_including_batch1_amendment(self):
        fixed = {r['source_path']: r for r in rows.snapshot()['estimated_rows']
                 if r['group'] == 'manual_batch'}
        archive = rows.archived_rows()[15:]
        self.assertEqual({r['source'] for r in archive}, set(fixed))
        for row in archive:
            self.assertEqual(row['sha256'], fixed[row['source']]['source_sha256_recorded'])
        self.assertEqual({r['case'] for r in archive if 'amended_source' in r},
                         {'pfml', 'eitc_first_refundable'})

    def test_uncertified_intervals_are_not_rendered(self):
        suppressed = {r['case'] for r in rows.load_pipeline_rows() if r['ci95'] is None}
        self.assertEqual(suppressed, {
            'run2_energy_public_benefit_fund', 'run2_meth_precursor_restrictions',
            'run8_extreme_risk_protection_order', 'c18_siegel_mcdv',
            'run7_abortion_informed_consent',
            'run8_domestic_violence_restraining_order_firearm_restriction'})
        for lang in ('en', 'zh'):
            figure = rows.render_noise(lang)
            for key in suppressed:
                section = re.search(r'<div class="l2-noise-row"[^>]*data-design="'+key+r'".*?(?=<div class="l2-noise-row"|</figure>)', figure, re.S).group()
                self.assertNotIn('class="whisker"', section)
                if key == 'run2_energy_public_benefit_fund':
                    self.assertNotIn('class="mark"', section)
                    self.assertIn('单位待认证' if lang == 'zh' else 'Unit awaiting certification', section)
                else:
                    self.assertIn('无区间' if lang == 'zh' else 'no interval', section)

    def test_bilingual_rows_and_gate_counts(self):
        for lang in ('en', 'zh'):
            additions = rows.render_additions(lang)
            self.assertEqual(additions.count('<tr data-'), 55)
            self.assertEqual(additions.count('data-prerun="true"'), 2)
            self.assertIn('871/999', additions)
            self.assertIn('742/999', additions)
            self.assertIn('2026-10-11', rows.render_gates(lang))
            gates = {key: int(n) for key, n in re.findall(r'data-gate="([^"]+)" data-count="(\d+)"', rows.render_gates(lang))}
            self.assertEqual(gates, rows.snapshot()['counts']['not_evaluable_by_gate'])
            self.assertEqual(rows.render_noise(lang).count('class="l2-noise-row"'), 53)

    def test_open_shared_axis_preserves_all_six_stops_and_native_values(self):
        for lang in ('en', 'zh'):
            figure = rows.render_noise(lang)
            self.assertIn('<details class="l2-definition">', figure)
            self.assertLess(figure.index('</details>'), figure.index('data-outcome-order='))
            self.assertEqual(figure.count('class="plot-axis '), 1)
            self.assertEqual(figure.count('class="l2-stopped"'), 6)
            first = figure.split('data-outcome-order=', 1)[1].split('data-outcome-order=', 1)[0]
            self.assertIn('class="band"', first)
            self.assertNotIn('log points', figure)
            self.assertNotIn('对数点', figure)
            self.assertEqual(figure.count(' · 修订重跑' if lang == 'zh' else ' · Amended rerun'), 8)
        self.assertEqual(rows.number(3420.456), '3,420')
        self.assertEqual(rows.number(-0.000061), '−0.0000610')
        for row in rows.load_pipeline_rows():
            self.assertGreater(row['n_treated'], 0)
            self.assertGreater(row['n_controls'], 0)
            self.assertTrue(row['window'])
            self.assertTrue(row['outcome_source'])

    def test_result_methods_and_unified_scale(self):
        from collections import Counter
        actual = rows.load_pipeline_rows()
        self.assertEqual(Counter(r['interval_metadata']['method'] for r in actual),
                         {'webb_wcu_t': 12, 'control_path_ct': 11, None: 4})
        for lang in ('en', 'zh'):
            table = rows.render_additions(lang)
            for r in actual:
                if r['ci95']:
                    self.assertIn(rows.method_tag(r, lang), table)
            self.assertEqual(table.count('<table'), 1)
            self.assertNotIn('class="l2-gates"', table)
            self.assertIn('class="l2-gates"', rows.render_gates(lang))
            self.assertIn('0.0203', table)
            self.assertIn('−4.44', table)
            self.assertNotIn('+2.1%', table)
        self.assertIn('医疗补助计划生育服务收入资格豁免', rows.render_additions('zh'))

    def test_primary_rows_trace_synthesis_including_stops(self):
        all_rows = rows.archived_rows() + rows.load_pipeline_rows() + rows.stopped_prototypes()
        primary = [r for r in all_rows if r.get('outcome_role', 'primary') == 'primary' and r['reference_available']]
        meta = rows.read_json(rows.ROOT / 'data/l3_meta/l2_set_v2.json')['questions']['Q1']
        self.assertEqual(len(primary), meta['n'])
        self.assertEqual(sum(r['exceeds'] for r in primary), meta['k'])
        self.assertEqual({r['case'] for r in primary if r['exceeds']},
                         {key.removeprefix('prototype:') for key in meta['exceeding_designs']})
        pipeline = [r for r in primary if r['order'] in range(24, 51)]
        self.assertEqual((len(pipeline), sum(r['exceeds'] for r in pipeline)), (27, 1))
        for lang in ('en', 'zh'):
            figure = rows.render_noise(lang)
            self.assertEqual(figure.count('data-outcome-role="secondary"'), 4)
            self.assertIn('5/45', figure)
            self.assertIn('75.5%', figure)
            self.assertIn('89.5%', figure)
            self.assertIn('85.0%', figure)
            self.assertIn('86.0%', figure)
            self.assertIn('class="l2-stopped"><svg', figure)
        clocks = {r['case']: r['clock'][0] for r in rows.load_pipeline_rows()}
        self.assertEqual(sum('DellaVigna' in value for value in clocks.values()), 10)
        for key in ('run4_lit_nurse_licensure_compact', 'run4_lit_family_planning_income_waiver'):
            self.assertIn('Published-study implementation-date year', clocks[key])

    def test_changed_source_bytes_fail_closed(self):
        with patch.object(Path, 'read_bytes', return_value=b'{}'):
            with self.assertRaisesRegex(ValueError, 'source hash mismatch'):
                rows.verified_json(rows.SNAPSHOT, '0' * 64)


if __name__ == '__main__':
    unittest.main()
