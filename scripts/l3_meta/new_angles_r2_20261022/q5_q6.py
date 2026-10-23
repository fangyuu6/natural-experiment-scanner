#!/usr/bin/env python3
"""T256 coder C: Q5 (sister laws) and Q6 (richer / more urban states). EXPLORATORY (EX-1).
Plan (hash-locked, read-only): docs/plans/l3-meta/PREPLAN_T256_R2_20261022.md
Run: .venv_did/bin/python -I -B scripts/l3_meta/new_angles_r2_20261022/q5_q6.py q5|q6|all [--force]
Outputs: data/l3_meta/new_angles_r2_20261022/{q5,q6}_*.csv and RESULTS_Q5.json / RESULTS_Q6.json (write-once unless --force).
"""
import sys, csv, json, math, collections, hashlib, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts/l3_meta/new_angles_20261021"))
import numpy as np
import na_core as C  # read-only reuse: halves, design_terms, band, two_sided_p

PLAN = ROOT / "docs/plans/l3-meta/PREPLAN_T256_R2_20261022.md"
PLAN_SHA = "a503c461f99954215ced45f789b1c74def7ceab635b5a5c9b33dfcd0f7d5f661"
IN = ROOT / "data/l3_meta/new_angles_20261021/inputs"
FINAL = ROOT / "data/l3_meta/new_angles_20261021/states_decide/mapping/FINAL_codes.csv"
SY = ROOT / "data/l3_meta/covariates/state_year_v2.csv"
LIVE = ROOT / "data/l3_meta/refresh/20261022_r2/live/l2_set_live.json"
OUT = ROOT / "data/l3_meta/new_angles_r2_20261022"
SEED = 20261022
NPERM = 5000
NPERM_SENS = 2000
NPERM_GUARD = 1000


# ---------------------------------------------------------------- generic helpers
def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def sgn(x):
    return (x > 0) - (x < 0)


def domain_group(d):
    return "other" if d in ("education", "other") else d


def binom_two_sided(k, n, p=0.5):
    """exact two-sided binomial p (sum of probabilities <= observed probability)."""
    if n == 0:
        return float("nan")
    pm = [math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(n + 1)]
    pk = pm[k]
    return float(min(1.0, sum(x for x in pm if x <= pk * (1 + 1e-9))))


def jclean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): jclean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jclean(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        return jclean(o.item())
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def write_csv(path, rows, force):
    rows = list(rows)
    path = Path(path)
    if path.exists() and not force:
        raise SystemExit(f"{path} exists (write-once; use --force)")
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, restval=""); w.writeheader(); w.writerows(rows)


def write_json(path, obj, force):
    path = Path(path)
    if path.exists() and not force:
        raise SystemExit(f"{path} exists (write-once; use --force)")
    path.write_text(json.dumps(jclean(obj), indent=1, allow_nan=False))


# ---------------------------------------------------------------- loading
def load_base():
    ros = {r["design_id"]: r for r in csv.DictReader(open(IN / "design_roster.csv"))}
    keys = {r["design_id"]: r["policy_key"] for r in csv.DictReader(open(IN / "policy_keys.csv"))}
    dirs = {r["design_id"]: int(r["direction_final"]) for r in csv.DictReader(open(FINAL))}
    live = {x["design_id"]: x for x in json.load(open(LIVE))["designs"] if x["status"] == "estimated"}
    # consistency check (plan section 0)
    problems = []
    if set(live) != set(ros):
        problems.append(f"id sets differ: live-only {sorted(set(live) - set(ros))}, roster-only {sorted(set(ros) - set(live))}")
    for did, r in ros.items():
        if did not in live:
            continue
        a, b = fnum(r["overall_att"]), live[did]["point_estimate"]
        if a is None or abs(a - b) > 1e-9:
            problems.append(f"overall_att mismatch {did}: {a} vs {b}")
        if (r["beyond_noise"] == "True") != bool(live[did]["beyond_noise"]) and r["beyond_noise"] != "":
            problems.append(f"beyond_noise mismatch {did}")
        if r["policy_family"] != live[did]["policy_family"]:
            problems.append(f"policy_family differs roster vs live {did}: {r['policy_family']} / {live[did]['policy_family']}")
    if problems:
        raise SystemExit("CONSISTENCY CHECK FAILED:\n" + "\n".join(problems))
    D = {}
    for did, r in ros.items():
        D[did] = dict(id=did, domain=r["domain"], dgroup=domain_group(r["domain"]), family=r["policy_family"], source_group=r["source_group"],
                      law=keys[did], overall=fnum(r["overall_att"]), sd=fnum(r["noise_sd"]), direction=dirs[did],
                      beyond=r["beyond_noise"])
    return D


