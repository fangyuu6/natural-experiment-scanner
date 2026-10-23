"""C55 mocked executors and synthetic election-grid geometry only."""
import copy, json, os, tempfile, unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from scripts.l2_pipeline.agents import sonnet_queue as q
from scripts.l2_pipeline.autopilot import Backend,Autopilot,atomic,read
from scripts.l2_pipeline.tests import test_agents as fixtures
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.biennial import support,declaration,periods,ADAPTER
from scripts.l2_pipeline.engine.design import build_design
from scripts.l2_pipeline.engine.ri import predraw_labels,relabeled_design,pre_family
from scripts.l2_pipeline.coverage import coverage_bounded_card,normalize_series_breaks
from scripts.l2_pipeline.tests import test_autopilot as pilot_fixtures

CFG=dict(min_treated=6,min_controls=5,min_cohorts=2,min_pre_periods=3,min_post_periods=2,
         controls='never_treated',support_gap_rule='drop_affected_state_v7',support_adapter=ADAPTER)

class QueueTests(unittest.TestCase):
    setUp=fixtures.AgentTests.setUp
    tearDown=fixtures.AgentTests.tearDown
    emit=fixtures.AgentTests.emit
    put=fixtures.AgentTests.put
    forecast=fixtures.AgentTests.forecast

    def packet(self,role='Card'):
        job=self.emit('S5',role);root=self.root/'queue'
        return job,root,q.enqueue(job.job_path,dict(design_id=job.design_id,attempt_id='invented'),root)

    def test_packet_full_prompt_contract_and_read_scope(self):
        job,root,receipt=self.packet();p=Path(receipt['packet']);binding=q.read(p/'PACKET.json')
        self.assertEqual((p/'PROMPT.md').read_bytes(),Path(job.job_path).read_bytes())
        self.assertEqual(binding['output_path'],job.output_path)
        self.assertEqual(q.read(p/'OUTPUT_SCHEMA.json')['contract'],job.contract)
        self.assertEqual(binding['allowed_read_paths'],[])
        self.assertEqual((p/'WORKER_README.md').read_text(),q.WORKER_INSTRUCTIONS)
        self.assertEqual(binding['lease_protocol']['lease_file'],'LEASE.json; token, worker, claimed_at, heartbeat_at')
        self.assertEqual(q.enqueue(job.job_path,binding['row'],root),receipt)

    def test_atomic_double_claim_and_heartbeat(self):
        _,root,r=self.packet()
        with self.assertRaises(ValueError):q.claim(root,'one','../../escape')
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda w:q.claim(root,w,r['job_id'],now=100),['one','two']))
        self.assertEqual(sum(x is not None for x in results),1)
        lease=next(x for x in results if x)
        q.heartbeat(root,r['job_id'],lease['token'],now=1000)
        self.assertEqual(q.reap(root,now=1000+q.DEAD_SECONDS-1),[])
        with self.assertRaises(ValueError):q.heartbeat(root,r['job_id'],'wrong',now=1001)

    def test_expiry_once_then_hub_and_stale_owner_refuses(self):
        job,root,r=self.packet();a=q.claim(root,'one',now=100)
        events=q.reap(root,now=100+q.LEASE_SECONDS)
        self.assertEqual(events[0]['status'],'pending')
        with self.assertRaises(ValueError):q.finish(root,r['job_id'],a['token'],now=5501)
        b=q.claim(root,'two',now=5501)
        with self.assertRaises(ValueError):q.heartbeat(root,r['job_id'],a['token'],now=5502)
        Path(job.output_path).parent.mkdir(parents=True,exist_ok=True);Path(job.output_path).write_text('{}')  # a worker that wrote something is not dead: second expiry goes to the hub
        self.assertEqual(q.reap(root,now=5501+q.LEASE_SECONDS)[0]['status'],'hub')
        self.assertIsNone(q.claim(root,'three'))
        self.assertEqual(q.reap(root,now=99999),[])
        with patch.object(q,'root_for',return_value=root):
            with self.assertRaisesRegex(ValueError,'HUB_QUEUE'):q.state(self.emit('S5','Card').job_path,self.ctx.repo)

    def test_worker_completion_same_collector_and_bad_contract_rejected(self):
        job,root,r=self.packet();lease=q.claim(root,'worker');self.put(job,self.forecast(job))
        with patch.object(q,'root_for',return_value=root):
            backend=Backend(self.ctx.repo)
            self.assertEqual(backend.job_state(job.job_path),'running')
            q.finish(root,r['job_id'],lease['token'])
            self.assertEqual(backend.job_state(job.job_path),'done')
            receipt=backend.collect(job.job_path);self.assertTrue(receipt['validated'])
            self.assertEqual(receipt,backend.collect(job.job_path))
        job2,root,r2=self.packet('Roth');lease=q.claim(root,'bad');value=self.forecast(job2)
        value['blindness_attestation']['other_submissions_read']=True;self.put(job2,value)
        q.finish(root,r2['job_id'],lease['token'])
        with self.assertRaises(ValueError):Backend(self.ctx.repo).collect(job2.job_path)

    def test_no_backend_change_after_codex_dispatch(self):
        job=self.emit('S5','Card');out=Path(job.output_dir);out.mkdir(parents=True,exist_ok=True)
        atomic(out/'DISPATCH_ONCE.json',dict(invented=True))
        with self.assertRaisesRegex(ValueError,'consumed'):q.enqueue(job.job_path,{},self.root/'queue')
        pilot=Autopilot(self.root.resolve()/'data/l2_pipeline/registry',backend=Backend(self.root.resolve()),agent_backends='sonnet_queue')
        self.assertEqual(pilot.executor(dict(design_id='D'),job.job_path),'codex')

    def test_completion_rename_crash_retries_or_recovers_without_requeue(self):
        for role,recover in [('Card',False),('Roth',True)]:
            job,root,r=self.packet(role);lease=q.claim(root,'worker_'+role,r['job_id'],now=100)
            self.put(job,self.forecast(job))
            with patch.object(q.os,'rename',side_effect=OSError('invented rename crash')):
                with self.assertRaises(OSError):q.finish(root,r['job_id'],lease['token'],now=101)
            if recover:
                self.assertEqual(q.reap(root,now=100+q.LEASE_SECONDS)[0]['status'],'done')
            else:q.finish(root,r['job_id'],lease['token'],now=102)
            self.assertTrue((root/'done'/r['job_id']).exists())
            q.finish(root,r['job_id'],lease['token'],now=99999)
            self.assertEqual(q.read(root/'done'/r['job_id']/'STATE.json')['expiries'],0)

    def test_expiry_rename_crash_counts_distinct_lease_once(self):
        job,root,r=self.packet();lease=q.claim(root,'worker',now=100)
        with patch.object(q.os,'rename',side_effect=OSError('invented rename crash')):
            with self.assertRaises(OSError):q.reap(root,now=5500)
        self.assertEqual(q.reap(root,now=5501)[0]['status'],'pending')
        self.assertEqual(q.read(root/'pending'/r['job_id']/'STATE.json')['expiries'],1)
        q.claim(root,'next',now=5502)
        self.assertEqual(q.reap(root,now=5503),[])
        Path(job.output_path).parent.mkdir(parents=True,exist_ok=True);Path(job.output_path).write_text('{}')  # not dead: the second expiry is final
        self.assertEqual(q.reap(root,now=10902)[0]['status'],'hub')

    def test_malformed_submission_reaches_canonical_collector(self):
        job,root,r=self.packet();lease=q.claim(root,'worker')
        output=Path(job.output_path);output.parent.mkdir(parents=True,exist_ok=True);output.write_text('malformed JSON')
        self.assertEqual(q.finish(root,r['job_id'],lease['token'])['status'],'correction_required')
        output.write_text('still malformed JSON')
        q.finish(root,r['job_id'],lease['token'])
        from scripts.l2_pipeline.agents.replacement import ContractRejection
        with self.assertRaises(ContractRejection) as caught:Backend(self.ctx.repo).collect(job.job_path)
        self.assertEqual(caught.exception.kind,'SCHEMA_FORMAT')

