"""C64 direction tests use invented metadata only, never outcomes."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from scripts.l2_pipeline.agents import collect_job, accept_purpose_verification
from scripts.l2_pipeline.agents.purpose import (C64_SCHEMA, emit_purpose_verification,
    validate_purpose_direction, validate_purpose_output)
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.storage import sha, write_once
from scripts.l2_pipeline.tests.test_pipeline import card
REPO = Path(__file__).resolve().parents[3]


def direction(sign=1):
    records = []
    for role, token in [('A', 'a'), ('B', 'b')]:
        records.append(dict(role=role, coder_id='coder_' + role, original_sha256=token * 64,
            recorded_at='2026-10-11T00:00:00Z', purpose_direction=sign,
            blindness_attestation={key: False for key in ['outcome_values_read',
                'outcome_associations_read', 'forecasts_read', 'literature_effect_signs_read', 'other_codings_read']},
            citations=[dict(source_ref='https://legislature.test/statute', locator='Section 1',
                evidence_kind='statute_purpose', intended_change='Raise coverage among eligible residents')]))
    return dict(target_outcome='health_insurance_coverage', purpose_direction=sign,
        purpose_direction_evidence=dict(schema=C64_SCHEMA, coded_at_stage='S0',
            target_outcome='health_insurance_coverage', status='verified', codings=records))


class C64PurposeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        value = card()
        value.update(l3_export_v2={'schema': C64_SCHEMA}, automation={'intake': {'domain': 'Health'}})
        value['first_order'].update(target_outcome='health_insurance_coverage')
        write_once(self.root / 'card.json', value)
        self.ctx = StageContext(load_card(self.root / 'card.json'), self.root / 'work', REPO)
        self.job = emit_purpose_verification(self.ctx, self.root / 'jobs')

    def tearDown(self): self.tmp.cleanup()

    def output(self):
        return dict(direction(), design_id=self.job.design_id, role='purpose_verifier',
            card_sha256=self.job.contract['card_sha256'], internal_persona_lens='Finkelstein',
            status='VERIFIED', target_population='Eligible residents', confirmation='Target and population [1]',
            citations=[dict(source_type='statute', source_ref='https://legislature.test/statute',
                title='Invented statute', locator='Section 1', supports=['target_outcome', 'target_population'])])

    def test_new_template_and_byte_identical_reemission(self):
        self.assertTrue(self.job.template_path.endswith('purpose_verifier_c86.md'))  # c64 text with the exact `supports` instruction (window 2026-10-17)
        self.assertEqual(self.job.contract['purpose_direction_rule'], C64_SCHEMA)
        before = {p: sha(p) for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.job, emit_purpose_verification(self.ctx, self.root / 'jobs'))
        self.assertEqual(before, {p: sha(p) for p in before})

    def test_every_defined_direction_requires_real_blind_records(self):
        for sign in (-1, 0, 1):
            self.assertEqual(validate_purpose_direction(direction(sign))['status'], 'verified')
        for sign in (True, False, -2, 2, '1', 1.0, None):
            bad = direction(); bad['purpose_direction'] = sign
            with self.assertRaises(ValueError): validate_purpose_direction(bad)

    def test_blindness_and_unique_located_provenance(self):
        mutations = [lambda e: e['codings'].pop(),
            lambda e: e['codings'][1].update(coder_id='coder_A'),
            lambda e: e['codings'][1].update(original_sha256='a' * 64),
            lambda e: e['codings'][0].update(recorded_at='2026-10-11T00:00:00'),
            lambda e: e['codings'][0]['citations'][0].update(locator='Other passage'),
            lambda e: e['codings'][0]['citations'][0].update(evidence_kind='literature_effect_sign'),
            lambda e: e.update(target_outcome='different_target')]
        for field in ('outcome_values_read', 'outcome_associations_read', 'forecasts_read',
                      'literature_effect_signs_read', 'other_codings_read'):
            mutations.append(lambda e, field=field: e['codings'][0]['blindness_attestation'].update({field: True}))
        for mutation in mutations:
            value = self.output(); mutation(value['purpose_direction_evidence'])
            with self.assertRaises(ValueError): validate_purpose_output(self.job, value)

    def test_disagreement_needs_distinct_third_and_reconciled_sign(self):
        value = direction(); records = value['purpose_direction_evidence']['codings']
        records[1]['purpose_direction'] = -1
        with self.assertRaisesRegex(ValueError, 'third'): validate_purpose_direction(value)
        third = copy.deepcopy(records[1]); third.update(role='third', coder_id='coder_C', original_sha256='c' * 64)
        third['blindness_attestation']['other_codings_read'] = True
        records.append(third)
        with self.assertRaisesRegex(ValueError, 'reconciliation'): validate_purpose_direction(value)
        value['purpose_direction'] = -1; validate_purpose_direction(value)
        records[0]['purpose_direction'] = -1
        with self.assertRaisesRegex(ValueError, 'third'): validate_purpose_direction(value)

    def test_pending_blind_coding_is_null_and_never_zero(self):
        value = self.output(); value['purpose_direction'] = None
        value['purpose_direction_evidence'].update(status='pending_blind_coding', coverage_code='FIELD_MISSING',
            reason='Independent B coding has not been collected')
        value['purpose_direction_evidence'].pop('codings'); validate_purpose_output(self.job, value)
        value['purpose_direction'] = 0
        with self.assertRaisesRegex(ValueError, 'Pending'): validate_purpose_output(self.job, value)

    def test_collector_and_hub_carry_direction_without_touching_originals(self):
        out = Path(self.job.output_path); out.parent.mkdir(parents=True)
        out.write_text(json.dumps(self.output()))
        before = sha(out); collect_job(self.job.job_path)
        ruling = accept_purpose_verification(self.ctx, 'accept', 'Purpose evidence reviewed')
        first = ruling['card_patch']['first_order']
        self.assertEqual(first['purpose_direction'], 1)
        self.assertEqual(first['purpose_direction_evidence'], direction()['purpose_direction_evidence'])
        self.assertEqual(before, sha(out))
        with self.assertRaisesRegex(ValueError, 'fresh blind direction'):
            accept_purpose_verification(self.ctx, 'accept', 'Changed target', verified_target='other_target',
                verified_population='Eligible residents', verified_citations=self.output()['citations'])

    def test_legacy_collection_contract_remains_compatible(self):
        from dataclasses import replace
        contract = dict(self.job.contract); contract.pop('purpose_direction_rule')
        legacy = replace(self.job, contract=contract)
        value = self.output(); value.pop('purpose_direction'); value.pop('purpose_direction_evidence')
        result = validate_purpose_output(legacy, value); self.assertNotIn('purpose_direction', result)


if __name__ == '__main__': unittest.main()
