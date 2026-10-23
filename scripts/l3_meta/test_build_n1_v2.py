"""Checks of build_n1_v2.py: constants rebound to V2, build_n1.py itself untouched (pinned hash), V2 sets file = frozen V1 records + appended."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_n1 as B  # noqa: E402
import build_n1_v2 as V  # noqa: E402

ROOT = B.ROOT


class T(unittest.TestCase):
    def test_build_n1_unchanged(self):
        self.assertEqual(hashlib.sha256(Path(B.__file__).read_bytes()).hexdigest(), "d688c83bd22477e96da25257199e8fcfbd65e0978d8943432fb8cd826fe68c43")

    def test_sets_v2_keeps_v1_records_byte_identical(self):
        v1 = json.loads(V.FROZEN_V1_SETS.read_text())
        v2 = json.loads(V.V2["SETS"].read_text())
        self.assertEqual(v2["designs"][:len(v1["designs"])], v1["designs"])
        ids = [d["design_id"] for d in v2["designs"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(set(ids) & {s["design_id"] for s in v2["skipped"]})

    def test_split(self):
        rows = [{"design_id": "a", "code": "EVAL", "family": "f", "primary": {"Z": 1.0}, "S1": {"Z": 2.0}},
                {"design_id": "zz_new", "code": "EVAL", "family": "g", "primary": {"Z": 3.0}, "S1": {"Z": 4.0}},
                {"design_id": "x", "code": "SUPPORT", "family": "h"}]
        import tempfile
        frozen_first = json.loads(V.FROZEN_V1_SETS.read_text())["designs"][0]["design_id"]
        rows[0]["design_id"] = frozen_first
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.json"
            p.write_text(json.dumps({"designs": rows}))
            out = V.split(p)
        self.assertEqual((out["frozen_V1_sets"]["n"], out["appended_V2_sets"]["n"]), (1, 1))
        self.assertEqual(out["appended_V2_sets"]["primary"]["mean_Z"], 3.0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