class RoutingTests(unittest.TestCase):
    def test_stable_ratio_per_role_override(self):
        routes=[q.route('D'+str(i),role) for i in range(2000) for role in ['A','B','auditor','third_reader','purpose_verifier','Card','Finkelstein','Roth','Liebman','Boehmke','reviewer','reviewer']]
        ratio=routes.count('sonnet_queue')/len(routes)
        self.assertTrue(.64<ratio<.69,ratio)
        self.assertEqual(q.route('D','A'),q.route('D','A'))
        self.assertEqual(q.route('D','A',{'A':'codex'}),'codex')
        with self.assertRaises(ValueError):q.route('D','A',{'A':'bad'})

    def test_claude_only_routes_every_role_to_sonnet_queue_and_refuses_a_codex_launch(self):
        # Owner 2026-10-15: no Codex work. ops.watchdog sets L2_CLAUDE_ONLY=1 for the loop only.
        roles=['A','B','auditor','third_reader','purpose_verifier','Card','Finkelstein','Roth','reviewer']
        def launch_attempt():
            with tempfile.TemporaryDirectory() as tmp:
                repo=Path(tmp).resolve();pilot=Autopilot(repo/'data/l2_pipeline/registry',backend=Backend(repo))
                job=repo/'job.md';atomic(job.with_suffix('.job.json'),dict(role='A',output_dir=str(repo/'output')))
                pilot.pending=[(dict(design_id='D',attempt_id='v1'),str(job))]
                with patch.object(pilot,'executor',return_value='codex'),patch.object(pilot,'stop_record',return_value=None),patch.object(pilot,'queue_reconciliations'),patch.object(pilot,'stop') as stop,patch.object(pilot.backend,'global_running',return_value=0),patch.object(pilot.backend,'job_state',return_value='pending'),patch.object(pilot.backend,'launch',return_value=dict(pid=1,started_at='t',argv=[])) as launch:
                    pilot.schedule()
                return launch.call_count,stop.call_args
        with patch.dict(os.environ):  # restored on exit: no leak into other tests
            os.environ.pop('L2_CLAUDE_ONLY',None)
            self.assertFalse(q.claude_only());self.assertEqual(q.route('D','A'),'codex')
            calls,stop=launch_attempt();self.assertEqual((calls,stop),(1,None))  # default mode keeps the Codex launch
            for value in ('0','','true','yes'):  # only the exact value 1 switches the mode
                os.environ['L2_CLAUDE_ONLY']=value;self.assertFalse(q.claude_only());self.assertEqual(q.route('D','A'),'codex')
            os.environ['L2_CLAUDE_ONLY']='1';self.assertTrue(q.claude_only())
            self.assertEqual({q.route('D'+str(i),role) for i in range(200) for role in roles},{'sonnet_queue'})
            self.assertEqual(q.route('D','A',{'A':'sonnet_queue'}),'sonnet_queue')
            # A registered contract override is not rewritten; the launch guard then stops that design visibly.
            self.assertEqual(q.route('D','Roth',{'Roth':'codex'}),'codex')
            calls,stop=launch_attempt();self.assertEqual(calls,0);self.assertIn('Claude-only: Codex launch refused',stop.args[-1])

    def test_queue_not_blocked_by_codex_budget_and_second_expiry_escalates(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp).resolve();reg=repo/'data/l2_pipeline/registry';pilot=Autopilot(reg,backend=Backend(repo))
            self.assertEqual(pilot.budget,6);pilot.budget=1
            job=repo/'job.md';atomic(job.with_suffix('.job.json'),dict(role='A',output_dir=str(repo/'output')))
            row=dict(design_id='D',attempt_id='v1');pilot.pending=[(row,str(job))]
            with patch.object(pilot,'executor',return_value='sonnet_queue'),patch.object(pilot,'stop_record',return_value=None),patch.object(pilot,'queue_reconciliations'),patch.object(pilot.backend,'global_running',return_value=1),patch.object(pilot.backend,'job_state',return_value='pending'),patch.object(q,'enqueue',return_value=dict(backend='sonnet_queue',job_id='mock')) as enqueue:
                pilot.schedule();enqueue.assert_called_once()
            packet=q.root_for(repo)/'hub'/'expired';packet.mkdir(parents=True)
            atomic(packet/'PACKET.json',dict(row=row,job_path=str(job)))
            atomic(reg/'ACTIVE_DESIGNS.json',dict(designs=[row]))
            # F10 (2026-10-18): a packet that reached the hub queue PARKs the design (reopenable); it no longer STOPs it.
            pilot.pending=[]  # the first tick's job is done with; this tick is about the hub packet only
            with patch.object(pilot,'stop') as stop,patch.object(pilot,'queue_reconciliations'),patch.object(pilot.backend,'global_running',return_value=0),patch.object(pilot,'executor',return_value='codex'):
                pilot.schedule();stop.assert_not_called()
            parked=read(pilot.case_dir(row)/'PARKED.json');self.assertIn('expired twice',parked['reason']);self.assertEqual(parked['metrics']['kind'],'QUEUE_PARK')

