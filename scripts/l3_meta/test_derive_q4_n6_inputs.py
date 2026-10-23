import sys, unittest
from pathlib import Path
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import derive_q4_n6_inputs as D


class T(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(D.clean({"a": float("nan"), "b": [1.0, float("inf")]}), {"a": None, "b": [1.0, None]})

    def test_family_count_short(self):
        rows = [dict(design_id="x", status="DROPPED_GATE", policy_family="f", H=4, s_pre=1.0)]
        q = D.q4_family_count(rows)
        self.assertEqual(q["families_with_party_history_both_groups_ge20"], 0)
        self.assertEqual(q["short_by"], 20)

    def test_amendment_hash_matches(self):
        want = [l.split()[1] for l in D.AMEND_HASH.read_text().splitlines() if l.startswith("sha256:")][0]
        self.assertEqual(D.sha256_file(D.AMEND), want)

    def test_output_is_write_once(self):
        self.assertEqual(D.main(["--out-dir", str(D.OUT_DIR)]), 2)


if __name__ == "__main__":
    unittest.main()