def input_hashes():
    return {str(p.relative_to(ROOT)): sha(p) for p in (PLAN, IN / "design_roster.csv", IN / "policy_keys.csv", FINAL, IN / "group_time_export.csv",
                                                       IN / "cohort_states.csv", SY, LIVE)}


def stamp():
    return dict(rule_id="EX-1", plan="docs/plans/l3-meta/PREPLAN_T256_R2_20261022.md", plan_sha256=sha(PLAN), seed=SEED,
                code_sha256=sha(HERE / "q5_q6.py"), input_sha256=input_hashes(), run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                python="numpy " + np.__version__)


# ================================================================ Q5: sister laws
def pair_counts(a, fam):
    """a: array of +1/-1 aligned signs; fam: array of family ints. Returns (agree_pairs, total_pairs, per-family dict)."""
    agree = total = 0
    per = {}
    for f in np.unique(fam):
        v = a[fam == f]
        k = len(v)
        if k < 2:
            continue
        pos = int((v > 0).sum()); neg = k - pos
        ag = pos * (pos - 1) // 2 + neg * (neg - 1) // 2
        tot = k * (k - 1) // 2
        agree += ag; total += tot
        per[int(f)] = (ag, tot, k)
    return agree, total, per


def S_score(a, fam):
    ag, tot, _ = pair_counts(a, fam)
    return (ag / tot) if tot else float("nan")


def shuffle_null(a, fam, groups, nperm, rng):
    """permute family labels among designs within group (or all if groups is None); a stays attached to the design.
    Equivalent to permuting a within group with family structure fixed."""
    idx_by = []
    if groups is None:
        idx_by = [np.arange(len(a))]
    else:
        for g in np.unique(groups):
            idx_by.append(np.where(groups == g)[0])
    out = np.empty(nperm)
    for b in range(nperm):
        ap = a.copy()
        for idx in idx_by:
            ap[idx] = a[rng.permutation(idx)]
        out[b] = S_score(ap, fam)
    return out


def q5_pattern_eval(a, fam, groups, nperm, rng):
    obs = S_score(a, fam)
    ag, tot, per = pair_counts(a, fam)
    null = shuffle_null(a, fam, groups, nperm, rng)
    null = null[np.isfinite(null)]
    p95 = float(np.percentile(null, 95)); m = float(null.mean())
    return dict(S=obs, pairs=tot, n_families=len(per), null_mean=m, null_p95=p95, null_p5=float(np.percentile(null, 5)), null_p975=float(np.percentile(null, 97.5)),
                excess=obs - m, above_p95=bool(obs > p95), excess_ge_015=bool(obs - m >= 0.15), pattern=bool(obs > p95 and obs - m >= 0.15),
                p_upper=float((1 + np.sum(null >= obs)) / (1 + len(null)))), null


