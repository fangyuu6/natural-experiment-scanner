import sys, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.dont_write_bytecode = True
import numpy as np
import q2_q3 as Q

def mk(vals, ses=None):
    return {e: (v, (ses[i] if ses else None)) for i, (e, v) in enumerate(vals.items())}

def test_onset_primary():
    es = {-4: (0.1, None), -3: (-0.2, None), -2: (0.1, None), -1: (0.0, None), 0: (0.3, None), 1: (0.4, None), 2: (0.5, None), 3: (0.5, None), 4: (0.5, None), 5: (0.5, None)}
    assert Q.onset_primary(es, 1.0) == 0
    es[1] = (0.1, None)           # not above envelope 0.2 at e=1 -> e=0 fails, e=2 works
    assert Q.onset_primary(es, 1.0) == 2
    assert Q.onset_primary(es, -1.0) == 6   # wrong sign everywhere -> censored
    es2 = {e: (0.1, None) for e in range(-4, 6)}
    assert Q.onset_primary(es2, 1.0) == 6
    es3 = dict(es); es3[4] = (0.5, None); es3[5] = (0.0, None); es3 = {k: v for k, v in es3.items() if k in (-4, -3, -2, -1)} | {0: (0.0, None), 1: (0.0, None), 2: (0, None), 3: (0, None), 4: (0.5, None), 5: (0.1, None)}
    assert Q.onset_primary(es3, 1.0) == 6   # e=4 visible but e+1=5 not above envelope

def test_secondary_and_elig():
    es = {e: (0.0, 1.0) for e in range(-4, 6)}
    es[2] = (3.0, 1.0); es[3] = (2.5, 1.0)
    assert Q.onset_secondary(es, 1.0) == 2
    assert Q.q2_eligible(es, 1.0) and not Q.q2_eligible(es, None)
    del es[0]
    assert Q.q2_eligible(es, 1.0)
    del es[1]
    assert not Q.q2_eligible(es, 1.0)

def test_gap_shuffle():
    T = np.array([1, 1, 1, 5, 5, 5.]); g = np.array(list("aaabbb"))
    gap, pair = Q.largest_gap(T, g, ["a", "b"])
    assert gap == 4 and pair == ("b", "a")
    rng = np.random.default_rng(1)
    s = np.array([0, 0, 0, 1, 1, 1])
    out = Q.shuffle_labels(rng, g, s)
    assert sorted(out[:3]) == sorted(g[:3])

def test_q3():
    rows = [(g, e, 1.0 + 0.1 * g, 0.2) for g in (1, 2, 3) for e in range(4)]
    cv = Q.cohort_values(rows)
    assert len(cv) == 3 and abs(cv[2][0] - 1.2) < 1e-12 and abs(cv[2][1] - 0.2) < 1e-12 and abs(cv[2][2] - 0.1) < 1e-12
    Qv, p = Q.q_stat([1.0, 1.0, 1.0], [0.1, 0.1, 0.1]); assert Qv == 0 and p == 1
    Qv, p = Q.q_stat([0.0, 1.0], [1.0, 1.0]); assert abs(Qv - 0.5) < 1e-12 and abs(p - math.erfc(math.sqrt(0.25))) < 1e-9
    rows2 = rows[:-1]
    assert len(Q.cohort_values(rows2)) == 2
    V = np.array([1., 2., 3., 4., np.nan])
    assert Q.placebo_p(3.0, V) == (1 + 2) / 5
    lp = Q.loo_p(V); assert abs(lp[0] - 1.0) < 1e-12 and abs(lp[3] - 1 / 4) < 1e-12 and np.isnan(lp[4])
    gt = [dict(g=1, event_time=e, att=float(e)) for e in range(4)] + [dict(g=2, event_time=e, att=2.0) for e in range(3)]
    assert Q.draw_cohort_values(gt) == [1.5]
    ev = Q.evaluate([dict(flag=1, source="A")] * 12 + [dict(flag=0, source="A")] * 18, 0.1)
    assert ev["pattern"] and ev["share"] == 0.4

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f(); print("ok", k)
