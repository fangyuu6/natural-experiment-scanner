"""Invented-only final signature/verification with immutable SHOULD closures."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.l2_pipeline import registration as reg
from scripts.l2_pipeline.contracts import DesignCard
from scripts.l2_pipeline.freeze import verify
from scripts.l2_pipeline.stages.s6_execute import review_passed, sealed_review_evidence
from scripts.l2_pipeline.storage import sha
from scripts.l2_pipeline.tests import test_pipeline


class SealedShouldClosure(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        test_pipeline.FinalApprovalTests().make_bundle(self.root)
        # Reuse a fully frozen invented fixture; no real study signatures occur.
        (self.root/'APPROVAL_FINAL.json').unlink()
        self.candidate=sha(self.root/'FREEZE_FINAL.json')
        self.review=json.loads((self.root/'EXECUTION_REVIEW.json').read_text())
        self.finding=dict(id='S01',description='Carry fixture limitations to reporting',
            evidence='REVIEW.json retains the invented interpretation',impact='interpretation')
        self.review['should']=[self.finding]
        (self.root/'EXECUTION_REVIEW.json').write_text(json.dumps(self.review))
        self.resolution=dict(schema='l2_sealed_review_resolution_v1',design_id='invented',
            candidate_sha256=self.candidate,review_sha256=sha(self.root/'EXECUTION_REVIEW.json'),
            hub_signer='fixture_hub',outcome_associations_read=False,execution_authorized=False,
            resolutions=[dict(bucket='should',id='S01',finding_sha256=reg.finding_sha256(self.finding),
                status='declined',reason='Frozen interpretation retained; enhancement deferred.',
                evidence=[dict(path='REVIEW.json',sha256=sha(self.root/'REVIEW.json'))])])
        self.ctx=SimpleNamespace(design_dir=self.root,card=SimpleNamespace(design_id='invented',
            payload=dict(automation=dict(hub_signer='fixture_hub'))))
        self.args=SimpleNamespace(signer='fixture_hub',signed_at='2026-10-08T00:00:00Z')

    def put_closure(self,value=None):
        (self.root/'EXECUTION_REVIEW_RESOLUTION.json').write_text(json.dumps(value or self.resolution))

    def test_pass_with_should_requires_closure_before_signature(self):
        self.assertFalse(review_passed(self.review,self.candidate,'invented'))
        with self.assertRaises(OSError): reg.approve(self.ctx,self.args,'final')
        self.assertFalse((self.root/'APPROVAL_FINAL.json').exists())
        self.assertFalse((self.root/'EXECUTION_ONCE.json').exists())

    def test_missing_or_malformed_consolidated_fields_cannot_pass_unsigned_guard(self):
        for key in ('should','fix_cycles'):
            with self.subTest(missing=key):
                review=copy.deepcopy(self.review); review['should']=[]; review.pop(key)
                self.assertFalse(review_passed(review,self.candidate,'invented'))
                (self.root/'EXECUTION_REVIEW.json').write_text(json.dumps(review))
                with self.assertRaises(ValueError): reg.approve(self.ctx,self.args,'final')
                self.assertFalse((self.root/'APPROVAL_FINAL.json').exists())
        for key,value in (('should',None),('should',{}),('should',''),('fix_cycles',False),
                ('fix_cycles','0'),('fix_cycles',None)):
            with self.subTest(malformed=key,value=value):
                review=copy.deepcopy(self.review); review['should']=[]; review[key]=value
                self.assertFalse(review_passed(review,self.candidate,'invented'))
                (self.root/'EXECUTION_REVIEW.json').write_text(json.dumps(review))
                with self.assertRaises(ValueError): reg.approve(self.ctx,self.args,'final')
                self.assertFalse((self.root/'APPROVAL_FINAL.json').exists())

    def test_exact_evidence_closure_signs_and_binds_both_immutable_artifacts(self):
        review_bytes=(self.root/'EXECUTION_REVIEW.json').read_bytes()
        self.put_closure()
        self.assertTrue(reg.approve(self.ctx,self.args,'final')['ok'])
        approval=json.loads((self.root/'APPROVAL_FINAL.json').read_text())
        self.assertEqual(approval['review_sha256'],sha(self.root/'EXECUTION_REVIEW.json'))
        self.assertEqual(approval['review_resolution_sha256'],sha(self.root/'EXECUTION_REVIEW_RESOLUTION.json'))
        self.assertEqual(review_bytes,(self.root/'EXECUTION_REVIEW.json').read_bytes())
        self.assertFalse((self.root/'EXECUTION_ONCE.json').exists())
        changed=copy.deepcopy(self.resolution); changed['resolutions'][0]['reason']='changed closure'
        self.put_closure(changed)
        self.assertFalse(verify(self.root,'final','fixture_hub')['ok'])

    def test_stale_wrong_hub_or_unbound_closure_refuses(self):
        mutations=(lambda v:v.update(candidate_sha256='a'*64),lambda v:v.update(design_id='other'),
            lambda v:v.update(review_sha256='b'*64),lambda v:v.update(hub_signer='other'),
            lambda v:v.update(outcome_associations_read=True),lambda v:v.update(execution_authorized=True),
            lambda v:v.update(resolutions=[]),lambda v:v['resolutions'][0].update(finding_sha256='c'*64),
            lambda v:v['resolutions'][0].update(status='open'),lambda v:v['resolutions'][0].update(reason=' '),
            lambda v:v['resolutions'][0].update(evidence=[]),
            lambda v:v['resolutions'][0]['evidence'][0].update(path='../escape.json'),
            lambda v:v['resolutions'][0]['evidence'][0].update(sha256='d'*64))
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value=copy.deepcopy(self.resolution); mutation(value); self.put_closure(value)
                with self.assertRaises((ValueError,OSError)): reg.approve(self.ctx,self.args,'final')
                self.assertFalse((self.root/'APPROVAL_FINAL.json').exists())

    def test_closure_cannot_replace_independent_pass_or_close_must(self):
        self.put_closure()
        mutations=(lambda v:v.update(verdict='STOP'),lambda v:v.update(must=[self.finding]),
            lambda v:v.update(review_scope='draft_readiness'),lambda v:v.update(candidate_sha256='a'*64),
            lambda v:v.update(design_id='other'),lambda v:v.update(complete_sweeps=True),
            lambda v:v['independence_attestation'].update(wrote_fixes=True))
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                review=copy.deepcopy(self.review); mutation(review)
                (self.root/'EXECUTION_REVIEW.json').write_text(json.dumps(review))
                with self.assertRaises(ValueError): sealed_review_evidence(self.root,self.candidate,'invented','fixture_hub')
                self.assertFalse((self.root/'EXECUTION_ONCE.json').exists())

    def test_s6_uses_same_closure_and_still_requires_hub_approval(self):
        from scripts.l2_pipeline.stages import s6_execute
        card=DesignCard.from_mapping(dict(design_id='invented',version='v1',policy_family='fixture',
            mechanism='fixture',track='DiD',unit='state',outcome_family='employment',
            automation=dict(hub_signer='fixture_hub')),self.root/'CARD.json')
        ctx=SimpleNamespace(card=card,design_dir=self.root,read=lambda n:json.loads((self.root/n).read_text()))
        self.put_closure()
        with patch('scripts.l2_pipeline.snapshots.active', return_value={}), \
                patch('scripts.l2_pipeline.run2_preparation.effective_context',return_value=ctx), \
                patch.object(s6_execute,'prepare',side_effect=AssertionError('Valid closure missed')), \
                patch('scripts.l2_pipeline.execution.execute_once',side_effect=AssertionError('Unsigned execution')):
            result=s6_execute.run(ctx)
        self.assertEqual(result.disposition.value,'parked')
        self.assertIn('approval',result.reason.lower())
