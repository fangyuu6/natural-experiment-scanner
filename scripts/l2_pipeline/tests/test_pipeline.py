from __future__ import annotations
import copy, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline.cards import validate_card, load_card, variant_card
from scripts.l2_pipeline.storage import local, write_once, sha
from scripts.l2_pipeline.freeze import REQUIRED_READINESS, ROLES, build_manifest, verify, environment, readiness_passed
from scripts.l2_pipeline.execution import execute_once, DeclaredStop
from scripts.l2_pipeline.stages.s1_treatment import reconcile, audit_sample, evaluate_v3
from scripts.l2_pipeline.stages.s4_specs import generate_spec
from scripts.l2_pipeline.stages.s6_execute import review_passed
from scripts.l2_pipeline.stages.s7_export import exports
from scripts.l2_pipeline.support import support_check
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.cli import main, run_stage

REPO=Path(__file__).resolve().parents[3]

def card():
    return {"schema_version":"1.0","version":"v1","design_id":"invented","policy_family":"invented_family","mechanism":"one intervention","track":"DiD","unit":"state","outcome_family":"employment",
            "sources":[{"source_id":"source1","url":"https://example.invalid/fixture","locator":"table1","license":"invented public"}],
            "lineage":{"queue_version":"v2","screen_version":"fixture","source_text_ids":["text1"]},
            "population":{"description":"invented all adults","filters":{}},
            "outcome":{"connector":"standardized_csv","population_filters":{},"measure_id":"employment_share",
                       "source_measure_id":"employment_share","measurement_refs":[{"source_ref":"invented panel", "locator":"employment column"}]},
            "first_order":{"target_outcome":"employment_share","primary_outcome":"employment_share","relation":"direct",
                           "availability":"available","statute_refs":[{"source_ref":"source1","locator":"invented employment purpose"}],
                           "literature_refs":[{"source_ref":"invented literature","locator":"employment definition"}],"secondary_outcomes":[]},
            "treatment":{},"analysis":{},"sensitivities":[]}

def tuple_row(u, status="treated", start=2002, mechanism="one"):
    return {"unit":u,"status":status,"start_period":start,"mechanism":mechanism,"locator":"table1"}

class CardTests(unittest.TestCase):
    def test_schema_and_nested_typos(self):
        self.assertEqual(validate_card(card()),[])
        for modify in (lambda c:c.update(track="rd"),lambda c:c.update(design_id="../escape"),lambda c:c["sources"][0].update(sha256="bad"),lambda c:c["population"].update(filterz={}),lambda c:c.update(unexpected=True)):
            c=card();modify(c);self.assertTrue(validate_card(c))
    def test_population_alignment(self):
        c=card();c["outcome"]["population_filters"]={"sex":"male"};self.assertTrue(validate_card(c))
    def test_shared_variants(self):
        c=card();v=variant_card(c,{"outcome":{"years":[2000,2020]}});self.assertNotIn("years",c["outcome"]);self.assertEqual(v["outcome"]["years"],[2000,2020])
        with self.assertRaises(ValueError):variant_card(c,{"design_id":"rescue"})
    def test_duplicate_sensitivities(self):
        c=card();c["sensitivities"]=[{"name":"shift"},{"name":"shift"}];self.assertTrue(validate_card(c))
    def test_paths_and_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for rel in ("../x","/tmp/x","a/../../x"):
                with self.assertRaises(ValueError):local(root,rel)
            write_once(root/"a.json",{"a":1})
            with self.assertRaises(FileExistsError):write_once(root/"a.json",{"a":2})

class TreatmentTests(unittest.TestCase):
    def test_denominators_and_scope(self):
        a=[tuple_row("a"),tuple_row("b"),tuple_row("c","unknown",None)]
        b=[tuple_row("a",mechanism="other"),tuple_row("b","unknown",None),tuple_row("c","unknown",None)]
        r=reconcile(a,b,["a","b","c"]);self.assertEqual(r["paired_known"],1);self.assertEqual(r["agreement"],0);self.assertEqual(r["counts"],{"known_conflict":1,"one_sided_missing":1,"both_unknown":1})
    def test_unknown_not_zero(self):
        r=reconcile([],[],["a"]);self.assertIsNone(r["agreement"]);self.assertEqual(r["accepted"],[])
    def test_seed_treated_only(self):
        rows=[tuple_row(str(i)) for i in range(20)]+[tuple_row("never","never_treated",None)]
        sample=audit_sample(rows,44);self.assertEqual(sample,audit_sample(rows,44));self.assertEqual(len(sample),10);self.assertNotIn("never",sample)
    def test_v3_bars(self):
        sample=[str(i) for i in range(10)]
        checks=[{"unit":u,"status":"known_error" if i<2 else "unresolved" if i<7 else "match","official_locator":"fixture","corrected_start_period":2001} for i,u in enumerate(sample)]
        self.assertTrue(evaluate_v3(sample,checks)["passed"])
        checks[7]["status"]="unresolved";self.assertFalse(evaluate_v3(sample,checks)["passed"])
        self.assertFalse(evaluate_v3(sample[:3],checks[:3])["passed"])
    def test_duplicate_and_missing_clock_refuse(self):
        with self.assertRaises(ValueError):reconcile([tuple_row("a"),tuple_row("a")],[],["a"])
        with self.assertRaises(ValueError):reconcile([tuple_row("a",start=None)],[],["a"])

