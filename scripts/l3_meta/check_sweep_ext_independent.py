#!/usr/bin/env python3
"""Independent recomputation of the extension store (SWEEP-EXT-V1): plain readers of the raw files, no import from the builders and no L2 module.

Rebuilds from the raw public files, with csv/json/zipfile/numpy only, the state-year values of a set of series of every file format and compares them cell by
cell with series_store_ext_v1.npz (value and eligibility): CDC Socrata (firearm crude rate), CDC historical CSV (teen birth rate 15-19, leading-cause age-adjusted rate
all causes, drug overdose crude and age-adjusted rates, suicide alias), BLS API v1 JSON (LAUS unemployment rate M13, CES weekly earnings mean of 12 months),
QCEW CSV (private weekly wage industry 10; child day care NAICS 6244), ACS summary files (table-based 2021-2024 and the derived whole-state files 2010-2019: poverty
under 5, all-age poverty, median household income all ages, single-mother family income, SNAP receipt, insurance 35-44), CPS Basic Monthly (teen employment: three cells from the
fixed-width records).  The all-cause crude death rate, the NCHS summary PDF series and the parked series are not rebuilt here (they need L2's Census denominators or
a PDF parser); the NCHS summary 15-19 rate is checked against the CDC teen birth rate in the overlap year 2019 instead.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/check_sweep_ext_independent.py --out data/l3_meta/sweep_ext_v1/INDEPENDENT_CHECK_EXT_V1.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
EXT = ROOT / "data/l3_meta/sweep_ext_v1"
SHARED = ROOT / "data/replication/shared_data"
STATE_FIPS = dict(zip("01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56".split(),
                      "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()))
STATE_NAME = dict(zip("Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|District of Columbia|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming".split("|"),
                      STATE_FIPS.values()))
SENTINELS = {-111111111, -222222222, -333333333, -555555555, -666666666, -888888888, -999999999}


class Store:
    def __init__(self, path):
        z = np.load(path, allow_pickle=False)
        self.units = [str(u) for u in z["units"]]
        self.years = [int(y) for y in z["years"]]
        self.ui = {u: i for i, u in enumerate(self.units)}
        self.yi = {y: i for i, y in enumerate(self.years)}
        self.z = {k: z[k] for k in z.files if "__" in k}
        self.sids = sorted({k.rsplit("__", 1)[0] for k in self.z})

    def cell(self, sid, unit, year):
        i, j = self.ui[unit], self.yi[year]
        return bool(self.z[f"{sid}__ok"][i, j]), float(self.z[f"{sid}__val"][i, j])


def rel(p):
    try:
        return str(Path(p).resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def number(tok):
    try:
        v = float(str(tok).strip().replace(",", ""))
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def compare(store, sid, expected, label):
    """expected: {(unit, year): value or None}.  Every store cell of the series is checked: eligible iff expected has a value; values equal within 1e-9 relative."""
    bad, n_ok, n_cmp = [], 0, 0
    for u in store.units:
        for y in store.years:
            e = expected.get((u, y))
            ok, v = store.cell(sid, u, y)
            if e is None:
                if ok:
                    bad.append((u, y, "store eligible, independent value missing", v, None))
                continue
            n_cmp += 1
            if not ok:
                bad.append((u, y, "store not eligible, independent value present", v, e))
            elif abs(v - e) > 1e-9 * max(1.0, abs(v), abs(e)):
                bad.append((u, y, "value differs", v, e))
            else:
                n_ok += 1
    return dict(series=sid, label=label, compared=n_cmp, agree=n_ok, disagreements=len(bad), first_disagreements=bad[:5])


# --- readers -------------------------------------------------------------------------------------------------
def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def firearm_rate():
    out = {}
    for p in sorted((SHARED / "cdc-nchs-socrata/fpsi-y8tj-2019-2024/raw").glob("fpsi-y8tj_*.csv")):
        for r in read_csv(p):
            if r["intent"] != "FA_Deaths" or not r["period"].isdigit():
                continue
            u = STATE_FIPS.get(r["geoid"].zfill(2))
            v = number(r["rate"])
            if u and v is not None and v >= 0:
                out[(u, int(r["period"]))] = v
    return out


def historical(path, filt, field):
    out = {}
    for r in read_csv(path):
        u = STATE_NAME.get(r["state"])
        if not u or not filt(r):
            continue
        v = number(r[field])
        if v is not None:
            out[(u, int(r["year"]))] = v
    return out


def bls_json(paths):
    series = {}
    for p in paths:
        d = json.loads(Path(p).read_text())
        assert d["status"] == "REQUEST_SUCCEEDED", p
        for s in d["Results"]["series"]:
            for rec in s["data"]:
                series.setdefault(s["seriesID"], {})[(int(rec["year"]), rec["period"])] = number(rec["value"])
    return series


def laus_rate(files):
    s = bls_json(files)
    out = {}
    for sid, d in s.items():
        if sid.startswith("LAUST") and sid.endswith("0000000000003"):
            u = STATE_FIPS[sid[5:7]]
            for (y, per), v in d.items():
                if per == "M13" and v is not None:
                    out[(u, y)] = v
    return out


def ces_weekly(files):
    s = bls_json(files)
    out = {}
    for sid, d in s.items():
        if sid.startswith("SMU") and sid.endswith("11") and len(sid) == 20:
            u = STATE_FIPS[sid[3:5]]
            for y in {y for (y, per) in d}:
                months = [d.get((y, f"M{m:02d}")) for m in range(1, 13)]
                if all(v is not None for v in months):
                    out[(u, y)] = sum(months) / 12
    return out


def qcew(paths, own, industry, field, sup="disclosure_code"):
    out = {}
    for p in paths:
        for r in read_csv(p):
            a = r["area_fips"].zfill(5)
            if a[:2] not in STATE_FIPS or a[2:] != "000" or r["own_code"] != own or r["industry_code"] != industry or r["size_code"] != "0":
                continue
            if r.get("qtr", "A") != "A":
                continue
            if r[sup] != "":
                continue
            v = number(r[field])
            if v is not None:
                out[(STATE_FIPS[a[:2]], int(r["year"]))] = v
    return out


def acs_cells(path, columns):
    """{state abbr: {column: value or None}} from one pipe-delimited ACS summary file, whole-state rows only."""
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh, delimiter="|"):
            g = r["GEO_ID"]
            if g.startswith("0400000US") and g[9:] in STATE_FIPS:
                row = {}
                for c in columns:
                    v = number(r[c])
                    row[c] = None if (v is None or int(v) in SENTINELS) else v
                out[STATE_FIPS[g[9:]]] = row
    return out


def acs_series(cat, files_by_year, kind):
    """Plain recomputation: numerator and denominator cells of the catalog, 100*sum(num)/sum(den), or the single estimate for medians."""
    def col(v):                                         # B17001_004E -> B17001_E004
        return v.split("_")[0] + "_" + v[-1] + v.split("_")[1][:-1]
    nums = [col(v) for v in cat["numerator_variables"]]
    dens = [col(v) for v in cat["denominator_variables"]]
    out = {}
    for y, paths in files_by_year.items():
        for p in paths:
            cells = acs_cells(p, sorted(set(nums + dens)))
            for u, row in cells.items():
                need = list(dict.fromkeys(nums + dens))
                if any(row[c] is None for c in need):
                    continue
                if dens:
                    d = sum(row[c] for c in dens)
                    if d > 0:
                        out[(u, y)] = 100.0 * sum(row[c] for c in nums) / d
                else:
                    out[(u, y)] = sum(row[c] for c in nums)
    return out


def cps_teen_employment_cell(zip_path, state_fips, month_ok=None):
    """Weighted employment percent of adult civilians aged 16-19 of one state in one month: fixed-width positions of the CPS Basic layouts (GESTFIPS 93-94, age 122-123,
    PRPERTYP 161-162, PEMLR 180-181, PWSSWGT 613-622 with four implied decimals)."""
    with zipfile.ZipFile(zip_path) as zf:
        members = [n for n in zf.namelist() if not n.endswith("/")]
        raw = zf.read(members[0])
    size = raw.find(b"\n") + 1
    dt = np.dtype(dict(names=["fips", "age", "typ", "emlr", "w"], formats=["S2", "S2", "S2", "S2", "S10"], offsets=[92, 121, 160, 179, 612], itemsize=size))
    a = np.frombuffer(raw, dtype=dt)
    fips, age, typ, emlr, w = (a[k].astype(np.int64) for k in ("fips", "age", "typ", "emlr", "w"))
    sel = (fips == int(state_fips)) & (typ == 2) & (age >= 16) & (age <= 19) & np.isin(emlr, range(1, 8)) & (w > 0)
    weights = w[sel] / 10000.0
    emp = np.isin(emlr[sel], [1, 2]).astype(float) * 100.0
    return float((weights * emp).sum()), float(weights.sum())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--store", default=str(EXT / "series_store_ext_v1.npz"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        sys.exit(f"{out} exists: not overwritten")
    store = Store(args.store)
    cat = json.loads((ROOT / "scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V15.json").read_text())["series"]
    plan = json.loads((EXT / "PLAN_EXT_V1.json").read_text())["series"]
    results = []

    def run(sid, label, expected):
        if sid in store.sids:
            results.append(compare(store, sid, expected, label))
        else:
            results.append(dict(series=sid, label=label, compared=0, agree=0, disagreements=-1, first_disagreements=["series not in the store"]))

    run("cdc_firearm_crude_rate", "Socrata CSV, period=year, intent FA_Deaths, field rate", firearm_rate())
    hist = ROOT / "data/l2_pipeline/connectors_v2/wonder/raw"
    run("cdc_teen_birth_rate", "y268-sna3, age_years '15-19 years', state_rate",
        historical(hist / "historical_y268-sna3.csv", lambda r: r["age_years"] == "15-19 years", "state_rate"))
    run("cdc_leading_cause_age_adjusted_rate", "bi63-dtpu, cause All causes, aadr",
        historical(hist / "historical_bi63-dtpu.csv", lambda r: r["cause_name"] == "All causes", "aadr"))
    run("nchs_historical_suicide_age_adjusted_rate", "bi63-dtpu, cause Suicide, aadr",
        historical(hist / "historical_bi63-dtpu.csv", lambda r: r["cause_name"] == "Suicide", "aadr"))
    drug = lambda r: r["sex"] == "Both Sexes" and r["age"] == "All Ages" and r["race"] == "All Races-All Origins" and r["unit"] == "per 100,000 population"
    run("nchs_historical_drug_overdose_crude_rate", "44rk-q6r2 both sexes all ages all races, rate", historical(hist / "historical_44rk-q6r2.csv", drug, "rate"))
    run("nchs_historical_drug_overdose_age_adjusted_rate", "44rk-q6r2, ageadjrate", historical(hist / "historical_44rk-q6r2.csv", drug, "ageadjrate"))
    laus_files = sorted((ROOT / "data/l4/insight_inputs/downloads/bls_laus_state_unemployment/raw").glob("*.json"))
    run("bls_laus_unemployment_rate", "BLS API v1 JSON, M13 annual average", laus_rate(laus_files))
    ces_files = sorted((ROOT / "data/l2_pipeline/connectors_v2/bls_api_v1/raw").glob("bls_v1_bls_ces_private_weekly_earnings_*[0-9a-f].json"))
    run("bls_ces_private_weekly_earnings", "BLS API v1 JSON, SMU...11, mean of 12 months", ces_weekly(ces_files))
    wage_files = [ROOT / f"data/did_samples/paid_sick_leave/raw/qcew_{y}_a_industry_10.csv" for y in range(2014, 2026)]
    run("bls_qcew_private_weekly_wage", "QCEW CSV own 5, industry 10, annual_avg_wkly_wage", qcew(wage_files, "5", "10", "annual_avg_wkly_wage"))
    cdc_files = [ROOT / f"data/l2_pipeline/connectors_v2/bls_qcew_industry/raw/{y}_6244.csv" for y in range(2001, 2025)]
    run("bls_qcew_private_child_day_care_employment", "QCEW CSV own 5, NAICS 6244, annual_avg_emplvl", qcew(cdc_files, "5", "6244", "annual_avg_emplvl"))
    # ACS: files per year of the series' table
    acs_seq = SHARED / "census-acs1-summary/acs1-sequence-by-state-2010-2019/raw"
    acs_tb = SHARED / "census-acs1-summary/acs1-table-based-2021-2024/raw"
    other_tb = ROOT / "data/l2_pipeline/connectors_v2/census_acs1/raw/tablebased"
    other_seq = ROOT / "data/l2_pipeline/connectors_v2/census_acs1/raw/seq_b22003"
    for sid in ("acs1_poverty_rate_age_under5", "acs1_poverty_rate_age_all", "acs1_median_household_income_age_all", "acs1_single_mother_median_family_income",
                "acs1_snap_household_receipt_rate", "acs1_insurance_coverage_age_35_44"):
        spec = cat[sid]
        table = spec["table"].lower()
        years = [y for y in spec["years"] if y >= 2010]
        files = {}
        for y in years:
            if y >= 2021:
                cands = [acs_tb / f"acsdt1y{y}-{table}.dat", other_tb / f"acsdt1y{y}-{table}.dat"]
                files[y] = [next(c for c in cands if c.is_file())] if any(c.is_file() for c in cands) else []
            else:
                got = sorted(acs_seq.glob(f"acsdt1y{y}-{table}-*-derived.dat"))
                if len(got) != 51 and table == "b22003":
                    got = sorted((other_seq / str(y)).glob(f"acsdt1y{y}-{table}-*-derived.dat"))
                files[y] = got
        run(sid, "ACS summary files, catalog numerator/denominator cells", acs_series(spec, files, "acs"))
    # CPS: three cells of teen employment (year mean of 12 monthly weighted means pooled over person-months, as the adapter documents)
    cps_cells = []
    for (u, y) in (("CA", 1994), ("TX", 2005), ("NY", 2019)):
        fips = next(k for k, v in STATE_FIPS.items() if v == u)
        wsum = esum = 0.0
        for mon in "jan feb mar apr may jun jul aug sep oct nov dec".split():
            e, w = cps_teen_employment_cell(ROOT / f"scripts/l2_batch1/raw/monthly/{y}_{mon}.zip", fips)
            esum += e
            wsum += w
        exp = esum / wsum
        ok, v = store.cell("cps_teen_employment", u, y)
        cps_cells.append(dict(unit=u, year=y, store=v, independent=exp, eligible=ok, agree=bool(ok and abs(v - exp) <= 1e-9 * max(1.0, abs(exp)))))
    results.append(dict(series="cps_teen_employment", label="CPS Basic Monthly ZIPs, fixed-width fields, 12 months pooled", compared=3, agree=sum(c["agree"] for c in cps_cells),
                        disagreements=sum(not c["agree"] for c in cps_cells), cells=cps_cells))
    # cross-source: NCHS summary 15-19 rate against the CDC teen birth rate in the overlap year 2019
    cross = []
    for u in store.units:
        ok1, v1 = store.cell("nchs_summary_age_specific_birth_rate", u, 2019)
        ok2, v2 = store.cell("cdc_teen_birth_rate", u, 2019)
        if ok1 and ok2:
            cross.append(abs(v1 - v2))
    results.append(dict(series="nchs_summary_age_specific_birth_rate", label="overlap year 2019 against cdc_teen_birth_rate (two publications of the same quantity)",
                        compared=len(cross), max_abs_difference=max(cross) if cross else None, n_differ_over_0_05=sum(1 for d in cross if d > 0.05)))
    total_dis = sum(max(r.get("disagreements", 0), 0) for r in results)
    missing = [r["series"] for r in results if r.get("disagreements") == -1]
    report = dict(rule_id="EX-1", version="SWEEP-EXT-V1", check="independent recomputation of the extension store", store=rel(args.store),
                  python=sys.version.split()[0], numpy=np.__version__, n_series_checked=len(results), disagreements=total_dis, series_missing_from_store=missing, results=results)
    with open(out, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(report, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps(dict(n_series_checked=len(results), disagreements=total_dis, missing=missing), indent=1))
    return 1 if total_dis or missing else 0


if __name__ == "__main__":
    sys.exit(main())
