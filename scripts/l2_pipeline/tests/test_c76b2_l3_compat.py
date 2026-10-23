"""Synthetic L3 roster/read-back contracts; never run an L3 report."""
import ast
import copy
import json
import statistics
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts.l3_meta.reader import forecast_summary,read_export
from scripts.l2_pipeline import c64
from scripts.l2_pipeline.autopilot import Autopilot,atomic,read
from scripts.l2_pipeline.tests.test_autopilot import MockAgents
from scripts.l2_pipeline.tests.test_pipeline import card
from scripts.l2_pipeline.stages.s7_export import exports


def roster(n):
    roles=['slot'+str(i) for i in range(n)]
    lock=dict(personas=roles)
    if n==3:lock['forecast_slots']={r:dict(slot=i+1) for i,r in enumerate(roles)}
    return [dict(persona=r,center=i,lock_verified=True) for i,r in enumerate(roles)],lock


class L3Compatibility(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()

    def test_three_and_five_slot_medians_retain_distinct_denominators(self):
        three=forecast_summary(*roster(3));five=forecast_summary(*roster(5))
        self.assertEqual((three['forecast_slot_count'],three['forecast_median']),(3,1))
        self.assertIsNone(three['forecast_median_of_five'])
        self.assertEqual(three['forecast_roster_kind'],'prospective_three')
        self.assertEqual((five['forecast_median'],five['forecast_median_of_five']),(2,2))
        self.assertEqual(five['forecast_roster_kind'],'legacy_five')
        self.assertEqual(forecast_summary([],None)['forecast_slot_count'],0)

    def test_incomplete_or_unverified_roster_rejected_in_both_modes(self):
        for mutate in ('missing','wrong_name','unverified','missing_contract','wrong_contract'):
            slots,lock=roster(3)
            if mutate=='missing':slots.pop()
            if mutate=='wrong_name':slots[0]['persona']='different'
            if mutate=='unverified':slots[0]['lock_verified']=False
            if mutate=='missing_contract':del lock['forecast_slots']
            if mutate=='wrong_contract':lock['forecast_slots'].pop('slot0')
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):forecast_summary(slots,lock)

    def test_legacy_inline_missing_l3_is_explicit_new_missing_l3_rejected(self):
        result=self.root/'RESULT.json';atomic(result,dict(status='STOPPED'))
        base=dict(design_id='fake',spec_version='v1',status='STOPPED',result={},selection_denominator='all_queued_designs_including_stops')
        path=self.root/'L3_EXPORT_DATA.json';atomic(path,base)
        self.assertEqual(read_export(path)['l3_export_status'],'FIELD_MISSING_LEGACY')
        self.assertNotIn('l3_export_v2',read_export(path))
        for patch_value in (dict(result={'$ref':c64.reference(result)}),dict(forecast_independence={})):
            atomic(path,dict(base,**patch_value))
            with self.assertRaisesRegex(ValueError,'Incomplete'):read_export(path)

    def test_q3_functions_keep_three_slot_rows_out_of_both_old_denominators(self):
        # Load only these pure functions, not builder imports or report execution.
        source=Path(__file__).resolve().parents[2]/'l3_meta/build_v2.py'
        nodes=[n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name in {'brier_skill','score_forecasts'}]
        namespace={'statistics':statistics}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),namespace)
        slots=[dict(center=1,p_positive=.7,exact_positive_event=True) for _ in range(3)]
        row=dict(design_id='invented',b=1,forecast_roster_kind='prospective_three',forecast_slots=slots)
        for full in (True,False):self.assertEqual(namespace['brier_skill']([row],full),(None,[]))
        self.assertEqual(namespace['score_forecasts']([row]),[])

    def register(self,has_numerical):
        backend=MockAgents(self.root);pilot=Autopilot(self.root/'data/l2_pipeline/registry',backend)
        pilot.registry.mkdir(parents=True,exist_ok=True)
        row=dict(design_id='fake',attempt_id='synthetic',work_dir=str(self.root/'data/l2_pipeline/work'))
        root=backend.root(row)
        result=dict(status='STOPPED',actual_data_estimation_calls=0)
        if has_numerical:result['l3_export_v2']={}
        atomic(root/'RESULT.json',result);atomic(root/'TERMINAL.json',dict(status='STOPPED'))
        value=card();value['design_id']='fake'
        exported=exports(value,dict(status='STOPPED'),result,{},result_reference=c64.reference(root/'RESULT.json'))
        atomic(root/'EXPORT.json',exported)
        with patch.object(backend,'ctx',return_value=SimpleNamespace(card=SimpleNamespace(version='v1'))),patch('scripts.l2_pipeline.snapshots.manifest_for',return_value=None):
            pilot.register_terminal(row,root)
        return read(pilot.registry/'TERMINAL_RUNS.json')['runs'][0],root

    def test_early_stop_without_numerical_block_has_no_dangling_pointer(self):
        record,root=self.register(False)
        self.assertNotIn('l3_export_v2_reference',record)
        self.assertEqual(c64.read_l3_export(root/'L3_EXPORT_DATA.json')['result']['actual_data_estimation_calls'],0)

    def test_present_empty_numerical_block_reference_resolves(self):
        record,root=self.register(True);ref=record['l3_export_v2_reference']
        relative=dict(ref,path=Path(ref['path']).name)
        self.assertEqual(c64.resolve_references({'$ref':relative},root),{})


if __name__=='__main__':unittest.main()
