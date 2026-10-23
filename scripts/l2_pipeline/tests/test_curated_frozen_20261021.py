"""T221: the five frozen mapping files (two independent mappers + a third reader) load under the curated route, bind to the
cards, and give the roster counts the T219 drafts predicted. Reads only the RAND workbook, the cards and the mapping files."""
from copy import deepcopy
import json
import unittest
from pathlib import Path

from scripts.l2_pipeline import curated_history as ch, curated_route as cr

REPO = Path(__file__).resolve().parents[3]
FROZEN_DIR = REPO / cr.MAPPING_DIR
CARDS = REPO / 'data/l2_pipeline/intake/bulk_20261020/ready_now/cards'
XLSX = REPO / cr.XLSX
KEYS = ('campus_ccw_ban_violent_crime', 'dv_firearm_removal_at_scene_firearm_homicide',
        'mental_health_commitment_prohibitor_firearm_homicide', 'state_preemption_punitive_firearm_homicide',
        'straw_purchase_law_firearm_homicide')
EXPECT = {'campus_ccw_ban_violent_crime': (18, 27), 'dv_firearm_removal_at_scene_firearm_homicide': (20, 16),
          'mental_health_commitment_prohibitor_firearm_homicide': (11, 19), 'state_preemption_punitive_firearm_homicide': (13, 37),
          'straw_purchase_law_firearm_homicide': (12, 34)}


def spec_of(key):
    return json.loads((FROZEN_DIR / ('b1020rn_%s.json' % key)).read_text())


def card_of(key):
    return json.loads((CARDS / ('b1020rn_%s.json' % key)).read_text())


class FrozenMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not XLSX.is_file():
            raise unittest.SkipTest('RAND workbook not present')
        cls.rows = ch.load_rows(XLSX)

    def test_each_file_is_frozen_by_three_distinct_readers_and_valid(self):
        for key in KEYS:
            spec = spec_of(key)
            cr.check_frozen(spec)
            ch.validate_mapping(spec)
            self.assertEqual(len(set(spec['mappers'])), 3, key)
            self.assertEqual(spec['design_id'], 'b1020rn_' + key)
            self.assertEqual(spec['freeze']['state'], 'frozen')

    def test_each_file_binds_to_its_card_through_load_spec(self):
        for key in KEYS:
            spec = spec_of(key)
            payload = deepcopy(card_of(key))
            payload['automation'] = dict(payload.get('automation') or {}, history_source=dict(
                kind=ch.SOURCE_KIND, mapping_sha256=ch.mapping_sha(spec), reason='access', rule=ch.RULING))
            loaded = cr.load_spec(REPO, payload)
            self.assertEqual(ch.mapping_sha(loaded), ch.mapping_sha(spec), key)

    def test_roster_counts_and_dates_match_the_card_screening(self):
        for key in KEYS:
            spec = spec_of(key)
            history = ch.build_history(self.rows, spec)
            self.assertLessEqual(history['ambiguity']['ambiguous_share'], ch.MAX_AMBIGUOUS_SHARE, key)
            accepted = {r['unit']: r for r in history['accepted']}
            self.assertEqual(len(accepted) + len(history['excluded']), len(spec['universe']), key)
            treated = {u: r['best_evidence_start_year'] for u, r in accepted.items() if r['status'] == 'treated' and not r.get('pre_window_bound')}
            never = [u for u, r in accepted.items() if r['status'] == 'never_treated']
            self.assertEqual((len(treated), len(never)), EXPECT[key], key)
            screening = card_of(key)['treatment']['screening_adoption']['adopters']
            in_db = [u for u in screening if u in treated]
            self.assertTrue(all(treated[u] == screening[u] for u in in_db), key)
            self.assertGreaterEqual(len(in_db), len(screening) - 1, key)
            plan = ch.audit_plan(history, spec, spec['design_id'])
            self.assertEqual(plan['census'], len(treated) <= 12)

    def test_stalking_and_violent_misdemeanor_have_no_frozen_mapping(self):
        for key in ('stalking_misdemeanor_firearm_restriction_firearm_homicide', 'violent_misdemeanor_firearm_restriction_firearm_homicide'):
            self.assertFalse((FROZEN_DIR / ('b1020rn_%s.json' % key)).exists())


if __name__ == '__main__':
    unittest.main()
