"""D8 synthetic regressions: exact census, scoped acceptance and existing resume."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import random
import unittest

from scripts.l2_pipeline.autopilot import atomic, read
from scripts.l2_pipeline.storage import sha, ensure_once
from scripts.l2_pipeline.intake.history_routing import select_fresh_sample
from scripts.l2_pipeline.stages.s1_treatment import evaluate_v3
from scripts.l2_pipeline.tests.test_autopilot import OfficialBridgeTests
from scripts.l2_pipeline.tests.test_c46 import RequeueTests
from scripts.l2_pipeline import c85


class CensusTests(unittest.TestCase):
    def test_all_nonempty_small_remainders_and_prior_exclusions(self):
        for n in range(1,10):
            records=[dict(unit=f'U{i:02}',status='treated') for i in range(n+3)]
            records.append(dict(unit='CONTROL',status='never_treated'))
            policy=dict(seed=55,excluded_units=['U00','U01','U02'],sample_size=10)
            expected=[f'U{i:02}' for i in range(3,n+3)]
            self.assertEqual(select_fresh_sample(records,policy),expected)
            self.assertEqual(select_fresh_sample(records[::-1],policy),expected)

    def test_zero_retains_reason_and_duplicate_records_fail(self):
        rows=[dict(unit='A',status='treated')]
        with self.assertRaisesRegex(ValueError,c85.PARK_REASON):
            select_fresh_sample(rows,dict(seed=1,excluded_units=['A']))
        with self.assertRaisesRegex(ValueError,'unique'):
            select_fresh_sample(rows*2,dict(seed=1,excluded_units=[]))

    def test_ten_and_larger_draw_unchanged(self):
        for n in (10,11,40):
            rows=[dict(unit=f'U{i:02}',status='treated') for i in range(n)]
            self.assertEqual(select_fresh_sample(rows,dict(seed=55,excluded_units=[])),
                random.Random(55).sample(sorted(r['unit'] for r in rows),10))

    def test_unscoped_short_audit_and_empty_census_still_fail(self):
        self.assertFalse(evaluate_v3(['A'],[])['passed'])
        self.assertFalse(evaluate_v3([],[],remainder_census=True)['passed'])

    def test_error_unresolved_and_correction_thresholds_unchanged(self):
        sample=[str(i) for i in range(9)]
        checks=[dict(unit=u,status='match',official_locator='synthetic') for u in sample]
        self.assertTrue(evaluate_v3(sample,checks,remainder_census=True)['passed'])
        for n,status in ((3,'known_error'),(6,'unresolved')):
            changed=deepcopy(checks)
            for item in changed[:n]:item.update(status=status,corrected_start_period=2003)
            self.assertFalse(evaluate_v3(sample,changed,remainder_census=True)['passed'])
        checks[0]['status']='known_error'
        self.assertFalse(evaluate_v3(sample,checks,remainder_census=True)['passed'])

    def test_prescreen_nonempty_census_and_zero_remainder(self):
        import tempfile
        from unittest.mock import patch
        from scripts.l2_pipeline.tests.test_gate0 import fixture
        from scripts.l2_pipeline.prescreen import evaluate
        with tempfile.TemporaryDirectory() as tmp, patch('scripts.l2_pipeline.population_resolution.choose',side_effect=ValueError('new construct')):
            root=Path(tmp);card=fixture(root)
            units=list(card['treatment']['screening_adoption']['adopters'])
            for n in (1,9):
                result=evaluate(card,root,excluded_units=units[:10-n])
                self.assertEqual(result['status'],'PROCEED')
                self.assertTrue(all(v['fresh_pass'] and v['fresh_pool_n']==n for v in result['variants']))
            result=evaluate(card,root,excluded_units=units)
            self.assertEqual(result['status'],'PRESCREEN_PARK')
            self.assertTrue(all(not v['fresh_pass'] for v in result['variants']))

    def test_missing_extra_and_duplicate_checks_rejected(self):
        checks=[dict(unit=u,status='match',official_locator='synthetic') for u in ('A','B')]
        for changed in (checks[:1],checks+checks[:1],checks+[dict(checks[0],unit='C')]):
            with self.assertRaisesRegex(ValueError,'prelocked'):
                evaluate_v3(['A','B'],changed,remainder_census=True)


class CensusBridgeTests(OfficialBridgeTests):
    def census(self,n=9):
        self.policy['excluded_units']=['U'+str(i) for i in range(12-n)]
        return self.prepare()

    def test_census_collector_and_accept_preserve_originals(self):
        from scripts.l2_pipeline.autopilot_history import accept
        original={j.output_path:sha(j.output_path) for j in self.jobs}
        job=self.census();self.audit(job,errors=1)
        selection=read(job.contract['selection_path'])
        self.assertEqual(selection['remainder_census']['owner_approval_date'],'2026-10-12')
        self.assertIn('v3_c85',job.template_path)
        self.assertNotIn('ten sampled',Path(job.job_path).read_text())
        self.assertEqual(read(job.promote_path)['audit']['sample_size'],9)
        self.assertEqual(accept(self.ctx,job.job_path).disposition,'ready')
        self.assertEqual(read(self.ctx.design_dir/'TREATMENT.json')['audit']['sample_size'],9)
        self.assertEqual(original,{j.output_path:sha(j.output_path) for j in self.jobs})

    def test_ten_state_packet_reentry_preserves_bytes(self):
        job=self.prepare()
        before={str(p):sha(p) for p in (Path(job.job_path),Path(job.job_path).with_suffix('.job.json'),self.ctx.design_dir/'AUDIT_SELECTION.json',self.ctx.design_dir/'RECONCILIATION.json')}
        self.assertIn('Only ten sampled tuples; full A/B originals forbidden',Path(job.job_path).read_text())
        self.assertNotIn('remainder_census',read(job.contract['selection_path']))
        self.assertEqual(self.prepare(),job)
        self.assertEqual(before,{p:sha(p) for p in before})

    def test_one_remaining_state_can_be_audited(self):
        from scripts.l2_pipeline.autopilot_history import accept
        job=self.census(1);self.audit(job)
        self.assertEqual(accept(self.ctx,job.job_path).disposition,'ready')

    def test_census_known_error_gate_still_parks(self):
        from scripts.l2_pipeline.autopilot_history import accept
        job=self.census();self.audit(job,errors=3)
        with self.assertRaisesRegex(ValueError,'V3 failed'):accept(self.ctx,job.job_path)

    def test_census_cannot_omit_or_restore_a_state(self):
        from scripts.l2_pipeline.autopilot_history import validate_job
        job=self.census()
        for sample in (job.contract['sample'][:-1],job.contract['sample']+['U0']):
            with self.assertRaisesRegex(ValueError,'identity/seed/tuples'):
                validate_job(replace(job,contract=dict(job.contract,sample=sample)))

    def test_census_authority_and_actual_size_are_bound(self):
        job=self.census();selection=read(job.contract['selection_path'])
        for key,value in (('owner_approval_date','2020-01-01'),('sample_size',10)):
            changed=deepcopy(selection);changed['remainder_census'][key]=value
            with self.assertRaisesRegex(ValueError,'authority/roster'):
                c85.validate_census(job,changed,job.contract['sample'])
        with self.assertRaisesRegex(ValueError,'authority/roster'):
            c85.validate_census(job,{},job.contract['sample'])

    def test_census_v7_retained_minimum_not_relaxed(self):
        from dataclasses import replace
        from scripts.l2_pipeline.c42 import Park
        card=deepcopy(dict(self.ctx.card.payload));card['analysis']['min_treated']=13
        self.ctx=replace(self.ctx,card=replace(self.ctx.card,payload=card))
        with self.assertRaisesRegex(Park,'V7 retained treated'):self.census()


class ResumeTests(RequeueTests):
    def setUp(self):
        super().setUp()
        case=self.pilot.case_dir(self.row)
        (case/'STOP.json').unlink()
        for action in ('reconcile','audit'):self.pilot.checkpoint(self.row,action).unlink()
        self.row['fresh_policy']=dict(seed=55,excluded_units=['U0'],sample_size=10)
        atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=[self.row]))
        atomic(case/'PARKED.json',dict(reason=c85.PARK_REASON,action='reconcile'))
        atomic(self.pilot.backend.root(self.row)/'RECONCILIATION.json',dict(
            audit_eligible_records=[dict(unit=f'U{i}',status='treated') for i in range(7)]))
        atomic(self.registry/'CODE_TASK.lock',dict(task='C85'))
        authority=self.repo/c85.RULE;authority.parent.mkdir(parents=True,exist_ok=True)
        authority.write_text('synthetic owner ruling 2026-10-12')

    def test_existing_resume_retains_history_and_old_bytes_is_idempotent(self):
        root=self.pilot.backend.root(self.row);case=self.pilot.case_dir(self.row)
        original={str(p):sha(p) for p in [Path(self.row['card']),case/'PARKED.json',root/'RECONCILIATION.json',root/'CODING_LOCK.json']}
        result=c85.apply(self.pilot);self.assertEqual(len(result['records']),1)
        receipt=result['records'][0]
        self.assertEqual(receipt['retained_actions'],['history'])
        self.assertEqual(receipt['restart_action'],'reconcile')
        self.assertEqual(self.pilot.next_action(receipt['new_row']),'reconcile')
        self.assertEqual(original,{p:sha(p) for p in original})
        self.assertEqual(result,c85.apply(self.pilot))
        # Simulate a crash after the committed requeue but before application receipt.
        (self.registry.parent/'autopilot/c85/APPLICATION.json').unlink()
        self.assertEqual(result,c85.apply(self.pilot))

    def test_all_matching_designs_are_resumed(self):
        second=deepcopy(self.row);second['design_id']='another-d8-fixture'
        card=read(self.row['card']);card['design_id']=second['design_id']
        path=Path(self.row['card']).with_name('second.json');ensure_once(path,card)
        second.update(card=str(path),card_sha256=sha(path))
        atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=[self.row,second]))
        ensure_once(self.pilot.case_dir(second)/'PARKED.json',dict(reason=c85.PARK_REASON,action='reconcile'))
        ensure_once(self.pilot.checkpoint(second,'history'),dict(action='history'))
        ensure_once(self.pilot.backend.root(second)/'RECONCILIATION.json',read(self.pilot.backend.root(self.row)/'RECONCILIATION.json'))
        result=c85.apply(self.pilot)
        self.assertEqual({r['design_id'] for r in result['records']},{self.row['design_id'],second['design_id']})

    def test_zero_remaining_keeps_park(self):
        self.row['fresh_policy']['excluded_units']=[f'U{i}' for i in range(7)]
        atomic(self.registry/'ACTIVE_DESIGNS.json',dict(designs=[self.row]))
        self.assertEqual(c85.apply(self.pilot)['records'],[])
        self.assertEqual(self.pilot.next_action(self.row),'PARK')

    def test_unrelated_or_combined_park_is_not_resumed(self):
        atomic(self.pilot.case_dir(self.row)/'PARKED.json',dict(reason='V7 retained treated below minimum; '+c85.PARK_REASON))
        self.assertEqual(c85.apply(self.pilot)['records'],[])

    def test_protected_design_is_not_resumed(self):
        ensure_once(self.pilot.backend.root(self.row)/'SEALED_REVIEW.json',dict(synthetic=True))
        self.assertEqual(c85.apply(self.pilot)['records'],[])

    def test_active_loop_or_wrong_window_refused(self):
        atomic(self.registry/'CODE_TASK.lock',dict(task='other'))
        with self.assertRaisesRegex(ValueError,'C85 stopped'):c85.apply(self.pilot)
        atomic(self.registry/'CODE_TASK.lock',dict(task='C85'))
        (self.registry/'AUTOPILOT_STOP').unlink()
        with self.assertRaisesRegex(ValueError,'C85 stopped'):c85.apply(self.pilot)


for cls,parent in ((CensusBridgeTests,OfficialBridgeTests),(ResumeTests,RequeueTests)):
    for name in dir(parent):
        if name.startswith('test_') and name not in cls.__dict__:setattr(cls,name,None)
del OfficialBridgeTests,RequeueTests,cls,parent,name
