"""Invented source trees and study manifests; no outcome loading or fitting."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.dependency_closure import dependency_inventory
from scripts.l2_pipeline.freeze import ACTIONS, REQUIRED_READINESS, ROLES, build_manifest, code_inventory, verify
from scripts.l2_pipeline.storage import sha, write_once


class DependencyClosureTests(unittest.TestCase):
    def source(self, root, name, text):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_transitive_lazy_package_imports_and_no_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.source(root, "pkg/__init__.py", "raise RuntimeError('must never execute')\n")
            entry = self.source(root, "pkg/entry.py", "def run():\n from . import bridge\n")
            self.source(root, "pkg/bridge.py", "from outside.worker import compute\n")
            self.source(root, "outside/__init__.py", "")
            self.source(root, "outside/worker.py", "from . import utility\n")
            resource = self.source(root, "outside/schema.json", '{"invented":true}\n')
            self.source(root, "outside/utility.py", "from pathlib import Path\nSCHEMA=Path(__file__).with_name('schema.json').read_text()\n")
            self.source(root, "outside/unrelated.py", "raise RuntimeError('unrelated')\n")
            bound = dependency_inventory(root, [entry])
            self.assertEqual(set(bound), {str((root / name).resolve()) for name in (
                "pkg/__init__.py", "pkg/entry.py", "pkg/bridge.py", "outside/__init__.py",
                "outside/worker.py", "outside/utility.py", "outside/schema.json")})
            self.assertEqual(bound[str(resource.resolve())], sha(resource))

    def test_dynamic_imports_and_all_pipeline_guard_are_conservative(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = self.source(root, "pkg/entry.py", "import importlib\nfrom importlib import import_module as load\nload('external.literal')\ndef run(name):\n return importlib.import_module('external.plugins.'+name)\n")
            self.source(root, "external/literal.py", "from . import nested\n")
            self.source(root, "external/nested.py", "")
            plugin = self.source(root, "external/plugins/new.py", "")
            test = self.source(root, "pkg/tests/test_fixture.py", "from missing_test_only_helper import x\n")
            bound = dependency_inventory(root, [entry], pipeline_root=root / "pkg")
            self.assertIn(str(plugin.resolve()), bound)
            self.assertIn(str(test.resolve()), bound)
            self.assertIn(str((root / "external/nested.py").resolve()), bound)
            self.source(root, "external/plugins/later.py", "")
            self.assertNotEqual(bound, dependency_inventory(root, [entry], pipeline_root=root / "pkg"))

    def test_missing_syntax_unknown_dynamic_and_escape_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            entry = self.source(root, "pkg/entry.py", "from pkg.deleted import work\n")
            with self.assertRaisesRegex(ValueError, "Missing repository import"):
                dependency_inventory(root, [entry])
            entry.write_text("def broken(\n")
            with self.assertRaisesRegex(ValueError, "Cannot inspect"):
                dependency_inventory(root, [entry])
            entry.write_text("import importlib\nimportlib.import_module(variable)\n")
            with self.assertRaisesRegex(ValueError, "static repository binding"):
                dependency_inventory(root, [entry])
            entry.write_text("import importlib.util\nimportlib.util.spec_from_file_location('unknown', variable)\n")
            with self.assertRaisesRegex(ValueError, "explicit repository binding"):
                dependency_inventory(root, [entry])
            external = self.source(Path(outside), "danger.py", "raise RuntimeError('no execution')\n")
            entry.unlink()
            entry.symlink_to(external)
            with self.assertRaisesRegex(ValueError, "escapes repository"):
                dependency_inventory(root, [entry])

    def test_real_inventory_binds_entry_guard_and_repo_dependencies(self):
        # Source hashes only; this never imports the external runtime modules.
        inventory = code_inventory()
        repo = Path(__file__).resolve().parents[3]
        for relative in ("scripts/l2_pipeline/stages/s6_execute.py", "scripts/l2_pipeline/freeze.py",
                         "scripts/l2_pipeline/dependency_closure.py", "scripts/l3_pooled/dta_reader.py",
                         "scripts/pipeline/registry/validate.py", "scripts/pipeline/registry/schema.json"):
            path = repo / relative
            self.assertEqual(inventory[str(path.resolve())], sha(path))

    def test_prep_final_and_guard_recompute_same_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "invented_repo"
            entry = self.source(repo, "pkg/entry.py", "from external import worker\n")
            worker = self.source(repo, "external/worker.py", "VALUE=1\n")
            study = Path(tmp) / "study"
            files = {}
            for role in ROLES:
                name = role + ".json"
                write_once(study / name, {"design_id": "invented"} if role == "card" else {"invented": role})
                files[name] = role
            prediction = "predictions/x/submission.json"
            write_once(study / prediction, {"persona": "x", "design_id": "invented", "spec_sha256": sha(study / "spec.json")})
            files[prediction] = "predictions"
            write_once(study / "PREDICTIONS_LOCK.json", {"personas": ["x"], "design_id": "invented",
                "spec_sha256": sha(study / "spec.json"), "files": {prediction: sha(study / prediction)}})
            files["PREDICTIONS_LOCK.json"] = "predictions"
            def inventory():
                return dependency_inventory(repo, [entry], pipeline_root=repo / "pkg")
            with patch("scripts.l2_pipeline.freeze.code_inventory", side_effect=inventory), \
                 patch("scripts.l2_pipeline.registered.interval_plan", return_value=({"expected_designs": ["invented"]}, None)), \
                 patch("scripts.l2_pipeline.registered.validate_calibration"):
                build_manifest(study, "prep", files, ["x"], "invented_hub")
                write_once(study / "APPROVAL_PREP.json", {"status": "APPROVED", "signed_by": "invented_hub",
                    "signed_at_utc": "2026-10-08T00:00:00Z", "approved_actions": ACTIONS["prep"],
                    "execution_authorized": False, "manifest_path": "FREEZE_PREP.json",
                    "manifest_sha256": sha(study / "FREEZE_PREP.json")})
                write_once(study / "FINAL_READINESS.json", {key: True for key in REQUIRED_READINESS})
                write_once(study / "CALIBRATION_GATES.json", {"passed": True, "cases": {"invented": {"case_id": "invented"}}})
                write_once(study / "REVIEW.json", {"invented": True})
                final_files = dict(files, **{name: "verification" for name in (
                    "FREEZE_PREP.json", "APPROVAL_PREP.json", "FINAL_READINESS.json", "CALIBRATION_GATES.json", "REVIEW.json")})
                build_manifest(study, "final", final_files, ["x"], "invented_hub")
                self.assertTrue(verify(study, "final", "invented_hub", require_approval=False)["ok"])
                prep = json.loads((study / "FREEZE_PREP.json").read_text())
                final = json.loads((study / "FREEZE_FINAL.json").read_text())
                self.assertEqual(prep["code_inventory"], final["code_inventory"])
                self.assertIn(str(worker.resolve()), final["code_inventory"])
                worker.write_text("VALUE=2\n")
                for stage in ("prep", "final"):
                    result = verify(study, stage, "invented_hub", require_approval=False)
                    self.assertTrue(result["ok"], result["issues"])
                tree = study / final["code_snapshot"]["base"] / "tree"
                snapshot_worker = tree / worker.resolve().relative_to(Path(final["code_snapshot"]["source_repo"]))
                snapshot_worker.write_text("VALUE=3\n")
                self.assertFalse(verify(study, "final", "invented_hub", require_approval=False)["ok"])


if __name__ == "__main__":
    unittest.main()
