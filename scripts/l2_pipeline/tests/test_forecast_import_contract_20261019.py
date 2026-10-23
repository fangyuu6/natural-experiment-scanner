"""B-group rebind (hub approval 2026-10-19): when FORECAST_IMPORT.json exists (forecasts rebound unchanged to a rebuilt design), the saved and
the registered forecast contract card are compared with `version` removed, exactly the field c57.science_equal ignores; nothing else is relaxed.
Invented inputs only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline.forecast_slots import validate_contract
from scripts.l2_pipeline.storage import ensure_once,sha

IMPORT_SCHEMA='l2_unchanged_forecast_import_v1'


def contract_card(version):
    return dict(version=version,design_id='invented',analysis=dict(clock='annual',controls='not_yet_treated'),support=dict(variants=['primary']))


class ForecastImportContractCard(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve()
        self.saved=contract_card('v2')                      # the card the forecasters saw (old attempt)
        meta=dict(contract=dict(card=self.saved))
        job=self.root/'job.md'
        job.write_text('## Execution metadata binding (data)\n```json\n'+json.dumps(meta)+'\n```\n')
        ensure_once(job.with_suffix('.job.json'),meta)
        ensure_once(self.root/'predictions/Card/COLLECTION.json',dict(job_path=str(job),job_sha256=sha(job)))
        self.spec=ensure_once(self.root/'SPEC.json',dict(invented=True))

    def check(self,registered):
        with patch('scripts.l2_pipeline.contract_card.registered_contract_card',return_value=registered):
            validate_contract(self.root,['Card'])

    def proof(self,**changes):
        value=dict(schema=IMPORT_SCHEMA,primary_target_unchanged=True,target_spec_sha256=sha(self.spec),personas=['Card'])
        return ensure_once(self.root/'FORECAST_IMPORT.json',dict(value,**changes))

    def test_version_only_difference_passes_with_a_valid_import_proof(self):
        self.proof();self.check(contract_card('v3'))

    def test_without_an_import_proof_the_version_difference_still_fails(self):
        with self.assertRaisesRegex(ValueError,'differs from registered'):self.check(contract_card('v3'))

    def test_any_other_difference_still_fails_even_with_a_proof(self):
        self.proof()
        other=contract_card('v3');other['analysis']['clock']='changed'
        with self.assertRaisesRegex(ValueError,'differs from registered'):self.check(other)

    def test_proof_for_another_spec_or_schema_does_not_relax(self):
        proof=self.proof(target_spec_sha256='0'*64)
        with self.assertRaisesRegex(ValueError,'differs from registered'):self.check(contract_card('v3'))
        proof.write_text(json.dumps(dict(json.loads(proof.read_text()),target_spec_sha256=sha(self.spec),schema='other')))
        with self.assertRaisesRegex(ValueError,'differs from registered'):self.check(contract_card('v3'))

    def test_identical_cards_pass_with_or_without_a_proof(self):
        self.check(copy.deepcopy(self.saved));self.proof();self.check(copy.deepcopy(self.saved))
