"""T284: forecasts rebound unchanged to a rebuilt design whose SPEC lost ONLY the forward-rule variant (start_minus_one, HUB_RULING_20261022_DRAFT_STOP Q7,
rebind-ui-20261023): the saved contract card may carry that one SUPPORT row; nothing else is relaxed. Invented inputs only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline.forecast_slots import validate_contract,FORWARD_RULE_DROPPED
from scripts.l2_pipeline.storage import ensure_once,sha

IMPORT_SCHEMA='l2_unchanged_forecast_import_v1'


def card(version,variants):
    return dict(version=version,design_id='invented',analysis=dict(clock='annual'),support=dict(variants=[dict(variant=v,passed=True) for v in variants]))


class RemovedRow(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve();self.src=self.root/'old';self.new=self.root/'new'
        self.saved=card('v4',['primary',FORWARD_RULE_DROPPED,'other'])           # card the forecasters saw
        meta=dict(contract=dict(card=self.saved))
        job=self.new/'job.md';job.parent.mkdir(parents=True)
        job.write_text('## Execution metadata binding (data)\n```json\n'+json.dumps(meta)+'\n```\n')
        ensure_once(job.with_suffix('.job.json'),meta)
        ensure_once(self.new/'predictions/Card/COLLECTION.json',dict(job_path=str(job),job_sha256=sha(job)))
        self.old_spec=ensure_once(self.src/'SPEC.json',dict(support=dict(variants=[dict(variant=v) for v in ['primary',FORWARD_RULE_DROPPED,'other']])))
        self.set_new_spec(['primary','other'])

    def set_new_spec(self,names):
        path=self.new/'SPEC.json'
        path.write_text(json.dumps(dict(support=dict(variants=[dict(variant=v) for v in names]))))
        self.spec=path

    def proof(self,**changes):
        value=dict(schema=IMPORT_SCHEMA,primary_target_unchanged=True,target_spec_sha256=sha(self.spec),personas=['Card'],
                   source_spec_path=str(self.old_spec),source_spec_sha256=sha(self.old_spec))
        path=self.new/'FORECAST_IMPORT.json'
        path.write_text(json.dumps(dict(value,**changes)))

    def check(self,registered):
        with patch('scripts.l2_pipeline.contract_card.registered_contract_card',return_value=registered):
            validate_contract(self.new,['Card'])

    def test_forward_rule_row_removed_passes(self):
        self.proof();self.check(card('v5',['primary','other']))

    def test_without_proof_still_fails(self):
        with self.assertRaisesRegex(ValueError,'differs from registered'):self.check(card('v5',['primary','other']))

    def test_another_row_missing_fails(self):
        self.set_new_spec(['primary']);self.proof()
        with self.assertRaisesRegex(ValueError,'differs from registered'):self.check(card('v5',['primary']))

    def test_forward_rule_row_plus_another_missing_fails(self):
        self.set_new_spec(['primary']);self.proof()
        with self.assertRaisesRegex(ValueError,'differs from registered'):self.check(card('v5',['primary']))

    def test_other_difference_still_fails(self):
        self.proof();other=card('v5',['primary','other']);other['analysis']['clock']='changed'
        with self.assertRaisesRegex(ValueError,'differs from registered'):self.check(other)

    def test_row_content_change_in_a_kept_row_fails(self):
        self.proof();other=card('v5',['primary','other']);other['support']['variants'][0]['passed']=False
        with self.assertRaisesRegex(ValueError,'differs from registered'):self.check(other)

    def test_source_spec_hash_mismatch_does_not_relax(self):
        self.proof(source_spec_sha256='0'*64)
        with self.assertRaisesRegex(ValueError,'differs from registered'):self.check(card('v5',['primary','other']))

    def test_source_spec_missing_does_not_relax(self):
        self.proof(source_spec_path=str(self.root/'nope.json'))
        with self.assertRaisesRegex(ValueError,'differs from registered'):self.check(card('v5',['primary','other']))

    def test_row_name_other_than_forward_rule_not_relaxed(self):
        self.set_new_spec(['primary',FORWARD_RULE_DROPPED]);self.proof()
        with self.assertRaisesRegex(ValueError,'differs from registered'):self.check(card('v5',['primary',FORWARD_RULE_DROPPED]))

    def test_unchanged_spec_identical_cards_still_pass(self):
        self.set_new_spec(['primary',FORWARD_RULE_DROPPED,'other']);self.proof()
        self.check(card('v5',['primary',FORWARD_RULE_DROPPED,'other']))


if __name__=='__main__':
    unittest.main()
