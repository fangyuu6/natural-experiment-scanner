"""Synthetic V7 exposure contracts; no downloads, dispatch or real outcomes."""
from __future__ import annotations
import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.l2_pipeline.agents import emit_job, collect_job, load_job
from scripts.l2_pipeline.agents.collect import validate_blindness
from scripts.l2_pipeline.contracts import DesignCard, StageContext
from scripts.l2_pipeline.predictions import lock_predictions
from scripts.l2_pipeline.storage import sha, write_once
from scripts.l2_pipeline.tests import test_agents as agent_fixtures
from scripts.l2_pipeline.tests.test_pipeline import tuple_row
from scripts.l2_pipeline.tests import test_literature_records as literature_fixtures

REPO=agent_fixtures.REPO


def attestation(published=True):
    return dict(other_submissions_read=False, our_outcome_data_read=False,
                our_estimates_read=False, published_results_read=published)


class V7BlindnessTests(unittest.TestCase):
    setUp = agent_fixtures.AgentTests.setUp
    tearDown = agent_fixtures.AgentTests.tearDown
    put = agent_fixtures.AgentTests.put
    forecast = agent_fixtures.AgentTests.forecast

    def literature_context(self):
        value=copy.deepcopy(self.ctx.card.payload)
        value['treatment']['source_type']='published_did_study'
        return StageContext(DesignCard.from_mapping(value,self.ctx.card.path),self.ctx.workspace,REPO)

    def emit(self,stage,role,**kwargs):
        return emit_job(self.literature_context(),stage,role,jobs_root=self.jobs,**kwargs)

    def test_v7_templates_and_contracts_for_coder_and_forecaster(self):
        for stage,role,kind in [('S1','A','coder'),('S5','Card','persona')]:
            with self.subTest(role=role):
                job=self.emit(stage,role)
                self.assertEqual(job.contract['source_type'],'published_did_study')
                self.assertEqual(job.contract['blindness_scope'],'our_outcomes_and_estimates_v7')
                self.assertEqual(Path(job.template_path).parts[-2:],('v7',kind+'.md'))
                text=Path(job.job_path).read_text()
                self.assertIn('MAY read the published paper',text)
                self.assertIn('our_outcome_data_read',text)
                self.assertIn('our_estimates_read',text)
                self.assertNotIn('"outcome_associations_read":false',text)
                self.assertEqual(load_job(Path(job.job_path)),job)

    def test_coder_public_results_accepted_private_data_and_peers_rejected(self):
        job=self.emit('S1','A')
        value=dict(design_id=job.design_id,role=job.role,source_sha256=job.contract['source_sha256'],
                   records=[tuple_row(str(i)) for i in range(10)],blindness_attestation=attestation())
        for key in ('other_submissions_read','our_outcome_data_read','our_estimates_read'):
            contaminated=copy.deepcopy(value);contaminated['blindness_attestation'][key]=True
            with self.assertRaises(ValueError): validate_blindness(job,contaminated)
            contaminated['blindness_attestation'][key]=0
            with self.assertRaises(ValueError): validate_blindness(job,contaminated)
        self.put(job,value);before=Path(job.output_path).read_bytes()
        receipt=collect_job(job.job_path)
        self.assertTrue(receipt['validated'])
        self.assertTrue(receipt['v7_blindness_validation']['published_results_read'])
        self.assertEqual(Path(job.output_path).read_bytes(),before)

    def test_auditor_uses_v7_template_and_keeps_original_hash(self):
        from scripts.l2_pipeline.stages.s1_treatment import audit_sample
        rows=[tuple_row(str(i)) for i in range(10)];paths=[]
        for role in ('A','B'):
            p=self.ctx.design_dir/'agents'/role/'submission.json';write_once(p,dict(records=rows));paths.append(p)
        write_once(self.ctx.design_dir/'CODING_LOCK.json',dict(files={str(p):sha(p) for p in paths}))
        write_once(self.ctx.design_dir/'AUDIT_SELECTION.json',dict(seed=42,sample=audit_sample(rows,42),source_sha256=sha(self.root/'source.json')))
        job=self.emit('S1','auditor')
        self.assertEqual(Path(job.template_path).parts[-2:],('v7','auditor.md'))
        value=dict(design_id=job.design_id,role=job.role,source_sha256=job.contract['source_sha256'],
                   selection_sha256=job.contract['selection_sha256'],blindness_attestation=attestation(),
                   checks=[dict(unit=str(i),status='match',official_locator='Synthetic official source') for i in range(10)])
        self.put(job,value);before=sha(Path(job.output_path));receipt=collect_job(job.job_path)
        self.assertEqual(receipt['original_sha256'],before)
        self.assertTrue(json.loads(Path(job.promote_path).read_text())['audit']['passed'])

    def test_forecast_records_published_knowledge_and_lock_validates_it(self):
        job=self.emit('S5','Card');value=self.forecast(job)
        value.update(blindness_attestation=attestation(),published_estimate_known=True)
        self.put(job,value);before=Path(job.output_path).read_bytes()
        collect_job(job.job_path);normalized=json.loads(Path(job.promote_path).read_text())
        self.assertEqual(normalized['published_estimate_knowledge'],'published estimate known')
        self.assertEqual(Path(job.output_path).read_bytes(),before)
        lock_predictions(self.ctx.design_dir,['Card'],job.design_id,job.contract['spec_sha256'])
        normalized['published_estimate_known']=False
        Path(job.promote_path).write_text(json.dumps(normalized))
        with self.assertRaisesRegex(ValueError,'blindness'):lock_predictions(self.ctx.design_dir,['Card'],job.design_id,job.contract['spec_sha256'])

    def test_forecast_missing_knowledge_and_contradictory_disclosure_rejected(self):
        job=self.emit('S5','Card');value=self.forecast(job)
        value.update(blindness_attestation=attestation())
        self.put(job,value)
        with self.assertRaisesRegex(ValueError,'knowledge disclosure'):collect_job(job.job_path)
        value['published_estimate_known']=False;Path(job.output_path).write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'contradicts'):collect_job(job.job_path)

    def test_legacy_literature_requires_explicit_public_exposure_private_nonexposure(self):
        job=SimpleNamespace(contract=dict(source_type='published_did_study'))
        old=dict(blindness_attestation=dict(other_submissions_read=False,outcome_associations_read=True),
                 blindness_details=dict(outcome_files_read=False,estimates_or_frozen_results_files_read=False,
                    outcomes_fitted=False,incidental_public_search_snippet_exposure=True,
                    deviation='Published-study estimates appeared in public search snippets'))
        self.assertTrue(validate_blindness(job,old)['published_results_read'])
        bad=copy.deepcopy(old);bad['blindness_details']['outcome_files_read']=True
        with self.assertRaises(ValueError):validate_blindness(job,bad)
        bad=copy.deepcopy(old);bad.pop('blindness_details')
        with self.assertRaises(ValueError):validate_blindness(job,bad)
        old.pop('blindness_details');old['blindness_limitation']=dict(project_outcome_files_read=False,
            outcome_models_fitted=False,reason='Published outcome summaries incidentally exposed',
            disclosure='Public tool results, no outcome estimates reproduced')
        self.assertTrue(validate_blindness(job,old)['published_results_read'])
        bad=copy.deepcopy(old);bad['blindness_limitation']['outcome_models_fitted']=True
        with self.assertRaises(ValueError):validate_blindness(job,bad)
        old['blindness_attestation']['outcome_associations_read']=False
        self.assertFalse(validate_blindness(job,old)['published_results_read'])

    def test_statutory_roles_keep_original_strict_scope(self):
        job=emit_job(self.ctx,'S5','Card',jobs_root=self.jobs)
        self.assertNotIn('source_type',job.contract)
        value=self.forecast(job);value['blindness_attestation']['outcome_associations_read']=True
        with self.assertRaises(ValueError):validate_blindness(job,value)
        value['blindness_attestation']=attestation()
        with self.assertRaises(ValueError):validate_blindness(job,value)

    def test_new_v7_job_cannot_submit_ambiguous_legacy_attestation(self):
        job=self.emit('S5','Card')
        with self.assertRaisesRegex(ValueError,'Missing V7'):validate_blindness(job,self.forecast(job))


