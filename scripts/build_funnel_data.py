"""Build deploy_output/funnel_data.json: the sequential screening funnel.

Gate semantics are NOT invented here; they mirror scripts/build_derived_views.py
(the `ok` expression that defines viable_strict):

    strict_enacted == 1                      (G1, src/extraction/enactment_rules.py)
    wave_flag == 0                           (scripts/build_wave_flags.py)
    "covid_window" not in confound_flags     (src/extraction/confound_calendar.py)
    data_verdict in (public_ok, mixed)       (G2, src/extraction/dataset_registry.py)
    min_gate >= 3                            (src/extraction/min_gate.py)

The cumulative order follows the commit order of the derived layers
(G1 -> confound/wave -> data access -> min-gate). `verify_level` is NOT a gate in
viable_strict (non-enacted rows also carry id_verified), so it is reported only as a note.

Every file is streamed with csv; everything is de-duplicated on policy_id (first kept).
Stdlib only.  Usage: python scripts/build_funnel_data.py
"""
import csv, json, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "data/output/policy_db_20260415_483k.csv"
DER = ROOT / "data/output/derived"
OUT = ROOT / "deploy_output/funnel_data.json"
csv.field_size_limit(2**31 - 1)

STAGES = [
    ("all", "All policies", "全部政策",
     "Every unique policy_id in the 483k policy DB (duplicate ids collapsed, first row kept)."),
    ("strict_enacted", "Strictly enacted", "严格已颁布",
     "G1: status is 'enacted' and, for federal legislation, the id must be a public law (PLAW/publ), not a BILLS-* bill version."),
    ("no_wave", "No adoption wave", "无多州采纳潮",
     "wave_flag = 0: fewer than 3 distinct states enacted a policy in the same family within one year either side (contaminated-controls risk for single-state DID)."),
    ("no_covid", "Outside COVID window", "避开新冠窗口",
     "No covid_window confound flag (policy date 2020-01-01 to 2021-12-31); the only confound flag that blocks viable_strict."),
    ("data_ok", "Public data linkable", "公开数据可得",
     "G2: data_verdict is public_ok or mixed, i.e. the free-text datasets field matches the curated public-microdata registry (empty, unmatched_only and restricted_only fail)."),
    ("min_gate", "Min-gate ≥ 3", "最小门槛 ≥ 3",
     "min_gate >= 3: the lowest of the four LLM sub-scores (significance, identification, treatment intensity, data feasibility) is at least 3; the binding constraint, not the mean."),
]
KEYS = [s[0] for s in STAGES]

REASON_LABELS = {
    "status_not_enacted": ("Status not 'enacted' (proposed/other)", "状态非“已颁布”(提案等)"),
    "status_unknown": ("Status unknown or blank", "状态未知或为空"),
    "bill_version_not_law": ("Federal BILLS-* text, not a public law", "联邦 BILLS-* 法案文本,非公法"),
    "no_strict_record": ("No G1 record", "无 G1 记录"),
    "dup_id_layer_conflict": ("Duplicate id: layers disagree", "重复 ID:各层结论不一致"),
    "wave_flagged": ("Part of a ≥3-state adoption wave", "属于 ≥3 州采纳潮"),
    "covid_window": ("Dated inside the COVID window", "日期落在新冠窗口内"),
    "data_empty": ("No datasets named", "未列出数据集"),
    "data_unmatched_only": ("Datasets not in public registry", "数据集不在公开登记表中"),
    "data_restricted_only": ("Restricted-access data only", "仅限受限数据"),
    "mg_missing": ("Sub-scores missing, min-gate undefined", "子分缺失,无法计算"),
    "mg_0": ("Weakest sub-score = 0", "最弱子分 = 0"),
    "mg_1": ("Weakest sub-score = 1", "最弱子分 = 1"),
    "mg_2": ("Weakest sub-score = 2", "最弱子分 = 2"),
}

