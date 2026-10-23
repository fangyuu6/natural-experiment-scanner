"""Deterministic G1 (really-enacted) classification. No LLM, no network.

verify_level: "id_verified"  — enactment proven by the document identifier itself
              "label_only"   — DB status label accepted but not independently verified
"""
import re

_PLAW_RE = re.compile(r"(plaw|publ)", re.IGNORECASE)
_BILLS_RE = re.compile(r"bills-", re.IGNORECASE)


def classify_enactment(policy_id, status, jurisdiction, source_family):
    status_l = (status or "").strip().lower()
    if status_l != "enacted":
        reason = "status_unknown" if status_l in ("", "unknown") else "status_not_enacted"
        return False, reason, "id_verified"

    pid = policy_id or ""
    is_federal = (jurisdiction or "").strip().lower() == "us federal"
    fam = (source_family or "").strip().lower()

    if is_federal and fam == "federal legislation":
        if _PLAW_RE.search(pid):
            return True, "public_law", "id_verified"
        if _BILLS_RE.search(pid):
            # Any BILLS-* version (ih/is/rs/rfs/eh/es/enr/...) is bill text, not law.
            return False, "bill_version_not_law", "id_verified"
        return True, "federal_status_enacted", "label_only"

    # "Federal Regulations" is the label actually used by the 483k corpus;
    # "Federal Register"/"regulations.gov" kept for other source snapshots.
    if fam in ("federal register", "federal regulations", "regulations.gov"):
        return True, "final_rule_label", "label_only"

    if is_federal:
        # A federal row must never carry a state_* reason.
        return True, "federal_status_enacted", "label_only"

    return True, "state_status_enacted", "label_only"
