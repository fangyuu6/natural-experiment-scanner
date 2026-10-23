"""Stage "d3runs" of the outcome sweep (amendment SWEEP_V1 7.2 as read with amendment B): the total series of the D3 pairs, estimated with the operators of the
target's own registered entry (its declaration, its mask and the engine's own label bundle), so that target and total have the same cells, the same
label assignments and the same estimator; c = t - a is then the complement.

Called by build_sweep.py --stage d3runs.  READ-ONLY on every L2 file.  Output: data/l3_meta/sweep_v1/D3_RUNS.json (written once).
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np

import build_d3 as D3
import sweep_core as S


def run_design(did, folder, result_path, store, primary_ids, scale_mode):
    """Candidate pairs of one design and the total on the target's mask for each.  `store` is a scale view of the series store."""
    stored_all = S.read_json(result_path)
    outs = D3.outcomes_of(stored_all, primary_ids)
    cand = [p for p in D3.PAIR_TABLE if p[1] in outs]
    out = dict(design_id=did, scale_mode=scale_mode, pairs=[])
    if not cand:
        return out
    ctx = S.load_design_context(did, folder)
    for pid, a_id, t_id, label in cand:
        a_name, A = outs[a_id]
        rec = dict(pair=pid, target_series=a_id, total_series=t_id, substitute=label, target_entry=a_name, code=None, reasons=[])
        e = ctx.entries[a_name]
        if e.status != "ready":
            rec.update(code="OTHER", reasons=[f"target entry not rebuilt: {e.status}"])
            out["pairs"].append(rec)
            continue
        ops = S.build_ops(e.declaration, e.design, S.own_bundles(e.design), with_cells=True)      # the gate's operators: the engine's own bundle for A's mask
        rows, outcomes = S.panel_outcomes(e)
        keys = ops.keys
        ya = np.array([outcomes[k] for k in keys])
        a, da = S.project(ops, ya)
        nf = A["noise_floor"]
        rec.update(n_mask_cells=len(keys), n_feasible=int(ops.feasibility["feasible_draws"]), a=a, a_stored=A["result"]["overall"]["att"],
                   a_draws_vs_stored=S.max_scaled_diff(da[ops.draw_index], nf["permutation_draws"]) if len(ops.draw_index) == len(nf["permutation_draws"]) else None)
        if t_id not in store.sids:
            rec.update(code="NO_TOTAL_SERIES")
            out["pairs"].append(rec)
            continue
        vt = store.values(t_id, keys, "val")
        okt = store.values(t_id, keys, "ok").astype(bool)
        rec["n_total_missing_cells"] = int((~okt).sum())
        if not okt.all():
            rec.update(code="CELL_SETS_DIFFER", reasons=[f"CELL_SETS_DIFFER: the total lacks {int((~okt).sum())} of {len(keys)} cells of the target's mask"])
            out["pairs"].append(rec)
            continue
        t, dt = S.project(ops, vt)
        ct = S.cell_atts(ops, vt)
        rec.update(t=t, dt=dt[ops.draw_index].tolist(), draw_index=list(ops.draw_index), t_cell_atts=ct.tolist(), t_values_checked=True,
                   keys=[list(k) for k in keys])
        # the total is also a registered entry of the design on the same structure: the stored estimate must be reproduced
        t_name = ("primary" if S.entry_series_id(ctx, "primary") == t_id else "secondary_" + t_id)
        st = stored_all["registered_results"].get(t_name)
        if st and st.get("status") == "ESTIMATED" and (st.get("noise_floor") or {}).get("permutation_draws") and t_name in ctx.entries and ctx.entries[t_name].status == "ready":
            same = [(c["g"], c["t"], c["base_period"], c["n_treated"], c["n_control"], bool(c["post"])) for c in st["result"]["group_time"]] == \
                   [(c["g"], c["t"], c["base_period"], c["n_treated"], c["n_control"], bool(c["post"])) for c in e.design["cells"]]
            sd_ = st["noise_floor"]["permutation_draws"]
            rec["stored_total"] = dict(entry=t_name, same_cells=bool(same), att_diff=abs(t - st["result"]["overall"]["att"]) / max(1.0, abs(t), abs(st["result"]["overall"]["att"])),
                                       draws_max_diff=S.max_scaled_diff(dt[ops.draw_index], sd_) if (same and len(sd_) == len(ops.draw_index)) else None)
        out["pairs"].append(rec)
    return out