# Kind of each drop reason, read off what the gate's rule actually does (not guessed):
#   "missing": the field the rule needs is absent or unresolved, so the rule was never truly evaluated.
#   "judged" : the field was present, the rule/score was evaluated on it, and it failed.
# A reason absent from this table falls into an explicit "unattributed" bucket.
KIND = {
    # enactment_rules.classify_enactment: status in ("", "unknown") -> status_unknown; any other
    # non-"enacted" value -> status_not_enacted; BILLS-* id on federal legislation -> bill_version_not_law.
    "status_unknown": ("missing", "Status field is blank or 'unknown': enactment was never determined."),
    "status_not_enacted": ("judged", "Status is present (e.g. proposed) and is not 'enacted'."),
    "bill_version_not_law": ("judged", "The id pattern was evaluated: BILLS-* is bill text, not a public law."),
    "no_strict_record": ("missing", "No G1 record exists for this id, so enactment was never evaluated."),
    "dup_id_layer_conflict": ("missing", "Duplicate id: the G1 layer says enacted, the merged view says not; unresolved, not guessed."),
    # build_wave_flags: wave_flag=1 iff >=3 distinct states enacted in the same family within +-1 year.
    "wave_flagged": ("judged", "Rule evaluated: >=3 states enacted in the same family within a year either side."),
    # confound_calendar.flag_confounds: needs a parseable date; a missing date yields no flag (a pass), so
    # every covid_window drop is a dated row inside 2020-01-01..2021-12-31.
    "covid_window": ("judged", "Rule evaluated on a parsed date that lies in 2020-2021."),
    # dataset_registry.classify_datasets: empty -> 'empty'; named but no alias match -> 'unmatched_only'
    # (kept unmatched "rather than guessed"); only restricted matches -> 'restricted_only'.
    "data_empty": ("missing", "No datasets named in the datasets field."),
    "data_unmatched_only": ("missing", "Datasets are named but none links to the curated registry; accessibility is unresolved, not judged."),
    "data_restricted_only": ("judged", "Datasets resolved, and all are restricted-access."),
    # min_gate.compute_min_gate: None when any of the four sub-scores is absent or non-numeric.
    "mg_missing": ("missing", "At least one of the four sub-scores is absent or non-numeric; min-gate undefined."),
    "mg_0": ("judged", "All four sub-scores present; the weakest is below the threshold of 3."),
    "mg_1": ("judged", "All four sub-scores present; the weakest is below the threshold of 3."),
    "mg_2": ("judged", "All four sub-scores present; the weakest is below the threshold of 3."),
}
KINDS = ("missing", "judged", "unattributed")
kind_of = lambda r: KIND[r][0] if r in KIND else "unattributed"


def split_of(counter):
    """reason->n counter -> {kind: n} over the three kinds (full counts, no truncation)."""
    out = dict.fromkeys(KINDS, 0)
    for r, n in counter.items():
        out[kind_of(r)] += n
    return out


def load_first(path, cols):
    """policy_id -> tuple(cols), first occurrence wins."""
    d = {}
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        rd = csv.reader(f)
        head = next(rd)
        ix = [head.index(c) for c in cols]
        ip = head.index("policy_id")
        for r in rd:
            if r[ip] not in d:
                d[r[ip]] = tuple(r[i] for i in ix)
    return d


def min_gate_of(s):
    try:
        return float(s) if s != "" else None
    except ValueError:
        return None


