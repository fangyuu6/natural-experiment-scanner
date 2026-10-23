"""C38 routing preserves locked constructs, blindness and terminal exclusions."""
import copy
import json
from pathlib import Path
import unittest

from scripts.l2_pipeline.intake import c38
from scripts.l2_pipeline.agents.jobs import load_job
from scripts.l2_pipeline.agents.contract_tests import job_type
from scripts.l2_pipeline.storage import sha


class C38HistoryRoutingTests(unittest.TestCase):
    def card(self):
        return dict(design_id='c35_cspp_regulation_mandatory_seatbelts',
            mechanism=c38.SEATBELT, treatment=dict(predicate=c38.SEATBELT),
            outcome=dict(series_id='fars_motor_vehicle_occupant_fatality_rate',
                frequency='annual', period_years=[1984, 1985, 1986],
                population_filters=dict(person_type='occupant')))

    def test_either_enforcement_and_actual_operation_clock_are_preserved(self):
        card = self.card(); original = copy.deepcopy(card)
        plan = c38.planning(card, ['MT', 'GA', 'MO', 'MN'])
        self.assertEqual(plan['mechanism'], c38.SEATBELT)
        self.assertIn('secondary-to-primary upgrade is not a new adoption', plan['enforcement_rule'])
        self.assertEqual(plan['clock']['ruling'], 'HUB_RULING_C11_IN_OPERATION')
        self.assertIn('exclude from both arms', plan['clock']['exclusion'])
        self.assertFalse(plan['s1_ready']); self.assertFalse(plan['execution_authorized'])
        self.assertEqual(card, original)

    def test_primary_only_redefinition_and_outcome_switch_are_refused(self):
        card = self.card()
        card['mechanism'] = card['treatment']['predicate'] = 'Primary enforcement only'
        with self.assertRaises(ValueError): c38.planning(card, ['MT'])
        card = self.card(); card['outcome']['series_id'] = 'fars_all_road_fatality_rate'
        with self.assertRaises(ValueError): c38.planning(card, ['MT'])

    def test_terminal_duplicate_and_running_death_penalty_are_not_routes(self):
        self.assertEqual(len(c38.ROUTES), 4)
        self.assertNotIn('c35_siegel_gunshowh', c38.ROUTES)
        self.assertNotIn('c35_cspp_death_penalty', c38.ROUTES)

    def test_prepared_packets_have_only_planning_inputs_and_disjoint_outputs(self):
        emission = c38.JOBS / 'EMISSION_REPORT.json'
        if not emission.exists(): self.skipTest('C38 task preparation absent')
        report = c38.read(emission)
        self.assertEqual((report['jobs'], report['designs'], report['attempt_id']), (8, 4, 'c38'))
        jobs = [load_job(Path(p)) for p in report['job_paths']]
        self.assertEqual(len({j.output_dir for j in jobs}), 8)
        for job in jobs:
            self.assertEqual(job_type(job), 's1_official_history_v9')
            # Archived packets retain their original absolute provenance. A
            # devcopy must exercise the identical template bytes at its new root.
            self.assertTrue(Path(job.template_path).is_file())
            self.assertEqual(sha(job.template_path), sha(c38.TEMPLATE))
            self.assertEqual(job.contract['mechanism'], job.contract['manual']['predicate'])
            self.assertEqual(len(job.contract['manual']['universe']), 50)
            self.assertEqual(job.contract['manual']['seed'], 352010)
            self.assertTrue(job.contract['peer_read_forbidden'])
            self.assertFalse(job.contract['s1_ready']); self.assertFalse(job.contract['execution_authorized'])
            plan = c38.read(next(i['path'] for i in job.inputs if Path(i['path']).name == 'COLLECTION_PLAN.json'))
            original = c38.read(c38.C35 / 'cards' / (job.design_id + '.json'))
            self.assertEqual(plan['mechanism'], original['mechanism'])
            for k in ('series_id', 'frequency', 'period_years', 'population_filters'):
                self.assertEqual(plan['outcome'][k], original['outcome'][k])
            self.assertEqual({Path(i['path']).name for i in job.inputs}, {'COLLECTION_PLAN.json', 'MANIFEST.json'})
            self.assertEqual(set(job.contract['permitted_source_paths']), {i['path'] for i in job.inputs})
            self.assertFalse(set(job.contract['previous_packet_hashes']) & set(job.contract['permitted_source_paths']))
            # C38 is now running; immutable preparation bytes remain checked.
            # Dispatch state is owned by its hub and is not a routing invariant.
        ledger = c38.read(c38.ROOT / 'C37_LEDGER_C38.json')
        duplicate = next(r for r in ledger['rows'] if r['design_id'] == 'c35_siegel_gunshowh')
        self.assertEqual(duplicate['disposition'], 'EXCLUDED_DUPLICATE_TERMINAL')
        self.assertEqual(duplicate['emitted_jobs'], 0)


if __name__ == '__main__': unittest.main()
