"""Migrated metadata adapters preserve clocks, evidence and isolated failures."""
import ast
import contextlib
import hashlib
import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.run6_adapters import adapter
from scripts.l2_pipeline.run6_adapters import intake_selection
from scripts.l2_pipeline.run6_adapters import verify as verifier
from scripts.l2_pipeline.run6_adapters.paths import REPO_ROOT, require
from scripts.l2_pipeline.run6_adapters.cdc import parse_cdc
from scripts.l2_pipeline.run6_adapters.cms import adapter as cms
from scripts.l2_pipeline.run6_adapters.pdaps import build_pdaps as pdaps
from scripts.l2_pipeline.run6_adapters.social import build_social as social


class Run6AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_archived_evidence_bytes_are_unchanged(self):
        manifest = json.loads((REPO_ROOT/'scripts/l2_pipeline/run6_adapters/MIGRATION.json').read_text())
        self.assertEqual(len(manifest['adapters']), 8)
        for entry in manifest['adapters']:
            with self.subTest(adapter=entry['active_path']):
                archived = REPO_ROOT / entry['archived_path']
                if archived.exists():
                    raw = archived.read_bytes()
                    self.assertEqual(len(raw), entry['archived_size_bytes'])
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), entry['archived_sha256'])
                self.assertTrue((REPO_ROOT/entry['active_path']).is_file())

    def test_importing_adapters_reads_no_archived_source_or_writes_files(self):
        modules = ('adapter', 'verify', 'intake_selection', 'cdc.fetch_cdc',
                   'cdc.parse_cdc', 'cms.adapter', 'pdaps.build_pdaps', 'social.build_social')
        for name in modules:
            module = importlib.import_module('scripts.l2_pipeline.run6_adapters.'+name)
            with self.subTest(module=name), patch.object(Path, 'read_text', side_effect=RuntimeError('read on import')), \
                    patch.object(Path, 'read_bytes', side_effect=RuntimeError('read on import')), \
                    patch.object(Path, 'write_text', side_effect=RuntimeError('write on import')):
                importlib.reload(module)

    def test_explicit_validation_remains_active_under_optimization(self):
        with self.assertRaisesRegex(ValueError, 'required provenance'):
            require(False, 'required provenance')
        for path in (REPO_ROOT/'scripts/l2_pipeline/run6_adapters').rglob('*.py'):
            self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(ast.parse(path.read_text()))), str(path))

    def test_freeze_inventory_binds_moved_code_and_its_dependencies(self):
        from scripts.l2_pipeline.freeze import code_inventory
        inventory = code_inventory()
        for relative in ('scripts/l2_pipeline/run6_adapters/adapter.py',
                         'scripts/l2_pipeline/run6_adapters/paths.py',
                         'scripts/l2_pipeline/run6_adapters/pdaps/build_pdaps.py',
                         'scripts/l2_pipeline/policy_tracker_sources.py',
                         'scripts/l2_pipeline/policy_tracker_intake.py'):
            path = REPO_ROOT / relative
            self.assertEqual(inventory[str(path.resolve())], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertFalse(any(Path(path).suffix == '.py' and Path(path).is_relative_to(REPO_ROOT/'data')
                             for path in inventory))

    def test_earliest_activation_cannot_be_replaced_by_later_supported_cohort(self):
        card = {'automation': {'earliest_cohort_check': {'passed': True},
                              'coverage_window': {'effective_period_years': list(range(2011, 2025))}}}
        def row(unit, year, active):
            return dict(unit=unit, year=year, active=active, locator='synthetic metadata')
        check = adapter.source_earliest({'annual_rows': [row('AA', 2005, 0), row('AA', 2006, 1),
                                                        row('BB', 2015, 0), row('BB', 2016, 1)]}, card)
        self.assertEqual(check['source_earliest_observed_activation_year'], 2006)
        self.assertFalse(check['passed'])
        for records, field in (([row('AA', 2015, 1)], 'source_baseline_left_censored_units'),
                               ([row('AA', 2015, None), row('AA', 2016, 1)], 'source_first_activation_after_unknown_units')):
            check = adapter.source_earliest({'annual_rows': records}, card)
            self.assertFalse(check['passed'])
            self.assertEqual(check[field], ['AA'])
        check = adapter.source_earliest({'annual_rows': [row('AA', 2015, 0), row('AA', 2016, 1)]}, card)
        self.assertTrue(check['passed'])
        self.assertNotIn(2016, check['source_earliest_pre_periods'] + check['source_earliest_post_periods'])
        card['automation']['earliest_cohort_check']['passed'] = False
        self.assertFalse(adapter.source_earliest({'annual_rows': [row('AA', 2015, 0), row('AA', 2016, 1)]}, card)['passed'])

    def test_prepare_restores_shared_builder_on_failure(self):
        policies = self.root/'policies.json'
        policies.write_text('[]')
        original = adapter.intake.build_card
        with patch.object(adapter.intake, 'prepare_batch', side_effect=ValueError('isolated preparation failure')):
            with self.assertRaisesRegex(ValueError, 'isolated preparation failure'):
                adapter.main(['--policies', str(policies), '--output-dir', str(self.root/'prepared')])
        self.assertIs(adapter.intake.build_card, original)

    def test_pdaps_unknown_values_stay_unknown(self):
        self.assertEqual(pdaps.code('Yes, for adults only', 'summary_requirement'), 1)
        self.assertEqual(pdaps.code('No', 'summary_requirement'), 0)
        self.assertIsNone(pdaps.code('No record found', 'summary_requirement'))
        self.assertIsNone(pdaps.code(None, 'summary_requirement'))
        self.assertIsNone(parse_cdc.code('Unknown', 'yes'))
        self.assertIsNone(parse_cdc.code('', 'age21'))

    def test_pdaps_failure_does_not_abort_other_policy(self):
        config = [('broken', 'broken_family', '', [], None), ('good', 'good_family', '', [], None)]
        def build(item):
            if item[0] == 'broken':
                raise ValueError('bad source')
            summary = dict(policy_family='good_family', history_complete=False, coverage={},
                           completeness_verdict='incomplete', existing_design_id=None,
                           missing_days=1, unknown_days=0, conflict_days=0)
            return {'policy_id': 'good'}, summary
        with patch.object(pdaps, 'ROOT', self.root), patch.object(pdaps, 'CONFIG', config), \
                patch.object(pdaps, 'build_policy', side_effect=build), contextlib.redirect_stdout(io.StringIO()):
            failures = pdaps.replay()
        self.assertEqual(failures[0]['policy_family'], 'broken_family')
        self.assertEqual(json.loads((self.root/'POLICIES.json').read_text())['policies'], [{'policy_id': 'good'}])
        self.assertEqual(len(json.loads((self.root/'SOURCE_CATALOG.json').read_text())['sources']), 1)

    def test_cdc_failure_is_recorded_without_aborting_receipt_batch(self):
        (self.root/'FETCH_REPORT.json').write_text(json.dumps([
            {'tracker_id': 'missing', 'status': 'downloaded'},
            {'tracker_id': 'stopped', 'status': 'stopped', 'error': 'blocked source'},
        ]))
        with patch.object(parse_cdc, 'ROOT', self.root), contextlib.redirect_stdout(io.StringIO()):
            failures = parse_cdc.parse()
        self.assertEqual(len(failures), 2)
        self.assertEqual(json.loads((self.root/'POLICIES.json').read_text()), [])
        self.assertEqual({r['tracker_id'] for r in json.loads((self.root/'SOURCE_CATALOG.json').read_text())}, {'missing', 'stopped'})

    def test_social_failure_preserves_other_source_cards(self):
        with patch.object(social, 'ROOT', self.root), \
                patch.object(social, 'dol', side_effect=ValueError('invalid source')), \
                patch.object(social, 'dol_indexing', return_value={'policy_id': 'minimum_wage_indexing'}), \
                patch.object(social, 'nces', return_value=[{'policy_id': 'compulsory_school_attendance'}]), \
                patch.object(social, 'nber', return_value={'policy_id': 'state_eitc'}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(social.main(['--data-root', str(self.root)]), 1)
        self.assertEqual(len(json.loads((self.root/'POLICIES.json').read_text())), 3)
        checks = json.loads((self.root/'ADAPTER_CHECKS.json').read_text())
        self.assertEqual(checks['failures'][0]['source'], 'dol')
        self.assertFalse(checks['source_hashes_verified'])

    def test_bad_source_hash_and_incomplete_cms_snapshot_fail_closed(self):
        raw = self.root/'raw'
        raw.mkdir()
        (raw/'source.html').write_text('synthetic source')
        (self.root/'DOWNLOADS.json').write_text(json.dumps([{'path': str(raw/'source.html'), 'sha256': '0'*64}]))
        with patch.object(social, 'ROOT', self.root), self.assertRaises(ValueError):
            social.provenance('source.html')
        snapshot = raw/'cms_eligibility_current.html'
        snapshot.write_text('<table><tr><td>Alabama</td><td>synthetic</td></tr></table>')
        (self.root/'DOWNLOADS.json').write_text(json.dumps([{'path': str(snapshot)}]))
        with patch.object(cms, 'ROOT', self.root), self.assertRaises(ValueError):
            cms.main(['--data-root', str(self.root)])
        self.assertFalse((self.root/'ELIGIBILITY_SNAPSHOT.json').exists())

    def test_selection_stops_malformed_candidate_and_keeps_valid_candidate(self):
        good = {'policy_id': 'cdc_628YAC_203_age21', 'policy_family': 'tobacco_sales_age',
                'outcome': {}, 'domain': 'Health'}
        for domain, records in (('cdc', [None, good]), ('pdaps', []), ('cms', []), ('social', [])):
            folder = self.root/domain
            folder.mkdir()
            (folder/'POLICIES.json').write_text(json.dumps(records))
        with patch.object(intake_selection, 'ROOT', self.root), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(intake_selection.main(['--data-root', str(self.root)]), 1)
        self.assertEqual(json.loads((self.root/'SELECTED_POLICIES.v2.json').read_text())['policies'][0]['policy_id'], good['policy_id'])
        rows = json.loads((self.root/'DISPOSITIONS.v2.json').read_text())['rows']
        self.assertEqual(rows[0]['action'], 'INVALID_CANDIDATE_STOPPED')
        self.assertEqual(rows[1]['action'], 'S0_CANDIDATE_PENDING_GATES')

    def test_verifier_checks_later_design_after_one_invalid_card(self):
        checked=[]
        cards=[self.root/'bad.json',self.root/'good.json']
        def validate(path, root, **kwargs):
            checked.append(path.name)
            if path.name=='bad.json':
                raise ValueError('invented malformed card')
            return {'design_id':'scalable_good','policy_family':'invented_family'}
        with patch.object(verifier,'verify_card',side_effect=validate):
            result=verifier.validate_preparation(cards,[],[],{'cards_emitted':2,'auditor_jobs':0},self.root)
        self.assertEqual(checked,['bad.json','good.json'])
        self.assertEqual(result['validated_design_ids'],['scalable_good'])
        self.assertEqual(len(result['failures']),1)
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual([row['status'] for row in result['checks']],['FAIL','PASS'])

    def test_verifier_checks_later_job_and_report_counts_scale(self):
        checked=[]
        jobs=[self.root/'bad.md',self.root/'good.md']
        from types import SimpleNamespace
        def validate(path,root,ids,**kwargs):
            checked.append(path.name)
            if path.name=='bad.md':
                raise ValueError('invented malformed job')
            return SimpleNamespace(design_id='scalable_good')
        with patch.object(verifier,'verify_card',return_value={'design_id':'scalable_good','policy_family':'invented_family'}), \
                patch.object(verifier,'verify_job',side_effect=validate):
            result=verifier.validate_preparation([self.root/'good.json'],[],jobs,
                {'cards_emitted':1,'auditor_jobs':2},self.root)
        self.assertEqual(checked,['bad.md','good.md'])
        self.assertEqual(len(result['failures']),1)
        self.assertFalse(any(row['kind']=='inventory' for row in result['failures']))

    def test_verifier_writes_failure_inventory_and_returns_nonzero(self):
        prepared=self.root/'prepared_v2'
        cards=prepared/'intake/cards';cards.mkdir(parents=True)
        for name in ('bad.json','good.json'):
            (cards/name).write_text('{}')
        report={'cards_emitted':2,'auditor_jobs':0,'first_order_ready':0,
                'mapped_first_order_lead_families':[],'complete_histories':0,
                'earliest_cohort_supported':0,'target_met':False}
        (prepared/'RUN6_REPORT.json').write_text(json.dumps(report))
        checked=[]
        def validate(path,root,**kwargs):
            checked.append(path.name)
            if path.name=='bad.json':
                raise ValueError('invented invalid card')
            return {'design_id':'scalable_good','policy_family':'invented_family'}
        with patch.object(verifier,'ROOT',self.root), patch.object(verifier,'verify_card',side_effect=validate), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(verifier.main(['--data-root',str(self.root)]),1)
        self.assertEqual(checked,['bad.json','good.json'])
        output=json.loads((self.root/'VALIDATION.json').read_text())
        self.assertEqual(output['card_history_job_verification'],'FAIL')
        self.assertEqual(len(output['design_failures']),1)
        self.assertFalse(output['historical_snapshot'])

    def test_default_job_verification_allows_collected_outputs(self):
        from types import SimpleNamespace
        output=self.root/'output.json';output.write_text('{}')
        job=SimpleNamespace(design_id='scalable_good',stage='S0',output_dir=str(self.root),
                            output_path=str(output),result_path=str(output),
                            contract={'execution_authorized':False})
        with patch.object(verifier,'load_job',return_value=job):
            self.assertIs(verifier.verify_job(self.root/'job.md',self.root,{'scalable_good'}),job)
            with self.assertRaisesRegex(ValueError,'already collected'):
                verifier.verify_job(self.root/'job.md',self.root,{'scalable_good'},historical_snapshot=True)


if __name__ == '__main__':
    unittest.main()
