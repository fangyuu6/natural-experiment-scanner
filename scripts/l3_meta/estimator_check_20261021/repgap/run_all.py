"""EXPLORATORY (T227). Re-estimate REPGAP staggered pairs with heterogeneity-robust estimators.
Read-only on the repo (reads run folders under data/replication/r1/). Writes only REPGAP_ESTIMATOR_PAIRS.csv.
Run: .venv_did/bin/python -I -B scripts/l3_meta/estimator_check_20261021/repgap/run_all.py   (from repo root)
"""
import os, sys, json, math, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "../../../.."))
sys.path.insert(0, HERE)
from estlib import twfe, imputation, cs, sa, boot_se, add_cohort  # noqa: E402

R1 = os.path.join(ROOT, "data/replication/r1")
OUT = os.path.join(ROOT, "data/l3_meta/estimator_check_20261021/repgap")
B = int(os.environ.get("NBOOT", "80"))
REP = pd.read_csv(os.path.join(ROOT, "data/l3_meta/repgap_v1/REPGAP_PAIRS_V1.csv")).set_index("slug")


def drop_partial(df, d, lo=0.5):
    """binarize fractional exposure at 0.5; drop ambiguous 0<frac<0.5 rows. returns df, n_dropped"""
    m = (df[d] > 0) & (df[d] < lo)
    out = df[~m].copy()
    out[d + "_bin"] = (out[d] >= lo).astype(int)
    return out, int(m.sum())


def run_pair(slug, df, y, unit, time, d, w=None, controls=(), trend=False, cluster=None, scale=None,
             twfe_spec_kwargs=None, note="", boot=True):
    """df must already be the estimation sample and d binary absorbing. scale: function mapping a coefficient to published units."""
    cluster = cluster or unit
    sc = scale or (lambda b: b)
    res = {}
    dd, nonabs = add_cohort(df, unit, time, d)
    res["n_obs"] = len(df); res["n_units"] = df[unit].nunique()
    coh = dd[np.isfinite(dd.G)].groupby(unit).G.first()
    res["n_cohorts"] = int(coh.nunique()); res["n_treated_units"] = int(len(coh))
    res["never_treated_units"] = int(df[unit].nunique() - len(coh))
    res["nonabsorbing_units"] = nonabs
    # original spec re-run
    res["rerun_spec_twfe"] = sc(twfe(df, y, unit, time, d, w=w, controls=controls, trend=trend, cluster=cluster)["est"])
    t0 = twfe(df, y, unit, time, d, cluster=cluster)
    res["twfe_simple"] = sc(t0["est"])
    try:
        res["imp_spec"] = sc(imputation(df, y, unit, time, d, w=w, controls=controls, trend=trend)["est"])
        info = imputation(df, y, unit, time, d, w=w, controls=controls, trend=trend)
        res["imp_spec_share_treated_used"] = info["n_used"] / max(1, info["n_treated"])
    except Exception as e:
        res["imp_spec"] = np.nan
    if trend:
        res["imp_spec_notrend"] = sc(imputation(df, y, unit, time, d, w=w, controls=controls)["est"])
        res["twfe_spec_notrend"] = sc(twfe(df, y, unit, time, d, w=w, controls=controls, cluster=cluster)["est"])
    elif w is not None or len(controls):
        res["imp_w_only"] = sc(imputation(df, y, unit, time, d, w=w)["est"]) if w is not None else np.nan
    res["imp_simple"] = sc(imputation(df, y, unit, time, d)["est"])
    c1 = cs(df, y, unit, time, d, agg="simple"); c2 = cs(df, y, unit, time, d, agg="group")
    res["cs_simple"] = sc(c1["est"]); res["cs_group"] = sc(c2["est"])
    if w is not None:
        res["cs_group_w"] = sc(cs(df, y, unit, time, d, w=w, agg="group")["est"])
    try:
        s = sa(df, y, unit, time, d, cluster=cluster)
        res["sa"] = sc(s["est"]); res["sa_control"] = s["control"]
    except Exception as e:
        res["sa"] = np.nan; res["sa_err"] = str(e)[:80]
    if boot:
        try:
            res["cs_group_se_boot"] = boot_se(lambda z: cs(z, y, unit, time, d, agg="group")["est"], df, unit, cluster, B=B)
            res["imp_spec_se_boot"] = boot_se(lambda z: imputation(z, y, unit, time, d, w=w, controls=controls, trend=trend)["est"],
                                              df, unit, cluster, B=B)
        except Exception as e:
            pass
    res["note"] = note
    return res


