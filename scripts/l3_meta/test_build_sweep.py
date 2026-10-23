"""Checks of the bookkeeping of build_sweep.py on INVENTED files (no repo data file is read, nothing is written):
the hash record parser (record, supplements, later amendment records), the contradiction check, the pinning check of loaded L2 modules,
the write-once helper and the largest-first roster key.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/test_build_sweep.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))

import build_sweep as B  # noqa: E402
import l3_pins  # noqa: E402
import sweep_core as S  # noqa: E402

H1, H2, H3 = "a" * 64, "b" * 64, "c" * 64


def record(path, amend_file, amend_sha, listed):
    lines = ["header", f"file: {amend_file}", f"sha256: {amend_sha}", "bytes: 1", "amended_utc (T_amend): 2026-10-15T00:00:00Z", "inputs:"]
    lines += [f"  {p} sha256 {h}" for p, h in listed.items()]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def real_record():
    """The real record as the refresh reads it: supplements may rebind L2 modules (l3_pins); the plain parser rejects a rebound line by design."""
    return l3_pins.merged_record(B._parse_one, l3_pins.record_paths(B.HASH_REC))


class Records(unittest.TestCase):
    def test_parse_merge_and_contradiction(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            main = tmp / "PREREG_AMEND_X_SWEEP_HASH.txt"
            record(main, "docs/a.md", H1, {"scripts/l2_pipeline/x.py": H2, "data/some.json": H3})
            rec = B.parse_hash_record(main)
            self.assertEqual(rec["files"], {"docs/a.md": H1})
            self.assertEqual(rec["listed"]["scripts/l2_pipeline/x.py"], H2)
            self.assertEqual(rec["t_amend_utc"], "2026-10-15T00:00:00Z")
            record(tmp / "PREREG_AMEND_X_SWEEP_HASH_S1.txt", "docs/a.md", H1, {"scripts/l2_pipeline/y.py": H3})
            record(tmp / "PREREG_AMEND_X_SWEEP_HASH_B1.txt", "docs/b.md", H2, {"data/other.json": H1})
            rec = B.parse_hash_record(main)
            self.assertEqual(rec["supplements"], ["PREREG_AMEND_X_SWEEP_HASH_B1.txt", "PREREG_AMEND_X_SWEEP_HASH_S1.txt"])
            self.assertEqual(set(rec["files"]), {"docs/a.md", "docs/b.md"})
            self.assertEqual(set(rec["listed"]), {"scripts/l2_pipeline/x.py", "data/some.json", "scripts/l2_pipeline/y.py", "data/other.json"})
            record(tmp / "PREREG_AMEND_X_SWEEP_HASH_S2.txt", "docs/a.md", H1, {"scripts/l2_pipeline/x.py": H1})      # contradicts the main record
            with self.assertRaises(RuntimeError):
                B.parse_hash_record(main)

    def test_real_record_is_consistent(self):
        rec = real_record()
        self.assertIn("PREREG_AMEND_20261015_SWEEP_V1.md", {Path(p).name for p in rec["files"]})
        self.assertIn("PREREG_AMEND_20261015_SWEEP_V1B.md", {Path(p).name for p in rec["files"]})
        self.assertEqual(B.verify_records(rec), [])
        l2 = [p for p in rec["listed"] if p.startswith("scripts/l2_pipeline/")]
        self.assertGreaterEqual(len(l2), 80)

    def test_pinning_flags_unlisted_and_changed_modules(self):
        S.load_l2()
        loaded = S.l2_files()
        rec = real_record()
        self.assertEqual(B.verify_l2(rec), [])
        fake = {"files": {}, "listed": {k: v for k, v in rec["listed"].items() if k != next(iter(loaded))}}
        self.assertTrue(any("not in the hash record" in p for p in B.verify_l2(fake)))
        first = next(iter(loaded))
        fake2 = {"files": {}, "listed": dict(rec["listed"], **{first: "0" * 64})}
        self.assertTrue(any("differs from the hash record" in p for p in B.verify_l2(fake2)))


class Helpers(unittest.TestCase):
    def test_write_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sub" / "x.json"
            B.write_once_json(p, {"a": float("nan"), "b": [1, 2]})
            self.assertEqual(json.loads(p.read_text()), {"a": None, "b": [1, 2]})
            with self.assertRaises(SystemExit):
                B.write_once_json(p, {})

    def test_policy_family_default(self):
        self.assertEqual(B.policy_family({"design_id": "d", "policy_family": None}), "d")
        self.assertEqual(B.policy_family({"design_id": "d", "policy_family": "f"}), "f")

    def test_amend_hashes_cover_both_amendments(self):
        self.assertEqual(set(B.amend_hashes()), {"PREREG_AMEND_20261015_SWEEP_V1.md", "PREREG_AMEND_20261015_SWEEP_V1B.md"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
