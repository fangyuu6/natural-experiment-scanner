#!/usr/bin/env python3
"""L3 package B, input 1: state-year covariate file v1 (+ state land-border adjacency, decennial urban table).

Definitions follow the L2 export manifest data/l2_pipeline/autopilot/c64/STATE_COVARIATES.json (read-only) and
PREREG_L3_META_ADDENDUM_V2.md N1/N7 (covariates 1-4) / N8 (adjacency):
  1 democratic/republican unified control, two clocks        Klarner (<=2011); two-collector reconciled file (2011-2024)
      jan1    governor on 1 January (Klarner prior-year governor <=2010; collectors' Jan-1 governor 2011-2024) + chambers in session
      session governor serving at the session start (Klarner as published <=2011; collectors' Dec-31 governor 2012-2024) + chambers in session
  2 citizen ideology (Berry et al. citi6016), last observed year carried forward
  3 ln real per-capita personal income = ln(BEA SAINC1 line 3 / (CPI-U_t / CPI-U_2024))
  4 urban population share, nearest decennial census (equal distance -> the EARLIER census)
  5 land-border adjacency from the Census county adjacency file 2026

Public data only.  Reads local files (data/l4 and the new raw files under data/l3_meta/covariates/raw); no network.
Run (interpreter with pandas for the Stata file):
  .venv_did/bin/python -I -B scripts/l3_meta/build_covariates.py
Outputs are NEW files under data/l3_meta/covariates/ and are never overwritten (write_new refuses on a byte difference).
"""
from __future__ import annotations

import collections
import csv
import hashlib
import io
import json
import math
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inputs_b_common as C  # noqa: E402

ROOT = C.ROOT
L4 = ROOT / "data/l4"
OUT = ROOT / "data/l3_meta/covariates"
RAW = OUT / "raw"
YEARS = list(range(1960, 2026))          # panel 1960-2025
CPI_REF_YEAR = 2024
VERSION = "v2"

INPUTS = {}                                 # role -> {path, bytes, sha256, ...}


def note_input(role, path, **extra):
    p = Path(path)
    INPUTS[role] = {"path": C.rel(p), "bytes": p.stat().st_size, "sha256": C.sha256_file(p), **extra}
    return p


def fnum(x):
    try:
        if x is None or str(x).strip() in ("", "(NA)", "NA", "nan"):
            return None
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------
# 1. party control
# ---------------------------------------------------------------------------------------------
def load_klarner():
    p = note_input("klarner_main", L4 / "klarner_partisan_balance/raw/Partisan_Balance_For_Use2011_06_09b.xlsx",
                   source="Klarner, State Partisan Balance Data 1937-2011, doi:10.7910/DVN/LZHMG3 (CC0)")
    name, sx = C.xlsx_sheets(p)[0]
    rows = C.xlsx_rows(p, sx)
    hdr = rows[0]
    ix = {h: i for i, h in enumerate(hdr)}
    out = {}
    for r in rows[1:]:
        if not r:
            continue
        def g(k):
            i = ix[k]
            return r[i] if i < len(r) else None
        st = g("state")
        if not st:
            continue
        usps = C.STATE_NAME_TO_USPS[st]
        year = int(float(g("year")))
        def lab(v, kind):
            v = fnum(v)
            if v is None:
                return None
            if kind == "gov":
                return {1.0: "D", 0.0: "R", 0.5: "I"}.get(v)
            return {1.0: "D", 0.0: "R", 0.5: "split"}.get(v)
        out[(usps, year)] = {"gov": lab(g("govparty_c"), "gov"), "sen": lab(g("sen_cont_alt"), "ch"),
                             "hs": lab(g("hs_cont_alt"), "ch"), "dem_unified": fnum(g("dem_unified")),
                             "rep_unified": fnum(g("rep_unified"))}
    return out


