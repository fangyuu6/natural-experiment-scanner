"""C58 blind transport tests use invented dates and never fit outcomes."""
import json, tempfile, unittest
from pathlib import Path
from dataclasses import replace
from scripts.l2_pipeline.agents.contract_tests.packets import emit_fixture_job
from scripts.l2_pipeline.agents.history_packet import card, check, clean_pending, answer_bearing
from scripts.l2_pipeline.agents import sonnet_queue as q
from scripts.l2_pipeline.storage import sha

from unittest.mock import patch


class PacketTests(unittest.TestCase):
    def test_recursive_year_metadata_and_treated_list_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            job=emit_fixture_job(Path(tmp),'s1_official_history_v9')
            check(job)
            for field in ('cohort_years','coded_years','treated_states','novel_screen_metadata'):
                contract=json.loads(json.dumps(job.contract))
                contract['card']['analysis']={field:{'CA':1973,'AK':1989}}
                with self.assertRaisesRegex(ValueError,'C58'): check(replace(job,contract=contract))
                self.assertNotIn('analysis',card(contract['card']))

    def test_both_backends_receive_same_clean_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);job=emit_fixture_job(root/'jobs','s1_official_history_v9')
            check(job)
            receipt=q.enqueue(job.job_path,dict(design_id=job.design_id),root/'queue')
            packet=Path(receipt['packet'])
            self.assertEqual((packet/'PROMPT.md').read_bytes(),Path(job.job_path).read_bytes())
            self.assertEqual(q.read(packet/'OUTPUT_SCHEMA.json')['contract'],job.contract)
            self.assertFalse(answer_bearing(job.contract))

    def test_clean_packet_regeneration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            job=emit_fixture_job(Path(tmp),'s1_official_history_v9')
            before=sha(job.job_path)
            self.assertEqual(clean_pending(job.job_path,Path(tmp)/'clean'),job.job_path)
            self.assertEqual(sha(job.job_path),before)

    @patch('scripts.l2_pipeline.agents.history_chunks.CHUNK_UNITS',1000)  # F7: this test exercises one ordinary packet
    def test_dirty_history_requeue_cleans_without_touching_active_lease(self):
        from dataclasses import asdict
        from scripts.l2_pipeline.agents.jobs import load_job
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);job=emit_fixture_job(root/'jobs','s1_official_history_v9')
            c=json.loads(json.dumps(job.contract));c['card']['analysis']={'screen_expected_support':{'cohort_years':[1973],'treated_states':['AA']}}
            dirty=replace(job,contract=c)
            marker='## Execution metadata binding (data)\n```json\n'
            before,tail=Path(job.job_path).read_text().split(marker,1);_,after=tail.split('\n```',1)
            value=asdict(dirty);value.pop('job_sha256')
            Path(job.job_path).write_text(before+marker+json.dumps(value,indent=2)+'\n```'+after)
            dirty=replace(dirty,job_sha256=sha(job.job_path))
            Path(job.job_path).with_suffix('.job.json').write_text(json.dumps(asdict(dirty)))
            old_hash=sha(job.job_path)
            # Historical outputs remain loadable; dispatch preflight refuses contamination.
            load_job(Path(job.job_path))
            with self.assertRaisesRegex(ValueError,'C58'):check(dirty)
            clean=clean_pending(job.job_path,root/'clean');check(load_job(Path(clean)))
            self.assertEqual(clean_pending(job.job_path,root/'clean'),clean)
            self.assertEqual(sha(job.job_path),old_hash)
            # Simulate a previously-enqueued packet from before C58.
            with self.assertRaisesRegex(ValueError,'C58'):
                q.enqueue(job.job_path,dict(design_id=job.design_id),root/'queue')
            with patch('scripts.l2_pipeline.agents.contract_tests.check_job_types',return_value=[]), \
                    patch('scripts.l2_pipeline.agents.history_packet.check'):
                receipt=q.enqueue(job.job_path,dict(design_id=job.design_id),root/'queue')
            lease=q.claim(root/'queue','worker',now=100)
            self.assertEqual(q.reroute_pending(root/'queue'),[])
            self.assertEqual(sha(Path(lease['packet'])/'PROMPT.md'),old_hash)
            events=q.reap(root/'queue',now=100+q.LEASE_SECONDS)
            self.assertEqual(events[-1]['status'],'rerouted_codex')
            check(load_job(Path(events[-1]['job'])))
            self.assertEqual(sha(job.job_path),old_hash)

    def test_exact_copy_requires_both_complete_histories_without_official_locator(self):
        from scripts.l2_pipeline.agents.history_packet import exact_copy
        from copy import deepcopy
        screen={'adopters':{'AA':1973},'never_through_coverage_end':['BB']}
        v={'records':[{'unit':'AA','status':'treated','best_evidence_start_year':1973},
                      {'unit':'BB','status':'never_treated'}],'sources':[]}
        self.assertTrue(exact_copy([v,deepcopy(v)],screen,['AA','BB']))
        other=deepcopy(v);other['records'][0]['best_evidence_start_year']=1974
        self.assertFalse(exact_copy([v,other],screen,['AA','BB']))
        official=deepcopy(v);official['sources']=[{'source_id':'law','source_type':'official','url':'https://fixture.gov/law'}]
        official['records'][0]['evidence_refs']=[{'source_id':'law','locator':'section 1','quote':'operation begins'}]
        self.assertFalse(exact_copy([v,official],screen,['AA','BB']))
        self.assertFalse(exact_copy([v,v],{'adopters':{'AA':1973}},['AA','BB']))

    def test_replacement_checks_original_acceptance_before_cleaning(self):
        from unittest.mock import patch
        from scripts.l2_pipeline.agents.replacement import emit
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);job=emit_fixture_job(root/'jobs','s1_official_history_v9')
            Path(job.output_dir).mkdir(parents=True,exist_ok=True)
            (Path(job.output_dir)/'COLLECTION.json').write_text(json.dumps({'validated':True}))
            with patch('scripts.l2_pipeline.agents.history_packet.clean_pending') as clean:
                with self.assertRaisesRegex(ValueError,'Accepted submissions'):emit(job.job_path,root/'slot','replacement')
                clean.assert_not_called()

    def test_selected_pending_job_cleans_and_launch_claim_preserves_transport(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from scripts.l2_pipeline.autopilot import Autopilot
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);job=emit_fixture_job(root/'jobs','s1_official_history_v9')
            case=root/'case';case.mkdir()
            (case/'COLLECTION_SELECTION_C48.json').write_text(json.dumps({'jobs':{'prior':{'job':job.job_path,'evidence':[]}}}))
            fake=SimpleNamespace(repo=root,registry=root/'data/l2_pipeline/registry',
                case_dir=lambda row:case,replacement_slot=lambda row,path:root/'slot')
            with patch('scripts.l2_pipeline.agents.history_packet.clean_pending',return_value='clean') as clean:
                self.assertEqual(Autopilot.effective_job(fake,{},'prior'),'clean')
                self.assertEqual(clean.call_args[0][0],job.job_path)
            claims=fake.registry.parent/'autopilot/worker_claims';claims.mkdir(parents=True)
            (claims/(q.identity(job.job_path)+'.json')).write_text('{}')
            with patch('scripts.l2_pipeline.agents.history_packet.clean_pending') as clean:
                self.assertEqual(Autopilot.effective_job(fake,{},'prior'),job.job_path)
                clean.assert_not_called()
