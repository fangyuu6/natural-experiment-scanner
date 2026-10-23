"""C76B2 cheap-gate contracts using only invented metadata."""
import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline import gate0,prescreen
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.tests.test_pipeline import card


def fixture(root):
    c=card();units=['AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD']
    (root/'source').write_text('opaque: do not parse')
    c['outcome'].update(paths=[str(root/'source')],years=[2000,2018],frequency='annual',units=units)
    c['treatment'].update(universe=units,screening_adoption=dict(adopters={u:2005+(i//5)*3 for i,u in enumerate(units[:10])},never_through_coverage_end=units[10:]))
    c['analysis'].update(min_pre_periods=3,min_post_periods=2,min_controls=5,min_cohorts=2,min_treated=6,controls='never_treated')
    return c


class BranchTests(unittest.TestCase):
    def test_legacy_map_unknown_not_zero(self):
        c=card();c['treatment']=dict(universe=['AL','AK','AZ'],screening_adoption_list_not_for_estimation=dict(adoptions={'AL':2005}),screening_support=dict(never_treated_states=['AK']))
        self.assertEqual([(r['unit'],r['start_period']) for r in prescreen.records(c,1)],[('AL',2006),('AK',None)])
        self.assertNotIn('screening_adoption',c['treatment'])

    def test_research_panel_bound_source_excludes_unknown_and_reversed(self):
        c=card();c['treatment']=dict(source_type='research_panel')
        data=dict(records=[dict(unit='AL',status='treated',start_period=2005),dict(unit='AK',status='unknown',start_period=None),dict(unit='AZ',status='treated',start_period=2001,primary_excluded=True)])
        with patch('scripts.l2_pipeline.sources.research_panel.s1.load_inputs',return_value=({},data,{})) as load:
            self.assertEqual(prescreen.records(c,-1),[dict(unit='AL',status='treated',start_period=2004)])
            load.assert_called_once_with(c['treatment'])

    def test_missing_sidecar_requires_optimistic_support_failure(self):
        with tempfile.TemporaryDirectory() as d,patch('scripts.l2_pipeline.population_resolution.choose',side_effect=ValueError('new construct')):
            c=fixture(Path(d));good=prescreen.evaluate(c,Path(d));self.assertEqual(good['status'],'PROCEED')
            self.assertTrue(all(v['passed'] is None for v in good['variants']))
            c['outcome']['years']=[2017,2018];bad=prescreen.evaluate(c,Path(d));self.assertEqual(bad['status'],'PRESCREEN_PARK')
            self.assertTrue(all(v['support_basis']=='optimistic_complete_rectangle' for v in bad['variants']));self.assertEqual(bad['outcome_values_read'],0)

    def test_explicit_support_pointer_is_verified(self):
        with tempfile.TemporaryDirectory() as d,patch('scripts.l2_pipeline.population_resolution.choose',side_effect=ValueError('new construct')),patch('scripts.l2_pipeline.prescreen.screen_ri',return_value=dict(passed=True,checks={})):
            root=Path(d);c=fixture(root);c['outcome']['series_id']='invented'
            side=ensure_once(root/'support.json',dict(series_id='invented',outcome_values_in_sidecar=False,metadata=[dict(unit=u,year=y) for u in c['treatment']['universe'] for y in range(2000,2019)]))
            c['outcome'].update(support_path=str(side),support_sha256=sha(side))
            r=prescreen.evaluate(c,root);self.assertTrue(r['variants'][0]['passed'])
            c['outcome']['support_sha256']='0'*64;r=prescreen.evaluate(c,root);self.assertIn('Support metadata changed',r['variants'][0]['inconclusive'])

    def test_full_year_shared_constructor_with_tolerated_clock(self):
        with tempfile.TemporaryDirectory() as d,patch('scripts.l2_pipeline.population_resolution.choose',side_effect=ValueError('new construct')):
            root=Path(d);c=fixture(root);c['analysis'].update(support_adapter='annual_full_year_v1',full_year_variant='primary')
            c['treatment']['full_year_declaration']=dict(key='invented',period_years=list(range(2000,2019)),window=[2000,2018],operative=c['treatment']['screening_adoption']['adopters'],never_controls=c['treatment']['screening_adoption']['never_through_coverage_end'],source_ready=True)
            c['treatment'].pop('screening_adoption');original=copy.deepcopy(c)
            from scripts.l2_pipeline.full_year import construct_full_year
            with patch('scripts.l2_pipeline.full_year.construct_full_year',wraps=construct_full_year) as build:r=prescreen.evaluate(c,root)
            self.assertEqual(build.call_count,3);self.assertEqual([a.args[0]['operative']['AL'] for a in build.call_args_list],[2005,2004,2006]);self.assertEqual(r['status'],'PROCEED');self.assertTrue(all('s3_pass' in v for v in r['variants']));self.assertEqual(c,original)

    def test_non_did_nonstate_named_adapter_stops(self):
        c=card();c['track']='RDD';self.assertIn('S3_TRACK',prescreen.evaluate(c,Path('.'))['reasons'][0])
        c['track']='DiD';c['unit']='county';self.assertIn('S2_PATH',prescreen.evaluate(c,Path('.'))['reasons'][0])


class LifecycleTests(unittest.TestCase):
    def pilot(self,root):
        root=root.resolve()
        from scripts.l2_pipeline.autopilot import Autopilot
        from scripts.l2_pipeline.tests.test_autopilot import MockAgents
        p=Autopilot(root/'data/l2_pipeline/registry',MockAgents(root));path=ensure_once(root/'card.json',fixture(root))
        return p,dict(design_id='invented',attempt_id='new',route='V9',card=str(path),card_sha256=sha(path),work_dir=str(root/'work'),prescreen_enabled=True)

    def test_seeded_eighth_stable(self):
        selected=[gate0.audit_selected('design'+str(i)) for i in range(8000)]
        self.assertEqual(selected,[gate0.audit_selected('design'+str(i)) for i in range(8000)]);self.assertTrue(900<sum(selected)<1100)
        self.assertNotEqual(selected,[i%8==0 for i in range(8000)])

    def test_catalog_bump_rechecks_park_preserves_old_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            p,r=self.pilot(Path(d));catalog=dict(catalog_id='v1')
            with patch.object(gate0,'catalog_identity',side_effect=lambda _:dict(catalog)),patch.object(gate0,'audit_selected',return_value=False),patch.object(prescreen,'evaluate',side_effect=[dict(status='PRESCREEN_PARK',reasons=['s3'],design_id='invented'),dict(status='PROCEED',reasons=[],design_id='invented')]) as ev:
                first=gate0.persist(p,r);self.assertEqual(gate0.next_status(p,r),'GATE0_PARK');self.assertEqual(ev.call_count,1)
                old=Path(first['receipt']);digest=sha(old);catalog['catalog_id']='v2';self.assertEqual(gate0.next_status(p,r),'GATE0_PARK');gate0.refresh(p,r);self.assertIsNone(gate0.next_status(p,r));self.assertEqual(ev.call_count,2);self.assertEqual(sha(old),digest)

    def test_audit_full_chain_and_censored_stop(self):
        with tempfile.TemporaryDirectory() as d:
            p,r=self.pilot(Path(d))
            with patch.object(gate0,'audit_selected',return_value=True),patch.object(prescreen,'evaluate',return_value=dict(status='PRESCREEN_PARK',reasons=['s3'],design_id='invented')),patch('scripts.l2_pipeline.c50.window_precheck',return_value=None):self.assertEqual(gate0.before_history(p,r)['status'],'PROCEED')
            gate0.record_audit(p,r,terminal='STOP');summary=gate0.summarize_audits(p,[r]);self.assertIsNone(summary['false_reject_rate']);self.assertEqual(summary['censored'],1)
            gate0.record_audit(p,r,terminal='ESTIMATED',estimated=True);summary=gate0.summarize_audits(p,[r]);self.assertEqual(summary['false_reject_rate'],1);self.assertFalse(summary['counts_toward_100'])

    def test_unrelated_park_is_censored_and_matching_gate_is_negative(self):
        from scripts.l2_pipeline.autopilot import atomic
        with tempfile.TemporaryDirectory() as d:
            p,r=self.pilot(Path(d))
            with patch.object(gate0,'audit_selected',return_value=True),patch.object(prescreen,'evaluate',return_value=dict(status='PRESCREEN_PARK',reasons=['s3'],design_id='invented')):gate0.persist(p,r)
            atomic(p.case_dir(r)/'PARKED.json',dict(action='history'))
            self.assertFalse(gate0.record_audit(p,r,terminal='PARK')['completed'])
            atomic(p.case_dir(r)/'PARKED.json',dict(action='S3'))
            value=gate0.record_audit(p,r,terminal='PARK');self.assertTrue(value['completed']);self.assertFalse(value['false_reject'])

    def test_generic_park_catalog_bump_reassesses_once_without_fabricating_purpose(self):
        with tempfile.TemporaryDirectory() as d:
            p,r=self.pilot(Path(d));ensure_once(p.case_dir(r)/'PARKED.json',dict(action='S3',reason='insufficient support'))
            r.pop('prescreen_enabled');catalog=dict(catalog_id='v1')
            with patch.object(gate0,'catalog_identity',side_effect=lambda _:dict(catalog)),patch('scripts.l2_pipeline.population_resolution.choose',side_effect=ValueError('unknown construct')) as choose:
                self.assertFalse(gate0.refresh(p,r)['requeued']);gate0.refresh(p,r);self.assertEqual(choose.call_count,1)
                catalog['catalog_id']='v2';gate0.refresh(p,r);self.assertEqual(choose.call_count,2)
                self.assertTrue((p.case_dir(r)/'PARKED.json').exists())

    def test_legacy_empty_row_summary_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            p,r=self.pilot(Path(d));self.assertIsNone(gate0.current(p,{}));self.assertIsNone(gate0.next_status(p,{}))
            self.assertEqual(gate0.summarize_audits(p,[r])['selected'],0)

    def test_window_before_history_and_sealed_exempt(self):
        from scripts.l2_pipeline.c42 import Park
        with tempfile.TemporaryDirectory() as d:
            p,r=self.pilot(Path(d))
            with patch.object(gate0,'audit_selected',return_value=False),patch('scripts.l2_pipeline.c50.window_precheck',side_effect=Park('empty window')):
                self.assertEqual(gate0.before_history(p,r)['status'],'GATE0_PARK');self.assertEqual(gate0.next_status(p,r),'GATE0_PARK')
            self.assertFalse((p.case_dir(r)/'jobs/history.json').exists());ensure_once(p.backend.root(r)/'FREEZE_PREP.json',{});self.assertTrue(gate0.before_history(p,r)['exempt'])

if __name__=='__main__':unittest.main()