def run_q5(D, force):
    rng = np.random.default_rng(SEED + 5)
    res = dict(question="Q5 sister laws: do versions of one kind of law point the same way?", n_perm=NPERM, seed=SEED + 5, stamp=stamp())
    nz = [d for d in D.values() if d["direction"] != 0 and d["overall"] not in (None, 0.0)]
    zero = [d for d in D.values() if d["direction"] == 0]
    res["counts"] = dict(n_designs=len(D), n_direction_zero_excluded=len(zero), n_overall_zero=sum(1 for d in D.values() if d["overall"] == 0.0),
                         n_nonzero_designs=len(nz))
    fams_all = collections.Counter(d["family"] for d in D.values())
    res["counts"]["families_with_2plus_designs_before_direction_filter"] = sum(1 for v in fams_all.values() if v >= 2)
    res["counts"]["designs_in_those_families_before_direction_filter"] = sum(v for v in fams_all.values() if v >= 2)
    for d in nz:
        d["a"] = sgn(d["overall"]) * d["direction"]
    fam_ids = {f: i for i, f in enumerate(sorted(set(d["family"] for d in nz)))}
    a = np.array([d["a"] for d in nz]); fam = np.array([fam_ids[d["family"]] for d in nz])
    grp = np.array([d["dgroup"] for d in nz])
    ag, tot, per = pair_counts(a, fam)
    inv = {i: f for f, i in fam_ids.items()}
    nfam = len(per)
    res["counts"].update(n_families_with_2plus_nonzero=nfam, n_pairs=tot, n_designs_in_those_families=int(sum(v[2] for v in per.values())))
    fam_rows = []
    for fi, (agp, totp, k) in sorted(per.items(), key=lambda kv: inv[kv[0]]):
        members = [d for d in nz if d["family"] == inv[fi]]
        fam_rows.append(dict(family=inv[fi], domain_groups="|".join(sorted(set(m["dgroup"] for m in members))), n_designs=k,
                             n_favourable=sum(1 for m in members if m["a"] > 0), n_unfavourable=sum(1 for m in members if m["a"] < 0),
                             pairs=totp, agreeing_pairs=agp, family_score=agp / totp, n_laws=len(set(m["law"] for m in members)),
                             members="|".join(m["id"] for m in members)))
    res["family_table"] = fam_rows
    min_ok = nfam >= 6 and tot >= 30
    res["minimum"] = dict(required="6 families with 2+ non-zero designs and 30 pairs", families=nfam, pairs=tot, met=bool(min_ok),
                          gap_families=max(0, 6 - nfam), gap_pairs=max(0, 30 - tot))
    # design-level csv (always written; unit = design in sister families)
    drows = [dict(design_id=d["id"], family=d["family"], domain=d["domain"], domain_group=d["dgroup"], law=d["law"], direction_final=d["direction"],
                  sign_overall_att=sgn(d["overall"]), aligned_sign=d["a"], family_size_nonzero=int((fam == fam_ids[d["family"]]).sum())) for d in nz]
    write_csv(OUT / "q5_design_level.csv", drows, force)
    write_csv(OUT / "q5_family_level.csv", fam_rows, force)
    if not min_ok:
        res["result"] = "not enough data"
        res["plain"] = [f"Result: not enough data.",
                        f"Only {nfam} families with 2+ clear-direction designs and {tot} pairs are available; the plan needs 6 families and 30 pairs (gap {max(0, 6-nfam)} families, {max(0, 30-tot)} pairs).",
                        f"n = {int(sum(v[2] for v in per.values()))} designs in {nfam} families."]
        res["pattern"] = None
        write_json(OUT / "RESULTS_Q5.json", res, force)
        return res
    prim, null = q5_pattern_eval(a, fam, grp, NPERM, rng)
    res["primary"] = prim
    res["primary"]["null_domain_group_preserved"] = True
    write_csv(OUT / "q5_null_draws.csv", [dict(draw=i, S=float(v)) for i, v in enumerate(null)], force)
    sens, _ = q5_pattern_eval(a, fam, None, NPERM, np.random.default_rng(SEED + 55))
    res["sensitivity_unrestricted_shuffle"] = sens
    # sensitivity: weighted by family (equal-weight mean of family scores)
    fs = [r["family_score"] for r in fam_rows]
    res["sensitivity_equal_weight_family_mean"] = dict(S_equal_weight=float(np.mean(fs)), note="descriptive; primary weights families by number of pairs")
    # sensitivity: policy_family merge adult_use_cannabis into adult_use_cannabis_legalization (obvious naming duplicate)
    merged = [("adult_use_cannabis_legalization" if d["family"] == "adult_use_cannabis" else d["family"]) for d in nz]
    mids = {f: i for i, f in enumerate(sorted(set(merged)))}
    fam_m = np.array([mids[f] for f in merged])
    agm, totm, perm_ = pair_counts(a, fam_m)
    if len(perm_) >= 1:
        sm, _ = q5_pattern_eval(a, fam_m, grp, NPERM_SENS, np.random.default_rng(SEED + 56))
        res["sensitivity_merge_adult_use_cannabis_into_legalization"] = sm
    # guards: one vote per law (inside a family, designs of one law collapse to the sign of their mean aligned sign; ties dropped)
    ov = []
    for f in sorted(set(d["family"] for d in nz)):
        bylaw = collections.defaultdict(list)
        for d in nz:
            if d["family"] == f:
                bylaw[d["law"]].append(d)
        for law, ms in bylaw.items():
            s = sgn(sum(m["a"] for m in ms))
            if s != 0:
                ov.append((f, law, s, ms[0]["dgroup"]))
    a1 = np.array([x[2] for x in ov]); fam1 = np.array([fam_ids[x[0]] for x in ov]); grp1 = np.array([x[3] for x in ov])
    g1, _ = q5_pattern_eval(a1, fam1, grp1, NPERM_GUARD, np.random.default_rng(SEED + 57))
    res["guard_one_vote_per_law"] = dict(n_votes=len(ov), **g1)
    # leave-one-family-out
    lofo = []
    for fi in sorted(per):
        keep = fam != fi
        sub_ag, sub_tot, sub_per = pair_counts(a[keep], fam[keep])
        if sub_tot == 0:
            continue
        ev, _ = q5_pattern_eval(a[keep], fam[keep], grp[keep], NPERM_GUARD, np.random.default_rng(SEED + 100 + fi))
        lofo.append(dict(left_out=inv[fi], S=ev["S"], null_mean=ev["null_mean"], null_p95=ev["null_p95"], excess=ev["excess"], above_p95=ev["above_p95"],
                         pairs_left=sub_tot, pattern=ev["pattern"]))
    res["guard_leave_one_family_out"] = dict(runs=lofo, n_pattern_kept=int(sum(x["pattern"] for x in lofo)), n_runs=len(lofo))
    # guard: leave-one-law-out and leave-one-domain-group-out (descriptive reruns with fresh nulls)
    def rerun(mask, tag, seed):
        if mask.sum() < 4:
            return None
        s_ag, s_tot, s_per = pair_counts(a[mask], fam[mask])
        if s_tot == 0:
            return None
        ev, _ = q5_pattern_eval(a[mask], fam[mask], grp[mask], NPERM_GUARD, np.random.default_rng(seed))
        return dict(left_out=tag, S=ev["S"], excess=ev["excess"], above_p95=ev["above_p95"], pairs=s_tot, pattern=ev["pattern"])
    laws = sorted(set(d["law"] for d in nz))
    lolo = [r for i, l in enumerate(laws) if (r := rerun(np.array([d["law"] != l for d in nz]), l, SEED + 300 + i)) is not None]
    res["guard_leave_one_law_out"] = dict(runs=lolo, n_pattern_kept=int(sum(x["pattern"] for x in lolo)), n_runs=len(lolo))
    dgs = sorted(set(grp))
    lodo = [r for i, g in enumerate(dgs) if (r := rerun(grp != g, g, SEED + 500 + i)) is not None]
    res["guard_leave_one_domain_out"] = dict(runs=lodo, n_pattern_kept=int(sum(x["pattern"] for x in lodo)), n_runs=len(lodo))
    # pattern rule items
    res["pattern_rule"] = dict(S_above_p95_of_shuffle=prim["above_p95"], S_minus_mean_shuffle_ge_0_15=prim["excess_ge_015"], minimum_met=True)
    res["pattern"] = bool(prim["pattern"])
    res["result"] = "pattern" if res["pattern"] else "no difference"
    res["reference_level_mean_shuffled_S"] = prim["null_mean"]
    res["note_mechanical_codrift"] = "designs of one family often share states, years and outcomes, so agreement can be mechanical co-drift"
    fam_txt = "; ".join(f"{r['family']} {r['agreeing_pairs']}/{r['pairs']}" for r in fam_rows)
    res["plain"] = [f"Result: {res['result']}.",
                    f"Sign agreement among sister-law designs was S = {prim['S']:.3f} against a shuffled reference of {prim['null_mean']:.3f} (95th percentile {prim['null_p95']:.3f}, excess {prim['excess']:.3f}, needs above the 95th percentile and +0.15).",
                    f"n = {int(sum(v[2] for v in per.values()))} designs in {nfam} families, {tot} pairs; per family agreeing/total pairs: {fam_txt}."]
    write_json(OUT / "RESULTS_Q5.json", res, force)
    return res


