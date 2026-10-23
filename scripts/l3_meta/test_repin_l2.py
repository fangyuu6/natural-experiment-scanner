"""Tests of repin_l2.lazy_closure on an INVENTED package tree (lazily imported modules must be found)."""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import repin_l2 as P  # noqa: E402


class Closure(unittest.TestCase):
    def test_lazy_function_level_imports(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pk = root / "scripts" / "l2_pipeline"
            (pk / "engine").mkdir(parents=True)
            (pk / "__init__.py").write_text("")
            (pk / "engine" / "__init__.py").write_text("")
            (pk / "a.py").write_text("def f():\n    from . import rules\n    from .b import x\n")
            (pk / "rules.py").write_text("from .engine import deep\n")
            (pk / "b.py").write_text("x = 1\n")
            (pk / "engine" / "deep.py").write_text("import json\nfrom ..c import y\n")
            (pk / "c.py").write_text("y = 2\n")
            (pk / "unrelated.py").write_text("")
            old = P.ROOT
            P.ROOT = root
            try:
                out = P.lazy_closure({"scripts/l2_pipeline/a.py": "h"})
            finally:
                P.ROOT = old
            self.assertEqual(set(out), {"scripts/l2_pipeline/a.py", "scripts/l2_pipeline/rules.py", "scripts/l2_pipeline/b.py",
                                        "scripts/l2_pipeline/engine/deep.py", "scripts/l2_pipeline/c.py", "scripts/l2_pipeline/engine/__init__.py", "scripts/l2_pipeline/__init__.py"})
            self.assertEqual(out["scripts/l2_pipeline/a.py"], "h")

    def test_new_module_function_level_imports_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            pk = Path(d) / "scripts" / "l2_pipeline"
            pk.mkdir(parents=True)
            (pk / "__init__.py").write_text("")
            (pk / "a.py").write_text("def f():\n    from . import rules\n")
            (pk / "rules.py").write_text("def g():\n    from . import other\n")
            (pk / "other.py").write_text("")
            old = P.ROOT
            P.ROOT = Path(d)
            try:
                out = P.lazy_closure({"scripts/l2_pipeline/a.py": "h"})
            finally:
                P.ROOT = old
            self.assertIn("scripts/l2_pipeline/rules.py", out)
            self.assertNotIn("scripts/l2_pipeline/other.py", out)

    def test_real_registration_pulls_rules(self):
        out = P.lazy_closure({"scripts/l2_pipeline/registration.py": "h"})
        self.assertIn("scripts/l2_pipeline/rules.py", out)

    def test_refresh_entry_seed_invented_tree(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pk = root / "scripts" / "l2_pipeline"
            l3 = root / "scripts" / "l3_meta"
            pk.mkdir(parents=True)
            l3.mkdir(parents=True)
            (pk / "__init__.py").write_text("")
            (pk / "fs.py").write_text("")
            (pk / "c64.py").write_text("")
            (pk / "unrelated.py").write_text("")
            (l3 / "entry.py").write_text("from scripts.l2_pipeline.fs import X\nfrom helper import y\n")
            (l3 / "helper.py").write_text("def y():\n    from scripts.l2_pipeline.c64 import z\n")
            old = P.ROOT
            P.ROOT = root
            try:
                out = P.refresh_entry_seed("scripts/l3_meta/entry.py")
            finally:
                P.ROOT = old
            self.assertIn("scripts/l2_pipeline/fs.py", out)
            self.assertIn("scripts/l2_pipeline/c64.py", out)       # through the local L3 module, function-level import
            self.assertNotIn("scripts/l2_pipeline/unrelated.py", out)

    def test_real_entry_seed_has_forecast_slots(self):
        self.assertIn("scripts/l2_pipeline/forecast_slots.py", P.refresh_entry_seed())


if __name__ == "__main__":
    unittest.main()
