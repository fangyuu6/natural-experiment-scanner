"""Unit tests for pure functions of q1_q4.py. Run: .venv_did/bin/python -I -B scripts/l3_meta/new_angles_r2_20261022/test_q1_q4.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
import numpy as np
import q1_q4 as Q


def test_shuffle_keeps_strata_and_sizes():
    lab = np.array(list("aabbbcc")); st = np.array([0, 0, 0, 1, 1, 1, 1])
    out = Q.shuffle_within_strata(lab, st, np.random.default_rng(1))
    assert sorted(out[st == 0]) == sorted(lab[st == 0]) and sorted(out[st == 1]) == sorted(lab[st == 1])


def test_domain_gaps():
    flag = np.array([1, 1, 0, 0, 0, 1.0]); lab = np.array(["a", "a", "b", "b", "b", "b"])
    g = Q.domain_gaps(flag, lab, ["a", "b"])
    assert abs(g["a"] - (1 - 0.25)) < 1e-12 and abs(g["b"] - (0.25 - 1)) < 1e-12


def test_tercile():
    t = Q.tercile_label({"a": 1, "b": 2, "c": 3, "d": 4, "e": 5, "f": 6})
    assert t["a"] == t["b"] == "low" and t["e"] == t["f"] == "high" and t["c"] == "mid"


def test_exact_binomial_and_sign():
    assert abs(Q.exact_two_sided(5, 10) - 1.0) < 1e-12
    assert Q.exact_two_sided(10, 10) < 0.003
    assert Q.sign(-2.0) == -1 and Q.sign(0) == 0 and Q.sign(None) == 0


def test_q1_labels_rule():
    res = {"a": dict(G=.25, p975=.2, p025=-.2), "b": dict(G=.25, p975=.3, p025=-.3), "c": dict(G=-.25, p975=.2, p025=-.2),
           "d": dict(G=.15, p975=.1, p025=-.1)}
    lab = Q.q1_labels(res, list(res))
    assert lab == {"a": "visible", "b": "no difference", "c": "hard to see", "d": "no difference"}


def test_q4_stats():
    S = [dict(att=1, dir=1, bn=1.0), dict(att=-1, dir=1, bn=1.0), dict(att=1, dir=-1, bn=0.0), dict(att=-2, dir=-1, bn=None)]
    s = Q.q4_stats(S)
    assert s["hits"] == 2 and s["right_bn"] == 1 and s["wrong_bn"] == 1 and s["not_distinguishable"] == 1 and s["no_flag"] == 1 and s["H_bn"] == 0.5


def test_one_vote_per_law():
    D = [dict(law="x", dg="a", source="s", v=1), dict(law="x", dg="a", source="s", v=0), dict(law="y", dg="b", source="s", v=1)]
    r = Q.one_vote_per_law(D, lambda d: d["v"])
    assert sorted((x[0], x[3]) for x in r) == [("x", 0.5), ("y", 1.0)]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
