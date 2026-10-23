"""Prospective V4 routing through the ordinary hub connector interface."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from scripts.l2_pipeline import connectors
from scripts.l2_pipeline.connectors.common import ConnectorRequest, ConnectorError
from scripts.l2_pipeline.coverage import coverage_bounded_card

ROOT = Path(__file__).resolve().parents[3]


class V4IntegrationTests(unittest.TestCase):
    def catalog(self):
        return json.loads((ROOT / 'scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V4.json').read_text())

    def test_fresh_hub_process_registers_v4_without_data_code(self):
        result = subprocess.run(
            [sys.executable, '-B', '-c',
             "import scripts.l2_pipeline.hub; "
             "from scripts.l2_pipeline.connectors import CONNECTOR_CATALOG; "
             "print(CONNECTOR_CATALOG['outcome_v4_wonder_firearm_deaths']['catalog_version'])"],
            cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '4')

    def test_available_v4_series_bind_exact_measure_and_population(self):
        for sid, spec in self.catalog()['series'].items():
            if not spec['public_available']:
                continue
            filters = spec.get('population_filters', {})
            if spec.get('required_filters') == ['age_group']:
                filters = {'age_group': spec['age_groups'][0]}
            if spec.get('required_filters') == ['cause_name']:
                filters = {'cause_name': spec['causes'][0]}
            outcome = dict(connector='outcome_v4_' + sid, series_id=sid,
                           measure_id=sid, population_filters=filters, frequency='annual')
            with self.subTest(series=sid):
                self.assertTrue(connectors.v2_supported(outcome, sid))
                wrong = copy.deepcopy(outcome)
                wrong['population_filters']['invented_population'] = 'all'
                self.assertFalse(connectors.v2_supported(wrong, sid))

    def test_blocked_age_birth_rate_cannot_claim_fertility_rate(self):
        spec = self.catalog()['series']['wonder_birth_rate_by_maternal_age']
        self.assertFalse(spec['public_available'])
        self.assertEqual(spec['years'], [])
        self.assertFalse(connectors.target_mapped(
            'outcome_v4_wonder_birth_rate_by_maternal_age', 'contraception_access',
            'wonder_birth_rate_by_maternal_age'))

    def test_mortality_seam_is_usable_through_public_construct(self):
        sid = 'wonder_firearm_deaths'
        spec = self.catalog()['series'][sid]
        filters = spec['population_filters']
        raw = 'data/l2_pipeline/connectors_v2/wonder/raw/'
        outcome = dict(connector='outcome_v4_' + sid, series_id=sid, measure_id=sid,
                       population_filters=filters, frequency='annual',
                       paths=[raw + sid + '__D76_1999-2017.tsv',
                              raw + sid + '__D158_2018-2024.tsv'],
                       years=[2017, 2018], units=['AL'])
        request = ConnectorRequest.from_card({'outcome': outcome}, ROOT)
        panel = connectors.construct(request)
        self.assertEqual([(r['unit'], r['year']) for r in panel.rows], [('AL', 2017), ('AL', 2018)])
        self.assertTrue(all(r['outcome_value'] is not None for r in panel.rows))
        wrong = copy.deepcopy(outcome)
        wrong['population_filters'] = {'injury_intent': 'suicide', 'injury_mechanism': 'firearm'}
        with self.assertRaises(ConnectorError):
            connectors.construct(ConnectorRequest.from_card({'outcome': wrong}, ROOT))

    def test_coverage_bounds_preserve_disjoint_source_years(self):
        sid = 'wonder_firearm_deaths'
        filters = self.catalog()['series'][sid]['population_filters']
        bounded = coverage_bounded_card(dict(
            treatment={'source_window': {'start_year': 1991, 'end_year': 2020},
                       'coverage_end': 2020},
            outcome={'connector': 'outcome_v4_' + sid, 'series_id': sid,
                     'measure_id': sid, 'population_filters': filters,
                     'years': [1991, 2024], 'period_years': list(range(1991, 2025))}))
        self.assertEqual(bounded['outcome']['years'], [1999, 2020])
        self.assertEqual(bounded['outcome']['period_years'], list(range(1999, 2021)))
        self.assertFalse(bounded['automation']['coverage_window']['parked'])


if __name__ == '__main__':
    unittest.main()
