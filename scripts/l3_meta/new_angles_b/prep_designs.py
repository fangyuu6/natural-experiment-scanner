#!/usr/bin/env python3
"""T233 prep (rule id EX-1, exploratory): read-only export of the 77 estimated designs of the r7 refresh set.

Writes (folder data/l3_meta/new_angles_20261021/states_decide/inputs/):
  designs.csv        one row per estimated design: headline estimate, se, noise_sd, domain, family, block (treatment identity), cohort counts
  cohort_cells.csv   one row per group-time cell: design, g_raw, g_cal (calendar year of the cohort), event_time, att, n_treated, n_control, post
  cohorts.csv        one row per design x cohort: g_cal, n_treated, states (| separated, if known), state_source, att_ev0_3, n_cells_ev0_3, max_event_time
  panel_outcomes.csv.gz   design, state, year, value  (raw outcome series of the registered primary; only designs whose frozen package replays)
  PREP_RECEIPT.json  counts and sha256 of inputs

Nothing under scripts/l2_pipeline or data/l2_pipeline is written. No network.
Run: PYTHONDONTWRITEBYTECODE=1 .venv_did/bin/python -I -B scripts/l3_meta/new_angles_b/prep_designs.py
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/l3_meta"))
sys.path.insert(0, str(ROOT))
import replay_l2 as R  # noqa: E402

LIVE = ROOT / "data/l3_meta/refresh/20261021_r1/live/l2_set_live.json"
N1SETS = ROOT / "data/l3_meta/n1_sets_v2_20261019/n1_sets_v2.json"
OUT = ROOT / "data/l3_meta/new_angles_20261021/states_decide/inputs"


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def walk(cur, parts):
    """Resolve a JSON pointer whose keys may themselves contain '/'."""
    if not parts:
        return cur
    for i in range(len(parts), 0, -1):
        k = "/".join(parts[:i])
        if isinstance(cur, dict) and k in cur:
            w = walk(cur[k], parts[i:])
            if w is not None:
                return w
    return None


def find_key(o, key, depth=0):
    if depth > 6:
        return None
    if isinstance(o, dict):
        if key in o:
            return o[key]
        for v in o.values():
            r = find_key(v, key, depth + 1)
            if r is not None:
                return r
    return None


def result_node(r, pointer, estimate=None):
    parts = [p for p in pointer.split("/") if p]
    node = walk(r, parts)
    if node is None and parts[:1] == ["primary_estimates"] and isinstance(r.get("primary_estimates"), list):
        tail = "/".join(parts[1:])
        lst = r["primary_estimates"]
        hit = [x for x in lst if x.get("id") == tail]
        if not hit and estimate is not None:
            hit = [x for x in lst if isinstance(x.get("overall"), dict) and abs((x["overall"].get("att") or 1e9) - estimate) < 1e-12]
        node = hit[0] if hit else None
        if isinstance(node, dict) and isinstance(node.get("result"), dict) and "group_time" in node["result"]:
            node = dict(node["result"], calendar_adoptions=node.get("calendar_adoptions"), calendar_years=node.get("calendar_years"))
        return node
    if parts and parts[-1] == "overall":
        node = walk(r, parts[:-1])
    if node is None and parts:
        # a few prototype pointers stop one level above the estimate: take the first descendant holding group_time
        node = walk(r, parts)
    if isinstance(node, dict) and isinstance(node.get("result"), dict) and "group_time" in node["result"]:
        node = node["result"]
    return node


def first_group_time_descendant(o, depth=0):
    if depth > 7 or not isinstance(o, (dict, list)):
        return None
    if isinstance(o, dict):
        if isinstance(o.get("group_time"), list) and o["group_time"]:
            return o
        if isinstance(o.get("result"), dict) and isinstance(o["result"].get("group_time"), list):
            return o["result"]
        vals = o.values()
    else:
        vals = o
    for v in vals:
        r = first_group_time_descendant(v, depth + 1)
        if r is not None:
            return r
    return None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    live = json.loads(LIVE.read_text())
    est = [e for e in live["designs"] if e["status"] == "estimated"]
    n1 = {d["design_id"]: d for d in json.loads(N1SETS.read_text())["designs"]}
    l2 = R._import_l2()

    # block = connected components of designs sharing policy_family or policy_name
    parent = {e["design_id"]: e["design_id"] for e in est}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for key in ("policy_family", "policy_name"):
        seen = {}
        for e in est:
            k = e[key]
            if k in seen:
                union(e["design_id"], seen[k])
            else:
                seen[k] = e["design_id"]
    # same law, different record names (checked by hand against policy_name / family text)
    by_id = {e["design_id"]: e for e in est}
    for a, b in (("prototype:adult_use_cannabis", None), ("prototype:primary_seat_belt", None)):
        pass
    fam_of = {}
    for e in est:
        fam_of.setdefault(find(e["design_id"]), len(fam_of))

    designs, cells, cohorts, panel_rows = [], [], [], []
    notes = []
    for e in est:
        did = e["design_id"]
        po = e["primary_outcomes"][0]
        src = e["source"]
        r = json.loads(Path(ROOT / src["path"]).read_text())
        node = result_node(r, src["pointer"], po.get("estimate"))
        gt = node.get("group_time") if isinstance(node, dict) else None
        if gt is None:
            node2 = first_group_time_descendant(r.get("primary_estimates", r))
            gt = node2.get("group_time") if node2 else None
            node = node2 if node2 else node
        if isinstance(gt, dict) and gt.get("moved_to"):
            import gzip as _gz
            arch = json.load(_gz.open(ROOT / os.path.dirname(src["path"]) / gt["moved_to"]))
            cur = arch
            for part in [q for q in gt["path"].split("/") if q]:
                cur = cur[int(part)] if isinstance(cur, list) else cur[part]
            gt = cur
        pkg_dir = ROOT / os.path.dirname(src["path"])
        loaded = None
        if (pkg_dir / "FREEZE_FINAL.json").exists():
            try:
                loaded = R.load_package(pkg_dir, l2)
            except Exception as ex:  # replay fails for a few packages; fall back to the stored sets
                notes.append(f"{did}: replay failed ({type(ex).__name__})")
        # g -> calendar
        cal = {}
        if loaded is not None:
            per = loaded["design"]["periods"]
            py = loaded["declaration"].get("period_years")
            cal = dict(zip(per, py)) if py else {p: p for p in per}
            cal_basis = "package_periods"
        else:
            pidx = find_key(r, "period_index_to_year")
            if isinstance(node, dict) and isinstance(node.get("calendar_years"), list) and node.get("calendar_adoptions") and not isinstance(pidx, dict) and gt and min(c["g"] for c in gt) < 100:
                cal = {i: y for i, y in enumerate(node["calendar_years"])}
                cal_basis = "variant_calendar_years"
            elif isinstance(pidx, dict):
                cal = {int(k): v for k, v in pidx.items()}
                cal_basis = "result_period_index_to_year"
            else:
                py = find_key(r, "period_years")
                if isinstance(py, list):
                    cal = {i: y for i, y in enumerate(py)}
                    cal_basis = "result_period_years"
                else:
                    cal_basis = "identity_if_year"

        def g_cal(g):
            if g in cal:
                return cal[g]
            if g >= 1900 and g < 2100:
                return g
            if cal_basis == "identity_if_year" and g >= 1900 and g < 2100:
                return g
            if g > 4000 and g < 9000:  # quarterly index (year*4+q)
                return g / 4.0
            return None

        # states per cohort
        states = {}
        state_source = ""
        if loaded is not None:
            ad = loaded["design"]["adoption"]
            for s, a in ad.items():
                if a is not None:
                    states.setdefault(a, []).append(s)
            state_source = "package_adoption"
        elif isinstance(node, dict) and isinstance(node.get("calendar_adoptions"), dict) and any(v for v in node["calendar_adoptions"].values()):
            rev = {y: i for i, y in cal.items()}
            for sname, yr in node["calendar_adoptions"].items():
                if yr:
                    states.setdefault(rev.get(int(yr), int(yr)), []).append(sname)
            state_source = "result_calendar_adoptions"
        elif did in n1 and n1[did].get("T_cohorts"):
            for k, v in n1[did]["T_cohorts"].items():
                states[int(k) if str(k).lstrip("-").isdigit() else k] = list(v)
            state_source = "n1_sets_v2"
        # cells
        by_g = {}
        for c in gt or []:
            g = c["g"]
            by_g.setdefault(g, []).append(c)
            cells.append(dict(design_id=did, g_raw=g, g_cal=g_cal(g), event_time=c["event_time"], att=c["att"],
                              n_treated=c.get("n_treated"), n_control=c.get("n_control"), post=int(bool(c.get("post")))))
        for g, cs in sorted(by_g.items()):
            ev = [c["att"] for c in cs if 0 <= c["event_time"] <= 3 and c["att"] is not None]
            cohorts.append(dict(design_id=did, g_raw=g, g_cal=g_cal(g), n_treated=max((c.get("n_treated") or 0) for c in cs),
                                states="|".join(sorted(states.get(g, []))), state_source=state_source if g in states else "",
                                att_ev0_3=(sum(ev) / len(ev)) if ev else "", n_cells_ev0_3=len(ev),
                                max_event_time=max(c["event_time"] for c in cs)))
        # raw outcomes
        n_panel = 0
        if loaded is not None:
            per = loaded["design"]["periods"]
            py = loaded["declaration"].get("period_years")
            m = dict(zip(per, py)) if py else {p: p for p in per}
            for (u, t), v in loaded["outcomes"].items():
                panel_rows.append((did, u, m.get(t), v))
                n_panel += 1
        designs.append(dict(
            design_id=did, policy_name=e["policy_name"], policy_family=e["policy_family"], block=fam_of[find(did)], domain=e["domain"],
            source_group=e["source_group"], topic_cluster=e.get("topic_cluster"), outcome_id=po.get("outcome_id"), outcome_label=(po.get("label") or "")[:200],
            outcome_unit=po.get("unit"), estimate=po.get("estimate"), se=po.get("se"), noise_sd=po.get("noise_sd"), noise_cutoff=po.get("noise_cutoff"),
            beyond_noise=int(bool(po.get("beyond_noise"))), n_group_time_cells=len(gt or []), n_cohorts=len(by_g),
            n_treated_total=sum(max((c.get("n_treated") or 0) for c in cs) for cs in by_g.values()),
            calendar_basis=cal_basis, state_source=state_source, panel_rows=n_panel, result_path=src["path"]))

    def write(name, rows):
        with open(OUT / name, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    write("designs.csv", designs)
    write("cohort_cells.csv", cells)
    write("cohorts.csv", cohorts)
    with gzip.open(OUT / "panel_outcomes.csv.gz", "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["design_id", "state", "year", "value"])
        w.writerows(panel_rows)
    receipt = dict(
        inputs={str(p.relative_to(ROOT)): sha256_file(p) for p in (LIVE, N1SETS)},
        counts=dict(designs=len(designs), cells=len(cells), cohorts=len(cohorts), panel_rows=len(panel_rows),
                    designs_with_panel=sum(1 for d in designs if d["panel_rows"]), designs_with_states=sum(1 for d in designs if d["state_source"]),
                    cohorts_with_states=sum(1 for c in cohorts if c["states"]), cohorts_without_calendar=sum(1 for c in cohorts if c["g_cal"] is None),
                    blocks=len({d["block"] for d in designs})),
        notes=notes)
    (OUT / "PREP_RECEIPT.json").write_text(json.dumps(receipt, indent=1))
    print(json.dumps(receipt["counts"]), notes)


if __name__ == "__main__":
    main()
