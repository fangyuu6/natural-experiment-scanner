"""C76 prospective contracts, using synthetic forecasts; no model call or fit."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from dataclasses import replace
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.forecast_slots import prepare,roster,independence,validate_contract,_historical
from scripts.l2_pipeline.agents.jobs import emit_stage_jobs
from scripts.l2_pipeline.agents.collect import collect_job
from scripts.l2_pipeline.agents.sonnet_queue import route,enqueue
from scripts.l2_pipeline.predictions import lock_predictions
from scripts.l2_pipeline.tests.test_c56 import ContractTests

class SlotsTests(ContractTests):
    def jobs(self):return emit_stage_jobs(self.ctx,'S5',jobs_root=self.root/'jobs')
    def value(self,job,point=1):
        return dict(persona=job.role,design_id=job.design_id,spec_sha256=job.contract['spec_sha256'],point=point,
            interval=[-1,3],interval_level=.8,rationale='synthetic primary ATT original units',disclosures=[],
            author_provenance=dict(worker_id='worker_'+job.role,model_id=job.contract['forecast_slot']['model_id']),
            blindness_attestation=dict(other_submissions_read=False,this_design_outcomes_read=False,this_design_estimates_read=False,outside_slot_pack_read=False))
    def test_distinct_packs_and_third_slot_codex(self):
        jobs=self.jobs();self.assertEqual(len(jobs),3)
        self.assertEqual(len({j.contract['forecast_slot']['pack_sha256'] for j in jobs}),3)
        for job in jobs:self.assertEqual(len(job.inputs),1)
        self.assertEqual(route(jobs[2].design_id,jobs[2].role,jobs[2].contract['role_backend_overrides']),'codex')
        with self.assertRaisesRegex(ValueError,'executor override'):enqueue(jobs[2].job_path,{},self.root/'queue')
        original=(self.ctx.design_dir/'FORECAST_SLOTS.json').read_bytes()
        self.assertEqual(prepare(self.ctx)['personas'],list(roster(self.ctx)))
        self.assertEqual((self.ctx.design_dir/'FORECAST_SLOTS.json').read_bytes(),original)
    def test_claude_only_registers_slot_3_as_sonnet_for_new_designs_and_keeps_registered_ones(self):
        # Owner 2026-10-15 (option A): new designs register slot 3 as claude-sonnet-5.5 through the Sonnet queue;
        # a design registered before the switch keeps gpt-6.1-sol on Codex and its write-once file is untouched.
        from unittest.mock import patch
        from scripts.l2_pipeline.agents.dispatch import command
        with patch.dict(os.environ,{'L2_CLAUDE_ONLY':'1'}):
            jobs=self.jobs();slots=json.loads((self.ctx.design_dir/'FORECAST_SLOTS.json').read_text())['slots']
            self.assertEqual({(s['model_id'],s['executor']) for s in slots.values()},{('claude-sonnet-5.5','sonnet_queue')})
            self.assertEqual(len({j.contract['forecast_slot']['pack_sha256'] for j in jobs}),3)
            self.assertEqual({route(j.design_id,j.role,j.contract['role_backend_overrides']) for j in jobs},{'sonnet_queue'})
            self.assertEqual(enqueue(jobs[2].job_path,{},self.root/'queue')['backend'],'sonnet_queue')
            with self.assertRaisesRegex(ValueError,'executor override'):command(jobs[2],codex='fixture-codex')
        self.setUp()  # a second design, registered before the switch (variable unset again)
        jobs=self.jobs();path=self.ctx.design_dir/'FORECAST_SLOTS.json';original=path.read_bytes()
        self.assertEqual(json.loads(original)['slots']['Roth']['model_id'],'gpt-6.1-sol')
        with patch.dict(os.environ,{'L2_CLAUDE_ONLY':'1'}):
            self.assertEqual(prepare(self.ctx)['slots']['Roth']['executor'],'codex');self.assertEqual(path.read_bytes(),original)
            self.assertEqual(route(jobs[2].design_id,jobs[2].role,jobs[2].contract['role_backend_overrides']),'codex')
    def test_new_spec_overrides_legacy_config_five_and_existing_lock_stays_five(self):
        ctx=replace(self.ctx,card=replace(self.ctx.card,payload=dict(self.ctx.card.payload,automation={'personas':['Card','Finkelstein','Roth','Liebman','Boehmke']})))
        p=ctx.design_dir/'SPEC.json';spec=json.loads(p.read_text());spec['c79_hygiene']={'prospective':True};p.write_text(json.dumps(spec))
        self.assertEqual(len(roster(ctx)),3)
        ensure_once(ctx.design_dir/'PREDICTIONS_LOCK.json',{'personas':['Card','Finkelstein','Roth','Liebman','Boehmke']})
        self.assertEqual(len(roster(ctx)),5)
    def test_slot_literature_reads_verified_summary_without_other_packs(self):
        p=ensure_once(self.root/'literature.json',{'status':'VERIFIED','title':'Synthetic paper','published_results':[{'estimate_as_reported':'2','units':'rate'}],'private_field':'excluded'})
        ctx=replace(self.ctx,card=replace(self.ctx.card,payload=dict(self.ctx.card.payload,automation={'literature_record':{'collector_verified':True,'record_ref':str(p)}})))
        value=prepare(ctx);slot=value['slots']['Finkelstein'];pack=json.loads(Path(slot['pack_path']).read_text())
        self.assertEqual(pack['verified_record']['title'],'Synthetic paper');self.assertNotIn('private_field',pack['verified_record'])
        self.assertNotIn('Synthetic paper',Path(value['slots']['Card']['pack_path']).read_text())
    def test_codex_slot_command_pins_model_and_legacy_command_unchanged(self):
        from scripts.l2_pipeline.agents.dispatch import command
        jobs=self.jobs();job=jobs[2]
        argv=command(job,codex='fixture-codex')
        self.assertEqual(argv[argv.index('--model')+1],job.contract['forecast_slot']['model_id'])
        with self.assertRaisesRegex(ValueError,'executor override'):command(jobs[0],codex='fixture-codex')
        bad=replace(job,contract=dict(job.contract,role_backend_overrides={job.role:'sonnet_queue'}))
        with self.assertRaisesRegex(ValueError,'executor override'):command(bad,codex='fixture-codex')
        legacy=replace(job,contract={k:v for k,v in job.contract.items() if k!='forecast_slot'})
        self.assertEqual(command(legacy,codex='fixture-codex'),['fixture-codex','exec','-C',job.repo,'-s','workspace-write','--color','never','-o',job.result_path,'-'])
    def test_collection_lock_provenance_consensus_and_null_author(self):
        jobs=self.jobs()
        for job in jobs:
            value=self.value(job);value['author_provenance']=None
            output=Path(job.output_path);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'provenance'):collect_job(job.job_path,self_check=True)
            output.write_text(json.dumps(self.value(job)))
            collect_job(job.job_path)
        validate_contract(self.ctx.design_dir,[j.role for j in jobs])
        p=lock_predictions(self.ctx.design_dir,[j.role for j in jobs],jobs[0].design_id,jobs[0].contract['spec_sha256'])
        locked=json.loads(p.read_text());self.assertTrue(locked['SINGLE_CONSENSUS']);self.assertFalse(locked['n10_eligible']);self.assertEqual(locked['n_eff'],1)
        for job in jobs:
            slot=locked['forecast_slots'][job.role]
            self.assertEqual(slot['prompt_sha256'],sha(job.job_path));self.assertTrue(slot['author_provenance']['worker_id'])
    def test_mutated_primary_contract_cannot_lock(self):
        jobs=self.jobs()
        for job in jobs:ensure_once(Path(job.output_path),self.value(job));collect_job(job.job_path)
        spec=self.ctx.design_dir/'SPEC.json';v=json.loads(spec.read_text());v['analysis']['clock']='changed';spec.write_text(json.dumps(v))
        with self.assertRaisesRegex(ValueError,'differs from registered'):validate_contract(self.ctx.design_dir,[j.role for j in jobs])
    def test_historical_pack_filters_current_related_and_same_policy_before_reading(self):
        ctx=replace(self.ctx,repo=self.root);c=copy.deepcopy(dict(ctx.card.payload));c['policy_family']='tax';c['mechanism']='current';c['outcome']['outcome_unit']='rate';c['lineage']={'source_text_ids':['current-text']}
        ctx=replace(ctx,card=replace(ctx.card,payload=c));records=[]
        for name,mechanism,sourceid in [('same_design','other','other-text'),('same_policy','current','other-text'),('lineage','other','current-text'),('eligible','other','other-text')]:
            root=self.root/'data/l2_pipeline'/name;did=ctx.card.design_id if name=='same_design' else name
            card=dict(design_id=did,policy_family='tax',mechanism=mechanism,outcome={'outcome_unit':'rate'},lineage={'source_text_ids':[sourceid]})
            ensure_once(root/'CARD.json',card)
            export=ensure_once(root/'L3_EXPORT_DATA.json',dict(result={'registered_results':{'primary':{'result':{'overall':{'att':2.5}}}}}))
            records.append(dict(design_id=did,study_estimated=True,package_path=str(root),s7_exports={export.name:sha(export)}))
        ensure_once(self.root/'data/l2_pipeline/registry/TERMINAL_RUNS.json',dict(runs=records))
        result=_historical(ctx);self.assertEqual([r['design_id'] for r in result],['eligible']);self.assertEqual(result[0]['estimate'],2.5)

class HygieneTests(unittest.TestCase):
    def test_near_identical_vs_distinct(self):
        value=dict(point=1,interval=[-1,3],interval_level=.8)
        near=[value,dict(value,point=1.01),dict(value,point=1.02)]
        self.assertTrue(independence(near)['SINGLE_CONSENSUS'])
        distinct=[dict(value,point=p) for p in (1,2,3)]
        slots={p:dict(author_provenance=dict(worker_id='w'+p)) for p in 'abc'}   # n10 needs >= 2 distinct worker ids (Q3 ruling 3b); unknown workers = not eligible
        self.assertEqual(independence(distinct)['n_eff'],3);self.assertFalse(independence(distinct)['n10_eligible'])
        self.assertTrue(independence(distinct,slots)['n10_eligible'])
    def test_all_five_c57_contracts_are_caught_including_empty_old_summary(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/c76_c57_contracts.json').read_text())
        self.assertEqual(len(fixture),5)
        for case in fixture:
            with self.subTest(design=case['design_id']), tempfile.TemporaryDirectory() as d:
                root=Path(d);job=root/'job.md'
                meta=dict(contract={'card':case['actual']})
                job.write_text('## Execution metadata binding (data)\n```json\n'+json.dumps(meta)+'\n```\n')
                ensure_once(job.with_suffix('.job.json'),meta)
                ensure_once(root/'predictions/Card/COLLECTION.json',dict(job_path=str(job),job_sha256=sha(job)))
                from unittest.mock import patch
                with patch('scripts.l2_pipeline.contract_card.registered_contract_card',return_value=case['expected']):
                    with self.assertRaisesRegex(ValueError,'differs from registered'):validate_contract(root,['Card'])
