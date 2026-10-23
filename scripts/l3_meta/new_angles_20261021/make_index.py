#!/usr/bin/env python3
"""T232: write RESULTS_NEW_ANGLES_A.json, an index of outputs with sha256 and headline numbers. EXPLORATORY."""
import json, hashlib, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data/l3_meta/new_angles_20261021"
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
J = lambda n: json.load(open(OUT / n))["result"]
q1, q2, q3, q5, alt = J("RESULTS_Q1.json"), J("RESULTS_Q2.json"), J("RESULTS_Q3.json"), J("RESULTS_Q5.json"), J("RESULTS_ALT_T227.json")
files = sorted(p for p in OUT.rglob("*") if p.is_file() and "states_decide" not in p.parts and p.name != "RESULTS_NEW_ANGLES_A.json")
idx = dict(rule_id="EX-1", task="T232", run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           plan=dict(path="docs/plans/l3-meta/PREPLAN_T232_20261021.md", sha256=sha(ROOT / "docs/plans/l3-meta/PREPLAN_T232_20261021.md")),
           code={p.name: sha(p) for p in sorted((ROOT / "scripts/l3_meta/new_angles_20261021").glob("*.py"))},
           headline=dict(
               q1=dict(n_valid=q1["n_designs_valid"], share_late_better=q1["primary"]["share_late_better"], median_D=q1["primary"]["median_D"], pattern=q1["pattern"]),
               q2=dict(n_labelled=q2["n_eligible"], counts=q2["overall_counts"], health_grow_pattern=q2["pattern_by_domain"]["health:grow"]["PATTERN"]),
               q3=dict(n_designs=q3["registered_slope"]["n_designs"], beta=q3["registered_slope"]["beta"], placebo=q3["registered_slope"]["placebo_slope_mean"],
                       excess_sd=q3["registered_slope"]["excess_in_null_sd"], pattern=q3["pattern"]),
               q5={k: dict(n_designs=q5[k]["primary"]["n_designs"], diff=q5[k]["primary"].get("pooled_diff"), band=q5[k]["primary"].get("null_band"), pattern=q5[k]["primary"].get("pattern")) for k in ("cut_A", "cut_B", "cut_C")},
               t227_alt=dict(q1_share=alt["q1"]["primary"]["share_late_better"], q2_counts=alt["q2"]["overall_counts"], q2_label_agreement=alt["q2"]["label_agreement"]["same_label"])),
           outputs={str(p.relative_to(OUT)): sha(p) for p in files})
(OUT / "RESULTS_NEW_ANGLES_A.json").write_text(json.dumps(idx, indent=1, default=float))
print(len(files), "files indexed")
