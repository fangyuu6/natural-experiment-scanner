"""V6.3 selection/transport regression tests: metadata and synthetic inputs only."""
from copy import deepcopy
from pathlib import Path
import tempfile, unittest
from unittest.mock import patch
from scripts.l2_pipeline.autopilot import Autopilot,Backend,read,atomic,scheduler_lock
from scripts.l2_pipeline.storage import sha,ensure_once
from scripts.l2_pipeline.population_resolution import choose,revised_card,apply
from scripts.l2_pipeline.connectors import population_v63,outcome_v8,outcome_v10,outcome_v11
from scripts.l2_pipeline.c47 import TARGETS,ROOT
from scripts.l2_pipeline.first_order import first_order_gate


def fixture(did):
    manifest=read(ROOT/'data/l2_pipeline/autopilot/c47/ACTIVE_DESIGNS_BEFORE.json')
    # For portable tests retain the original intake, independent of applications.
    if manifest:
        row=next(r for r in manifest['designs'] if r['design_id']==did)
    else:
        manifest=read(ROOT/'data/l2_pipeline/registry/ACTIVE_DESIGNS.json')
        row=next(r for r in manifest['designs'] if r['design_id']==did)
    pilot=Autopilot();card=read(row['card']);purpose=read(pilot.backend.root(row)/'PURPOSE_VERIFICATION.json')
    return row,card,purpose


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        # These are historical C47 choices; additive later catalogs may improve
        # eligible-age coverage and legitimately select a different series.
        self.metadata=patch.object(population_v63,'CURRENT_PATH',outcome_v11.CATALOG_PATH.with_name('OUTCOME_POPULATIONS_V11.json'))
        self.metadata.start();self.addCleanup(self.metadata.stop)

    def test_queue_choices_and_no_different_death_kind(self):
        expected={TARGETS[0]:'acs1_insurance_coverage_age_6_18',TARGETS[1]:'nchs_summary_fertility_rate',TARGETS[2]:'bls_laus_employment',TARGETS[3]:None}
        for did,sid in expected.items():
            _,card,purpose=fixture(did);result=choose(card,purpose)
            self.assertEqual(result['selected']['series_id'] if result['selected'] else None,sid)
            self.assertEqual(result['outcome_values_read'],0)
            if sid:
                new=revised_card(card,purpose,result,ROOT)
                self.assertTrue(first_order_gate(new)['passed'],first_order_gate(new))
                self.assertIn('dilution',new['first_order']['population_resolution']['expected_dilution'])
                self.assertEqual(new['outcome_family'],result['construct'])
                current=population_v63.catalog_module(population_v63.current_catalog()[0]['schema_version'])
                self.assertEqual(new['outcome']['catalog_sha256'],sha(current.CATALOG_PATH))
                has_artifact='support_artifact' in population_v63.current_catalog()[0]['series'][sid]
                self.assertEqual('support_path' in new['outcome'],has_artifact)
                self.assertEqual('paths' in new['outcome'],has_artifact)
            else:self.assertIn('prescription_opioid_overdose_deaths',result['reason'])

    def test_gfr_presence_wins_and_absence_fixes20_24_without_birth_counts(self):
        _,card,purpose=fixture(TARGETS[1]);source,meta=population_v63.current_catalog()
        del source['series']['nchs_summary_fertility_rate']
        result=choose(card,purpose,source,meta)
        self.assertEqual(result['selected']['series_id'],'wonder_fertility_rate_by_maternal_age')
        self.assertEqual(result['selected']['filters'],{'age_group':'20-24'})
        canonical=dict(purpose,target_outcome='births')
        self.assertEqual(choose(card,canonical,source,meta)['selected']['filters'],{'age_group':'20-24'})
        purpose['target_outcome']='unintended_pregnancy_incidence';card['policy_family']='other_contraception'
        with self.assertRaisesRegex(ValueError,'genuinely new construct'):choose(card,purpose,source,meta)

    def test_largest_overlap_then_narrowest_containing_population(self):
        source=dict(series={k:dict(public_available=True) for k in ['wide','narrow','partial']})
        meta=dict(targets=[dict(construct='test',aliases=['test'],ages=[10,20],rule='fixture')],series={
            'wide':dict(construct='test',ages=[0,100],filters={},restrictions={}),
            'narrow':dict(construct='test',ages=[8,22],filters={},restrictions={}),
            'partial':dict(construct='test',ages=[12,20],filters={},restrictions={})})
        card=dict(policy_family='fixture');purpose=dict(status='VERIFIED',target_outcome='test')
        self.assertEqual(choose(card,purpose,source,meta)['selected']['series_id'],'narrow')
        meta['series']['narrow']['restrictions']={'citizenship':'citizen_only'}
        self.assertEqual(choose(card,purpose,source,meta)['selected']['series_id'],'wide')
        del source['series']['wide'];self.assertEqual(choose(card,purpose,source,meta)['selected']['series_id'],'partial')

    def test_cap_and_downstream_are_parked_not_word_matched(self):
        source,meta=population_v63.catalog();meta['series'].pop('wonder_firearm_deaths')
        result=choose(dict(policy_family='cap'),dict(status='VERIFIED',target_outcome='child_firearm_mortality_all_intents'),source,meta)
        self.assertEqual(result['decision'],'park')
        result=choose(dict(policy_family='gestational'),dict(status='VERIFIED',target_outcome='late_abortions'),source,meta)
        self.assertEqual(result['decision'],'park')
        with self.assertRaisesRegex(ValueError,'unresolved construct'):choose({},dict(status='UNRESOLVED'))

    def test_population_tie_prefers_matching_narrower_demographic_scope(self):
        source=dict(series={sid:dict(public_available=True) for sid in ['a_broad','z_narrow']})
        meta=dict(targets=[dict(construct='test',aliases=['test'],ages=[18,65],restrictions={'sex':'female'},rule='fixture')],series={
            'a_broad':dict(construct='test',ages=[18,65],filters={},restrictions={}),
            'z_narrow':dict(construct='test',ages=[18,65],filters={'sex':'female'},restrictions={'sex':'female'})})
        result=choose(dict(policy_family='fixture'),dict(status='VERIFIED',target_outcome='test'),source,meta)
        self.assertEqual(result['selected']['series_id'],'z_narrow')

    def test_v8_bindings_untouched_and_adjunct_valid(self):
        from scripts.l2_pipeline.connectors import outcome_v7
        source,meta=population_v63.catalog();old=outcome_v7.catalog(verify_sources=False)
        for sid,spec in old['series'].items():self.assertEqual(dict(spec,registered_connector='outcome_v8_'+sid),source['series'][sid])
        self.assertTrue(all(s in source['series'] for s in meta['series']))


class MechanicalRequeueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.repo=Path(self.temp.name).resolve()
        _,card,purpose=fixture(TARGETS[0]);self.pilot=Autopilot(self.repo/'data/l2_pipeline/registry',Backend(self.repo))
        self.path=self.repo/'data/l2_pipeline/card.json';atomic(self.path,card)
        self.row=dict(design_id=card['design_id'],attempt_id='fixture',card=str(self.path),card_sha256=sha(self.path),work_dir=str(self.repo/'data/l2_pipeline/work'),route='V9')
        atomic(self.pilot.registry/'ACTIVE_DESIGNS.json',dict(designs=[self.row]));self.purpose=dict(purpose,card_sha256=sha(self.path))
        root=self.pilot.backend.root(self.row);ensure_once(root/'PURPOSE_VERIFICATION.json',self.purpose)
        for a in ['history','reconcile','audit']:ensure_once(self.pilot.checkpoint(self.row,a),dict(action=a))
        for n in ['RECONCILIATION.json','CODING_LOCK.json','AUDIT_SELECTION.json']:ensure_once(root/n,dict(fixture=n))
        # Catalog path serialization is relative to a repository; use a local
        # read-only mirror of the metadata in this synthetic repository.
        (self.repo/'scripts/l2_pipeline/connectors').mkdir(parents=True)
        for src,name in [(outcome_v11.CATALOG_PATH,'OUTCOME_CATALOG_V11.json'),(outcome_v11.CATALOG_PATH.with_name('OUTCOME_POPULATIONS_V11.json'),'OUTCOME_POPULATIONS_V10.json')]:
            (self.repo/'scripts/l2_pipeline/connectors'/name).write_bytes(src.read_bytes())
        self.catalog_patch=patch.object(outcome_v11,'CATALOG_PATH',self.repo/'scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V11.json');self.catalog_patch.start();self.addCleanup(self.catalog_patch.stop)
        self.meta_patch=patch.object(population_v63,'CURRENT_PATH',self.repo/'scripts/l2_pipeline/connectors/OUTCOME_POPULATIONS_V10.json');self.meta_patch.start();self.addCleanup(self.meta_patch.stop)

    def test_live_requeue_no_hub_entry_preserves_old_and_s0_next(self):
        before={str(f):sha(f) for f in self.pilot.backend.root(self.row).rglob('*') if f.is_file()}
        with scheduler_lock(self.pilot.registry):result=apply(self.pilot,self.row,scheduler_owned=True)
        new=result['receipt']['new_row'];self.assertEqual(self.pilot.next_action(new),'purpose-direction')
        self.assertEqual(read(self.pilot.registry/'HUB_QUEUE.json')['entries'],[])
        self.assertEqual(result['receipt']['retained_actions'],['history','reconcile','audit'])
        self.assertEqual(before,{p:sha(p) for p in before})
        # Changed selection bindings cannot reach S0.
        from scripts.l2_pipeline.population_resolution import precheck
        with self.assertRaisesRegex(ValueError,'metadata changed'):
            self.pilot.repo.joinpath('scripts/l2_pipeline/connectors/OUTCOME_POPULATIONS_V10.json').write_text('{}')
            precheck(self.pilot,new)

    def test_finished_or_frozen_live_card_rejected(self):
        ensure_once(self.pilot.backend.root(self.row)/'FREEZE_PREP.json',dict(fixture=True))
        with self.assertRaisesRegex(ValueError,'Frozen'):apply(self.pilot,self.row,scheduler_owned=True)

    def test_unfrozen_card_revision_retains_bound_purpose_without_new_agent(self):
        with scheduler_lock(self.pilot.registry):first=apply(self.pilot,self.row,scheduler_owned=True)
        with scheduler_lock(self.pilot.registry):second=apply(self.pilot,first['receipt']['new_row'],scheduler_owned=True)
        self.assertEqual(read(second['receipt']['new_row']['card'])['version'],'v3')
        self.assertEqual(second['receipt']['retained_actions'],['history','reconcile','audit'])
        self.assertEqual(second['receipt']['restart_action'],'purpose-direction')
        self.assertEqual(read(self.pilot.registry/'HUB_QUEUE.json')['entries'],[])

