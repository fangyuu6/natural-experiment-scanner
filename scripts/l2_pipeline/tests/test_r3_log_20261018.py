"""R3 outcome_transform (level | log): invented, outcome-free fixtures; no real outcome value is read."""
import copy, csv, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline import outcome_transform as ot
from scripts.l2_pipeline.cards import validate_card, variant_card, CardError
from scripts.l2_pipeline.connectors.common import ConnectorError
from scripts.l2_pipeline.panel_builder import build_panel

EXAMPLE = Path(__file__).parents[1] / 'examples/design.json'


def base_card(**top):
    card = json.loads(EXAMPLE.read_text())
    card['outcome']['series_id'] = 'synthetic_positive'
    card.update(top)
    return card


def with_series(ids):
    tmp = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
    json.dump({'schema': 'log_positive_series_v1', 'series': {i: {'evidence': 'synthetic'} for i in ids}}, tmp)
    tmp.close()
    return patch.object(ot, 'ELIGIBLE', Path(tmp.name))


def write_source(base, values):
    with (base / 'source.csv').open('w', newline='') as h:
        w = csv.DictWriter(h, fieldnames=['unit', 'year', 'outcome_value']); w.writeheader()
        for u, y, v in values: w.writerow(dict(unit=u, year=y, outcome_value=v))


def panel_card(**top):
    outcome = dict(connector='standardized_csv', paths=['source.csv'], years=[2000, 2001], units=['AL', 'AK'], frequency='annual',
                   population_filters={'age_min': 25}, source_population_filters={'age_min': 25}, outcome_unit='per 1,000', series_id='synthetic_positive')
    return dict({'design_id': 'synthetic', 'population': {'filters': {'age_min': 25}}, 'outcome': outcome}, **top)


class Field(unittest.TestCase):
    def test_default_is_level_and_old_cards_validate_unchanged(self):
        card = base_card()
        self.assertEqual(validate_card(card), [])
        self.assertEqual(ot.transform(card), 'level')
        self.assertEqual(ot.export_fields(card), {})

    def test_invalid_value_rejected(self):
        self.assertTrue(validate_card(base_card(outcome_transform='ihs')))
        self.assertTrue(validate_card(base_card(outcome_transform='log1p')))

    def test_explicit_level_passes_without_series_check(self):
        self.assertEqual(validate_card(base_card(outcome_transform='level')), [])

    def test_log_needs_cleared_series_else_card_stays_level(self):
        with with_series([]):
            errors = validate_card(base_card(outcome_transform='log'))
        self.assertTrue(any('stays level' in e for e in errors), errors)
        with with_series(['synthetic_positive']):
            self.assertEqual(validate_card(base_card(outcome_transform='log')), [])

    def test_no_offsets_or_zero_replacement_keys(self):
        card = base_card(outcome_transform='log'); card['outcome']['log1p_offset'] = 1
        with with_series(['synthetic_positive']):
            self.assertTrue(any('log1p' in e for e in validate_card(card)))
        card = base_card(outcome_transform='log'); card['outcome']['zero_replacement'] = 0.5
        with with_series(['synthetic_positive']):
            self.assertTrue(validate_card(card))

    def test_no_switch_in_a_sensitivity(self):
        card = base_card(outcome_transform='log')
        with with_series(['synthetic_positive']):
            with self.assertRaises(CardError):
                variant_card(card, {'outcome_transform': 'level'})
            variant = variant_card(card, {'population': {}})
        self.assertEqual(variant['outcome_transform'], 'log')

    def test_s0_gate_reads_no_outcome_value(self):
        card = base_card(outcome_transform='log')
        with with_series([]):
            self.assertTrue(ot.s0_issues(card))
        with with_series(['synthetic_positive']):
            self.assertEqual(ot.s0_issues(card), [])


