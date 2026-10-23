#!/usr/bin/env python3
"""Generate partials/story_grid.html, the staggered-adoption illustration of the homepage story (stdlib only, 3.9+).

    python3 scripts/viz_site/make_story_assets.py                  # (re)write partials/story_grid.html
    python3 scripts/viz_site/make_story_assets.py --check          # exit 1 if the file is stale (build.py --check does this too)
    python3 scripts/viz_site/make_story_assets.py --verify-claims  # re-check the hand-typed proof numbers against their source files

Input : deploy_output/adoption_data.json   families[*].states[*] = {code, first_year, n, ...}, .never, .basis
        deploy_output/coverage_data.json   years, states[code].not_observed / .total
Output: scripts/viz_site/partials/story_grid.html   <figure> (language-neutral SVG + legend) and <p class="st-cap">;
        every sentence is a {{t:..}} / {{tf:..}} placeholder, so the file serves both languages.

SELECTION RULE (deterministic; the same data always gives the same picture)
  1. Observed states only: coverage_data.states[code].not_observed is false (29 of the 51 jurisdictions). A state with
     thin coverage is never drawn as "no matching law".
  2. Families with basis == "enacted" (an enacted law, not a bill), window Y0..Y1 = coverage_data.years (2016..2026).
  3. Interior adopter = observed state whose first_year lies strictly inside the window (Y0 < first_year < Y1).
     A first year equal to Y0 is left-censored (we cannot see earlier laws) and Y1 is a partial year, so neither
     gives a usable switch-on date.
  4. The family with the clearest staggered pattern = most distinct interior adoption years; ties broken by more
     interior adopters, then by family name. It also needs at least MIN_NEVER observed states with no matching law
     at all (a comparison group).
  5. Rows: one adopter per distinct interior year (the observed state with the most matching laws in that year, then
     alphabetical), ordered by year and capped at MAX_ADOPTERS; then NEVER_ROWS observed never-adopting states, the
     best-covered first (largest coverage total), shown alphabetically. The count of rows is therefore about 12.
  Cell colours: gray = no matching law yet, amber = the year of the state's first matching law in the window,
  blue = every year after it. Labels are state codes and years (language-neutral).

--verify-claims reads the source files of the numbers that are typed into i18n/home_story.*.json or the partial
(examples, stability) and reports OK / MISMATCH / SKIPPED (a source file that is not on this machine).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEPLOY = ROOT / "deploy_output"
OUT = HERE / "partials" / "story_grid.html"

MIN_NEVER = 4
NEVER_ROWS = 4
MAX_ADOPTERS = 9

# geometry of the SVG (user units); the figure scales with its container
X0, STEP, CELL_W = 34, 36, 32
ROW_PITCH, ROW_H = 18, 14
HEAD_Y, TOP = 11, 18


# --------------------------------------------------------------------------- selection

def _load(name: str):
    return json.loads((DEPLOY / (name + ".json")).read_text(encoding="utf-8-sig"))


def select():
    adoption, coverage = _load("adoption_data"), _load("coverage_data")
    y0, y1 = coverage["years"]
    cov = coverage["states"]
    observed = sorted(c for c, v in cov.items() if not v["not_observed"])
    obs = set(observed)
    candidates = []
    for fam in adoption["families"]:
        if fam.get("basis") != "enacted":
            continue
        adopters = {s["code"]: s for s in fam["states"] if s["code"] in obs}
        interior = [s for s in adopters.values() if y0 < s["first_year"] < y1]
        years = {s["first_year"] for s in interior}
        never = sorted(obs - {s["code"] for s in fam["states"]})
        if years and len(never) >= MIN_NEVER:
            candidates.append((-len(years), -len(interior), fam["family"], fam, interior, never))
    if not candidates:
        raise SystemExit("no family satisfies the selection rule")
    _, _, _, fam, interior, never = min(candidates, key=lambda c: c[:3])

    by_year = {}
    for s in interior:
        cur = by_year.get(s["first_year"])
        if cur is None or (-s["n"], s["code"]) < (-cur["n"], cur["code"]):
            by_year[s["first_year"]] = s
    rows = [by_year[y] for y in sorted(by_year)][:MAX_ADOPTERS]
    controls = sorted(sorted(never, key=lambda c: (-cov[c]["total"], c))[:NEVER_ROWS])
    return {"family": fam, "rows": rows, "controls": controls, "years": (y0, y1), "observed": len(observed),
            "total": len(cov)}


# --------------------------------------------------------------------------- rendering

def _rect(cls: str, x: int, y: int) -> str:
    return '<rect class="%s" x="%d" y="%d" width="%d" height="%d" rx="3"/>' % (cls, x, y, CELL_W, ROW_H)


def _row(code: str, first, y0: int, y1: int, y: int) -> str:
    cells = []
    for i, yr in enumerate(range(y0, y1 + 1)):
        if first is None or yr < first:
            cls = "st-u"
        elif yr == first:
            cls = "st-e"
        else:
            cls = "st-t"
        cells.append(_rect(cls, X0 + i * STEP, y))
    return '<text class="st-l" x="%d" y="%d" text-anchor="end">%s</text>%s' % (X0 - 6, y + ROW_H - 3, code, "".join(cells))


def render_partial() -> str:
    sel = select()
    fam, rows, controls = sel["family"], sel["rows"], sel["controls"]
    y0, y1 = sel["years"]
    fam_path = "adoption_data.families[family=%s]" % fam["family"]
    n_cols = y1 - y0 + 1
    width = X0 + n_cols * STEP - (STEP - CELL_W)

    out = []
    y = TOP
    heads = []
    for i, yr in enumerate(range(y0, y1 + 1)):
        label = "’%02d%s" % (yr % 100, "*" if yr == y1 else "")
        heads.append('<text class="st-y" x="%d" y="%d">%s</text>' % (X0 + i * STEP + CELL_W // 2, HEAD_Y, label))
    out.append("".join(heads))
    for s in rows:
        out.append(_row(s["code"], s["first_year"], y0, y1, y))
        y += ROW_PITCH
    y += 4
    out.append('<text class="st-g" x="%d" y="%d">{{tf:story.grid.none|from=raw(coverage_data.years.0)|to=raw(coverage_data.years.1)}}</text>' % (X0, y + 7))
    y += 14
    for code in controls:
        out.append(_row(code, None, y0, y1, y))
        y += ROW_PITCH
    height = y - 4 + 2

    first_years = [s["first_year"] for s in rows]
    desc = ("{{tf:story.grid.desc|adopt=%d|first=raw(%d)|last=raw(%d)|none=%d}}"
            % (len(rows), min(first_years), max(first_years), len(controls)))
    cap = ("{{tf:story.grid.cap_html|family=text(%s.family)|laws=%s.n_policies|states=%s.n_states"
           "|from=raw(coverage_data.years.0)|to=raw(coverage_data.years.1)"
           "|shown=%d|obs=countnot(coverage_data.states,not_observed)}}"
           % (fam_path, fam_path, fam_path, len(rows) + len(controls)))

    lines = [
        "<!-- GENERATED by scripts/viz_site/make_story_assets.py from deploy_output/adoption_data.json and coverage_data.json.",
        "     Do not edit by hand: re-run the script (build.py --check fails when this file is stale). Selection rule: see the",
        "     script's docstring. Family: %s (%d interior adopters in the observed states, %d distinct first years), rows: %s;"
        % (fam["family"], len(rows), len(set(first_years)), ", ".join("%s %d" % (s["code"], s["first_year"]) for s in rows)),
        "     comparison rows (observed, no matching law in the window): %s. -->" % ", ".join(controls),
        '<figure class="st-fig">',
        '<svg class="st-grid" viewBox="0 0 %d %d" role="img" aria-labelledby="st-grid-t st-grid-d">'
        '<title id="st-grid-t">{{t:story.grid.title}}</title><desc id="st-grid-d">%s</desc>' % (width, height, desc),
    ]
    lines.extend(out)
    lines += [
        "</svg>",
        '<div class="st-legend"><span><i class="st-sw st-e"></i>{{t:story.grid.lg_first}}</span>'
        '<span><i class="st-sw st-t"></i>{{t:story.grid.lg_after}}</span>'
        '<span><i class="st-sw st-u"></i>{{t:story.grid.lg_none}}</span></div>',
        "</figure>",
        '<p class="st-cap">%s</p>' % cap,
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- claim verification

def _csv_rows(path: Path, wanted: set, key: str):
    csv.field_size_limit(sys.maxsize)
    found = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row[key] in wanted and row[key] not in found:
                found[row[key]] = row
    return found


# Hand-typed in partials/home_story.html (example cards): policy_id, design label, score, jurisdiction, year, topic
EXAMPLES = [
    ("OR-SB802-Regular Session", "RD", "4.3", "OR State", "2017", "political_institutions",
     "Allows person who is at least 16 years of age to register to vote."),
    ("CA-AB1076-Regular Session", "DID", "4.7", "CA State", "2023", "labor_market",
     "Contracts in restraint of trade: noncompete agreements."),
    ("fedreg-E9-7052", "DID", "4.5", "US Federal", "2009", "veterans_military", "Post-9/11 GI Bill"),
]


def verify_claims() -> int:
    bad = 0

    def report(ok, what):
        nonlocal bad
        if ok is None:
            print("SKIPPED   " + what)
        else:
            print(("OK        " if ok else "MISMATCH  ") + what)
            bad += 0 if ok else 1

    # 1. example cards
    big, derived = ROOT / "data/output/policy_db_20260415_483k.csv", ROOT / "data/output/derived/derived_views.csv"
    if big.is_file() and derived.is_file():
        ids = {e[0] for e in EXAMPLES}
        dv = _csv_rows(derived, ids, "policy_id")
        rows = _csv_rows(big, ids, "policy_id")
        topics = set()
        for pid, design, score, jur, year, topic, title in EXAMPLES:
            r, d = rows.get(pid), dv.get(pid)
            ok = bool(r and d) and (r["best_design"], r["composite_score"], r["jurisdiction"], r["year"],
                                    r["policy_topic"], r["title"]) == (design, score, jur, year, topic, title) \
                and d["viable_strict"] == "1" and r["status"] == "enacted" and r["confidence"] == "high" \
                and float(d["composite_score"]) >= 4.3
            topics.add(topic)
            report(ok, "example card %s: %s, %s, score %s, viable_strict=1, enacted, confidence high" % (pid, design, jur, score))
        kinds = {e[3] == "US Federal" for e in EXAMPLES}
        report(len(topics) == 3 and kinds == {True, False} and {e[1] for e in EXAMPLES} == {"RD", "DID"},
               "examples: 3 distinct topics, RD and DID both present, federal and state both present")
    else:
        report(None, "example cards (policy_db_20260415_483k.csv / derived_views.csv not on this machine)")

    # 2. repeat-run stability (home story proof card b)
    tex, rep = ROOT / "paper/tables/stability_table.tex", ROOT / "scripts/stability_test_report.json"
    runs = ROOT / "scripts_ablation_qwen/stability_runs_NEW_qwen.jsonl"
    if tex.is_file():
        t = tex.read_text(encoding="utf-8")
        report(bool(re.search(r"design\\_type & categorical & fleiss_kappa & 0\.873 \[0\.815, 0\.926\]", t))
               and "Qwen 3.6 Plus" in t and "100 bills" in t and "5 runs" in t,
               "stability_table.tex: design_type Fleiss kappa 0.873 [0.815, 0.926], Qwen 3.6 Plus, 100 bills x 5 runs")
    else:
        report(None, "stability_table.tex")
    if rep.is_file():
        r = json.loads(rep.read_text(encoding="utf-8"))
        report((r["n_bills"], r["repeats_per_bill"], r["success_count"], r["crash_count"],
                r["json_parse_error_count"], r["validation_error_count"]) == (100, 5, 500, 0, 0, 0),
               "stability_test_report.json: 100 bills x 5 repeats, 500 successes, no crash / parse / validation errors")
    else:
        report(None, "stability_test_report.json")
    if runs.is_file():
        by, statuses = {}, set()
        for line in runs.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            by.setdefault(rec["policy_id"], []).append(rec)
            statuses.add(rec["status"])
        pre = sum(1 for v in by.values() if all(x["result"].get("skipped_prefilter") for x in v))
        report(len(by) == 100 and all(len(v) == 5 for v in by.values()) and statuses == {"ok"} and pre == 9,
               "stability_runs_NEW_qwen.jsonl: 100 bills x 5 runs all ok; 9 bills skipped by the ceremonial pre-filter")
        report(abs(_fleiss(by, "design_type") - 0.873) < 0.0005, "stability_runs_NEW_qwen.jsonl: recomputed Fleiss kappa of design_type = 0.873")
    else:
        report(None, "stability_runs_NEW_qwen.jsonl")
    return 1 if bad else 0


def _fleiss(by: dict, field: str) -> float:
    from collections import Counter
    n_raters, totals, agree = 5, Counter(), []
    for runs in by.values():
        c = Counter(str(x["result"].get(field)) for x in runs)
        agree.append((sum(v * v for v in c.values()) - n_raters) / (n_raters * (n_raters - 1)))
        totals.update(c)
    p_bar = sum(agree) / len(agree)
    p_e = sum((v / (len(by) * n_raters)) ** 2 for v in totals.values())
    return (p_bar - p_e) / (1 - p_e)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 when partials/story_grid.html is stale; write nothing")
    ap.add_argument("--verify-claims", action="store_true", help="re-check the hand-typed numbers against their source files")
    args = ap.parse_args(argv)
    if args.verify_claims:
        return verify_claims()
    fresh = render_partial()
    if args.check:
        current = OUT.read_text(encoding="utf-8-sig").replace("\r\n", "\n") if OUT.is_file() else None
        if current != fresh:
            print("partials/story_grid.html is stale: run python3 scripts/viz_site/make_story_assets.py", file=sys.stderr)
            return 1
        print("story_grid.html is up to date")
        return 0
    OUT.write_text(fresh, encoding="utf-8")
    print("wrote %s" % OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
