"""F1 purpose-direction fix (paraphrased locator voided valid codings) and N12 date fields; invented, outcome-free fixtures."""
import copy, unittest
from pathlib import Path
from scripts.l2_pipeline.agents.purpose import validate_purpose_direction, C64_SCHEMA
from scripts.l2_pipeline.intake.official_history import date_fields, engine_records

TEMPLATE = Path(__file__).parents[1] / 'agents/templates/v6/purpose_direction_c83.md'
URL = 'https://www.law.cornell.edu/uscode/text/42/1397jj'
PACKET = [dict(source_ref=URL, locator='Subsection (b)(1) defines a targeted low-income child', source_type='statute', supports=['target_outcome'])]


def coding(role, locator, source_ref=URL, sign=1, n=0):
    return dict(role=role, coder_id='coder-%s' % role, recorded_at='2026-10-17T07:36:00+00:00', purpose_direction=sign,
        original_sha256=('%x' % (n + 1)) * 64 if n < 15 else 'a' * 64,
        citations=[dict(source_ref=source_ref, locator=locator, intended_change='raise coverage', evidence_kind='statute_purpose')],
        blindness_attestation=dict(outcome_values_read=False, outcome_associations_read=False, forecasts_read=False,
            literature_effect_signs_read=False, other_codings_read=False))


def value(codings):
    return dict(target_outcome='insurance_coverage', purpose_direction=1, purpose_direction_evidence=dict(
        schema=C64_SCHEMA, coded_at_stage='S0', target_outcome='insurance_coverage', status='verified', codings=codings))


class Direction(unittest.TestCase):
    def test_exact_packet_citation_is_accepted(self):
        v = value([coding('A', PACKET[0]['locator'], n=1), coding('B', PACKET[0]['locator'], n=2)])
        self.assertEqual(validate_purpose_direction(v, PACKET)['status'], 'verified')

    def test_paraphrased_locator_stays_rejected_by_the_validator(self):
        v = value([coding('A', 'my own words', n=1), coding('B', 'my own words', n=2)])
        with self.assertRaisesRegex(ValueError, 'must match an S0 target citation'):
            validate_purpose_direction(v, PACKET)

    def test_template_tells_coders_to_copy_the_locator(self):
        text = TEMPLATE.read_text()
        self.assertIn('character for character', text)
        self.assertIn('`locator`', text)


class Dates(unittest.TestCase):
    def test_copies_iso_dates_only(self):
        r = date_fields(dict(enactment_date='2022-05-10', operational_implementation_date='2023-01-01', operational_date_precision='day'))
        self.assertEqual(r, dict(enactment_date='2022-05-10', effective_date='2023-01-01', date_precision='day'))

    def test_year_only_or_narrative_stays_null(self):
        r = date_fields(dict(enactment_date='2022', operational_implementation_date='January 2023', operational_date_precision='month'))
        self.assertEqual(r, dict(enactment_date=None, effective_date=None, date_precision=None))
        self.assertEqual(date_fields({}), dict(enactment_date=None, effective_date=None, date_precision=None))

    def test_engine_records_carry_the_fields_and_keep_the_clock(self):
        rows = [dict(unit='AL', status='treated', start_period=2024, best_evidence_start_year=2023, enactment_date='2022-05-10',
                     operational_implementation_date='2023-01-01', operational_date_precision='day'),
                dict(unit='AK', status='never_treated', start_period=None)]
        out = engine_records(copy.deepcopy(rows), set())
        self.assertEqual((out[0]['start_period'], out[0]['effective_date'], out[0]['date_precision'], out[0]['enactment_date']),
                         (2023, '2023-01-01', 'day', '2022-05-10'))
        self.assertNotIn('effective_date', out[1])


if __name__ == '__main__':
    unittest.main()
