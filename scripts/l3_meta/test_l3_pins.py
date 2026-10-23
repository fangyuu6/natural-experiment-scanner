"""Tests of the rebound rule (l3_pins.py), its use in refresh_all.check_group, and the extension builders' pure parts."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import l3_pins as P  # noqa: E402
import refresh_all as R  # noqa: E402

H = lambda b: hashlib.sha256(b).hexdigest()


def rec(sections, amend="docs/a.md", sha="a" * 64):
    out = [f"file: {amend}", f"sha256: {sha}", "amended_utc (T_amend): 2026-10-16T00:00:00Z"]
    for head, lines in sections:
        out.append(head + ":")
        out += [f"  {p} sha256 {h}" for p, h in lines]
    return "\n".join(out) + "\n"


class Pins(unittest.TestCase):
    def test_rebound_lines_only_l2(self):
        t = rec([("L2 modules rebound (reason)", [("scripts/l2_pipeline/x.py", "b" * 64)])])
        self.assertEqual(dict(P.rebound_lines(t)), {"scripts/l2_pipeline/x.py": "b" * 64})
        bad = rec([("L2 modules rebound", [("scripts/l3_meta/x.py", "b" * 64)])])
        with self.assertRaises(RuntimeError):
            P.rebound_lines(bad)

    def test_effective_l2(self):
        t1 = rec([("L2 modules (82)", [("scripts/l2_pipeline/x.py", "1" * 64), ("scripts/l2_pipeline/y.py", "2" * 64)])])
        t2 = rec([("L2 modules added", [("scripts/l2_pipeline/z.py", "3" * 64)])])
        t3 = rec([("L2 modules rebound", [("scripts/l2_pipeline/x.py", "9" * 64)])])
        eff, owner, over = P.effective_l2([("r", t1), ("s1", t2), ("s2", t3)])
        self.assertEqual(eff["scripts/l2_pipeline/x.py"], "9" * 64)
        self.assertEqual(owner["scripts/l2_pipeline/x.py"], "s2")
        self.assertEqual(over, {"scripts/l2_pipeline/x.py": ["r"]})
        t4 = rec([("L2 modules added", [("scripts/l2_pipeline/y.py", "7" * 64)])])
        with self.assertRaises(RuntimeError):            # a plain clash is still an error
            P.effective_l2([("r", t1), ("s", t4)])

    def test_natural_order(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("A_HASH.txt", "A_HASH_S1.txt", "A_HASH_S2.txt", "A_HASH_S10.txt", "A_HASH_B1.txt"):
                (Path(d) / n).write_text("x")
            self.assertEqual([p.name for p in P.record_paths(Path(d) / "A_HASH.txt")],
                             ["A_HASH.txt", "A_HASH_B1.txt", "A_HASH_S1.txt", "A_HASH_S2.txt", "A_HASH_S10.txt"])

    def test_check_group_follows_rebound(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs").mkdir()
            (root / "scripts/l2_pipeline").mkdir(parents=True)
            mod = root / "scripts/l2_pipeline/x.py"
            mod.write_bytes(b"v1")
            (root / "docs/a.md").write_bytes(b"amend")
            main = root / "docs/R_HASH.txt"
            main.write_text(rec([("L2 modules (1)", [("scripts/l2_pipeline/x.py", H(b"v1"))])], "docs/a.md", H(b"amend")))
            g = R.ConvGroup("t", "docs/R_HASH.txt", inputs=False)
            self.assertEqual(R.check_group(root, g)[0], [])
            mod.write_bytes(b"v2")
            self.assertTrue(R.check_group(root, g)[0])        # drift is refused ...
            (root / "docs/R_HASH_S2.txt").write_text(rec([("L2 modules rebound (window closed)", [("scripts/l2_pipeline/x.py", H(b"v2"))])], "docs/a.md", H(b"amend")))
            problems, summ = R.check_group(root, g)
            self.assertEqual(problems, [])                    # ... until a rebound line records the new bytes
            self.assertEqual(summ["l2_rebound"], ["scripts/l2_pipeline/x.py"])
            mod.write_bytes(b"v3")
            self.assertTrue(R.check_group(root, g)[0])        # and drift after the rebound is refused again

    def test_install_merges_with_rebound(self):
        class Fake:
            HASH_REC = None

            @staticmethod
            def _parse_one(path):
                import build_sweep
                return build_sweep._parse_one(path)

            @staticmethod
            def parse_hash_record(path=None):
                raise AssertionError("replaced")
        with tempfile.TemporaryDirectory() as d:
            main = Path(d) / "R_HASH.txt"
            main.write_text(rec([("L2 modules (1)", [("scripts/l2_pipeline/x.py", "1" * 64)])]))
            (Path(d) / "R_HASH_S2.txt").write_text(rec([("L2 modules rebound", [("scripts/l2_pipeline/x.py", "2" * 64)])], "docs/b.md", "b" * 64))
            Fake.HASH_REC = main
            undo = P.install(Fake)
            r = Fake.parse_hash_record()
            self.assertEqual(r["listed"]["scripts/l2_pipeline/x.py"], "2" * 64)
            self.assertEqual(r["supplements"], ["R_HASH_S2.txt"])
            undo()
            with self.assertRaises(AssertionError):
                Fake.parse_hash_record()


class ExtParts(unittest.TestCase):
    def test_ext_series_table(self):
        import build_sweep_ext as X
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.csv"
            p.write_text("outcome_key,family,ext_in_store,ext_status,frequency\na,mortality,1,READY,annual\nb,employment,0,PARKED,annual\n")
            self.assertEqual(list(X.ext_series_table(p)), ["a"])

    def test_union_loader(self):
        import build_d1_ext as U
        with tempfile.TemporaryDirectory() as d:
            v1, ex = Path(d) / "v1", Path(d) / "ex"
            v1.mkdir(), ex.mkdir()

            def fake(path):
                n = Path(path).parent.name
                ids = ["s1", "s2"] if n == "v1" else ["e1"]
                return dict(sids=ids, fam=["f"] * len(ids), code=["SUPPORTED"] * len(ids), att=np.ones(len(ids)), sd=np.ones(len(ids)),
                            placebo=np.zeros((len(ids), 5)), meta={}, z=None)
            load = U.union_loader(fake)
            U._STATE["ext_dir"] = None
            self.assertEqual(load(v1 / "x.npz")["sids"], ["s1", "s2"])
            U._STATE["ext_dir"] = ex
            with self.assertRaises(RuntimeError):
                load(v1 / "x.npz")                       # extension file missing
            (ex / "x.npz").write_text("")
            f = load(v1 / "x.npz")
            self.assertEqual(f["sids"], ["s1", "s2", "e1"])
            self.assertEqual(f["placebo"].shape, (3, 5))
            U._STATE["ext_dir"] = None


if __name__ == "__main__":
    unittest.main()
