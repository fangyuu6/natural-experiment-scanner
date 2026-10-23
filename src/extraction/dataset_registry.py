"""Curated registry of outcome-data sources with access status.

Seeded from the natural-experiment-gatecheck taxonomy (ralph loop, 12,443
verified rows): G2 kills concentrated in restricted/DUA sources and in
outcomes with no co-located public microdata.
Matching is case-insensitive substring over alias lists — deliberately
conservative; anything unmatched stays 'unmatched' rather than guessed.
"""

PUBLIC = {
    "cps":        ["current population survey", "cps"],
    "acs":        ["american community survey", "acs"],
    "ipums":      ["ipums"],
    "qcew":       ["quarterly census of employment", "qcew"],
    "fars":       ["fatality analysis reporting", "fars"],
    "cdc_wonder": ["cdc wonder"],
    "ucr_nibrs":  ["uniform crime report", "ucr", "nibrs"],
    "ipeds":      ["ipeds"],
    "hmda":       ["home mortgage disclosure", "hmda"],
    "epa_aqs":    ["air quality system", "epa aqs"],
    "ntsb_faa":   ["ntsb", "faa"],
    "uscg_bard":  ["boating accident report"],
    "election":   ["election returns", "voter file", "voter registration"],
    "bds":        ["business dynamics statistics"],
    "cbp":        ["county business patterns"],
    "seer":       ["seer"],
    "nvss":       ["national vital statistics", "nvss"],
    "medicare_puf": ["medicare public use", "medicare provider utilization"],
    "irs_soi":    ["statistics of income", "irs soi"],
    "ffiec_call": ["call report", "ffiec"],
    "sba_loans":  ["sba loan", "7(a)"],
    "usda_nass":  ["nass", "census of agriculture"],
    "eia":        ["energy information administration", "eia"],
    "osha_ims":   ["osha inspection", "osha enforcement"],
    "uspto":      ["uspto", "patentsview"],
}

RESTRICTED = {
    "hcup_sid":    ["hcup", "state inpatient database"],
    "tmsis":       ["t-msis", "tmsis", "medicaid claims"],
    "srtr_optn":   ["srtr", "optn"],
    "ui_wage":     ["ui wage", "unemployment insurance wage", "lehd"],
    "credit_bureau": ["credit bureau", "equifax", "experian", "transunion"],
    "scanner":     ["nielsen", "iqvia", "iri scanner", "scanner data"],
    "workers_comp": ["workers comp", "workers' comp"],
    "irs_micro":   ["tax return microdata", "irs microdata"],
    "hospital_discharge": ["hospital discharge data"],
    "all_payer":   ["all-payer claims", "apcd"],
}


def _match(text, table):
    hits = []
    for canon, aliases in table.items():
        if any(a in text for a in aliases):
            hits.append(canon)
    return hits


def classify_datasets(datasets_str):
    if not datasets_str or not str(datasets_str).strip():
        return {"public": [], "restricted": [], "unmatched": [], "verdict": "empty"}
    parts = [p.strip().lower() for p in
             str(datasets_str).replace(";", ",").split(",") if p.strip()]
    if not parts:
        return {"public": [], "restricted": [], "unmatched": [], "verdict": "empty"}
    public, restricted, unmatched = [], [], []
    for p in parts:
        pub = _match(p, PUBLIC)
        res = _match(p, RESTRICTED)
        if res:
            restricted.extend(res)
        elif pub:
            public.extend(pub)
        else:
            unmatched.append(p)
    public = sorted(set(public))
    restricted = sorted(set(restricted))
    unmatched = sorted(set(unmatched))
    if public and not restricted:
        verdict = "public_ok" if not unmatched else "mixed"
    elif restricted and not public:
        verdict = "restricted_only" if not unmatched else "mixed"
    elif public and restricted:
        verdict = "mixed"
    else:
        verdict = "unmatched_only"
    return {"public": public, "restricted": restricted,
            "unmatched": unmatched, "verdict": verdict}
