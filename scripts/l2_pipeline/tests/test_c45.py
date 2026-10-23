"""Synthetic §7/8 regressions: real packet loading/collection; no model or fits."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.autopilot import Autopilot, Backend, atomic, read
from scripts.l2_pipeline.agents.replacement import ContractRejection, emit, rejection_kind
from scripts.l2_pipeline.agents.jobs import emit_job, load_job
from scripts.l2_pipeline.agents.collect import collect_job
from scripts.l2_pipeline.agents.contract_tests import golden_output, check_job_types
from scripts.l2_pipeline.agents.contract_tests.packets import emit_fixture_job, _context
from scripts.l2_pipeline.storage import ensure_once, sha


class FakeHost(Backend):
    def __init__(self,repo):
        super().__init__(repo);self.states={};self.launches=[]
    def job_state(self,path):
        return self.states.get(str(path)) or super().job_state(path)
    def global_running(self): return sum(v=='running' for v in self.states.values())
    def launch(self,path,claim):
        self.launches.append(str(path));self.states[str(path)]='running'
        return dict(pid=len(self.launches),started_at='invented')
    def alive(self,pid): return True


class C45Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name).resolve();self.registry=self.repo/'data/l2_pipeline/registry'
        self.backend=FakeHost(self.repo);self.pilot=Autopilot(self.registry,self.backend)
        self.row=dict(design_id='contract-fixture',attempt_id='original')

    def put(self,job,value): atomic(Path(job.output_path),value)

    def pair(self):
        root=self.repo/'fixture'
        ctx=_context(root,'s1_coder')
        a=emit_job(ctx,'S1','A',jobs_root=root/'jobs')
        b=emit_job(ctx,'S1','B',jobs_root=root/'jobs')
        for job in (a,b):
            self.put(job,golden_output(job))
            atomic(Path(job.output_dir)/'DISPATCH_RESULT.json',dict(status='COMPLETED',exit_code=0))
        ensure_once(self.pilot.case_dir(self.row)/'jobs/history.json',dict(jobs=[a.job_path,b.job_path]))
        return a,b

    def test_transport_collects_all_authorized_roles_with_original_inputs_and_outputs_retained(self):
        kinds=['s0_purpose','s1_coder','s1_coder_v7','s1_auditor','s1_auditor_v7',
               's1_followup_auditor','s1_third_read','s1_official_history_v9',
               's5_forecast','s5_forecast_v7','s5_template_forecast','s5_template_forecast_v7',
               's6_draft_reviewer','s6_sealed_reviewer']
        for index,kind in enumerate(kinds):
            with self.subTest(kind=kind):
                old=emit_fixture_job(self.repo/str(index),kind)
                self.put(old,{'rejected_original':'invented'})
                before={str(p):sha(p) for p in (Path(old.job_path),Path(old.job_path).with_suffix('.job.json'),Path(old.output_path))}
                new=emit(old.job_path,self.repo/str(index)/'replacement','c45_test')
                self.assertEqual(old.inputs,new.inputs)
                self.assertNotEqual(old.output_path,new.output_path)
                self.assertEqual(new.contract['attempt_id'],'c45_test')
                check_job_types([new]);self.put(new,golden_output(new))
                receipt=collect_job(new.job_path)
                self.assertTrue(receipt['validated']);self.assertEqual(receipt,collect_job(new.job_path))
                if new.stage in {'S0','S5'}:
                    self.assertEqual(read(Path(new.output_dir).parent.parent/'COLLECTION.json'),receipt)
                if new.stage=='S5':self.assertEqual(new.promote_path,old.promote_path)
                self.assertEqual(before,{p:sha(p) for p in before})

    def test_one_replacement_keeps_peer_and_restart_dispatches_once(self):
        a,b=self.pair();value=golden_output(a)
        value['blindness_attestation']['outcome_associations_read']=True;self.put(a,value)
        old=sha(a.output_path)
        self.assertIsNone(self.pilot.agent_action(self.row,'history'))
        peer=sha(Path(b.output_dir)/'COLLECTION.json')
        path=self.pilot.effective_job(self.row,a.job_path)
        self.assertNotEqual(path,a.job_path);self.pilot.schedule()
        self.assertEqual(self.backend.launches,[path])
        restarted=Autopilot(self.registry,self.backend)
        self.assertIsNone(restarted.agent_action(self.row,'history'));restarted.schedule()
        self.assertEqual(self.backend.launches,[path])
        new=load_job(Path(path));self.put(new,golden_output(new));self.backend.states[path]='done'
        result=restarted.agent_action(self.row,'history')
        self.assertEqual(result['jobs'],[path,b.job_path])
        self.assertEqual(peer,sha(Path(b.output_dir)/'COLLECTION.json'));self.assertEqual(old,sha(a.output_path))
        ledger=[json.loads(v) for v in (self.registry/'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()]
        self.assertTrue(all('§7' in v['rule'] for v in ledger if v['decision'] in {'REPLACEMENT_PREPARED','RESERVE','LAUNCHED'}))

    def test_second_contract_failure_stops_and_queues_without_another_replacement(self):
        a,b=self.pair();value=golden_output(a);value.pop('blindness_attestation');self.put(a,value)
        self.pilot.agent_action(self.row,'history')
        path=self.pilot.effective_job(self.row,a.job_path);new=load_job(Path(path))
        self.put(new,{});self.backend.states[path]='done'
        with self.assertRaisesRegex(ValueError,'replacement failed') as raised:
            self.pilot.agent_action(self.row,'history')
        self.pilot.stop(self.row,'history',str(raised.exception))
        self.assertEqual(self.pilot.next_action(self.row),'STOP')
        self.assertEqual(len(read(self.registry/'HUB_QUEUE.json')['entries']),1)
        self.assertEqual(len(list((self.registry.parent/'autopilot/replacements').rglob('REPLACEMENT.json'))),1)

    def test_slot_is_design_role_wide_across_scheduler_attempts_and_stages(self):
        a,b=self.pair()
        exc=ContractRejection(a.job_path,'SCHEMA_FORMAT','invented format')
        self.pilot.replace_submission(self.row,'history',exc)
        row=dict(self.row,attempt_id='new_scheduler_attempt')
        with self.assertRaisesRegex(ValueError,'replacement failed'):
            self.pilot.replace_submission(row,'audit',exc)

    def test_schema_and_url_failures_are_contract_but_scientific_verdicts_never_are(self):
        for message in ['Missing/contaminated blindness attestation',
                        'V9 source id/URL invalid or duplicate','Invalid audit checks',
                        'Agent output must be an object','V9 history schema missing']:
            self.assertIsNotNone(rejection_kind(ValueError(message)))
            for value in [{'verdict':'STOP'},{'must':[{'id':'scientific'}]},
                          {'status':'PARK'},{'audit':{'passed':False}}]:
                self.assertIsNone(rejection_kind(ValueError(message),value))
        for message in ['V3 failed','MUST>0','PARK','NEEDS_RULING: mechanism',
                        'Agent input changed','Reviewed code input changed','V9 absence needs official code/history evidence']:
            self.assertIsNone(rejection_kind(ValueError(message)))

    def test_replacement_forecast_is_read_by_the_complete_roster_lock(self):
        from scripts.l2_pipeline.predictions import lock_predictions
        from scripts.l2_pipeline.agents.jobs import PERSONAS
        root=self.repo/'forecasts';ctx=_context(root,'s5_forecast')
        original=emit_job(ctx,'S5','Card',jobs_root=root/'jobs')
        self.put(original,{'rejected_original':'invented'})
        job=emit(original.job_path,root/'replacement','c45_roster')
        self.put(job,golden_output(job));collect_job(job.job_path)
        for persona in PERSONAS[1:]:
            peer=emit_job(ctx,'S5',persona,jobs_root=root/'jobs')
            self.put(peer,golden_output(peer));collect_job(peer.job_path)
        lock=lock_predictions(ctx.design_dir,list(PERSONAS),ctx.card.design_id,sha(ctx.design_dir/'SPEC.json'))
        value=read(lock)
        self.assertIn('predictions/Card/submission.json',value['files'])
        self.assertIn(str(Path(job.output_path).relative_to(ctx.design_dir)),value['original_files'])
        self.assertEqual(read(Path(original.output_path)),{'rejected_original':'invented'})

    def test_reported_failed_v3_is_not_replaced_even_with_a_bad_attestation(self):
        value=dict(checks=[dict(status='known_error' if n<3 else 'match') for n in range(10)])
        self.assertIsNone(rejection_kind(ValueError('Missing/contaminated blindness attestation'),value))

    def test_backend_rejects_malformed_json_and_v9_url_and_retains_invalid_original(self):
        job=emit_fixture_job(self.repo/'v9','s1_official_history_v9')
        value=golden_output(job);value['sources'][0]['url']='invalid-url';self.put(job,value)
        with self.assertRaises(ContractRejection) as result:self.backend.collect(job.job_path)
        self.assertEqual(result.exception.kind,'V9_SOURCE_ID_URL')
        Path(job.output_path).write_text('{')
        with self.assertRaises(ContractRejection) as result:self.backend.collect(job.job_path)
        self.assertEqual(result.exception.kind,'SCHEMA_FORMAT')

    def test_dead_without_output_qualifies_but_failed_process_with_output_does_not(self):
        job=emit_fixture_job(self.repo/'dead','s1_coder')
        atomic(Path(job.output_dir)/'DISPATCH_RESULT.json',dict(status='FAILED_NO_RETRY',exit_code=1))
        with self.assertRaises(ContractRejection) as result:self.backend.job_state(job.job_path)
        self.assertEqual(result.exception.kind,'DEAD_NO_RESULT')
        self.put(job,{'retained':'partial'})
        with self.assertRaises(ValueError) as result:self.backend.job_state(job.job_path)
        self.assertNotIsInstance(result.exception,ContractRejection)

    def test_live_worker_with_partial_output_remains_running(self):
        job=emit_fixture_job(self.repo/'live','s1_coder');self.put(job,{})
        atomic(Path(job.output_dir)/'DISPATCH_ONCE.json',{})
        with patch('scripts.l2_pipeline.agents.liveness.status',return_value='alive'):
            self.assertEqual(self.backend.job_state(job.job_path),'running')
        self.assertFalse((self.pilot.replacement_slot(self.row,job.job_path)/'REPLACEMENT.json').exists())

    def test_orphan_reconciliation_records_no_dispatch_and_is_idempotent(self):
        p=self.registry/'worker_reconciliations/orphan.json'
        atomic(p,dict(reason='Ambiguous liveness exceeded three hours',reservation='/invented/placeholder'))
        self.pilot.queue_reconciliations();self.pilot.queue_reconciliations()
        entries=read(self.registry/'HUB_QUEUE.json')['entries']
        self.assertEqual(len(entries),1);self.assertEqual(entries[0]['status'],'DEAD_NO_RESULT')
        self.assertIn('§8',entries[0]['rule']);self.assertFalse(self.backend.launches)

    def test_superseded_stop_keeps_old_bytes_and_second_failure_has_new_stop(self):
        a,b=self.pair();self.pilot.stop(self.row,'history','Missing/contaminated blindness attestation')
        root=self.pilot.case_dir(self.row);before=sha(root/'STOP.json')
        ensure_once(root/'STOP_SUPERSEDED_C45.json',dict(stop_sha256=before))
        self.assertEqual(self.pilot.next_action(self.row),'history')
        self.pilot.stop(self.row,'history','§7 replacement failed')
        self.assertEqual(self.pilot.next_action(self.row),'STOP')
        self.assertEqual(read(root/'STOP_C45.json')['reason'],'§7 replacement failed')
        self.assertEqual(before,sha(root/'STOP.json'))

    def test_replacement_metadata_cannot_change_seed_predicate_or_input(self):
        old=emit_fixture_job(self.repo/'tamper','s1_coder')
        new=emit(old.job_path,self.repo/'tamper/replacement','c45_test')
        from scripts.l2_pipeline.agents.replacement import validate
        contract=dict(new.contract,manual=dict(new.contract['manual'],seed=999))
        with self.assertRaisesRegex(ValueError,'scientific contract'):validate(replace(new,contract=contract))


if __name__=='__main__':unittest.main()
