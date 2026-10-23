"""L3 selection-aware reference, library (rule id EX-1, exploratory; version SELREF-V1).

Rules: docs/plans/l3-meta/PREREG_AMEND_20261016_SELREF_V1.md (hash record PREREG_AMEND_20261016_SELREF_HASH.txt), written before any statistic under a
block-restricted reassignment existed.  Sections 2.4 and 2.5 of that text are implemented here as pure functions; section 2.1 (what is reassigned) is the
sweep's: the label of a unit is (adoption period, operative offset, exposure path, whole-window-clean flag) of the registered primary design and draw b gives
unit j the label of unit perm_b[j].  Only the permutations change; the operators, the series store, the cell patterns and the support rules are those of
sweep_core (imported, read-only; nothing is written under scripts/l2_pipeline or data/l2_pipeline).

Pieces: covariate table, reference blocks (U, P5, P3, P8, PC5, REG, N5), permutations, StratifiedLabels (subclass of sweep_core.AlignedLabels), the sweep of
one design under a reference (the structure pass of sweep_core.sweep_design with the label bundles replaced), the Q1 primary reference.
"""
from __future__ import annotations

import csv
import math
import random
import sys
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import sweep_core as S  # noqa: E402

ROOT = S.ROOT
RULE_ID, QUESTION, VERSION = "EX-1", "SELREF", "SELREF-V1"
N_DRAWS = S.N_DRAWS
RI_SEED = S.RI_SEED
COV_PATH = ROOT / "data/l3_meta/covariates/state_year_v2.csv"
COV_COLUMNS = ("dem_unified_jan1", "citizen_ideology", "ln_pcpi_real_2024usd", "urban_share")
MIN_BLOCK = 5                      # amendment 2.4 (f): a block holds at least 5 units
RIDGE = 1.0                        # amendment 2.4 (e): ridge penalty on every slope
REFERENCES = OrderedDict([
    ("U", dict(kind="uniform", K=1)),
    ("P5", dict(kind="timing", K=5)),
    ("P3", dict(kind="timing", K=3)),
    ("P8", dict(kind="timing", K=8)),
    ("PC5", dict(kind="pc1", K=5)),
    ("REG", dict(kind="region", K=4)),
    ("N5", dict(kind="noise", K=5)),
])
NEW_REFERENCES = tuple(k for k in REFERENCES if k != "U")
PRIMARY = "P5"
REGION_ORDER = ("Northeast", "Midwest", "South", "West")
REGION = {}
for _r, _s in (("Northeast", "CT ME MA NH RI VT NJ NY PA"), ("Midwest", "IL IN MI OH WI IA KS MN MO NE ND SD"),
               ("South", "DE DC FL GA MD NC SC VA WV AL KY MS TN AR LA OK TX"), ("West", "AZ CO ID MT NV NM UT WY AK CA HI OR WA")):
    for _st in _s.split():
        REGION[_st] = _r
assert len(REGION) == 51, "the Census region table must hold 50 states and DC"


# ---------------------------------------------------------------------------------------------
# covariates (amendment 2.4 (c))
# ---------------------------------------------------------------------------------------------
def load_covariate_table(path=COV_PATH):
    """{(state, year): tuple of the four N1 covariates, a blank cell is None}."""
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[(r["state"], int(r["year"]))] = tuple(None if r[c] in ("", None) else float(r[c]) for c in COV_COLUMNS)
    return out