class SupportTests(unittest.TestCase):
    def test_metadata_supported_and_stopped(self):
        keys={(u,t) for u in ("a","b","c") for t in range(2000,2005)}
        treatment=[tuple_row("a"),tuple_row("b","never_treated",None),tuple_row("c","never_treated",None)]
        cfg={"min_pre_periods":2,"min_controls":2,"min_cohorts":1}
        good=support_check(keys,treatment,cfg);self.assertTrue(good["passed"]);self.assertEqual(good["outcome_values_parsed"],0)
        keys.remove(("b",2003));self.assertEqual(support_check(keys,treatment,cfg)["disposition"],"stopped")
    def test_track_stub_and_no_support_rescue(self):
        c=card();c["track"]="RCT";self.assertEqual(generate_spec(c,{})["status"],"stub")
        c["track"]="DiD"
        with self.assertRaises(ValueError):generate_spec(c,{"primary_passed":False})

class FreezeTests(unittest.TestCase):
    def test_exact_readiness(self):
        good={k:True for k in REQUIRED_READINESS};self.assertTrue(readiness_passed(good))
        for invalid in ({},{"x":True},dict(good,x=True),{k:1 for k in good},{k:"PASS" for k in good},None):self.assertFalse(readiness_passed(invalid))
    def test_optimized_readiness(self):
        code="""from scripts.l2_pipeline.freeze import readiness_passed; from scripts.l2_pipeline.execution import execute_once; import pathlib;
if readiness_passed({'x':'PASS'}): raise RuntimeError('guard bypass')
try: execute_once(pathlib.Path('/tmp/does-not-exist-l2-fixture'), 'hub', lambda: (_ for _ in ()).throw(RuntimeError('outcome opened')), lambda x: {})
except RuntimeError as exc:
 if 'outcome opened' in str(exc): raise
else: raise RuntimeError('unsigned accepted')"""
        p=subprocess.run([sys.executable,"-B","-O","-c",code],cwd=REPO,capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr)
    def test_build_and_unsigned_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); files={}
            for role in ROLES:
                name=role+".json";write_once(root/name,card() if role=="card" else {"fixture":role});files[name]=role
            write_once(root/"predictions/x/submission.json",{"persona":"x","design_id":"invented","spec_sha256":sha(root/"spec.json")});files["predictions/x/submission.json"]="predictions"
            write_once(root/"PREDICTIONS_LOCK.json",{"personas":["x"],"design_id":"invented","spec_sha256":sha(root/"spec.json"),"files":{"predictions/x/submission.json":sha(root/"predictions/x/submission.json")}});files["PREDICTIONS_LOCK.json"]="predictions"
            build_manifest(root,"prep",files,["x"],"hub")
            self.assertTrue(verify(root,"prep","hub",require_approval=False)["ok"])
            self.assertFalse(verify(root,"prep","hub")["ok"])
            write_once(root/"APPROVAL_PREP.json",{"status":"APPROVED","signed_by":"hub","signed_at_utc":"2026-10-07T00:00:00Z","approved_actions":["pre_adoption_anchor","synthetic_calibration"],"execution_authorized":False,"manifest_path":"FREEZE_PREP.json","manifest_sha256":sha(root/"FREEZE_PREP.json")})
            self.assertTrue(verify(root,"prep","hub")["ok"])
            (root/"spec.json").write_text('{"changed":true}')
            self.assertFalse(verify(root,"prep","hub")["ok"])
    def test_review_must_definition_and_binding(self):
        from scripts.l2_pipeline.tests.test_s6_review_fixes import sealed_review
        good=sealed_review("abc")
        self.assertTrue(review_passed(good,"abc","invented"))
        for patch_value in ({"must":["wrong number"]},{"complete_sweeps":True},{"fix_cycles":3},{"candidate_sha256":"different"},
                            {"review_scope":"draft_readiness"},{"design_id":"other"},
                            {"independence_attestation":{"wrote_implementation":True,"wrote_fixes":False,"real_outcome_associations_read":False}}):
            self.assertFalse(review_passed(dict(good,**patch_value),"abc","invented"))

