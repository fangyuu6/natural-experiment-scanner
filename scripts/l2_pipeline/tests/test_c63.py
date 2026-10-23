"""Synthetic C63 restart, authorship, scope and odd-wave contracts."""
import csv,gzip,json,os,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts.l2_pipeline.ops import watchdog as w
from scripts.l2_pipeline.agents import packet_scope as scope
from scripts.l2_pipeline.storage import ensure_once,sha

class RehearsalTests(unittest.TestCase):
    def test_s4_rebase_verifies_bytes_and_does_not_copy_later_receipts(self):
        from scripts.l2_pipeline.c63_rehearsal import copy_s4_receipt,rehearsal_id
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'source';root=Path(d)/'copy';source.mkdir();root.mkdir()
            for p in (source,root):(p/'SPEC.json').write_text('{}')
            receipt=ensure_once(source/'receipts/S4/1.json',dict(result=dict(artifacts=[dict(path=str(source/'SPEC.json'),sha256=sha(source/'SPEC.json'))])))
            old=sha(receipt);ensure_once(source/'receipts/S5/2.json',{})
            copy_s4_receipt(source,root)
            new=json.loads((root/'receipts/S4/1.json').read_text())
            self.assertEqual(new['result']['artifacts'][0]['path'],str((root/'SPEC.json').resolve()))
            self.assertEqual(old,sha(receipt));self.assertFalse((root/'receipts/S5').exists())
            (root/'SPEC.json').write_text('changed')
            with self.assertRaises(ValueError):copy_s4_receipt(source,root)
            with self.assertRaises(ValueError):rehearsal_id(dict(final_manifest_sha256='hash',rehearsal_id='../outside'))

class RestartTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.reg=self.root/'data/l2_pipeline/registry';self.reg.mkdir(parents=True)
        (self.reg/'AUTOPILOT_STOP').touch()
        (self.reg/'AUTOPILOT_LEDGER.jsonl').write_text(json.dumps(dict(decision='CLEAN_STOP',time='2026-10-11T12:00:00+00:00'))+'\n')
        self.report=ensure_once(self.root/'data/l2_pipeline/autopilot/c63/TEST_REPORT.json',dict(invented=True))
        self.evidence=dict(no_archival_fits=True,runs=[dict(command=['python','--no-archival-fits']),dict(command=['python','-O','--no-archival-fits'])])
    def test_restart_requires_newest_report_both_modes_and_no_lock(self):
        with patch('scripts.l2_pipeline.registration._check_evidence',return_value=self.evidence):
            self.assertEqual(w.eligible(self.root)[0],self.report)
            (self.reg/'CODE_TASK.lock').touch()
            with self.assertRaisesRegex(ValueError,'CODE_TASK'):w.eligible(self.root)
            (self.reg/'CODE_TASK.lock').unlink()
            other=ensure_once(self.report.parent/'TEST_REPORT_new.json',{})
            with self.assertRaisesRegex(ValueError,'newest'):w.eligible(self.root,self.report)
            self.evidence['runs'][1]['command']=['python','--no-archival-fits']
            with self.assertRaisesRegex(ValueError,'Both'):w.eligible(self.root)
    def test_crash_or_stale_clean_receipt_never_restarts(self):
        ensure_once(self.report.parent/'RESTART_RECEIPT.json',dict(time='2026-10-11T13:00:00+00:00'))
        with self.assertRaisesRegex(ValueError,'no CLEAN_STOP'):w.clean_stop(self.root)
    def test_restart_once_starts_the_loop_claude_only(self):
        # Owner 2026-10-15: the watchdog starts the loop with sonnet_queue and L2_CLAUDE_ONLY=1 (child only); no process starts here.
        repo=self.root.resolve()
        with patch.dict(os.environ):
            os.environ.pop('L2_CLAUDE_ONLY',None)
            with patch('scripts.l2_pipeline.registration._check_evidence',return_value=self.evidence),patch.object(w.subprocess,'Popen',return_value=SimpleNamespace(pid=4242)) as popen:
                receipt=w.restart_once(repo)
            self.assertNotIn('L2_CLAUDE_ONLY',os.environ)  # the watchdog's own environment is not changed
        popen.assert_called_once();command=popen.call_args.args[0];env=popen.call_args.kwargs['env']
        self.assertEqual(command[command.index('--agent-backends')+1],'sonnet_queue');self.assertNotIn('mixed',command)
        self.assertEqual(command[:3],[str(repo/'.venv_did/bin/python'),'-B','-m']);self.assertEqual(env['L2_CLAUDE_ONLY'],'1')
        saved=json.loads((repo/'data/l2_pipeline/registry/WATCHDOG_RESTART.json').read_text())
        self.assertEqual((saved['status'],saved['agent_backends'],saved['scheduler_pid']),('STARTED','sonnet_queue',4242))
        self.assertEqual(saved['command'],command);self.assertEqual(receipt,saved)
        self.assertEqual(json.loads((repo/'data/l2_pipeline/autopilot/c63/RESTART_RECEIPT.json').read_text()),saved)
        self.assertFalse((repo/'data/l2_pipeline/registry/AUTOPILOT_STOP').exists())
    def test_four_daily_windows_token_ownership_and_stale_evidence(self):
        now=datetime(2026,10,11,15,tzinfo=timezone.utc)
        for i in range(4):
            lock=w.begin_code_task(str(i),self.root,now)
            with self.assertRaises(ValueError):w.begin_code_task('other',self.root,now)
            with self.assertRaises(ValueError):w.end_code_task('wrong',self.root)
            with patch('scripts.l2_pipeline.registration._check_evidence',side_effect=ValueError('hash changed')):
                with self.assertRaises(ValueError):w.end_code_task(lock['token'],self.root)
            self.assertTrue((self.reg/'CODE_TASK.lock').exists())
            with patch('scripts.l2_pipeline.registration._check_evidence',return_value=self.evidence):w.end_code_task(lock['token'],self.root)
        with self.assertRaisesRegex(ValueError,'limit'):w.begin_code_task('fifth',self.root,now)
    def test_actual_evidence_rejects_code_change(self):
        from scripts.l2_pipeline.registration import _check_evidence
        self.report.write_text(json.dumps(dict(schema='l2_pipeline_test_evidence_v1',passed=True,code_inventory={},runs=[{'exit_code':0}]*2)))
        with patch('scripts.l2_pipeline.registration.code_inventory',return_value={'code':'changed'}):
            with self.assertRaises(ValueError):_check_evidence(self.report)