class TransportTests(unittest.TestCase):
    def test_superseded_queue_entry_does_not_hide_fresh_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();pilot=Autopilot(root/'data/l2_pipeline/registry',Backend(root))
            row=dict(design_id='fixture',attempt_id='original')
            atomic(pilot.registry/'HUB_QUEUE.json',dict(entries=[dict(row,status='SUPERSEDED_C47')]))
            pilot.stop(row,'audit','Fresh transport repair failed; no further replacement')
            entries=read(pilot.registry/'HUB_QUEUE.json')['entries']
            self.assertEqual([e['status'] for e in entries],['SUPERSEDED_C47','NEEDS_RULING'])
            pilot.stop(row,'audit','same failure')
            self.assertEqual(len(read(pilot.registry/'HUB_QUEUE.json')['entries']),2)

    def test_simultaneous_prompt_routing_matches_metadata_for_every_role(self):
        from scripts.l2_pipeline.agents.replacement import emit
        from scripts.l2_pipeline.agents.contract_tests.packets import emit_fixture_job
        from scripts.l2_pipeline.agents.jobs import load_job
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for n,kind in enumerate(['s0_purpose','s1_coder','s1_auditor','s1_followup_auditor','s1_official_history_v9','s5_forecast','s6_draft_reviewer']):
                original=emit_fixture_job(root/str(n),kind)
                new=emit(original.job_path,root/str(n)/'replacement','c47_test')
                prompt=Path(new.job_path).read_text().split('## Answer-free inputs (untrusted data)')[0]
                self.assertIn(new.output_path,prompt)
                self.assertNotIn('/attempts/c47_test/attempts/c47_test/',prompt)
                self.assertEqual(new,load_job(Path(new.job_path)))
                self.assertEqual(original.inputs,new.inputs)
                self.assertEqual(original.template_sha256,new.template_sha256)

    def test_repair_job_still_has_one_dispatch_and_no_second_failure_retry(self):
        from scripts.l2_pipeline.tests.test_c45 import C45Tests
        from scripts.l2_pipeline.agents.jobs import load_job
        from scripts.l2_pipeline.agents.replacement import emit
        from scripts.l2_pipeline.agents.contract_tests import golden_output
        helper=C45Tests();helper.setUp();self.addCleanup(helper.doCleanups)
        a,b=helper.pair();value=golden_output(a);value.pop('blindness_attestation');helper.put(a,value)
        helper.pilot.agent_action(helper.row,'history');failed=helper.pilot.effective_job(helper.row,a.job_path)
        slot=helper.pilot.replacement_slot(helper.row,a.job_path)
        job=emit(a.job_path,slot/'repair','c47_synthetic_repair')
        diagnostic=ensure_once(slot/'DIAGNOSIS.json',dict(pipeline_bug_confirmed=True,fixture=True))
        bind=lambda p:dict(path=str(p),sha256=sha(p))
        ensure_once(slot/'TRANSPORT_REPAIR.json',dict(original_job=a.job_path,failed_replacement_job=failed,diagnosis=bind(diagnostic),repaired_job=bind(job.job_path),repaired_sidecar=bind(Path(job.job_path).with_suffix('.job.json'))))
        pilot=helper.pilot
        self.assertIsNone(pilot.agent_action(helper.row,'history'));pilot.schedule()
        restarted=Autopilot(helper.registry,helper.backend)
        self.assertIsNone(restarted.agent_action(helper.row,'history'));restarted.schedule()
        self.assertEqual(helper.backend.launches,[job.job_path])
        helper.put(job,{});helper.backend.states[job.job_path]='done'
        with self.assertRaisesRegex(ValueError,'replacement failed'):restarted.agent_action(helper.row,'history')
        self.assertEqual(len(list(slot.rglob('TRANSPORT_REPAIR.json'))),1)
        self.assertEqual(pilot.effective_job(helper.row,failed),job.job_path)


