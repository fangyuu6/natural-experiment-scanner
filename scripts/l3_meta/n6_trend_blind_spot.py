#!/usr/bin/env python3
"""L3 meta, EXPLORATORY (EX-1): N6 trend blind spot (T178). Spec: PREREG_AMEND_20261020_N6_RESUME.md.
Run: VECLIB_MAXIMUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv_did/bin/python -I -B scripts/l3_meta/n6_trend_blind_spot.py
Reads only derived L3 inputs; writes data/l3_meta/n6_20261020/ (write-once)."""
import sys, json, hashlib, datetime
from pathlib import Path
import numpy as np
from scipy import stats, optimize

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
IN = ROOT / "data/l3_meta/q4_n6_inputs_20261020"
LIVE = ROOT / "data/l3_meta/refresh/20261019_r3/live/l2_set_live.json"
OUT = ROOT / "data/l3_meta/n6_20261020"
AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261020_N6_RESUME.md"
SEED = 20261020
K_LEADS = 4
EIG_TOL = 1e-10
SAT_TOL = 1e-8
INFL = 1.13


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def reference(event_times):
    return 0 if -1 in set(event_times) else -1


def select_leads(event_times, k=K_LEADS):
    """indices of the k leads nearest to the reference (all if k is None), and r."""
    r = reference(event_times)
    idx = sorted((i for i, e in enumerate(event_times) if e < r), key=lambda i: -event_times[i])
    return (idx if k is None else idx[:k]), r


def wald_gamma(h, V, power):
    """gamma at which the joint Wald test (5%) has the given power. Returns dict."""
    h = np.asarray(h, float); V = np.asarray(V, float)
    w, U = np.linalg.eigh((V + V.T) / 2)
    keep = w > EIG_TOL * w.max()
    df = int(keep.sum())
    c = U[:, keep].T @ h
    q = float(np.sum(c ** 2 / w[keep]))
    out_norm = float(np.linalg.norm(h - U[:, keep] @ c))
    if out_norm > SAT_TOL * np.linalg.norm(h) or df == 0:
        return dict(gamma=None, df=df, saturated=True, hVh=q)
    crit = stats.chi2.ppf(0.95, df)
    f = lambda lam: stats.ncx2.sf(crit, df, lam) - power
    hi = 1.0
    while f(hi) < 0:
        hi *= 2
    lam = optimize.brentq(f, 0.0, hi, xtol=1e-12)
    return dict(gamma=float(np.sqrt(lam / q)), df=df, saturated=False, hVh=q)


def anyt_gamma(h, V, power, ndraw=40000, seed=SEED):
    h = np.asarray(h, float); V = np.asarray(V, float)
    sd = np.sqrt(np.diag(V))
    w, U = np.linalg.eigh((V + V.T) / 2)
    L = U * np.sqrt(np.clip(w, 0, None))
    z = np.random.default_rng(seed).standard_normal((ndraw, len(h))) @ L.T
    def pw(g):
        return float(np.mean(np.any(np.abs((g * h + z) / sd) > 1.96, axis=1)))
    if pw(0.0) >= power:
        return 0.0
    hi = 1.0
    while pw(hi) < power:
        hi *= 2
        if hi > 1e12:
            return None
    lo = 0.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if pw(mid) < power: lo = mid
        else: hi = mid
    return (lo + hi) / 2


def design_row(did, lead, meta, cutoff, k=K_LEADS, vscale=1.0, with_anyt=True):
    et = lead["event_times"]
    idx, r = select_leads(et, k)
    row = dict(design_id=did, reference=r, n_leads_available=sum(1 for e in et if e < r), n_used=len(idx),
               H=meta.get("H"), policy_family=meta.get("policy_family"), n_treated=meta.get("n_treated_overall"),
               v_mult=meta.get("v_mult"), v_ci=meta.get("v_ci"), noise_cutoff=cutoff)
    if not idx or not meta.get("H") or not (meta.get("v_mult") or 0) > 0:
        row.update(status="NO_INPUT"); return row
    h = np.array([et[i] - r for i in idx], float)
    V = np.array(lead["cov"], float)[np.ix_(idx, idx)] * vscale
    w = np.linalg.eigvalsh(V)
    row["rank_deficient"] = bool((w > EIG_TOL * w.max()).sum() < len(idx))
    row["flag_short"] = len(idx) < 3
    H = meta["H"]; se = float(np.sqrt(meta["v_mult"]))
    for p in (50, 80):
        g = wald_gamma(h, V, p / 100)
        row[f"saturated{p}"] = g["saturated"]; row["df"] = g["df"]
        gam = g["gamma"]
        row[f"gamma{p}"] = gam
        if gam is None:
            for key in ("bias_avg", "bias_last", "R_se", "R_cut"): row[f"{key}{p}"] = None
        else:
            b = gam * (H + 1) / 2
            row[f"bias_avg{p}"] = b; row[f"bias_last{p}"] = gam * H
            row[f"R_se{p}"] = b / se
            row[f"R_cut{p}"] = b / cutoff if cutoff and cutoff > 0 else None
            if meta.get("v_ci"):
                row[f"R_seci{p}"] = b / float(np.sqrt(max(meta["v_mult"], meta["v_ci"])))
        if with_anyt and p == 50:
            ga = anyt_gamma(h, V, 0.5)
            row["gamma50_anyt"] = ga
            row["anyt_ratio"] = (ga / gam) if (ga is not None and gam) else None
    row["status"] = "OK"
    return row


