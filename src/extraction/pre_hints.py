"""
Pre-LLM design hint generation.
Scans bill text for patterns that suggest specific research designs
(e.g., recertification thresholds → RD) and generates hint strings
to prepend to the LLM prompt so it evaluates these designs.
"""
import re
from typing import List


def detect_recertification(text: str) -> tuple:
    """Detect recertification/decertification threshold in bill text.
    Returns (percentage: str, quote: str) or (None, None) if not found.
    Shared between pre_hints and llm_analyzer post-processing fallback.
    """
    if not text or "recertif" not in text.lower():
        return None, None
    # Strip line numbers (FL bill HTML inserts "285\n" between digits) then normalize whitespace
    cleaned = re.sub(r"\n\d{1,4}\n", "\n", text)
    normalized = re.sub(r"\s+", " ", cleaned)
    match = None
    pct = None
    # Pattern 1: contiguous digits "60 percent of the employees eligible...pay dues...recertif"
    # Use .{0,500}? instead of [^.]*? — legal text has "s. 447.307" which contains periods
    m = re.search(
        r"(\d{2,})\s*percent\s+of\s+the\s+employees\s+eligible\s+for\s+representation.{0,500}?pay\s+dues.{0,500}?recertif",
        normalized, re.IGNORECASE | re.DOTALL,
    )
    if m:
        pct, match = m.group(1), m
    if not match:
        # Pattern 2: split digits "6 0 percent..." (line number removed between digits)
        m = re.search(
            r"(\d)\s+(\d)\s*percent\s+of\s+the\s+employees\s+eligible\s+for\s+representation.{0,500}?pay\s+dues.{0,500}?recertif",
            normalized, re.IGNORECASE | re.DOTALL,
        )
        if m:
            pct, match = m.group(1) + m.group(2), m
    if not match:
        # Pattern 3: "less than X percent...recertif"
        m = re.search(
            r"(?:less than|below|had less than)\s+(\d{2,})\s*percent.{0,500}?recertif",
            normalized, re.IGNORECASE | re.DOTALL,
        )
        if m:
            pct, match = m.group(1), m
    if not match:
        # Pattern 4: generic "X percent...recertif"
        m = re.search(r"(\d{2,})\s*percent.{0,500}?recertif", normalized, re.IGNORECASE | re.DOTALL)
        if m:
            pct, match = m.group(1), m
    if not match or not pct or not pct.isdigit():
        return None, None
    start = max(0, match.start() - 60)
    quote = normalized[start:match.end() + 280].strip()
    if len(quote) > 400:
        quote = quote[:397] + "..."
    return pct, quote


def detect_targeted_program_did(text: str) -> bool:
    """Detect optional / targeted policy changes that often imply DID."""
    if not text:
        return False
    low = text.lower()
    policy_change = any(
        phrase in low for phrase in [
            "introduced",
            "reintroduced",
            "added",
            "removed",
            "appropriated",
            "funded",
            "started covering",
            "now covers",
        ]
    )
    limited_exposure = any(
        phrase in low for phrase in [
            "optional",
            "practice-level",
            "provider-level",
            "adoption was",
            "adopters",
            "participants",
            "eligible",
            "beneficiaries",
            "not universal",
            "specific to",
        ]
    )
    targeted_context = any(
        phrase in low for phrase in [
            "program",
            "reimbursement",
            "billing code",
            "cpt",
            "voucher",
            "grant",
            "incentive",
            "monitoring",
            "warning",
            "label",
            "designation",
            "medicare",
            "medicaid",
            "snap",
        ]
    )
    return policy_change and limited_exposure and targeted_context


def generate_design_hints(bill_text: str) -> List[str]:
    """Scan bill text before LLM call and generate design hints.
    These hints are prepended to the user prompt so the LLM evaluates
    specific designs it might otherwise miss.
    """
    hints = []
    # Recertification / decertification RD
    pct, _ = detect_recertification(bill_text)
    if pct:
        hints.append(
            f"DESIGN HINT: This bill contains a recertification/decertification "
            f"threshold at {pct}%. Evaluate as potential RD with "
            f"running_variable = dues_paying_share, cutoff = {pct}%. "
            f"Also evaluate DID (interstate or within-bill exemptions) — "
            f"output BOTH designs in primary_designs if applicable."
        )
    if detect_targeted_program_did(bill_text):
        hints.append(
            "DESIGN HINT: This text appears to describe an optional or targeted "
            "policy change rather than universal treatment. Evaluate DID using "
            "adopters, participants, or more-exposed eligible units versus "
            "otherwise similar non-adopters, non-participants, or less-exposed units. "
            "Voluntary take-up does not by itself invalidate DID."
        )
    return hints
