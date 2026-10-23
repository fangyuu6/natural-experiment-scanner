"""Synthetic public tracker tables do not certify absent historical changes."""
import unittest

from scripts.l2_pipeline.policy_tracker_extra import (
    STATE_NAMES, parse_iihs_seatbelts, parse_kff_expansion,
)


def table(rows, month='October 2026'):
    return '<html><p>' + month + '</p><table>' + ''.join(
        '<tr>' + ''.join('<td>' + cell + '</td>' for cell in row) + '</tr>'
        for row in rows) + '</table></html>'


def kff_rows():
    rows = [['Location', 'Status of Medicaid Expansion Decision', 'Expansion Implementation Date',
             'Expansion Adopted Through Ballot Initiative', 'Trigger Law In Place']]
    for name in STATE_NAMES:
        rows.append([name, 'Adopted', '1/1/2014', 'No', 'No'] if name != 'Alabama'
                    else [name, 'Not Adopted', 'N/A', 'N/A', ''])
    next(row for row in rows if row[0] == 'Maine')[2] = '1/10/2019'
    return rows


def iihs_rows():
    rows = [['State', 'Initial effective date', 'Primary enforcement?', 'Who is covered? In what seats?',
             'fine', 'child seat', 'adult belt', 'child fine', 'rear seat']]
    for code in STATE_NAMES.values():
        rows.append([code, '07/18/91', 'yes; effective 12/09/99', '16+ in all seats',
                     '$25', 'under 6', '6+', '$25', 'no preference'])
    next(row for row in rows if row[0] == 'NH')[1:3] = ['n/a', 'no law']
    next(row for row in rows if row[0] == 'MA')[1:3] = ['02/01/94', 'no']
    return rows


class ExtraPolicyTrackerTests(unittest.TestCase):
    def test_kff_roster_retains_raw_dates_but_never_certifies_repeals(self):
        history = parse_kff_expansion(table(kff_rows()))
        self.assertEqual(len(history['states']), 51)
        self.assertEqual(len(history['events']), 50)
        self.assertEqual(history['current_nonadopters'], ['AL'])
        maine = next(row for row in history['states'] if row['unit'] == 'ME')
        self.assertEqual(maine['start_date'], '2019-01-10')
        self.assertEqual(maine['date_as_reported'], '1/10/2019')
        self.assertTrue(history['complete_implementation_roster'])
        self.assertFalse(history['complete_change_history'])
        self.assertFalse(history['repeals_included'])
        self.assertFalse(history['s1_certified'])
        self.assertEqual(history['as_of'], '2026-10')
        self.assertIsNone(history['coverage']['end_year'])

    def test_kff_missing_duplicate_and_unknown_status_fail_closed(self):
        variants = [kff_rows()[:-1], kff_rows() + [kff_rows()[-1]]]
        changed = kff_rows()
        changed[1][1] = 'Pending'
        variants.append(changed)
        changed = kff_rows()
        changed[2][2] = '2014'
        variants.append(changed)
        changed = kff_rows()
        changed[2][2] = '1/1/2010'
        variants.append(changed)
        for rows in variants:
            with self.subTest(rows=rows[-1]), self.assertRaises(ValueError):
                parse_kff_expansion(table(rows))

    def test_iihs_snapshot_preserves_coverage_dates_and_remains_parked(self):
        rows = iihs_rows()
        rows[1][2] = 'yes; effective 12/09/99 (secondary rear seats; effective 9/1/19)'
        history = parse_iihs_seatbelts(table(rows))
        al = next(row for row in history['states'] if row['unit'] == 'AL')
        self.assertEqual(al['initial_effective_date'], '1991-07-18')
        self.assertEqual(al['dated_enforcement_mentions'], ['1999-12-09', '2019-09-01'])
        self.assertEqual(al['primary_enforcement_as_reported'], rows[1][2])
        self.assertEqual(history['disposition'], 'PARK')
        self.assertIn('1986 repeal', history['reason'])
        self.assertFalse(history['complete_change_history'])
        self.assertFalse(history['repeals_included'])
        self.assertFalse(history['complete_list'])
        self.assertEqual(history['as_of'], '2026-10')
        self.assertEqual(history['coverage'], {'start_year': None, 'end_year': None})

    def test_iihs_missing_duplicate_and_schema_changes_fail_closed(self):
        for rows in (iihs_rows()[:-1], iihs_rows() + [iihs_rows()[-1]], [['Different schema']]):
            with self.assertRaises(ValueError):
                parse_iihs_seatbelts(table(rows))

    def test_missing_publication_month_is_unknown_never_retrieval_backfilled(self):
        self.assertIsNone(parse_kff_expansion(table(kff_rows(), 'As of date not reported'))['as_of'])
        self.assertIsNone(parse_iihs_seatbelts(table(iihs_rows(), 'As of date not reported'))['as_of'])


if __name__ == '__main__':
    unittest.main()