# ---------------------------------------------------------------------------------------------
# small numeric pieces (amendment 2.4 (b), (d), (e), (f))
# ---------------------------------------------------------------------------------------------
def lower_median(values):
    """v[(m - 1) // 2] of the sorted list v (the lower of the two middle values)."""
    v = sorted(values)
    if not v:
        raise ValueError("no value for the lower median")
    return v[(len(v) - 1) // 2]


def average_ranks(keys):
    """1-based ranks of `keys` (ascending); ties share the mean of the ranks they span.  math.inf can stand for 'never'."""
    order = sorted(range(len(keys)), key=lambda i: keys[i])
    ranks = [0.0] * len(keys)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and keys[order[j + 1]] == keys[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def standardise(X):
    """Columns centred and divided by the sample SD (ddof = 1); a column with SD 0 is dropped.  Returns (Z, kept column indices)."""
    X = np.asarray(X, dtype=float)
    if X.ndim != 2 or X.shape[0] < 2:
        raise ValueError("standardisation needs at least two rows")
    mean = X.mean(axis=0)
    sd = X.std(axis=0, ddof=1)
    kept = [c for c in range(X.shape[1]) if sd[c] > 0]
    return (X[:, kept] - mean[kept]) / sd[kept], kept


def ridge_index(Z, s, lam=RIDGE):
    """beta = (Z'Z + lam I)^(-1) Z'(s - mean s); index = Z beta."""
    Z = np.asarray(Z, dtype=float)
    sc = np.asarray(s, dtype=float) - float(np.mean(s))
    beta = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ sc)
    return Z @ beta, beta


def pc1_index(Z):
    """Score on the eigenvector of Z'Z/(n - 1) of the largest eigenvalue (the sign is immaterial)."""
    Z = np.asarray(Z, dtype=float)
    w, V = np.linalg.eigh(Z.T @ Z / (Z.shape[0] - 1))
    return Z @ V[:, -1]


def noise_rows(design_id, n_free):
    """n_free x 4 independent standard normal numbers (amendment 2.4 (e), reference N5), row by row."""
    rng = random.Random(f"SELREF-V1|NOISE-X|{design_id}")
    return [[rng.gauss(0.0, 1.0) for _ in range(4)] for _ in range(n_free)]


def split_sorted(order, K_eff):
    """Consecutive groups of `order` of sizes floor(n/K_eff), the first (n mod K_eff) one larger."""
    n = len(order)
    base, extra = divmod(n, K_eff)
    out, pos = [], 0
    for k in range(K_eff):
        size = base + (1 if k < extra else 0)
        out.append(list(order[pos:pos + size]))
        pos += size
    return out


def effective_blocks(K, n_free):
    return max(1, min(K, n_free // MIN_BLOCK))


# ---------------------------------------------------------------------------------------------
# reference blocks (amendment 2.4)
# ---------------------------------------------------------------------------------------------
def seed_for(reference_id, design_id):
    return RI_SEED if reference_id == "U" else f"SELREF-V1|{reference_id}|{design_id}"


def build_reference(reference_id, design_id, units, adoption, ystar, cov):
    """Blocks of one reference for one design.

    units: sorted state codes of the registered primary design (U0); adoption: adoption period of each unit or None; ystar: reference year; cov: the table of
    load_covariate_table().  Returns dict(id, design_id, seed, n, n_free, fixed (positions), blocks (ascending position lists, in block order), K_eff, ystar, index)."""
    if reference_id not in REFERENCES:
        raise ValueError(f"unknown reference {reference_id}")
    n = len(units)
    spec = REFERENCES[reference_id]
    base = dict(id=reference_id, design_id=design_id, seed=seed_for(reference_id, design_id), n=n, ystar=ystar, index=None)
    if spec["kind"] == "uniform":
        return dict(base, n_free=n, fixed=[], blocks=[list(range(n))], K_eff=1)
    rows = [cov.get((u, ystar)) for u in units]
    free = [i for i, r in enumerate(rows) if r is not None and all(v is not None for v in r)]
    fset = set(free)
    fixed = [i for i in range(n) if i not in fset]
    names = [units[i] for i in free]
    n_free = len(free)
    base.update(n_free=n_free, fixed=fixed)
    if spec["kind"] == "region":
        groups = OrderedDict((r, []) for r in REGION_ORDER)
        for i in free:
            groups[REGION[units[i]]].append(i)
        blocks = [g for g in groups.values() if g]
        return dict(base, blocks=blocks, K_eff=len(blocks))
    Z, kept = standardise([rows[i] for i in free])
    if Z.shape[1] == 0:
        raise ValueError(f"{design_id}: every covariate is constant over the free units in {ystar}")
    K_eff = effective_blocks(spec["K"], n_free)
    if spec["kind"] in ("timing", "noise"):
        keys = [math.inf if adoption[i] is None else adoption[i] for i in free]
        s = average_ranks(keys)
        Zuse = standardise(noise_rows(design_id, n_free))[0] if spec["kind"] == "noise" else Z
        if max(s) == min(s):
            idx = pc1_index(Zuse)
        else:
            idx, _ = ridge_index(Zuse, s)
    else:
        idx = pc1_index(Z)
    order = sorted(range(n_free), key=lambda j: (round(float(idx[j]), 9), names[j]))
    blocks = [sorted(free[j] for j in grp) for grp in split_sorted(order, K_eff)]
    return dict(base, blocks=blocks, K_eff=K_eff, index={names[j]: float(idx[j]) for j in range(n_free)})


def design_inputs(ctx):
    """(units, adoption list, reference year) of a loaded design context: amendment 2.4 (a) and (b)."""
    des, dec = ctx.primary.design, ctx.primary.declaration
    py = dec.get("period_years")
    units = list(des["units"])
    adoption = [des["adoption"][u] for u in units]
    cal = (lambda t: py[t]) if py is not None else (lambda t: t)
    onset = [cal(des["operative"][u]) for u in units if des["adoption"][u] is not None]
    return units, adoption, lower_median(onset)


# ---------------------------------------------------------------------------------------------
# permutations (amendment 2.5)
# ---------------------------------------------------------------------------------------------
def make_perms(blocks, n, n_draws, seed):
    """n_draws permutations of range(n): block by block, `sh = list(block); rng.shuffle(sh); perm[block[i]] = sh[i]`; positions outside every block keep perm[j] = j.
    With one block holding range(n) and the engine's seed this is the shuffle loop of sweep_core.AlignedLabels."""
    rng = random.Random(seed)
    out = []
    for _ in range(n_draws):
        perm = list(range(n))
        for block in blocks:
            sh = list(block)
            rng.shuffle(sh)
            for p, q in zip(block, sh):
                perm[p] = q
        out.append(perm)
    return out


def perm_diagnostics(perms, adoption):
    """Mean number of units whose label (adoption period) differs from the observed, mean number of truly treated units that carry no treatment, and the
    share of draws that reproduce the observed adoption pattern exactly."""
    n = len(adoption)
    moved = swapped = same = 0
    for perm in perms:
        new = [adoption[perm[j]] for j in range(n)]
        moved += sum(1 for j in range(n) if new[j] != adoption[j])
        swapped += sum(1 for j in range(n) if adoption[j] is not None and new[j] is None)
        same += int(all(new[j] == adoption[j] for j in range(n)))
    d = float(len(perms))
    return dict(mean_moved=moved / d, mean_swapped_out=swapped / d, share_identical_to_observed=same / d)


def reference_summary(ref, units, adoption):
    """Block structure for the files: units per block, treated count per block, fixed units, index values."""
    blocks = [dict(units=[units[i] for i in b], treated=sum(1 for i in b if adoption[i] is not None)) for b in ref["blocks"]]
    return dict(id=ref["id"], seed=ref["seed"], n=ref["n"], n_free=ref["n_free"], K_eff=ref["K_eff"], ystar=ref["ystar"],
                fixed_units=[units[i] for i in ref["fixed"]], blocks=blocks,
                index=None if ref["index"] is None else {k: round(v, 9) for k, v in ref["index"].items()})


class StratifiedLabels(S.AlignedLabels):
    """sweep_core.AlignedLabels with the permutations of a reference: labels0 and bundles() are inherited unchanged; only perms differ."""

    def __init__(self, declaration0, design0, years, blocks, seed, n_draws=N_DRAWS):
        super().__init__(declaration0, design0, years, n_draws=0)         # builds labels0 and checks the exposure; draws no permutation
        self.blocks = [list(b) for b in blocks]
        self.seed = seed
        self.perms = make_perms(self.blocks, len(self.units0), n_draws, seed)


def primary_labels(design):
    """The label tuples of engine.ri.predraw_labels for the primary design itself."""
    return [(design["adoption"][u], None if design["adoption"][u] is None else design["operative"][u] - design["adoption"][u],
             design["exposure"][u], design["whole_window_clean"][u]) for u in design["units"]]


def primary_bundles(design, blocks, seed, n_draws=N_DRAWS):
    """Label bundles of the primary design's own cell pattern under a reference (a generator)."""
    labels = primary_labels(design)
    n = len(labels)
    for perm in make_perms(blocks, n, n_draws, seed):
        yield [labels[perm[p]] for p in range(n)]


# ---------------------------------------------------------------------------------------------
# the sweep of one design under a reference (structure pass of sweep_core.sweep_design, label bundles replaced)
# ---------------------------------------------------------------------------------------------
def sweep_design_ref(ctx, store, series_table, ref, check_engine_bundle=False):
    """Same records as sweep_core.sweep_design.  check_engine_bundle (reference U only): the aligned bundle of the primary must equal engine.ri.predraw_labels."""
    prim = ctx.primary
    b = S.Builder(ctx)
    U0 = list(prim.design["units"])
    U0s = set(U0)
    years = b.years
    T0 = S.treated_units(prim.design)
    al = StratifiedLabels(prim.declaration, prim.design, years, ref["blocks"], ref["seed"])
    own_ok = None
    if check_engine_bundle:
        own_ok = S.aligned_equals_engine(al, prim.design)
        if not own_ok:
            raise RuntimeError("ALIGNMENT_FAIL: the uniform bundle of the primary differs from engine.ri.predraw_labels")
    recs = OrderedDict()
    groups = OrderedDict()
    for sid in sorted(series_table):
        rec = dict(sid=sid, family=series_table[sid]["family"], code=None, detail="", att=float("nan"), placebo=np.full(N_DRAWS, np.nan),
                   n_cells=0, n_units=0, fingerprint="", kept=float("nan"), n_feasible=0, support_pass_l2=None, strict_dropped=0)
        recs[sid] = rec
        elig = store.eligible_keys(sid, U0s, years, "ok")
        sem = store.eligible_keys(sid, U0s, years, "sem")
        rec["strict_dropped"] = len(sem - elig)
        if not elig:
            rec["code"] = "NO_CELLS"
            continue
        try:
            dec, keys, design, summ = b.build(elig, sid)
        except ValueError as exc:
            rec["code"], rec["detail"] = "NO_DESIGN", str(exc)[:200]
            continue
        rec["support_pass_l2"] = bool(summ.get("support_pass", summ.get("passed")))
        if any(u not in U0s for u in design["units"]):
            raise RuntimeError("ALIGNMENT_FAIL: a pattern has a unit outside the primary's unit list")
        rec["n_cells"], rec["n_units"] = len(design["mask"]), len(design["units"])
        if not any(c["post"] for c in design["cells"]):
            rec["code"], rec["detail"] = "NO_DESIGN", "no post cell"
            continue
        if not S.family_of(design["cells"]):
            rec["code"] = "NO_PRE_FAMILY"
            continue
        rec["kept"] = len(S.treated_units(design) & T0) / len(T0)
        if rec["kept"] < S.MIN_KEPT_TREATED:
            rec["code"] = "KEPT_LT_80"
            continue
        y = store.values(sid, sorted(design["mask"]), "val")
        if not np.all(np.isfinite(y)):
            raise RuntimeError(f"non-finite value on the mask of {sid}")
        if float(np.max(y) - np.min(y)) == 0.0:
            rec["code"] = "CONSTANT"
            continue
        fp = S.mask_fingerprint(design)
        rec["fingerprint"] = fp
        g = groups.setdefault(fp, dict(dec=dec, design=design, sids=[]))
        g["sids"].append(sid)
    patterns = OrderedDict()
    for fp, g in groups.items():
        ops = S.build_ops(g["dec"], g["design"], al.bundles(g["design"]))
        patterns[fp] = dict(n_cells=len(g["design"]["mask"]), n_units=len(g["design"]["units"]), n_post_cells=sum(1 for c in g["design"]["cells"] if c["post"]),
                            feasible=ops.feasibility["feasible_draws"], support_pass=bool(ops.feasibility["support_pass"]), sids=list(g["sids"]),
                            infeasible_reasons=dict(Counter(x["reason"][:60] for x in ops.feasibility["infeasible_draws"])))
        for sid in g["sids"]:
            rec = recs[sid]
            y = store.values(sid, ops.keys, "val")
            att, draws = S.project(ops, y)
            rec["att"], rec["placebo"] = att, draws
            rec["n_feasible"] = int(ops.feasibility["feasible_draws"])
            rec["code"] = "SUPPORTED" if ops.feasibility["support_pass"] else "RI_FEASIBLE_LT_90"
    return dict(records=recs, patterns=patterns, units=U0, treated_primary=sorted(T0), years=list(years), aligned_equals_engine=own_ok)


def design_arrays_from_records(res):
    """The arrays of a design file (the schema of build_sweep.sweep_task) from the records of sweep_design_ref."""
    recs = res["records"]
    sids = list(recs)
    placebo = np.vstack([recs[k]["placebo"] for k in sids])
    stats = [S.draw_statistics(recs[k]["att"], recs[k]["placebo"]) if recs[k]["code"] in ("SUPPORTED", "RI_FEASIBLE_LT_90") else
             dict(sd=float("nan"), cutoff=float("nan"), p2=float("nan"), z=float("nan")) for k in sids]
    return dict(
        series_ids=np.array(sids), family=np.array([recs[k]["family"] for k in sids]), code=np.array([recs[k]["code"] for k in sids]),
        detail=np.array([recs[k]["detail"] for k in sids]), fingerprint=np.array([recs[k]["fingerprint"] for k in sids]),
        n_cells=np.array([recs[k]["n_cells"] for k in sids], dtype=np.int64), n_units=np.array([recs[k]["n_units"] for k in sids], dtype=np.int64),
        kept=np.array([recs[k]["kept"] for k in sids], dtype=float), n_feasible=np.array([recs[k]["n_feasible"] for k in sids], dtype=np.int64),
        support_pass_l2=np.array([-1 if recs[k]["support_pass_l2"] is None else int(recs[k]["support_pass_l2"]) for k in sids], dtype=np.int64),
        strict_dropped=np.array([recs[k]["strict_dropped"] for k in sids], dtype=np.int64),
        att=np.array([recs[k]["att"] for k in sids], dtype=float), sd=np.array([x["sd"] for x in stats], dtype=float),
        cutoff=np.array([x["cutoff"] for x in stats], dtype=float), p2=np.array([x["p2"] for x in stats], dtype=float),
        z=np.array([x["z"] for x in stats], dtype=float), placebo=placebo)


# ---------------------------------------------------------------------------------------------
# comparison with a SWEEP-V1 design file (identity gate, amendment 5.1 and 5.2)
# ---------------------------------------------------------------------------------------------
TIGHT = 1e-12
LOOSE = 1e-9
STRUCTURAL_CODES = ("NO_CELLS", "NO_DESIGN", "NO_PRE_FAMILY", "KEPT_LT_80", "CONSTANT")


def _scaled_max(a, b):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    both = np.isfinite(a) & np.isfinite(b)
    if not both.any():
        return 0.0
    return float(np.max(np.abs(a[both] - b[both]) / np.maximum(1.0, np.maximum(np.abs(a[both]), np.abs(b[both])))))


def compare_with_sweep(arrays, old, full):
    """arrays: this module's arrays; old: dict-like of the SWEEP-V1 npz.  Always checked (5.2): series ids, families, structural codes, fingerprints and the ATT
    array.  With full=True (reference U, 5.1) also the codes, the NaN pattern and values of the placebo draws, sd, cutoff, p2, z and the feasible counts."""
    res = dict(ok=True, problems=[])

    def bad(msg):
        res["ok"] = False
        res["problems"].append(msg)

    if [str(x) for x in arrays["series_ids"]] != [str(x) for x in old["series_ids"]]:
        bad("series ids differ")
        return res
    if [str(x) for x in arrays["family"]] != [str(x) for x in old["family"]]:
        bad("families differ")
    new_code, old_code = [str(x) for x in arrays["code"]], [str(x) for x in old["code"]]
    for i, (a, b) in enumerate(zip(new_code, old_code)):
        if (a in STRUCTURAL_CODES or b in STRUCTURAL_CODES) and a != b:
            bad(f"structural code of series {i} differs: {a} vs {b}")
    if [str(x) for x in arrays["fingerprint"]] != [str(x) for x in old["fingerprint"]]:
        bad("pattern fingerprints differ")
    res["att_max_scaled_diff"] = _scaled_max(arrays["att"], old["att"])
    if not np.array_equal(np.isfinite(arrays["att"]), np.isfinite(old["att"])) or res["att_max_scaled_diff"] > TIGHT:
        bad(f"ATT differs: {res['att_max_scaled_diff']:.2e}")
    res["codes_differing_support"] = int(sum(1 for a, b in zip(new_code, old_code) if a != b))
    if full:
        if new_code != old_code:
            bad("codes differ")
        for key in ("att", "sd", "cutoff", "p2", "z"):
            d = _scaled_max(arrays[key], old[key])
            res[f"{key}_max_scaled_diff"] = d
            if not np.array_equal(np.isfinite(arrays[key]), np.isfinite(old[key])) or d > LOOSE:
                bad(f"{key} differs: {d:.2e}")
        if not np.array_equal(np.isfinite(arrays["placebo"]), np.isfinite(old["placebo"])):
            bad("NaN pattern of the placebo draws differs")
        d = _scaled_max(arrays["placebo"], old["placebo"])
        res["placebo_max_scaled_diff"] = d
        if d > LOOSE:
            bad(f"placebo draws differ: {d:.2e}")
        if not np.array_equal(arrays["n_feasible"], old["n_feasible"]):
            bad("feasible counts differ")
    return res


# ---------------------------------------------------------------------------------------------
# the Q1 primary reference (amendment 3.3)
# ---------------------------------------------------------------------------------------------
def q1_primary(ctx, ref, n_draws=N_DRAWS):
    """ATT of the registered primary entry (stored panel) and its placebo ATTs on its own cell pattern under a reference."""
    prim = ctx.primary
    if prim is None or prim.status != "ready":
        raise RuntimeError("the primary entry is not ready")
    rows, outcomes = S.panel_outcomes(prim)
    ops = S.build_ops(prim.declaration, prim.design, primary_bundles(prim.design, ref["blocks"], ref["seed"], n_draws))
    y = np.array([outcomes[k] for k in ops.keys])
    att, draws = S.project(ops, y)
    st = S.draw_statistics(att, draws)
    return dict(att=att, sd=st["sd"], cutoff=st["cutoff"], p2=st["p2"], z=st["z"], n_feasible=int(ops.feasibility["feasible_draws"]),
                support_pass=bool(ops.feasibility["support_pass"]), n_cells=len(ops.keys), placebo=draws)