class ScopeTests(unittest.TestCase):
    def test_scope_copies_only_allowlist_and_refuses_traversal_or_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);a=root/'allowed';a.write_text('metadata');(root/'secret').write_text('unrelated')
            packet=root/'packet';ensure_once(packet/'PACKET.json',dict(allowed_read_paths=[str(a)]))
            staged=scope.stage(packet);relative=staged['files'][0]['staged']
            self.assertEqual(scope.read_allowed(packet,relative),b'metadata')
            with self.assertRaises(ValueError):scope.read_allowed(packet,'../secret')
            (packet/relative).chmod(0o644);(packet/relative).write_text('tamper')
            with self.assertRaises(ValueError):scope.read_allowed(packet,relative)
    def test_one_persona_per_worker_across_attempts(self):
        history={'claims':[dict(worker='w',design_id='d',persona='Card')]}
        self.assertFalse(scope.can_claim(history,'w',('d','Roth')))
        self.assertTrue(scope.can_claim(history,'w',('d','Card')))
        self.assertTrue(scope.can_claim(history,'v',('d','Roth')))
        self.assertTrue(scope.can_claim(history,'w',('other','Roth')))
    def test_q3_retains_include_and_exclude_views(self):
        from scripts.l2_pipeline.ops.forecast_authors import q3_views
        rows=[dict(design_id='d',version='v1'),dict(design_id='e',version='v1')]
        result=q3_views(rows,[dict(design_id='d',version='v1',shared_author_personas=True)])
        self.assertEqual(len(result['include_all']),2);self.assertEqual(len(result['exclude_shared_author']),1)

class SurveyTests(unittest.TestCase):
    def test_weighted_latest_release_suppression_and_no_ny_splice(self):
        from scripts.l2_pipeline.connectors.c63_yrbs import aggregate
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'micro.csv.gz'
            with gzip.open(path,'wt',newline='') as f:
                out=csv.DictWriter(f,fieldnames=['release','sitecode','year','weight','seatbelt_rarely_never']);out.writeheader()
                for release,site,n in [(2019,'NY',100),(2023,'NYA',100),(2021,'CT',99),(2019,'VT',100)]:
                    for i in range(n):out.writerow(dict(release=release,sitecode=site,year=2013,weight=3 if i%2 else 1,seatbelt_rarely_never=1 if i%2 else 2))
            cells,n=aggregate([path],['seatbelt_rarely_never'])
            self.assertEqual(n,399);self.assertEqual(cells['NY',2013,'seatbelt_rarely_never']['value'],75)
            self.assertEqual(cells['NY',2013,'seatbelt_rarely_never']['release'],2019)
            self.assertEqual(cells['NYA',2013,'seatbelt_rarely_never']['release'],2023)
            self.assertIsNone(cells['CT',2013,'seatbelt_rarely_never']['value'])
            self.assertTrue(cells['CT',2013,'seatbelt_rarely_never']['suppressed'])
            self.assertIsNone(cells['VT',2013,'seatbelt_rarely_never']['value'])
    def test_odd_grid_compresses_clock_without_annual_interpolation(self):
        from scripts.l2_pipeline.biennial import periods,adapter,normalize,ODD_ADAPTER
        out=dict(frequency='biennial',period_years=list(range(1991,2006,2)),years=[1991,2005])
        self.assertEqual(adapter(out),ODD_ADAPTER);self.assertEqual(len(periods(out)),8)
        tr=[dict(unit='u',status='treated',start_period=1995)]
        self.assertEqual(normalize(tr,{},periods(out),True)[0][0]['start_period'],3)
        self.assertLess(normalize([dict(unit='u',status='treated',start_period=1990)],{},periods(out),False)[0][0]['start_period'],0)
        out['period_years']=[1991,1993,1996]
        with self.assertRaises(ValueError):periods(out)
    def test_coverage_tie_chooses_longest_not_alphabet(self):
        from scripts.l2_pipeline.population_resolution import choose
        source={'series':{sid:dict(public_available=True,years=years) for sid,years in [('a_short',[2011,2012]),('z_long',list(range(1995,2013)))]}}
        meta=dict(series={sid:dict(construct='smoking',ages=[18,120],restrictions={},filters={}) for sid in source['series']},targets=[dict(aliases=['smoking'],construct='smoking',ages=[18,120],restrictions={},rule='fixture')])
        c=dict(policy_family='fixture');r=choose(c,dict(status='VERIFIED',target_outcome='smoking'),source,meta)
        self.assertEqual(r['selected']['series_id'],'z_long')

