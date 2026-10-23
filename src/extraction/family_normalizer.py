"""
Normalize policy_family labels against a registry of known families.

Runs after LLM returns a policy_family to prevent fragmentation.
Three-step deterministic normalization:
  1. Spelling fixes (childcare -> child_care, marijuana -> cannabis)
  2. Explicit alias table (sectoral_minimum_wage -> minimum_wage, etc.)
  3. Suffix merge (paid_family_leave_expansion -> paid_family_leave)

The registry is loaded once from the existing CSV on first call,
then grows as new canonical labels are accepted.
"""
import csv
import threading
from pathlib import Path
from typing import Dict, Optional, Set

_lock = threading.Lock()
_registry: Optional[Set[str]] = None
_CSV_PATH = Path(__file__).resolve().parents[1] / "output" / "policy_db.csv"

# ── Step 1: Spelling normalization ──
def _normalize_spelling(key: str) -> str:
    k = key.replace("childcare", "child_care")
    k = k.replace("marijuana", "cannabis")
    return k

# ── Step 2: Explicit aliases (source -> canonical) ──
_ALIASES: Dict[str, str] = {
    # ── Original aliases ──
    "sectoral_minimum_wage": "minimum_wage",
    "public_pension_reform": "pension_reform",
    "public_pension_administration": "pension_administration",
    "public_pension_benefit_expansion": "pension_benefit_expansion",
    "municipal_pension_administration": "pension_administration",
    "state_earned_income_tax_credit": "earned_income_tax_credit",
    "school_choice_voucher": "school_voucher",
    "medical_cannabis_legalization": "medical_cannabis",
    "administrative_report": "administrative_reporting",
    # ── Derived from Qwen 3.6 Plus stability test (100 bills × 5 runs) ──
    # civil service / federal employment
    "probationary_period": "civil_service_probation",
    "federal_probationary_period": "civil_service_probation",
    "federal_probation": "civil_service_probation",
    "federal_probation_period": "civil_service_probation",
    "federal_employment_rules": "civil_service_probation",
    "civil_service_rules": "civil_service_probation",
    "employment_protection": "civil_service_probation",
    # student aid / higher ed
    "higher_ed_grants": "student_aid",
    "student_aid_eligibility": "student_aid",
    "higher_education_access": "student_aid",
    "higher_ed_access": "student_aid",
    "higher_education_support": "student_aid",
    "federal_student_aid": "student_aid",
    # military / veteran benefits
    "military_survivor_benefits": "survivor_benefits",
    "military_benefits": "survivor_benefits",
    "sbp_dic_offset": "survivor_benefits",
    "gi_bill_benefits": "gi_bill",
    "veteran_education_benefits": "gi_bill",
    "veterans_education_benefits": "gi_bill",
    "tricare_eligibility": "health_insurance_eligibility",
    "military_health_insurance": "health_insurance_eligibility",
    "military_health_benefits": "health_insurance_eligibility",
    "veterans_benefits": "survivor_benefits",
    # federal pay
    "federal_pay_adjustment": "hazard_pay",
    "federal_pay": "hazard_pay",
    "federal_compensation": "hazard_pay",
    "federal_pay_supplement": "hazard_pay",
    # gig worker
    "gig_worker_equity_compensation": "gig_worker_compensation",
    "gig_worker_classification": "gig_worker_compensation",
    "worker_classification": "gig_worker_compensation",
    "gig_worker_equity": "gig_worker_compensation",
    # electoral
    "electoral_college_abolition": "electoral_reform",
    # workplace safety
    "occupational_safety": "workplace_safety",
    # net operating loss
    "net_operating_loss_limitation": "net_operating_loss",
    "net_operating_loss_carryover": "net_operating_loss",
    # infrastructure
    "transportation_infrastructure": "infrastructure_spending",
    # tort
    "tort_liability_immunity": "tort_liability",
    "tort_liability_shield": "tort_liability",
    "tort_liability_reform": "tort_liability",
    # energy
    "weatherization_program": "energy_assistance",
    "low_income_energy_assistance": "energy_assistance",
    # education workforce
    "educator_stipend": "school_counselor_stipend",
    "teacher_compensation": "school_counselor_stipend",
    "education_subsidy": "school_counselor_stipend",
    # reporting
    "reporting_requirement": "reporting_mandate",
    # immigration
    "immigration_deportation_grounds": "immigration_enforcement",
    "deportation_grounds": "immigration_enforcement",
    "immigration_deportation_criteria": "immigration_enforcement",
    "criminal_inadmissibility": "immigration_enforcement",
    "criminal_deportability": "immigration_enforcement",
    "criminal_deportation": "immigration_enforcement",
    # privacy / surveillance
    "alpr_privacy": "data_privacy",
    "alpr_regulation": "data_privacy",
    "surveillance_regulation": "data_privacy",
    "data_privacy_regulation": "data_privacy",
    # domestic violence
    "vexatious_litigant_reform": "domestic_violence_protection",
    "domestic_violence_litigation": "domestic_violence_protection",
    "domestic_violence_court_procedure": "domestic_violence_protection",
    # organ transplant
    "organ_donation_grants": "organ_transplant",
    "organ_transplant_waitlist": "organ_transplant",
    "organ_procurement_grants": "organ_transplant",
    "organ_transplant_oversight": "organ_transplant",
    "organ_donation": "organ_transplant",
    "transplant_grants": "organ_transplant",
    "organ_transplant_reform": "organ_transplant",
    # congressional
    "congressional_ethics": "congressional_accountability",
    "congressional_labor_standards": "congressional_accountability",
    # anti-discrimination / civil rights
    "civil_rights": "anti_discrimination",
    # social security
    "social_security_retirement_age": "social_security",
    # health insurance governance
    "health_insurance_regulation": "health_insurance_governance",
    "health_insurance_compact": "health_insurance_governance",
    "state_health_insurance_compact": "health_insurance_governance",
    # reporting harmonization
    "reporting_harmonization": "reporting_simplification",
    "regulatory_reporting_simplification": "reporting_simplification",
    "administrative_reporting": "reporting_simplification",
    # school discipline
    "school_policing": "school_discipline",
    "school_discipline_reform": "school_discipline",
    "school_discipline_policy": "school_discipline",
    # asylum
    "asylum_application": "asylum_application_form",
    "asylum_application_form": "asylum_application_form",
}