class ExecutionTests(unittest.TestCase):
    def test_approval_before_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); opened=[]
            with self.assertRaises(RuntimeError):execute_once(root,"hub",lambda:opened.append(1),lambda x:{})
            self.assertEqual(opened,[]);self.assertFalse((root/"EXECUTION_ONCE.json").exists())
    def test_failure_consumes_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch("scripts.l2_pipeline.execution.require_verified",return_value={"manifest_sha256":"abc"}):
                with self.assertRaisesRegex(RuntimeError,"verbatim"):execute_once(root,"hub",lambda:0,lambda x:(_ for _ in ()).throw(RuntimeError("verbatim")))
                failure=json.loads((root/"FAILURE.json").read_text());self.assertEqual(failure["message"],"verbatim");self.assertIn("RuntimeError",failure["traceback"])
                with self.assertRaises(FileExistsError):execute_once(root,"hub",lambda:1,lambda x:{})
    def test_declared_stop_and_success_write_once(self):
        for stop in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                def fit(x):
                    if stop:raise DeclaredStop("registered support")
                    return {"point":3.0}
                with patch("scripts.l2_pipeline.execution.require_verified",return_value={"manifest_sha256":"abc"}):
                    result=execute_once(root,"hub",lambda:0,fit)
                    self.assertEqual(result,{"status":"STOPPED","reason":"registered support"} if stop else {"point":3.0})
                    with self.assertRaises(FileExistsError):execute_once(root,"hub",lambda:0,fit)
    def test_export_retains_failures(self):
        value=exports(card(),{"status":"FAILED_NO_RETRY","message":"failed"},None,{"sha":"abc"});self.assertEqual(value["l3_meta"]["status"],"FAILED_NO_RETRY");self.assertIsNone(value["estimates_page"]["result"])

