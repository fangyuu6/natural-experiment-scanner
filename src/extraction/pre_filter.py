"""
Title-based pre-filter: skip obvious non-substantive bills before LLM call.

Layer 1 — high-confidence title keyword matching.
Layer 2 — regex patterns for date-based ceremonial designations.
Conservative: only skips when >99.5% confident the bill is
ceremonial/commemorative/non-binding.
"""
import re

# Keywords that, when found in the lowercased title, indicate a ceremonial bill.
SKIP_TITLE_KEYWORDS = [
    # Person-level ceremonial (commendations)
    "congratulat",
    "upon the occasion",
    "in honor of",
    "in memory of",
    "appreciation for",
    "death mourned",
    ", mourned",

    # Recognition events
    "honoring the",
    "celebrating the",
    "; recognize",   # Georgia pattern: "Person; recognize"

    # Awareness / proclamation days / weeks / months
    "awareness day",
    "awareness week",
    "awareness month",
    "appreciation day",
    "appreciation week",
    "appreciation month",
    "recognition day",
    "recognition week",
    "recognition month",

    # Procedural / housekeeping
    "notifying the senate",
    "notifying the house",
    "special order calendar",

    # Proclamation / memorial to governor (Task 4)
    "memorializing governor",

    # Non-binding expressions (Task 8)
    "expressing support",
    "expressing sympathy",
    "expressing gratitude",
    "expressing the sense",
]

# Title prefixes (match start of lowercased title)
SKIP_TITLE_PREFIXES = [
    "memorials,",           # Tennessee pattern: "Memorials, Academic Achievement — ..."
    "urging ",              # Non-binding resolution: "Urging Congress to..." (Task 6)
    "joint resolution urging",  # (Task 6)
]

# "commend" matches many bills but some have RD/DID because title also contains
# "recommend" or "amend" (substantive verbs). Exclude those.
COMMEND_EXCLUDE = ["recommend", "amend", "act relating to"]

# Keywords that need exclusion lists to avoid false positives
_GUARDED_KEYWORDS = {
    # "tribute to" matches "contribute to" / "distribute to"
    "tribute to": ["contribute", "distribute"],
    # "retirement of" matches substantive retirement/utility policy (Task 1)
    "retirement of": ["retirement system", "retirement plan", "retirement benefit",
                      "retirement fund", "retirement act", "sales and use tax",
                      "levy", "option", "electric generation", "public utility",
                      "judicial", "discipline", "disability retirement",
                      "wind energy", "transmission"],
    # "recognizing the" — only skip clearly ceremonial patterns
    "recognizing the": ["recognizing the importance", "recognizing the need",
                        "recognizing the impact", "recognizing the role"],
    # "proclaim" — skip ceremonial proclamations, guard substantive emergency powers (Task 3)
    "proclaim": ["emergency", "authority", "powers", "executive order",
                 "state of emergency", "disaster"],
    # "resolution expressing" — skip non-binding, guard intent-to-legislate (Task 5)
    "resolution expressing": ["intent of the legislature", "intent to fund",
                              "intent to appropriate"],
}

# Task 2: "convening of the" REMOVED — only caught 1 bill and it was a false positive.
# Procedural "convening" cases are already caught by "notifying the senate/house".

# Layer 2: regex for date-based ceremonial designations (Task 7)
# Matches: "designates May 2, 2018, as X Day", "designating the month of June 2020 as..."
_DESIGNAT_DAY_RE = re.compile(
    r'designat\w*\s+'
    r'(?:the\s+)?(?:month\s+of\s+|week\s+of\s+)?'
    r'(?:january|february|march|april|may|june|july|august|september|october|november|december)'
    r'\s+\d',
    re.IGNORECASE,
)


def should_skip_bill(title: str, raw_text: str = "") -> tuple:
    """
    Return (True, reason) if the bill is almost certainly non-substantive.

    Layer 1: title keyword/prefix matching.
    Layer 2: regex patterns for date-based designations.
    raw_text is accepted for future expansion but not currently used.
    """
    t = (title or "").lower().strip()
    if not t:
        return False, ""

    # Layer 1a: exact keyword in title
    for kw in SKIP_TITLE_KEYWORDS:
        if kw in t:
            return True, f"ceremonial:{kw}"

    # Layer 1b: title prefix match
    for prefix in SKIP_TITLE_PREFIXES:
        if t.startswith(prefix):
            return True, f"ceremonial_prefix:{prefix}"

    # Layer 1c: "commend" with exclusion list
    if "commend" in t and not any(ex in t for ex in COMMEND_EXCLUDE):
        return True, "ceremonial:commend"

    # Layer 1d: guarded keywords — skip only if no exclusion term is present
    for kw, excludes in _GUARDED_KEYWORDS.items():
        if kw in t and not any(ex in t for ex in excludes):
            return True, f"ceremonial:{kw}"

    # Layer 2: date-pattern designations ("designates May 2, 2018, as X Day")
    if _DESIGNAT_DAY_RE.search(t):
        return True, "ceremonial:designat_date"

    return False, ""
