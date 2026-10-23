"""Synthetic regression checks; source outcomes and model dispatch stay disabled."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline.engine.calibration import blind_anchor
from scripts.l2_pipeline.engine.panels import projected_keys,_raw_records
from scripts.l2_pipeline.contract_card import registered_execution_card
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.agents import sonnet_queue as q
from scripts.l2_pipeline.c57 import science_equal,stale_flag
from scripts.l2_pipeline.tests.test_c56 import ContractTests
from scripts.l2_pipeline.tests.test_c55 import QueueTests

class AnchorTests(unittest.TestCase):
    def test_named_keys_and_poisoned_post_values_preserve_old_anchor(self):
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/'a.csv';b=Path(d)/'b.csv'
            a.write_text('unit,year,outcome_value\nu,1,1\nu,2,4\nv,1,5\nv,2,2\nu,3,POISON\n')
            b.write_text('outcome_value,unit,year\n1,u,1\n4,u,2\n5,v,1\n2,v,2\nPOISON,u,3\n')
            kwargs=dict(units=['u','v'],periods=[1,2],earliest_operative_period=3,value_column='outcome_value')
            x=blind_anchor(a,**kwargs);y=blind_anchor(b,**kwargs)
            self.assertEqual({k:v for k,v in x.items() if k!='panel_sha256'},{k:v for k,v in y.items() if k!='panel_sha256'})
            self.assertEqual(projected_keys(a),projected_keys(b))
            self.assertEqual(_raw_records(a,{('u',1)}),_raw_records(b,{('u',1)}))
            b.write_text('outcome_unit,outcome_value,unit,year\n"Deaths per 100,000",1,u,1\n"Deaths per 100,000",4,u,2\n"Deaths per 100,000",5,v,1\n"Deaths per 100,000",2,v,2\n"Deaths per 100,000",POISON,u,3\n')
            self.assertEqual(blind_anchor(b,**kwargs)['value'],x['value'])
            self.assertEqual(projected_keys(a),projected_keys(b))
            b.write_text('unit,unit,year,outcome_value\nu,u,1,0\n')
            with self.assertRaisesRegex(RuntimeError,'duplicated'):blind_anchor(b,**kwargs)

class ExecutionCardTests(ContractTests):
    def test_s6_and_dry_load_card_equal_spec_comparison_group(self):
        root=self.ctx.design_dir;spec=json.loads((root/'SPEC.json').read_text())
        ensure_once(root/'CARD.json',dict(self.ctx.card.payload))
        files={n:dict(role='card' if n=='CARD.json' else 'verification',sha256=sha(root/n)) for n in ['CARD.json','SPEC.json','SUPPORT.json']}
        manifest=dict(files=files)
        c=registered_execution_card(root,manifest)
        self.assertEqual(c['analysis']['controls'],spec['comparison_group']);self.assertEqual(c['comparison_group'],spec['comparison_group'])
        self.assertEqual(c['analysis'],spec['analysis'])
        (root/'SPEC.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'input changed'):registered_execution_card(root,manifest)

class RoleTests(QueueTests):
    def test_pending_history_moves_and_inprogress_remains_until_requeue(self):
        job=self.emit('S1','A');root=self.root/'queue';row=dict(design_id=job.design_id,attempt_id='invented')
        r=q.enqueue(job.job_path,row,root);packet=Path(r['packet']);before={p.name:p.read_bytes() for p in packet.iterdir() if p.is_file()}
        events=q.reroute_pending(root);self.assertEqual(events[0]['backend'],'codex');self.assertIsNone(q.locate(root,r['job_id']))
        archived=root/'rerouted_codex'/r['job_id'];self.assertEqual(before,{p.name:p.read_bytes() for p in archived.iterdir() if p.is_file()})
        # A separate history lease stays unchanged while owned, then is routed on expiry.
        job=self.emit('S1','B');r=q.enqueue(job.job_path,row,root);lease=q.claim(root,'worker',r['job_id'],now=1)
        self.assertFalse(q.reroute_pending(root));self.assertTrue((Path(lease['packet'])/'LEASE.json').exists())
        events=q.reap(root,now=1+q.LEASE_SECONDS)
        self.assertEqual(events[-1]['status'],'rerouted_codex');self.assertEqual(events[-1]['job_id'],r['job_id'])
        self.assertIsNone(q.claim(root,'new_sonnet_worker'));self.assertFalse(q.reroute_pending(root))

    def test_claude_only_keeps_history_packets_in_the_sonnet_queue(self):
        # Owner 2026-10-15: with L2_CLAUDE_ONLY=1 no pending or expired packet goes back to Codex, so the
        # second rename onto a non-empty rerouted_codex/<id> (OSError 66) can no longer end the loop.
        job=self.emit('S1','A');root=self.root/'queue';row=dict(design_id=job.design_id,attempt_id='invented')
        with patch.dict(os.environ,{'L2_CLAUDE_ONLY':'1'}):
            r=q.enqueue(job.job_path,row,root);packet=Path(r['packet'])
            before={p.name:p.read_bytes() for p in packet.iterdir() if p.is_file()}
            self.assertEqual(q.reroute_pending(root),[]);self.assertEqual(q.locate(root,r['job_id']),packet)
            self.assertEqual(before,{p.name:p.read_bytes() for p in packet.iterdir() if p.is_file()})
            # A history lease that expires returns to pending for another Sonnet worker, never to Codex.
            job2=self.emit('S1','B');r2=q.enqueue(job2.job_path,row,root);q.claim(root,'worker',r2['job_id'],now=1)
            events=q.reap(root,now=1+q.LEASE_SECONDS)
            self.assertEqual([(e['job_id'],e['status']) for e in events],[(r2['job_id'],'pending')])
            self.assertEqual(q.locate(root,r2['job_id']),root/'pending'/r2['job_id'])
            self.assertTrue(q.claim(root,'second_sonnet_worker',r2['job_id'],now=2+q.LEASE_SECONDS))
            self.assertEqual(q.reap(root,now=3+q.LEASE_SECONDS),[]);self.assertEqual(list((root/'rerouted_codex').iterdir()),[])
        # Control: the same state in default mode still returns the pending history packet to Codex.
        with patch.dict(os.environ):
            os.environ.pop('L2_CLAUDE_ONLY',None)
            self.assertEqual([e['status'] for e in q.reroute_pending(root)],['rerouted_codex'])

class MechanicalTests(unittest.TestCase):
    def test_mechanical_rebind_refuses_consumed_or_changed_science(self):
        from types import SimpleNamespace
        from scripts.l2_pipeline.c57 import validate_rebind
        from scripts.l2_pipeline.population_resolution import bind,rebase_sources
        from scripts.l2_pipeline.tests.test_pipeline import card
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);oldpath=ensure_once(root/'old.json',card());registry=root/'registry';registry.mkdir();(registry/'AUTOPILOT_STOP').touch()
            ensure_once(root/'SPEC.json',{'science':True});ensure_once(root/'SUPPORT.json',{'mask':True})
            auth=ensure_once(root/'authority.json',dict(schema='l2_c57_mechanical_authorization_v1',designs=['invented']))
            old=dict(card=str(oldpath),design_id='invented');p=SimpleNamespace(registry=registry,backend=SimpleNamespace(root=lambda r:root),stop_record=lambda r:True)
            authority=bind(auth);new=rebase_sources(json.loads(oldpath.read_text()),oldpath);new['version']='v2'
            new.setdefault('automation',{})['c57_implementation_revision']=dict(prior_card=bind(oldpath),prior_spec=bind(root/'SPEC.json'),authority=authority,science_unchanged=True,execution_authorized=False)
            path=ensure_once(root/'new.json',new);ruling=dict(authority=authority,execution_authorized=False,superseded_status='SUPERSEDED_C57',prior_spec=bind(root/'SPEC.json'),prior_support=bind(root/'SUPPORT.json'))
            validate_rebind(p,old,ruling,path)
            new['analysis']['controls']='not_yet_treated';path.write_text(json.dumps(new))
            with self.assertRaisesRegex(ValueError,'scientific card'):validate_rebind(p,old,ruling,path)
            (root/'EXECUTION_ONCE.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'consumed'):validate_rebind(p,old,ruling,path)
    def test_stale_flag_survives_comparison_without_fitting(self):
        from scripts.l2_pipeline.registered import forecast_comparison
        result=forecast_comparison({'invented':dict(point=1,interval=[0,2],interval_level=.8,
            stale_contract_card=True,stale_contract_differing_fields={'treated_n':dict(contract=12,registered=18)})},None,None)
        self.assertTrue(result['rows'][0]['stale_contract_card'])
        self.assertEqual(result['rows'][0]['stale_contract_differing_fields']['treated_n']['registered'],18)
    def test_science_equality_ignores_only_source_identity_and_version(self):
        a=dict(version='v1',card_sha256='a',analysis=dict(controls='never_treated'),support=dict(n=6));b=copy.deepcopy(a)
        b.update(version='v2',card_sha256='b');self.assertEqual(science_equal(a,b),[])
        b['support']['n']=7;self.assertEqual(science_equal(a,b),['support'])

    def test_stale_annotation_preserves_forecast_bytes_even_when_no_fields_differ(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);ensure_once(root/'prediction.json',dict(point=1));lock=ensure_once(root/'PREDICTIONS_LOCK.json',dict(files={'prediction.json':sha(root/'prediction.json')},original_files={}))
            auth=ensure_once(root/'authority.json',{'ruling':True});before=lock.read_bytes()
            audit=dict(design_id='x',version='v1',registered_treated_n=18,jobs=[dict(stage='S5',old_controls='never_treated',registered_controls='never_treated',old_screen_support=18)])
            path=stale_flag(root,audit,auth,root/'flag.json');v=json.loads(path.read_text())
            self.assertTrue(v['stale_contract_card']);self.assertEqual(v['differing_fields'],{});self.assertEqual(lock.read_bytes(),before)
