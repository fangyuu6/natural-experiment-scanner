"""min-gate viability score: the binding constraint, not the average.

The additive composite score lets high importance offset low feasibility
(a policy scored 5/5/5/1 nets the same composite as 4/4/4/4). The loop found
the binding constraint in practice is min(), not mean(): the top-40 policies
ranked by composite score yielded 0 survivors under adversarial review. This
module exposes the four LLM sub-scores' minimum as an explicit gate.

Sub-score keys, as found in `detail_json` (verified against 2000 sampled
rows of data/output/policy_db_20260415_483k.csv on 2026-07-10: 0 missing
keys, 0 non-numeric values, 0 bad JSON): policy_significance, identification,
treatment_intensity, data_feasibility. Each is stored as a numeric string,
e.g. "3.0".
"""

_KEYS = ("policy_significance", "identification", "treatment_intensity", "data_feasibility")


def compute_min_gate(subscores):
    vals = []
    for k in _KEYS:
        v = subscores.get(k)
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            return None
    return min(vals) if len(vals) == len(_KEYS) else None
