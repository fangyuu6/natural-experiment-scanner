"""Synthetic tick-path closure, immutable evidence and consumed-slot contracts."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from scripts.l2_pipeline.autopilot import Autopilot,atomic,read
from scripts.l2_pipeline.tests.test_autopilot import MockAgents
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.registration import finding_sha256
from scripts.l2_pipeline.sealed_closure import accept,resume
from scripts.l2_pipeline.closure_evidence import snapshot,validate


class SealedDataClosure(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.repo=Path(tmp.name).resolve();self.reg=self.repo/'data/l2_pipeline/registry'
        self.backend=MockAgents(self.repo);self.p=Autopilot(self.reg,self.backend)
        self.row=dict(design_id='fixture',attempt_id='a',work_dir=str(self.repo/'work'),status='ACTIVE')
        self.root=self.backend.root(self.row);self.root.mkdir(parents=True)
        self.backend.ctx=lambda row:SimpleNamespace(design_dir=self.root,card=SimpleNamespace(payload=dict(automation=dict(hub_signer='hub'))))
        evidence=ensure_once(self.root/'SPEC.json',dict(invented=True))
        ensure_once(self.root/'FREEZE_FINAL.json',dict(files={'SPEC.json':dict(sha256=sha(evidence),role='spec')}))
        finding=dict(id='S1',description='Interpretation',evidence='Frozen specification',impact='interpretation')
        review=dict(design_id='fixture',candidate_sha256=sha(self.root/'FREEZE_FINAL.json'),review_scope='sealed_candidate',
            verdict='PASS',must=[],should=[finding],complete_sweeps=1,fix_cycles=0,
            independence_attestation=dict(wrote_implementation=False,wrote_fixes=False,real_outcome_associations_read=False))
        ensure_once(self.root/'EXECUTION_REVIEW.json',review)
        self.job=self.p.case_dir(self.row)/'review.md';self.job.parent.mkdir(parents=True);self.job.write_text('invented')
        ensure_once(self.job.with_suffix('.job.json'),dict(design_id='fixture',job_sha256=sha(self.job),promote_path=str(self.root/'EXECUTION_REVIEW.json'),contract=dict(candidate_sha256=review['candidate_sha256'])))
        self.inventory=dict(jobs=[str(self.job)])
        ensure_once(self.p.case_dir(self.row)/'jobs/sealed.json',self.inventory)
        self.closure=dict(schema='l2_sealed_review_resolution_v1',design_id='fixture',candidate_sha256=review['candidate_sha256'],
            review_sha256=sha(self.root/'EXECUTION_REVIEW.json'),hub_signer='hub',outcome_associations_read=False,execution_authorized=False,
            resolutions=[dict(bucket='should',id='S1',finding_sha256=finding_sha256(finding),status='declined',reason='Retain frozen interpretation',evidence=[dict(path='SPEC.json',sha256=sha(evidence))])])
        atomic(self.reg/'ACTIVE_DESIGNS.json',dict(designs=[self.row]))
        self.p.stop(self.row,'sealed','NEEDS_RULING: review SHOULD requires evidence-backed hub closure')

    def put(self,value=None):
        return ensure_once(self.root/'EXECUTION_REVIEW_RESOLUTION.json',value or self.closure)

    def test_tick_resumes_once_without_dispatch_or_execution(self):
        closure=self.put();before=closure.read_bytes()
        self.p.next_action=lambda row:'PARK'
        for _ in range(3):self.p.tick()
        self.assertIsNone(self.p.stop_record(self.row))
        self.assertEqual(self.p.done(self.row,'sealed'),self.inventory)
        self.assertEqual(read(self.reg/'HUB_QUEUE.json')['entries'][0]['status'],'SUPERSEDED_C70')
        self.assertEqual(before,closure.read_bytes());self.assertEqual(self.backend.launches,[])
        self.assertFalse((self.root/'EXECUTION_ONCE.json').exists())
        rows=[json.loads(x) for x in (self.reg/'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()]
        self.assertEqual(sum(r['decision']=='SUPERSEDED_C70' for r in rows),1)

    def test_review_gate_accepts_valid_hub_data_only(self):
        with self.assertRaisesRegex(ValueError,'NEEDS_RULING'):self.p.review_gate(self.row,[str(self.job)],'sealed_candidate')
        self.put();self.p.review_gate(self.row,[str(self.job)],'sealed_candidate')
        self.assertTrue((self.p.case_dir(self.row)/'SEALED_CLOSURE_ACCEPTED.json').exists())

    def test_invalid_signer_missing_finding_and_mutation_remain_stopped(self):
        for key,value in [('hub_signer','other'),('resolutions',[]),('candidate_sha256','0'*64),('execution_authorized',True)]:
            v=copy.deepcopy(self.closure);v[key]=value
            atomic(self.root/'EXECUTION_REVIEW_RESOLUTION.json',v)
            self.assertFalse(resume(self.p,self.row));self.assertIsNotNone(self.p.stop_record(self.row))
        atomic(self.root/'EXECUTION_REVIEW_RESOLUTION.json',self.closure)
        (self.root/'SPEC.json').write_text('{}')
        self.assertFalse(resume(self.p,self.row))

    def test_changed_accepted_closure_and_consumed_intent_refuse(self):
        self.put();accept(self.p,self.row,self.inventory['jobs'])
        value=copy.deepcopy(self.closure);value['resolutions'][0]['reason']='changed'
        atomic(self.root/'EXECUTION_REVIEW_RESOLUTION.json',value)
        self.assertFalse(resume(self.p,self.row))
        atomic(self.root/'EXECUTION_REVIEW_RESOLUTION.json',self.closure)
        ensure_once(self.p.case_dir(self.row)/'EXECUTE_INTENT.json',{})
        self.assertFalse(resume(self.p,self.row))

    def test_dry_tick_does_not_consume_closure(self):
        self.put();self.p.tick(dry_run=True)
        self.assertFalse((self.p.case_dir(self.row)/'SEALED_CLOSURE_ACCEPTED.json').exists())

    def test_restart_completes_queue_after_marker_without_reopening_slots(self):
        self.put()
        with patch('scripts.l2_pipeline.sealed_closure._complete',side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):resume(self.p,self.row)
        self.assertIsNone(self.p.stop_record(self.row))
        self.assertFalse(resume(self.p,self.row))
        self.assertEqual(read(self.reg/'HUB_QUEUE.json')['entries'][0]['status'],'SUPERSEDED_C70')
        self.p.stop(self.row,'execute','new unrelated failure')
        self.assertEqual(self.p.stop_record(self.row)['action'],'execute')
        self.assertFalse(resume(self.p,self.row))


class ImmutableEvidence(unittest.TestCase):
    def test_code_ruling_live_data_refused_copies_stable(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d).resolve()
            for relative in ('scripts/impl.py','docs/ruling.md','data/live.json'):
                source=repo/relative;source.parent.mkdir(parents=True,exist_ok=True);source.write_text('invented evidence')
                with self.assertRaises(ValueError):validate(dict(path=str(source),sha256=sha(source)),repo)
                pointer=snapshot(source,repo/'data/study')
                validate(pointer,repo);self.assertEqual(snapshot(source,repo/'data/study'),pointer)
                source.write_text('later mutable source');validate(pointer,repo)
                Path(pointer['path']).write_text('tamper')
                with self.assertRaises(ValueError):validate(pointer,repo)

    def test_frozen_code_and_symlink_are_not_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();path=ensure_once(root/'CODE.json',{})
            e=dict(path='CODE.json',sha256=sha(path))
            with self.assertRaises(ValueError):validate(e,root,root=root,frozen_files={'CODE.json':dict(sha256=sha(path),role='code')})
            link=root/'evidence';link.symlink_to(root,target_is_directory=True)
            with self.assertRaises(ValueError):validate(dict(path=str(link/'CODE.json'),sha256=sha(path)),root)

    def test_relative_frozen_evidence_accepts_canonical_trusted_root_alias(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d).resolve();root=base/'study';root.mkdir()
            path=ensure_once(root/'SPEC.json',dict(invented=True))
            alias=base/'root_alias';alias.symlink_to(root,target_is_directory=True)
            files={'SPEC.json':dict(sha256=sha(path),role='spec')}
            self.assertEqual(validate(dict(path='SPEC.json',sha256=sha(path)),alias,
                root=alias,frozen_files=files),path)
            link=root/'inner';link.symlink_to(root,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'symlink'):
                validate(dict(path='inner/SPEC.json',sha256=sha(path)),alias,root=alias,frozen_files=files)

    def test_parent_traversal_cannot_escape_repository(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d).resolve();repo=base/'repo';(repo/'data/evidence').mkdir(parents=True)
            outside=ensure_once(base/'outside.json',{})
            with self.assertRaisesRegex(ValueError,'escapes project'):
                validate(dict(path=str(repo/'data/evidence/../../../outside.json'),sha256=sha(outside)),repo)