class BiennialTests(unittest.TestCase):
    def fixture(self,enabled=False):
        years=list(range(1980,2003,2));out=dict(frequency='biennial',years=[1980,2002],period_years=years)
        rows=[dict(unit=f'T{i}',status='treated',start_period=g) for i,g in enumerate([1987]*6+[1992]*6)]
        rows += [dict(unit=f'C{i}',status='never_treated',start_period=None) for i in range(6)]
        keys={(r['unit'],y) for r in rows for y in years}
        summary=support(keys,rows,CFG,out,enabled)
        d=declaration(rows,CFG,out,summary,key='synthetic',variant='primary',transition_year_drop=enabled)
        return out,rows,keys,summary,d

    def test_support_numerical_and_fixed_ri_use_same_even_grid(self):
        out,rows,keys,s,d=self.fixture()
        self.assertTrue(s['passed'],s.get('issues'));self.assertEqual(s['outcome_values_parsed'],0)
        self.assertEqual(d['cohorts']['T0'],4);self.assertEqual(d['cohorts']['T6'],6)
        design=build_design(d,[(u,y,0.,None) for u,y in keys])
        self.assertEqual(s['cell_inventory'],design['cells'])
        self.assertEqual(s['analysis_mask'],[[u,out['period_years'][t]] for u,t in sorted(design['mask'])])
        self.assertTrue(pre_family(design))
        self.assertEqual(predraw_labels(design,3),predraw_labels(design,3))
        fake=relabeled_design(d,design,predraw_labels(design,1)[0])
        self.assertTrue(all(c['n_control']>=5 for c in fake['cells']))
        self.assertTrue(all(0<=t<len(out['period_years']) for _,t in fake['mask']))

    def test_operation_year_drop_odd_and_even_events(self):
        out,rows,keys,s,d=self.fixture(True)
        self.assertTrue(s['passed'],s.get('issues'))
        self.assertEqual(d['cohorts']['T0'],4) # odd 1987 -> 1988, once
        self.assertEqual(d['cohorts']['T6'],7) # even 1992 drop -> 1994
        design=build_design(d,[(u,y,0.,None) for u,y in keys]);self.assertEqual(design['cells'],s['cell_inventory'])
        self.assertIn(['T0',1988],s['analysis_mask']);self.assertNotIn(['T6',1992],s['analysis_mask'])

    def test_missing_election_and_invalid_grid_do_not_gain_support(self):
        out,rows,keys,_,_=self.fixture();keys={(u,y) for u,y in keys if y!=1982}
        self.assertFalse(support(keys,rows,CFG,out)['passed'])
        for grid in ([1980,1983,1984],[1980,1984,1986]):
            with self.assertRaises(ValueError):periods(dict(out,period_years=grid))

