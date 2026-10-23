"""Independent count/provenance checks against the supplied author panel bytes."""
import csv
import hashlib
import json
import unittest
from collections import Counter
from pathlib import Path

from scripts.l3_pooled.dta_reader import rows

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data/pipeline/v2"


def read_csv(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


class ArtifactChecks(unittest.TestCase):
    def test_author_panel_counts_and_zero_semantics_from_raw_dta(self):
        reported = {r["scope"]: r for r in read_csv(OUT / "l3_pooled_counts.csv")}
        for bundle, scope in (("SPID", "author_spid_all"),
                              ("NBER", "author_nber_unmerged")):
            policies, pairs, counts = set(), set(), Counter()
            for row in rows(ROOT / f"data/l3_pooled/downloads/DVKim_{bundle}_iqt.dta",
                            {"policy_num", "state", "year", "adopt_year", "adopted"}):
                policies.add(row["policy_num"])
                pairs.add((row["policy_num"], row["state"]))
                counts["supplied_panel_rows"] += 1
                counts["events"] += row["adopt_year"] == row["year"]
                counts["at_risk_rows"] += (row["adopt_year"] is None
                                          or row["year"] <= row["adopt_year"])
                counts["convention_zero_rows"] += row["adopted"] == 0
                expected = int(row["adopt_year"] is not None
                               and row["year"] >= row["adopt_year"])
                self.assertEqual(row["adopted"], expected)
            self.assertEqual(int(reported[scope]["policies"]), len(policies))
            self.assertEqual(int(reported[scope]["state_policy_pairs"]), len(pairs))
            for field, count in counts.items():
                self.assertEqual(int(reported[scope][field]), count, (scope, field))

    def test_every_declared_input_matches_its_bytes(self):
        manifests = sorted(OUT.glob("*input_manifest*"))
        self.assertTrue(manifests)
        for path in manifests:
            if path.suffix != ".csv":
                continue
            for row in read_csv(path):
                source = ROOT / row["path"]
                self.assertEqual(source.stat().st_size, int(row["bytes"]))
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                                 row["sha256"], str(source))
        l2 = json.loads((OUT / "l2_summary.json").read_text())
        for relative, expected in l2["input_sha256"].items():
            self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(),
                             expected, relative)

    def test_pilot_queue_denominators_and_no_association_run(self):
        summary = json.loads((OUT / "summary.json").read_text())
        self.assertEqual(summary["l3"]["all_policy_count"], 10)
        self.assertEqual(summary["l3"]["blind_policy_count"], 9)
        self.assertEqual(summary["l3"]["real_association_fit_calls"], 0)
        self.assertEqual(summary["l3"]["association_tables"], 0)
        scopes = {r["scope"]: r for r in summary["l3"]["pooled_counts"]}
        self.assertEqual(scopes["pilot10"]["policies"], 10)
        self.assertEqual(scopes["fixed100"]["policies"], 100)
        manifest = json.loads((OUT / "output_manifest.json").read_text())
        self.assertEqual(manifest["new_public_downloads"], [])
        self.assertEqual(manifest["estimation_calls"], 0)
        self.assertEqual(manifest["outcome_association_calls"], 0)
        limits = {"PROTOCOL_V2_LEAN.md": 120, "PROTOCOL_V2_RESULTS.md": 40}
        for document in manifest["documents"]:
            path = ROOT / document["path"]
            self.assertLessEqual(document["lines"], limits[path.name])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             document["sha256"])


if __name__ == "__main__":
    unittest.main()
