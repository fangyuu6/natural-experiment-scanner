#!/usr/bin/env python3
"""L3 meta, EXPLORATORY (rule id EX-1): read-only replay of the frozen L2 estimator on a frozen package (T175).

Rules: docs/plans/l3-meta/PREREG_AMEND_20261020_Q4_N6_INPUTS.md (written before any result below was computed).

What it does
  * Opens a frozen L2 design package (FREEZE_FINAL.json bound files) READ-ONLY through the L2 loaders, rebuilds the registered
    declaration, design and outcome panel, and re-evaluates the cell contrasts with the SAME formulas as
    scripts/l2_pipeline/engine/estimator.py (estimate_design), keeping the per-unit influence scores that L2 does not export.
  * Gate: overall ATT and every event-study ATT must equal the stored RESULT.json values (absolute 1e-8). A package that
    fails is dropped and listed, never repaired.
  * Derives from the influence scores: Var_mult(b) = sum_u psi_u^2 / n^2 for the overall ATT, the covariance of event-study
    coefficients cov(e, e') = sum_u psi_u(e) psi_u(e') / n^2, and (through L2's own `observed_fields`) s_pre, s_pre_n and H.
Nothing under scripts/l2_pipeline or data/l2_pipeline is written; no network; numpy only for linear algebra.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TOL_ATT = 1e-8


# ----------------------------------------------------------------------------------------------------------------------
# pure numerics (unit-tested on synthetic designs)
# ----------------------------------------------------------------------------------------------------------------------
def influence_table(units, cells, outcomes):
    """Cell contrasts and per-unit influence scores, mirroring engine/estimator.estimate_design.

    units: list of unit ids; cells: list of dicts with treated, controls, t, base_period, event_time, n_treated, g, post;
    outcomes: {(unit, period): value}.  Returns dict(n, units, cells, overall=(att, psi), events={e: dict}).
    """
    n = len(units)
    index = {u: i for i, u in enumerate(units)}
    records, infl = [], []
    for cell in cells:
        change = {u: outcomes[u, cell["t"]] - outcomes[u, cell["base_period"]] for u in list(cell["treated"]) + list(cell["controls"])}
        mg = float(np.mean([change[u] for u in cell["treated"]]))
        mc = float(np.mean([change[u] for u in cell["controls"]]))
        psi = np.zeros(n)
        for role, mean, sign in (("treated", mg, 1.0), ("controls", mc, -1.0)):
            members = cell[role]
            for u in members:
                psi[index[u]] = sign * n / len(members) * (change[u] - mean)
        records.append(dict(cell, att=mg - mc))
        infl.append(psi)

    def aggregate(selected):
        mass = np.array([records[j]["n_treated"] for j in selected], dtype=float)
        total = mass.sum()
        att = float(sum(w * records[j]["att"] for w, j in zip(mass, selected)) / total)
        psi = sum(w * infl[j] for w, j in zip(mass, selected)) / total
        for j in selected:
            for u in records[j]["treated"]:
                psi[index[u]] += n / total * (records[j]["att"] - att)
        return att, psi

    events = {}
    for e in sorted({r["event_time"] for r in records}):
        sel = [j for j, r in enumerate(records) if r["event_time"] == e]
        att, psi = aggregate(sel)
        events[e] = dict(att=att, psi=psi, n_treated=int(sum(records[j]["n_treated"] for j in sel)),
                         cohorts=sorted({records[j]["g"] for j in sel}))
    post = [j for j, r in enumerate(records) if r["post"]]
    overall = aggregate(post)
    return dict(n=n, units=list(units), cells=records, overall=overall, events=events)


def variance_from_psi(psi):
    """Unit-multiplier (influence function) variance of a statistic with influence vector psi: sum psi^2 / n^2."""
    psi = np.asarray(psi, dtype=float)
    return float(np.sum(psi * psi) / (len(psi) ** 2))


def covariance_from_psi(psis):
    """Covariance matrix of several statistics from their influence vectors (rows)."""
    m = np.asarray(psis, dtype=float)
    n = m.shape[1]
    return (m @ m.T) / (n * n)


def variance_from_ci(ci):
    """Variance implied by a symmetric 95% interval: ((hi - lo) / (2 * 1.96))^2; None when no interval."""
    if not ci or len(ci) != 2 or ci[0] is None or ci[1] is None:
        return None
    return ((float(ci[1]) - float(ci[0])) / (2 * 1.96)) ** 2


def joint_covariance(psi_by_design, units_by_design):
    """Cross-design covariance of overall ATTs when the unit multiplier is shared by state: C_ij = sum_s psi_i,s psi_j,s / (n_i n_j)."""
    ids = list(psi_by_design)
    c = np.zeros((len(ids), len(ids)))
    for a, i in enumerate(ids):
        ui = {u: k for k, u in enumerate(units_by_design[i])}
        for b, j in enumerate(ids):
            if b < a:
                c[a, b] = c[b, a]
                continue
            uj = {u: k for k, u in enumerate(units_by_design[j])}
            shared = set(ui) & set(uj)
            c[a, b] = sum(psi_by_design[i][ui[u]] * psi_by_design[j][uj[u]] for u in shared) / (len(ui) * len(uj))
    return ids, c


def lead_block(events, n_min=1):
    """Pre-event coefficients (event_time < 0) with their covariance matrix from the replayed influence scores."""
    times = [e for e in sorted(events) if e < 0 and events[e]["n_treated"] >= n_min]
    if not times:
        return dict(event_times=[], coef=[], n_treated=[], cov=[])
    psis = [events[e]["psi"] for e in times]
    cov = covariance_from_psi(psis)
    return dict(event_times=times, coef=[events[e]["att"] for e in times], n_treated=[events[e]["n_treated"] for e in times],
                cov=cov.tolist())


def gate_against_stored(replay, stored_overall_att, stored_events, tol=TOL_ATT):
    """Reproduction gate: overall and every stored event-study ATT must be reproduced (absolute tol scaled by max(1, |x|))."""
    diffs = []
    d0 = abs(replay["overall"][0] - stored_overall_att)
    diffs.append(d0 / max(1.0, abs(stored_overall_att)))
    stored_by_e = {r["event_time"]: r["att"] for r in stored_events}
    missing = sorted(set(stored_by_e) ^ set(replay["events"]))
    for e, att in stored_by_e.items():
        if e in replay["events"]:
            diffs.append(abs(replay["events"][e]["att"] - att) / max(1.0, abs(att)))
    worst = max(diffs) if diffs else float("inf")
    return dict(passed=bool(not missing and worst <= tol), max_scaled_diff=worst, overall_diff=d0, event_time_mismatch=missing,
                n_events_compared=len(diffs) - 1)


# ----------------------------------------------------------------------------------------------------------------------
# package loading (L2 loaders, read-only)
# ----------------------------------------------------------------------------------------------------------------------
def _import_l2():
    sys.dont_write_bytecode = True
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.l2_pipeline.contract_card import registered_execution_card
    from scripts.l2_pipeline.registered import load_registration
    from scripts.l2_pipeline.engine.panels import load_bound_panel
    from scripts.l2_pipeline.engine.design import build_design, structural_rows
    from scripts.l2_pipeline import l3_addendum
    return dict(card=registered_execution_card, load=load_registration, panel=load_bound_panel, build=build_design,
                structural=structural_rows, addendum=l3_addendum)


def load_package(pkg, l2=None):
    """Declaration, design and outcomes of the registered primary of a frozen package."""
    l2 = l2 or _import_l2()
    pkg = Path(pkg).resolve()
    manifest = json.loads((pkg / "FREEZE_FINAL.json").read_text())
    card = l2["card"](pkg, manifest)
    bundle = l2["load"](pkg, card, _draft_manifest=manifest)
    declaration = bundle["declarations"]["primary"]
    rows, identity = l2["panel"](bundle["bindings"]["primary"])
    design = l2["build"](declaration, rows)
    outcomes = {(u, t): y for u, t, y, _ in l2["structural"](declaration, rows)}
    return dict(declaration=declaration, design=design, outcomes=outcomes, identity=identity, bundle_l3=bundle.get("l3_export_v2"))


def observed_s_pre_h(loaded, l2=None):
    """s_pre, s_pre_n, H computed by L2's own `observed_fields` (same definition as the exporting designs)."""
    l2 = l2 or _import_l2()
    f = l2["addendum"].observed_fields(loaded["declaration"], loaded["design"], loaded["outcomes"], None)
    return dict(s_pre=f["s_pre"], s_pre_n=f["s_pre_n"], H=f["H"], balanced_status=f["balanced_profile"]["status"],
                balanced_event_times=f["balanced_profile"]["event_times"])


def finite_or_none(x):
    return x if isinstance(x, (int, float)) and math.isfinite(x) else None
