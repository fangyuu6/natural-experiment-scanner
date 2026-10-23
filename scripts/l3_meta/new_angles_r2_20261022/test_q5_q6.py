"""Unit tests for pure functions of q5_q6.py. Run: .venv_did/bin/python -I -B scripts/l3_meta/new_angles_r2_20261022/test_q5_q6.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
import numpy as np
import q5_q6 as Q


def test_binom():
    assert abs(Q.binom_two_sided(5, 10) - 1.0) < 1e-12
    assert abs(Q.binom_two_sided(0, 10) - 2 / 1024) < 1e-12
    assert abs(Q.binom_two_sided(9, 10) - 22 / 1024) < 1e-12


def test_pairs():
    a = np.array([1, 1, -1, 1, -1]); fam = np.array([0, 0, 0, 1, 1])
    ag, tot, per = Q.pair_counts(a, fam)
    # family 0: +,+,-: pairs 3, agree 1 ; family 1: +,-: pairs 1, agree 0
    assert (ag, tot) == (1, 4) and per[0] == (1, 3, 3) and per[1] == (0, 1, 2)
    assert Q.S_score(np.array([1, 1, 1]), np.array([0, 0, 0])) == 1.0
    assert np.isnan(Q.S_score(np.array([1, -1]), np.array([0, 1])))


def test_shuffle_preserves_sizes():
    rng = np.random.default_rng(1)
    a = np.array([1, 1, 1, 1, -1, -1]); fam = np.array([0, 0, 1, 1, 2, 2])
    n = Q.shuffle_null(a, fam, np.array(list("xxxxyy")), 200, rng)
    assert np.all((n >= 0) & (n <= 1))
    # group y holds only -1 so family 2 always agrees (both -1) in grouped shuffle
    assert n.min() >= 2 / 3 - 1e-9


def test_pooled_from_T_matches_na_core():
    rng = np.random.default_rng(2)
    ds = []
    for _ in range(5):
        y = rng.normal(size=6); w = rng.integers(1, 5, 6).astype(float); lab = Q.C.halves(rng.normal(size=6))
        ds.append((y, w, lab))
    T = np.array([Q.C.design_terms(*d)[:4] for d in ds])
    pooled = Q.C.fe_contrast([dict(y=d[0], w=d[1], lab=d[2]) for d in ds])[0]
    assert abs(Q.pooled_from_T(T, np.ones(5)) - pooled) < 1e-12
    # design weight 2 on one design == duplicating it
    v = np.array([2, 1, 1, 1, 1.0])
    dup = Q.C.fe_contrast([dict(y=d[0], w=d[1], lab=d[2]) for d in ds + [ds[0]]])[0]
    assert abs(Q.pooled_from_T(T, v) - dup) < 1e-12


def test_domain_group():
    assert Q.domain_group("education") == "other" and Q.domain_group("health") == "health"


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f(); print("ok", k)
