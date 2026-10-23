#!/usr/bin/env python3
"""Build scripts/viz_site/data/curated_adoption.json from verified adoption tables.

Inputs (all under scripts/viz_site/data/curated_raw/):
  <id>.final.json            research + independent second-pass result per policy
  db_evidence_checked.json   hand-checked rows of the policy DB that are the enacting law of a state's adoption

Output: scripts/viz_site/data/curated_adoption.json (deterministic: same inputs -> same bytes).
No network, no CSV pass. Stdlib only.

Usage: python3 scripts/viz_site/make_curated_adoption.py [--out PATH]
"""
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
RAW = HERE / "data" / "curated_raw"
OUT = HERE / "data" / "curated_adoption.json"

NOTE = "Illustrative: adoption years from public trackers and statutes, verified by an independent second pass"
KEEP_VERIFICATION = {"verified", "fixed", "single_source"}
MAX_SOURCES = 3
MAX_REASON = 80
MAX_TITLE = 90

# display order is the order of this list
POLICIES = [
    {
        "id": "license_recognition",
        "short_en": "License recognition", "short_zh": "执照互认",
        "year_basis_en": "Year the law was signed (any-state recognition across most occupations).",
        "year_basis_zh": "法案签署年份（对大部分职业承认他州执照）。",
        "labor_topic_en": "Occupational licensing barriers to worker mobility and wages",
        "labor_topic_zh": "职业执照壁垒对劳动力流动与工资的影响",
        "excluded": {
            "NJ": "Disputed: 2013 endorsement law vs 2018/2019 versions, no defensible year",
        },
    },
    {
        "id": "nurse_licensure_compact",
        "short_en": "Nurse compact", "short_zh": "护士协定",
        "year_basis_en": "Year the bill joining the enhanced Nurse Licensure Compact was signed; start-up dates ignored.",
        "year_basis_zh": "加入增强版护士执照协定的法案签署年份，不计实际启用时间。",
        "labor_topic_en": "Licensing portability and nurse labor supply across state lines",
        "labor_topic_zh": "执照可携性与护士的跨州劳动供给",
        "excluded": {},
    },
    {
        "id": "medical_licensure_compact",
        "short_en": "Medical compact", "short_zh": "医师协定",
        "year_basis_en": "Year the bill joining the Interstate Medical Licensure Compact was signed; start-up dates ignored.",
        "year_basis_zh": "加入州际医师执照协定的法案签署年份，不计实际启用时间。",
        "labor_topic_en": "Licensing portability, physician supply and telehealth",
        "labor_topic_zh": "执照可携性、医师供给与远程医疗",
        "excluded": {},
    },
    {
        "id": "tnc_framework",
        "short_en": "Ride-hailing laws", "short_zh": "网约车立法",
        "year_basis_en": "Year the first statewide ride-hailing framework statute was signed; insurance-only laws and local rules not counted.",
        "year_basis_zh": "首部全州网约车框架法的签署年份；纯保险立法与地方规则不计。",
        "labor_topic_en": "Gig-economy entry, driver earnings and taxi-market effects",
        "labor_topic_zh": "零工经济进入、司机收入与出租车市场冲击",
        "excluded": {
            "CA": "Disputed: 2013 CPUC rule, 2014 insurance law, 2019 checks; no single act",
        },
    },
    {
        "id": "paid_sick_leave",
        "short_en": "Paid sick leave", "short_zh": "带薪病假",
        "year_basis_en": "Year the statewide mandate first took effect for covered employers.",
        "year_basis_zh": "全州强制规定对适用雇主首次生效的年份。",
        "labor_topic_en": "Paid sick days, job retention and worker health",
        "labor_topic_zh": "带薪病假、就业稳定与劳动者健康",
        "excluded": {},
    },
    {
        "id": "paid_family_leave",
        "short_en": "Paid family leave", "short_zh": "带薪家庭假",
        "year_basis_en": "Year the state insurance program first paid benefits (not when contributions began).",
        "year_basis_zh": "州保险项目首次发放待遇的年份（不是开始缴费的年份）。",
        "labor_topic_en": "Leave-taking, maternal employment and wages after childbirth",
        "labor_topic_zh": "休假、产后母亲就业与工资",
        "excluded": {},
    },
    {
        "id": "salary_history_ban",
        "short_en": "Salary-history bans", "short_zh": "薪资史禁令",
        "year_basis_en": "Year the ban took effect for private employers.",
        "year_basis_zh": "禁令对私营雇主生效的年份。",
        "labor_topic_en": "Pay transparency and the gender and racial wage gap",
        "labor_topic_zh": "薪酬透明与性别、种族工资差距",
        "excluded": {
            "AL": "Removed: law bars refusing to hire over non-disclosure, not asking",
        },
    },
    {
        "id": "min_wage_15",
        "short_en": "$15 minimum wage", "short_zh": "15美元最低工资",
        "year_basis_en": "Year the law or ballot measure setting a path to a $15 state minimum wage was enacted.",
        "year_basis_zh": "设定15美元州最低工资路径的法律（或公投）的颁布年份。",
        "labor_topic_en": "Employment and wage effects of higher wage floors",
        "labor_topic_zh": "更高工资下限对就业与工资的影响",
        "excluded": {},
    },
]