# ================================================================ Q6: richer / more urban states
def load_sy():
    inc, urb = {}, {}
    for r in csv.DictReader(open(SY)):
        k = (r["state"], int(r["year"]))
        v = fnum(r["ln_pcpi_real_2024usd"]); u = fnum(r["urban_share"])
        if v is not None:
            inc[k] = v
        if u is not None:
            urb[k] = u
    return inc, urb


def load_cells():
    gt = collections.defaultdict(lambda: collections.defaultdict(dict))
    pre = collections.defaultdict(list)
    for r in csv.DictReader(open(IN / "group_time_export.csv")):
        did = r["design_id"]; g = int(r["g"]); e = int(float(r["event_time"])); a = fnum(r["att"])
        if e <= -2 and a is not None:
            pre[did].append(a)
        if r["post"] == "True" and e >= 0:
            gt[did][g][e] = (a, fnum(r["n_treated"]))
    s2 = {did: (float(np.std(v, ddof=1)) if len(v) > 3 else None) for did, v in pre.items()}
    cs = collections.defaultdict(dict)
    for r in csv.DictReader(open(IN / "cohort_states.csv")):
        cs[r["design_id"]][int(r["g"])] = r
    return gt, s2, cs


def cohort_cov(row, g, inc, urb, year_rule):
    """n_states-weighted (each matched state weight 1) mean of covariate in the year before onset.
    year_rule 'g-1': calendar year g-1 for every state (plan text); 't232': stored state onset year (as in T232).
    Cohort dropped when no state has a known stored onset year. Returns (income, urban, n_states_used) or None."""
    if row["matched"] != "1" or not row["states"]:
        return None
    sts = row["states"].split("|")
    ons = [int(x) if x not in ("", "None") else None for x in row["onset_years"].split("|")]
    if not any(o is not None for o in ons):
        return None
    iv, uv = [], []
    for st, on in zip(sts, ons):
        if on is None:
            continue
        yr = (g - 1) if year_rule == "g-1" else on
        if (st, yr) in inc:
            iv.append(inc[(st, yr)])
        if (st, yr) in urb:
            uv.append(urb[(st, yr)])
    if not iv or not uv:
        return None
    return float(np.mean(iv)), float(np.mean(uv)), len(sts)