PAIRS = {}


def pair(fn):
    PAIRS[fn.__name__] = fn
    return fn


@pair
def paglayan_2019_teacher_labor():
    d = pd.read_csv(f"{R1}/paglayan_2019_teacher_labor/estimation_sample.csv")
    d = d[(d.CBeverrequired == 1) & (~d.State.isin(["WI", "DC"])) & d.year.between(1959, 1990)].dropna(subset=["lnppexpend", "CBrequired_SY"])
    r = run_pair("paglayan", d, "lnppexpend", "State", "year", "CBrequired_SY",
                 note="all 32 units eventually treated (no never-treated); imputation cannot impute years after the last adoption")
    r.update(paper_estimator="TWFE static (Table 2 col 3); Fig 4 dynamic baseline", our_estimator="TWFE static, state+year FE, unweighted")
    return r


@pair
def jayaratne_1996_misc_regulation():
    d = pd.read_csv(f"{R1}/jayaratne_1996_misc_regulation/panel_primary.csv", dtype={"fips": str})
    d = d[d.in_basic == 1]
    r = run_pair("jay", d, "growth_pct", "fips", "year", "D", note="outcome growth in pp; branch deregulation dummy; HC0 SE in original")
    r.update(paper_estimator="TWFE/OLS with state and year effects (equation 1)", our_estimator="TWFE/OLS, state+year FE, unweighted")
    return r


@pair
def son_2022_private_insurance_benefit_manda():
    d = pd.read_csv(f"{R1}/son_2022_private_insurance_benefit_manda/estimation_panel.csv", dtype={"fips": str})
    r = run_pair("son", d, "y", "state", "year", "D", w="population", controls=["exp_pc", "phys", "prev"],
                 note="WLS pop weights + 3 controls in original; 45 treated of 49")
    r.update(paper_estimator="TWFE static Adopt x Post (Table 2 col 2); Table A3 event study", our_estimator="WLS TWFE, pop weights, 3 controls")
    return r


@pair
def braslavskiy_2025_cannabis_policy():
    d = pd.read_csv(f"{R1}/braslavskiy_2025_cannabis_policy/analysis_panel.csv")
    d["T"] = (d.MML >= 0.5).astype(int)
    r = run_pair("bras", d, "log_fats_pc", "state", "year", "T", w="pop", trend=True,
                 note="original = BJS imputation with pop weights + state linear trends (imp_spec reproduces it); TWFE shown with trends too")
    r.update(paper_estimator="BJS imputation (robust), Table 1 panel C col 3", our_estimator="BJS imputation, pop-weighted, state trends")
    return r


@pair
def hinrichs_2012_higher_ed_access():
    d = pd.read_csv(f"{R1}/hinrichs_2012_higher_ed_access/analysis_sample.csv")
    d["unitid"] = d.unitid.astype(str)
    r = run_pair("hin", d, "black_percent", "unitid", "year", "ban", w="efrace24", trend="fips", cluster="fips",
                 note="institution FE, year FE, state linear trends, enrolment weights; 4 treated states, 14 colleges, 9 states; very small sample")
    r.update(paper_estimator="TWFE static (REStat Table 5 col 6)", our_estimator="WLS TWFE institution+year FE, state trends, enrolment weights")
    return r


@pair
def caceresdelpian_2012_family_law():
    d = pd.read_csv(f"{R1}/caceresdelpian_2012_family_law/independent/analysis_panel.csv")
    d["lv"] = np.log(d.violent_rate)
    r = run_pair("cdp", d, "lv", "state", "year", "treatment", note="log violent crime rate, unweighted; unilateral divorce 1965-1996")
    r.update(paper_estimator="TWFE static (Table 2 panel A col 1)", our_estimator="TWFE, state+year FE, unweighted")
    return r


