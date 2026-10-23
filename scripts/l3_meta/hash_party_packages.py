#!/usr/bin/env python3
"""Hash table of the L2 package files read by build_party_histories.py (read-only on data/l2_pipeline).

Writes data/l3_meta/party_coding/package_files_v2.csv (design_id, file, bytes, sha256) from design_summary_v2.json.
Run:  python3 -I -B scripts/l3_meta/hash_party_packages.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inputs_b_common as C  # noqa: E402

S = json.loads((C.ROOT / "data/l3_meta/party_coding/design_summary_v2.json").read_text())
rows = []
for s in S:
    for f in ("CARD.json", "TREATMENT.json", "CERTIFIED_HISTORY.json"):
        p = C.ROOT / s["package"] / f
        if p.exists():
            rows.append([s["design_id"], s["package"], f, p.stat().st_size, C.sha256_file(p)])
print(C.write_new(C.ROOT / "data/l3_meta/party_coding/package_files_v2.csv",
                  C.csv_bytes(["design_id", "package_dir", "file", "bytes", "sha256"], rows)))
