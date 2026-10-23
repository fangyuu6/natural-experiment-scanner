"""HUB_RULING_20261019_NOVEL_GATE Amendment 1 (second code set): a NOVEL-stopped sealed review resumes on a valid hub closure;
the fallback signer of the automatic draft closures equals the registration signer. Invented inputs only."""
import unittest
from unittest.mock import patch
from scripts.l2_pipeline.autopilot import Autopilot,atomic,read
from scripts.l2_pipeline.sealed_closure import resume
from scripts.l2_pipeline.c54 import close_should as c54_close_should
from scripts.l2_pipeline.maintenance import close_should as maintenance_close_should
from scripts.l2_pipeline.registration import _review
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.tests import test_c70_closure as _c70, test_c54 as _c54

NOVEL='ValueError: NEEDS_RULING: NOVEL/science finding requires explicit validated hub closure'
STANDING='hub autopilot (standing authorization 2026-10-10)'   # what autopilot.py writes into REGISTRATION.json when the card has none


def stop_as(reason):
    original=Autopilot.stop
    return patch.object(Autopilot,'stop',lambda self,row,action,_reason:original(self,row,action,reason))


class ResumeNovelSealedStop(_c70.SealedDataClosure):
    """Every sealed-closure contract of test_c70_closure holds when the retained stop carries the NOVEL text."""
    def setUp(self):
        with stop_as(NOVEL):super().setUp()


class UnrelatedSealedStopStays(unittest.TestCase):
    put=_c70.SealedDataClosure.put
    def setUp(self):
        with stop_as('ValueError: Run failure before terminal: invented'):_c70.SealedDataClosure.setUp(self)

    def test_valid_closure_does_not_lift_an_unrelated_stop(self):
        self.put();self.assertFalse(resume(self.p,self.row));self.assertIsNotNone(self.p.stop_record(self.row))


class NovelSealedStopWithoutClosureStays(unittest.TestCase):
    def setUp(self):
        with stop_as(NOVEL):_c70.SealedDataClosure.setUp(self)

    def test_no_closure_no_resume(self):
        self.assertFalse(resume(self.p,self.row));self.assertIsNotNone(self.p.stop_record(self.row))
        self.assertFalse((self.p.case_dir(self.row)/'SEALED_CLOSURE_ACCEPTED.json').exists())


class FallbackSigner(unittest.TestCase):
    setUp=_c54.C54Tests.setUp
    ctx=_c54.C54Tests.ctx
    review_fixture=_c54.C54Tests.review_fixture

    def test_c54_closure_signer_equals_the_registration_signer(self):
        ctx,attempt,job,path=self.review_fixture()
        self.p.stop(self.row,'draft','review SHOULD')
        self.assertTrue(c54_close_should(self.p,self.row,str(job)))
        self.assertEqual(read(self.root/('DRAFT_RESOLUTION_'+attempt+'.json'))['hub_signer'],STANDING)

    def test_maintenance_closure_signer_equals_the_registration_signer(self):
        ctx=self.ctx();self.p.backend.ctx=lambda row:ctx;self.p.backend.root=lambda row:self.root
        auth=self.repo/'docs/plans/l2-pipeline/HUB_RULING_C54_CLOSURES.md';auth.parent.mkdir(parents=True);auth.write_text('invented C54 authority')
        evidence=self.repo/'data/l2_pipeline/EVIDENCE.json';ensure_once(evidence,dict(invented=True))
        attempt='custom_maintenance';job=self.repo/'data/l2_pipeline/job.md';job.write_text('invented immutable packet')
        path=self.root/('REVIEW_DRAFT_'+attempt+'.json')
        finding=dict(id='S01',description='Sensitivity limitations documentation gap',impact='No number is affected',
            evidence='see data/l2_pipeline/EVIDENCE.json')
        ensure_once(path,dict(design_id='invented',candidate_sha256=sha(self.root/'SPEC.json'),review_scope='draft_readiness',
            complete_sweeps=1,fix_cycles=0,verdict='PASS',must=[],should=[finding],independence_attestation=dict(
                wrote_implementation=False,wrote_fixes=False,real_outcome_associations_read=False)))
        ensure_once(job.with_suffix('.job.json'),dict(design_id='invented',design_version='v1',job_sha256=sha(job),
            promote_path=str(path),contract=dict(attempt_id=attempt,candidate_sha256=sha(self.root/'SPEC.json'))))
        ensure_once(self.p.case_dir(self.row)/'jobs/draft.json',dict(jobs=[str(job)]))
        self.assertTrue(maintenance_close_should(self.p,self.row,str(job)))
        self.assertEqual(read(self.root/('DRAFT_RESOLUTION_'+attempt+'.json'))['hub_signer'],STANDING)


class SingleReviewSignerGuard(unittest.TestCase):
    setUp=_c54.C54Tests.setUp
    ctx=_c54.C54Tests.ctx
    review_fixture=_c54.C54Tests.review_fixture

    def closed(self):
        ctx,attempt,job,path=self.review_fixture()
        self.p.stop(self.row,'draft','review SHOULD')
        self.assertTrue(c54_close_should(self.p,self.row,str(job)))
        return ctx,attempt,self.root/('DRAFT_RESOLUTION_'+attempt+'.json')

    def test_r2_closure_with_another_signer_is_refused_before_prep(self):
        ctx,attempt,closure=self.closed();value=read(closure)
        with patch('scripts.l2_pipeline.rules.at_least',return_value=True):
            self.assertEqual(_review(ctx,attempt)['status'],'RESOLVED_SINGLE_REVIEW')
            self.assertEqual(_review(ctx,attempt,signer=STANDING)['status'],'RESOLVED_SINGLE_REVIEW')
            with self.assertRaisesRegex(ValueError,'signer differs'):_review(ctx,attempt,signer='another registered signer')
            atomic(closure,dict(value,hub_signer='hub (Claude coordinator) under a ruling'))
            with self.assertRaisesRegex(ValueError,'signer differs'):_review(ctx,attempt)

    def test_without_r2_any_non_empty_closure_signer_still_passes(self):
        ctx,attempt,closure=self.closed();value=read(closure)
        atomic(closure,dict(value,hub_signer='hub (Claude coordinator) under a ruling'))
        self.assertEqual(_review(ctx,attempt)['status'],'HUB_RESOLVED_PENDING_SEALED_REVIEW')