@pair
def friedman_2015_ecig_policy():
    d = pd.read_csv(f"{R1}/friedman_2015_ecig_policy/work/analysis_panel.csv")
    d["y"] = 100 * d.y1217
    d["pidx"] = d.period.rank(method="dense").astype(int)
    ctr = ["smokefree_all3", "y1825", "cigtax_real13", "medinc_k13", "unemp", "pop_mil", "pct_u18", "pct_black", "pct_other", "pct_hisp"]
    r = run_pair("fri", d, "y", "state", "pidx", "ban", controls=ctr,
                 note="published 0.9 pp (journal abstract) vs frozen 0.613 pp; REPGAP csv compares 0.9 with 0.0061 (unit mismatch). Reported here in pp. Only 18 treated state-periods, two cohorts")
    r.update(paper_estimator="OLS TWFE (journal says 'regression'; WP Table 1 col 2)", our_estimator="OLS TWFE state+period FE, 10 controls, unweighted")
    return r


@pair
def shover_2019_cannabis_policy():
    d = pd.read_csv(f"{R1}/shover_2019_cannabis_policy/estimation_sample.csv")
    d = d[d.in_primary_sample == 1].dropna(subset=["ln_age_mort_rate", "unemployment"]).copy()
    ctr = ["rxdmp_original", "rxid_original", "pmlaw_original", "unemployment"]
    orig = twfe(d, "ln_age_mort_rate", "state", "year", "Medical_Cannabis_Law", controls=ctr)["est"]
    d2, nd = drop_partial(d, "Medical_Cannabis_Law")
    # controls in original are time-varying policy fractions; kept as supplied
    r = run_pair("sho", d2, "ln_age_mort_rate", "state", "year", "Medical_Cannabis_Law_bin", controls=ctr,
                 scale=lambda b: 100 * (math.exp(b) - 1),
                 note=f"fractional law var binarized at 0.5 for all estimators ({nd} rows with 0<frac<0.5 dropped); percent scale 100*(exp(b)-1); orig fractional TWFE b={orig:.4f}")
    r["rerun_spec_twfe_fractional_original"] = 100 * (math.exp(orig) - 1)
    r.update(paper_estimator="TWFE/GLM (Gaussian) log mortality rate, state+year FE, 4 controls", our_estimator="same, state-clustered")
    return r


@pair
def rees_2019_overdose_harm_reduction():
    d = pd.read_csv(f"{R1}/rees_2019_overdose_harm_reduction/panel.csv")
    d["lr"] = np.log(d.deaths / d["pop"])
    d1, nd = drop_partial(d, "NAL")
    r = run_pair("rees", d1, "lr", "state", "year", "NAL_bin", w="pop", controls=["GSL"],
                 note=f"original = Poisson FE with pop freq weights (coef -0.108); here log(deaths/pop) OLS, NAL binarized at 0.5 ({nd} rows dropped), GSL as control; "
                      "NOT same scale as the Poisson coefficient: read ratios robust/twfe_simple, not robust/published")
    r.update(paper_estimator="Poisson TWFE (NBER w23171 col 1)", our_estimator="Poisson FE, pop weights, GSL, ln(pop)")
    return r


@pair
def wang_2026_snap_broad_based_categorical_e():
    d = pd.read_csv(f"{R1}/wang2026_snap_bbce/analysis_panel.csv")
    d["T"] = d["bbce.c"].astype(int)
    r = run_pair("wang", d, "lpc", "st_fips", "year", "T", scale=lambda b: 100 * (math.exp(b) - 1),
                 note="original = Callaway-Sant'Anna DR with covariates, Kennedy bias-corrected percent; here uncorrected 100*(exp(b)-1), no covariates")
    r.update(paper_estimator="Callaway-Sant'Anna (doubly robust, covariates)", our_estimator="CS port (DR, covariates) on authors' data")
    return r