def main():
    dv = load_first(DER / "derived_views.csv",
                    ["strict_enacted", "verify_level", "confound_flags", "wave_flag",
                     "data_verdict", "min_gate", "viable_strict"])
    se_reason = load_first(DER / "strict_enacted.csv", ["strict_enacted", "reason"])
    acc = load_first(DER / "data_access.csv", ["n_public", "verdict"])
    print(f"derived unique ids: {len(dv)}", file=sys.stderr)

    # stream main csv for unique ids + family/topic; also re-count the LLM data_feasibility score
    # on the policies that reach the min-gate (stages 1-4 passed), to check the note on that score
    seen = {}
    feas_in = Counter()
    with open(MAIN, newline="", encoding="utf-8", errors="replace") as f:
        rd = csv.reader(f)
        head = next(rd)
        ip, ifam, itop, idj = (head.index(c) for c in ("policy_id", "source_family", "policy_topic", "detail_json"))
        nrows = 0
        for r in rd:
            nrows += 1
            pid = r[ip]
            if pid not in seen:
                seen[pid] = ((r[ifam] or "").strip() or "(unknown)", (r[itop] or "").strip() or "(unknown)")
                v = dv.get(pid)     # (se, verify_level, confound_flags, wave_flag, data_verdict, ...)
                if v and v[0] == "1" and v[3] == "0" and "covid_window" not in v[2].split("|") \
                        and v[4] in ("public_ok", "mixed"):
                    try:
                        x = float(json.loads(r[idj] or "{}").get("data_feasibility"))
                    except (ValueError, TypeError, AttributeError):
                        x = None
                    feas_in[x] += 1
    total = len(seen)
    print(f"main rows {nrows}, unique ids {total}; ids missing from derived: "
          f"{sum(1 for p in seen if p not in dv)}", file=sys.stderr)

    n_out = [0] * len(STAGES)
    standalone = Counter()
    reasons = defaultdict(Counter)              # stage_key -> reason -> n   (global)
    fam_tot = Counter(); top_tot = Counter()
    viable_col = 0
    verify_in_strict = Counter(); mixed_no_public = 0
    rows = []
    for pid, (fam, top) in seen.items():
        se, vl, cf, wf, dver, mg_s, via = dv.get(pid, ("0", "", "", "0", "empty", "", "0"))
        mg = min_gate_of(mg_s)
        passes = [True,
                  se == "1",
                  wf == "0",
                  "covid_window" not in cf.split("|"),
                  dver in ("public_ok", "mixed"),
                  mg is not None and mg >= 3]
        for i in range(1, len(STAGES)):
            standalone[KEYS[i]] += passes[i]
        viable_col += via == "1"
        # cumulative walk + drop attribution (first failing gate)
        depth = 0
        drop = None
        for i in range(1, len(STAGES)):
            if passes[i]:
                depth = i
            else:
                if i == 1:
                    s1, why = se_reason.get(pid, ("0", "no_strict_record"))
                    # build_derived_views._load is last-wins on duplicate ids; strict_enacted.csv
                    # first row may say 1 while the merged view says 0 (72 ids) -> flag, don't guess.
                    drop = (KEYS[1], "dup_id_layer_conflict" if s1 == "1" else (why or "no_strict_record"))
                elif i == 2:
                    drop = (KEYS[2], "wave_flagged")
                elif i == 3:
                    drop = (KEYS[3], "covid_window")
                elif i == 4:
                    drop = (KEYS[4], "data_" + dver)
                else:
                    drop = (KEYS[5], "mg_missing" if mg is None else "mg_%d" % int(mg))
                break
        for i in range(depth + 1):
            n_out[i] += 1
        if drop:
            reasons[drop[0]][drop[1]] += 1
        if depth == len(STAGES) - 1:
            if acc.get(pid, ("0", ""))[0] in ("0", ""):
                mixed_no_public += 1
        if se == "1":
            verify_in_strict[vl] += 1
        fam_tot[fam] += 1; top_tot[top] += 1
        rows.append((fam, top, depth, drop))

    top8 = [t for t, _ in top_tot.most_common() if t != "(unknown)"][:8]
    fams = [f for f, _ in fam_tot.most_common()]
    NS = len(STAGES)
    cross = {}
    def cell(f, t):
        return cross.setdefault(f + "||" + t, {"n": [0] * NS, "drops": defaultdict(Counter)})
    for fam, top, depth, drop in rows:
        ts = ["*"] + ([top] if top in top8 else [])
        for f in ("*", fam):
            for t in ts:
                c = cell(f, t)
                for i in range(depth + 1):
                    c["n"][i] += 1
                if drop:
                    c["drops"][drop[0]][drop[1]] += 1
    for c in cross.values():
        # per-stage split by kind, from the FULL reason counts (index 0 = starting pool, nothing dropped)
        sp = {k: [0] * NS for k in KINDS}
        for i, key in enumerate(KEYS):
            for kind, n in split_of(c["drops"].get(key, {})).items():
                sp[kind][i] = n
        for kind in KINDS:
            c["n_dropped_" + kind] = sp[kind]
        c["drops"] = {k: [[r, n] for r, n in v.most_common()] for k, v in c["drops"].items()}
        for i, key in enumerate(KEYS[1:], 1):        # every cell must balance, stage by stage
            assert c["n"][i - 1] - c["n"][i] == sum(sp[k][i] for k in KINDS), (key, c["n"])

    def brief(c):   # cell without the per-reason lists
        return {k: v for k, v in c.items() if k != "drops"}

    stages = []
    for i, (k, en, zh, definition) in enumerate(STAGES):
        sp = split_of(reasons[k])
        stages.append({"key": k, "label_en": en, "label_zh": zh, "definition": definition,
                       "n_in": total if i == 0 else n_out[i - 1], "n_out": n_out[i],
                       "standalone_pass": total if i == 0 else standalone[k],
                       **{"n_dropped_" + kind: sp[kind] for kind in KINDS}})
    drop_reasons = [{"stage_key": k, "reason": r, "kind": kind_of(r), "n": n}
                    for k in KEYS[1:] for r, n in reasons[k].most_common()]
    removed = {kind: sum(s["n_dropped_" + kind] for s in stages) for kind in KINDS}

    data = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": total,
        "stages": stages,
        "by_source_family": {f: brief(cross[f + "||*"]) for f in fams},
        "by_topic_top8": {t: brief(cross["*||" + t]) for t in top8},
        "drop_reasons": drop_reasons,
        "reason_labels": {k: {"en": v[0], "zh": v[1],
                              "kind": kind_of(k), "kind_why": KIND[k][1] if k in KIND else ""}
                          for k, v in REASON_LABELS.items()},
        "cross": cross,
        "notes": {
            "verify_level_among_strict_enacted": dict(verify_in_strict),
            "final_stage_with_zero_public_dataset_matches": mixed_no_public,
            "viable_strict_column_count": viable_col,
            "order_source": "scripts/build_derived_views.py (viable_strict expression)",
            "data_feasibility_at_min_gate_input": {
                "n": sum(feas_in.values()),
                "n_ge2": sum(n for x, n in feas_in.items() if x is not None and x >= 2),
                "n_ge3": sum(n for x, n in feas_in.items() if x is not None and x >= 3),
                "n_missing": feas_in[None]},
            "removed_total": total - n_out[-1],
            "removed_by_kind": removed,
            "kind_rule": "missing = the field a gate needs is absent/unresolved; judged = a rule or score was "
                         "evaluated and failed; unattributed = reason not in KIND (should be 0)",
        },
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # sanity
    outs = [s["n_out"] for s in stages]
    print("stage n_out:", outs)
    print("standalone :", [s["standalone_pass"] for s in stages])
    assert all(a >= b for a, b in zip(outs, outs[1:])), "stage counts not monotone"
    print("monotone non-increasing: OK")
    print(f"last stage {outs[-1]} vs viable_strict==1 count {viable_col}:",
          "MATCH" if outs[-1] == viable_col else "MISMATCH")
    assert outs[-1] == viable_col
    for s in stages[1:]:
        assert s["n_in"] - s["n_out"] == sum(reasons[s["key"]].values()), s["key"]
        assert s["n_in"] - s["n_out"] == sum(s["n_dropped_" + k] for k in KINDS), s["key"]
        print(f"  {s['key']:15s} dropped {s['n_in'] - s['n_out']:>8,} = missing {s['n_dropped_missing']:>8,}"
              f" + judged {s['n_dropped_judged']:>8,} + unattributed {s['n_dropped_unattributed']}")
    print("drop attribution sums to n_in - n_out per stage (and per cross cell): OK")
    assert sum(removed.values()) == total - outs[-1]
    print("removed by kind:", removed, f"of {total - outs[-1]:,}",
          {k: f"{v / (total - outs[-1]):.1%}" for k, v in removed.items()})
    print("size bytes:", OUT.stat().st_size)
    fe = data["notes"]["data_feasibility_at_min_gate_input"]
    assert fe["n"] == stages[4]["n_out"], fe
    print("notes:", data["notes"])


if __name__ == "__main__":
    main()
