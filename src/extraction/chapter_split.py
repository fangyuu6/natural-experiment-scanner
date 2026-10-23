"""Chapter split for multi-RD/DID extraction on federal omnibus bills.

Implements §1-§4 of WIP spec
`src/docs/superpowers/specs/2026-04-20-multi-design-chapter-split-WIP.md`.

Only federal bills (`govinfo-bills-*`) and committee reports (`govinfo-crpt-*`)
are splittable — state bills and Federal Register don't have structural chapters
(see §3 empirical findings: state "Section N." matches are statutory citations,
not dividers).

Public API:
    should_split(policy_id, raw_text) -> (bool, reason)
    split_chapters(policy_id, raw_text) -> List[Chapter]
    is_boilerplate_chapter(chapter_text) -> (bool, reason)
    make_chapter_id(policy_id, ord, marker) -> str
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

# --- § 2 Trigger rule ---
TRIGGER_MIN_MARKERS = 3
TRIGGER_MIN_CHARS = 10_000

# --- § 4.2 Cost budget ---
MAX_CHAPTERS = 8

# --- § 3 Structural regex (US federal only) ---
FEDERAL_MARKER_RE = re.compile(
    r'^\s*(TITLE\s+[IVXLCDM]+'
    r'|DIVISION\s+[A-Z](?![A-Z])'
    r'|CHAPTER\s+[IVXLCDM\d]+'
    r'|SUBTITLE\s+[A-Z]'
    r'|PART\s+[IVXLCDM\d]+)\b',
    re.MULTILINE,
)

# Federal bills often begin with a TABLE OF CONTENTS listing every TITLE/SEC.
# TOC occurrences are densely packed (small inter-marker gaps) in the document
# prologue. We strip a leading cluster of markers if it looks like a TOC:
# 3+ consecutive markers in the first 15% of the doc with <3000 char gaps.
# This correctly separates `govinfo-bills-112s1113is` (TOC + body, each title
# listed twice) while leaving `govinfo-crpt-crpt-112srpt99` (real SUBTITLE
# headers between chapters, spread throughout) intact.
_TOC_REGION_FRACTION = 0.15
_TOC_MAX_GAP = 3000
_TOC_MIN_CLUSTER = 3

# Conservative whitelist: only these sources have reliable TITLE/DIVISION structure.
# `govinfo-govpub-*` is excluded pending evaluation; `govinfo-chrg-*` is hearings
# (oral testimony, no statute structure); `fedreg-*` is Federal Register (agency-
# organized, not structural); `trial-*` is state legislation (no章节 structure).
FEDERAL_SPLITTABLE_PREFIXES = ("govinfo-bills-", "govinfo-crpt-")

_SLUG_RE = re.compile(r'[^a-z0-9]+')
_MONEY_RE = re.compile(
    r'\$\s?[\d,]+|\b\d+(?:\.\d+)?\s*(?:million|billion|thousand)\b',
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r'\b(?:19|20)\d{2}\b'
    r'|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b',
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r'\d+(?:\.\d+)?\s*(?:percent|%)', re.IGNORECASE)


@dataclass
class Chapter:
    chapter_id: str
    policy_id: str
    marker: str
    ord: int
    char_start: int
    char_end: int
    char_len: int
    text: str


def _slug(marker: str) -> str:
    return _SLUG_RE.sub('_', marker.strip().lower()).strip('_') or 'chapter'


def make_chapter_id(policy_id: str, ord: int, marker: str) -> str:
    """URL-safe chapter id: `{policy_id}--{ord:02d}-{slug(marker)}`.

    Uses `--` (not `#`) so the id survives URL paths without fragment collision.
    `ord` disambiguates duplicate markers (e.g. two TITLE III in one bill).
    """
    return f"{policy_id}--{ord:02d}-{_slug(marker)}"


def _is_splittable_source(policy_id: str) -> bool:
    pid = (policy_id or "").lower()
    return any(pid.startswith(p) for p in FEDERAL_SPLITTABLE_PREFIXES)


def should_split(policy_id: str, raw_text: str) -> Tuple[bool, str]:
    """Apply §2 trigger rule + §3 jurisdiction filter.

    Returns (True, '') if bill should be split, else (False, reason).
    """
    text = raw_text or ""
    if len(text) < TRIGGER_MIN_CHARS:
        return False, f"char_len={len(text)} < {TRIGGER_MIN_CHARS}"
    if not _is_splittable_source(policy_id):
        return False, f"source={policy_id[:30]} not in federal splittable whitelist"
    n_markers = len(FEDERAL_MARKER_RE.findall(text))
    if n_markers < TRIGGER_MIN_MARKERS:
        return False, f"markers={n_markers} < {TRIGGER_MIN_MARKERS}"
    return True, ""


def _strip_leading_toc(matches: List[re.Match], total_len: int) -> List[re.Match]:
    """Drop a leading TOC cluster from `matches` if one is present.

    Returns a possibly-shorter list. A TOC cluster is: 3+ consecutive markers
    all within the first 15% of the document, where each is within 3000 chars
    of the previous. If no such prefix exists, returns `matches` unchanged.
    """
    if len(matches) < _TOC_MIN_CLUSTER:
        return matches
    threshold = total_len * _TOC_REGION_FRACTION
    cluster_end = -1
    for i, m in enumerate(matches):
        if m.start() >= threshold:
            break
        if i == 0:
            cluster_end = 0
            continue
        gap = m.start() - matches[i - 1].start()
        if gap > _TOC_MAX_GAP:
            break
        cluster_end = i
    if cluster_end + 1 >= _TOC_MIN_CLUSTER:
        return matches[cluster_end + 1:]
    return matches


def split_chapters(policy_id: str, raw_text: str) -> List[Chapter]:
    """Split `raw_text` on federal structural markers.

    Preconditions: `should_split(policy_id, raw_text)` returned True.
    Honors §4.2 cap: if marker count exceeds `MAX_CHAPTERS`, the tail is merged
    into one "(remainder)" chapter so cost stays bounded.
    """
    matches = list(FEDERAL_MARKER_RE.finditer(raw_text))
    if len(matches) < TRIGGER_MIN_MARKERS:
        return []
    matches = _strip_leading_toc(matches, len(raw_text))
    if len(matches) < TRIGGER_MIN_MARKERS:
        return []

    starts = [m.start() for m in matches]
    markers = [raw_text[m.start():m.end()].strip() for m in matches]

    if len(matches) > MAX_CHAPTERS:
        keep = MAX_CHAPTERS - 1
        tail_marker = f"{markers[keep]}+ (remainder)"
        starts = starts[:keep] + [starts[keep]]
        markers = markers[:keep] + [tail_marker]

    chapters: List[Chapter] = []
    n = len(starts)
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < n else len(raw_text)
        chapters.append(Chapter(
            chapter_id=make_chapter_id(policy_id, i, markers[i]),
            policy_id=policy_id,
            marker=markers[i],
            ord=i,
            char_start=start,
            char_end=end,
            char_len=end - start,
            text=raw_text[start:end],
        ))
    return chapters


def is_boilerplate_chapter(chapter_text: str) -> Tuple[bool, str]:
    """§4.1 chapter-level prefilter.

    A chapter is boilerplate (skip LLM) if it's <2000 chars AND has no numeric
    signal (money / year / percent). Purpose: avoid wasting LLM on pure naming,
    short-title, or severability blocks.
    """
    text = chapter_text or ""
    if len(text) >= 2000:
        return False, ""
    if (_MONEY_RE.search(text) or _DATE_RE.search(text) or _PERCENT_RE.search(text)):
        return False, ""
    return True, f"short ({len(text)} chars) without money/date/percent"