# --- source ranking: official statute/legislature pages first, then trackers, then the rest ---
_TIER1 = (  # nonprofit trackers, compact authorities, reference sites
    "ncsl.org", "nationalpartnership.org", "abetterbalance.org", "nelp.org", "epi.org",
    "archbridgeinstitute.org", "goldwaterinstitute.org", "ij.org", "csg.org", "ballotpedia.org",
    "legiscan.com", "justia.com", "americanprogress.org", "fsmb.org", "osteopathic.org",
    "imlcc.com", "imlcc.org", "nursecompact.com", "ncsbn.org", "badgerinstitute.org",
)
_TIER3 = (  # press releases, vendor blogs, wikis: last resort
    "wikipedia.org", "globenewswire.com", "yahoo.com", "vivian.com", "revealbackground.com",
    "newswise.com", "weatherbyhealthcare.com", "leaderstat.com", "billtrack50.com", "trackbill.com",
)
_OFFICIAL_HINTS = (
    "legislature", "legis.", "leg.", "azleg", "njleg", "ndlegis", "nmlegis", "wyoleg", "mgaleg",
    "malegislature", "revisor", "cga.ct", "capitol.", "flsenate", "dccouncil", "rilegislature",
    "leginfo", "lis.virginia", "oklegislature", "kslegislature", "legmt", "txcourts", "senate.", "house.",
)


def source_tier(url):
    host = urlparse(url).netloc.lower()
    if any(host == d or host.endswith("." + d) for d in _TIER3):
        return 3
    if any(host == d or host.endswith("." + d) for d in _TIER1):
        return 1
    if host.endswith(".gov") or host.endswith(".us") or ".state." in host or any(h in host for h in _OFFICIAL_HINTS):
        return 0
    return 2


def pick_sources(urls):
    seen, uniq = set(), []
    for u in urls:
        if isinstance(u, str) and u.startswith(("http://", "https://")) and u not in seen:
            seen.add(u)
            uniq.append(u)
    ranked = sorted(enumerate(uniq), key=lambda iu: (source_tier(iu[1]), iu[0]))
    return [u for _, u in ranked[:MAX_SOURCES]]


def die(msg):
    sys.exit("make_curated_adoption: " + msg)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        die("cannot read %s: %s" % (path, e))


