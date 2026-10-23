"""T187 (HUB_RULING_20261020_SEX_OFFENDER_CARDS.md, panel 5-0): for the construct "rape" the preferred series is the legacy rate; the revised
series stays a labelled secondary. The change is a NEW population table OUTCOME_POPULATIONS_V17C.json (V17B plus that one field) that
population_v63.CURRENT_PATH points to; V17B stays byte-identical because cards and rulings bind its hash. Metadata only; no outcome value."""
import json
import unittest
from pathlib import Path
from scripts.l2_pipeline.connectors import population_v63
from scripts.l2_pipeline.population_resolution import choose
from scripts.l2_pipeline.storage import sha

PURPOSE = dict(status='VERIFIED', target_outcome='rape', target_construct='rape')
V17B_SHA256 = 'b9544f7d9ae532caabab391c81a50cf249c5f16ab431adbcc850c805e667bb40'      # the bytes the 10-20 cards and rulings bind
V17C_SHA256 = '10b95da4c6621f1213c0692f933ff2ade1a8a6e55736ca02c3d833de1276f305'       # the 10-20 pointer table, kept byte-identical after the 10-23 switch to V19


class RapePreferredSeries(unittest.TestCase):
    def tables(self):
        folder = Path(population_v63.__file__).parent
        return json.loads((folder / 'OUTCOME_POPULATIONS_V17B.json').read_text()), json.loads((folder / 'OUTCOME_POPULATIONS_V17C.json').read_text()), folder

    def test_v17b_and_v17c_are_untouched_and_current_is_v19_which_carries_the_rape_field(self):
        _, _, folder = self.tables()
        self.assertEqual(sha(folder / 'OUTCOME_POPULATIONS_V17B.json'), V17B_SHA256)
        self.assertEqual(sha(folder / 'OUTCOME_POPULATIONS_V17C.json'), V17C_SHA256)
        self.assertEqual(population_v63.CURRENT_PATH.name, 'OUTCOME_POPULATIONS_V19.json')   # window 2026-10-23: V19 = V18 + this one field + six QCEW series
        current = json.loads(population_v63.CURRENT_PATH.read_text())
        self.assertEqual([t['preferred_series'] for t in current['targets'] if t['construct'] == 'rape'], ['fbi_ucr_rape_legacy_rate'])

    def test_v17c_differs_from_v17b_in_exactly_the_rape_preferred_series(self):
        b, c, _ = self.tables()
        self.assertEqual(c['catalog_id'], b['catalog_id'])
        rape_b = [t for t in b['targets'] if t['construct'] == 'rape']; rape_c = [t for t in c['targets'] if t['construct'] == 'rape']
        self.assertEqual(len(rape_c), 1); self.assertNotIn('preferred_series', rape_b[0])
        self.assertEqual(rape_c[0]['preferred_series'], 'fbi_ucr_rape_legacy_rate')
        self.assertEqual({k: v for k, v in rape_c[0].items() if k != 'preferred_series'}, rape_b[0])
        self.assertEqual([t for t in c['targets'] if t['construct'] != 'rape'], [t for t in b['targets'] if t['construct'] != 'rape'])
        self.assertEqual({k: v for k, v in c.items() if k != 'targets'}, {k: v for k, v in b.items() if k != 'targets'})
        self.assertEqual(c['series']['fbi_ucr_rape_revised_rate'], b['series']['fbi_ucr_rape_revised_rate'])          # revised stays catalogued

    def test_rape_no_longer_ties_and_selects_legacy(self):
        for family in ('sex_offender_notification', 'sex_offender_registration', 'anything'):
            result = choose(dict(policy_family=family), PURPOSE)
            self.assertEqual(result['selected']['series_id'], 'fbi_ucr_rape_legacy_rate')
            self.assertEqual([c['series_id'] for c in result['candidates']], ['fbi_ucr_rape_legacy_rate'])

    def test_the_old_table_still_ties(self):
        """V17B alone (what the 10-20 cards were built against) keeps the tie, so nothing built on it is reinterpreted."""
        from scripts.l2_pipeline.connectors import outcome_v17
        source = outcome_v17.catalog(verify_sources=False)
        _, meta = population_v63.catalog(source=source, path=Path(population_v63.__file__).parent / 'OUTCOME_POPULATIONS_V17B.json')
        with self.assertRaisesRegex(ValueError, 'equal population and supported-window series tie'):
            choose(dict(policy_family='sex_offender_notification'), PURPOSE, source, meta)

    def test_other_constructs_keep_their_resolution(self):
        from scripts.l2_pipeline.connectors import outcome_v17
        source, new = population_v63.current_catalog()
        source17 = outcome_v17.catalog(verify_sources=False)
        _, old = population_v63.catalog(source=source17, path=Path(population_v63.__file__).parent / 'OUTCOME_POPULATIONS_V17B.json')
        for target, construct in (('reported_violent_crime', 'reported_violent_crime'), ('homicide_deaths', 'homicide_deaths'), ('robbery', 'robbery'),
                                  ('employment_status', 'employment_status'), ('ui_benefit_receipt', 'ui_benefit_receipt')):
            a = choose(dict(policy_family='gun_policy'), dict(status='VERIFIED', target_outcome=target, target_construct=construct), source17, old)
            b = choose(dict(policy_family='gun_policy'), dict(status='VERIFIED', target_outcome=target, target_construct=construct), source, new)
            self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()