def build_q6(D, gt, s2, cs, inc, urb, variant):
    """variant: primary | ext (add dir=0/no-sd designs: sign(overall), S2) | s2all (S2 scale for all designs) | t232year."""
    out = []
    drop = collections.Counter()
    year_rule = "t232" if variant == "t232year" else "g-1"
    for did, d in sorted(D.items()):
        cells = gt.get(did, {})
        if not cells or not cs.get(did):
            drop["no_state_list"] += 1; continue
        gs_all = sorted(cells)
        if min(gs_all) >= 8000:
            drop["quarterly_not_calendar"] += 1; continue
        ok_primary = d["direction"] != 0 and d["sd"]
        if variant in ("primary", "t232year"):
            if not ok_primary:
                drop["not_direction_and_sd"] += 1; continue
            scale, sign = d["sd"], d["direction"]
        elif variant == "ext":
            if ok_primary:
                scale, sign = d["sd"], d["direction"]
            else:
                if not s2.get(did) or d["overall"] in (None, 0.0):
                    drop["no_s2_or_zero"] += 1; continue
                scale, sign = s2[did], sgn(d["overall"])
        elif variant == "s2all":
            if not s2.get(did):
                drop["no_s2"] += 1; continue
            sign = d["direction"] if d["direction"] != 0 else sgn(d["overall"] or 0)
            if sign == 0:
                drop["no_sign"] += 1; continue
            scale = s2[did]
        else:
            raise ValueError(variant)
        y, w, xi, xu, gl = [], [], [], [], []
        for g in gs_all:
            row = cs[did].get(g)
            if row is None or g >= 8000 or g < 1900:
                continue
            ev = cells[g]
            if not all(e in ev and ev[e][0] is not None for e in range(4)):
                continue
            if not ev[0][1]:
                continue
            cv = cohort_cov(row, g, inc, urb, year_rule)
            if cv is None:
                continue
            y.append(float(np.mean([ev[e][0] for e in range(4)])) * sign / scale)
            w.append(ev[0][1]); xi.append(cv[0]); xu.append(cv[1]); gl.append(g)
        out.append(dict(id=did, law=d["law"], domain=d["domain"], dgroup=d["dgroup"], y=np.array(y), w=np.array(w), xi=np.array(xi), xu=np.array(xu), g=gl,
                        n_elig=len(y)))
    return out, dict(drop)


def pooled_from_T(T, v):
    """T: (..., nd, 4) array of [num_pos, den_pos, num_neg, den_neg]; v: design weights (nd,)."""
    s = (T * v[..., :, None]).sum(axis=-2)
    return s[..., 0] / s[..., 1] - s[..., 2] / s[..., 3]


