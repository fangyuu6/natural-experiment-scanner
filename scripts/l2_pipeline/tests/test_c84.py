"""Synthetic C84: exploratory absence, exact-defect recovery, bounded correction."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from scripts.l2_pipeline.agents import purpose_direction as d,sonnet_queue as q
from scripts.l2_pipeline import c83_l3 as l3
from scripts.l2_pipeline.agents.contract_tests import golden_output
from scripts.l2_pipeline.agents.contract_tests.packets import _context,emit_fixture_job
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.autopilot import Backend,Autopilot,read,atomic
from scripts.l2_pipeline.maintenance import resume_code_fix,reason_tags
from scripts.l2_pipeline.agents.replacement import ContractRejection,rejection_kind

class Direction(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.ctx=_context(self.root,'empty_direction')
        self.ctx.card.payload['first_order']['statute_refs']=[]
        self.row=dict(design_id=self.ctx.card.design_id,attempt_id='test')
        self.pilot=SimpleNamespace(backend=SimpleNamespace(ctx=lambda row:self.ctx),
            case_dir=lambda row:self.root/'case',pending=[],collect_submissions=Mock())
    def test_empty_citations_no_dispatch_and_idempotent(self):
        with patch.object(d,'emit',side_effect=AssertionError('must not dispatch')):
            value=d.action(self.pilot,self.row)
            self.assertEqual(value,d.action(self.pilot,self.row))
        self.assertEqual(value['reason'],'no_public_citation');self.assertEqual(value['field_status']['F1'],'FIELD_MISSING')
        self.pilot.collect_submissions.assert_not_called()
    def test_unsupported_purpose_citations_count_as_absent(self):
        ensure_once(self.ctx.design_dir/'PURPOSE_VERIFICATION.json',dict(citations=[dict(source_ref='https://example.invalid',locator='s1',supports=['target_population'],source_type='paper')]))
        self.assertEqual(d.action(self.pilot,self.row)['reason'],'no_public_citation')
    def test_purpose_only_citations_dispatch(self):
        ensure_once(self.ctx.design_dir/'PURPOSE_VERIFICATION.json',dict(citations=[dict(source_ref='https://example.invalid',locator='s1',supports=['target_outcome'],source_type='paper')]))
        self.pilot.collect_submissions.return_value=None
        self.assertIsNone(d.action(self.pilot,self.row));self.pilot.collect_submissions.assert_called_once()
        self.assertEqual(len(list((self.root/'case').rglob('*.job.json'))),2)
    def test_statute_only_citations_dispatch(self):
        self.ctx.card.payload['first_order']['statute_refs']=[dict(source_ref='https://example.invalid',locator='s1')]
        self.pilot.collect_submissions.return_value=None
        self.assertIsNone(d.action(self.pilot,self.row));self.pilot.collect_submissions.assert_called_once()
    def test_coding_failure_is_missing_and_l2_continues(self):
        self.ctx.card.payload['first_order']['statute_refs']=[dict(source_ref='https://example.invalid',locator='s1')]
        self.pilot.collect_submissions.side_effect=ValueError('invalid blind coding')
        value=d.action(self.pilot,self.row)
        self.assertEqual(value['reason'],'blind_coding_unavailable');self.assertIsNone(value['purpose_direction'])
    def test_missing_retain_and_overlay_preserve_null_and_reason(self):
        old=sha(self.ctx.design_dir/'SPEC.json');d.action(self.pilot,self.row)
        value=read(l3.retain(self.ctx));self.assertEqual(value['field_status']['F1'],'FIELD_MISSING')
        result=l3.overlay({},value);self.assertEqual(result['purpose_direction_status'],'FIELD_MISSING')
        self.assertIsNone(result['first_order']['purpose_direction'])
        self.assertEqual(result['first_order']['purpose_direction_evidence']['reason'],'no_public_citation')
        self.assertEqual(old,sha(self.ctx.design_dir/'SPEC.json'))
    def test_protected_design_unchanged(self):
        ensure_once(self.ctx.design_dir/'RESULT.json',{})   # F1-C: only an estimate artifact closes F1 coding
        before={p:p.read_bytes() for p in self.ctx.design_dir.iterdir() if p.is_file()}
        d.action(self.pilot,self.row);self.assertIsNone(l3.retain(self.ctx))
        self.assertEqual(before,{p:p.read_bytes() for p in self.ctx.design_dir.iterdir() if p.is_file()})

class Queue(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.job=emit_fixture_job(self.root,'s0_purpose')
        self.queue=q.root_for(self.root);self.packet=q.enqueue(self.job.job_path,{},self.queue)
        self.lease=q.claim(self.queue,'invented-worker');self.answer=golden_output(self.job)
    def write(self,value):
        path=Path(self.job.output_path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
    def check(self):return q.self_check(self.queue,self.packet['job_id'],self.lease['token'])
    def finish(self):return q.finish(self.queue,self.packet['job_id'],self.lease['token'])
    def malformed(self):
        self.answer['citations'][0]['supports']=['outcome named in section 3']
        self.write(self.answer)
    def test_self_check_same_error_no_promotion_token_checks(self):
        self.malformed();result=self.check()
        self.assertIn('Purpose citation must identify supported fields',result['error'])
        self.assertTrue(result['correction_allowed']);self.assertFalse(Path(self.job.promote_path).exists())
        with self.assertRaises(ValueError):q.self_check(self.queue,self.packet['job_id'],'wrong')
        with self.assertRaises(ValueError):q.self_check(self.queue,self.packet['job_id'],self.lease['token'],now=self.lease['heartbeat_at']+q.LEASE_SECONDS)
    def test_first_finish_keeps_slot_then_corrected_finishes(self):
        self.malformed();original=Path(self.job.output_path).read_bytes()
        result=self.finish();self.assertEqual(result['status'],'correction_required')
        packet=Path(result['packet']);self.assertFalse((packet/'COMPLETION.json').exists())
        self.assertEqual((packet/'REJECTED_SUBMISSION.txt').read_bytes(),original)
        self.assertEqual(result,self.finish())
        self.write(golden_output(self.job));self.assertTrue(self.check()['validated'])
        self.assertTrue(Path(self.finish()).is_relative_to(self.queue/'done'))
        self.assertTrue(Backend(self.root).collect(self.job.job_path)['validated'])
    def test_second_bad_draft_reaches_bounded_replacement(self):
        self.malformed();self.assertTrue(self.check()['correction_allowed'])
        self.answer['citations'][0]['supports']=['still invalid'];self.write(self.answer)
        self.assertFalse(self.check()['correction_allowed'])
        self.assertTrue(Path(self.finish()).is_relative_to(self.queue/'done'))
        with self.assertRaises(ContractRejection) as cm:Backend(self.root).collect(self.job.job_path)
        self.assertEqual(cm.exception.kind,'SCHEMA_FORMAT')
    def test_third_draft_refused(self):
        self.malformed();self.check();self.answer['confirmation']='second draft';self.write(self.answer);self.check()
        self.write(golden_output(self.job))
        with self.assertRaises(ValueError):self.finish()
    def test_substantive_unresolved_not_correctable(self):
        self.malformed();self.answer.update(status='UNRESOLVED',unresolved_reason='invented evidence gap');self.write(self.answer)
        self.assertFalse(self.check()['correction_allowed'])
        self.assertTrue(Path(self.finish()).is_relative_to(self.queue/'done'))
    def test_changed_packet_rejected(self):
        self.write(self.answer);Path(self.lease['packet']).joinpath('PROMPT.md').write_text('changed')
        with self.assertRaises(ValueError):self.check()
    def test_cli_and_worker_instructions_expose_queue_self_check(self):
        self.write(self.answer)
        with patch('builtins.print'):
            self.assertEqual(q.main(['self-check','--root',str(self.queue),'--job-id',self.packet['job_id'],'--token',self.lease['token']]),0)
        self.assertIn('self-check --job-id',q.WORKER_INSTRUCTIONS)

class Recovery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.job=emit_fixture_job(self.root,'s0_purpose')
        self.pilot=Autopilot(q.root_for(self.root).parent,Backend(self.root))
        self.row=dict(design_id=self.job.design_id,attempt_id='test',work_dir=str(self.root/'work'))
        self.design=self.root/'design';self.ctx=SimpleNamespace(design_dir=self.design,card=SimpleNamespace(payload={'first_order':{'statute_refs':[]}}))
        self.pilot.backend.root=lambda row:self.design;self.pilot.backend.ctx=lambda row:self.ctx
        self.case=self.pilot.case_dir(self.row)
        ensure_once(self.case/'jobs/purpose.json',dict(jobs=[self.job.job_path]))
        answer=golden_output(self.job);answer['citations'][0]['supports']=['outcome named in section 3']
        ensure_once(Path(self.job.output_path),answer)
    def stop(self,action='purpose',reason='ValueError: Purpose citation must identify supported fields'):
        self.pilot.stop(self.row,action,reason)
    def test_exact_purpose_resume_and_one_replacement(self):
        self.stop();original=Path(self.job.output_path).read_bytes()
        self.assertTrue(resume_code_fix(self.pilot,self.row));self.assertIsNone(self.pilot.stop_record(self.row))
        self.assertFalse(resume_code_fix(self.pilot,self.row))
        self.assertIsNone(self.pilot.collect_submissions(self.row,'purpose',[self.job.job_path]))
        replacement=self.pilot.effective_job(self.row,self.job.job_path)
        self.assertNotEqual(replacement,self.job.job_path)
        self.pilot.collect_submissions(self.row,'purpose',[self.job.job_path])
        self.assertEqual(replacement,self.pilot.effective_job(self.row,self.job.job_path))
        self.assertEqual(original,Path(self.job.output_path).read_bytes())
        with self.assertRaises(ValueError):self.pilot.replace_submission(self.row,'purpose',ContractRejection(replacement,'SCHEMA_FORMAT','again'))
    def test_old_c62_supersession_preserved_and_new_stop_authoritative(self):
        old=ensure_once(self.case/'STOP.json',dict(reason='old'));proof=ensure_once(self.case/'old.json',{})
        marker=ensure_once(self.case/'STOP_SUPERSEDED_C62.json',dict(stop={'path':str(old),'sha256':sha(old)},evidence=[{'path':str(proof),'sha256':sha(proof)}]))
        before=marker.read_bytes();self.stop();self.assertTrue(resume_code_fix(self.pilot,self.row))
        self.assertEqual(marker.read_bytes(),before);self.stop()
        self.assertIsNotNone(self.pilot.stop_record(self.row));self.assertFalse(resume_code_fix(self.pilot,self.row))
    def test_f1_resume_without_inventory(self):
        self.stop('purpose-direction','ValueError: Purpose direction requires public source locators')
        self.assertTrue(resume_code_fix(self.pilot,self.row));self.assertIsNone(self.pilot.stop_record(self.row))
        self.assertEqual(read(self.case/'CODE_FIX_C84.json')['reused_jobs'],[])
    def test_f1_with_citations_not_the_empty_citation_defect(self):
        self.ctx.card.payload['first_order']['statute_refs']=[dict(source_ref='https://example.invalid',locator='s1')]
        self.stop('purpose-direction','ValueError: Purpose direction requires public source locators')
        self.assertFalse(resume_code_fix(self.pilot,self.row))
    def test_unrelated_or_frozen_stop_not_resumed(self):
        self.stop(reason='ValueError: Purpose citation must identify supported fields PLUS OTHER FAILURE')
        self.assertFalse(resume_code_fix(self.pilot,self.row))
        ensure_once(self.design/'FREEZE_PREP.json',{});self.assertFalse(resume_code_fix(self.pilot,self.row))
    def test_schema_classification_excludes_substantive_and_other_errors(self):
        exc=ValueError('Purpose citation must identify supported fields')
        self.assertEqual(rejection_kind(exc,{'status':'VERIFIED'}),'SCHEMA_FORMAT')
        self.assertIsNone(rejection_kind(exc,{'status':'UNRESOLVED'}))
        self.assertIsNone(rejection_kind(ValueError('Purpose requires located target and population support'),{}))

class DirectionTransport(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.ctx=_context(self.root,'transport_direction')
        self.ctx.card.payload['first_order']['statute_refs']=[dict(source_ref='https://example.invalid',locator='s1')]
        self.row=dict(design_id=self.ctx.card.design_id,attempt_id='test')
        self.pilot=Autopilot(q.root_for(self.root).parent,Backend(self.root),agent_backends='mixed')
        self.pilot.backend.ctx=lambda row:self.ctx;self.pilot.backend.root=lambda row:self.ctx.design_dir
        self.job=d.emit(self.ctx,d.ROLES[0],self.pilot.case_dir(self.row)/'purpose_direction_jobs')
        self.pilot.backend.global_running=lambda:0
        self.pilot.queue_reconciliations=lambda:None
    def test_expired_direction_records_missing_never_stop_on_repeat(self):
        queue=q.root_for(self.root);q.enqueue(self.job.job_path,self.row,queue)
        q.claim(queue,'worker',now=0);q.reap(queue,now=q.LEASE_SECONDS)
        q.claim(queue,'worker',now=q.LEASE_SECONDS);q.reap(queue,now=2*q.LEASE_SECONDS)
        q.claim(queue,'worker',now=2*q.LEASE_SECONDS);q.reap(queue,now=3*q.LEASE_SECONDS)
        for k in range(3,6):q.claim(queue,'worker',now=k*q.LEASE_SECONDS);q.reap(queue,now=(k+1)*q.LEASE_SECONDS)  # no output at all: six leases before the hub
        self.pilot.schedule();self.pilot.schedule()
        self.assertIsNone(self.pilot.stop_record(self.row))
        self.assertEqual(self.pilot.done(self.row,'purpose-direction')['field_status']['F1'],'FIELD_MISSING')
    def test_enqueue_failure_drops_remaining_f1_dispatches(self):
        peer=d.emit(self.ctx,d.ROLES[1],self.pilot.case_dir(self.row)/'purpose_direction_jobs')
        self.pilot.pending=[(self.row,self.job.job_path),(self.row,peer.job_path)]
        with patch.object(q,'enqueue',side_effect=ValueError('invented queue error')) as enqueue:
            self.pilot.schedule();self.assertEqual(enqueue.call_count,1)
        self.assertIsNone(self.pilot.stop_record(self.row))
        self.assertEqual(self.pilot.done(self.row,'purpose-direction')['reason'],'blind_coding_unavailable')
    def test_cached_verified_direction_reuses_accepted_bytes(self):
        from scripts.l2_pipeline.agents.collect import collect_job
        peer=d.emit(self.ctx,d.ROLES[1],self.pilot.case_dir(self.row)/'purpose_direction_jobs')
        for index,job in enumerate([self.job,peer]):
            value=golden_output(job);value['coder_id']='independent'+str(index)
            value['citations'][0].update(source_ref='https://example.invalid',locator='s1')
            ensure_once(Path(job.output_path),value);collect_job(job.job_path)
        value=d.resolve(self.ctx,[self.job.job_path,peer.job_path]);before=sha(self.ctx.design_dir/'PURPOSE_DIRECTION_C83.json')
        with patch.object(self.pilot,'collect_submissions',side_effect=AssertionError('cached')):
            self.assertEqual(d.action(self.pilot,self.row)['purpose_direction'],value['purpose_direction'])
        self.assertEqual(before,sha(self.ctx.design_dir/'PURPOSE_DIRECTION_C83.json'))

    def test_transport_recovery_uses_existing_missing_before_checkpoint(self):
        d.missing(self.ctx,'no_public_citation')
        before=sha(self.ctx.design_dir/'PURPOSE_DIRECTION_C83.json')
        self.assertTrue(d.transport_failure(self.pilot,self.row,self.job.job_path,'different transport reason'))
        self.assertEqual(self.pilot.done(self.row,'purpose-direction')['reason'],'no_public_citation')
        self.assertEqual(before,sha(self.ctx.design_dir/'PURPOSE_DIRECTION_C83.json'))
