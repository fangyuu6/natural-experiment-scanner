"""C54 synthetic transitions, metadata loaders and evidence-bound routing."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts.l2_pipeline.autopilot import Autopilot,Backend,read,atomic
from scripts.l2_pipeline.autopilot_ledger import compact
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.engine.panels import PanelBinding,audit_binding,load_bound_panel,metadata_keys
from scripts.l2_pipeline.c54 import DEFERRED,close_should,draft_identity,supersede_stop,apply_open_queue,validate_implementation_ruling
from scripts.l2_pipeline.registration import _retain_cards,_review
from scripts.l2_pipeline.tests.test_pipeline import card

class C54Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Path(self.tmp.name).resolve();self.registry=self.repo/'data/l2_pipeline/registry'
        self.p=Autopilot(self.registry,Backend(self.repo));self.row=dict(design_id='invented',attempt_id='v1',work_dir=str(self.repo/'data/l2_pipeline/work'))
        self.root=self.repo/'data/l2_pipeline/work/invented/v1';self.root.mkdir(parents=True)

    def ledger(self):
        return [json.loads(line) for line in (self.registry/'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()]

    def test_repeated_stop_and_park_suppressed_after_scheduler_restart(self):
        for decision in ('STOP','PARK'):
            for _ in range(3):self.p.ledger(self.row,'S3',decision,'stop',reason=decision)
            self.p=Autopilot(self.registry,Backend(self.repo))
            self.assertFalse(self.p.ledger(self.row,'S3',decision,'stop',reason=decision))
        self.assertEqual([v['decision'] for v in self.ledger()],['STOP','PARK'])
        self.p.ledger(self.row,'S3','STOP','stop',reason='STOP')
        self.assertEqual(len(self.ledger()),3)

    def test_idle_decide_wait_polls_and_budget_occupancy_are_not_transitions(self):
        for _ in range(8):
            self.p.ledger(self.row,'ri','DECIDE','accept')
            self.p.ledger(self.row,'ri','WAIT_LOCAL_BUDGET','budget',running=_,budget=2)
        self.assertEqual(len(self.ledger()),2)
        self.p.ledger(self.row,'ri','ACCEPT','accept');self.assertEqual(len(self.ledger()),3)
        other=dict(self.row,attempt_id='v2');self.p.ledger(other,'ri','ACCEPT','accept');self.assertEqual(len(self.ledger()),4)

    def test_compaction_keeps_raw_first_and_each_return_transition_with_interleaving(self):
        values=[dict(design_id='a',action='draft',decision='STOP',time='1',reason='x'),
            dict(design_id='b',action='S3',decision='PARK',time='2'),
            dict(design_id='a',action='draft',decision='STOP',time='3',reason='x'),
            dict(design_id='a',action='draft',decision='STOP',time='4',reason='y'),
            dict(design_id='a',action='draft',decision='STOP',time='5',reason='x')]
        path=self.registry/'AUTOPILOT_LEDGER.jsonl';path.parent.mkdir(parents=True)
        lines=[(json.dumps(v)+'\n').encode() for v in values];path.write_bytes(b''.join(lines));original=path.read_bytes()
        receipt=compact(path,self.registry/'archive')
        self.assertEqual(Path(receipt['archive']).read_bytes(),original)
        self.assertEqual(sha(receipt['archive']),receipt['original_sha256'])
        self.assertEqual(path.read_bytes(),b''.join(lines[i] for i in (0,1,3,4)))
        self.assertEqual(compact(path,self.registry/'archive')['active_records'],4)

    def test_incomplete_or_malformed_compaction_preserves_active(self):
        path=self.registry/'AUTOPILOT_LEDGER.jsonl';path.parent.mkdir(parents=True)
        for content in (b'{"decision":"STOP"}',b'not-json\n'):
            path.write_bytes(content)
            with self.assertRaises(ValueError):compact(path,self.registry/'archive')
            self.assertEqual(path.read_bytes(),content)

    def test_reordered_metadata_passes_opaque_audit_and_actual_invented_loader(self):
        for name in ('source','panel'):
            (self.root/(name+'.csv')).write_text('unit,year,outcome_value\nAA,2000,POISON\nAA,2001,POISON\n')
        meta=self.root/'metadata.csv';meta.write_text('annual_usable,available,year,unit\n1,1,2000,AA\n1,1,2001,AA\n')
        src=self.root/'source.csv';pan=self.root/'panel.csv'
        b=PanelBinding(src,pan,meta,sha(src),sha(pan),sha(meta))
        self.assertTrue(audit_binding(b)['passed'])
        with patch('scripts.l2_pipeline.engine.panels._value',side_effect=AssertionError('numeric audit')):audit_binding(b)
        src.write_text(src.read_text().replace('POISON','7'));pan.write_text(pan.read_text().replace('POISON','8'))
        b=PanelBinding(src,pan,meta,sha(src),sha(pan),sha(meta))
        rows,_=load_bound_panel(b);self.assertEqual([r[2] for r in rows],[8,8])
        pan.write_text('year,unit,outcome_value\n2000,AA,8\n2001,AA,8\n')
        reordered=PanelBinding(src,pan,meta,sha(src),sha(pan),sha(meta))
        self.assertTrue(audit_binding(reordered)['passed'])
        self.assertEqual(load_bound_panel(reordered)[0],rows)

    def test_metadata_keys_reject_duplicate_missing_or_hash_changed(self):
        path=self.root/'metadata.csv'
        for content in ('available,unit\n1,AA\n','unit,year,unit\nAA,2000,AA\n','year,unit\n2000,AA\n2000,AA\n'):
            path.write_text(content)
            with self.assertRaises(RuntimeError):metadata_keys(path)

    def ctx(self):
        c=card();c['analysis']={'controls':'never_treated'};path=ensure_once(self.repo/'data/l2_pipeline/CARD.json',c)
        self.row.update(card=str(path),card_sha256=sha(path));ensure_once(self.root/'SPEC.json',dict(c,comparison_group='not_yet_treated',analysis=dict(controls='not_yet_treated'),support=dict(variants=[dict(variant='primary',cell_inventory=[])])))
        return SimpleNamespace(repo=self.repo,design_dir=self.root,card=SimpleNamespace(payload=c,path=path,design_id='invented'),read=lambda n:read(self.root/n))

    def test_scientific_card_matches_registered_metadata_group_without_mutating_intake(self):
        ctx=self.ctx();raw=ctx.card.path.read_bytes()
        with patch('scripts.l2_pipeline.cards.load_card',return_value=SimpleNamespace(payload=ctx.card.payload)):_retain_cards(ctx)
        self.assertEqual(read(self.root/'SCIENTIFIC_CARD.json')['analysis'],ctx.read('SPEC.json')['analysis'])
        self.assertEqual(ctx.card.path.read_bytes(),raw);self.assertEqual(ctx.card.payload['analysis']['controls'],'never_treated')

    def test_scientific_card_rejects_unregistered_spec_edits(self):
        ctx=self.ctx();spec=ctx.read('SPEC.json');spec['analysis']['min_controls']=2;atomic(self.root/'SPEC.json',spec)
        with patch('scripts.l2_pipeline.cards.load_card',return_value=SimpleNamespace(payload=ctx.card.payload)):
            with self.assertRaisesRegex(ValueError,'normalization differs'):_retain_cards(ctx)
        self.assertFalse((self.root/'SCIENTIFIC_CARD.json').exists())

    def review_fixture(self,description=None):
        ctx=self.ctx();self.p.backend.ctx=lambda row:ctx;self.p.backend.root=lambda row:self.root
        auth=self.repo/'docs/plans/l2-pipeline/HUB_RULING_C54_CLOSURES.md';auth.parent.mkdir(parents=True);auth.write_text('invented C54 authority')
        attempt='custom_c53_final';job=self.repo/'data/l2_pipeline/job.md';job.write_text('invented immutable packet')
        path=self.root/('REVIEW_DRAFT_'+attempt+'.json')
        finding=dict(id='S01',description=description or next(iter(DEFERRED)),impact='nonblocking_preparation_status_report_failure',evidence='invented')
        ensure_once(path,dict(design_id='invented',candidate_sha256=sha(self.root/'SPEC.json'),review_scope='draft_readiness',
            complete_sweeps=1,fix_cycles=0,verdict='PASS',must=[],should=[finding],independence_attestation=dict(
                wrote_implementation=False,wrote_fixes=False,real_outcome_associations_read=False)))
        ensure_once(job.with_suffix('.job.json'),dict(design_id='invented',design_version='v1',job_sha256=sha(job),
            promote_path=str(path),contract=dict(attempt_id=attempt,candidate_sha256=sha(self.root/'SPEC.json'))))
        ensure_once(self.p.case_dir(self.row)/'jobs/draft.json',dict(jobs=[str(job)]))
        return ctx,attempt,job,path

    def test_actual_bound_draft_attempt_is_used_for_prep_not_inferred_filename(self):
        ctx,attempt,job,path=self.review_fixture()
        self.assertEqual(self.p.draft_attempt(self.row),attempt)
        self.assertEqual(draft_identity(self.p,self.row,[job]),(attempt,path))
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'identity'):self.p.draft_attempt(self.row)

    def test_should_deferral_is_verbatim_bound_idempotent_and_resume_is_auditable(self):
        ctx,attempt,job,path=self.review_fixture();before=path.read_bytes()
        self.p.stop(self.row,'draft','review SHOULD')
        self.assertTrue(close_should(self.p,self.row,str(job)));self.assertTrue(close_should(self.p,self.row,str(job)))
        closure=self.root/('DRAFT_RESOLUTION_'+attempt+'.json');v=read(closure)
        self.assertEqual(v['resolutions'][0]['finding_verbatim'],read(path)['should'][0])
        self.assertEqual(path.read_bytes(),before);self.assertEqual(_review(ctx,attempt)['status'],'HUB_RESOLVED_PENDING_SEALED_REVIEW')
        supersede_stop(self.p,self.row,[dict(path=str(closure),sha256=sha(closure))])
        self.assertIsNone(self.p.stop_record(self.row));self.assertEqual(read(self.registry/'HUB_QUEUE.json')['entries'][0]['status'],'SUPERSEDED_C54')
        self.p.stop(self.row,'prep','new failure');self.assertEqual(self.p.stop_record(self.row)['reason'],'new failure')
        closure.write_text('{}')
        with self.assertRaisesRegex(ValueError,'bytes changed'):self.p.stop_record(self.row)

    def test_should_science_unknown_and_frozen_remain_open(self):
        ctx,attempt,job,path=self.review_fixture('Change sample threshold')
        self.assertFalse(close_should(self.p,self.row,str(job)))
        self.assertFalse((self.root/('DRAFT_RESOLUTION_'+attempt+'.json')).exists())
        review=read(path);review['should'][0]['description']=next(iter(DEFERRED));atomic(path,review)
        ensure_once(self.root/'FREEZE_PREP.json',{'frozen':True});before={str(x):sha(x) for x in self.root.rglob('*') if x.is_file()}
        self.assertFalse(close_should(self.p,self.row,str(job)))
        self.assertEqual(before,{str(x):sha(x) for x in self.root.rglob('*') if x.is_file()})

    def test_live_implementation_ruling_cannot_change_outcome_science(self):
        ctx=self.ctx();auth=self.repo/'docs/plans/l2-pipeline/HUB_RULING_C54_CLOSURES.md';auth.parent.mkdir(parents=True);auth.write_text('C54')
        atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=[self.row]))
        new=copy.deepcopy(ctx.card.payload);new['version']='v2';new['outcome']['years']=[2000,2001]
        path=ensure_once(self.repo/'data/l2_pipeline/NEW_CARD.json',new)
        with self.assertRaisesRegex(ValueError,'change science'):
            validate_implementation_ruling(self.p,dict(design_id='invented',authority=dict(path=str(auth),sha256=sha(auth)),execution_authorized=False),path)

    def test_followup_prep_stop_cannot_be_closed_by_old_pass(self):
        ctx,attempt,job,path=self.review_fixture()
        atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=[self.row]))
        self.p.stop(self.row,'prep','unrelated science failure')
        self.assertEqual(apply_open_queue(self.p),[])
        self.assertEqual(self.p.stop_record(self.row)['reason'],'unrelated science failure')
        self.assertEqual(read(self.registry/'HUB_QUEUE.json')['entries'][0]['status'],'NEEDS_RULING')

    def test_bad_queue_design_is_isolated_and_other_design_is_processed(self):
        other=dict(self.row,design_id='other')
        atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=[self.row,other]))
        atomic(self.registry/'HUB_QUEUE.json',dict(entries=[dict(r,status='NEEDS_RULING') for r in [self.row,other]]))
        with patch('scripts.l2_pipeline.c54._apply_row',side_effect=[ValueError('bad binding'),dict(design_id='other',next_action='prep')]) as apply:
            values=apply_open_queue(self.p)
        self.assertEqual(apply.call_count,2);self.assertTrue(values[0]['retained_open']);self.assertEqual(values[1]['design_id'],'other')
        self.assertEqual(self.ledger()[-1]['decision'],'C54_APPLY_ERROR')

    def test_rebuild_failure_preserves_new_stop_and_application(self):
        from scripts.l2_pipeline.c54 import rebuild
        ctx,attempt,job,path=self.review_fixture();new=dict(self.row,attempt_id='v2')
        self.p.backend.perform=lambda *args:(_ for _ in ()).throw(OSError('invented missing preparation source'))
        with patch('scripts.l2_pipeline.requeue.requeue',return_value=dict(new_row=new)):
            value=rebuild(self.p,self.row,[dict(kind='invented proof')],[],scheduler_owned=True)
        self.assertEqual(value['failure']['action'],'S0');self.assertEqual(value['draft_jobs'],[])
        self.assertIn('C54 rebuild failed',self.p.stop_record(new)['reason'])
        app=self.registry.parent/'autopilot/c54/resolutions/invented/v1/APPLICATION.json'
        self.assertEqual(read(app),value)

    def test_historical_requeue_without_attempt_ids_retains_new_stop(self):
        path=self.registry/'AUTOPILOT_LEDGER.jsonl';path.parent.mkdir(parents=True)
        rows=[dict(design_id='a',action='draft',decision='STOP',reason='same'),
              dict(design_id='a',action='requeue',decision='SUPERSEDED_C53'),
              dict(design_id='a',action='draft',decision='STOP',reason='same')]
        original=''.join(json.dumps(r)+'\n' for r in rows).encode();path.write_bytes(original)
        compact(path,self.registry/'archive');self.assertEqual(path.read_bytes(),original)
        self.p.ledger(self.row,'draft','STOP','stop',reason='same')
        self.p.ledger(self.row,'resume','SUPERSEDED_C54','c54')
        self.assertTrue(self.p.ledger(self.row,'draft','STOP','stop',reason='same'))
        restarted=Autopilot(self.registry,Backend(self.repo))
        self.assertFalse(restarted.ledger(self.row,'draft','STOP','stop',reason='same'))

    def test_draft_emission_failure_retains_science_stop_and_application(self):
        from scripts.l2_pipeline.c54 import rebuild
        ctx,attempt,job,path=self.review_fixture();new=dict(self.row,attempt_id='v2')
        self.p.backend.ctx=lambda row,strict=False:ctx
        self.p.backend.perform=lambda *args:dict(ready=True)
        science=[dict(id='M02',description='Invented registered measurement break')]
        with patch('scripts.l2_pipeline.requeue.requeue',return_value=dict(new_row=new)),patch('scripts.l2_pipeline.agents.emit_stage_jobs',side_effect=OSError('invented packet failure')):
            value=rebuild(self.p,self.row,[dict(kind='invented proof')],science,scheduler_owned=True)
        self.assertEqual(value['failure']['action'],'draft');self.assertEqual(value['science_must_open'],science)
        self.assertIn('Invented registered measurement break',self.p.stop_record(new)['reason'])
        self.assertEqual(read(self.registry.parent/'autopilot/c54/resolutions/invented/v1/APPLICATION.json'),value)
