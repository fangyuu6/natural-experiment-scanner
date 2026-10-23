"""Invented inputs only: relocation, full loading and pre-signature refusals."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from scripts.l2_pipeline import forecast_snapshot as forecasts, dry_load
from scripts.l2_pipeline.storage import sha
from scripts.l2_pipeline.engine.panels import PanelBinding


class DryLoadTests(unittest.TestCase):
    def forecast(self, root):
        directory=root/'predictions/Card';directory.mkdir(parents=True)
        source=directory/'attempts/old/Card.filled.json';source.parent.mkdir(parents=True)
        source.write_text('{"point": -0.1}')
        collection=dict(original_path='/relocated/old/predictions/Card/attempts/old/Card.filled.json',
                        original_sha256=sha(source))
        cp=directory/'COLLECTION.json';cp.write_text(json.dumps(collection))
        lock=dict(original_files={str(p.relative_to(root)):sha(p) for p in (source,cp)})
        return source, collection, lock

    def test_relocated_original_uses_only_root_objects_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,collection,lock=self.forecast(root)
            record=forecasts.create(root,lock)
            self.assertEqual(forecasts.original(root,lock,'Card',collection,record),dict(point=-.1))
            with self.assertRaisesRegex(ValueError,'escapes execution root'):
                forecasts.original(root,lock,'Card',collection)
            target=root/record['base']/'objects'/sha(source)
            target.write_text('{"point": 10}')
            with self.assertRaisesRegex(ValueError,'Tampered forecast'):
                forecasts.original(root,lock,'Card',collection,record)

    def test_unlocked_ambiguous_hash_or_extra_object_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,collection,lock=self.forecast(root)
            record=forecasts.create(root,lock)
            bad=dict(collection,original_sha256='0'*64)
            with self.assertRaisesRegex(ValueError,'identity ambiguous/missing'):
                forecasts.original(root,lock,'Card',bad,record)
            (root/record['base']/'objects/unbound').write_text('extra')
            with self.assertRaisesRegex(ValueError,'Unbound forecast'):
                forecasts.validate(root,record,lock)

    def test_full_value_loader_all_supported_variants_no_fits_or_values_in_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'card.json').write_text('{"design_id":"invented","analysis":{"controls":"never_treated"}}')
            (root/'SPEC.json').write_text('{"design_id":"invented","analysis":{"controls":"never_treated"}}')
            (root/'SUPPORT.json').write_text('{"variants":[{"variant":"primary","comparison_group":"never_treated"}]}')
            panel=root/'panel.csv';panel.write_text('unit,year,outcome_value\nA,2000,12345.6789\n')
            meta=root/'metadata.csv';meta.write_text('unit,year,available\nA,2000,1\n')
            binding=PanelBinding(panel,panel,meta,sha(panel),sha(panel),sha(meta))
            bundle=dict(expected=['primary','secondary_good','secondary_unsupported','omit_A'],
                support={n:dict(passed=n!='secondary_unsupported') for n in
                         ['primary','secondary_good','secondary_unsupported','omit_A']},
                bindings={n:binding for n in ['primary','secondary_good','omit_A']})
            files={n:dict(role='card' if n=='card.json' else 'verification',sha256=sha(root/n)) for n in ['card.json','SPEC.json','SUPPORT.json']}
            (root/'FREEZE_FINAL.json').write_text(json.dumps(dict(files=files,
                code_snapshot=dict(digest='a'*64),dry_load_inventory=[dict(variant=n,
                    panel_sha256=sha(panel),metadata_sha256=sha(meta)) for n in ['omit_A','primary','secondary_good']])))
            with patch('scripts.l2_pipeline.registered.load_registration',return_value=bundle) as registration, \
                 patch('scripts.l2_pipeline.registered.run_registered',side_effect=AssertionError('Fit reached')), \
                 patch('scripts.l2_pipeline.engine.runner.fit_primary',side_effect=AssertionError('Fit reached')):
                value=dry_load.inputs(root)
            registration.assert_called_once()
            self.assertEqual([r['variant'] for r in value['loaded']],['omit_A','primary','secondary_good'])
            self.assertNotIn('12345',json.dumps(value))
            self.assertEqual(value['actual_data_estimation_calls'],0)
            self.assertFalse((root/'EXECUTION_ONCE.json').exists())
            panel.write_text('unit,year,outcome_value\nA,2000,DO_NOT_DECODE\n')
            bad=PanelBinding(panel,panel,meta,sha(panel),sha(panel),sha(meta))
            bundle['bindings']['primary']=bad
            with patch('scripts.l2_pipeline.registered.load_registration',return_value=bundle), self.assertRaises(ValueError):
                dry_load.inputs(root)
            self.assertFalse((root/'DRY_LOAD.json').exists())

    def test_missing_stale_truthy_or_tampered_pass_refuses_final_signature(self):
        from scripts.l2_pipeline.registration import approve
        from scripts.l2_pipeline.tests.test_pipeline import FinalApprovalTests
        from scripts.l2_pipeline.freeze import verify
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);FinalApprovalTests().make_bundle(root)
            receipt=root/'DRY_LOAD.json';original=receipt.read_bytes()
            manifest=json.loads((root/'FREEZE_FINAL.json').read_text())
            (root/'APPROVAL_FINAL.json').unlink()
            ctx=SimpleNamespace(design_dir=root,card=SimpleNamespace(design_id='invented',
                payload=dict(automation=dict(hub_signer='fixture_hub'))))
            args=SimpleNamespace(signer='fixture_hub',signed_at='2026-10-07T00:00:00Z')
            receipt.unlink()
            with self.assertRaisesRegex(ValueError,'DRY-LOAD PASS'):
                approve(ctx,args,'final')
            self.assertFalse((root/'APPROVAL_FINAL.json').exists())
            for field,value in [('candidate_sha256','b'*64),('passed','true'),
                                ('code_snapshot_digest','b'*64),('actual_data_estimation_calls',False)]:
                changed=json.loads(original);changed[field]=value;receipt.write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError,'DRY-LOAD PASS'):
                    dry_load.require_pass(root,sha(root/'FREEZE_FINAL.json'),'invented')
            receipt.write_bytes(original)
            approve(ctx,args,'final')
            self.assertTrue(verify(root,'final','fixture_hub')['ok'])
            changed=json.loads(original);changed['loaded']=[dict(variant='changed')]
            receipt.write_text(json.dumps(changed))
            self.assertFalse(verify(root,'final','fixture_hub')['ok'])
            self.assertFalse((root/'EXECUTION_ONCE.json').exists())


if __name__=='__main__': unittest.main()
