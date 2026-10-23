"""Invented agent outputs only: no model, network or real study fits."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.autopilot import (
    Autopilot, Backend, CHAIN, atomic, ingest, read, scheduler_lock,
)
from scripts.l2_pipeline.storage import ensure_once, sha


class MockAgents(Backend):
    def __init__(self,repo):
        super().__init__(repo)
        self.async_local=False
        self.states={};self.launches=[];self.actions=[];self.fail={};self.external=0
        self.must=set();self.should=set();self.dead=set()

    def root(self,row):
        return Path(row['work_dir'])/row['design_id']/'v1'

    def jobs(self,row,action,pilot):
        count=5 if action=='forecasts' else 2 if action=='history' else 1
        paths=[]
        for n in range(count):
            path=pilot.case_dir(row)/'mock'/action/(str(n)+'.md')
            out=path.parent/(str(n)+'_output');promote=out/'submission.json'
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text('invented agent')
            ensure_once(path.with_suffix('.job.json'),dict(role=str(n),output_dir=str(out),output_path=str(out/'original.json'),
                promote_path=str(promote),result_path=str(out/'result.txt')))
            if action in {'draft','sealed'}:
                out.mkdir(parents=True,exist_ok=True)
                atomic(promote,dict(verdict='STOP' if row['design_id'] in self.must else 'PASS',
                    must=[{'id':'invented'}] if row['design_id'] in self.must else [],
                    should=[{'id':'invented'}] if row['design_id'] in self.should else [],
                    review_scope='draft_readiness' if action=='draft' else 'sealed_candidate'))
            self.states.setdefault(str(path),'pending');paths.append(str(path))
        return paths

    def job_state(self,path):
        if self.states[path]=='failed': raise ValueError('Agent FAILED_NO_RETRY')
        return self.states[path]

    def collect(self,path): return {'validated':True}
    def replacement_job(self,path,slot,attempt_id):
        new=slot/(attempt_id+'.md');new.parent.mkdir(parents=True,exist_ok=True)
        new.write_text('invented replacement agent')
        metadata=read(Path(path).with_suffix('.job.json'))
        ensure_once(new.with_suffix('.job.json'),dict(metadata,contract=dict(contract_replacement=True)))
        self.states[str(new)]='pending'
        return str(new)
    def global_running(self): return self.external+sum(s=='running' for s in self.states.values())
    def launch(self,path,claim):
        if path in self.launches: raise AssertionError('Duplicate model dispatch')
        self.launches.append(path);self.states[path]='running'
        return {'pid':len(self.launches),'started_at':'invented'}
    def alive(self,pid): return pid not in self.dead
    def finish(self):
        for p,state in list(self.states.items()):
            if state=='running':self.states[p]='done'

    def perform(self,row,action,pilot):
        did=row['design_id'];root=self.root(row)
        self.actions.append((did,action))
        if (did,action) in self.fail:
            if action=='execute':
                atomic(root/'EXECUTION_ONCE.json',{'status':'ATTEMPT_CONSUMED'})
                atomic(root/'FAILURE.json',{'status':'FAILED_NO_RETRY','message':'invented run failure'})
            raise self.fail[did,action]
        if action=='execute':
            atomic(root/'EXECUTION_ONCE.json',{'status':'ATTEMPT_CONSUMED'})
            atomic(root/'TERMINAL.json',{'status':'COMPLETED'})
        if action=='register':
            path=pilot.registry/'TERMINAL_RUNS.json';v=read(path,{'runs':[],'pre_run_stops':[]})
            if not any(r['design_id']==did for r in v['runs']):
                v['runs'].append({'design_id':did,'status':read(root/'TERMINAL.json',read(root/'FAILURE.json'))['status']})
                atomic(path,v)
        return {'accepted':action}


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name).resolve();self.registry=self.repo/'data/l2_pipeline/registry'
        self.backend=MockAgents(self.repo);self.pilot=Autopilot(self.registry,self.backend)

    def seed(self,n):
        rows=[dict(design_id='d'+str(i),attempt_id='test',work_dir=str(self.repo/'data/l2_pipeline/work'),
                   card='invented',card_sha256='invented',route='V9',managed_execution=True) for i in range(n)]
        atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=rows))
        return rows

    def drive(self,pilot=None,limit=150):
        pilot=pilot or self.pilot
        for _ in range(limit):
            pilot.tick();self.backend.finish()
            rows=read(self.registry/'ACTIVE_DESIGNS.json')['designs']
            if all(pilot.next_action(r) in {'STOP','FINISHED'} for r in rows):return
        self.fail('State machine did not finish')

    def test_end_to_end_55_designs_global_ten_including_external_workers(self):
        self.pilot.budget=10  # explicit historical cap; C55 default is six
        rows=self.seed(55);self.backend.external=3
        for _ in range(150):
            self.pilot.tick()
            self.assertLessEqual(self.backend.global_running(),10)
            self.backend.finish()
            if all(self.pilot.next_action(r)=='FINISHED' for r in rows):break
        self.assertTrue(all(self.pilot.next_action(r)=='FINISHED' for r in rows))
        self.assertEqual(len(read(self.registry/'TERMINAL_RUNS.json')['runs']),55)
        self.assertEqual(len(self.backend.launches),len(set(self.backend.launches)))
        for row in rows:
            self.assertTrue(all(self.pilot.done(row,a) for a in CHAIN))
            self.assertEqual(self.backend.actions.count((row['design_id'],'execute')),1)
        ledger=[json.loads(line) for line in (self.registry/'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()]
        self.assertTrue(all(v['rule'] for v in ledger))

    def test_resume_running_and_completed_jobs_never_redispatch(self):
        rows=self.seed(2);self.pilot.tick();launches=list(self.backend.launches)
        resumed=Autopilot(self.registry,self.backend,budget=1)
        resumed.tick();self.assertEqual(launches,self.backend.launches)
        self.backend.finish();self.drive(resumed)
        before=list(self.backend.launches);resumed.tick()
        self.assertEqual(before,self.backend.launches)
        self.assertTrue(all(resumed.next_action(r)=='FINISHED' for r in rows))

    def test_needs_ruling_exception_must_and_failure_stop_only_their_design(self):
        rows=self.seed(5)
        self.backend.fail['d0','reconcile']=ValueError('NEEDS_RULING: history conflict')
        self.backend.fail['d1','S3']=RuntimeError('unexpected support exception')
        self.backend.must.add('d2')
        self.backend.fail['d3','execute']=RuntimeError('invented run failure')
        self.drive();self.pilot.tick()
        queue=read(self.registry/'HUB_QUEUE.json')['entries']
        self.assertEqual({v['design_id'] for v in queue},{'d0','d1','d2','d3'})
        self.assertTrue(all(v['reason'] for v in queue))
        self.assertEqual(self.pilot.next_action(rows[4]),'FINISHED')
        self.assertEqual(self.backend.actions.count(('d3','execute')),1)
        self.assertFalse(self.pilot.done(rows[2],'prep'))
        self.assertTrue(any(r['design_id']=='d3' and r['status']=='FAILED_NO_RETRY'
                            for r in read(self.registry/'TERMINAL_RUNS.json')['runs']))

    def test_lost_execution_checkpoint_resumes_terminal_without_retry(self):
        row=self.seed(1)[0];self.drive()
        self.pilot.checkpoint(row,'execute').unlink()
        self.pilot.tick();self.assertEqual(self.backend.actions.count(('d0','execute')),1)
        self.assertTrue(self.pilot.done(row,'execute')['resumed_terminal'])

    def test_consumed_execution_without_terminal_is_never_retried(self):
        row=self.seed(1)[0];atomic(self.backend.root(row)/'EXECUTION_ONCE.json',{'status':'ATTEMPT_CONSUMED'})
        self.pilot.tick();self.pilot.tick()
        self.assertEqual(self.backend.launches,[])
        self.assertIn('Consumed',read(self.registry/'HUB_QUEUE.json')['entries'][0]['reason'])

    def test_interrupted_launch_claim_replaces_waiting_role_once_under_section7(self):
        row=self.seed(1)[0]
        jobs=self.backend.jobs(row,'history',self.pilot)
        import hashlib
        claim=self.registry.parent/'autopilot/worker_claims'/(hashlib.sha256(jobs[0].encode()).hexdigest()+'.json')
        ensure_once(claim,dict(job=jobs[0],row=row,started_at='2000-01-01T00:00:00+00:00'))
        self.pilot.tick()
        replacement=self.pilot.effective_job(row,jobs[0])
        self.assertNotEqual(replacement,jobs[0]);self.assertNotIn(jobs[0],self.backend.launches)
        self.assertEqual(self.backend.launches.count(replacement),1)
        self.pilot.tick();self.assertEqual(self.backend.launches.count(replacement),1)
        self.assertFalse((self.registry/'HUB_QUEUE.json').exists())

    def test_checkpoint_after_approval_crash_recovers_from_existing_bytes(self):
        # The adapter verifies an existing signature instead of rewriting its UTC.
        row=dict(card='invented',card_sha256='invented',work_dir='invented',attempt_id='test')
        root=self.repo/'data/l2_pipeline/existing';atomic(root/'APPROVAL_FINAL.json',{'signed_at':'old'})
        ctx=type('Ctx',(),{'design_dir':root})()
        backend=Backend(self.repo)
        with patch.object(backend,'ctx',return_value=ctx),patch('scripts.l2_pipeline.registration.hub_signer',return_value='hub'),patch('scripts.l2_pipeline.freeze.require_verified',return_value={'ok':True}) as verify:
            self.assertTrue(backend.perform(row,'approve-final',self.pilot)['ok'])
            verify.assert_called_once_with(root,'final','hub')
        self.assertEqual(read(root/'APPROVAL_FINAL.json'),{'signed_at':'old'})

    def test_existing_finished_package_is_byte_identical_and_read_only(self):
        row=self.seed(1)[0];root=self.backend.root(row)
        atomic(root/'TERMINAL.json',{'status':'COMPLETED'});atomic(root/'FREEZE_FINAL.json',{'frozen':'invented'})
        before={p:sha(p) for p in root.rglob('*') if p.is_file()}
        self.pilot.tick();self.pilot.tick()
        self.assertEqual(before,{p:sha(p) for p in root.rglob('*') if p.is_file()})
        self.assertEqual(self.backend.launches,[]);self.assertEqual(self.backend.actions,[])

    def test_lock_excludes_another_scheduler(self):
        with scheduler_lock(self.registry):
            with self.assertRaisesRegex(ValueError,'Another autopilot'):
                with scheduler_lock(self.registry):pass

    def test_budget_is_configurable_and_existing_excess_launches_nothing(self):
        self.seed(55);self.backend.external=14
        self.pilot.tick();self.assertEqual(self.backend.launches,[])
        self.backend.external=0;Autopilot(self.registry,self.backend,budget=17).tick()
        self.assertEqual(len(self.backend.launches),17)

    def test_dry_plan_never_emits_agents_or_performs_stages(self):
        self.seed(55);self.pilot.tick(dry_run=True)
        self.assertEqual(self.backend.actions,[]);self.assertEqual(self.backend.launches,[])
        self.assertEqual(self.backend.states,{})

    def test_should_needs_existing_ruling_no_auto_closure(self):
        self.seed(1);self.backend.should.add('d0');self.drive()
        self.assertIn('SHOULD',read(self.registry/'HUB_QUEUE.json')['entries'][0]['reason'])

    def test_ingest_schema_and_duplicate_rejection_without_partial_manifest(self):
        self.seed(0)
        cards=self.repo/'data/l2_pipeline/cards';cards.mkdir(parents=True)
        atomic(cards/'bad.json',{'design_id':'invalid'})
        with self.assertRaises(ValueError):ingest(cards,self.pilot)
        self.assertEqual(read(self.registry/'ACTIVE_DESIGNS.json')['designs'],[])
        (cards/'bad.json').unlink()
        from scripts.l2_pipeline.cards import load_card
        example=Path(__file__).parents[1]/'examples/design.json'
        card=json.loads(example.read_text());card['design_id']='intake_new'
        card['analysis']['min_post_periods']=2
        card['treatment']['universe']=['A','B','C'];card['treatment']['seed']=5
        atomic(cards/'valid.json',card)
        self.assertEqual(ingest(cards,self.pilot)['ingested'],1)
        with self.assertRaisesRegex(ValueError,'Duplicate'):ingest(cards,self.pilot)
        card['design_id']='terminal_duplicate';atomic(cards/'valid.json',card)
        atomic(self.registry/'TERMINAL_RUNS.json',dict(runs=[{'design_id':'terminal_duplicate'}],pre_run_stops=[]))
        with self.assertRaisesRegex(ValueError,'Duplicate'):ingest(cards,self.pilot)

    def test_real_dispatch_state_waits_for_completion_not_half_written_output(self):
        root=self.repo/'data/l2_pipeline/jobs';root.mkdir(parents=True)
        path=root/'job.md';atomic(path.with_suffix('.job.json'),dict(output_dir=str(root),output_path=str(root/'submission.json'),result_path=str(root/'result.txt')))
        atomic(root/'DISPATCH_ONCE.json',{'retries':0});atomic(root/'submission.json',{'partial':True})
        backend=Backend(self.repo)
        self.assertEqual(backend.job_state(path),'running')
        atomic(root/'DISPATCH_RESULT.json',{'status':'COMPLETED','exit_code':0})
        self.assertEqual(backend.job_state(path),'done')

    def test_ledger_partial_append_is_preserved_and_recovered(self):
        self.seed(0);path=self.registry/'AUTOPILOT_LEDGER.jsonl'
        path.write_bytes(b'{"valid":true}\n{"partial":')
        self.pilot.tick(dry_run=True)
        self.pilot.ledger({},'recovery','PASS','record')
        values=[json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(values[0],{'valid':True})
        evidence=list(self.registry.glob('AUTOPILOT_LEDGER_RECOVERY_*.json'))
        self.assertEqual(bytes.fromhex(read(evidence[0])['tail_hex']),b'{"partial":')

    def test_terminal_append_is_idempotent_and_preserves_old_rows(self):
        from types import SimpleNamespace
        row=self.seed(1)[0];root=self.backend.root(row)
        for name,value in [('TERMINAL.json',{'status':'COMPLETED'}),('RESULT.json',{'actual_data_estimation_calls':1}),
                           ('EXPORT.json',{'registry_event':{'design_id':'d0'},'estimates_page':{},'l3_meta':{}})]:atomic(root/name,value)
        old={'design_id':'historical','run_id':'historical__v1','status':'COMPLETED'}
        atomic(self.registry/'TERMINAL_RUNS.json',dict(runs=[old],pre_run_stops=[]))
        with patch.object(self.backend,'ctx',return_value=SimpleNamespace(card=SimpleNamespace(version='v1'))),patch('scripts.l2_pipeline.snapshots.manifest_for',return_value=None):
            first=self.pilot.register_terminal(row,root);before=sha(self.registry/'TERMINAL_RUNS.json')
            second=self.pilot.register_terminal(row,root)
        self.assertEqual(first['run_id'],second['run_id'])
        self.assertEqual(sha(self.registry/'TERMINAL_RUNS.json'),before)
        self.assertEqual(read(self.registry/'TERMINAL_RUNS.json')['runs'][0],old)

    def test_terminal_append_crash_allows_a_neighbours_legitimate_append(self):
        from types import SimpleNamespace
        row=self.seed(1)[0];root=self.backend.root(row)
        for name,value in [('TERMINAL.json',{'status':'COMPLETED'}),('RESULT.json',{'actual_data_estimation_calls':1}),
                           ('EXPORT.json',{'registry_event':{'design_id':'d0'},'estimates_page':{},'l3_meta':{}})]:atomic(root/name,value)
        path=self.registry/'TERMINAL_RUNS.json';old={'design_id':'old','run_id':'old__v1'}
        atomic(path,dict(runs=[old],pre_run_stops=[]))
        with patch.object(self.backend,'ctx',return_value=SimpleNamespace(card=SimpleNamespace(version='v1'))),patch('scripts.l2_pipeline.snapshots.manifest_for',return_value=None):
            with patch('scripts.l2_pipeline.autopilot.atomic',side_effect=OSError('invented crash before append')):
                with self.assertRaises(OSError):self.pilot.register_terminal(row,root)
            neighbour={'design_id':'neighbour','run_id':'neighbour__v1'}
            atomic(path,dict(runs=[old,neighbour],pre_run_stops=[]))
            self.pilot.register_terminal(row,root)
        self.assertEqual(read(path)['runs'][:2],[old,neighbour])
        self.assertEqual(len(read(path)['runs']),3)
        self.assertEqual(len(list(self.pilot.case_dir(row).glob('TERMINAL_REGISTRY_BEFORE_*.json'))),2)

    def test_dry_plan_existing_jobs_has_exactly_one_row_per_design(self):
        row=self.seed(1)[0];row['history_jobs']=self.backend.jobs(row,'history',self.pilot)
        atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=[row]))
        plan=self.pilot.tick(dry_run=True)
        self.assertEqual(len(plan['designs']),1)
        self.assertEqual(len(plan['designs'][0]['jobs']),2)

    def test_global_dispatch_reservation_counts_external_jobs(self):
        from types import SimpleNamespace
        from scripts.l2_pipeline.agents.dispatch import reserve_global
        budget=self.registry/'AUTOPILOT_WORKER_BUDGET.json';atomic(budget,{'max_workers':1})
        old=self.repo/'data/l2_pipeline/old/DISPATCH_ONCE.json';atomic(old,{'retries':0})
        new=self.repo/'data/l2_pipeline/new/DISPATCH_ONCE.json'
        def finish(seconds):atomic(old.parent/'DISPATCH_RESULT.json',{'status':'COMPLETED'})
        with patch('scripts.l2_pipeline.agents.dispatch.time.sleep',side_effect=finish) as sleep:
            reserve_global(SimpleNamespace(repo=str(self.repo)),new,{'retries':0})
        sleep.assert_called_once();self.assertTrue(new.exists())

    def test_local_calibration_budget_resumes_without_blocking_agent_jobs(self):
        rows=self.seed(4);self.backend.async_local=True
        launches=[]
        def launch(request):
            launches.append(str(request));return {'pid':1000+len(launches),'started_at':'invented'}
        self.backend.launch_local=launch
        for row in rows[:3]:
            for action in CHAIN[:CHAIN.index('calibrate')]:ensure_once(self.pilot.checkpoint(row,action),{})
        self.pilot.tick()
        self.assertEqual(len(launches),2)
        self.assertEqual(len(self.backend.launches),2)  # fourth design's A/B runs concurrently
        resumed=Autopilot(self.registry,self.backend,local_budget=2);resumed.tick()
        self.assertEqual(len(launches),2)
        first=Path(launches[0]);atomic(first.parent/'RESULT.json',dict(status='COMPLETED',value={'calibrated':True}))
        resumed.tick();self.assertEqual(len(launches),3)
        self.assertEqual(len(set(launches)),3)

    def test_async_terminal_waits_for_run_receipt_and_never_retries(self):
        row=self.seed(1)[0];self.backend.async_local=True
        for action in CHAIN[:CHAIN.index('execute')]:ensure_once(self.pilot.checkpoint(row,action),{})
        self.backend.launch_local=lambda p:{'pid':1000,'started_at':'invented'}
        self.pilot.tick()
        atomic(self.backend.root(row)/'EXECUTION_ONCE.json',{'status':'ATTEMPT_CONSUMED'})
        atomic(self.backend.root(row)/'TERMINAL.json',{'status':'COMPLETED'})
        self.pilot.tick();self.assertFalse(self.pilot.done(row,'execute'))
        self.assertNotIn(('d0','S7'),self.backend.actions)
        atomic(self.pilot.case_dir(row)/'local/execute/RESULT.json',dict(status='COMPLETED',value={'done':True}))
        self.pilot.tick();self.assertEqual(self.pilot.next_action(row),'FINISHED')
        self.assertNotIn(('d0','execute'),self.backend.actions)

    def test_async_failure_before_run_slot_keeps_exact_reason_and_never_retries(self):
        row=self.seed(1)[0];self.backend.async_local=True
        for action in CHAIN[:CHAIN.index('execute')]:ensure_once(self.pilot.checkpoint(row,action),{})
        launches=[]
        def launch(path):launches.append(path);return {'pid':1000,'started_at':'invented'}
        self.backend.launch_local=launch;self.pilot.tick()
        atomic(self.pilot.case_dir(row)/'local/execute/RESULT.json',dict(status='FAILED_NO_RETRY',reason='exact candidate hash mismatch'))
        self.pilot.tick();self.pilot.tick()
        self.assertEqual(len(launches),1)
        self.assertIn('exact candidate hash mismatch',read(self.registry/'HUB_QUEUE.json')['entries'][0]['reason'])

    def test_validated_v8b_uses_fresh_gate_and_never_emits_official_ab(self):
        row=self.seed(1)[0];row['route']='V8b'
        row['routing']=dict(intake_root='invented',fresh_jobs=self.backend.jobs(row,'audit',self.pilot))
        atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=[row]))
        with patch('scripts.l2_pipeline.intake.config.IntakeConfig.from_existing'),patch('scripts.l2_pipeline.intake.v8b_calendar.configure'),patch('scripts.l2_pipeline.intake.v8b_calendar.install'),patch('scripts.l2_pipeline.intake.fresh_gate.fresh_gate') as gate:
            self.drive();self.assertTrue(gate.called)
        self.assertEqual(self.pilot.next_action(row),'FINISHED')
        self.assertFalse(any('/history/' in p for p in self.backend.launches))

    def test_ingest_unvalidated_v8b_cannot_bypass_official_history(self):
        self.seed(0);cards=self.repo/'data/l2_pipeline/cards';cards.mkdir(parents=True)
        card=read(Path(__file__).parents[1]/'examples/design.json')
        card['analysis']['min_post_periods']=2
        card['automation']={'autopilot':{'route':'V8b'}};atomic(cards/'v8b.json',card)
        with self.assertRaisesRegex(ValueError,'existing validated'):ingest(cards,self.pilot)
        self.assertEqual(read(self.registry/'ACTIVE_DESIGNS.json')['designs'],[])

    def test_ingest_declared_v8b_can_adopt_its_prepared_packets_but_not_running_ones(self):
        self.seed(0);intake=self.repo/'data/l2_pipeline/intake';cards=intake/'cards';cards.mkdir(parents=True)
        card=read(Path(__file__).parents[1]/'examples/design.json');did=card['design_id']
        card['analysis']['min_post_periods']=2
        card['automation']={'autopilot':{'route':'V8b','intake_root':str(intake)}}
        cp=cards/(did+'.json');atomic(cp,card)
        atomic(intake/'imports'/did/'PANEL_IMPORT.json',{'units':['AL','AK']})
        job=intake/'fresh_audit_jobs/S1'/did/'auditor.md';job.parent.mkdir(parents=True)
        job.write_text('invented fresh packet');out=intake/'work'/did/'auditor'
        atomic(job.with_suffix('.job.json'),dict(design_id=did,role='auditor',output_dir=str(out),
            contract=dict(card_sha256=sha(cp))))
        with patch('scripts.l2_pipeline.intake.config.IntakeConfig.from_existing'),patch('scripts.l2_pipeline.intake.v8b_calendar.configure'),patch('scripts.l2_pipeline.intake.v8b_calendar.install'),patch('scripts.l2_pipeline.intake.fresh_gate.fresh_gate'):
            self.assertEqual(ingest(cards,self.pilot)['ingested'],1)
        atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=[]));atomic(out/'DISPATCH_ONCE.json',{'retries':0})
        with self.assertRaisesRegex(ValueError,'Duplicate'):ingest(cards,self.pilot)


class OfficialBridgeTests(unittest.TestCase):
    def setUp(self):
        from scripts.l2_pipeline.intake.history_routing import emit_pair
        from scripts.l2_pipeline.agents.contract_tests import golden_output
        from scripts.l2_pipeline.agents import collect_job
        from scripts.l2_pipeline.contracts import StageContext,DesignCard
        from scripts.l2_pipeline.autopilot_history import prepare
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.card=read(Path(__file__).parents[1]/'examples/design.json')
        self.card['treatment'].update(universe=['U'+str(n) for n in range(17)],excluded_units=[])
        self.card['mechanism']=self.card['treatment']['predicate']='one'
        self.card['transition_year_drop']=True
        self.path=ensure_once(self.root/'card.json',self.card)
        self.jobs=emit_pair(self.card,self.card['treatment']['universe'],root=self.root/'route',attempt_id='test')
        for job in self.jobs:
            path=Path(job.output_path);path.parent.mkdir(parents=True,exist_ok=True)
            output=golden_output(job)
            for record in output['records'][12:]:
                record.update(status='never_treated',start_period=None,evidence_status='absent (V2_LIST)',
                    absence_basis=dict(official_code_history_no_earlier_enactment=True,
                        no_earlier_in_compilations_on_disk=True,no_search_log_contradiction=True))
                record['evidence_refs'][0]['supports']='absence_code_history'
            path.write_text(json.dumps(output));collect_job(job.job_path)
        self.ctx=StageContext(DesignCard.from_mapping(self.card,self.path),self.root/'work',Path(__file__).resolve().parents[3])
        self.policy=dict(seed=55,excluded_units=['U0','U1'],sample_size=10)

    def prepare(self):
        from scripts.l2_pipeline.autopilot_history import prepare
        from scripts.l2_pipeline.agents.jobs import load_job
        result=prepare(self.ctx,[j.job_path for j in self.jobs],self.policy,self.root/'audit_jobs')
        return load_job(Path(result['jobs'][0]))

    def audit(self,job,errors=0):
        from scripts.l2_pipeline.agents.contract_tests import golden_output,check_job_types
        from scripts.l2_pipeline.agents import collect_job
        check_job_types([job])
        output=golden_output(job)
        for check in output['checks'][:errors]:check.update(status='known_error',corrected_start_period=2003)
        path=Path(job.output_path);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(output));collect_job(job.job_path)

    def test_real_emitter_collector_fresh_v3_and_corrected_operation_clock(self):
        from scripts.l2_pipeline.autopilot_history import accept
        before={j.output_path:sha(j.output_path) for j in self.jobs}
        job=self.prepare();self.audit(job,errors=1)
        self.assertFalse(set(job.contract['sample'])&{'U0','U1'})
        self.assertEqual(len(job.contract['sample']),10)
        result=accept(self.ctx,job.job_path);self.assertEqual(result.disposition,'ready')
        records=read(self.ctx.design_dir/'TREATMENT.json')['records']
        corrected=next(r for r in records if r['unit']==job.contract['sample'][0])
        self.assertEqual(corrected['start_period'],2002)
        self.assertEqual(corrected['audited_first_full_start_period'],2003)
        self.assertEqual(before,{j.output_path:sha(j.output_path) for j in self.jobs})

    def test_existing_v3_threshold_is_not_relaxed(self):
        from scripts.l2_pipeline.autopilot_history import accept
        job=self.prepare();self.audit(job,errors=3)
        with self.assertRaisesRegex(ValueError,'V3 failed'):accept(self.ctx,job.job_path)
        self.assertFalse((self.ctx.design_dir/'TREATMENT.json').exists())

    def test_fresh_audit_insufficient_after_exclusions_never_redraws(self):
        self.policy['excluded_units']=['U0','U1','U2']
        self.assertEqual(set(self.prepare().contract['sample']), {'U'+str(i) for i in range(3,12)})

    def test_operation_year_disagreement_is_excluded_under_c42(self):
        # Independent date disagreements cannot be hidden by identical binary tuples.
        from scripts.l2_pipeline.autopilot_history import prepare
        from scripts.l2_pipeline.agents import collect_job
        job=self.jobs[1];out=Path(job.output_path);value=read(out)
        value['records'][0]['best_evidence_start_year']+=1
        value['records'][0]['evidence_refs'][0]['year']+=1
        out.write_text(json.dumps(value))
        for name in ('COLLECTION.json','NORMALIZATION.json','NORMALIZED_HISTORY.json'):
            (out.parent/name).unlink()
        collect_job(job.job_path)
        fresh=self.prepare()
        report=read(self.ctx.design_dir/'RECONCILIATION.json')
        self.assertIn('best_evidence_year_conflict', report['excluded_conflict_state_reasons']['U0'])
        self.assertNotIn('U0',fresh.contract['sample'])


if __name__=='__main__':unittest.main()
