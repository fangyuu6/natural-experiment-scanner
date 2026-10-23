"""Date-window confound flags from the ralph-loop kill taxonomy. Pure functions."""
from datetime import date

_ABORTION_HINTS = ("abortion", "reproductive", "contracept")
# Families are snake_case controlled vocab, so a bare "drug" prefix is safe there.
# Titles are free text: require drug-specific terms ("drug schedul", not bare
# "schedul") to avoid wage/fee-schedule false positives.
_DRUG_FAMILY_HINTS = ("drug", "opioid", "fentanyl", "kratom", "controlled_substance",
                      "narcotic")
_DRUG_TITLE_HINTS = ("opioid", "fentanyl", "kratom", "narcotic", "controlled substance",
                     "drug schedul")


def _parse(date_iso):
    try:
        y, m, d = (date_iso or "").split("-")
        return date(int(y), int(m), int(d))
    except (ValueError, AttributeError):
        return None


def flag_confounds(date_iso, policy_topic, policy_family, title):
    d = _parse(date_iso)
    if d is None:
        return []
    flags = []
    fam = (policy_family or "").lower()
    ttl = (title or "").lower()
    # policy_topic kept in the signature for interface stability; no current
    # window keys off topic alone.

    if date(2020, 1, 1) <= d <= date(2021, 12, 31):
        flags.append("covid_window")
    if date(2008, 9, 1) <= d <= date(2010, 12, 31):
        flags.append("great_recession_window")
    if d >= date(2022, 6, 24) and any(h in fam or h in ttl for h in _ABORTION_HINTS):
        flags.append("post_dobbs")
    if date(2014, 1, 1) <= d <= date(2023, 12, 31) and (
            any(h in fam for h in _DRUG_FAMILY_HINTS) or
            any(h in ttl for h in _DRUG_TITLE_HINTS)):
        flags.append("fentanyl_wave")
    return flags