# ── Step 3: Suffix patterns that should merge into parent ──
_MERGE_SUFFIXES = {
    "expansion", "reform", "increase", "adjustment", "extension",
    "delay", "amendment", "clarification", "technical",
    "repeal", "suspension", "moratorium", "sunset",
    "local_opt_in", "opt_in",
}


def _load_registry() -> Set[str]:
    """Load existing canonical family labels from CSV, pre-cleaned.

    Applies spelling normalization + alias + suffix collapse so the
    registry only contains canonical (parent) labels.
    """
    raw_families: Set[str] = set()
    if not _CSV_PATH.exists():
        return raw_families
    try:
        with _CSV_PATH.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                fam = (row.get("policy_family") or "").strip().lower().replace(" ", "_")
                if fam and not fam.startswith("unique_"):
                    raw_families.add(_normalize_spelling(_ALIASES.get(_normalize_spelling(fam), _normalize_spelling(fam))))
    except Exception:
        pass

    # Collapse suffix variants: if both "X" and "X_expansion" exist, keep only "X"
    canonical: Set[str] = set()
    for fam in sorted(raw_families):
        parts = fam.split("_")
        merged = False
        for n in range(len(parts) - 1, 1, -1):
            candidate = "_".join(parts[:n])
            suffix = "_".join(parts[n:])
            if suffix in _MERGE_SUFFIXES and candidate in raw_families:
                merged = True
                break
        if not merged:
            canonical.add(fam)
    return canonical


def _find_suffix_parent(key: str, registry: Set[str]) -> Optional[str]:
    """If key = parent + _suffix and parent exists in registry, return parent."""
    parts = key.split("_")
    for n in range(len(parts) - 1, 1, -1):
        candidate = "_".join(parts[:n])
        suffix = "_".join(parts[n:])
        if suffix in _MERGE_SUFFIXES and candidate in registry:
            return candidate
    return None


def normalize_family(raw_family: str) -> str:
    """Normalize a policy_family label against the known registry.

    Call this after LLM returns policy_family, before writing to CSV.
    Thread-safe; loads registry once from CSV on first call.
    """
    global _registry
    if not raw_family or raw_family.startswith("unique_"):
        return raw_family

    # Load registry on first call
    with _lock:
        if _registry is None:
            _registry = _load_registry()

    # Step 1: spelling normalization
    key = _normalize_spelling(raw_family)

    # Step 2: explicit alias
    key = _ALIASES.get(key, key)

    # Step 3: suffix merge (e.g. paid_family_leave_expansion -> paid_family_leave)
    if key not in _registry:
        parent = _find_suffix_parent(key, _registry)
        if parent:
            return parent

    # Accept (existing canonical or new label)
    if key not in _registry:
        with _lock:
            _registry.add(key)
    return key
