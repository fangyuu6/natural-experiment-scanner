#!/usr/bin/env python3
"""N11 common drift, version N11-V1 (rule id EX-1, exploratory).

Rules: docs/plans/l3-meta/PREREG_AMEND_20261017_N11_V1.md (hash record PREREG_AMEND_20261017_N11_V1_HASH.txt).
Read-only on the r4 refresh sweep files and the N1-V2 sets file. Output written once:
data/l3_meta/n11_v1/RESULTS_N11_V1.json.   Run: .venv_did/bin/python -I -B scripts/l3_meta/build_n11.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261017_N11_V1.md"
HASH_REC = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261017_N11_V1_HASH.txt"
AMEND_S1 = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261017_N11_V1_S1.md"
HASH_REC_S1 = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261017_N11_V1_S1_HASH.txt"
SETS = ROOT / "data/l3_meta/n1_sets_v2/n1_sets_v2.json"
SWEEP = ROOT / "data/l3_meta/refresh/20261017_r4/sweep"
OUT = ROOT / "data/l3_meta/n11_v1/RESULTS_N11_V1.json"
B = 9999
SEED = 20261017
MIN_SERIES, MIN_WINDOW, MIN_PAIRS, MIN_FAMILIES = 20, 10, 40, 10
K_FLOOR, K_INCONCL, P_PERM = 0.05, 0.30, 0.05


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def check_record():
    want = {}
    for rec, am in ((HASH_REC, AMEND), (HASH_REC_S1, AMEND_S1)):
        want.update(_parse(rec, am))
    bad = [f for f, h in want.items() if sha(ROOT / f) != h]
    if bad:
        raise SystemExit("REFUSED: hash mismatch " + str(bad))
    return len(want)


def _parse(rec, am):
    txt = rec.read_text()
    want = {}
    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("sha256:"):
            want[str(am.relative_to(ROOT))] = line.split()[1]
        else:
            m = re.match(r"^(\S+) sha256 ([0-9a-f]{64})$", line)
            if m:
                want[m.group(1)] = m.group(2)
    return want


def wls(c, o, w):
    """Return (alpha, beta) of weighted least squares of c on [1, o] or None when degenerate."""
    sw = w.sum()
    if len(c) < 3 or sw <= 0:
        return None
    mo = (w * o).sum() / sw
    mc = (w * c).sum() / sw
    v = (w * (o - mo) ** 2).sum()
    if v <= 1e-15:
        return None
    b = (w * (o - mo) * (c - mc)).sum() / v
    return mc - b * mo, b


def load_designs(sweep=SWEEP, sets_path=SETS):
    sets = {d["design_id"]: d for d in json.load(open(sets_path))["designs"]}
    out = []
    for f in sorted(Path(sweep).glob("*.npz")):
        if f.name.startswith("series_store"):
            continue
        z = np.load(f, allow_pickle=True)
        m = json.loads(str(z["meta_json"]))
        s = sets.get(m["design_id"])
        if s is None:
            continue
        w0, w1 = int(min(m["years"])), int(max(m["years"]))
        onset = dict(s["T_operative_onset_year"])
        coh = {st: int(y) for y, l in s["T_cohorts"].items() for st in l}
        for st in s["T"]:
            if onset.get(st) is None:
                onset[st] = coh[st]
        A = {(st, y) for st in s["T"] for y in range(max(w0, int(onset[st])), w1 + 1)}
        zz = np.array(z["z"], dtype=float)
        ok = (np.array(z["code"]) == "SUPPORTED") & np.isfinite(zz)
        zz = np.where(ok, zz, np.nan)
        out.append(dict(id=m["design_id"], family=s["policy_family"], w=(w0, w1), A=A, z=zz,
                        sids=[str(x) for x in z["series_ids"]]))
    return out


def pair_tables(D):
    n = len(D)
    O = np.zeros((n, n))
    C = np.full((n, n), np.nan)
    N = np.zeros((n, n), dtype=int)
    WIN = np.zeros((n, n), dtype=int)
    for i in range(n):
        for j in range(i + 1, n):
            lo, hi = max(D[i]["w"][0], D[j]["w"][0]), min(D[i]["w"][1], D[j]["w"][1])
            win = max(0, hi - lo + 1)
            o = 0.0
            if win > 0:
                ai = {x for x in D[i]["A"] if lo <= x[1] <= hi}
                aj = {x for x in D[j]["A"] if lo <= x[1] <= hi}
                u = len(ai | aj)
                o = len(ai & aj) / u if u else 0.0
            both = np.isfinite(D[i]["z"]) & np.isfinite(D[j]["z"])
            k = int(both.sum())
            c = float(np.mean(D[i]["z"][both] * D[j]["z"][both])) if k else np.nan
            for a, b in ((i, j), (j, i)):
                O[a, b] = o
                C[a, b] = c
                N[a, b] = k
                WIN[a, b] = win
    return O, C, N, WIN


def estimate(D, O, C, N, WIN, B=B, seed=SEED):
    n = len(D)
    fam = sorted({d["family"] for d in D})
    fidx = np.array([fam.index(d["family"]) for d in D])
    iu = [(i, j) for i in range(n) for j in range(i + 1, n)]
    ok = [(i, j) for i, j in iu if N[i, j] >= MIN_SERIES and WIN[i, j] >= MIN_WINDOW]
    head = [(i, j) for i, j in ok if fidx[i] != fidx[j]]
    same = [(i, j) for i, j in ok if fidx[i] == fidx[j]]
    res = dict(n_designs=n, n_families_all=len(fam), n_pairs_headline=len(head), n_pairs_same_family=len(same))
    fams_in = sorted({int(fidx[x]) for p in head for x in p})
    res["n_families_headline"] = len(fams_in)
    if len(head) < MIN_PAIRS or len(fams_in) < MIN_FAMILIES:
        res["verdict"] = "NOT_EVALUABLE"
        return res
    I = np.array([p[0] for p in head]); J = np.array([p[1] for p in head])
    c = C[I, J]; o = O[I, J]; w = N[I, J].astype(float)
    a, b = wls(c, o, w)
    width = float(np.percentile(o, 90) - np.percentile(o, 10))
    res.update(alpha=a, beta=b, width=width, K=b * width,
               O_p10=float(np.percentile(o, 10)), O_p50=float(np.percentile(o, 50)), O_p90=float(np.percentile(o, 90)),
               share_O_zero=float(np.mean(o == 0)), n_O_one=int(np.sum(o == 1.0)))
    rng = np.random.default_rng([seed, 11])
    F = len(fam)
    bs, skipped = [], 0
    for _ in range(B):
        cnt = np.bincount(rng.integers(0, F, size=F), minlength=F)
        m = cnt[fidx].astype(float)
        ww = w * m[I] * m[J]
        keep = ww > 0
        r = wls(c[keep], o[keep], ww[keep]) if keep.sum() >= 3 else None
        if r is None:
            skipped += 1
        else:
            bs.append(r[1])
    bs = np.array(bs)
    lo, hi = np.percentile(bs, [2.5, 97.5])
    rng = np.random.default_rng([seed, 12])
    ge = 0
    for _ in range(B):
        pi = rng.permutation(n)
        r = wls(c, O[pi[I], pi[J]], w)
        if r is not None and r[1] >= b:
            ge += 1
    p = (1 + ge) / (B + 1)
    res.update(beta_lo=float(lo), beta_hi=float(hi), K_lo=float(lo * width), K_hi=float(hi * width),
               p_perm=p, boot_skipped=skipped)
    if lo > 0 and p < P_PERM and res["K"] >= K_FLOOR:
        res["verdict"] = "DETECTED"
    elif res["K_hi"] >= K_INCONCL:
        res["verdict"] = "INCONCLUSIVE"
    else:
        res["verdict"] = "NOT_DETECTED"
    # extras
    ex = {}
    if same:
        Is = np.array([p_[0] for p_ in same]); Js = np.array([p_[1] for p_ in same])
        r = wls(C[Is, Js], O[Is, Js], N[Is, Js].astype(float))
        ex["same_family_beta"] = None if r is None else r[1]
        ex["same_family_n"] = len(same)
    r = wls(c, o, np.ones_like(w))
    ex["unweighted_beta"] = None if r is None else r[1]
    lof = []
    for f in fams_in:
        keep = (fidx[I] != f) & (fidx[J] != f)
        r = wls(c[keep], o[keep], w[keep]) if keep.sum() >= 3 else None
        if r is not None:
            lof.append(r[1])
    ex["leave_one_family_out_beta_range"] = [float(min(lof)), float(max(lof))] if lof else None
    res["extras"] = ex
    return res


def main():
    if OUT.exists():
        raise SystemExit("REFUSED: result exists " + str(OUT))
    n_rec = check_record()
    D = load_designs()
    O, C, N, WIN = pair_tables(D)
    res = estimate(D, O, C, N, WIN)
    res.update(version="N11-V1", rule_id="EX-1", n_hash_checked=n_rec,
               builder_sha256=sha(__file__), amendment_sha256=sha(AMEND), hash_record_sha256=sha(HASH_REC),
               sets_sha256=sha(SETS), designs=[d["id"] for d in D], B=B, seed=SEED)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=2, sort_keys=True, default=float))
    print(json.dumps({k: v for k, v in res.items() if k != "designs"}, indent=1, sort_keys=True, default=float))


if __name__ == "__main__":
    main()