class CliTests(unittest.TestCase):
    def test_cli_prior_gate_and_intake(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/"card.json";write_once(p,card());ctx=StageContext(load_card(p),root/"work",REPO)
            result=run_stage(ctx,"S2");self.assertEqual(result.disposition.value,"failed_no_retry");self.assertIn("S1",result.reason)
            self.assertEqual(main(["run","--stage","S0","--design",str(p),"--work-dir",str(root/"other")]),0)
    def test_batch_reports_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cards=root/"cards";cards.mkdir();write_once(cards/"good.json",card());write_once(cards/"bad.json",{})
            self.assertEqual(main(["run","--stage","S0","--batch",str(cards),"--work-dir",str(root/"work")]),1)

class IntegrationTests(unittest.TestCase):
    def test_s0_to_s4_and_audit_resume_no_associations(self):
        import csv
        from scripts.l2_pipeline.stages.s3_support import variant_treatment
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=card();units=['AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA'];years=list(range(2000,2008))
            write_once(root/'source.json',{'complete_list':'invented fixture'})
            c['sources'][0].update(path='source.json',sha256=sha(root/'source.json'),complete_list=True,coverage_end='2007-12-31')
            c['treatment']={'universe':units,'seed':42,'complete_list_source_id':'source1','predicate':'invented','coverage_end':'2007-12-31','historical_convention':'cumulative ever treated'}
            c['mechanism']='one';c['analysis']={'min_pre_periods':2,'min_controls':5,'min_cohorts':2,'min_treated':10,'controls':'never_treated','estimand':'supported ATT','clock':'calendar_year','weights':'cohort-size post cells','inference':'CT-first registered fallback','diagnostics':['pre','placebo'],'stop_rules':['registered_support']}
            c['outcome'].update(paths=['outcomes.csv'],source_population_filters={},years=[2000,2007],units=units,frequency='annual',outcome_unit='fixture units')
            c['sensitivities']=[{'name':'shift','changes':{'treatment':{'start_shift':1}}}]
            with (root/'outcomes.csv').open('w',newline='') as handle:
                writer=csv.writer(handle);writer.writerow(['unit','year','outcome_value'])
                writer.writerows((u,y,-3+(i+y)%7) for i,u in enumerate(units) for y in years)
            write_once(root/'card.json',c);ctx=StageContext(load_card(root/'card.json'),root/'work',REPO)
            self.assertEqual(run_stage(ctx,'S0').disposition.value,'ready')
            self.assertEqual(run_stage(ctx,'S1').disposition.value,'parked')
            rows=[tuple_row(u,start=2002 if i<5 else 2004) if i<10 else tuple_row(u,'never_treated',None) for i,u in enumerate(units)]
            for role in ('A','B'):write_once(ctx.design_dir/'agents'/role/'submission.json',{'records':rows})
            self.assertEqual(run_stage(ctx,'S1').disposition.value,'parked')
            sample=json.loads((ctx.design_dir/'AUDIT_SELECTION.json').read_text())['sample']
            write_once(ctx.design_dir/'OFFICIAL_AUDIT.json',{'checks':[{'unit':u,'status':'match','official_locator':'invented official fixture'} for u in sample]})
            self.assertEqual(run_stage(ctx,'S1').disposition.value,'ready')
            for stage in ('S2','S3','S4'):
                result=run_stage(ctx,stage);self.assertEqual(result.disposition.value,'ready',result.reason)
            support=json.loads((ctx.design_dir/'SUPPORT.json').read_text())
            self.assertEqual(support['outcome_values_parsed'],0)
            primary,shift=support['variants'];self.assertEqual(primary['cells'][0]['cohort']+1,shift['cells'][0]['cohort'])
            # C79: the prospective added rows use bound primary bytes and survive
            # the real registration loader without changing the primary support.
            from scripts.l2_pipeline.dry_load import draft
            spec=json.loads((ctx.design_dir/'SPEC.json').read_text())
            self.assertEqual(spec['support']['variants'][0],primary)
            self.assertLessEqual(len(spec['c79_hygiene']['added']),3)
            loaded=draft(ctx)
            self.assertTrue(loaded['passed'])
            self.assertEqual(loaded['actual_data_estimation_calls'],0)
            # Changed metadata refuses before another support computation.
            p=ctx.design_dir/'panels'/'primary'/'metadata.csv';p.write_text(p.read_text().replace('AL,2000','AL,1999'))
            self.assertEqual(run_stage(ctx,'S3').disposition.value,'failed_no_retry')
    def test_future_cohort_cannot_supply_second_supported_cohort(self):
        keys={(u,t) for u in ['a','b','c','d'] for t in range(2000,2005)}
        rows=[tuple_row('a',start=2002),tuple_row('b',start=2010),tuple_row('c','never_treated',None),tuple_row('d','never_treated',None)]
        result=support_check(keys,rows,{'min_pre_periods':2,'min_controls':2,'min_cohorts':2})
        self.assertFalse(result['passed']);self.assertEqual(result['cohorts'],1)
    def test_prediction_lock_missing_cannot_be_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'lock must be bound'):build_manifest(Path(tmp),'prep',{},['x'],'hub')