def q6_contrast(designs, xkey, nperm, rng, label=""):
    """Per covariate. Returns (result dict, per-design rows). designs: eligible >=4 cohorts."""
    use = [d for d in designs if d["n_elig"] >= 4]
    res = dict(covariate=label, n_designs_ge4_cohorts=len(use), n_designs_any=len(designs))
    labs, rows = [], []
    for d in use:
        lab = C.halves(d[xkey])
        labs.append(lab)
    # observed
    nd = len(use)
    Tobs = np.zeros((nd, 4)); Dunw = np.full(nd, np.nan)
    ok = np.zeros(nd, bool)
    for i, d in enumerate(use):
        t = C.design_terms(d["y"], d["w"], labs[i])
        if t is None:
            continue
        ok[i] = True
        Tobs[i] = t[:4]
        Dunw[i] = float(d["y"][labs[i] == 1].mean() - d["y"][labs[i] == -1].mean())
    ids = np.where(ok)[0]
    use = [use[i] for i in ids]; labs = [labs[i] for i in ids]; Tobs = Tobs[ids]; Dunw = Dunw[ids]
    nd = len(use)
    res["n_designs_both_halves"] = nd
    res["n_cohorts_high"] = int(sum((l == 1).sum() for l in labs)); res["n_cohorts_low"] = int(sum((l == -1).sum() for l in labs))
    rowsd = [dict(design_id=d["id"], law=d["law"], domain=d["domain"], domain_group=d["dgroup"], n_eligible_cohorts=d["n_elig"],
                  n_high=int((l == 1).sum()), n_low=int((l == -1).sum()), mean_x_high=float(d[xkey][l == 1].mean()), mean_x_low=float(d[xkey][l == -1].mean()),
                  D=float(Dunw[i]), D_positive=int(Dunw[i] > 0)) for i, (d, l) in enumerate(zip(use, labs))]
    if nd == 0:
        return res, rowsd, None
    # null draws: within-design shuffles of x across cohorts == permuting the half labels
    Tn = np.empty((nperm, nd, 4)); Dn = np.empty((nperm, nd))
    for b in range(nperm):
        for i, d in enumerate(use):
            lab = rng.permutation(labs[i])
            t = C.design_terms(d["y"], d["w"], lab)
            Tn[b, i] = t[:4]
            Dn[b, i] = d["y"][lab == 1].mean() - d["y"][lab == -1].mean()
    return res, rowsd, dict(use=use, Tobs=Tobs, Dunw=Dunw, Tn=Tn, Dn=Dn)


def eval_subset(mask, Tobs, Dunw, Tn, v, laws):
    """mask: bool over designs. v: design weights. Returns dict with share/binomial and FE contrast + band."""
    idx = np.where(mask)[0]
    n = len(idx)
    if n == 0:
        return None
    Dpos = int((Dunw[idx] > 0).sum()); Dneg = int((Dunw[idx] < 0).sum())
    nn = Dpos + Dneg
    share = Dpos / nn if nn else float("nan")
    p_b = binom_two_sided(Dpos, nn) if nn else float("nan")
    obs = float(pooled_from_T(Tobs[idx], v[idx]))
    null = pooled_from_T(Tn[:, idx, :], v[idx])
    lo, hi = C.band(null)
    return dict(n_designs=n, share_positive=share, n_positive=Dpos, n_negative=Dneg, binom_p=p_b,
                outside_035_065=bool(nn and (share < 0.35 or share > 0.65)), fe_contrast=obs, null_mean=float(null.mean()), band_2_5=lo, band_97_5=hi,
                outside_band=bool(obs < lo or obs > hi), p_two_sided=C.two_sided_p(obs, null))


def q6_pattern(base, rule_strict_sign=True):
    """base is the full-sample eval; returns item dict (LODO added by caller)."""
    return dict(share_outside_035_065=base["outside_035_065"], binom_p_lt_05=bool(base["binom_p"] < 0.05), fe_outside_band=base["outside_band"])


