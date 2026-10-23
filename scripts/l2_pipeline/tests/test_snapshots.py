"""Invented freezes only; isolation and refusal before any outcome loader."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from scripts.l2_pipeline import snapshots
from scripts.l2_pipeline.freeze import code_inventory, verify
from scripts.l2_pipeline.storage import sha
from scripts.l2_pipeline.tests import test_pipeline


class SnapshotTests(unittest.TestCase):
    def test_rate_card_and_companion_resolve_without_construction_sources(self):
        """Replay the omitted-resource failure in a genuinely isolated tree."""
        repo = Path(__file__).resolve().parents[3]
        card = json.loads((repo/'scripts/l2_pipeline/examples/design.json').read_text())
        card['outcome_scale'] = 'rate'
        card['outcome'].update(series_id='wonder_firearm_homicide_deaths',
                               measure_id='wonder_firearm_homicide_deaths')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            inventory = code_inventory()
            record = snapshots.create(root, inventory, repo)
            tree = snapshots.validate(root, record, inventory)
            fixture = root/'card.json'; fixture.write_text(json.dumps(card))
            script = """import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
original = Path.open
def guarded(path, *args, **kwargs):
    if 'data' in path.resolve().parts:
        raise AssertionError('Construction source read: '+str(path))
    return original(path, *args, **kwargs)
Path.open = guarded
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.variants import secondary_card
from scripts.l2_pipeline.connectors import CONNECTOR_CATALOG
card = load_card(Path(sys.argv[2]))
peer = secondary_card(card.payload, dict(role='secondary',
    outcome='wonder_firearm_homicide_deaths_female',
    connector='outcome_v6_wonder_firearm_homicide_deaths_female'))