# ---------- pooled summaries ----------
def quart(x):
    x = np.asarray(x, float)
    if len(x) == 0: return dict(n=0)
    q = np.percentile(x, [25, 50, 75])
    return dict(n=int(len(x)), q25=float(q[0]), median=float(q[1]), q75=float(q[2]), min=float(x.min()), max=float(x.max()))


def share_gt1(x):
    x = np.asarray(x, float)
    return float(np.mean(x > 1)) if len(x) else float("nan")


def vals(rows, key, as_zero=False):
    out = []
    for r in rows:
        if r.get("status") != "OK": continue
        v = r.get(key)
        if v is None:
            if as_zero and r.get(key.replace("R_se", "saturated").replace("R_cut", "saturated")) is not False: out.append(0.0)
            continue
        out.append(v)
    return out


def family_boot(rows, key, stat, B=9999, seed=SEED):
    fam = {}
    for r in rows:
        if r.get("status") == "OK" and r.get(key) is not None:
            fam.setdefault(r["policy_family"], []).append(r[key])
    fams = list(fam)
    if len(fams) < 2: return dict(n_families=len(fams), lo=None, hi=None)
    rng = np.random.default_rng(seed)
    res = []
    for _ in range(B):
        pick = rng.integers(0, len(fams), len(fams))
        x = np.concatenate([fam[fams[i]] for i in pick])
        res.append(stat(x))
    lo, hi = np.percentile(res, [2.5, 97.5])
    return dict(n_families=len(fams), lo=float(lo), hi=float(hi), indicative=len(fams) < 10)


def spearman(rows, key, xkey):
    pts = [(r[xkey], r[key], r["policy_family"]) for r in rows if r.get("status") == "OK" and r.get(key) is not None and r.get(xkey) is not None]
    if len(pts) < 4: return dict(n=len(pts), rho=None)
    x = np.array([p[0] for p in pts], float); y = np.array([p[1] for p in pts], float); f = [p[2] for p in pts]
    rho = float(stats.spearmanr(x, y)[0])
    loo = []
    for fm in set(f):
        m = np.array([a != fm for a in f])
        if m.sum() >= 4: loo.append(float(stats.spearmanr(x[m], y[m])[0]))
    rng = np.random.default_rng(SEED)
    fams = sorted(set(f)); boot = []
    for _ in range(2000):
        pick = rng.integers(0, len(fams), len(fams))
        sel = np.concatenate([np.where(np.array(f) == fams[i])[0] for i in pick])
        if len(set(x[sel])) > 1 and len(set(y[sel])) > 1:
            boot.append(float(stats.spearmanr(x[sel], y[sel])[0]))
    return dict(n=len(pts), rho=rho, loo_min=min(loo) if loo else None, loo_max=max(loo) if loo else None,
                boot_lo=float(np.percentile(boot, 2.5)) if boot else None, boot_hi=float(np.percentile(boot, 97.5)) if boot else None)


