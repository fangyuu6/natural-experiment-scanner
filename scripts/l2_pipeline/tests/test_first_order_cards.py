"""Guard the no-peek boundary and fresh registration reset."""
import json
import tempfile
import unittest
from pathlib import Path
from scripts.l2_pipeline.first_order_cards import _read, OUT, TARGETS
from scripts.l2_pipeline.cards import validate_card

class FirstOrderReintakeTests(unittest.TestCase):
    def test_result_files_rejected_even_in_card_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'run2'; root.mkdir()
            result=root/'RESULT.json'
            # Invalid JSON establishes rejection happens before parsing bytes.
            result.write_text('not permitted data')
            with self.assertRaisesRegex(ValueError,'metadata whitelist'):
                _read(result)

    def test_new_cards_do_not_inherit_execution_material(self):
        forbidden={'mask_path','mask_sha256','support_metadata_path','support_metadata_sha256',
                   'interval_path','registration','full_year_declaration','url_by_path','source_sha256'}
        for old_id in TARGETS:
            with self.subTest(design=old_id):
                card=json.loads((OUT/(old_id+'_fo.json')).read_text())
                self.assertEqual([],validate_card(card))
                self.assertEqual('v1',card['version'])
                self.assertEqual([],card['outcome']['paths'])
                self.assertEqual([],card['sensitivities'])
                self.assertEqual([],card['first_order']['secondary_outcomes'])
                for field in ('outcome','analysis','treatment'):
                    self.assertFalse(forbidden & card[field].keys())
                lineage=card['automation']['first_order_reintake']
                self.assertFalse(lineage['prior_result_values_read'])
                self.assertTrue(lineage['fresh_forecasts_required'])
                self.assertTrue(lineage['fresh_freeze_required'])
                self.assertEqual(['S0','S1','S2','S3','S4','S5','S6','S7'],lineage['required_stages'])

if __name__=='__main__': unittest.main()