assert peer['outcome']['population_filters']['sex'] == 'female'
assert peer['outcome_scale'] == 'rate'
assert CONNECTOR_CATALOG[peer['outcome']['connector']]['source_validation_required'] is True
print('metadata-only snapshot replay passed')
"""
            result = subprocess.run([sys.executable, '-I', '-B',
                *(['-O'] if sys.flags.optimize else []), '-c', script, str(tree), str(fixture)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('metadata-only snapshot replay passed', result.stdout)

    def test_registered_wonder_builders_still_require_source_activation(self):
        """Metadata registration cannot authorize unverified source building."""
        from scripts.l2_pipeline.connectors import wonder_registry, outcome_v5, outcome_v6
        from scripts.l2_pipeline.connectors.common import ConnectorRequest, ConnectorError
        sid = 'wonder_firearm_homicide_deaths'
        for module, version in ((wonder_registry, 4), (outcome_v5, 5), (outcome_v6, 6)):
            data = (wonder_registry.catalog(verify_sources=False) if version == 4 else
                    outcome_v5.catalog(module.CATALOG_PATH, version, verify_sources=False))
            filters = data['series'][sid]['population_filters']
            request = ConnectorRequest(dict(connector=f'outcome_v{version}_{sid}',
                series_id=sid, measure_id=sid, frequency='annual', population_filters=filters),
                filters, (2018,), ('AL',), 'annual', Path('/invented'))
            builder = wonder_registry if version == 4 else outcome_v5
            with patch.object(builder, '_verified_wonder', side_effect=ConnectorError('unverified source')) as check, \
                 patch('scripts.l2_pipeline.connectors.wonder.build', side_effect=AssertionError('Source loader reached')):
                with self.assertRaisesRegex(ConnectorError, 'unverified source'):
                    builder._construct(request, data)
                check.assert_called_once_with(data['series'][sid])

    def bundle(self, temporary):
        base = Path(temporary).resolve(); repo = base/'repo'; study = base/'study'
        real_repo = Path(__file__).resolve().parents[3]
        inventory = {}
        for source, digest in code_inventory().items():
            target = repo/Path(source).relative_to(real_repo)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(Path(source).read_bytes())
            inventory[str(target)] = digest
        with patch('scripts.l2_pipeline.freeze.code_inventory', return_value=inventory):
            test_pipeline.FinalApprovalTests().make_bundle(study)
        return repo, study, json.loads((study/'FREEZE_FINAL.json').read_text())

    def test_live_edits_ignored_by_verifier_and_isolated_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo, study, manifest = self.bundle(temporary)
            baseline = snapshots.call(study, manifest, 'probe', {})
            (repo/'scripts/l2_pipeline/engine/ri.py').write_text("raise RuntimeError('live tree executed')\n")
            (repo/'scripts/l2_pipeline/freeze.py').write_text("raise RuntimeError('live guard executed')\n")
            report = verify(study, 'final', 'fixture_hub')
            self.assertTrue(report['ok'], report['issues'])
            self.assertEqual(snapshots.call(study, manifest, 'probe', {}), baseline)
            self.assertIn(manifest['code_snapshot']['base'], baseline['file'])
            prep = json.loads((study/'FREEZE_PREP.json').read_text())
            self.assertEqual(prep['code_snapshot'], manifest['code_snapshot'])
            self.assertEqual(prep['code_inventory'], manifest['code_inventory'])

    def test_tree_object_and_extra_file_tampering_refused_before_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, study, manifest = self.bundle(temporary)
            record = manifest['code_snapshot']; base = study/record['base']
            relative = 'scripts/l2_pipeline/engine/ri.py'; digest = record['entries'][relative]
            for target in (base/'tree'/relative, base/'objects'/digest):
                original = target.read_bytes(); target.write_text('raise RuntimeError("tamper")\n')
                with patch('scripts.l2_pipeline.snapshots.subprocess.run', side_effect=AssertionError('Worker opened')):
                    with self.assertRaisesRegex(ValueError, 'Tampered code snapshot'):
                        snapshots.call(study, manifest, 'probe', {})
                self.assertFalse(verify(study, 'final', 'fixture_hub')['ok'])
                target.write_bytes(original)
            extra = base/'tree/scripts/unbound.py'; extra.write_text('UNBOUND=True\n')
            with self.assertRaisesRegex(ValueError, 'Unbound code snapshot file'):
                snapshots.call(study, manifest, 'probe', {})

    def test_sealed_collection_uses_bound_collector_after_live_edit(self):
        from types import SimpleNamespace
        from scripts.l2_pipeline.agents.jobs import emit_job
        from scripts.l2_pipeline.agents.collect import collect_job
        from scripts.l2_pipeline.cards import load_card
        from scripts.l2_pipeline.storage import write_once
        with tempfile.TemporaryDirectory() as temporary:
            repo, study, manifest = self.bundle(temporary)
            review = json.loads((study/'EXECUTION_REVIEW.json').read_text())
            (study/'EXECUTION_REVIEW.json').unlink()
            (study/'APPROVAL_FINAL.json').unlink()
            ctx = SimpleNamespace(design_dir=study, card=load_card(study/'card.json'),
                                  repo=Path(__file__).resolve().parents[3])
            job = emit_job(ctx, 'S6', 'reviewer', jobs_root=study/'jobs', attempt_id='invented')
            write_once(Path(job.output_path), review)
            (repo/'scripts/l2_pipeline/agents/collect.py').write_text("raise RuntimeError('live collector executed')\n")
            receipt = collect_job(job.job_path)
            self.assertTrue(receipt['validated'])
            self.assertFalse(receipt['execution_authorized'])
            self.assertFalse((study/'EXECUTION_ONCE.json').exists())

    def test_single_run_refuses_live_callbacks_without_consuming_slot(self):
        from scripts.l2_pipeline.execution import execute_once
        with tempfile.TemporaryDirectory() as temporary:
            _, study, _ = self.bundle(temporary)
            with self.assertRaisesRegex(RuntimeError, 'Snapshot registered runner required'):
                execute_once(study, 'fixture_hub', lambda: self.fail('Live loader'), lambda x: {})
            self.assertFalse((study/'EXECUTION_ONCE.json').exists())


if __name__ == '__main__': unittest.main()