def q6_analyse(designs, xkey, nperm, rng, label, min_designs=25):
    res, rowsd, st = q6_contrast(designs, xkey, nperm, rng, label)
    nd = res["n_designs_both_halves"]
    res["minimum"] = dict(required="25 designs with at least 4 eligible mapped cohorts (2 per half)", available=nd, met=bool(nd >= min_designs),
                          gap=max(0, min_designs - nd))
    if nd < min_designs:
        res["result"] = "not enough data"; res["pattern"] = None
        return res, rowsd
    use = st["use"]; Tobs, Dunw, Tn, Dn = st["Tobs"], st["Dunw"], st["Tn"], st["Dn"]
    ones = np.ones(nd)
    allm = np.ones(nd, bool)
    base = eval_subset(allm, Tobs, Dunw, Tn, ones, None)
    res["primary"] = base
    # sign of D vs null of share (descriptive reference band for share from the shuffles)
    nsh = (Dn > 0).mean(axis=1)
    res["share_null_band_2_5_97_5"] = [float(np.percentile(nsh, 2.5)), float(np.percentile(nsh, 97.5))]
    res["median_D"] = float(np.median(Dunw))
    # per-domain-group descriptive (>= 8 designs)
    dg = np.array([d["dgroup"] for d in use]); laws = np.array([d["law"] for d in use])
    bydom = {}
    for g in sorted(set(dg)):
        m = dg == g
        if m.sum() >= 8:
            e = eval_subset(m, Tobs, Dunw, Tn, ones, None)
            bydom[g] = {k: e[k] for k in ("n_designs", "share_positive", "binom_p", "fe_contrast", "band_2_5", "band_97_5", "outside_band")}
        else:
            bydom[g] = dict(n_designs=int(m.sum()), note="fewer than 8 designs, not shown")
    res["by_domain_group_descriptive"] = bydom
    # one vote per law: weights 1/k within law for the FE contrast; law-mean D for the share
    ul = sorted(set(laws))
    k_law = {l: int((laws == l).sum()) for l in ul}
    v1 = np.array([1.0 / k_law[l] for l in laws])
    lawD = np.array([Dunw[laws == l].mean() for l in ul])
    nlp, nln = int((lawD > 0).sum()), int((lawD < 0).sum())
    obs1 = float(pooled_from_T(Tobs, v1)); null1 = pooled_from_T(Tn, v1); lo1, hi1 = C.band(null1)
    res["guard_one_vote_per_law"] = dict(n_laws=len(ul), share_positive=nlp / (nlp + nln) if (nlp + nln) else float("nan"), binom_p=binom_two_sided(nlp, nlp + nln),
                                         fe_contrast=obs1, band_2_5=lo1, band_97_5=hi1, outside_band=bool(obs1 < lo1 or obs1 > hi1))
    # leave-one-law-out (same shuffles)
    lolo = []
    for l in ul:
        m = laws != l
        if m.sum() < 5:
            continue
        e = eval_subset(m, Tobs, Dunw, Tn, ones, None)
        lolo.append(dict(left_out=l, n_designs=e["n_designs"], share_positive=e["share_positive"], fe_contrast=e["fe_contrast"], outside_band=e["outside_band"],
                         binom_p=e["binom_p"]))
    res["guard_leave_one_law_out"] = dict(n=len(lolo), fe_sign_kept=all(sgn(x["fe_contrast"]) == sgn(base["fe_contrast"]) for x in lolo),
                                         share_side_kept=all(sgn(x["share_positive"] - 0.5) == sgn(base["share_positive"] - 0.5) for x in lolo),
                                         n_still_outside_band=int(sum(x["outside_band"] for x in lolo)),
                                         fe_min=min(x["fe_contrast"] for x in lolo), fe_max=max(x["fe_contrast"] for x in lolo))
    # leave-one-domain-out
    lodo = []
    for g in sorted(set(dg)):
        m = dg != g
        if m.sum() < 5:
            continue
        e = eval_subset(m, Tobs, Dunw, Tn, ones, None)
        lodo.append(dict(left_out=g, n_designs=e["n_designs"], share_positive=e["share_positive"], fe_contrast=e["fe_contrast"], outside_band=e["outside_band"],
                         fe_sign_kept=bool(sgn(e["fe_contrast"]) == sgn(base["fe_contrast"])),
                         share_side_kept=bool(sgn(e["share_positive"] - 0.5) == sgn(base["share_positive"] - 0.5))))
    res["guard_leave_one_domain_out"] = lodo
    lodo_same = all(x["fe_sign_kept"] and x["share_side_kept"] for x in lodo)
    items = dict(share_outside_035_065=base["outside_035_065"], share_binom_p_lt_0_05=bool(base["binom_p"] < 0.05), fe_contrast_outside_2_5_97_5_band=base["outside_band"],
                 same_sign_after_leave_one_domain_out=bool(lodo_same))
    res["pattern_rule"] = items
    res["pattern"] = bool(all(items.values()))
    # strict-reading extra item: (i) and (ii) point the same way
    agree = bool(sgn(base["share_positive"] - 0.5) == sgn(base["fe_contrast"]))
    res["strict_reading_extra_item_share_and_fe_same_direction"] = agree
    res["pattern_strict_reading"] = bool(res["pattern"] and agree)
    res["result"] = "pattern" if res["pattern_strict_reading"] else "no difference"
    return res, rowsd


