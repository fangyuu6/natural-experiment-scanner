"""Orchestrate chapter-split extraction on federal omnibus bills.

Wraps `extract_rd_did` so a callsite can use one function — `extract_with_chapters`
— regardless of whether the bill splits or not. If the bill trips the §2 trigger
(federal source, markers≥3, char_len≥10k), returns one result per chapter; else
returns one result total (the normal single-bill path).

Each chapter runs through `extract_rd_did` exactly like a standalone bill —
Step 6 (LLM call) and Step 7 (response handling) are unchanged; only the input
text and the `chapter_id` label on the output differ. See §5 of the WIP spec
(`src/docs/superpowers/specs/2026-04-20-multi-design-chapter-split-WIP.md`).

Manifest-first guarantee (§4.3): if the caller provides a `chapter_writer`
callback, we persist the chapter manifest BEFORE any LLM call, so a crash mid-
batch leaves behind enough state to resume by diffing results against chapters.

Boilerplate chapters (§4.1) bypass the LLM and get a stub result identical in
shape to the pre-filter stub from `extract_rd_did`.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ingestion.models import Policy
from extraction.chapter_split import (
    Chapter,
    is_boilerplate_chapter,
    should_split,
    split_chapters,
)
from extraction.llm_analyzer import extract_rd_did


def _stub_for_boilerplate(policy: Policy, chapter: Chapter, reason: str) -> Dict[str, Any]:
    """Mirror the shape of `extract_rd_did`'s pre-filter stub."""
    return {
        "policy_id": policy.id,
        "chapter_id": chapter.chapter_id,
        "parent_policy_id": policy.id,
        "title": f"{policy.title or ''} — {chapter.marker}",
        "jurisdiction": policy.jurisdiction or "",
        "document_url": policy.document_url or "",
        "introduced_date": str(policy.introduced_date) if policy.introduced_date else "",
        "rd_candidate": False,
        "did_candidate": False,
        "design_type": "",
        "primary_designs": '["NONE"]',
        "composite_score": 1,
        "feasibility_score": 1,
        "feasibility_label": "Skip",
        "is_feasible": False,
        "verdict": f"Chapter prefiltered: {reason}",
        "skipped_prefilter": reason,
        "policy_topic": "ceremonial",
    }


def _make_chapter_policy(policy: Policy, chapter: Chapter) -> Policy:
    """Produce a Policy whose `raw_text` is the chapter fragment.

    Keeps the original `id` so downstream logging stays grouped under the bill.
    The distinct `chapter_id` is stitched onto the result dict after extraction,
    not into the Policy.id field.
    """
    return policy.model_copy(update={
        "raw_text": chapter.text,
        "title": f"{policy.title or ''} — {chapter.marker}".strip(" —"),
    })


def extract_with_chapters(
    policy: Policy,
    *,
    chapter_writer: Optional[Callable[[str, List[Chapter]], None]] = None,
    llm_runner: Callable[..., Dict[str, Any]] = extract_rd_did,
    **llm_kwargs,
) -> List[Dict[str, Any]]:
    """Extract RD/DID evidence, splitting into chapters when applicable.

    Args:
        policy: the bill to analyze.
        chapter_writer: optional callback `(policy_id, chapters) -> None` invoked
            before any LLM call — use this to persist the chapter manifest first
            so a crash leaves behind recoverable state.
        llm_runner: the single-bill extractor. Defaults to `extract_rd_did`.
            Exposed for testing/mocking.
        **llm_kwargs: forwarded to `llm_runner` (model_override, api_key_override,
            base_url_override).

    Returns:
        List of result dicts. Non-split: a one-element list (result['chapter_id']
        is None). Split: one dict per chapter — boilerplate chapters have stub
        results, others have full `extract_rd_did` output decorated with
        `chapter_id` and `parent_policy_id`.
    """
    text = policy.raw_text or ""
    ok, _reason = should_split(policy.id, text)
    if not ok:
        result = llm_runner(policy, **llm_kwargs)
        result["chapter_id"] = None
        result["parent_policy_id"] = None
        return [result]

    chapters = split_chapters(policy.id, text)
    if len(chapters) < 2:
        # Split regex matched but structure didn't cleanly partition; fall back.
        result = llm_runner(policy, **llm_kwargs)
        result["chapter_id"] = None
        result["parent_policy_id"] = None
        return [result]

    if chapter_writer is not None:
        chapter_writer(policy.id, chapters)

    results: List[Dict[str, Any]] = []
    for ch in chapters:
        is_bp, bp_reason = is_boilerplate_chapter(ch.text)
        if is_bp:
            results.append(_stub_for_boilerplate(policy, ch, bp_reason))
            continue
        chapter_policy = _make_chapter_policy(policy, ch)
        r = llm_runner(chapter_policy, **llm_kwargs)
        r["chapter_id"] = ch.chapter_id
        r["parent_policy_id"] = policy.id
        results.append(r)
    return results
