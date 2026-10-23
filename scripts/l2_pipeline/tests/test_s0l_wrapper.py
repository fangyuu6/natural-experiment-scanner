"""S0L wrapper compatibility and provenance checks using synthetic records only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline.agents import collect_job, emit_literature_jobs
from scripts.l2_pipeline.agents.literature import validate_literature_output
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests.test_literature_records import REPO, URL, answer


class S0LWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        registry = self.root / 'registry.json'
        ensure_once(registry, {'studies': [{'study_id': 'wrapper_fixture'}]})
        self.job = emit_literature_jobs(registry, repo=REPO,
                                       jobs_root=self.root / 'jobs')[0]

    def test_failed_search_and_structured_disclosures_preserve_unresolved_record(self):
        value = answer(self.job)
        value['search_log'].append({'query': 'unproductive search', 'source_url': None})
        value['scans'][0]['limitations'] = ['No separate scan', 'Clean scan is not proof']
        value.update(status='UNRESOLVED', gaps=[{'field': 'adoption', 'detail': 'Missing appendix'}],
                     unresolved_reason='Missing appendix')
        before = copy.deepcopy(value)
        result = validate_literature_output(self.job, value)
        self.assertEqual(result, before)
        self.assertEqual(value, before)
        result['gaps'][0]['detail'] = 'Changed returned copy'
        self.assertEqual(value, before)

    def test_discovery_endpoints_are_never_claim_evidence(self):
        value = answer(self.job)
        endpoint = 'https://example.org/search?query=paper'
        value['search_log'][0]['source_url'] = endpoint
        value['scans'][0]['source_url'] = endpoint
        value['access']['paywalled_sources'] = [{'url': endpoint, 'reason': 'challenge'}]
        self.assertEqual(validate_literature_output(self.job, value), value)
        for field in ('policy_evidence', 'outcome_evidence', 'source_version', 'study_window', 'adoption'):
            changed = copy.deepcopy(value)
            changed[field]['source_url'] = endpoint
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_literature_output(self.job, changed)
        for field in ('citation_evidence', 'published_results'):
            changed = copy.deepcopy(value)
            changed[field][0]['source_url'] = endpoint
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_literature_output(self.job, changed)

    def test_unresolved_still_requires_nonsearch_citation_evidence(self):
        for citations in ([], [{'source_url': 'https://example.org/search?q=paper',
                               'locator': 'Citation metadata'}]):
            value = answer(self.job)
            value.update(status='UNRESOLVED', gaps=['No open appendix'],
                         unresolved_reason='No open appendix', citation_evidence=citations)
            with self.subTest(citations=citations), self.assertRaises(ValueError):
                validate_literature_output(self.job, value)

    def test_restarts_preserved_and_exact_duplicate_events_rejected(self):
        value = answer(self.job)
        row = copy.deepcopy(value['adoption']['states'][0])
        row['year'] = 2008
        value['adoption']['states'].append(row)
        self.assertEqual(validate_literature_output(self.job, value), value)
        duplicate = copy.deepcopy(row)
        duplicate['state'] = duplicate['state'].lower()
        value['adoption']['states'].append(duplicate)
        with self.assertRaises(ValueError):
            validate_literature_output(self.job, value)

    def test_one_primary_reported_uncertainty_satisfies_worker_contract(self):
        value = answer(self.job)
        extra = copy.deepcopy(value['published_results'][0])
        extra.update(estimate_as_reported='-0.075*', se_as_reported=None)
        value['published_results'].append(extra)
        self.assertEqual(validate_literature_output(self.job, value), value)
        value['published_results'][0].update(se_as_reported=None, ci_as_reported='[-0.20, -0.04]')
        self.assertEqual(validate_literature_output(self.job, value), value)
        value['published_results'][0].update(ci_as_reported=None, p_as_reported='<.01')
        with self.assertRaises(ValueError):
            validate_literature_output(self.job, value)

    def test_secondary_uncertainty_cannot_replace_primary_uncertainty(self):
        value = answer(self.job)
        secondary = copy.deepcopy(value['published_results'][0])
        secondary['primary'] = False
        value['published_results'].append(secondary)
        value['published_results'][0]['se_as_reported'] = None
        with self.assertRaises(ValueError):
            validate_literature_output(self.job, value)

    def test_metadata_locators_and_browser_group_scan_are_truthful(self):
        value = answer(self.job)
        value['citation_evidence'][0]['locator'] = 'Publications > Peer-Reviewed Articles, citation entry'
        value['scans'][0]['source_url'] = 'public web search results in search_log'
        self.assertEqual(validate_literature_output(self.job, value), value)
        value['scans'][0]['separate_local_scan'] = True
        with self.assertRaises(ValueError):
            validate_literature_output(self.job, value)

    def test_discovery_archive_still_requires_hash_license_and_local_scan(self):
        value = answer(self.job)
        endpoint = 'https://example.org/search?query=paper'
        path = Path(self.job.output_dir) / 'discovery.html'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'<html>Synthetic discovery metadata</html>')
        value['downloads'] = [{'path': path.name, 'url': endpoint, 'size_bytes': path.stat().st_size,
                               'sha256': sha(path), 'license': 'Synthetic fixture'}]
        value['scans'][0].update(source_url=endpoint, separate_local_scan=True,
                                method='synthetic local check', limitations=['Heuristic only'])
        self.assertEqual(validate_literature_output(self.job, value), value)
        for mutate in (
            lambda v: v['downloads'][0].update(sha256='0' * 64),
            lambda v: v['downloads'][0].update(size_bytes=1),
            lambda v: v['downloads'][0].update(license=''),
            lambda v: v['downloads'][0].update(path='../candidate.json'),
            lambda v: v['scans'][0].update(separate_local_scan=False),
        ):
            changed = copy.deepcopy(value)
            mutate(changed)
            with self.assertRaises(ValueError):
                validate_literature_output(self.job, changed)

    def test_empty_disclosure_values_are_rejected(self):
        for mutate in (
            lambda v: v.update(gaps=[{'field': 'adoption'}]),
            lambda v: v['scans'][0].update(limitations=[]),
            lambda v: v['scans'][0].update(limitations=['']),
            lambda v: v['search_log'][0].update(query=''),
            lambda v: v['search_log'][0].update(source_url='https://user:pass@example.org/search'),
        ):
            value = answer(self.job)
            value.update(status='UNRESOLVED', gaps=['Missing appendix'],
                         unresolved_reason='Missing appendix')
            mutate(value)
            with self.assertRaises(ValueError):
                validate_literature_output(self.job, value)

    def test_collection_is_offline_idempotent_and_preserves_worker_bytes(self):
        value = answer(self.job)
        value['search_log'].append({'query': 'failed attempt', 'source_url': None})
        value['scans'][0]['limitations'] = ['No separate local scan']
        value.update(status='UNRESOLVED', gaps=[{'field': 'adoption', 'reason': 'Missing appendix'}],
                     unresolved_reason='Missing appendix')
        ensure_once(Path(self.job.output_path), value)
        originals = {path: sha(path) for path in (
            Path(self.job.output_path), Path(self.job.job_path),
            Path(self.job.contract['candidate_path']))}
        with patch('urllib.request.urlopen', side_effect=AssertionError('collector fetched')), \
             patch('scripts.l2_pipeline.connectors.construct', side_effect=AssertionError('outcome access')):
            receipt = collect_job(self.job.job_path)
            self.assertEqual(receipt, collect_job(self.job.job_path))
        self.assertEqual(receipt['status'], 'PARK')
        self.assertEqual(receipt['record_status'], 'UNRESOLVED')
        self.assertFalse(receipt['execution_authorized'])
        self.assertEqual(receipt['outcome_values_read'], 0)
        intake = json.loads(Path(receipt['s0_intake_path']).read_text())
        self.assertEqual(intake['issues'], value['gaps'])
        self.assertEqual(intake['reason'], value['unresolved_reason'])
        promoted = json.loads(Path(receipt['promoted_path']).read_text())
        for key, expected in value.items():
            self.assertEqual(promoted[key], expected)
        self.assertEqual(promoted['original_sha256'], originals[Path(self.job.output_path)])
        self.assertEqual(originals, {path: sha(path) for path in originals})


if __name__ == '__main__':
    unittest.main()
