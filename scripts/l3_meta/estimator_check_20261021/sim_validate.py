"""T227: validate eclib on simulated staggered panels with known (heterogeneous, dynamic) effects.  EXPLORATORY.
Run: .venv_did/bin/python -I -B scripts/l3_meta/estimator_check_20261021/sim_validate.py [--reps 200] [--out FILE.csv]"""
import sys, json, argparse, math
from pathlib import Path
import numpy as np
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import eclib as E


class O: pass


def make_panel(rng, n_units=60, T=30, n_never=15, cohorts=(8, 12, 16, 20), het=True):
    units = [f"u{i:02d}" for i in range(n_units)]
    adoption = {}
    k = 0
    for u in units[n_never:]:
        adoption[u] = cohorts[k % len(cohorts)]; k += 1
    for u in units[:n_never]:
        adoption[u] = None
    periods = list(range(1, T + 1))
    ufe = {u: rng.normal(0, 1) for u in units}
    tfe = {t: 0.1 * t + rng.normal(0, 0.3) for t in periods}
    def tau(g, e):                       # cohort-specific level and a dynamic ramp (heterogeneous)
        base = {8: 3.0, 12: 1.0, 16: -1.0, 20: 2.0}[g] if het else 1.0
        return base + (0.4 * e if het else 0.0)
    y = {}
    for u in units:
        for t in periods:
            g = adoption[u]
            eff = tau(g, t - g) if (g is not None and t >= g) else 0.0
            y[u, t] = ufe[u] + tfe[t] + eff + rng.normal(0, 0.5)
    exposure = {u: {t: dict(clean=(adoption[u] is None or t < adoption[u]), full=(adoption[u] is not None and t >= adoption[u]))
                    for t in periods} for u in units}
    D = dict(units=units, periods=periods, observed_periods=periods, adoption=adoption,
             mask={(u, t) for u in units for t in periods}, exposure=exposure,
             whole_window_clean={u: adoption[u] is None for u in units}, groups=sorted(set(cohorts)))
    o = O(); o.D = D; o.y = y; o.keys = sorted(D["mask"]); o.index = {k_: i for i, k_ in enumerate(o.keys)}
    o.yvec = np.array([y[k_] for k_ in o.keys]); o.min_controls = 1
    return o, tau


def truth(o, cells, tau):
    post = [c for c in cells if c["post"]]
    m = sum(c["n_treated"] for c in post)
    return sum(c["n_treated"] / m * tau(c["g"], c["event_time"]) for c in post)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--reps", type=int, default=200); ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rng = np.random.default_rng(20261021)
    res = {k: [] for k in ("truth", "cs_never", "cs_nyt", "sa", "twfe")}
    K = None
    for r in range(a.reps):
        o, tau = make_panel(rng)
        K = len(o.keys)
        cn = E.make_cells(o.D, "never"); cy = E.make_cells(o.D, "nyt")
        res["truth"].append(truth(o, cn, tau))
        res["cs_never"].append(float(E.agg_weights(cn, o.index, K) @ o.yvec))
        res["cs_nyt"].append(float(E.agg_weights(cy, o.index, K) @ o.yvec))
        res["sa"].append(E.sun_abraham(o, cn)[0])
        res["twfe"].append(E.twfe(o, cn)[0])
    t = np.array(res["truth"])
    out = []
    for k in ("cs_never", "cs_nyt", "sa", "twfe"):
        x = np.array(res[k]); out.append(dict(estimator=k, mean_truth=t.mean(), mean_est=x.mean(), bias=float((x - t).mean()),
                                              rmse=float(np.sqrt(((x - t) ** 2).mean())), reps=a.reps))
    for row in out:
        print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()}))
    if a.out:
        import csv
        with open(a.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
    # coverage check of the analytic SE for the pipeline-type estimator (homogeneous no-het case)
    cov, n = 0, 100
    for r in range(n):
        o, tau = make_panel(rng, het=False)
        cn = E.make_cells(o.D, "never")
        att, se = E.overall_se(o, cn)
        cov += abs(att - truth(o, cn, tau)) <= 1.96 * se
    print(json.dumps(dict(se_coverage_cs_never=cov / n, reps=n)))

if __name__ == "__main__":
    main()