class WindowTests(unittest.TestCase):
    def test_opioid_definition_is_window_edge_for_all_designs(self):
        card=dict(outcome=dict(connector='opioid',series_id='cdc_opioid_dispensing_rate',years=[2006,2024]),treatment=dict(source_window=dict(start_year=2000,end_year=2024)))
        catalog={'opioid':dict(coverage_start=2006,coverage_end=2024,series_id='cdc_opioid_dispensing_rate')}
        bounded=coverage_bounded_card(card,catalog=catalog)
        self.assertEqual(bounded['outcome']['years'],[2006,2018])
        self.assertEqual(bounded['automation']['coverage_window']['connector_bounds']['series_breaks'][0]['year'],2019)
        self.assertEqual(coverage_bounded_card(bounded,catalog=catalog),bounded)
        late=copy.deepcopy(card);late['outcome']['years']=[2020,2024]
        self.assertTrue(coverage_bounded_card(late,catalog=catalog)['automation']['coverage_window']['parked'])

    def test_generic_definition_break_anchors_first_requested_segment(self):
        card=dict(outcome=dict(connector='series',years=[2000,2020]),treatment=dict(source_window=dict(start_year=2000,end_year=2020)))
        catalog={'series':dict(coverage_start=2000,coverage_end=2020,series_breaks=[dict(year=2010,definition_change=True)])}
        self.assertEqual(coverage_bounded_card(card,catalog=catalog)['outcome']['years'],[2000,2009])

    def breaks_card(self,breaks,years=(2000,2020)):
        card=dict(outcome=dict(connector='series',years=list(years)),treatment=dict(source_window=dict(start_year=years[0],end_year=years[1])))
        return card,{'series':dict(coverage_start=2000,coverage_end=2020,series_breaks=breaks)}

    def test_list_pair_series_breaks_normalize_without_dropping_breaks(self):
        # connectors/c70_build.py writes [year, note] pairs.
        pairs=[[2011,'reporting_practice_change'],(2021,'NIBRS_transition')]
        self.assertEqual(normalize_series_breaks(pairs),[dict(year=2011,note='reporting_practice_change'),dict(year=2021,note='NIBRS_transition')])
        self.assertEqual(normalize_series_breaks(None),[])
        # A pair has no definition_change flag, so it is not a window edge...
        card,catalog=self.breaks_card([[2011,'x'],[2012,'y']])
        bounded=coverage_bounded_card(card,catalog=catalog)
        self.assertEqual(bounded['outcome']['years'],[2000,2020])
        # ...while a flagged dict next to pairs still is, and the pairs stay recorded.
        card,catalog=self.breaks_card([[2011,'x'],dict(year=2015,definition_change=True)])
        bounded=coverage_bounded_card(card,catalog=catalog)
        self.assertEqual(bounded['outcome']['years'],[2000,2014])
        self.assertEqual(bounded['automation']['coverage_window']['connector_bounds']['series_breaks'][0],[2011,'x'])
        self.assertEqual(coverage_bounded_card(bounded,catalog=catalog),bounded)

    def test_real_v15_pair_breaks_do_not_crash_coverage(self):
        from scripts.l2_pipeline.connectors import outcome_v15,outcome_v5
        data=outcome_v15.catalog(verify_sources=False);name='outcome_v15_fbi_ucr_larceny_rate'
        entry=dict(outcome_v5.entries(data)[name]);entry['series_breaks']=data['series'][entry['series_id']]['series_breaks']
        self.assertTrue(all(isinstance(b,list) for b in entry['series_breaks']))
        card=dict(outcome=dict(connector=name,series_id=entry['series_id'],years=[1990,2016]),treatment=dict(source_window=dict(start_year=1990,end_year=2020)))
        bounded=coverage_bounded_card(card,catalog={name:entry})
        self.assertEqual(bounded['automation']['coverage_window']['status'],'bounded')
        self.assertEqual(bounded['outcome']['years'],[1990,2016])

    def test_dict_series_breaks_unchanged(self):
        breaks=[dict(year=2010,definition_change=True,before='a',after='b'),dict(year=2015)]
        result=normalize_series_breaks(breaks)
        self.assertEqual(result,breaks);self.assertIs(result[0],breaks[0])
        card,catalog=self.breaks_card(breaks)
        self.assertEqual(coverage_bounded_card(card,catalog=catalog)['outcome']['years'],[2000,2009])

    def test_unsupported_series_break_shapes_raise_value_error(self):
        for bad in ('break 2011',[2011],[2011,'x','y'],['2011','x'],[True,'x'],[2011,5],None,7):
            with self.subTest(entry=bad),self.assertRaisesRegex(ValueError,'series_breaks entry 1 must be a dict or a'):
                normalize_series_breaks([dict(year=2010,definition_change=True),bad])
        with self.assertRaisesRegex(ValueError,'series_breaks must be a list'):normalize_series_breaks('abc')
        card,catalog=self.breaks_card([[2011,'x'],'free text'])
        self.assertFalse(coverage_bounded_card(card,catalog=catalog)['automation']['coverage_window']['parked'])