def run_q6(D, force):
    inc, urb = load_sy()
    gt, s2, cs = load_cells()
    res = dict(question="Q6 does the same law pay off more in richer or more urban states?", n_perm=NPERM, seed=SEED + 6, stamp=stamp())
    designs, drop = build_q6(D, gt, s2, cs, inc, urb, "primary")
    res["design_selection"] = dict(n_roster=len(D), n_kept_with_state_list_and_cells=len(designs), dropped=drop,
                                   n_with_ge4_eligible_cohorts=sum(1 for d in designs if d["n_elig"] >= 4),
                                   n_cohorts_total=int(sum(d["n_elig"] for d in designs)),
                                   year_rule="covariate read at calendar year g-1 (plan text); cohorts without any known stored onset year dropped; states missing the covariate skipped")
    allrows = []
    prim = {}
    for key, nm in (("xi", "income"), ("xu", "urbanity")):
        rng = np.random.default_rng(SEED + 6 + (0 if key == "xi" else 1))
        r, rows = q6_analyse(designs, key, NPERM, rng, nm)
        prim[nm] = r
        for x in rows:
            x["covariate"] = nm
        allrows += rows
    res["primary"] = prim
    # sensitivities
    sens = {}
    for var, tag in (("t232year", "year_rule_T232_stored_state_onset"), ("ext", "extended_set_sign_overall_S2_for_dir0_or_no_sd"), ("s2all", "S2_scale_all_designs")):
        dd, dr = build_q6(D, gt, s2, cs, inc, urb, var)
        sens[tag] = dict(n_designs_with_cells=len(dd), n_ge4_cohorts=sum(1 for d in dd if d["n_elig"] >= 4), dropped=dr)
        for key, nm in (("xi", "income"), ("xu", "urbanity")):
            r, _ = q6_analyse(dd, key, NPERM_SENS, np.random.default_rng(SEED + 60 + len(tag) + (0 if key == "xi" else 1)), nm)
            sens[tag][nm] = r
    res["sensitivities"] = sens
    res["not_claimed"] = "party and ideology correlate with state wealth (T232): even a clear gap is not a wealth effect; no similar-state reference is run here"
    # plain lines
    def line(nm):
        r = prim[nm]
        if r["result"] == "not enough data":
            return f"{nm}: not enough data ({r['minimum']['available']} designs, need 25, gap {r['minimum']['gap']})"
        p = r["primary"]
        return (f"{nm}: {p['n_positive']}/{p['n_positive']+p['n_negative']} designs higher in richer/more urban half (share {p['share_positive']:.2f}, exact p {p['binom_p']:.3f}); "
                f"pooled contrast {p['fe_contrast']:.3f} noise units vs shuffle band [{p['band_2_5']:.3f}, {p['band_97_5']:.3f}]; result {r['result']}")
    results = [prim["income"]["result"], prim["urbanity"]["result"]]
    if all(x == "not enough data" for x in results):
        head = "not enough data"
    elif any(x == "pattern" for x in results):
        head = "pattern in " + " and ".join(nm for nm in ("income", "urbanity") if prim[nm]["result"] == "pattern")
    else:
        head = "no difference"
    res["result"] = head
    res["plain"] = [f"Result: {head}.", line("income"), line("urbanity") + f". n = {prim['income']['n_designs_both_halves']} designs (income), {prim['urbanity']['n_designs_both_halves']} (urbanity)."]
    write_csv(OUT / "q6_design_level.csv", allrows, force)
    write_json(OUT / "RESULTS_Q6.json", res, force)
    return res


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    what = args[0] if args else "all"
    if sha(PLAN) != PLAN_SHA:
        raise SystemExit("plan hash mismatch")
    OUT.mkdir(exist_ok=True, parents=True)
    D = load_base()
    t0 = time.time()
    if what in ("q5", "all"):
        r = run_q5(D, force)
        print("Q5:", *r["plain"], sep="\n  ")
        print("Q5 done", round(time.time() - t0, 1), "s")
    if what in ("q6", "all"):
        r = run_q6(D, force)
        print("Q6:", *r["plain"], sep="\n  ")
        print("Q6 done", round(time.time() - t0, 1), "s")
