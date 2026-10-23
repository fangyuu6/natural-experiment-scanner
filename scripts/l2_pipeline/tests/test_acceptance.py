"""Meaningful blindness/integrity and full inventory acceptance checks."""
from __future__ import annotations
import contextlib, copy, csv, io, json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from scripts.l2_pipeline.cli import preflight
from scripts.l2_pipeline.acceptance import ROOT, main, validate_archival_s1, compare_batch4
from scripts.l2_pipeline.opaque import build_opaque_panel, opaque_rows
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import DesignCard, StageContext
from scripts.l2_pipeline.stages import s0_intake, s4_specs


class OpaqueTests(unittest.TestCase):
    def test_nonnumeric_and_quoted_tails_are_preserved_without_interpretation(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);source=base/'source.csv'
            source.write_bytes(b'unit,year,secret_outcome,extra\r\nAL,2001,DO_NOT_DECODE,"arbitrary,tail"\r\nAK,2001,NaN,inf\r\n')
            mask=ensure_once(base/'mask.json',[['AL',2001]])
            card={'design_id':'fixture','outcome':{'paths':[str(source)],'mask_path':str(mask),'source_sha256':sha(source),'mask_sha256':sha(mask)}}
            report=build_opaque_panel(card,base,base/'output')
            self.assertEqual((base/'output/panel.csv').read_bytes(),b'unit,year,secret_outcome,extra\r\nAL,2001,DO_NOT_DECODE,"arbitrary,tail"\r\n')
            self.assertEqual(report['construction']['outcome_values_parsed'],0)

    def test_binding_mutation_and_duplicate_keys_refuse(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);source=base/'source.csv';source.write_text('unit,year,y\nAL,2001,x\n')
            mask=ensure_once(base/'mask.json',[['AL',2001]])
            card={'design_id':'fixture','outcome':{'paths':[str(source)],'mask_path':str(mask),'source_sha256':sha(source),'mask_sha256':sha(mask)}}
            source.write_text('unit,year,y\nAL,2001,changed\n')
            with self.assertRaisesRegex(ValueError,'binding'): build_opaque_panel(card,base,base/'output')
            self.assertFalse((base/'output').exists())
            source.write_text('unit,year,y\nAL,2001,x\nAL,2001,x\n')
            with self.assertRaisesRegex(ValueError,'Duplicate'): opaque_rows(source)

    def test_unsupported_empty_mask_keeps_header_and_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);source=base/'source.csv';source.write_text('unit,year,y\nAL,2001,opaque\n')
            mask=ensure_once(base/'mask.json',[])
            card={'design_id':'fixture','outcome':{'paths':[str(source)],'mask_path':str(mask),'source_sha256':sha(source),'mask_sha256':sha(mask)}}
            report=build_opaque_panel(card,base,base/'output')
            self.assertEqual(report['cells'],0)
            self.assertEqual((base/'output/panel.csv').read_bytes(),b'unit,year,y\n')


class AcceptanceTests(unittest.TestCase):
    def test_forged_archival_declaration_cannot_create_source_ready_receipt(self):
        declaration=json.loads((ROOT/'scratchpad/l2_batch4/DECLARATIONS.json').read_text())['pay_secrecy']
        declaration['source_ready']=False
        with self.assertRaisesRegex(ValueError,'differs'): validate_archival_s1(ROOT,'pay_secrecy',declaration)

    def test_later_stage_refuses_changed_imported_external_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);source=base/'historical.json';source.write_text('{"source":"original"}')
            design=base/'work';ensure_once(design/'S1_IMPORT_PROVENANCE.json',{'evidence':[{'path':'historical.json','sha256':sha(source)}]})
            ctx=SimpleNamespace(repo=base,design_dir=design)
            preflight(ctx,'S0')
            source.write_text('{"source":"changed"}')
            with self.assertRaisesRegex(ValueError,'evidence changed'): preflight(ctx,'S0')

    def test_every_batch4_variant_is_reproduced_and_frozen_specs_verified_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            spec_hashes={key:sha(ROOT/f'scripts/l2_batch4/specs/{key}_v1.json') for key in ('pay_secrecy','pregnancy_accommodation')}
            with contextlib.redirect_stdout(io.StringIO()):
                code=main(['--cards-dir',str(base/'cards'),'--work-dir',str(base/'work')])
            self.assertEqual(code,0)
            report=json.loads((base/'work/ACCEPTANCE_REPORT.json').read_text())
            self.assertEqual(report['panel_count'],124)
            self.assertEqual(report['unsupported_count'],5)
            self.assertTrue(report['equivalent'])
            expected={'pay_secrecy':(10,30,6),'pregnancy_accommodation':(25,20,9)}
            for case in report['cases']:
                counts=case['primary_counts']
                self.assertEqual((counts['treated'],counts['controls'],counts['cohorts']),expected[case['design']])
                self.assertFalse(case['scientific_diffs'])
                verification=case['archival_spec_verification']
                self.assertTrue(verification['imported_registration_semantically_equal'])
                self.assertTrue(verification['lineage_bound'])
                self.assertTrue(verification['read_only'])
                self.assertFalse(verification['prospective_spec_generated'])
                self.assertFalse(case['prospective_readiness_granted'])
                self.assertEqual(verification['sha256'],spec_hashes[case['design']])
                self.assertTrue(all(r['panel_bytes_equal'] and r['metadata_bytes_equal'] for r in case['panels']))
                self.assertFalse((base/'work'/case['design']/'v1/SPEC.json').exists())
                self.assertFalse((base/'work'/case['design']/'v1/SPEC_DRAFT.json').exists())
                archived_card=load_card(base/'cards'/(case['design']+'.json'))
                self.assertEqual(archived_card.payload['analysis']['interval_path'],['Webb','CT_complete_control_path','point_plus_RI_if_uncertified'])
                proof=json.loads((base/'work'/case['design']/'v1/S1_IMPORT_PROVENANCE.json').read_text())
                self.assertTrue(proof['locked_originals'])
                self.assertEqual(proof['known_errors'],0)
                self.assertEqual(proof['unresolved'],3)
                # Successful historical reproduction does not waive either
                # prospective gate or fabricate first-order metadata.
                context=StageContext(archived_card,base/'work',ROOT)
                for stage in (s0_intake,s4_specs):
                    result=stage.run(context)
                    self.assertEqual(result.disposition.value,'parked')
                    self.assertEqual(result.reason,'first-order outcome registration incomplete')
                self.assertFalse((context.design_dir/'SPEC.json').exists())
                self.assertEqual(sha(ROOT/verification['path']),spec_hashes[case['design']])
                changed=copy.deepcopy(dict(archived_card.payload))
                changed['analysis']['registration']['estimand']='altered imported estimand'
                changed_context=StageContext(DesignCard.from_mapping(changed,archived_card.path),base/'work',ROOT)
                changed_report=compare_batch4(changed_context)
                self.assertFalse(changed_report['equivalent'])
                self.assertFalse(changed_report['archival_spec_verification']['imported_registration_semantically_equal'])
                self.assertTrue(changed_report['archival_spec_verification']['lineage_bound'])
            self.assertEqual(report['outcome_values_parsed'],0)
            self.assertEqual(report['agents_dispatched'],0)
            self.assertEqual(report['new_downloads'],[])
            self.assertFalse(report['prospective_readiness_granted'])
            self.assertEqual(report['prospective_specs_generated'],0)
            self.assertTrue(report['stage_stop'].startswith('S3 archival'))

if __name__=='__main__': unittest.main()
