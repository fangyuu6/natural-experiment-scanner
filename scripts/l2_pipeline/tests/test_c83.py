"""Invented, outcome-free C81/F1/F6/sidecar contracts."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts.l2_pipeline import c83_history as h,c83_l3 as l3
from scripts.l2_pipeline.agents import purpose_direction as d
from scripts.l2_pipeline.agents.contract_tests import golden_output,check_job_types
from scripts.l2_pipeline.agents.contract_tests.packets import emit_fixture_job,_context
from scripts.l2_pipeline.agents.jobs import load_job
from scripts.l2_pipeline.agents.collect import collect_job
from scripts.l2_pipeline.agents.sonnet_queue import route
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.c42 import Park

class History(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        self.job=emit_fixture_job(self.root,'s1_official_history_v9');self.original=golden_output(self.job)
        self.original['records'][0]['status']=None;self.original['records'][0]['start_period']=None
        self.original['collection_complete']=False
        ensure_once(Path(self.job.output_path),self.original)
        class Backend:
            def root(s,row):return self.root/'design'
            def job_state(s,path):return 'done' if Path(load_job(Path(path)).output_path).exists() else 'pending'
            def collect(s,path):return collect_job(path)
        self.pilot=SimpleNamespace(registry=self.root/'data/l2_pipeline/registry',backend=Backend(),pending=[],ledger=lambda *a,**k:None)
        self.row=dict(design_id=self.job.design_id)
    def emitted(self):
        self.assertFalse(h.complete_history(self.pilot,self.row,self.job.job_path))
        return load_job(Path(self.pilot.pending[-1][1]))
    def test_one_gap_role_blind_packet_and_idempotence(self):
        job=self.emitted();before=Path(self.job.output_path).read_bytes()
        self.assertEqual(job.role,'A');self.assertEqual(job.contract['manual']['universe'],['U0'])
        self.assertEqual(route(job.design_id,job.role),'codex')
        self.assertNotIn('records',job.contract);self.assertNotIn('adoption_years',Path(job.job_path).read_text())
        self.assertFalse(h.complete_history(self.pilot,self.row,self.job.job_path))
        self.assertEqual({p for r,p in self.pilot.pending},{job.job_path})
        self.assertEqual(Path(self.job.output_path).read_bytes(),before)
    def test_merge_collect_and_original_bytes_preserved(self):
        job=self.emitted();answer=golden_output(job)
        for s in answer['sources']:s['source_id']='c81_'+s['source_id']
        for r in answer['records']:
            r['source_id']='c81_'+r['source_id']
            for ref in r['evidence_refs']:ref['source_id']='c81_'+ref['source_id']
        ensure_once(Path(job.output_path),answer)
        before=Path(self.job.output_path).read_bytes()
        self.assertTrue(h.complete_history(self.pilot,self.row,self.job.job_path))
        self.assertTrue(h.complete_history(self.pilot,self.row,self.job.job_path))
        merged=h.completed_value(self.job,self.original)
        self.assertEqual(merged['records'][1:],self.original['records'][1:])
        self.assertEqual(Path(self.job.output_path).read_bytes(),before)
        self.assertTrue(collect_job(self.job.job_path)['validated'])
    def test_second_incomplete_is_final_and_never_replacement(self):
        job=self.emitted();answer=golden_output(job);answer['collection_complete']=False
        ensure_once(Path(job.output_path),answer)
        with self.assertRaises(Park) as cm:h.complete_history(self.pilot,self.row,self.job.job_path)
        self.assertTrue(cm.exception.metrics['final'])
        self.assertEqual(len(list(h.slot(self.pilot,self.row).rglob('*.job.json'))),1)
    def test_invalid_completion_is_final(self):
        job=self.emitted();ensure_once(Path(job.output_path),{'records':[],'collection_complete':True})
        with self.assertRaises(Park) as cm:h.complete_history(self.pilot,self.row,self.job.job_path)
        self.assertTrue(cm.exception.metrics['final'])
    def test_omitted_units_are_gaps(self):
        from scripts.l2_pipeline.c59_queue import require_complete_history
        v=copy.deepcopy(self.original);v['records']=v['records'][1:];v['collection_complete']=True
        self.assertEqual(h.gaps(self.job,v),['U0'])
        with self.assertRaises(Park):require_complete_history(self.job,v)
    def test_raw_record_preserves_crlf_spacing_unicode(self):
        original=self.root/'raw.json';completion=self.root/'fresh.json'
        kept=b'{"unit":"B",\r\n  "status":"treated", "label":"\xe5\xb7\x9e"}'
        original.write_bytes(b'{"sources":[],"records":[{"unit":"A","status":null},'+kept+b']}')
        completion.write_bytes(b'{"sources":[],"records":[{"unit":"A","status":"unknown"}]}')
        merged=h.merge(original,completion,['A'])
        self.assertIn(kept,merged);self.assertEqual(json.loads(merged)['records'][0]['status'],'unknown')
    def test_merge_rejects_classified_overwrite_and_source_collision(self):
        other=self.root/'other.json';ensure_once(other,dict(sources=[],records=[dict(unit='U1',status='treated')]))
        with self.assertRaises(ValueError):h.merge(self.job.output_path,other,['U1'])

class Direction(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        self.ctx=_context(self.root,'s0_direction')
        ensure_once(self.ctx.design_dir/'PURPOSE_VERIFICATION.json',dict(confirmation='forbidden literature sign',citations=[dict(source_ref='https://example.invalid/statute',locator='section 1',source_type='statute',supports=['target_outcome'],estimate=99)]))
    def output(self,job,sign,worker):
        value=golden_output(job);value.update(purpose_direction=sign,coder_id=worker)
        ensure_once(Path(job.output_path),value);collect_job(job.job_path)
    def test_jobs_blind_sonnet_and_agreement(self):
        jobs=[d.emit(self.ctx,r,self.root/'jobs') for r in d.ROLES[:2]]
        for job in jobs:
            check_job_types([job]);self.assertEqual(route(job.design_id,job.role),'sonnet_queue')
            self.assertNotIn('forbidden literature sign',Path(job.job_path).read_text());self.assertNotIn('estimate',json.dumps(job.contract))
        for i,j in enumerate(jobs):self.output(j,-1,'worker'+str(i))
        resolved=d.resolve(self.ctx,[j.job_path for j in jobs]);self.assertEqual(resolved['purpose_direction'],-1)
        self.assertEqual(len(resolved['purpose_direction_evidence']['codings']),2)
    def test_disagreement_third_is_independent_and_blind(self):
        jobs=[d.emit(self.ctx,r,self.root/'jobs') for r in d.ROLES]
        self.output(jobs[0],1,'a');self.output(jobs[1],-1,'b')
        self.assertIsNone(d.resolve(self.ctx,[j.job_path for j in jobs[:2]]))
        self.assertEqual(jobs[0].contract['citations'],jobs[2].contract['citations'])
        self.assertNotIn('codings',jobs[2].contract)
        self.output(jobs[2],0,'c');self.assertEqual(d.resolve(self.ctx,[j.job_path for j in jobs])['purpose_direction'],0)
    def test_duplicate_author_rejected(self):
        jobs=[d.emit(self.ctx,r,self.root/'jobs') for r in d.ROLES[:2]]
        for job in jobs:self.output(job,1,'same')
        with self.assertRaises(ValueError):d.resolve(self.ctx,[j.job_path for j in jobs])
    def test_queue_claim_independence_survives_expiry_and_lease_provenance(self):
        from scripts.l2_pipeline.agents.sonnet_queue import enqueue,claim,reap
        jobs=[d.emit(self.ctx,r,self.root/'jobs') for r in d.ROLES[:2]]
        queue=self.root/'queue'
        for job in jobs:enqueue(job.job_path,{},queue)
        self.assertIsNotNone(claim(queue,'worker',now=1))
        self.assertIsNone(claim(queue,'worker',now=2))
        reap(queue,now=10000)
        # Same author can reclaim its own role, never the independent role.
        self.assertIsNotNone(claim(queue,'worker',now=10001))
        self.assertIsNone(claim(queue,'worker',now=10002))
        self.assertIsNotNone(claim(queue,'other',now=10003))
    def test_existing_specs_unchanged_supplement_frozen_hash(self):
        jobs=[d.emit(self.ctx,r,self.root/'jobs') for r in d.ROLES[:2]]
        for i,job in enumerate(jobs):self.output(job,-1,str(i))
        d.resolve(self.ctx,[j.job_path for j in jobs])
        old=ensure_once(self.ctx.design_dir/'SPEC_ADDENDUM_C64.json',dict(first_order=dict(purpose_direction=None)))
        digest=sha(old);specdigest=sha(self.ctx.design_dir/'SPEC.json')
        path=l3.retain(self.ctx);self.assertEqual(sha(old),digest);self.assertEqual(sha(self.ctx.design_dir/'SPEC.json'),specdigest)
        data=json.loads(path.read_text());self.assertEqual(data['first_order']['purpose_direction'],-1)
        self.assertEqual(data['field_status']['F6'],'FIELD_MISSING');self.assertTrue(data['geography']['reasons'])
        ensure_once(self.ctx.design_dir/'FREEZE_PREP.json',dict(files={l3.SPEC_NAME:dict(sha256=sha(path))}))
        self.assertEqual(l3.frozen(self.ctx.design_dir),data);self.assertIsNone(l3.retain(self.ctx))
        path.write_text('{}')
        with self.assertRaises(ValueError):l3.frozen(self.ctx.design_dir)

class Metadata(unittest.TestCase):
    def test_sidecar_projection_and_rebuild_never_opens_outcomes(self):
        from scripts.l2_pipeline.connectors.support_repair import rebuild,project
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'support.json';target=root/'fixed.json'
            value=dict(outcome_values_in_sidecar=False,artifact_path='must-not-open.json',metadata=[dict(unit='A',year=2000,available=True,annual_usable=True,linkage_certified=True,value=9,population=10,count=2)])
            ensure_once(source,value);receipt=rebuild(source,target)
            self.assertEqual(receipt['outcome_artifacts_opened'],0);self.assertEqual(receipt['removed_fields'],['count','population','value'])
            self.assertEqual(json.loads(target.read_text()),project(value))
            self.assertTrue(rebuild(target,root/'same.json')['byte_identical'])
    def test_risk_set_available_but_uncertified_covariates_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ctx=SimpleNamespace(repo=root,design_dir=root/'design',card=SimpleNamespace(payload=dict(treatment=dict(universe=['A','B']),outcome=dict(years=[2000,2010]))))
            ensure_once(ctx.design_dir/'TREATMENT.json',dict(audit=dict(passed=True),records=[dict(unit='A',status='treated',start_period=2005,absorbing=True),dict(unit='B',status='never_treated',start_period=None)]))
            ensure_once(root/'data/l2_pipeline/autopilot/c64/STATE_COVARIATES.json',dict(status='NEEDS_DATA'))
            value=l3.geography(ctx);self.assertEqual(value['R'],['B']);self.assertEqual(value['risk_set_status'],'AVAILABLE');self.assertEqual(value['status'],'FIELD_MISSING')

class AdditionalHistory(unittest.TestCase):
    setUp=History.setUp
    emitted=History.emitted
    def test_both_roles_wait_for_selected_completion_then_final(self):
        from dataclasses import replace
        job=self.emitted();peerpath=self.root/'peer.md';peerpath.write_text('invented peer packet')
        output=ensure_once(self.root/'peer.json',dict(self.original,role='B'))
        peer=replace(self.job,role='B',job_path=str(peerpath),output_path=str(output))
        original_load=load_job
        def loader(path,*a,**kw):return peer if Path(path)==peerpath else original_load(path,*a,**kw)
        with patch('scripts.l2_pipeline.agents.jobs.load_job',side_effect=loader):
            self.assertFalse(h.complete_history(self.pilot,self.row,str(peerpath)))
            self.assertEqual(len(self.pilot.pending),1)
            ensure_once(Path(job.output_path),golden_output(job))
            self.assertFalse(h.complete_history(self.pilot,self.row,str(peerpath)))
            with self.assertRaises(Park) as cm:h.complete_history(self.pilot,self.row,str(peerpath))
        self.assertTrue(cm.exception.metrics['final'])
    def test_dead_completion_consumes_slot_without_replacement(self):
        from scripts.l2_pipeline.agents.replacement import ContractRejection
        job=self.emitted()
        with patch.object(self.pilot.backend,'job_state',side_effect=ContractRejection(job.job_path,'DEAD_NO_RESULT','invented')):
            with self.assertRaises(Park) as cm:h.complete_history(self.pilot,self.row,self.job.job_path)
        self.assertTrue(cm.exception.metrics['final'])
        self.assertEqual(len(list(h.slot(self.pilot,self.row).rglob('*.job.json'))),1)
    def test_unknown_completion_is_excluded_both_arms_under_c42(self):
        from scripts.l2_pipeline.c42 import exclusion_scope
        from scripts.l2_pipeline.stages.s1_treatment import reconcile
        job=self.emitted();answer=golden_output(job);row=answer['records'][0]
        row.update(status='unknown',start_period=None,reason='Located statutory scope unresolved',disposition='NEEDS_RULING',named_conflict=dict(kind='policy_definition_ambiguous',detail='Scope varies in located code',binary_relevant_fact='Whether policy covers registered universe',source_ids=[row['source_id']],question='Does section include this population?'))
        ensure_once(Path(job.output_path),answer)
        self.assertTrue(h.complete_history(self.pilot,self.row,self.job.job_path))
        merged=h.completed_value(self.job,self.original)
        peer=golden_output(self.job)
        comp=reconcile(merged['records'],peer['records'],self.job.contract['manual']['universe'])
        scope=exclusion_scope(dict(universe=self.job.contract['manual']['universe']),comp,[merged,peer])
        self.assertIn('U0',scope['excluded_units'])
        self.assertEqual(merged['records'][0]['status'],'unknown')
    def test_final_marker_cannot_be_requeued(self):
        case=self.root/'case';root=self.pilot.registry;root.mkdir(parents=True)
        ensure_once(root/'ACTIVE_DESIGNS.json',dict(designs=[self.row]))
        ensure_once(case/'PARKED.json',dict(reason='INSUFFICIENT_WORK'))
        self.pilot.case_dir=lambda row:case
        self.assertEqual(len(h.requeue_current(self.pilot)),1)
        self.assertTrue(h.reopened(self.pilot,self.row))
        ensure_once(case/'PARKED_C83_FINAL.json',dict(reason='INSUFFICIENT_WORK final'))
        self.assertFalse(h.reopened(self.pilot,self.row));self.assertEqual(h.requeue_current(self.pilot),[])

class AdditionalMetadata(unittest.TestCase):
    def test_top_level_outcome_sidecar_rejected(self):
        from scripts.l2_pipeline.connectors.support_repair import project
        with self.assertRaises(ValueError):project(dict(outcome_values_in_sidecar=False,metadata=[],rows=[dict(value=10)]))