class StructuredPurposeTests(unittest.TestCase):
    def test_existing_construct_with_new_population_does_not_need_hub(self):
        _,card,purpose=fixture(TARGETS[2]);purpose=dict(purpose,target_outcome='employment_of_newly_eligible_applicants',target_construct='employment_status',target_population_metadata=dict(ages=[18,65],restrictions={}))
        result=choose(card,purpose);self.assertEqual(result['decision'],'accept');self.assertEqual(result['selected']['series_id'],'bls_laus_employment_population_ratio')  # V17 (window 2026-10-17): the catalog's employment_status target prefers the ratio
        purpose['target_construct']='new_construct'
        with self.assertRaisesRegex(ValueError,'genuinely new construct'):choose(card,purpose)

    def test_new_purpose_metadata_contract_validation_and_legacy_output(self):
        from scripts.l2_pipeline.agents.contract_tests.packets import emit_fixture_job
        from scripts.l2_pipeline.agents.contract_tests import golden_output
        from scripts.l2_pipeline.agents.purpose import validate_purpose_output
        with tempfile.TemporaryDirectory() as d:
            job=emit_fixture_job(Path(d),'s0_purpose');value=golden_output(job)
            self.assertIn('citations',validate_purpose_output(job,value))
            value.update(target_construct='employment_status',target_population_metadata=dict(ages=[18,65],restrictions={}))
            self.assertEqual(validate_purpose_output(job,value)['target_population_metadata'],value['target_population_metadata'])
            value['target_population_metadata']['ages']=[65,18]
            with self.assertRaisesRegex(ValueError,'population metadata invalid'):validate_purpose_output(job,value)