class Construction(unittest.TestCase):
    def build(self, values, **top):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        base = Path(d.name); write_source(base, values)
        report = build_panel(panel_card(**top), base, base / 'out')
        with Path(report['panel_path']).open() as h: return report, list(csv.DictReader(h))

    def test_log_panel_is_natural_log_in_log_points(self):
        import math
        report, rows = self.build([('AL', 2000, '1.0'), ('AL', 2001, '2.718281828459045'), ('AK', 2000, '10'), ('AK', 2001, '100')], outcome_transform='log')
        got = {(r['unit'], r['year']): float(r['outcome_value']) for r in rows}
        self.assertAlmostEqual(got[('AL', '2000')], 0.0); self.assertAlmostEqual(got[('AL', '2001')], 1.0)
        self.assertAlmostEqual(got[('AK', '2001')], math.log(100))
        self.assertEqual({r['outcome_unit'] for r in rows}, {'log points'})
        self.assertEqual(report['construction']['outcome_transform'], 'log')

    def test_level_panel_bytes_unchanged_by_the_new_code(self):
        _, plain = self.build([('AL', 2000, '1.2300'), ('AL', 2001, '4'), ('AK', 2000, '5'), ('AK', 2001, '6')])
        _, explicit = self.build([('AL', 2000, '1.2300'), ('AL', 2001, '4'), ('AK', 2000, '5'), ('AK', 2001, '6')], outcome_transform='level')
        keep = lambda rows: [(r['unit'], r['year'], r['outcome_value'], r['outcome_unit']) for r in rows]
        self.assertEqual(keep(plain), keep(explicit))
        self.assertIn('1.2300', [r['outcome_value'] for r in plain])

    def test_nonpositive_value_stops_with_no_artifact_and_no_replacement(self):
        for bad in ('0', '-1.5'):
            d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
            base = Path(d.name); write_source(base, [('AL', 2000, '1'), ('AL', 2001, bad), ('AK', 2000, '3'), ('AK', 2001, '4')])
            with self.assertRaises(ConnectorError):
                build_panel(panel_card(outcome_transform='log'), base, base / 'out')
            self.assertFalse((base / 'out' / 'panel.csv').exists())


class Export(unittest.TestCase):
    def test_export_units_field(self):
        card = base_card(outcome_transform='log')
        self.assertEqual(ot.export_fields(card), {'outcome_transform': 'log', 'units': 'log points'})
        card = base_card(outcome_transform='level'); card['outcome']['outcome_unit'] = 'per 1,000'
        self.assertEqual(ot.export_fields(card), {'outcome_transform': 'level', 'units': 'per 1,000'})

    def test_exports_function_carries_units_only_when_declared(self):
        from scripts.l2_pipeline.stages.s7_export import exports
        terminal = {'status': 'STOPPED', 'reason': 'x'}
        plain = exports(base_card(), terminal, None, {})['estimates_page']
        self.assertNotIn('units', plain); self.assertNotIn('outcome_transform', plain)
        logged = exports(base_card(outcome_transform='log'), terminal, None, {})['estimates_page']
        self.assertEqual((logged['outcome_transform'], logged['units']), ('log', 'log points'))