@pair
def anderson_2024_primary_seat_belt_law():
    p = f"{R1}/anderson_2024_primary_seat_belt_law/indep/analysis_source_master_1983to2019.dta"
    d = pd.read_stata(p, convert_categoricals=False)
    d["yv"] = d.fatal_occupant / d.vmt_total
    ctr_log = ["pctblack", "pcthisp", "age", "medinc", "unemprate", "violentcrimerate_ce", "propcrimerate_ce", "rural_den", "urban_den",
               "vmt_rural", "vmt_urban", "gastax"]
    for c in ctr_log:
        v = d[c].to_numpy(float); o = np.zeros_like(v); pos = v > 0; o[pos] = np.log(v[pos]); d["ln_" + c] = o
    ctr = ["secondary", "adj_secondary_primary"] + ["ln_" + c for c in ctr_log] + ["speed65", "speed70", "bac8", "mlda21"]
    # robust estimators target PSL (adj_primary); other two policy dummies kept as controls in spec-preserving runs only
    r = run_pair("and", d, "yv", "fips", "year", "adj_primary", controls=ctr, cluster="fips",
                 note="3 policy dummies in original (SSL, PSL, SSL->PSL); robust estimators treat PSL only, SSL states stay in the control pool (partial); "
                      "paper also reports Sun-Abraham elsewhere; published cell is TWFE. Outcome in levels per million VMT")
    r["rerun_spec_twfe"] = twfe(d, "yv", "fips", "year", "adj_primary", controls=ctr, cluster="fips")["est"]
    r.update(paper_estimator="TWFE (Table 1 col 5); paper's robustness uses Sun-Abraham", our_estimator="TWFE, 3 policy dummies, 16 controls")
    return r


@pair
def stevenson_2006_family_law():
    sys.path.insert(0, os.path.join(ROOT, "scripts/replication/r1/stevenson_2006_family_law"))
    import estimate as E, panel as P, swdata as S  # noqa
    rows = S.load_master()
    prepared = P.prepare(rows, "divlaw")
    rr = P.select(prepared, 2, E.T3_YEARS, tuple(E.T3_EXCLUDE), "spouse")
    y, mean_rate = E.t3_outcome(rr, "spouse", 1)
    d = pd.DataFrame({"st": [r["st"] for r in rr], "year": [r["year"] for r in rr], "treat": [r["treat"] for r in rr], "y": y})
    orig = E.run_t3(prepared, 2, "spouse", 1)
    r = run_pair("stev", d, "y", "st", "year", "treat", note=f"outcome = female spousal homicide rate scaled to percent of unweighted mean; original first estimate {orig['point']:.3f}; 9 states already unilateral in 1960 are always-treated (dropped by cohort estimators)")
    r.update(paper_estimator="TWFE static (Table III col 1), unweighted OLS", our_estimator="same, HC1 robust SE")
    return r


