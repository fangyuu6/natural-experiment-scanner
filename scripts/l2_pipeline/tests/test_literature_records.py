"""Synthetic evidence contracts; no research agents or outcome reads."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline.agents import emit_literature_jobs, collect_job, load_job
from scripts.l2_pipeline.agents.dispatch import command, main as dispatch
from scripts.l2_pipeline.agents.literature import pending_candidates, validate_literature_output
from scripts.l2_pipeline.storage import ensure_once, sha

REPO = Path(__file__).resolve().parents[3]
URL = 'https://example.org/open/paper'


def located(locator='Methods section'):
    return dict(source_url=URL, locator=locator)


def answer(job):
    return dict(study_id=job.design_id, design_id=job.design_id, role='literature_record',
        candidate_sha256=job.contract['candidate_sha256'], source_type='published_did_study',
        status='VERIFIED', title='Synthetic example', authors=['Example Author'], year=2010,
        venue='Synthetic Journal', doi='10.1234/fixture', citation_evidence=[dict(
            **located('Metadata header'), supports=['title', 'authors', 'year', 'venue', 'doi'])],
        source_version=dict(version='author manuscript', doi='10.1234/fixture', **located('Title page 1')),
        policy_id='fixture_policy', policy_definition='Synthetic legal definition',
        target_outcome='unknown_fixture_construct', target_population='Synthetic population',
        policy_evidence=located(), primary_outcome='unknown_fixture_construct',
        data_source='Synthetic data', population='Synthetic population', outcome_evidence=located(),
        study_window=dict(start=2000, end=2010, **located()), adoption=dict(
            states=[dict(state='CA', year=2005, **located('Appendix Table A1 page 12'))],
            coverage_start=2000, coverage_end=2010, reported_scope_complete=True, complete_list=False,
            universe=['CA', 'TX'], **located('Appendix Table A1 page 12')),
        published_results=[dict(estimate_as_reported='-0.120**', se_as_reported='(0.045)',
            ci_as_reported=None, units='percentage points', primary=True, **located('Table 3 row 1 column 2 page 8'))],
        access=dict(open_fulltext=True, open_version_url=URL, paywalled_sources=[]),
        search_log=[dict(query='synthetic title DOI', source_url=URL)], downloads=[],
        scans=[dict(source_url=URL, separate_local_scan=False, method='browser only', flags=[],
            limitations='No locally available bytes; no separate local scan claimed')],
        gaps=[], outcome_associations_fit=False, paid_calls=0)


class LiteratureRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.registry = self.root / 'registry.json'; self.checklist = self.root / 'checklist.json'
        ensure_once(self.registry, dict(studies=[dict(study_id='done', completion=dict(
            full_record_hub_check_completed=True)), dict(study_id='pending', title='Synthetic lead')]))
        ensure_once(self.checklist, dict(records=[dict(study_id='done', full_record_completed=True),
                                                 dict(study_id='pending', full_record_completed=False)]))
        self.jobs = self.root / 'jobs'
        self.job = emit_literature_jobs(self.registry, repo=REPO, jobs_root=self.jobs, checklist=self.checklist)[0]

    def tearDown(self):
        self.temp.cleanup()

    def test_emission_skips_only_full_records_and_binds_isolated_network_packet(self):
        self.assertEqual(pending_candidates(self.registry, self.checklist)[1], ['done'])
        again = emit_literature_jobs(self.registry, repo=REPO, jobs_root=self.jobs, checklist=self.checklist)
        self.assertEqual(again, [self.job]); self.assertEqual(load_job(Path(self.job.job_path)), self.job)
        self.assertEqual(self.job.stage, 'S0L'); self.assertIn('--search', command(self.job))
        prompt = Path(self.job.job_path).read_text()
        self.assertIn('never bypass', prompt); self.assertIn('appendix tables', prompt)
        self.assertFalse(Path(self.job.output_path).exists())
        self.assertFalse(Path(self.job.promote_path).is_relative_to(Path(self.job.output_dir)))

    def test_dispatch_dry_run_filters_new_stage_without_launch(self):
        with patch('scripts.l2_pipeline.agents.dispatch.preflight'), patch(
            'scripts.l2_pipeline.agents.dispatch._run') as run, contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(dispatch(['--jobs-root', str(self.jobs), '--stage', 'S0L', '--dry-run']), 0)
        run.assert_not_called(); self.assertIn('--search exec', printed.getvalue())
        self.assertFalse(Path(self.job.output_dir).exists())

    def test_missing_fields_locators_and_p_only_cannot_claim_full_verification(self):
        for mutate in (
            lambda v: v.pop('policy_definition'),
            lambda v: v.update(authors=[]),
            lambda v: v['citation_evidence'][0].update(supports=['title', 'doi']),
            lambda v: v['adoption'].update(reported_scope_complete=False),
            lambda v: v['adoption']['states'][0].update(year='2005'),
            lambda v: v['published_results'][0].update(se_as_reported=None, p_as_reported='<.01'),
            lambda v: v['published_results'][0].update(locator='full text'),
            lambda v: v['policy_evidence'].update(source_url='https://example.org/search?q=fixture'),
            lambda v: v['access'].update(open_fulltext=False),
            lambda v: v.update(gaps=['missing original appendix']),
        ):
            value = answer(self.job); mutate(value)
            with self.assertRaises(ValueError): validate_literature_output(self.job, value)

    def test_numeric_strings_preserved_and_no_connector_park_is_idempotent(self):
        value = answer(self.job); ensure_once(Path(self.job.output_path), value)
        receipt = collect_job(self.job.job_path)
        self.assertTrue(receipt['validated']); self.assertEqual(receipt['status'], 'PARK')
        self.assertEqual(receipt, collect_job(self.job.job_path))
        collected = json.loads(Path(self.job.promote_path).read_text())
        self.assertEqual(collected['published_results'], value['published_results'])
        self.assertFalse(receipt['execution_authorized']); self.assertEqual(receipt['outcome_values_read'], 0)
        self.assertFalse(list(Path(self.job.contract['s0_card_dir']).glob('*.json')))

    def test_blocked_open_version_search_parks_specific_reason(self):
        value = answer(self.job); value.update(status='UNRESOLVED', gaps=['Appendix is paywalled'],
            unresolved_reason='No open appendix found after author/NBER/repository search')
        value['access'] = dict(open_fulltext=False, open_version_url=None,
            paywalled_sources=[dict(url='https://example.org/paywall', reason='subscription required')])
        ensure_once(Path(self.job.output_path), value)
        result = collect_job(self.job.job_path)
        self.assertEqual(result['status'], 'PARK'); self.assertEqual(result['reason'], value['unresolved_reason'])
        self.assertEqual(json.loads(Path(result['s0_intake_path']).read_text())['issues'], value['gaps'])

    def test_collector_emits_candidate_card_automatically_and_keeps_readiness_pending(self):
        from scripts.l2_pipeline.tests.test_literature_cards import record
        value = answer(self.job); mapped = record()
        for field in ('policy_id', 'policy_definition', 'target_outcome', 'target_population',
                      'primary_outcome', 'population', 'policy_evidence', 'outcome_evidence',
                      'study_window', 'adoption', 'outcome_mapping'):
            value[field] = mapped[field]
        ensure_once(Path(self.job.output_path), value)
        result = collect_job(self.job.job_path)
        self.assertEqual(result['status'], 'CARD', result['reason'])
        intake = json.loads(Path(result['s0_intake_path']).read_text())
        card_path = Path(intake['card_path'])
        self.assertTrue(card_path.is_file()); self.assertEqual(sha(card_path), intake['card_sha256'])
        card = json.loads(card_path.read_text())
        self.assertEqual(card['outcome']['connector'], 'fars_state')
        self.assertFalse(card['automation']['literature_record']['s1_certified'])
        self.assertEqual(result, collect_job(self.job.job_path))

    def test_archive_bytes_license_scan_and_directory_validated(self):
        value = answer(self.job); path = Path(self.job.output_dir) / 'paper.html'
        path.parent.mkdir(parents=True); path.write_bytes(b'<html>Synthetic source</html>')
        value['downloads'] = [dict(path='paper.html', url=URL, size_bytes=path.stat().st_size,
            sha256=sha(path), license='Synthetic fixture')]
        value['scans'][0].update(separate_local_scan=True, method='normalized local heuristic',
            limitations='No dedicated ML scan; clean scan is not proof')
        self.assertEqual(validate_literature_output(self.job, value), value)
        for field, new in [('size_bytes', 1), ('sha256', '0'*64), ('license', ''), ('path', '../candidate.json')]:
            changed = copy.deepcopy(value); changed['downloads'][0][field] = new
            with self.assertRaises(ValueError): validate_literature_output(self.job, changed)
        value['scans'][0]['separate_local_scan'] = False
        with self.assertRaises(ValueError): validate_literature_output(self.job, value)

    def test_source_candidate_tamper_and_promoted_record_tamper_fail_closed(self):
        value = answer(self.job); ensure_once(Path(self.job.output_path), value); collect_job(self.job.job_path)
        Path(self.job.promote_path).write_text('{}')
        with self.assertRaises(ValueError): collect_job(self.job.job_path)
        Path(self.job.contract['candidate_path']).write_text('{}')
        with self.assertRaises(ValueError): load_job(Path(self.job.job_path))

    def test_duplicate_identity_and_incorrect_checklist_refused(self):
        self.registry.write_text(json.dumps(dict(studies=[dict(study_id='same'), dict(study_id='same')])))
        with self.assertRaises(ValueError): pending_candidates(self.registry)
        self.registry.write_text(json.dumps(dict(studies=[dict(study_id='other')])))
        with self.assertRaises(ValueError): pending_candidates(self.registry, self.checklist)


if __name__ == '__main__': unittest.main()