class FinalApprovalTests(unittest.TestCase):
    def make_bundle(self,root):
        from scripts.l2_pipeline.predictions import lock_predictions
        from scripts.l2_pipeline.freeze import ACTIONS
        from scripts.l2_pipeline.tests.test_s6_review_fixes import calibration_record,sealed_review
        from scripts.l2_pipeline.tests.test_interval_review_fixes import fixture,evaluation
        from scripts.l2_pipeline.engine.calibration import certify_intervals
        design,op,_=fixture()
        roles={role+'.json':role for role in ROLES}
        plan=dict(schema='l2_interval_plan_v1',expected_designs=['invented'],candidate_order=['webb_wcu_t','control_path_ct'],certification_path='INTERVAL_CERTIFICATION.json')
        for file,role in roles.items():write_once(root/file,card() if role=='card' else plan if role=='interval_plan' else {'fixture':role})
        spec_sha=sha(root/'spec.json')
        write_once(root/'predictions/x/submission.json',{'persona':'x','design_id':'invented','spec_sha256':spec_sha,'point':-1.0,'interval':[-3,1],'interval_level':.95,'rationale':'invented forecast','disclosures':[]})
        lock_predictions(root,['x'],'invented',spec_sha)
        roles.update({'PREDICTIONS_LOCK.json':'predictions','predictions/x/submission.json':'predictions'})
        build_manifest(root,'prep',roles,['x'],'fixture_hub')
        def approval(stage,**extra):
            return dict(status='APPROVED',signed_by='fixture_hub',signed_at_utc='2026-10-07T00:00:00Z',approved_actions=ACTIONS[stage],execution_authorized=stage=='final',manifest_path='FREEZE_'+stage.upper()+'.json',manifest_sha256=sha(root/('FREEZE_'+stage.upper()+'.json')),**extra)
        write_once(root/'APPROVAL_PREP.json',approval('prep'))
        for name,payload in [('CALIBRATION_GATES.json',{'passed':True,'cases':{'invented':calibration_record(design)}}),('FINAL_READINESS.json',{k:True for k in REQUIRED_READINESS}),('REVIEW.json',{'purpose':'invented numerical evidence'})]:write_once(root/name,payload)
        write_once(root/'INTERVAL_CERTIFICATION.json',certify_intervals({'invented':evaluation(design,op)},expected_designs=['invented']))
        roles['INTERVAL_CERTIFICATION.json']='interval_certification'
        roles.update({n:'verification' for n in ('FREEZE_PREP.json','APPROVAL_PREP.json','CALIBRATION_GATES.json','FINAL_READINESS.json','REVIEW.json')})
        build_manifest(root,'final',roles,['x'],'fixture_hub')
        review=sealed_review(sha(root/'FREEZE_FINAL.json'))
        write_once(root/'EXECUTION_REVIEW.json',review)
        # This is a guard-only invented manifest; it has no registered panel.
        # Full loader regressions use the proper input fixture in test_dry_load.
        manifest=json.loads((root/'FREEZE_FINAL.json').read_text())
        write_once(root/'DRY_LOAD.json',dict(schema='l2_dry_load_v1',passed=True,fitting_disabled=True,
            design_id='invented',candidate_sha256=sha(root/'FREEZE_FINAL.json'),
            code_snapshot_digest=manifest['code_snapshot']['digest'],loaded=[],actual_data_estimation_calls=0,
            execution_slot_consumed=False,outcome_values_printed=False,outcome_associations_computed=False))
        write_once(root/'APPROVAL_FINAL.json',approval('final',review_sha256=sha(root/'EXECUTION_REVIEW.json'),
            dry_load_sha256=sha(root/'DRY_LOAD.json')))
    def test_exact_final_approval_then_sweep_tamper_refuses_before_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.make_bundle(root);self.assertTrue(verify(root,'final','fixture_hub')['ok'])
            review=json.loads((root/'EXECUTION_REVIEW.json').read_text());review['must']=['would produce an incorrect number'];(root/'EXECUTION_REVIEW.json').write_text(json.dumps(review))
            opened=[]
            with self.assertRaises(RuntimeError):execute_once(root,'fixture_hub',lambda:opened.append(True),lambda data:{})
            self.assertEqual(opened,[]);self.assertFalse((root/'EXECUTION_ONCE.json').exists())
    def test_empty_lock_and_truthy_readiness_cannot_pass_rebound_unsigned_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.make_bundle(root)
            lock=json.loads((root/'PREDICTIONS_LOCK.json').read_text());lock['files']={};(root/'PREDICTIONS_LOCK.json').write_text(json.dumps(lock))
            m=json.loads((root/'FREEZE_FINAL.json').read_text());m['files']['PREDICTIONS_LOCK.json']['sha256']=sha(root/'PREDICTIONS_LOCK.json');(root/'FREEZE_FINAL.json').write_text(json.dumps(m))
            self.assertFalse(verify(root,'final','fixture_hub',require_approval=False)['ok'])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.make_bundle(root)
            (root/'FINAL_READINESS.json').write_text(json.dumps({k:'PASS' for k in REQUIRED_READINESS}))
            m=json.loads((root/'FREEZE_FINAL.json').read_text());m['files']['FINAL_READINESS.json']['sha256']=sha(root/'FINAL_READINESS.json');(root/'FREEZE_FINAL.json').write_text(json.dumps(m))
            self.assertFalse(verify(root,'final','fixture_hub',require_approval=False)['ok'])
    def test_registry_snapshot_hook_validates_both_sides(self):
        from scripts.l2_pipeline.registry import update_snapshot
        previous={'registry_version':'1.0.0','generated_at':'2026-10-07T00:00:00Z','source_texts':[],'policy_families':[],'legal_instruments':[],'designs':[],'outcome_specs':[],'runs':[]}
        current,site=update_snapshot(previous,outcome_specs=[],runs=[],design_updates={},generated_at='2026-10-07T01:00:00Z')
        self.assertEqual(site['counts']['designs'],0);self.assertNotEqual(previous['generated_at'],current['generated_at'])
        with self.assertRaises(ValueError):update_snapshot(dict(previous,registry_version='bad'),outcome_specs=[],runs=[],design_updates={},generated_at='2026-10-07T01:00:00Z')
