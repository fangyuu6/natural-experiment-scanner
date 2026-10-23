"""Prospective routing, population identity and calendar-hole regressions."""
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.agents.jobs import emit_stage_jobs
from scripts.l2_pipeline.connectors import ConnectorError, ConnectorRequest, construct
from scripts.l2_pipeline.coverage import coverage_bounded_card
from scripts.l2_pipeline.first_order import target_mapping
from scripts.l2_pipeline.stages.s1_treatment import run
from scripts.l2_pipeline.contracts import DesignCard, Disposition, StageContext
from scripts.l2_pipeline.cli import run_stage
from scripts.l2_pipeline import hub


class ResearchOutcomeIntegration(unittest.TestCase):
    def card(self, series='acs1_insurance_coverage_age_19_25', filters=None):
        return {'treatment': {'source_type': 'research_panel',
                    'source_window': {'start_year': 2000, 'end_year': 2024},
                    'coverage_end': 2024},
                'outcome': {'connector': 'outcome_v2_' + series, 'series_id': series,
                    'measure_id': series, 'years': [2000, 2024], 'units': ['AL'],
                    'population_filters': filters or {'age_group': '19_25', 'sex': 'all'}},
                'first_order': {'target_outcome': 'health_insurance_policy'}}

    def test_calendar_holes_and_changed_age_bins_never_splice(self):
        current = coverage_bounded_card(self.card())
        self.assertEqual(current['outcome']['years'], [2017, 2024])
        self.assertNotIn(2020, current['outcome']['period_years'])
        historical = coverage_bounded_card(self.card('acs1_insurance_coverage_age_18_24'))
        self.assertEqual(historical['outcome']['years'], [2008, 2016])
        self.assertTrue(target_mapping(self.card())['mapped'])
        self.assertFalse(target_mapping(self.card(filters={'age_group': '18_24', 'sex': 'all'}))['mapped'])

    def test_missing_cause_and_wrong_series_are_rejected_before_data_read(self):
        card = self.card('cdc_leading_cause_deaths')
        card['first_order']['target_outcome'] = 'mortality_by_cause'
        card['outcome']['population_filters'] = {}
        self.assertFalse(target_mapping(card)['mapped'])
        card['outcome']['population_filters'] = {'cause_name': 'Suicide'}
        self.assertTrue(target_mapping(card)['mapped'])
        card['outcome']['series_id'] = 'cdc_firearm_deaths'
        request = ConnectorRequest.from_card(card, Path.cwd())
        with patch('scripts.l2_pipeline.connectors.construct_outcome_v2') as adapter:
            with self.assertRaises(ConnectorError):
                construct(request)
            adapter.assert_not_called()

    def test_research_panel_uses_only_the_official_audit_route(self):
        ctx = SimpleNamespace(card=SimpleNamespace(payload=self.card()))
        with patch('scripts.l2_pipeline.sources.research_panel.s1.run', return_value='research_result') as route:
            self.assertEqual(run(ctx), 'research_result')
            route.assert_called_once_with(ctx)
        with patch('scripts.l2_pipeline.sources.research_panel.s1.emit_auditor_job', return_value='job') as emitter:
            self.assertEqual(emit_stage_jobs(ctx, 'S1', attempt_id='prospective'), ['job'])
            self.assertEqual(emitter.call_args.kwargs['attempt_id'], 'prospective')
            with self.assertRaises(ValueError):
                emit_stage_jobs(ctx, 'S1', roles=['A', 'B'])

    def test_audit_packet_preparation_grants_no_s1_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value = dict(self.card(), design_id='invented_research', version='v1',
                policy_family='invented', mechanism='invented', track='DiD',
                unit='state', outcome_family='health')
            path = root / 'card.json'
            path.write_text(json.dumps(value))
            ctx = StageContext(DesignCard.from_mapping(value, path), root / 'work', Path.cwd())
            args = SimpleNamespace(stage='S1', allow_draft=False, jobs_root=root / 'jobs',
                attempt_id='prospective', source_pack=None)
            with patch.object(hub, 'contexts', return_value=[(ctx, None)]), \
                    patch.object(hub, 'preflight', side_effect=ValueError('S0 parked')) as check, \
                    patch.object(hub, 'emit_stage_jobs', return_value=[]):
                report = hub.prepare(args)
                self.assertEqual(report['results'][0]['status'], 'PREPARED')
                check.assert_not_called()
            result = run_stage(ctx, 'S1')
            self.assertEqual(result.disposition, Disposition.FAILED)
            self.assertIn('Missing prior stage receipt: S0', result.reason)
            self.assertFalse((ctx.design_dir / 'TREATMENT.json').exists())


if __name__ == '__main__':
    unittest.main()