class V7InflightLiteratureTests(unittest.TestCase):
    setUp=literature_fixtures.LiteratureRecordTests.setUp
    tearDown=literature_fixtures.LiteratureRecordTests.tearDown

    def test_old_s0l_output_collects_v7_without_rewriting_packet(self):
        value=literature_fixtures.answer(self.job);self.assertNotIn('blindness_attestation',value)
        write_once(Path(self.job.output_path),value)
        original=sha(Path(self.job.output_path));prompt=sha(Path(self.job.job_path))
        receipt=collect_job(self.job.job_path)
        self.assertEqual(receipt['collection_rule'],'HUB_RULING_V7_LITERATURE_BLINDNESS')
        record=json.loads(Path(self.job.promote_path).read_text())
        self.assertTrue(record['published_estimate_known'])
        self.assertEqual(original,sha(Path(self.job.output_path)))
        self.assertEqual(prompt,sha(Path(self.job.job_path)))
        self.assertEqual(receipt,collect_job(self.job.job_path))

    def test_s0l_explicit_project_data_exposure_fails(self):
        from scripts.l2_pipeline.agents.literature import validate_literature_output
        for field in ('our_outcome_data_read','our_estimates_read'):
            value=literature_fixtures.answer(self.job);value[field]=True
            with self.assertRaises(ValueError):validate_literature_output(self.job,value)


if __name__=='__main__':unittest.main()
