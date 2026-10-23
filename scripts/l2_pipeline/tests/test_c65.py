"""Metadata-only annotations reach exports while sealed bytes remain immutable."""
import csv,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from scripts.l2_pipeline.c65 import annotations,retain,NAME,PANEL_NAME,AUTH,CLOSURES
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.stages.s7_export import exports

class AnnotationTests(unittest.TestCase):
    def test_names_cannot_substitute_for_support(self):
        for name in ('b1010_permitless_carry','b1010b_child_restraint_booster_age5','b1010_primary_seatbelt','b1010b_zero_tolerance_under21','b1010_handheld_ban_all','unrelated'):
            self.assertIsNone(annotations(name))

    def test_hash_bound_metadata_overlay_no_values_or_masks_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);root=repo/'work';root.mkdir()
            for relative in (AUTH,CLOSURES):
                p=repo/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('invented ruling')
            path=root/'panels/primary/metadata.csv';path.parent.mkdir(parents=True)
            path.write_text('unit,year,available,series_break\nAA,2017,1,denominator_splice\nAA,2018,1,denominator_splice\nAA,2019,1,\n')
            ensure_once(root/'SUPPORT.json',dict(variants=[dict(variant='primary',cell_inventory=[dict(t=2019,base_period=2016,treated=['AA'],controls=['BB'],post=True,n_treated=1)])]))
            original=path.read_bytes();(path.parent/'panel.csv').write_text('POISON: outcome must not be read')
            ctx=SimpleNamespace(repo=repo,design_dir=root,card=SimpleNamespace(design_id='b1010_permitless_carry'))
            result=retain(ctx);self.assertEqual(retain(ctx),result)
            value=json.loads(result.read_text());meta=json.loads((root/PANEL_NAME).read_text())
            self.assertEqual([r['year'] for r in meta['cell_flags']],[2017,2018]);self.assertEqual(path.read_bytes(),original)
            self.assertEqual(meta['source_metadata'][0]['sha256'],sha(path))
            card=dict(design_id=ctx.card.design_id,version='v1',policy_family='invented',track='did',lineage={},outcome_family='health')
            exported=exports(card,dict(status='STOPPED'),None,{},export_annotations=value)
            self.assertEqual(exported['estimates_page']['export_annotations'],value)
            self.assertEqual(exported['l3_meta']['export_annotations'],value)
            ensure_once(root/'FREEZE_PREP.json',dict(frozen=True));before={p:sha(p) for p in root.rglob('*') if p.is_file()}
            self.assertIsNone(retain(ctx));self.assertEqual(before,{p:sha(p) for p in root.rglob('*') if p.is_file()})
