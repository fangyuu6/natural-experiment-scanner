"""EXPLORATORY (T227). Adds ratios/flags to REPGAP_ESTIMATOR_PAIRS_RAW.csv -> REPGAP_ESTIMATOR_PAIRS.csv and prints summary numbers."""
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "../../../.."))
OUT = os.path.join(ROOT, "data/l3_meta/estimator_check_20261021/repgap")
df = pd.read_csv(os.path.join(OUT, "REPGAP_ESTIMATOR_PAIRS_RAW.csv"))
# Friedman: REPGAP csv stores ours as a fraction (0.0061) against a published value in pp (0.9): unit mismatch. Use pp for ratios here.
m0 = df.slug == "friedman_2015_ecig_policy"
df["first_estimate_repgap_csv"] = df["first_estimate"]
df.loc[m0, "first_estimate"] = 0.6134
df["repgap_ratio_as_in_csv"] = df["repgap_ratio"]
df["first_ratio_unit_consistent"] = df["first_estimate"] / df["published"]
df["robust_median4"] = df[["cs_simple", "cs_group", "sa", "imp_spec"]].median(axis=1)
# bootstrap SEs were taken on the raw coefficient scale; convert to published scale for the two percent-scaled pairs (delta method)
for sl, col in [("shover_2019_cannabis_policy", "imp_spec"), ("wang_2026_snap_broad_based_categorical_e", "imp_spec")]:
    mm = df.slug == sl
    for sc_ in ["cs_group_se_boot", "imp_spec_se_boot"]:
        b_raw = np.log(df.loc[mm, "cs_group" if sc_.startswith("cs") else "imp_spec"] / 100 + 1)
        df.loc[mm, sc_] = df.loc[mm, sc_] * 100 * np.exp(b_raw)
df["z_cs_group_vs_pub"] = (df["cs_group"] - df["published"]) / df["cs_group_se_boot"]
df["z_imp_spec_vs_pub"] = (df["imp_spec"] - df["published"]) / df["imp_spec_se_boot"]
EST = ["robust_median4", "rerun_spec_twfe", "twfe_simple", "imp_spec", "imp_simple", "cs_simple", "cs_group", "cs_group_w", "sa"]
for e in EST:
    if e in df:
        df["ratio_pub__" + e] = df[e] / df["published"]
        df["ratio_first__" + e] = df[e] / df["first_estimate"]
        df["signflip_vs_pub__" + e] = np.where(df[e].isna(), np.nan, (np.sign(df[e]) != np.sign(df["published"])).astype(float))
# estimator effect on the SAME simplified spec: robust / twfe_simple
for e in ["imp_simple", "cs_simple", "cs_group", "sa"]:
    df["rel_to_twfe_simple__" + e] = df[e] / df["twfe_simple"]
# Unit fix for Friedman: published 0.9 pp, REPGAP 'first_estimate' stored as fraction (0.0061); use pp scale
m = df.slug == "friedman_2015_ecig_policy"
df.to_csv(os.path.join(OUT, "REPGAP_ESTIMATOR_PAIRS.csv"), index=False)
done = df[df.status == "done"].copy()
pd.set_option("display.width", 250, "display.max_columns", 40)
cols = ["slug", "published", "first_estimate", "rerun_spec_twfe", "twfe_simple", "imp_spec", "cs_simple", "cs_group", "sa"]
print(done[cols].round(4).to_string())
print()
EXTRA = []
for e in ["rerun_spec_twfe", "twfe_simple", "imp_spec", "imp_simple", "cs_simple", "cs_group", "sa"]:
    r = done["ratio_pub__" + e]
    ok = r.notna()
    print(e, "n", ok.sum(), "median ratio_pub", round(r[ok].median(), 3), "sign flips", int(done["signflip_vs_pub__" + e].sum()),
          ">10% from rerun:", int(((done[e] / done["rerun_spec_twfe"] - 1).abs() > 0.10).sum()))

def block(sub, label):
    print("==", label, "n =", len(sub))
    print("first estimate: median ratio to published", round(sub.first_ratio_unit_consistent.median(), 3))
    for e in ["rerun_spec_twfe", "twfe_simple", "imp_spec", "cs_simple", "cs_group", "sa", "robust_median4"]:
        r = sub["ratio_pub__" + e]; ok = r.notna()
        mv = ((sub[e] / sub.first_estimate - 1).abs() > 0.10)
        print(f"{e:16s} med ratio_pub {r[ok].median():6.3f} | sign flips vs pub {int(sub['signflip_vs_pub__'+e].sum())} | moved>10% from first: {int(mv.sum())} / {int(ok.sum())} | med ratio_first {sub['ratio_first__'+e].median():6.3f}")
block(done, "all done")
block(done[~done.slug.isin(["rees_2019_overdose_harm_reduction", "anderson_2024_primary_seat_belt_law", "llerasmuney_2015_compulsory_schooling"])], "excluding scale/partial-spec pairs")
done[["slug","published","first_estimate","rerun_spec_twfe","twfe_simple","imp_spec","cs_simple","cs_group","sa","robust_median4","cs_group_se_boot","imp_spec_se_boot","se_pub"]].round(4).to_csv("/dev/stdout", index=False)

print(done[["slug","z_cs_group_vs_pub","z_imp_spec_vs_pub"]].round(2).to_string())
print("flips cs_simple:", list(done.slug[done["signflip_vs_pub__cs_simple"]==1]), "sa:", list(done.slug[done["signflip_vs_pub__sa"]==1]), "imp:", list(done.slug[done["signflip_vs_pub__imp_spec"]==1]), "cs_group:", list(done.slug[done["signflip_vs_pub__cs_group"]==1]))
print("abs z cs_group >1.96:", int((done.z_cs_group_vs_pub.abs()>1.96).sum()), " imp:", int((done.z_imp_spec_vs_pub.abs()>1.96).sum()))
print(done[["slug","ratio_pub__cs_simple","ratio_pub__sa","ratio_pub__imp_spec","rel_to_twfe_simple__cs_simple","rel_to_twfe_simple__sa"]].round(2).to_string())