def build_policy(meta, evidence_doc):
    pid = meta["id"]
    final = load(RAW / (pid + ".final.json"))
    for k in ("name_en", "name_zh", "definition_en", "definition_zh", "as_of", "states", "summary_sources"):
        if not final.get(k):
            die("%s.final.json missing %s" % (pid, k))

    states, excluded = {}, {}
    for st in sorted(final["states"]):
        v = final["states"][st]
        year, ver = v.get("year"), v.get("verification")
        if year is not None and ver in KEEP_VERIFICATION:
            if not isinstance(year, int) or isinstance(year, bool):
                die("%s %s year is not an int: %r" % (pid, st, year))
            srcs = pick_sources(v.get("sources") or [])
            if not srcs:
                die("%s %s has a year but no usable source URL" % (pid, st))
            states[st] = {"year": year, "verification": ver, "confidence": v.get("confidence") or "unknown", "sources": srcs}
        elif ver == "disputed" or (ver == "fixed" and year is None) or (year is not None and ver not in KEEP_VERIFICATION):
            reason = meta["excluded"].get(st) or (v.get("note") or "").strip()[:MAX_REASON] or "Excluded after verification"
            if len(reason) > MAX_REASON:
                die("%s %s excluded reason longer than %d" % (pid, st, MAX_REASON))
            excluded[st] = reason
    unknown_excl = set(meta["excluded"]) - set(excluded)
    if unknown_excl:
        die("%s: excluded reasons given for states that are not excluded: %s" % (pid, sorted(unknown_excl)))
    if not states:
        die("%s has no adopting states" % pid)

    years = [s["year"] for s in states.values()]
    ymin, ymax = min(years), max(years)
    for key, val in (("year_min", ymin), ("year_max", ymax)):
        if final.get(key) is not None and final[key] != val:
            die("%s: %s in final.json (%s) disagrees with the kept states (%s)" % (pid, key, final[key], val))

    ev_doc = evidence_doc["policies"].get(pid)
    if ev_doc is None:
        die("db_evidence_checked.json has no entry for %s" % pid)
    evidence, seen = [], set()
    for r in ev_doc["accepted"]:
        st = r["state"]
        if st not in states:
            die("%s evidence %s: state has no final adoption year" % (pid, st))
        if abs(int(r["year"]) - states[st]["year"]) > 1:
            die("%s evidence %s: DB year %s not within +-1 of %s" % (pid, st, r["year"], states[st]["year"]))
        if st in seen:
            die("%s evidence: duplicate state %s" % (pid, st))
        seen.add(st)
        title = (r["title"] or "").strip()
        if not title or len(title) > MAX_TITLE:
            die("%s evidence %s: title empty or longer than %d" % (pid, st, MAX_TITLE))
        if r["match"] not in ("enacting", "amendment"):
            die("%s evidence %s: bad match %r" % (pid, st, r["match"]))
        evidence.append({"state": st, "year": int(r["year"]), "policy_id": str(r["policy_id"]), "title": title, "match": r["match"]})
    evidence.sort(key=lambda e: (e["state"], e["year"], e["policy_id"]))

    ss, seen_u = [], set()
    for u in final["summary_sources"]:
        if isinstance(u, str) and u.startswith(("http://", "https://")) and u not in seen_u:
            seen_u.add(u)
            ss.append(u)

    return final["as_of"], {
        "id": pid,
        "name_en": final["name_en"],
        "name_zh": final["name_zh"],
        "short_en": meta["short_en"],
        "short_zh": meta["short_zh"],
        "definition_en": final["definition_en"],
        "definition_zh": final["definition_zh"],
        "year_basis_en": meta["year_basis_en"],
        "year_basis_zh": meta["year_basis_zh"],
        "labor_topic_en": meta["labor_topic_en"],
        "labor_topic_zh": meta["labor_topic_zh"],
        "n_states": len(states),
        "year_min": ymin,
        "year_max": ymax,
        "states": states,
        "excluded": excluded,
        "db_evidence": evidence,
        "summary_sources": ss,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    evidence_doc = load(RAW / "db_evidence_checked.json")
    as_ofs, policies = set(), []
    for meta in POLICIES:
        as_of, pol = build_policy(meta, evidence_doc)
        as_ofs.add(as_of)
        policies.append(pol)
    if len(as_ofs) != 1:
        die("policies disagree on as_of: %s" % sorted(as_ofs))

    doc = {
        "generated": evidence_doc.get("generated") or sorted(as_ofs)[0],
        "as_of": as_ofs.pop(),
        "note": NOTE,
        "policies": policies,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for p in policies:
        print("%-26s n_states=%2d  %d-%d  db_evidence=%d  excluded=%d" % (
            p["id"], p["n_states"], p["year_min"], p["year_max"], len(p["db_evidence"]), len(p["excluded"])))
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