class SourceUnit(unittest.TestCase):
    def test_export_fields_source_unit_only_for_log_cards(self):
        log = ot.export_fields(base_card(outcome_transform='log'), 'per 1,000 residents')
        self.assertEqual(log, {'outcome_transform': 'log', 'units': 'log points', 'source_unit_before_log': 'per 1,000 residents'})
        self.assertEqual(ot.export_fields(base_card(outcome_transform='log')), {'outcome_transform': 'log', 'units': 'log points'})
        self.assertNotIn('source_unit_before_log', ot.export_fields(base_card(outcome_transform='level'), 'per 1,000'))
        self.assertEqual(ot.export_fields(base_card(), 'per 1,000'), {})

    def test_exports_carry_source_unit_and_old_exports_byte_identical(self):
        import json as _json
        from scripts.l2_pipeline.stages.s7_export import exports
        terminal = {'status': 'COMPLETED', 'reason': 'x'}
        result = {'source_unit_before_log': 'per 1,000 residents', 'att': 0.1}
        out = exports(base_card(outcome_transform='log'), terminal, result, {})
        for block in ('registry_event', 'estimates_page', 'l3_meta'):
            self.assertEqual(out[block]['source_unit_before_log'], 'per 1,000 residents')
            self.assertEqual(out[block]['units'], 'log points')
        # level cards and cards without the field ignore the result key: output equals the export without it
        for card in (base_card(), base_card(outcome_transform='level')):
            with_key = exports(card, terminal, dict(result), {})
            without = exports(card, terminal, {'att': 0.1}, {})
            for block in with_key:
                self.assertNotIn('source_unit_before_log', with_key[block])
                a = {k: v for k, v in with_key[block].items() if k != 'result'}
                b = {k: v for k, v in without[block].items() if k != 'result'}
                self.assertEqual(_json.dumps(a, sort_keys=True), _json.dumps(b, sort_keys=True))
        stopped = exports(base_card(outcome_transform='log'), {'status': 'STOPPED', 'reason': 'x'}, None, {})
        self.assertNotIn('source_unit_before_log', stopped['estimates_page'])

    def test_s2_details_feed_the_registration_bundle_and_result_record(self):
        import inspect
        from scripts.l2_pipeline import registered
        src = inspect.getsource(registered)
        self.assertIn("export_fields(card,(panels.get('primary',{}).get('construction') or {}).get('source_unit_before_log'))", src)
        self.assertIn("('outcome_transform','units','source_unit_before_log')", src)

    def test_s2_records_source_unit_in_construction(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        base = Path(d.name); write_source(base, [('AL', 2000, '1'), ('AL', 2001, '2'), ('AK', 2000, '3'), ('AK', 2001, '4')])
        report = build_panel(panel_card(outcome_transform='log'), base, base / 'out')
        self.assertEqual(report['construction']['source_unit_before_log'], 'per 1,000')


class Pools(unittest.TestCase):
    log = {'outcome_transform': 'log', 'units': 'log points'}
    level = {'units': 'per 1,000'}

    def test_pool_never_mixes_scales(self):
        self.assertEqual(ot.assert_poolable([self.log, dict(self.log)]), 'log')
        self.assertEqual(ot.assert_poolable([self.level, {'outcome_transform': 'level', 'units': 'x'}]), 'level')
        with self.assertRaises(ValueError):
            ot.assert_poolable([self.log, self.level])

    def test_benchmark_scale_rule(self):
        for args in ((self.log, 'log'), (self.log, 'percent'), (self.level, 'level')):
            ot.check_benchmark(*args, 'rate', 'rate')
        for entry, bench in ((self.log, 'level'), (self.level, 'log'), (self.level, 'percent')):
            with self.assertRaises(ValueError): ot.check_benchmark(entry, bench, 'rate', 'rate')

    def test_benchmark_basis_rule(self):
        ot.check_benchmark(self.log, 'log', 'count', 'count'); ot.check_benchmark(self.log, 'percent', 'rate', 'rate')
        for basis, bench_basis in (('rate', 'count'), ('count', 'rate')):
            with self.assertRaises(ValueError): ot.check_benchmark(self.log, 'log', basis, bench_basis)
            with self.assertRaises(ValueError): ot.check_benchmark(self.log, 'percent', basis, bench_basis)
        with self.assertRaises(ValueError): ot.check_benchmark(self.log, 'log', 'rate', 'share')
        with self.assertRaises(ValueError): ot.check_benchmark(self.log, 'log', 'level', 'rate')

    def test_log_export_without_units_is_refused(self):
        with self.assertRaises(ValueError): ot.scale_class({'outcome_transform': 'log'})


if __name__ == '__main__':
    unittest.main()