def load_collectors():
    p = note_input("collectors_reconciled", L4 / "political_control_2011_2024/reconciled_wide.csv",
                   source="two-collector reconciliation of NCSL/NGA state party control 2011-2024 "
                          "(data/l4/political_control_2011_2024/RECONCILE_REPORT.md); disputed cells blank")
    out = {}
    with open(p, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[(r["state"], int(r["year"]))] = r
    return out


def _cat(gov, hs, sen):
    if None in (gov, hs, sen):
        return None, None, "unknown"
    du = 1 if (gov == "D" and hs == "D" and sen == "D") else 0
    ru = 1 if (gov == "R" and hs == "R" and sen == "R") else 0
    return du, ru, "D_unified" if du else "R_unified" if ru else "divided"


def party_rows(usps, year, klar, coll):
    """Both clocks.  Returns dict(jan1=(du,ru,cat,gov), session=(du,ru,cat,gov), house, senate, source, clock, note)."""
    blank = dict(jan1=(None, None, "unknown", None), session=(None, None, "unknown", None), house=None, senate=None,
                 source="none", clock="none", note="")
    if usps == "DC":
        return {**blank, "note": "DC_no_state_legislature_not_coded"}
    if year > 2024:
        return {**blank, "note": "after_2024_not_coded"}
    # chambers in session of year t
    if year <= 2011:
        k = klar.get((usps, year)) or {}
        hs, sen = k.get("hs"), k.get("sen")
        csrc = "klarner_main"
    else:
        c = coll.get((usps, year)) or {}
        hs, sen = (c.get("house_majority") or None), (c.get("senate_majority") or None)
        csrc = "collectors_reconciled"
    # governors
    k_t = (klar.get((usps, year)) or {}).get("gov") if year <= 2011 else None
    k_prev = (klar.get((usps, year - 1)) or {}).get("gov") if year <= 2010 else None
    c_t = coll.get((usps, year)) or {}
    gov_jan1 = k_prev if year <= 2010 else (c_t.get("gov_party_jan1") or None)
    gov_sess = k_t if year <= 2011 else (c_t.get("gov_party_dec31") or None)
    src = ("klarner_main(prior-year governor for jan1)" if year <= 2010 else
           "klarner_main(chambers, session governor)+collectors_reconciled(jan1 governor)" if year == 2011 else "collectors_reconciled")
    clock = ("jan1 governor = Klarner prior-year governor; chambers = Klarner in-session" if year <= 2010 else
             "jan1 governor = collectors Jan-1; chambers = Klarner in-session" if year == 2011 else
             "jan1 governor = collectors Jan-1; chambers = collectors session majority (NCSL)")
    note = ""
    if usps == "NE":
        return dict(jan1=(0, 0, "nonpartisan_NE", gov_jan1), session=(0, 0, "nonpartisan_NE", gov_sess), house="NP", senate="NP",
                    source=src, clock=clock, note="NE_unicameral_nonpartisan_UD_UR_set_to_0")
    j, s_ = _cat(gov_jan1, hs, sen), _cat(gov_sess, hs, sen)
    if j[2] == "unknown" or s_[2] == "unknown":
        note = "missing_or_disputed_cell_blank"
    return dict(jan1=(*j, gov_jan1), session=(*s_, gov_sess), house=hs, senate=sen, source=src, clock=clock, note=note)


# ---------------------------------------------------------------------------------------------
# 2. Berry citizen ideology
# ---------------------------------------------------------------------------------------------
def load_berry():
    import pandas as pd  # venv interpreter
    p = note_input("berry_ideology", L4 / "berry_ideology/raw/stateideology_v2018.dta_.zip",
                   source="Berry, Ringquist, Fording, Hanson; citizen ideology citi6016 (1960-2016), update of 2018-06-18")
    z = zipfile.ZipFile(p)
    member = [n for n in z.namelist() if n.endswith(".dta") and not n.startswith("__MACOSX")][0]
    df = pd.read_stata(io.BytesIO(z.read(member)))
    df = df[df["state"].notna()].copy()
    out, name_check = {}, collections.Counter()
    for r in df.itertuples(index=False):
        code, year = int(r.state), int(r.year)
        usps = C.BERRY_CODE_TO_USPS[code]
        nm = (r.statename or "").strip()
        if nm:
            name_check["name_matches_alphabetical_code" if nm == C.BERRY_CODE_TO_NAME[code] else "NAME_MISMATCH"] += 1
        v = None if (r.citi6016 is None or (isinstance(r.citi6016, float) and math.isnan(r.citi6016))) else float(r.citi6016)
        out[(usps, year)] = v
    return out, dict(name_check)


# ---------------------------------------------------------------------------------------------
# 3. BEA per-capita personal income and CPI-U
# ---------------------------------------------------------------------------------------------
def load_bea():
    p = note_input("bea_sainc1", L4 / "covariates_public/raw/bea_sainc/SAINC1__ALL_AREAS_1929_2025.csv",
                   source="BEA Regional Data SAINC1 (zip SAINC.zip, last updated 2026-09-30), line 3 per capita personal income, dollars")
    rd = list(csv.reader(open(p, encoding="latin-1", newline=""), skipinitialspace=True))
    hdr = rd[0]
    yidx = {int(h): i for i, h in enumerate(hdr) if re.fullmatch(r"\d{4}", h)}
    out = {}
    for r in rd[1:]:
        if len(r) < 9 or r[4].strip() != "3":
            continue
        fips = r[0].strip().strip('"')[:2]
        if fips not in C.FIPS_TO_USPS:
            continue
        usps = C.FIPS_TO_USPS[fips]
        for y, i in yidx.items():
            out[(usps, y)] = fnum(r[i]) if i < len(r) else None
    return out


def load_cpi():
    files = sorted(list((L4 / "covariates_public/raw/bls_cpi").glob("*.json")) + list((RAW / "bls_cpi_1960_1969").glob("*.json"))
                   + list((RAW / "bls_cpi_1970_1974").glob("*.json")))
    cpi = {}
    for k, f in enumerate(files):
        note_input(f"bls_cpi_{k:02d}", f, source="BLS Public Data API v1, CUUR0000SA0 CPI-U all items US city average NSA 1982-84=100")
        d = json.load(open(f))
        assert d["status"] == "REQUEST_SUCCEEDED", f
        for x in d["Results"]["series"][0]["data"]:
            if x["period"] == "M13":
                y, v = int(x["year"]), float(x["value"])
                if y in cpi and cpi[y] != v:
                    raise ValueError(f"CPI conflict {y}: {cpi[y]} vs {v}")
                cpi[y] = v
    return cpi


# ---------------------------------------------------------------------------------------------
# 4. urban share, decennial
# ---------------------------------------------------------------------------------------------
_NUMTOK = re.compile(r"^(?:-|\d[\d,]*(?:\.\d+)?%?)$")


def parse_urpop_1900_1990():
    p = note_input("census_urban_1900_1990", RAW / "census_urban_1900_1990/urpop0090.txt",
                   source="Census Table 1 Urban and Rural Population 1900 to 1990 (Oct. 1995)")
    names = set(C.STATE_NAME_TO_USPS)
    years, res = None, {}
    for ln in open(p, encoding="latin-1").read().split("\n"):
        if re.fullmatch(r"(\s*(?:19|20)\d0)+\s*", ln):
            ys = []
            for m in re.findall(r"(?:19|20)\d0", ln):
                if m not in ys:
                    ys.append(m)
            years = [int(y) for y in ys]
            continue
        m = re.match(r"^\s+([A-Za-z][A-Za-z .]*?)\s{2,}(.*)$", ln)
        if not m or m.group(1).strip() not in names or years is None:
            continue
        toks = [t for t in m.group(2).split() if _NUMTOK.match(t)]
        if len(toks) != 5 * len(years):
            raise ValueError(f"urpop0090 token count {m.group(1)} {years} {len(toks)}")
        for k, y in enumerate(years):
            vals = [0.0 if t == "-" else float(t.replace(",", "").replace("%", "")) for t in toks[5 * k:5 * k + 5]]
            tot, urb, rur, pu, pr = vals
            share = urb / (urb + rur)
            assert abs(share * 100 - pu) <= 0.06, (m.group(1), y, share, pu)   # printed percent check
            res[(C.STATE_NAME_TO_USPS[m.group(1).strip()], y)] = (share, "census_table1_urban_over_urban_plus_rural")
    return res


def urban_2000():
    pa = note_input("census_urban_2000_ua", RAW / "census_urban_2000_ua_state/st2kua.txt", source="Census 2000 urbanized areas, state portions")
    pc = note_input("census_urban_2000_uc", RAW / "census_urban_2000_uc_state/st2kuc.txt", source="Census 2000 urban clusters, state portions")
    pt = note_input("census_popest_2000_2010", L4 / "covariates_public/raw/census_popest/st-est00int-01.csv",
                    source="Census intercensal estimates 2000-2010, April 1 2000 base (denominator)")
    urb = collections.Counter()
    for f in (pa, pc):
        for line in open(f, errors="replace"):
            m = re.match(r"^(\d{2}) (\d{5}) (.{50,60}?)\s+(\d+)(\(PT\))?\s+(\d+)\s+([\d.]+)\s*$", line.rstrip("\n"))
            if m:
                urb[m.group(1)] += int(m.group(4))
    national = sum(v for k, v in urb.items() if k in C.FIPS_TO_USPS)
    assert national == 222360539, national                                  # Census national urban population 2000
    tot = {}
    for r in csv.reader(open(pt, encoding="latin-1", newline="")):
        if r and r[0].startswith("."):
            tot[r[0][1:].strip()] = int(r[1].replace(",", ""))
    out = {}
    for name, usps in C.STATE_NAME_TO_USPS.items():
        out[(usps, 2000)] = (urb[C.USPS_TO_FIPS[usps]] / tot[name], "census2000_ua_uc_state_portions_over_apr2000_base")
    return out, national


def urban_2010_2020():
    p = note_input("census_urban_2010_2020", RAW / "census_urban_2010_2020_state/State_Urban_Rural_Pop_2020_2010.xlsx",
                   source="Census state urban/rural population 2020 and 2010")
    sheet = [t for n, t in C.xlsx_sheets(p) if n == "States"][0]
    rows = C.xlsx_rows(p, sheet)
    out = {}
    for r in rows[1:]:
        if not r or not r[0] or r[1] not in C.USPS_TO_FIPS:
            continue
        usps = r[1]
        tot20, urb20, rur20, tot10, urb10, rur10 = (float(r[3]), float(r[4]), float(r[6]), float(r[8]), float(r[9]), float(r[11]))
        assert abs(tot20 - urb20 - rur20) < 1.5 and abs(tot10 - urb10 - rur10) < 1.5, usps
        out[(usps, 2020)] = (urb20 / tot20, "census2020_state_table_urban_over_total")
        out[(usps, 2010)] = (urb10 / tot10, "census2010_state_table_urban_over_total")
    p2 = note_input("census_urban_2010_pct", RAW / "census_urban_2000_2010_pct/PctUrbanRural_State.txt", source="Census 2010 percent urban by state")
    diffs = []
    for r in csv.DictReader(open(p2, encoding="latin-1", newline="")):
        usps = C.FIPS_TO_USPS.get(r["STATE"])
        if usps:
            diffs.append(abs(float(r["POPPCT_URBAN"]) / 100 - out[(usps, 2010)][0]))
    return out, max(diffs)


def nearest_census(year, available):
    best = min(available, key=lambda c: (abs(c - year), c))     # equal distance -> earlier census
    return best


# ---------------------------------------------------------------------------------------------
# 5. adjacency
# ---------------------------------------------------------------------------------------------
LAND_RULE_REMOVALS = {            # state pairs in the Census county adjacency file that share NO land boundary
    ("AZ", "CO"): "point_only (Four Corners)", ("NM", "UT"): "point_only (Four Corners)",
    ("IL", "MI"): "water_only (Lake Michigan)", ("MI", "MN"): "water_only (Lake Superior)",
    ("NY", "RI"): "water_only (Block Island Sound)",
}


def adjacency():
    p = note_input("census_county_adjacency_2026", L4 / "census_county_adjacency/raw/county_adjacency2026.txt",
                   source="Census County Adjacency File 2026 (pipe-delimited, Length = shared boundary metres)")
    rows = [l.rstrip("\n").split("|") for l in open(p, encoding="utf-8")]
    seen = set()
    agg = collections.defaultdict(lambda: {"pairs": 0, "len": 0, "maxlen": 0, "zero": 0})
    for r in rows[1:]:
        if len(r) != 5 or not r[3]:
            continue
        a, b = r[1], r[3]
        fa, fb = a[:2], b[:2]
        if fa == fb or fa not in C.FIPS_TO_USPS or fb not in C.FIPS_TO_USPS:
            continue
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        sp = tuple(sorted((C.FIPS_TO_USPS[fa], C.FIPS_TO_USPS[fb])))
        L = int(r[4])
        d = agg[sp]
        d["pairs"] += 1; d["len"] += L; d["maxlen"] = max(d["maxlen"], L); d["zero"] += (L == 0)
    out = []
    for sp in sorted(agg):
        d = agg[sp]
        rule = 0 if sp in LAND_RULE_REMOVALS else 1
        out.append([sp[0], sp[1], d["pairs"], d["len"], d["maxlen"], int(d["zero"] == d["pairs"]), 1, rule,
                    LAND_RULE_REMOVALS.get(sp, "")])
    return out


# ---------------------------------------------------------------------------------------------
def main():
    klar = load_klarner()
    coll = load_collectors()
    berry, berry_names = load_berry()
    bea = load_bea()
    cpi = load_cpi()

    # urban decennial table
    urb = {}
    urb.update(parse_urpop_1900_1990())
    u2000, national2000 = urban_2000()
    urb.update(u2000)
    u1020, maxdiff2010 = urban_2010_2020()
    urb.update(u1020)
    censuses = sorted({y for (_, y) in urb})
    for usps in C.USPS_ORDER:
        for y in censuses:
            assert (usps, y) in urb, ("urban missing", usps, y)

    # --- checks -----------------------------------------------------------------------------
    checks = {}
    for y in YEARS:
        assert y in cpi, ("CPI missing", y)
    checks["cpi_years"] = [min(cpi), max(cpi)]
    checks["cpi_2024"] = cpi[CPI_REF_YEAR]
    checks["berry_name_check"] = berry_names
    checks["berry_years"] = [min(y for (_, y) in berry), max(y for (_, y) in berry if berry[(_, y)] is not None)]
    checks["urban_2000_national_total_matches_census_222360539"] = national2000
    checks["urban_2010_two_sources_max_abs_diff_share"] = maxdiff2010
    # clock check: Klarner 2011 vs collectors 2011
    agree = collections.Counter()
    dis = []
    for usps in C.USPS_ORDER:
        if usps in ("DC", "NE"):
            continue
        k, c = klar.get((usps, 2011)), coll.get((usps, 2011))
        if not k or not c:
            continue
        for fld, kv, cv in (("gov_jan1_vs_klarner_gov", k["gov"], c["gov_party_jan1"] or None),
                            ("gov_dec31_vs_klarner_gov", k["gov"], c["gov_party_dec31"] or None),
                            ("house", k["hs"], c["house_majority"] or None), ("senate", k["sen"], c["senate_majority"] or None)):
            if kv is None or cv is None:
                agree[fld + ":n/a"] += 1
            elif kv == cv:
                agree[fld + ":agree"] += 1
            else:
                agree[fld + ":differ"] += 1
                dis.append((usps, fld, kv, cv))
    seam = {}
    for label, ky, cy2, cf in (("klarner_gov_2010_vs_collectors_jan1_2011", 2010, 2011, "gov_party_jan1"),
                               ("klarner_gov_2011_vs_collectors_jan1_2012", 2011, 2012, "gov_party_jan1")):
        cc = collections.Counter(); dd = []
        for usps in C.USPS_ORDER:
            if usps in ("DC", "NE"):
                continue
            a = (klar.get((usps, ky)) or {}).get("gov"); b = (coll.get((usps, cy2)) or {}).get(cf) or None
            if a is None or b is None:
                cc["n/a"] += 1
            elif a == b:
                cc["agree"] += 1
            else:
                cc["differ"] += 1; dd.append((usps, a, b))
        seam[label] = {**dict(cc), "differences": dd}
    checks["jan1_governor_proxy_accuracy_prior_year_klarner"] = seam
    checks["klarner_2011_vs_collectors_2011"] = dict(agree)
    checks["klarner_2011_vs_collectors_2011_differences"] = dis
    # collectors internal clock: gov_dec31(t-1) == gov_jan1(t)
    mism = 0; n = 0
    for (usps, y), c in coll.items():
        p = coll.get((usps, y - 1))
        if p and p["gov_party_dec31"] and c["gov_party_jan1"]:
            n += 1; mism += p["gov_party_dec31"] != c["gov_party_jan1"]
    checks["collectors_dec31_prev_vs_jan1_next"] = {"pairs": n, "mismatches": mism}

    # --- panel ------------------------------------------------------------------------------
    header = ["state", "fips", "year", "dem_unified_jan1", "rep_unified_jan1", "party_cat_jan1", "gov_party_jan1",
              "house_control", "senate_control",
              "dem_unified_session", "rep_unified_session", "party_cat_session", "gov_party_session",
              "party_source", "party_clock", "party_note",
              "citizen_ideology", "ideology_source_year", "ideology_carried_forward",
              "pcpi_nominal_usd", "cpi_u_annual_avg", f"pcpi_real_{CPI_REF_YEAR}usd", f"ln_pcpi_real_{CPI_REF_YEAR}usd",
              "urban_share", "urban_census_year", "urban_share_method"]
    rows = []
    miss = collections.Counter()
    last_berry_year = max(y for (_, y) in berry if berry[(_, y)] is not None)
    for usps in C.USPS_ORDER:
        for y in YEARS:
            pr = party_rows(usps, y, klar, coll)
            du, ru, cat, gov = pr["jan1"]
            sdu, sru, scat, sgov = pr["session"]
            hs, sen, src, clk, pnote = pr["house"], pr["senate"], pr["source"], pr["clock"], pr["note"]
            # ideology
            ideo = iy = None
            carried = ""
            if usps != "DC":
                iy = min(y, last_berry_year)
                ideo = berry.get((usps, iy))
                carried = int(y > last_berry_year)
            nom = bea.get((usps, y))
            cp = cpi[y]
            real = nom * cpi[CPI_REF_YEAR] / cp if nom is not None else None
            ln = math.log(real) if real else None
            cy = nearest_census(y, censuses)
            us, um = urb[(usps, cy)]
            vals = [usps, C.USPS_TO_FIPS[usps], y, du, ru, cat, gov, hs, sen, sdu, sru, scat, sgov, src, clk, pnote,
                    None if ideo is None else f"{ideo:.6f}", iy, carried,
                    None if nom is None else f"{nom:.0f}", f"{cp:.3f}", None if real is None else f"{real:.2f}",
                    None if ln is None else f"{ln:.6f}", f"{us:.6f}", cy, um]
            rows.append(vals)
            for h, v in zip(header, vals):
                if v is None or v == "":
                    miss[h] += 1
    checks["panel_rows"] = len(rows)
    checks["panel_states"] = len({r[0] for r in rows})
    checks["panel_years"] = [YEARS[0], YEARS[-1]]
    checks["missing_by_column"] = dict(miss)
    dec = collections.defaultdict(collections.Counter)
    clk = collections.defaultdict(collections.Counter)
    for r in rows:
        dec[(r[2] // 10) * 10][r[5]] += 1
        if r[5] != "unknown" and r[11] != "unknown":
            clk[(r[2] // 10) * 10]["same" if r[5] == r[11] else "jan1_differs_from_session"] += 1
    checks["party_cat_jan1_counts_by_decade"] = {str(k): dict(v) for k, v in sorted(dec.items())}
    checks["jan1_vs_session_category_by_decade"] = {str(k): dict(v) for k, v in sorted(clk.items())}

    # --- outputs ----------------------------------------------------------------------------
    outs = {}
    outs["state_year"] = C.write_new(OUT / f"state_year_{VERSION}.csv", C.csv_bytes(header, rows))
    urban_rows = [[usps, y, f"{urb[(usps, y)][0]:.6f}", urb[(usps, y)][1]] for usps in C.USPS_ORDER for y in censuses]
    outs["urban_decennial"] = C.write_new(OUT / f"urban_decennial_{VERSION}.csv",
                                           C.csv_bytes(["state", "census_year", "urban_share", "method"], urban_rows))
    adj = adjacency()
    outs["adjacency"] = C.write_new(OUT / f"state_adjacency_{VERSION}.csv", C.csv_bytes(
        ["state_a", "state_b", "county_pairs", "boundary_length_m_sum", "boundary_length_m_max_pair",
         "point_only", "census_county_adjacent", "land_border_rule_v1", "rule_v1_removal_reason"], adj))
    checks["adjacency_unordered_state_pairs_census"] = len(adj)
    checks["adjacency_land_border_rule_v1_pairs"] = sum(r[7] for r in adj)
    checks["adjacency_removed_pairs"] = {f"{a}-{b}": why for (a, b), why in LAND_RULE_REMOVALS.items()}
    deg = collections.Counter()
    for r in adj:
        if r[7]:
            deg[r[0]] += 1; deg[r[1]] += 1
    checks["states_without_land_neighbors"] = sorted(set(C.USPS_ORDER) - set(deg))

    code_sha = C.sha256_file(Path(__file__))
    receipt = {
        "schema": "l3_inputs_b_covariates_receipt_v1", "version": VERSION, "built_utc": C.utc_now(),
        "builder": {"path": C.rel(__file__), "sha256": code_sha, "python": sys.version.split()[0]},
        "spec_sources": ["data/l2_pipeline/autopilot/c64/STATE_COVARIATES.json (read-only; field definitions)",
                         "docs/plans/l3-meta/PREREG_L3_META_ADDENDUM_V2.md N1, N7, N8", "docs/plans/RESTART_PLAN_20261015.md section 3"],
        "frozen_rules": {
            "panel": "50 states + DC, 1960-2025",
            "party_unified": "Democratic (Republican) unified = governor and both chambers of that party; NE: both indicators 0 with flag; DC not coded",
            "party_clock_jan1": "(primary, spec name dem_unified_jan1) governor on 1 January: <=2010 Klarner's governor of the PRIOR year (Klarner's governor is the one serving when the session convenes, so the prior-year value is the 1 January incumbent), 2011-2024 the collectors' Jan-1 governor; chambers: Klarner in-session control <=2011, collectors' session majority 2012-2024 (NOT certified 1 January readings: a legislature seated after 1 January is read as in force on 1 January)",
            "party_clock_session": "(secondary, *_session columns) governor serving at session start: Klarner as published <=2011, collectors' Dec-31 governor 2012-2024; same chambers; 2025 not coded in either clock",
            "ideology": "Berry citi6016; key on state code 1-50 (alphabetical); 2017+ carries the last observed year (2016)",
            "real_income": f"BEA SAINC1 line 3 nominal per-capita personal income x CPI-U_{CPI_REF_YEAR} / CPI-U_year (annual averages, M13), natural log",
            "urban_share": "urban / (urban + rural) at the nearest decennial census; equal distance (years ending in 5) takes the EARLIER census; 2021-2025 use 2020",
            "adjacency": "Census county adjacency 2026 aggregated to state pairs; land_border_rule_v1 removes 2 point-only and 3 water-only pairs (see state_adjacency_v1.csv); AK and HI have no neighbours",
        },
        "inputs": INPUTS, "outputs": outs, "checks": checks,
        "limitations": [
            "Klarner indicators are session-year snapshots, not certified 1 January readings; the pooled study rejected them for its own Jan-1 gate (scripts/l3_pooled/run/party_calendar_sources.py).",
            "The party clock changes at 2011/2012 (Klarner session year -> collectors' Jan-1 governor); see checks.klarner_2011_vs_collectors_2011.",
            "DC has no party or ideology value; designs whose universe contains DC need a rule for it in the question's run version.",
            "2000 urban share uses the April 1 2000 estimates base as denominator (differences from the original census count are below 0.01 percentage points).",
            "Urban definitions change between censuses (1990 table, 2000 UA+UC, 2010, 2020); the file reports each census as published.",
            "land_border_rule_v1 is a single-pass reading of geography, not an independent certification.",
        ],
    }
    outs["receipt"] = C.write_new(OUT / f"RECEIPT_covariates_{VERSION}.json", C.json_bytes(receipt))
    for k, v in outs.items():
        print(k, v["status"], v["path"], v["bytes"], v["sha256"][:12])
    print(json.dumps({k: checks[k] for k in ("panel_rows", "klarner_2011_vs_collectors_2011", "collectors_dec31_prev_vs_jan1_next",
                                              "adjacency_unordered_state_pairs_census", "adjacency_land_border_rule_v1_pairs",
                                              "states_without_land_neighbors", "berry_name_check")}, indent=1))
    print("missing_by_column", checks["missing_by_column"])


if __name__ == "__main__":
    main()