@pair
def english_only_llerasmuney_shertzer2015():
    d = pd.read_csv(f"{R1}/english_only_llerasmuney_shertzer2015/indep/analysis_foreignborn_6_16.csv.gz")
    d = d[d.AGE.between(10, 16)].copy()
    d["st"] = d.STATEFIP.astype(str)
    ctr = ["female", "urban", "par_lit_both", "par_lit_one", "par_eng_both", "par_eng_one", "frac_u14", "frac_o65", "frac_fb",
           "frac_recent_fb", "frac_urban", "frac_black", "lag_recent_fb_enroll", "pauto", "sch_ml", "docpc", "pmfg"]
    ageD = pd.get_dummies(d.AGE, prefix="age", drop_first=True, dtype=float)
    yrs = pd.get_dummies(d.yrsusa_cat, prefix="yu", drop_first=True, dtype=float)
    d = pd.concat([d, ageD, yrs], axis=1)
    ctr += list(ageD.columns) + list(yrs.columns)
    orig_lpm = twfe(d, "speaks_eng", "st", "YEAR", "eng_all", w="PERWT", controls=ctr, cluster="st")["est"]
    # micro-level unit = state, time = census year: individual rows pooled; cohort estimators run on state x year weighted cell means
    cell = d.groupby(["st", "YEAR"]).apply(lambda z: pd.Series({"y": np.average(z.speaks_eng, weights=z.PERWT), "eng_all": z.eng_all.max(),
                                                                 "w": z.PERWT.sum()})).reset_index()
    r = run_pair("lms", cell, "y", "st", "YEAR", "eng_all", w="w", boot=True,
                 note=f"original = individual probit AME (0.0195 ours, 0.022 paper); here state x census-year cell means of speaks_English, 3 periods (1910,1920,1930), "
                      f"micro LPM TWFE with controls gives {orig_lpm:.4f}; cohort estimators have only 2 cohorts, 1-2 usable periods")
    r["rerun_spec_twfe"] = orig_lpm
    r["imp_spec"] = imputation(d.assign(st=d.st), "speaks_eng", "st", "YEAR", "eng_all", w="PERWT", controls=ctr)["est"]
    r["twfe_simple_micro_nocontrols"] = twfe(d, "speaks_eng", "st", "YEAR", "eng_all", w="PERWT", cluster="st")["est"]
    r.update(paper_estimator="probit AME, state+year FE (Table 3 panel A col 5)", our_estimator="probit AME, individual controls, state clustered (data: IPUMS 1910-30)")
    return r


SKIP = {
    "friedberg_1998_family_law": "headline is a derived counterfactual (6 percent lower divorce rate in 1988) from a 4-law-type WLS with state trends and 16 irregularity dummies, not a binary-treatment coefficient; no robust analogue",
    "mcghee_2022_voting_methods": "target is a sum of two coefficients (baseline + 2020 increment) for universal mail on county turnout, a 2020-only shock with no pre-period staggering; not a standard panel DiD",
    "carpenter_2019_vaccination_policy": "regression sample is stored only as .rds (no R or pyreadr on this machine) and the design is individual-level NIS-Teen with 22 policy controls and cohort-by-state treatment; not rebuilt offline",
}
FOLDER = {
    "carpenter_2019_vaccination_policy": "tdap_carpenter_lawler2019",
    "llerasmuney_2015_compulsory_schooling": "english_only_llerasmuney_shertzer2015",
    "wang_2026_snap_broad_based_categorical_e": "wang2026_snap_bbce",
}
FN_ALIAS = {"llerasmuney_2015_compulsory_schooling": "english_only_llerasmuney_shertzer2015"}


def main():
    stag = REP[REP.staggered == "staggered"]
    rows = []
    for slug, rec in stag.iterrows():
        base = dict(paper_id=rec.paper_id, slug=slug, run_folder=FOLDER.get(slug, slug), design=rec.design, tier=rec.tier,
                    published=rec.p, se_pub=rec.se_pub, first_estimate=rec.o, repgap_ratio=rec.r)
        if os.environ.get("ONLY") and slug not in os.environ["ONLY"].split(","):
            continue
        fn = PAIRS.get(slug) or PAIRS.get(FN_ALIAS.get(slug, ""))
        if slug in SKIP or fn is None:
            base.update(status="skipped", skip_reason=SKIP.get(slug, "not implemented"))
            rows.append(base); continue
        try:
            import time as _t; _t0 = _t.time()
            out = fn(); print("  time", round(_t.time() - _t0, 1), flush=True)
            base.update(out); base["status"] = "done"
        except Exception as e:
            base.update(status="error", skip_reason=f"{type(e).__name__}: {str(e)[:150]}")
        print(slug, base.get("status"), {k: (round(v, 4) if isinstance(v, float) else v) for k, v in base.items()
                                         if k in ("rerun_spec_twfe", "twfe_simple", "imp_spec", "imp_simple", "cs_simple", "cs_group", "sa", "skip_reason")}, flush=True)
        rows.append(base)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "REPGAP_ESTIMATOR_PAIRS_RAW%s.csv" % os.environ.get("TAG", "")), index=False)
    return df


if __name__ == "__main__":
    main()