def pooled(rows):
    ok = [r for r in rows if r.get("status") == "OK"]
    flagged = [r for r in ok if r["rank_deficient"] or r["flag_short"] or r["saturated50"] or r["saturated80"]]
    unflag = [r for r in ok if r not in flagged]
    S = {}
    for p in (50, 80):
        for sc in ("R_se", "R_cut", "R_seci"):
            k = f"{sc}{p}"
            for lab, rr in (("all", rows), ("unflagged", unflag)):
                v = vals(rr, k)
                S[f"{k}_{lab}"] = dict(**quart(v), share_gt1=share_gt1(v))
        k = f"R_se{p}"
        v0 = vals(rows, k, as_zero=True)
        S[f"{k}_undefined_as_zero"] = dict(**quart(v0), share_gt1=share_gt1(v0))
        S[f"{k}_boot_share"] = family_boot(rows, k, share_gt1)
        S[f"{k}_boot_median"] = family_boot(rows, k, lambda x: float(np.median(x)))
        S[f"{k}_spearman_nleads"] = spearman(rows, k, "n_leads_available")
        S[f"{k}_spearman_ntreated"] = spearman(rows, k, "n_treated")
    S["n_designs"] = len(rows); S["n_ok"] = len(ok); S["n_flagged"] = len(flagged)
    S["flagged"] = [r["design_id"] for r in flagged]
    S["undefined50"] = [r["design_id"] for r in ok if r["saturated50"]]
    S["undefined80"] = [r["design_id"] for r in ok if r["saturated80"]]
    return S


def patterns(S, sens):
    out = {}
    fam = S["R_se50_boot_share"]["n_families"]
    a = True
    for p in (50, 80):
        sh = S[f"R_se{p}_all"]["share_gt1"]; lo = S[f"R_se{p}_boot_share"]["lo"]
        a = a and sh >= 0.25 and lo is not None and lo > 0.10
    out["A_blind_spot_common"] = bool(a and fam >= 8)
    out["n_families"] = fam
    b = {}
    for xk in ("nleads", "ntreated"):
        ok = True
        for p in (50, 80):
            s = S[f"R_se{p}_spearman_{xk}"]
            if s["rho"] is None or abs(s["rho"]) < 0.5 or s["loo_min"] is None: ok = False; continue
            same = all(abs(v) >= 0.5 and np.sign(v) == np.sign(s["rho"]) for v in (s["loo_min"], s["loo_max"]))
            ok = ok and same
        b[xk] = bool(ok)
    out["B_dependence"] = b
    base = S["R_se50_all"]["share_gt1"]
    out["fragile"] = bool(any(abs(sens[k]["R_se50_all"]["share_gt1"] - base) > 0.10 for k in sens))
    out["label"] = ("pattern A" if out["A_blind_spot_common"] else "") + (" pattern B" if any(b.values()) else "")
    out["label"] = out["label"].strip() or "no pattern"
    return out


def run(lead_all, derived, live):
    meta = {r["design_id"]: r for r in derived["designs"]}
    cut = {}
    for d in live["designs"]:
        po = d.get("primary_outcomes") or []
        cut[d["design_id"]] = po[0].get("noise_cutoff") if po else None
    ids = sorted(lead_all)
    rows = [design_row(i, lead_all[i], meta[i], cut.get(i)) for i in ids]
    variants = {
        "all_leads": [design_row(i, lead_all[i], meta[i], cut.get(i), k=None, with_anyt=False) for i in ids],
        "V_x_1.13sq": [design_row(i, lead_all[i], meta[i], cut.get(i), vscale=INFL ** 2, with_anyt=False) for i in ids],
    }
    S = pooled(rows)
    sens = {k: pooled(v) for k, v in variants.items()}
    return rows, variants, S, sens, patterns(S, sens)


def main(argv=None):
    if OUT.exists():
        print("output exists (write-once)", OUT); return 2
    want = [l.split()[1] for l in AMEND.with_name("PREREG_AMEND_20261020_N6_RESUME_HASH.txt").read_text().splitlines() if l.startswith("sha256:")][0]
    if sha256_file(AMEND) != want:
        print("amendment hash mismatch"); return 3
    lead = json.load(open(IN / "LEADS_COV.json")); der = json.load(open(IN / "DERIVED_INPUTS.json")); live = json.load(open(LIVE))
    rows, variants, S, sens, pat = run(lead, der, live)
    OUT.mkdir(parents=True)
    res = dict(schema="n6_trend_blind_spot_v1", generated_utc=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               amendment_sha256=want, script_sha256=sha256_file(__file__), inputs={"LEADS_COV": sha256_file(IN / "LEADS_COV.json"), "DERIVED_INPUTS": sha256_file(IN / "DERIVED_INPUTS.json"), "live": sha256_file(LIVE)},
               designs=rows, variants=variants, pooled=S, pooled_sensitivity=sens, patterns=pat)
    (OUT / "N6_RESULTS.json").write_text(json.dumps(res, indent=1, default=lambda o: None))
    print(json.dumps(dict(patterns=pat, n_ok=S["n_ok"], flagged=S["flagged"]), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
