"""Q2 retry, W2 defect: requeue() hands validate_retry the LOADED card (cards.load_card -> connectors.rates.prospective_card, which normalises
outcome.outcome_unit of a rate card), the stored old card is raw JSON; comparing them raw refused the dv, mental_health and straw cards
('Q2 retry card may differ from the old card only in version'). The comparison now uses one representation; every real difference is still refused.
Uses the real card files read-only (never written) and the synthetic repo of test_curated_retry_20261023."""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline import curated_route as cr
from scripts.l2_pipeline.autopilot import atomic
from scripts.l2_pipeline.connectors.rates import prospective_card
from scripts.l2_pipeline.requeue import requeue
from scripts.l2_pipeline.storage import sha
from scripts.l2_pipeline.tests.test_curated_retry_20261023 import RetryBase

REPO = Path(__file__).resolve().parents[3]
CARDS = REPO / 'data/l2_pipeline/intake/bulk_20261020/ready_now/cards'
REAL = ('b1020rn_campus_ccw_ban_violent_crime', 'b1020rn_dv_firearm_removal_at_scene_firearm_homicide',
        'b1020rn_mental_health_commitment_prohibitor_firearm_homicide', 'b1020rn_straw_purchase_law_firearm_homicide')
NORMALISED = ('b1020rn_dv_firearm_removal_at_scene_firearm_homicide', 'b1020rn_mental_health_commitment_prohibitor_firearm_homicide',
              'b1020rn_straw_purchase_law_firearm_homicide')


def raw_old(design):
    path = CARDS / (design + '_curated.json')
    if not path.is_file():
        raise unittest.SkipTest('real curated card not present: ' + path.name)
    return path, json.loads(path.read_text())


class RealCardTests(unittest.TestCase):
    def test_load_normalisation_changes_only_the_outcome_unit_of_the_three_rate_cards(self):
        for design in REAL[1:]:
            _, old = raw_old(design)
            norm = prospective_card(old)
            self.assertEqual({k for k in norm if norm[k] != old.get(k)}, {'outcome'}, design)
            self.assertEqual({k for k in norm['outcome'] if norm['outcome'][k] != old['outcome'].get(k)}, {'outcome_unit'}, design)
            self.assertEqual(prospective_card(norm), norm)                       # idempotent: both sides can be normalised

    def test_version_only_retry_card_is_accepted_in_loaded_form_for_all_four(self):
        for design in REAL:
            path, old = raw_old(design)
            new = deepcopy(old)
            new['version'] = 'v99'
            loaded = prospective_card(new)                                       # what load_card().payload is
            # the W2 comparison (raw old vs loaded new) refused the three normalised cards:
            raw_changed = {k for k in set(old) | set(loaded) if old.get(k) != loaded.get(k)}
            self.assertEqual(raw_changed != {'version'}, design in NORMALISED, design)
            self.assertTrue(cr.only_version_differs(path, loaded), design)

    def test_every_real_difference_is_still_refused(self):
        for design in REAL:
            path, old = raw_old(design)
            changes = {
                'mapping': lambda c: c['automation']['history_source'].update(mapping_sha256='0' * 64),
                'treatment_excluded': lambda c: c['treatment'].update(excluded_units=sorted(set(c['treatment'].get('excluded_units', [])) | {'XX'})),
                'treatment_universe': lambda c: c['treatment'].update(universe=list(c['treatment']['universe'])[:-1]),
                'window': lambda c: c['outcome'].update(years=[c['outcome']['years'][0] + 1, c['outcome']['years'][-1]]),
                'outcome_series': lambda c: c['outcome'].update(series_id='something_else'),
                'analysis': lambda c: c['analysis'].update(min_controls=99),
                'population': lambda c: c['population'].update(description='other'),
                'design_id': lambda c: c.update(design_id='other'),
                'extra_key': lambda c: c.update(unregistered=True),
            }
            for name, change in changes.items():
                new = deepcopy(old)
                new['version'] = 'v99'
                try:
                    change(new)
                except KeyError:
                    continue
                with self.subTest(design=design, change=name):
                    self.assertFalse(cr.only_version_differs(path, prospective_card(new)))

    def test_same_version_or_no_version_change_is_not_a_version_only_difference(self):
        path, old = raw_old(REAL[1])
        self.assertFalse(cr.only_version_differs(path, prospective_card(old)))   # identical card: nothing differs, so not "version only"


class RawFileTests(RetryBase):
    """The written card file is also compared raw, so a change hidden by normalisation cannot pass."""

    def test_raw_card_file_must_differ_only_in_version(self):
        for name, change in (('unit', lambda c: c['outcome'].update(outcome_unit='a different raw unit')),
                             ('mapping', lambda c: c['automation']['history_source'].update(mapping_sha256='1' * 64))):
            card = deepcopy(self.new_card)
            change(card)
            path = Path(self.row['card']).with_name('raw_%s.json' % name)
            atomic(path, card)
            with self.subTest(name):
                # a normaliser that maps every outcome_unit to one string hides the 'unit' change from the loaded form; the raw check still refuses it
                with patch('scripts.l2_pipeline.connectors.rates.prospective_card', side_effect=normalise_unit):
                    self.assertFalse(cr.only_version_differs(self.row['card'], normalise_unit(card), path))
        self.assertTrue(cr.only_version_differs(self.row['card'], self.new_card, self.new_path))

    def test_requeue_succeeds_when_load_card_normalises_the_outcome_unit(self):
        """Reproduces W2: the stored old card has a raw unit that load_card rewrites; the retry must still requeue."""
        old = json.loads(Path(self.row['card']).read_text())
        old['outcome']['outcome_unit'] = 'firearm homicide deaths, state of residence'
        atomic(Path(self.row['card']), old)
        row = dict(self.row, card_sha256=sha(self.row['card']))
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[row]))
        self.row = row
        new = deepcopy(old)
        new['version'] = 'v10'
        atomic(self.new_path, new)
        with patch('scripts.l2_pipeline.connectors.rates.prospective_card', side_effect=normalise_unit):
            self.log_receipt()
            value = requeue(self.design, self.new_path, self.make_ruling(), self.pilot)
        self.assertEqual((value['restart_action'], value['curated_retry']), ('history', True))
        self.assertEqual(self.retry_row()['attempt_id'], 'requeue_v10')

    def test_requeue_still_refuses_a_real_change_under_normalisation(self):
        old = json.loads(Path(self.row['card']).read_text())
        old['outcome']['outcome_unit'] = 'firearm homicide deaths, state of residence'
        atomic(Path(self.row['card']), old)
        row = dict(self.row, card_sha256=sha(self.row['card']))
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[row]))
        self.row = row
        new = deepcopy(old)
        new['version'] = 'v10'
        new['treatment']['excluded_units'] = ['S01']
        atomic(self.new_path, new)
        with patch('scripts.l2_pipeline.connectors.rates.prospective_card', side_effect=normalise_unit):
            self.log_receipt()
            with self.assertRaises(ValueError):
                requeue(self.design, self.new_path, self.make_ruling(), self.pilot)
        self.assertEqual(self.retry_row(), row)


def normalise_unit(card):
    result = deepcopy(dict(card))
    result['outcome'] = dict(result.get('outcome', {}), outcome_unit='deaths per 100,000 residents in declared age/sex population')
    return result


if __name__ == '__main__':
    unittest.main()
