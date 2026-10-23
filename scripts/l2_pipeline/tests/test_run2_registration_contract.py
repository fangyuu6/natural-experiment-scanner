"""Invented-only handoff, immutable intake and evidence-backed draft closures."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.l2_pipeline import registration as reg
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.cli import preflight, run_stage
from scripts.l2_pipeline.contracts import StageContext, StageResult, Disposition
from scripts.l2_pipeline.run2_preparation import effective_context, prepared_payload
from scripts.l2_pipeline.stages.s6_execute import review_passed
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests.test_pipeline import card


class Run2RegistrationContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Path(self.tmp.name).resolve();self.source=self.repo/'intake.json'
        value=card();value['analysis'].update(min_pre_periods=3,min_controls=2,min_cohorts=1)
        value['outcome']['years']=[2000,2010];value['automation']={}
        ensure_once(self.source,value)
        self.ctx=StageContext(load_card(self.source),self.repo/'work',self.repo)
        self.root=self.ctx.design_dir;self.root.mkdir(parents=True)
        ensure_once(self.root/'CARD.json',value)
        ensure_once(self.root/'TREATMENT.json',dict(records=[],audit={}))
        payload=prepared_payload(value,[])
        ensure_once(self.root/'RUN2_PREPARATION.json',dict(schema_version='run2_preparation_v1',
            design_id=self.ctx.card.design_id,version=self.ctx.card.version,original_card_path=str(self.source),
            original_card_sha256=sha(self.source),treatment_sha256=sha(self.root/'TREATMENT.json'),
            prepared_payload=payload,outcome_association_calls=0,execution_authorized=False))
        self.spec=dict(design_id=self.ctx.card.design_id,track='DiD',population=payload['population'],
            outcome=payload['outcome'],analysis=payload['analysis'],support=dict(variants=[
                dict(variant='primary',cell_inventory=[dict(cohort=2005,period=2006,base_period=2004,treated=['CA','NY'],controls=['TX','WA'])])]))
        ensure_once(self.root/'SPEC.json',self.spec)
        self.old_spec=self.repo/'original_SPEC.json';ensure_once(self.old_spec,self.spec)
        self.review=dict(design_id=self.ctx.card.design_id,candidate_sha256=sha(self.old_spec),
            review_scope='draft_readiness',complete_sweeps=1,fix_cycles=0,verdict='STOP',
            must=[dict(id='M01',description='wrong mask',evidence='fixture',impact='incorrect_registered_number')],
            should=[dict(id='S01',description='interpret clock',evidence='fixture',impact='interpretation')],
            independence_attestation=dict(wrote_implementation=False,wrote_fixes=False,real_outcome_associations_read=False))
        ensure_once(self.root/'REVIEW_DRAFT_old.json',self.review)
        self.evidence=self.repo/'evidence/fix.txt';self.evidence.parent.mkdir();self.evidence.write_text('Verified invented mask and clock interpretation.\n')

    def closure(self):
        return dict(schema='l2_draft_resolution_v1',design_id=self.ctx.card.design_id,
            review_attempt='old',review_sha256=sha(self.root/'REVIEW_DRAFT_old.json'),
            source_spec=dict(path=str(self.old_spec),sha256=sha(self.old_spec)),
            target_spec_sha256=sha(self.root/'SPEC.json'),target_unchanged=True,hub_signer='hub',
            outcome_associations_read=False,execution_authorized=False,resolutions=[
                dict(bucket=bucket,id=f['id'],finding_sha256=reg.finding_sha256(f),status='resolved',
                    reason='Implemented and checked using invented data.',
                    evidence=[dict(path=str(self.evidence),sha256=sha(self.evidence))])
                for bucket in ('must','should') for f in self.review[bucket]])

    def test_scientific_payload_keeps_original_identity_and_hash(self):
        ctx=effective_context(self.ctx)
        self.assertEqual(ctx.card.path,self.source)
        self.assertEqual(ctx.card.payload['analysis']['multiplier_count'],4999)
        self.assertNotEqual(ctx.card.payload,self.ctx.card.payload)
        self.assertEqual(json.loads((self.root/'CARD.json').read_text()),self.ctx.card.payload)
        self.assertEqual(effective_context(ctx).card.payload,ctx.card.payload)

    def test_changed_intake_or_treatment_rejects_prepared_payload(self):
        (self.root/'TREATMENT.json').write_text('{"records":["changed"]}')
        with self.assertRaisesRegex(ValueError,'binding changed'): effective_context(self.ctx)

    def test_run_stage_uses_prepared_payload(self):
        result=StageResult('S2',self.ctx.card.design_id,Disposition.READY,'invented')
        with patch('scripts.l2_pipeline.cli.preflight'),patch('scripts.l2_pipeline.cli.importlib.import_module') as module:
            module.return_value.run.return_value=result
            self.assertEqual(run_stage(self.ctx,'S2'),result)
            actual=module.return_value.run.call_args.args[0]
            self.assertEqual(actual.card.payload['analysis']['multiplier_count'],4999)
            self.assertEqual(actual.card.path,self.source)

    def test_stop_requires_every_finding_and_current_hashed_evidence(self):
        value=self.closure()
        self.assertTrue(reg._validate_resolution(self.ctx,'old',value))
        for changed in (dict(value,resolutions=value['resolutions'][:1]),
                        dict(value,target_spec_sha256='0'*64),dict(value,target_unchanged=False)):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                reg._validate_resolution(self.ctx,'old',changed)
        changed=copy.deepcopy(value);changed['resolutions'][0]['status']='declined'
        with self.assertRaises(ValueError):reg._validate_resolution(self.ctx,'old',changed)
        changed=copy.deepcopy(value);changed['resolutions'][1]['finding_sha256']='0'*64
        with self.assertRaises(ValueError):reg._validate_resolution(self.ctx,'old',changed)
        self.evidence.write_text('changed')
        with self.assertRaisesRegex(ValueError,'evidence changed'):reg._validate_resolution(self.ctx,'old',value)

    def test_hub_closure_never_fabricates_independent_pass(self):
        original=(self.root/'REVIEW_DRAFT_old.json').read_bytes()
        resolution=self.repo/'resolution.json';ensure_once(resolution,self.closure())
        result=reg.resolve_draft(self.ctx,SimpleNamespace(signer='hub',resolution=resolution,review_attempt='old'))
        self.assertFalse(result['execution_authorized'])
        review=reg._review(self.ctx,'old')
        self.assertEqual(review['independent_verdict'],'STOP')
        self.assertNotIn('verdict',review)
        self.assertFalse(review_passed(review,sha(self.root/'SPEC.json'),self.ctx.card.design_id))
        self.assertEqual((self.root/'REVIEW_DRAFT_old.json').read_bytes(),original)

    def test_changed_target_requires_forecast_supersession(self):
        changed=copy.deepcopy(self.spec);changed['support']['variants'][0]['cell_inventory'].append(dict(cohort=2005,period=2007,base_period=2004,treated=['CA','NY'],controls=['TX','WA']))
        (self.root/'SPEC.json').write_text(json.dumps(changed))
        value=self.closure();value['target_unchanged']=False
        self.assertFalse(reg._validate_resolution(self.ctx,'old',value))

    def test_independent_pass_with_open_should_remains_blocked(self):
        review=dict(self.review,must=[],verdict='PASS',candidate_sha256=sha(self.root/'SPEC.json'),review_scope='sealed_candidate')
        self.assertFalse(review_passed(review,sha(self.root/'SPEC.json'),self.ctx.card.design_id))
        review['should']=[]
        self.assertTrue(review_passed(review,sha(self.root/'SPEC.json'),self.ctx.card.design_id))

    def test_prep_preserves_intake_and_binds_scientific_payload(self):
        original=(self.root/'CARD.json').read_bytes()
        ensure_once(self.root/'PREDICTIONS_LOCK.json',dict(personas=['invented'],design_id=self.ctx.card.design_id))
        args=SimpleNamespace(signer='hub',test_report=self.evidence,review_attempt='old')
        with patch.object(reg,'lock'),patch.object(reg,'_check_evidence',return_value={}),\
                patch.object(reg,'_review',return_value={}),patch.object(reg,'build_manifest'),\
                patch.object(reg,'verify',return_value=dict(ok=True)),patch.object(reg,'_inventory',return_value={}):
            reg.prep(effective_context(self.ctx),args,[self.ctx.card.design_id])
        self.assertEqual((self.root/'CARD.json').read_bytes(),original)
        self.assertEqual(json.loads((self.root/'SCIENTIFIC_CARD.json').read_text()),effective_context(self.ctx).card.payload)

    def test_archived_code_proof_does_not_excuse_legal_source_change(self):
        implementation=self.repo/'scripts/l2_pipeline/module.py';implementation.parent.mkdir(parents=True);implementation.write_text('old')
        archived=self.repo/'archive.py';archived.write_text('old')
        legal=self.repo/'legal.txt';legal.write_text('original law')
        ensure_once(self.root/'S1_IMPORT_PROVENANCE.json',dict(evidence=[
            dict(path=str(implementation.relative_to(self.repo)),sha256=sha(implementation)),
            dict(path=str(legal.relative_to(self.repo)),sha256=sha(legal))]))
        ensure_once(self.root/'S1_CODE_ARCHIVE.json',dict(files={str(implementation):dict(path=str(archived),sha256=sha(archived))}))
        implementation.write_text('new')
        preflight(self.ctx,'S0')
        legal.write_text('changed law')
        with self.assertRaisesRegex(ValueError,'source/coder'):preflight(self.ctx,'S0')


if __name__=='__main__':unittest.main()
