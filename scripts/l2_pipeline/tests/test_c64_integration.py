"""Synthetic C64 registration/export and fixed-draw compatibility checks."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline import c64
from scripts.l2_pipeline.engine import ri
from scripts.l2_pipeline.engine.design import build_design, structural_rows
from scripts.l2_pipeline.tests.test_equivalence import invented_fixture
from scripts.l2_pipeline.stages.s7_export import exports

class ExportTests(unittest.TestCase):
    def test_no_computation_at_export_and_missing_is_not_zero(self):
        card=dict(design_id='fake',version='v1',policy_family='fake',track='DiD',lineage={},outcome_family='fake')
        registration=dict(schema=c64.SCHEMA,first_order=dict(purpose_direction=None))
        with patch('scripts.l2_pipeline.l3_addendum.observed_fields',side_effect=AssertionError('S7 must not compute')):
            output=exports(card,dict(status='STOPPED'),None,{},registration)['l3_meta']['l3_export_v2']
        self.assertIsNone(output['s_pre']);self.assertIsNone(output['H'])
        self.assertEqual(output['status'],'STOP_PRE_RUN')
        self.assertEqual(exports(card,dict(status='COMPLETED'),{}, {})['l3_meta']['l3_export_v2']['status'],'addendum fields unavailable')

    def test_new_exports_carry_all_computed_fields(self):
        fields=dict(s_pre=2.,H=4,trough=[dict(unit='A')],placebo_profiles=[dict(draw_index=8)],placebo_labels=dict(seed=10))
        block=c64.export_block({},dict(l3_export_v2=fields),dict(schema=c64.SCHEMA))
        for k,v in fields.items():self.assertEqual(block[k],v)

    def test_fixed_draws_legacy_values_and_hash_unchanged(self):
        d,rows=invented_fixture();design=build_design(d,rows)
        outcomes={(u,t):y for u,t,y,g in structural_rows(d,rows)}
        old=ri.permutation_reference(d,design,outcomes,count=13)
        new=ri.permutation_reference(d,design,outcomes,count=13,l3_export=dict(purpose_direction=-1))
        self.assertEqual(old,{k:v for k,v in new.items() if k not in ('placebo_profiles','placebo_labels')})
        self.assertEqual(len(new['placebo_labels']['draws']),13)
        self.assertEqual([p['draw_index'] for p in new['placebo_profiles']],list(range(13)))
        self.assertTrue(new['placebo_profiles'][0]['group_time'])

    def test_sealed_retention_is_read_only(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'FREEZE_PREP.json';path.write_bytes(b'original')
            before={p.name:p.read_bytes() for p in root.iterdir()}
            self.assertIsNone(c64.retain(SimpleNamespace(design_dir=root)))
            self.assertEqual(before,{p.name:p.read_bytes() for p in root.iterdir()})
