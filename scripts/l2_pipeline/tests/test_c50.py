"""C50 synthetic regressions; no real outcomes or study fits."""
from copy import deepcopy
from pathlib import Path
from dataclasses import replace
import tempfile,unittest,json
from unittest.mock import patch
from scripts.l2_pipeline.connectors import c35_core,c46_core,population_v63
from scripts.l2_pipeline.connectors.common import ConnectorRequest,ConnectorError,sha
from scripts.l2_pipeline.c50 import window_card,equivalence_target
from scripts.l2_pipeline.coverage import coverage_bounded_card
from scripts.l2_pipeline.agents.replacement import rejection_kind
from scripts.l2_pipeline.c42 import Park
from scripts.l2_pipeline.autopilot import Autopilot,Backend,read
from scripts.l2_pipeline.storage import ensure_once
from scripts.l2_pipeline.c50 import reopen

class SourceTests(unittest.TestCase):
    def test_compact_and_explicit_are_identical_and_wrong_binding_refused(self):
        for module in (c35_core,c46_core):
            with tempfile.TemporaryDirectory() as d,patch.object(module,'REPO',Path(d)):
                path=Path(d)/'artifact.json';payload=dict(series_id='fixture',population_filters={},rows=[dict(unit='AL',year=2000,value=123)],metadata=[dict(unit='AL',year=2000,available=True)],inventory=[],limitations=[],details={})
                path.write_text(json.dumps(payload));spec=dict(series_id='fixture',population_filters={},frequency=['annual'],years=[2000],artifact=dict(path='artifact.json',sha256=sha(path)))
                compact=ConnectorRequest({}, {},(2000,),('AL',),'annual',Path(d)/'unrelated/card')
                explicit=replace(compact,options=dict(paths=['artifact.json'],source_sha256={'artifact.json':sha(path)}))
                self.assertEqual(module.construct(compact,spec),module.construct(explicit,spec))
                absolute=replace(compact,options=dict(paths=[str(path)],source_sha256={str(path):sha(path)}))
                self.assertEqual(module.construct(compact,spec),module.construct(absolute,spec))
                for opts in [dict(paths=['wrong.json']),dict(paths=[]),dict(source_sha256={'artifact.json':'bad'}),dict(paths=['artifact.json'],source_sha256={'artifact.json':'bad'})]:
                    with self.assertRaises(ConnectorError):module.construct(replace(compact,options=opts),spec)
                path.write_text('{}')
                with self.assertRaises(ConnectorError):module.construct(compact,spec)

class RulingTests(unittest.TestCase):
    def test_equivalence_table_is_bound_by_code_snapshot_inventory(self):
        from scripts.l2_pipeline.freeze import code_inventory
        from scripts.l2_pipeline.c50 import TABLE
        self.assertEqual(code_inventory()[str(TABLE.resolve())],sha(TABLE))

    def test_construct_id_and_approved_aliases_match_without_fuzzy_guess(self):
        _,meta=population_v63.current_catalog()
        for alias,construct,sid in [('Violent crime offense incidence','reported_violent_crime','fbi_ucr_violent_crime_rate'),('drinking driving behavior','drinking_driver_crash_deaths','fars_drinking_driver_crash_fatality_rate'),('motor_vehicle_bicycle_overtaking_lateral_clearance','pedalcyclist_traffic_deaths','fars_pedalcyclist_fatality_rate_all_ages'),('a verifier near synonym','voter_turnout','turnout_vep_highest_office_rate')]:
            target=population_v63.target_metadata(alias,construct=construct,meta=meta)
            self.assertEqual(target['preferred_series'],sid)
            self.assertEqual(target['construct'],construct)
        self.assertIsNone(equivalence_target('unreviewed death construct',construct='new_construct',meta=meta))
        self.assertIsNone(equivalence_target('drinking driving behavior',meta=dict(meta,catalog_id='first_order_outcomes_v9')))

    def test_mechanical_window_preserves_science_holes_and_does_not_mutate(self):
        card=dict(version='v1',outcome=dict(connector='invented',years=[1980,2025],period_years=list(range(1980,2026))),treatment=dict(source_window=dict(start_year=1983,end_year=1995),coverage_end='1995-12-31'),analysis=dict(min_post_periods=2),sensitivities=[dict(name='shift',changes=dict(treatment=dict(start_shift=1)))],automation={})
        catalog=dict(invented=dict(coverage=dict(start=1975,end=2020,excluded_years=[1990])))
        old=deepcopy(card);new=window_card(card,catalog=catalog)
        self.assertEqual(card,old);self.assertEqual(new['version'],'v2');self.assertEqual(new['outcome']['years'],[1983,1995]);self.assertNotIn(1990,new['outcome']['period_years']);self.assertEqual(new['analysis'],old['analysis']);self.assertEqual(new['treatment'],old['treatment']);self.assertEqual(new['sensitivities'][0]['changes']['treatment'],old['sensitivities'][0]['changes']['treatment'])
        self.assertEqual(coverage_bounded_card(new,catalog=catalog)['outcome'],new['outcome'])
        card['treatment']['coverage_end']='1970-12-31'
        with self.assertRaises(Park):window_card(card,catalog=catalog)

    def test_absence_contract_defect_never_replaces_substantive_failure(self):
        exc=ValueError('V9 absence requires all three V2_LIST checks')
        self.assertEqual(rejection_kind(exc),'V9_ABSENCE')
        self.assertIsNone(rejection_kind(exc,dict(verdict='STOP',must=['M01'])))

    def test_new_stop_after_c50_supersession_remains_visible(self):
        with tempfile.TemporaryDirectory() as d:
            p=Autopilot(Path(d)/'data/l2_pipeline/registry',Backend(Path(d).resolve()));row=dict(design_id='fixture',attempt_id='a')
            p.stop(row,'S2','old');reopen(p,row,[]);self.assertIsNone(p.stop_record(row));p.stop(row,'S1','new');self.assertEqual(p.stop_record(row)['reason'],'new')

    def test_fresh_v3_failure_parks_with_counts(self):
        from scripts.l2_pipeline.c48_audit import bound_comparison
        from scripts.l2_pipeline.tests.test_c48 import AuditTests
        fixture=AuditTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        with patch('scripts.l2_pipeline.intake.official_history.corrected_comparison',return_value=({},dict(passed=False,known_start_errors=3,unresolved=0,sample_size=10),[])):
            with self.assertRaises(Park) as ctx:bound_comparison(fixture.evidence)
        self.assertEqual(ctx.exception.metrics['known_start_errors'],3)

if __name__=='__main__':unittest.main()