class IngestTests(unittest.TestCase):
    def test_old_archival_estimate_does_not_unlock_ingest(self):
        from scripts.l2_pipeline.ops.ingest_gate import check
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);registry=root/'data/l2_pipeline/registry'
            ensure_once(registry/'INGEST_GATE.json',dict(canary_designs=[]))
            ensure_once(registry/'TERMINAL_RUNS.json',dict(runs=[dict(study_estimated=True,package_path=str(root/'old'),status='COMPLETED')]))
            with self.assertRaisesRegex(ValueError,'INGEST_PAUSED'):check(SimpleNamespace(repo=root,registry=registry),[])
    def test_first_batch_is_canaries_then_requires_their_completion(self):
        from scripts.l2_pipeline.ops.ingest_gate import check
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);registry=root/'registry';ensure_once(registry/'INGEST_GATE.json',dict(canary_designs=[]));pilot=SimpleNamespace(repo=root,registry=registry)
            paths=[ensure_once(root/(str(i)+'.json'),dict(design_id=str(i))) for i in range(3)]
            with patch('scripts.l2_pipeline.ops.ingest_gate.estimated_runs',return_value=[dict(run_id='first',design_id='first')]):
                with self.assertRaisesRegex(ValueError,'1-2'):check(pilot,paths)
                state=check(pilot,paths[:2]);self.assertEqual(state['phase'],'CANARIES')
                (registry/'INGEST_GATE.json').write_text(json.dumps(state))
                with self.assertRaisesRegex(ValueError,'canaries'):check(pilot,paths[2:])

class FirstEstimateTests(unittest.TestCase):
    def test_statistical_stop_with_fits_unlocks_but_zero_fits_does_not(self):
        from scripts.l2_pipeline.ops.ingest_gate import estimated_runs
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d).resolve();registry=repo/'data/l2_pipeline/registry';study=registry.parent/'autopilot/work/invented/v1'
            terminal=ensure_once(study/'TERMINAL.json',dict(status='STOPPED'));result=ensure_once(study/'RESULT.json',dict(invented=True))
            row=dict(package_path=str(study),study_estimated=True,status='STOPPED',actual_data_estimation_calls=1,
                terminal_path=str(terminal),terminal_sha256=sha(terminal),result_path=str(result),result_sha256=sha(result))
            path=ensure_once(registry/'TERMINAL_RUNS.json',dict(runs=[row]));pilot=SimpleNamespace(registry=registry)
            self.assertEqual(estimated_runs(pilot),[row])
            row['actual_data_estimation_calls']=0;path.write_text(json.dumps(dict(runs=[row])))
            self.assertFalse(estimated_runs(pilot))
    def test_c75_any_autopilot_package_counts_if_hashes_match(self):
        from scripts.l2_pipeline.ops.ingest_gate import estimated_runs
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d).resolve();registry=repo/'data/l2_pipeline/registry';pilot=SimpleNamespace(registry=registry)
            def make(study):
                terminal=ensure_once(study/'TERMINAL.json',dict(status='COMPLETED'));result=ensure_once(study/'RESULT.json',dict(invented=True))
                return dict(package_path=str(study),study_estimated=True,status='COMPLETED',actual_data_estimation_calls=1,
                    terminal_path=str(terminal),terminal_sha256=sha(terminal),result_path=str(result),result_sha256=sha(result))
            def write(row):
                path=registry/'TERMINAL_RUNS.json'
                if not path.exists():ensure_once(path,dict(runs=[]))
                path.write_text(json.dumps(dict(runs=[row])))
            inside=make(repo/'data/l2_pipeline/autopilot/c72/work/b1010_invented/v1');write(inside)
            self.assertEqual(estimated_runs(pilot),[inside])
            outside=make(repo/'data/l2_pipeline/other/work/b1010_invented/v1');write(outside)
            self.assertFalse(estimated_runs(pilot))
            bad=make(repo/'data/l2_pipeline/autopilot/c72/work/b1010_bad/v1');bad['result_sha256']='0'*64;write(bad)
            self.assertFalse(estimated_runs(pilot))
