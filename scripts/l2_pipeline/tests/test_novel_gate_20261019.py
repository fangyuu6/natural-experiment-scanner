"""HUB_RULING_20261019_NOVEL_GATE: a valid hub closure resumes a NOVEL-stopped draft; class-table closure needs no card signer. Invented inputs only."""
import unittest
from unittest.mock import patch
from scripts.l2_pipeline.autopilot import read,atomic
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.population_resolution import bind
from scripts.l2_pipeline.registration import finding_sha256
from scripts.l2_pipeline.tests import test_c54 as _c54, test_c76b2_class_export as _cls

NOVEL='ValueError: NEEDS_RULING: NOVEL/science finding requires explicit validated hub closure'


class ResumeNovelStop(unittest.TestCase):
    setUp=_c54.C54Tests.setUp
    ledger=_c54.C54Tests.ledger
    ctx=_c54.C54Tests.ctx
    review_fixture=_c54.C54Tests.review_fixture

    def stopped(self,write_closure=True):
        ctx,attempt,job,path=self.review_fixture('Unlisted SHOULD requiring a hub judgment')
        evidence=ensure_once(self.root/'evidence/EVIDENCE.json',dict(invented=True))
        closure=None
        if write_closure:
            value=dict(schema='l2_draft_resolution_v1',design_id=self.row['design_id'],review_attempt=attempt,
                review_sha256=sha(path),source_spec=bind(self.root/'SPEC.json'),target_spec_sha256=sha(self.root/'SPEC.json'),
                target_unchanged=True,hub_signer='invented hub',execution_authorized=False,outcome_associations_read=False,
                resolutions=[dict(bucket='should',id=f['id'],finding_sha256=finding_sha256(f),status='declined',
                    reason='Ruling class rule',evidence=[bind(evidence)]) for f in read(path)['should']])
            closure=ensure_once(self.root/('DRAFT_RESOLUTION_'+attempt+'.json'),value)
        atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=[self.row]))
        self.p.stop(self.row,'draft',NOVEL)
        return job,path,closure,evidence

    def test_reason_carries_the_review_should_tag(self):
        from scripts.l2_pipeline.maintenance import reason_tags
        self.assertIn('review_should',reason_tags(NOVEL))
        self.assertNotIn('review_should',reason_tags('ValueError: NEEDS_RULING: something else entirely'))

    def test_valid_hub_closure_supersedes_the_novel_stop_once(self):
        from scripts.l2_pipeline.maintenance import resume_should
        job,path,closure,evidence=self.stopped();original=closure.read_bytes()
        self.assertTrue(resume_should(self.p,self.row));self.assertFalse(resume_should(self.p,self.row))
        self.assertIsNone(self.p.stop_record(self.row));self.assertTrue(self.p.checkpoint(self.row,'draft').exists())
        self.assertEqual(closure.read_bytes(),original)
        self.assertEqual(read(self.registry/'HUB_QUEUE.json')['entries'][0]['status'],'SUPERSEDED_C62')

    def test_novel_stop_without_closure_stays_a_stop(self):
        from scripts.l2_pipeline.maintenance import resume_should
        self.stopped(write_closure=False)
        self.assertFalse(resume_should(self.p,self.row))
        self.assertIsNotNone(self.p.stop_record(self.row));self.assertFalse(self.p.checkpoint(self.row,'draft').exists())

    def test_changed_closure_or_evidence_fails_closed(self):
        from scripts.l2_pipeline.maintenance import resume_should
        job,path,closure,evidence=self.stopped();original=closure.read_bytes()
        value=read(closure);value['resolutions']=[];atomic(closure,value)
        self.assertFalse(resume_should(self.p,self.row));self.assertIsNotNone(self.p.stop_record(self.row))
        closure.write_bytes(original);evidence.write_text('{}')
        self.assertFalse(resume_should(self.p,self.row));self.assertIsNotNone(self.p.stop_record(self.row))


class ClassTableSigner(unittest.TestCase):
    setUp=_cls.Contracts.setUp
    review=_cls.Contracts.review

    def test_known_review_closes_when_the_card_has_no_signer(self):
        path,scope=self.review()
        with patch('scripts.l2_pipeline.registration.hub_signer',return_value=''),patch('scripts.l2_pipeline.c54.draft_identity',return_value=('a',path)),patch.object(_cls.c,'selected',return_value=False):
            self.assertTrue(_cls.c.accept(self.p,self.row,self.job,scope))
        closure=_cls.c.read(self.root/'DRAFT_RESOLUTION_a.json')
        self.assertEqual(closure['hub_signer'],'hub autopilot (standing authorization 2026-10-10)')
        self.assertTrue(closure['auto_closed'])

    def test_novel_review_still_escalates_without_signer(self):
        finding=dict(self.finding,class_issue=dict(kind='NOVEL'))
        path,scope=self.review(finding=finding)
        with patch('scripts.l2_pipeline.registration.hub_signer',return_value=''):
            self.assertFalse(_cls.c.accept(self.p,self.row,self.job,scope))
        self.assertFalse(list(self.root.glob('*RESOLUTION*')))


if __name__=='__main__':
    unittest.main()
