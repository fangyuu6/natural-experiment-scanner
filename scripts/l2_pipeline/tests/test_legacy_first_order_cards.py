"""Invented status/spec fixtures only; no historical outcome fits or forecasts."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline import legacy_first_order_cards as legacy
from scripts.l2_pipeline.cards import validate_card
from scripts.l2_pipeline.first_order import first_order_gate
from scripts.l2_pipeline.storage import sha


def write(root: Path, relative: str, value):
    path=root/relative;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(value if isinstance(value,str) else json.dumps(value))


def fixture(root: Path):
    batches={'batch1':['pfml','eitc_first_refundable'],
             'batch2':['automatic_voter','btb_private','medical_cannabis','film_refundable'],
             'batch3':['public_btb','mw_indexing']}
    for batch,keys in batches.items():
        write(root,legacy.SUMMARIES[batch],{'status':'COMPLETED','run_id':'invented_'+batch,
              'case_order':keys,'case_statuses':{key:'PUBLISHABLE_EXAMPLE' for key in keys}})
    for index,(key,path) in enumerate(legacy.SPECS.items()):
        identifier='did-'+str(index).zfill(20)
        write(root,path,('Design `'+identifier+'`; invented registered outcome metadata.') if path.endswith('.md') else
              {'design_id':identifier,'outcome_id':key+'_invented_registered_outcome','specification':{'estimand':'invented registered target'}})
    write(root,legacy.LEGAL['batch2'],{'verified_representatives':[{'key':key,'source_url':'https://example.invalid/'+key,
          'locator':'invented statute purpose section','scope':'invented legal scope','legal_citation':'invented citation '+key}
          for key in ('automatic_voter','btb_private','medical_cannabis')]})
    for key in ('public_btb','mw_indexing'):
        write(root,legacy.LEGAL[key],{'verified':True,'url':'https://example.invalid/'+key,
              'locator':'invented section','scope':'invented purpose','citation':'invented citation '+key})


def author():
    return {'author_id':'invented_clean_test_author','legacy_result_values_read':False,
            'legacy_forecast_values_or_direction_labels_read':False,
            'target_mappings_verified_against_user_task':True,'cards_independently_authored':True}


def cards(root):
    # Synthetic helper drafts are permitted as test fixtures. Real generation
    # receives cards independently authored by the separate clean agent.
    return {row['case_key']:legacy.draft_card(root,row) for row in legacy.completion_projection(root)['cases']
            if row['replacement_required']}


class LegacyFirstOrderTests(unittest.TestCase):
    def test_status_only_projection_covers_all_eight_and_keeps_film(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root);projection=legacy.completion_projection(root)
            self.assertEqual(projection['completed_cases'],8)
            self.assertEqual(projection['replacement_cards_required'],7)
            self.assertTrue(projection['status_only'])
            retained=[r for r in projection['cases'] if not r['replacement_required']]
            self.assertEqual([r['case_key'] for r in retained],['film_refundable'])
            self.assertEqual(projection['per_case_result_files_read'],0)
            self.assertEqual(projection['forecast_ledgers_read'],0)
            self.assertIn('absence of TERMINAL.json',projection['scope_resolution'])

    def test_results_forecasts_and_arbitrary_metadata_cannot_be_inputs(self):
        with patch.object(Path,'read_text',side_effect=AssertionError('forbidden file opened')):
            for path in ('scripts/l2_batch1/results/run/pfml.json','scripts/l2_batch3/specs/predictions/public_btb/Card.filled.json',
                         'docs/plans/l2-scale/BATCH2_RESULTS.md','arbitrary/CARD.json'):
                with self.assertRaisesRegex(ValueError,'whitelist'):
                    legacy.read_metadata(Path('/private/tmp/invented'),path)

    def test_summary_value_payload_or_nested_status_is_refused(self):
        for patch_value in ({'point_estimate':'must not project'}, {'case_statuses':{'pfml':{'status':'PUBLISHABLE_EXAMPLE'}}}):
            with tempfile.TemporaryDirectory() as td:
                root=Path(td);fixture(root)
                path=root/legacy.SUMMARIES['batch1'];value=json.loads(path.read_text());value.update(patch_value)
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError): legacy.completion_projection(root)

    def test_draft_shapes_are_new_valid_and_unavailable_without_legacy_spec_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root)
            for key,card in cards(root).items():
                self.assertEqual(validate_card(card),[])
                self.assertTrue(card['design_id'].endswith('_fo'))
                self.assertEqual(card['version'],'v1')
                self.assertEqual(first_order_gate(card)['reason'],'first-order outcome unavailable')
                self.assertEqual(card['outcome']['paths'],[])
                self.assertEqual(card['sensitivities'],[])
                self.assertNotIn('registration',card['analysis'])
                self.assertNotIn('registration_identity',card['automation'])
                self.assertEqual(card['automation']['first_order_reintake']['required_stages'],['S0','S1','S2','S3','S4','S5','S6','S7'])

    def test_clean_authorship_is_required_before_any_real_generation(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root)
            contaminated=dict(author(),legacy_forecast_values_or_direction_labels_read=True)
            with self.assertRaisesRegex(ValueError,'fresh clean author'):
                legacy.build(root,clean_author=contaminated,authored_cards=cards(root))
            self.assertFalse((root/legacy.CARD_ROOT).exists())
            with self.assertRaisesRegex(ValueError,'seven'):
                legacy.build(root,clean_author=author(),authored_cards={})

    def test_independent_synthetic_generation_parks_seven_and_preserves_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root)
            hashes={path:sha(root/path) for path in legacy.WHITELIST}
            authored=cards(root)
            authored['mw_indexing']['first_order'].update(target_outcome='real_wages_earnings',primary_outcome='real_wages_earnings',relation='direct')
            authored['mw_indexing']['outcome']['measure_id']='real_wages_earnings'
            authored['mw_indexing']['analysis']['source_and_data_gaps']='Independent author population and deflator gap.'
            report=legacy.build(root,clean_author=author(),authored_cards=authored)
            self.assertEqual(report['new_cards'],7)
            self.assertEqual(report['aligned_originals_retained'],1)
            self.assertEqual(report['actual_data_estimation_calls'],0)
            for record in report['cards']:
                self.assertEqual(record['s0']['disposition'],'parked')
                self.assertEqual(record['s0']['reason'],'first-order outcome unavailable')
                card=json.loads((root/record['path']).read_text())
                disclosure=card['automation']['first_order_reintake']
                self.assertTrue(disclosure['prior_registration_ran'])
                self.assertFalse(disclosure['prior_result_values_read'])
                self.assertEqual(disclosure['generation_author'],'invented_clean_test_author')
            self.assertEqual(hashes,{path:sha(root/path) for path in legacy.WHITELIST})
            self.assertTrue((root/legacy.META_ROOT/'LEGACY_COMPLETION_PROJECTION.json').is_file())
            self.assertTrue((root/legacy.META_ROOT/'LEGACY_FO_REPORT.md').is_file())
            report_text=(root/legacy.META_ROOT/'LEGACY_FO_REPORT.md').read_text()
            self.assertIn('real_wages_earnings',report_text)
            self.assertIn('Independent author population and deflator gap.',report_text)
            self.assertFalse(any((root/legacy.WORK_ROOT).glob('*/v1/SPEC.json')))

    def test_inherited_support_registration_or_forecasts_refused(self):
        for owner,field in (('analysis','registration'),('automation','prediction_templates'),('treatment','full_year_declaration')):
            with tempfile.TemporaryDirectory() as td:
                root=Path(td);fixture(root);authored=cards(root)
                authored['pfml'][owner][field]={'invented':'legacy bytes'}
                with self.assertRaisesRegex(ValueError,'No inherited'):
                    legacy.build(root,clean_author=author(),authored_cards=authored)


if __name__=='__main__': unittest.main()
