"""Deterministic CSPP v2.6 core and Caughey–Warshaw policy-level import.

The full CSPP release is an authorized public mirror of individual CW variables;
the inaccessible Dataverse replication archive is not represented as downloaded.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

DEFAULT_RAW = Path(__file__).resolve().parents[4] / "data/l2_pipeline/sources/research_panel/raw/cspp"
SOURCE_ID = "cspp"
CORE = "inctax tels absvot earlvot elecdayreg mailreg motorvoter pubcamfun indlimit candlim corporlimits universal_health_care medicaid_stateadoption equalpay pubbrefeed genderrights_gender_equal_pay genderrights_era_ratification genderrights_state_eras genderrights_gender_discrimination genderrights_gender_discrimination_laws_post1964 foca aborparc aborparn pgestlim ssame sstmrrge fhpriv fhpub fhurb environ environmental_covenants_act transboundary_pollution_reciproc ewaste environment_state_nepas w_environment_endangered_species w_environment_solar_taxcredit fsmlp fswp fwtldrps fesasap guncontrol_opencarry w_guncontrol_registration_requir fairemp fairtrade miglab z_labor_unemployment_compensatio urbrenen frent gayempnondisc".split()
# Realized expenditure, average payments and tax burden are not statutory
# treatment parameters; policy/public opinion indexes are also excluded.
CW_EXCLUDED = {
    "x_tax_burden": "realized tax-to-income ratio, not a statutory tax parameter",
    "z_education_expenditures_per_pup": "realized spending per pupil",
    "z_education_higher_edu_spending": "realized per-student subsidy",
    "value_mean": "public opinion outcome",
}
OUTCOMES = {
    "demographics": ["Census ACS migration", "ACS education enrollment"],
    "economic-fiscal": ["ACS household disposable income", "BEA personal income", "Census state revenue"],
    "government": ["Census local government revenue", "Census government employment"],
    "elections": ["EAC turnout and registration", "FEC campaign receipts"],
    "criminal justice": ["FBI reported crime", "BJS imprisonment"],
    "education": ["NCES enrollment and completion", "NAEP achievement"],
    "healthcare": ["CDC mortality", "ACS insurance coverage"],
    "welfare": ["ACS poverty", "ACS insurance coverage", "HHS TANF participation"],
    "rights": ["ACS employment and earnings", "ACS marriage", "CDC births"],
    "environment": ["EPA air quality", "EIA electricity generation"],
    "drug-alcohol": ["CDC substance-related mortality", "NHTSA traffic fatalities"],
    "gun control": ["CDC firearm mortality", "FBI reported crime"],
    "labor": ["BLS employment and wages", "ACS earnings"],
    "transportation": ["NHTSA traffic fatalities", "FHWA travel"],
    "misc. regulation": ["BLS occupational employment", "Census business formation"],
}


def _number(raw: str, column: str) -> float | None:
    if raw.strip() in {"", "NA", "NaN", "N/A", "."}:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"Non-numeric CSPP value for {column}: {raw!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"Non-finite CSPP value for {column}")
    return value


def _is_binary(meta: dict) -> bool:
    desc = meta["long_desc"].lower()
    # V2.6 definition says 0/1 but the delivered series contains 14/15/16.
    # Keep the source numeric codes parked until coding is clarified.
    if meta["variable"] == "x_labor_childlabor_workpermitage":
        return False
    # Explicit definition, rather than observing only two numeric values.
    return "0 =" in desc and "1 =" in desc and "2 =" not in desc and "0.5 =" not in desc and "-1 =" not in desc


def _unit(meta: dict, binary: bool) -> str:
    if binary:
        return "indicator (source-defined 0/1 direction)"
    name, desc = meta["variable"], meta["long_desc"].lower()
    if name.startswith("w_") or name in {"ssame", "sstmrrge", "fwtldrps", "fesasap"}:
        return "source-defined ordinal policy code"
    if name.startswith("z_"):
        return "source-dollar measure; deflator/base-year requires verification"
    if "eligibility" in desc:
        return "source eligibility measure; denominator requires verification"
    if "rate" in desc:
        return "source tax rate; units require verification"
    return "source-defined numeric measure"


def _outcomes(meta: dict) -> list[str]:
    """Definition-led first-order candidates, without testing associations."""
    name = meta["variable"]
    groups = [
        (("medicaid", "chip_", "universal_health", "snrpresc"), ["ACS uninsured share among eligible population", "CMS program enrollment"]),
        (("tanf_", "afdc_up"), ["HHS cash-assistance caseload", "ACS disposable income of eligible families"]),
        (("unemployment_comp" ,), ["DOL UI recipiency", "DOL weekly benefits paid"]),
        (("disability_insurance",), ["State disability-insurance claims", "ACS employment among covered workers"]),
        (("equalpay", "equal_pay", "gender_discrimination"), ["ACS female-male wage gap", "EEOC sex-discrimination charges"]),
        (("employment_discr", "gayempnondisc", "fairemp", "fair_employ", "age_discrimination", "state_ada"), ["EEOC employment-discrimination charges", "ACS affected-group employment and earnings"]),
        (("absvot", "earlvot", "elecdayreg", "mailreg", "motorvoter"), ["EAC voter registration", "Census CPS voting turnout"]),
        (("pubcamfun", "indlimit", "candlim", "corporlimits"), ["State campaign-finance receipts", "State donor/candidate contribution amounts"]),
        (("abortion", "aborpar", "pgestlim", "foca"), ["CDC abortion incidence", "CDC adolescent birth rate"]),
        (("ec_access",), ["NSFG emergency contraception use", "CDC unintended-pregnancy proxy births"]),
        (("pubbrefeed",), ["CDC NIS breastfeeding duration"]),
        (("guncontrol",), ["CDC firearm homicide mortality", "CDC firearm suicide mortality"]),
        (("bottlebill", "ewaste", "electronic_waste"), ["State reported beverage-container/e-waste recycling volumes"]),
        (("solar_taxcredit", "publicbenefit_funds"), ["EIA residential solar installations", "EIA renewable generation"]),
        (("car_emissions", "air_pollution", "ghg_cap", "transboundary_pollution"), ["EPA regulated pollutant emissions", "EPA ambient air concentrations"]),
        (("wetland", "fwtldrps", "fswp", "endangered", "fesasap"), ["USFWS endangered-species status", "USFWS wetland acreage"]),
        (("environ", "state_nepas", "fsmlp", "cogrowman"), ["State environmental permitting and project reviews", "EPA regulated pollutant emissions"]),
        (("minimumwage", "minwage", "prevailing_wage"), ["BLS covered-worker hourly earnings", "BLS covered-sector employment"]),
        (("right_to_work", "collective_bargaining", "antiinjunction", "relations_acts"), ["CPS union membership and coverage", "BLS affected-sector wages"]),
        (("childlabor",), ["CPS adolescent employment", "NCES school attendance"]),
        (("workers_compensation",), ["State workers-compensation claims", "BLS occupational injuries"]),
        (("labor_merit", "retirement_system"), ["Census public-sector employment", "Census public retirement contributions"]),
        (("miglab",), ["DOL farmworker earnings", "DOL farmworker workplace violations"]),
        (("licenses_",), ["CPS licensed-occupation employment and earnings", "State licensing board registrations"]),
        (("smoking_ban", "cigarette", "cig_taxes"), ["BRFSS smoking prevalence", "State taxed cigarette sales"]),
        (("mlda21", "zerotol", "kegreg"), ["NHTSA alcohol-involved youth fatalities", "YRBS youth drinking"]),
        (("marijuana",), ["SAMHSA NSDUH marijuana use", "FBI marijuana arrests"]),
        (("gambling",), ["State lottery/casino receipts", "State gambling participation"]),
        (("helmets", "seatbelts", "car_insuran", "controlledaccessh"), ["NHTSA traffic fatalities", "NHTSA injury crashes"]),
        (("fair_housing", "fhpriv", "fhpub", "fhurb", "urban_housing", "housing_direct", "housing_enabling", "urbrenen"), ["HUD housing-discrimination complaints", "Census affected-group residential segregation"]),
        (("rent_control", "frent"), ["ACS gross rents", "Census rental-housing stock"]),
        (("civilunions", "sstmrrge", "ssame", "interracial_marriage", "nofault_divorce"), ["ACS affected-group marriage formation", "CDC marriage/divorce incidence"]),
        (("hatecrimes", "hate_crimes"), ["FBI reported bias-motivated offenses"]),
        (("public_accom", "school_segregation"), ["Civil Rights Data Collection school segregation", "HUD/DOJ civil-rights complaints"]),
        (("era_", "state_eras", "jury_service"), ["State civil-rights complaints", "State jury composition"]),
        (("death_penalty",), ["BJS executions", "BJS death-sentence admissions"]),
        (("cruelty",), ["FBI animal-cruelty offenses", "State cruelty prosecutions"]),
        (("shield", "sedition", "rfra"), ["State civil/criminal filings affected by protection", "Court legal-rights claims"]),
        (("pain_suffering", "lemonlaw", "fairtrade"), ["State affected civil case filings", "State civil damages or consumer complaints"]),
        (("grandvist",), ["State family-court visitation orders"]),
        (("physician_suicide", "livingwill"), ["State assisted-death reports", "CDC end-of-life mortality"]),
        (("sterilization",), ["State sterilization procedure records"]),
        (("teacher_cert", "corporal_punishment", "librarysystem", "schoolfordeaf", "biblereading", "moment_of_silence"), ["NCES affected school enrollment and staffing", "Civil Rights Data Collection school discipline"]),
        (("english_language", "instate_tuition"), ["ACS immigrant college enrollment", "ACS immigrant public-service access"]),
        (("tax", "tels", "debt_limit"), ["Census state tax receipts", "Census government revenue and spending"]),
        (("homerule", "utility_jurisdiction"), ["Census local-government revenue", "EIA regulated electricity prices"]),
    ]
    for patterns, candidates in groups:
        if any(pattern in name for pattern in patterns):
            return candidates
    return OUTCOMES.get(meta["category"], ["policy-specific public outcome linkage required"])


def load(raw_dir: Path | str | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    """Return rows, variable definitions and pinned file receipts without network.

    None remains unknown. No forward fill, interpolation, absorbing adoption
    transform, polarity inversion, or threshold selection is performed.
    """
    raw_dir = Path(raw_dir) if raw_dir is not None else DEFAULT_RAW
    receipts = json.loads((raw_dir / "receipts.json").read_text())
    for receipt in receipts:
        if receipt.get("role") not in {"panel", "codebook"}:
            continue
        path = raw_dir / Path(receipt["path"]).name
        data = path.read_bytes()
        if len(data) != receipt["size_bytes"] or hashlib.sha256(data).hexdigest() != receipt["sha256"]:
            raise ValueError(f"Receipt mismatch for {path.name}")
    with (raw_dir / "codebook_2-6.csv").open(newline="", encoding="utf-8-sig") as stream:
        codebook = {r["variable"]: r for r in csv.DictReader(stream)}
    wanted = set(CORE)
    for name, meta in codebook.items():
        if "Caughey" in meta["sources"] and meta["category"] != "policy-ideology" and name not in CW_EXCLUDED and not name.startswith("z_ssi_") and name != "z_tanf_paymentsperfamily":
            wanted.add(name)
    variables = []
    for name in sorted(wanted):
        meta = codebook[name]
        binary = _is_binary(meta)
        note = "Preserve source annual status including all reversals; legal enactment/effective-date clock remains subject to official audit."
        if not binary:
            note += " Parked: no predeclared threshold; ordinal codes also require a definition-specific threshold."
        else:
            note += " Code 1 denotes the exact source-defined predicate, including prohibitions, restrictions and abolition; retain its direction rather than interpreting it as liberalism."
        if name.startswith("education_teacher_cert"):
            note += " Codebook asks an adoption-year question but release values are 0/1; coding direction requires source clarification."
        if name == "x_labor_childlabor_workpermitage":
            note += " Codebook states 0/1 but release has age codes 14/15/16; parked pending source clarification."
        if name == "drugs_marijuana_decriminalization":
            note += " Name suggests decriminalization but definition asks whether possession is criminal; retain source direction and require audit."
        variables.append({
            "policy_id": f"cspp:{name}", "definition": meta["long_desc"],
            "label": meta["short_desc"], "kind": "binary_adoption" if binary else "continuous_intensity",
            "unit": _unit(meta, binary), "clock": "source-coded annual status; statutory clock unverified",
            "outcomes": _outcomes(meta),
            "source_id": SOURCE_ID, "notes": note, "declared_coverage": meta["years"],
            "original_sources": meta["sources"], "original_citation": meta["plaintext_cite"],
            "category": meta["category"], "threshold": None,
            "original_family": "caughey_warshaw" if "Caughey" in meta["sources"] else "cspp_core",
        })
    rows, observed_bounds = [], {}
    with (raw_dir / "correlates2-6.csv").open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        missing = wanted.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSPP release lacks selected variables: {sorted(missing)}")
        seen = set()
        for record in reader:
            state, year = record["st"], int(record["year"])
            key = (state, year)
            if key in seen:
                raise ValueError(f"Duplicate CSPP state/year: {key}")
            seen.add(key)
            for name in sorted(wanted):
                value = _number(record[name], name)
                rows.append({"state": state, "year": year, "policy_id": f"cspp:{name}", "value": value})
                if value is not None:
                    bounds = observed_bounds.setdefault(name, [year, year])
                    bounds[0] = min(bounds[0], year)
                    bounds[1] = max(bounds[1], year)
    for meta in variables:
        name = meta["policy_id"].split(":", 1)[1]
        meta["observed_coverage"] = observed_bounds.get(name)
    # Preserve all jurisdictions and internal missing cells in each policy's
    # observed window. Structural missing outside that window is metadata.
    rows = [row for row in rows if row["policy_id"].split(":", 1)[1] in observed_bounds and
            observed_bounds[row["policy_id"].split(":", 1)[1]][0] <= row["year"] <= observed_bounds[row["policy_id"].split(":", 1)[1]][1]]
    return rows, variables, receipts