class CodeFixSupersessionTests(unittest.TestCase):
    """C78: a STOP caused by a fixed code defect is superseded once, never redispatched."""
    setUp=pilot_fixtures.AutopilotTests.setUp
    seed=pilot_fixtures.AutopilotTests.seed
    REASON="AttributeError: 'list' object has no attribute 'get'"

    def stopped(self,reason=REASON,action='purpose'):
        row=self.seed(1)[0];case=self.pilot.case_dir(row)
        inventory=ensure_once(case/'jobs/purpose.json',dict(jobs=[str(case/'purpose_jobs/purpose_verifier.md')]))
        self.pilot.stop(row,action,reason);return row,case,inventory

    def decisions(self,name):
        lines=(self.registry/'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()
        return sum(json.loads(l)['decision']==name for l in lines)

    def test_tag_is_narrow_and_names_the_code_task(self):
        from scripts.l2_pipeline.maintenance import reason_tags,require_match,CODE_FIXES
        self.assertEqual(reason_tags(self.REASON),['code_fix_c78']);self.assertEqual(CODE_FIXES['code_fix_c78']['task'],'C78')
        self.assertEqual(reason_tags("AttributeError: 'NoneType' object has no attribute 'get'"),[])
        self.assertEqual(require_match(self.REASON,['code_fix_c78']),['code_fix_c78'])

    def test_supersedes_once_and_reuses_existing_job_without_dispatch(self):
        from scripts.l2_pipeline.maintenance import resume_code_fix
        row,case,inventory=self.stopped();original=inventory.read_bytes()
        self.assertEqual(self.pilot.next_action(row),'STOP')
        self.assertTrue(resume_code_fix(self.pilot,row))
        self.assertIsNone(self.pilot.stop_record(row));self.assertNotEqual(self.pilot.next_action(row),'STOP')
        marker,evidence=case/'STOP_SUPERSEDED_C62.json',case/'CODE_FIX_C78.json'
        snapshot={p:p.read_bytes() for p in (marker,evidence,case/'STOP.json',self.registry/'HUB_QUEUE.json')}
        proof=json.loads(evidence.read_text())
        self.assertEqual((proof['code_task'],proof['dispatches'],proof['reused_jobs']),('C78',0,json.loads(original)['jobs']))
        self.assertEqual(json.loads(self.registry.joinpath('HUB_QUEUE.json').read_text())['entries'][0]['status'],'SUPERSEDED_C62')
        self.assertEqual(self.decisions('SUPERSEDED_C62'),1)
        # Second call and a new tick-level call are no-ops; nothing was launched or performed.
        self.assertFalse(resume_code_fix(self.pilot,row))
        self.assertEqual({p:p.read_bytes() for p in snapshot},snapshot)
        self.assertEqual(self.decisions('SUPERSEDED_C62'),1)
        self.assertEqual(inventory.read_bytes(),original)
        self.assertEqual((self.backend.launches,self.backend.actions),([],[]))

    def test_recurrence_after_supersession_is_not_superseded_again(self):
        from scripts.l2_pipeline.maintenance import resume_code_fix
        row,case,_=self.stopped();self.assertTrue(resume_code_fix(self.pilot,row))
        self.pilot.stop(row,'purpose',self.REASON)  # same defect again -> new STOP_C62.json
        self.assertEqual(self.pilot.stop_record(row)['reason'],self.REASON)
        self.assertFalse(resume_code_fix(self.pilot,row));self.assertEqual(self.pilot.next_action(row),'STOP')
        self.assertEqual(self.decisions('SUPERSEDED_C62'),1)

    def test_other_reasons_actions_and_missing_or_consumed_state_stay_stopped(self):
        from scripts.l2_pipeline.maintenance import resume_code_fix
        row,case,_=self.stopped("AttributeError: 'NoneType' object has no attribute 'get'")
        self.assertFalse(resume_code_fix(self.pilot,row));self.assertEqual(self.pilot.next_action(row),'STOP')
        for name,action,make in [('other action','history',lambda c,r:None),
                                 ('no job inventory','purpose',lambda c,r:(c/'jobs/purpose.json').unlink()),
                                 ('frozen','purpose',lambda c,r:ensure_once(self.pilot.backend.root(r)/'FREEZE_PREP.json',dict(frozen=True)))]:
            with self.subTest(name):
                self.setUp();row,case,_=self.stopped(action=action);make(case,row)
                self.assertFalse(resume_code_fix(self.pilot,row));self.assertEqual(self.pilot.next_action(row),'STOP')
                self.assertEqual(self.decisions('SUPERSEDED_C62'),0);self.assertFalse((case/'CODE_FIX_C78.json').exists())

    def test_tick_runs_the_code_fix_resume_each_loop(self):
        from scripts.l2_pipeline import maintenance
        row,case,_=self.stopped()
        with patch.object(maintenance,'resume_code_fix',return_value=False) as resume:
            self.pilot.tick()
        resume.assert_called_with(self.pilot,row)
