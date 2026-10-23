"""C42 history exclusions and completeness checks use synthetic data only."""
import contextlib
import io
import json
from pathlib import Path
import unittest

from scripts.l2_pipeline.c42 import Park, completeness, exclusion_scope, effective_context, post_support_gate
from scripts.l2_pipeline.autopilot import atomic, ingest, read
from scripts.l2_pipeline.tests.test_autopilot import AutopilotTests, OfficialBridgeTests


class ExclusionTests(unittest.TestCase):
    def test_all_gap_and_interruption_kinds_exclude_both_arms(self):
        cfg=dict(universe=['T','C','U','G'], excluded_units=['G'])
        comparison=dict(gaps=[dict(unit='U',kind='both_unknown'),dict(unit='G',kind='known_conflict')])
        values=[dict(records=[dict(unit='T',observed_interruptions=[2000]),
                              dict(unit='C',history_uncertain=True)]),dict(records=[])]
        result=exclusion_scope(cfg,comparison,values)
        self.assertEqual(result['excluded_units'], ['C','G','T','U'])
        self.assertEqual(result['excluded_conflict_states'],result['excluded_units'])
        self.assertEqual(result['excluded_conflict_state_reasons']['C'],['A:history_uncertain'])
        self.assertEqual(cfg['excluded_units'],['G'])
        self.assertEqual(exclusion_scope(result,comparison,values),result)

    def test_completeness_checks_analysis_without_inventing_declarations(self):
        card=read(Path(__file__).parents[1]/'examples/design.json')
        card['analysis']={}
        errors=completeness(card)
        self.assertIn('$.analysis.estimand: missing',errors)
        self.assertEqual(card['analysis'],{})

    def test_prospective_card_requires_first_order_even_if_archival_schema_allows_it(self):
        card=read(Path(__file__).parents[1]/'examples/design.json')
        card['analysis']['min_post_periods']=2
        del card['first_order']
        self.assertIn('$.first_order: missing prospective purpose declaration',completeness(card))

    def test_post_gate_parks_single_post_cohort_and_preserves_registered_counts(self):
        support=dict(variants=[dict(variant='primary',retained_treated=list('ABCDEF'),
            never_controls=list('GHIJK'),cells=[dict(cohort=2003,period=t) for t in (2003,2004)]+
                                                 [dict(cohort=2004,period=2004)])])
        with self.assertRaises(Park) as error:post_support_gate(support,dict(min_post_periods=2))
        self.assertEqual(error.exception.metrics['post_periods_by_cohort'],{'2003':2,'2004':1})
        self.assertEqual(error.exception.metrics['retained_controls'],5)
        support['variants'][0]['cells'].append(dict(cohort=2004,period=2005))
        self.assertEqual(post_support_gate(support,dict(min_post_periods=2))['outcome_values_read'],0)


class C42SchedulerTests(AutopilotTests):
    # Reuse fixtures without duplicating the inherited scheduler suite.
    def test_park_is_durable_and_does_not_create_hub_ruling(self):
        row=self.seed(1)[0]
        self.backend.fail['d0','reconcile']=Park('ten-treated audit unavailable',dict(fresh_treated=9))
        self.pilot.tick();self.backend.finish();self.pilot.tick()
        self.assertEqual(self.pilot.next_action(row),'PARK')
        self.assertFalse((self.registry/'HUB_QUEUE.json').exists())
        park=read(self.pilot.case_dir(row)/'PARKED.json')
        self.assertEqual(park['metrics']['fresh_treated'],9)
        self.assertNotIn(('d0','S0'),self.backend.actions)

    def test_bulk_completeness_reports_every_card_before_mutation(self):
        self.seed(0)
        cards=self.repo/'data/l2_pipeline/cards';cards.mkdir(parents=True)
        for name in ('a','b'):
            atomic(cards/(name+'.json'),dict(design_id=name))
        output=io.StringIO()
        with contextlib.redirect_stdout(output),self.assertRaisesRegex(ValueError,'Incomplete'):
            ingest(cards,self.pilot)
        reports=[json.loads(line.split(' ',1)[1]) for line in output.getvalue().splitlines()]
        self.assertEqual([r['design_id'] for r in reports],['a','b'])
        self.assertEqual(read(self.registry/'ACTIVE_DESIGNS.json')['designs'],[])


class C42BridgeTests(OfficialBridgeTests):
    def test_stage_adapter_enforces_post_gate_after_existing_s3_pass(self):
        from unittest.mock import patch
        from scripts.l2_pipeline.autopilot import Backend
        from scripts.l2_pipeline.contracts import StageResult, Disposition
        self.prepare()
        atomic(self.ctx.design_dir/'SUPPORT.json',dict(variants=[dict(variant='primary',
            retained_treated=list('ABCDEF'),never_controls=list('GHIJK'),
            cells=[dict(cohort=2004,period=2004)])]))
        backend=Backend(self.ctx.repo)
        with patch.object(backend,'ctx',return_value=self.ctx),patch('scripts.l2_pipeline.cli.preflight'), \
             patch('scripts.l2_pipeline.cli.run_stage',return_value=StageResult('S3',self.ctx.card.design_id,Disposition.READY,'synthetic existing pass')):
            with self.assertRaisesRegex(Park,'post periods below minimum'):
                backend.perform({},'S3',None)

    def test_exclusion_reload_preserves_later_preparation_exclusions(self):
        from dataclasses import replace
        from copy import deepcopy
        self.prepare()
        payload=deepcopy(dict(self.ctx.card.payload));payload['treatment']['excluded_units']=['U3']
        context=replace(self.ctx,card=replace(self.ctx.card,payload=payload))
        self.assertIn('U3',effective_context(context).card.payload['treatment']['excluded_units'])

    def test_registered_exclusions_survive_s1_and_context_reload(self):
        self.policy['excluded_units']=[]
        # The original fixture declares no exclusion; C42 declaration still
        # binds evidence and is transported to the scientific card and S1.
        job=self.prepare();self.audit(job)
        from scripts.l2_pipeline.autopilot_history import accept
        accept(self.ctx,job.job_path)
        ctx=effective_context(self.ctx)
        self.assertEqual(ctx.card.payload['treatment']['excluded_conflict_states'],[])
        proof=read(ctx.design_dir/'S1_IMPORT_PROVENANCE.json')
        self.assertTrue(any(Path(i['path']).name=='C42_PREPARATION.json' for i in proof['evidence']))

    def test_insufficient_fresh_sample_retains_exact_counts_and_evidence(self):
        self.policy['excluded_units']=['U'+str(i) for i in range(12)]
        with self.assertRaises(Park) as found:self.prepare()
        self.assertEqual(found.exception.metrics['counts']['fresh_treated'],0)
        self.assertEqual(found.exception.metrics['counts']['retained_treated'],12)
        self.assertTrue((self.ctx.design_dir/'RECONCILIATION.json').exists())
        self.assertFalse((self.ctx.design_dir/'AUDIT_SELECTION.json').exists())


# Only the C42 tests above inherit fixture methods, not the parent tests.
for cls, parent in ((C42SchedulerTests,AutopilotTests),(C42BridgeTests,OfficialBridgeTests)):
    for name in dir(parent):
        if name.startswith('test_') and name not in cls.__dict__:
            setattr(cls,name,None)
del AutopilotTests, OfficialBridgeTests, cls, parent, name

if __name__=='__main__':unittest.main()
