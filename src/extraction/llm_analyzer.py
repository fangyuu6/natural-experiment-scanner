"""
LLM-based RD/DID extraction. Single prompt per policy, JSON output.
"""
import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

import httpx
from openai import OpenAI

from config.settings import (
    OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL,
    LITERATURE_LLM_API_KEY, LITERATURE_LLM_BASE_URL, LITERATURE_LLM_MODEL,
)

# ── Multi-key rotation with per-key token bucket ──
# Set OPENAI_API_KEYS=key1,key2,key3 in .env for round-robin rotation.
# Falls back to single OPENAI_API_KEY if not set.
import random as _random

_API_KEYS: List[str] = []
_raw_keys = os.getenv("OPENAI_API_KEYS", "")
if _raw_keys:
    _API_KEYS = [k.strip() for k in _raw_keys.split(",") if k.strip()]
if not _API_KEYS and OPENAI_API_KEY:
    _API_KEYS = [OPENAI_API_KEY]

# Per-key token bucket: controls actual request rate per key.
# Target ~500 RPM per key (well under 600 RPM limit).
_RPM_PER_KEY = int(os.getenv("LLM_RPM_PER_KEY", "580"))
_BUCKET_INTERVAL = 60.0 / _RPM_PER_KEY  # seconds between tokens per key

class _KeyBucket:
    """Token bucket rate limiter per API key."""
    __slots__ = ("key", "lock", "next_ok")

    def __init__(self, key: str):
        self.key = key
        self.lock = threading.Lock()
        self.next_ok = 0.0  # epoch when next request is allowed

    def acquire(self) -> float:
        """Wait until this key is available. Returns wait time."""
        with self.lock:
            now = time.time()
            if now >= self.next_ok:
                self.next_ok = now + _BUCKET_INTERVAL
                return 0.0
            wait = self.next_ok - now
            self.next_ok += _BUCKET_INTERVAL
        time.sleep(wait)
        return wait

_key_buckets: List[_KeyBucket] = [_KeyBucket(k) for k in _API_KEYS]
_key_counter = 0
_key_lock = threading.Lock()


def _next_api_key_throttled() -> tuple:
    """Round-robin key selection with token bucket wait. Returns (key, bucket)."""
    global _key_counter
    with _key_lock:
        idx = _key_counter % len(_key_buckets)
        _key_counter += 1
    bucket = _key_buckets[idx]
    bucket.acquire()
    return bucket.key


def _next_api_key() -> str:
    """Round-robin across available API keys (unthrottled, for literature etc.)."""
    global _key_counter
    with _key_lock:
        key = _API_KEYS[_key_counter % len(_API_KEYS)]
        _key_counter += 1
        return key

# ── In-flight limiter: caps concurrent API calls regardless of worker count ──
# 11 keys × 500 RPM ≈ 92 req/s; at 3-5s per call, ~400 in-flight saturates.
_MAX_INFLIGHT = int(os.getenv("LLM_MAX_INFLIGHT", "1600"))
_inflight_sem = threading.BoundedSemaphore(_MAX_INFLIGHT)

# Shared HTTP connection pool
_MAX_CONN = int(os.getenv("LLM_MAX_CONNECTIONS", "3000"))
_shared_http_client = httpx.Client(
    limits=httpx.Limits(max_connections=_MAX_CONN, max_keepalive_connections=_MAX_CONN // 3),
    timeout=httpx.Timeout(15.0, connect=5.0),
)
from ingestion.models import Policy
from extraction.feasibility_scorer import (
    calculate_feasibility_score, get_feasibility_label, get_feasibility_recommendations,
    get_evidence_tier, get_is_feasible,
)
from extraction.prompts_aer import (
    SYSTEM_PROMPT, USER_PROMPT_TEMPLATE,
    LITERATURE_SYSTEM_PROMPT, LITERATURE_USER_PROMPT_TEMPLATE,
)
from extraction.confound_detector import detect_confounds
from extraction.family_normalizer import normalize_family
from extraction.pre_filter import should_skip_bill
from extraction.pre_hints import generate_design_hints
from extraction.schemas import AERExtraction
from pydantic import ValidationError

import logging
logger = logging.getLogger(__name__)

# Module-level toggle: set to False to disable pre-filtering (e.g. --no-prefilter)
PREFILTER_ENABLED = True
# Skip literature LLM call (for vote-only runs)
SKIP_LITERATURE = False


# Scoring weights (Python-computed composite)
W_ID, W_PS, W_TI, W_DF = 0.35, 0.25, 0.20, 0.20

# Confound severity ordering
_SEV = {"none": 0, "manageable": 1, "severe": 2, "fatal": 3}

# Canonical running variable types (stabilization: map LLM free-text → standard enum)
_RV_CANONICAL = {
    "child_age": "age", "birth_date": "age", "age_at_offense": "age",
    "age_of_the_income_eligible_person": "age", "child_s_age": "age",
    "firm_size": "employees", "headcount": "employees", "fte": "employees",
    "number_of_employees": "employees", "workforce_size": "employees",
    "employer_size": "employees", "company_size": "employees",
    "household_income": "income", "earnings": "income", "wage": "income",
    "income_level": "income", "adjusted_gross_income": "income",
    "student_enrollment": "enrollment", "school_enrollment": "enrollment",
    "birth_date_relative_to_cutoff": "date", "effective_date": "date",
}
_RV_KEYWORD_MAP = [
    ("age", "age"), ("employee", "employees"), ("firm", "employees"),
    ("worker", "employees"), ("headcount", "employees"), ("fte", "employees"),
    ("income", "income"), ("earning", "income"), ("wage", "income"),
    ("enroll", "enrollment"), ("population", "population"),
    ("date", "date"), ("dues", "dues_paying_share"), ("percent", "dues_paying_share"),
    ("test_score", "test_score"), ("score", "test_score"),
]

def _canonicalize_rv(rv: str) -> str:
    """Normalize running_variable_type to canonical enum value."""
    if not rv:
        return ""
    rv_clean = re.sub(r"[^a-z0-9_]", "_", rv.lower().strip()).strip("_")
    # Already canonical
    _CANONICAL_SET = {"age", "employees", "income", "population", "enrollment",
                      "date", "dues_paying_share", "test_score"}
    if rv_clean in _CANONICAL_SET:
        return rv_clean
    # Exact alias match
    if rv_clean in _RV_CANONICAL:
        return _RV_CANONICAL[rv_clean]
    # Keyword match
    for keyword, canonical in _RV_KEYWORD_MAP:
        if keyword in rv_clean:
            return canonical
    return rv_clean


def _safe_score(val, default=None):
    """Convert LLM score to 1-5 float. Returns default if unparseable."""
    if val is None:
        return default
    try:
        f = float(val)
        return max(1.0, min(5.0, f))
    except (TypeError, ValueError):
        return default


def _call_llm(prompt: str, max_retries: int = 5,
              model_override: str = None,
              api_key_override: str = None,
              base_url_override: str = None) -> str:
    """Call LLM with token bucket + in-flight semaphore + jittered backoff.

    Flow: acquire token bucket slot (rate) → acquire semaphore (concurrency) → call API.
    """
    model = model_override or OPENAI_MODEL

    for attempt in range(max_retries):
        try:
            # 1) Token bucket: controls per-key request rate
            key = api_key_override or _next_api_key_throttled()
            # 2) In-flight semaphore: caps total concurrent API calls
            _inflight_sem.acquire()
            try:
                client = OpenAI(
                    api_key=key,
                    base_url=base_url_override or OPENAI_BASE_URL or None,
                    http_client=_shared_http_client,
                )
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                    timeout=60.0,
                )
            finally:
                _inflight_sem.release()
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            is_rate_limit = "429" in str(e) or "rate" in str(e).lower()

            if attempt == max_retries - 1:
                raise Exception(f"LLM call failed after {max_retries} attempts: {str(e)}")

            # Jittered exponential backoff: base * 2^attempt * (0.5–1.5)
            base = 3 if is_rate_limit else 1
            delay = base * (2 ** (attempt + 1)) * (0.5 + _random.random())
            logger.debug("Retry %d/%d in %.1fs: %s", attempt + 1, max_retries, delay, str(e)[:80])
            time.sleep(delay)



def _call_literature_llm(policy_summary: Dict[str, Any], max_retries: int = 2) -> Optional[Dict[str, Any]]:
    """Call DeepSeek (or fallback) for literature assessment. Returns parsed dict or None."""
    base_url = LITERATURE_LLM_BASE_URL or OPENAI_BASE_URL or None
    model = LITERATURE_LLM_MODEL or OPENAI_MODEL

    if not (LITERATURE_LLM_API_KEY or _API_KEYS):
        return None

    prompt = LITERATURE_USER_PROMPT_TEMPLATE.format(
        title=policy_summary.get("title") or "",
        jurisdiction=policy_summary.get("jurisdiction") or "",
        introduced_date=policy_summary.get("introduced_date") or "N/A",
        design_type=policy_summary.get("design_type") or "",
        intuition_pitch=policy_summary.get("intuition_pitch") or "",
        treatment_description=policy_summary.get("treatment_description") or "",
        potential_outcomes=policy_summary.get("potential_outcomes") or "",
        policy_topic=policy_summary.get("policy_topic") or "",
        policy_family=policy_summary.get("policy_family") or "",
        cutoff_value=policy_summary.get("cutoff_value") or "",
        recommended_datasets=policy_summary.get("recommended_datasets") or "",
    )

    for attempt in range(max_retries):
        try:
            api_key = LITERATURE_LLM_API_KEY or _next_api_key()
            client = OpenAI(api_key=api_key, base_url=base_url, http_client=_shared_http_client)
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": LITERATURE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=2000,
                temperature=0,
                timeout=20.0,
            )
            raw = (resp.choices[0].message.content or "").strip()
            parsed = _parse_json_from_response(raw)
            return parsed
        except Exception as e:
            logger.warning(f"Literature LLM attempt {attempt+1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
    return None


def _merge_literature_results(out: Dict[str, Any], lit: Dict[str, Any]) -> None:
    """Merge literature LLM results into the main output dict."""
    lit_landscape = lit.get("literature_landscape") or {}
    if isinstance(lit_landscape, dict):
        out["llm_literature_saturation"] = (lit_landscape.get("saturation") or "").strip()
        out["llm_literature_reasoning"] = (lit_landscape.get("reasoning") or "").strip()
        known = lit_landscape.get("known_similar_studies") or []
        if isinstance(known, list):
            out["llm_known_similar_studies"] = json.dumps(known, ensure_ascii=False)
        else:
            out["llm_known_similar_studies"] = "[]"
        out["unique_contribution"] = (lit_landscape.get("unique_contribution") or "").strip()

    angles = lit.get("research_angles") or []
    if isinstance(angles, list) and angles:
        out["research_angles"] = json.dumps(angles, ensure_ascii=False)


def _infer_policy_status_from_raw_text(raw_text: str) -> Optional[str]:
    """
    从 raw_text 中根据关键词推断 policy_status（全文本匹配，不依赖 "last action:"）。
    用于 Florida、Virginia 等州：enacted 信号含 "signed by governor", "chaptered", "effective date" 等。
    """
    if not raw_text:
        return None
    text = raw_text.lower()
    # Enacted 信号（整段文本中任一命中即视为已生效）
    enacted_signals = [
        "chapter no.",
        " ch. 20",
        "approved by governor",
        "signed into law",
        "signed by governor",
        "signed by the governor",
        "chaptered",
        "effective date:",
        "effective date",
    ]
    for sig in enacted_signals:
        if sig in text:
            return "enacted"
    # 未通过：委员会死亡、撤回等（且未命中 enacted）
    if " died in " in text or "withdrawn" in text or "laid on table" in text:
        return "proposed"
    return None


def _apply_strict_rd_did_rules(out: Dict[str, Any]) -> None:
    """
    严格规则：未生效、阈值未知、操纵风险中高的政策不得标为 RD/DID 候选（无法做可信经济学研究）。
    """
    status = (out.get("policy_status") or "").strip().lower()
    if status == "proposed":
        out["rd_candidate"] = False
        out["did_candidate"] = False
        return
    # RD: 必须有具体阈值（操纵风险高的设计仍保留，由 identification 分数反映风险）
    if out.get("rd_candidate"):
        cutoff = str(out.get("cutoff_value") or "").strip().lower()
        if not cutoff or cutoff == "unknown" or cutoff == "n/a":
            out["rd_candidate"] = False
        # Note: we no longer block high/medium manipulation_risk RD designs here.
        # Instead, identification score is penalised in _map_aer_to_legacy().


def _inject_rd_from_percent_recertification(policy: Policy, out: Dict[str, Any]) -> None:
    """
    规则补全：正文中若出现「X percent of ... pay dues ... recertification」类表述，
    则无论 LLM 是否标出，都补上 RD 候选（60% 会费缴纳率是典型 RD 设计）。
    注：Bill Text HTML 常有行号插入导致 "60" 变成 "6\\n0"，故先做空白规范化再匹配。
    """
    raw = policy.raw_text or ""
    if not raw or "recertif" not in raw.lower():
        return
    # 去掉行号（FL bill text 中 "6\n285\n0 percent" → "6 0 percent"），再做空白规范化
    # 行号特征：独占一行或在行首的纯数字（1-4位），前后都是换行
    cleaned = re.sub(r"\n\d{1,4}\n", "\n", raw)
    normalized = re.sub(r"\s+", " ", cleaned)
    match = None
    pct = None
    # 法律全文含 "s. 447.307" 等条款引用，句号会打断 [^.]*? 匹配，
    # 改用 .{0,500}? （允许任意字符），recertif 锚点足够精确不会误匹配
    # 模式1：连续数字 "60 percent of the employees eligible"（至少两位数，排除行号残留的单个 0）
    m = re.search(
        r"(\d{2,})\s*percent\s+of\s+the\s+employees\s+eligible\s+for\s+representation.{0,500}?pay\s+dues.{0,500}?recertif",
        normalized,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        pct = m.group(1)
        match = m
    if not match:
        # 模式2：行号拆开数字 "6 0 percent of the employees eligible"
        m = re.search(
            r"(\d)\s+(\d)\s*percent\s+of\s+the\s+employees\s+eligible\s+for\s+representation.{0,500}?pay\s+dues.{0,500}?recertif",
            normalized,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            pct = m.group(1) + m.group(2)
            match = m
    if not match:
        # 模式3：仅 "X percent ... recertif"
        m = re.search(
            r"(?:less than|below|had less than)\s+(\d{2,})\s*percent.{0,500}?recertif",
            normalized,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            pct = m.group(1)
            match = m
    if not match:
        m = re.search(r"(\d{2,})\s*percent.{0,500}?recertif", normalized, re.IGNORECASE | re.DOTALL)
        if m:
            pct = m.group(1)
            match = m
    if not match or not pct or not pct.isdigit():
        return
    # 用于 quote 的片段
    start = max(0, match.start() - 60)
    quote = normalized[start : match.end() + 280].strip()
    if len(quote) > 400:
        quote = quote[:397] + "..."
    out["rd_candidate"] = True
    out["rd_score_source"] = "rule_injected_unscored"
    out["cutoff_value"] = pct
    out["cutoff_unit"] = "percent"
    out["running_variable_type"] = out.get("running_variable_type") or "dues_paying_share"
    out["direction"] = out.get("direction") or "below_cutoff"
    if not (out.get("quote_snippet") or "percent" in (out.get("quote_snippet") or "")):
        out["quote_snippet"] = quote
    if not (out.get("rd_reasoning") or "").strip():
        out["rd_reasoning"] = "Dues-paying share below threshold triggers recertification; suitable for RD"


def _validate_extraction(parsed: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Run Pydantic validation on the parsed LLM JSON.

    Returns (validated_dict, None) on success or (None, error_summary) on failure.
    The error summary is a short human/LLM-readable description used to guide retry.
    """
    try:
        model = AERExtraction.model_validate(parsed)
    except ValidationError as e:
        errs = e.errors()
        lines = []
        for err in errs[:3]:
            loc = ".".join(str(x) for x in err.get("loc", []))
            msg = err.get("msg", "invalid")
            lines.append(f"- {loc}: {msg}")
        summary = "Pydantic validation failed:\n" + "\n".join(lines)
        return None, summary
    return model.model_dump(exclude_none=False), None


def _build_retry_prompt(original_prompt: str, failed_json: Dict[str, Any], error_summary: str) -> str:
    """Construct a follow-up prompt that asks the LLM to fix specific validation errors."""
    preview = json.dumps(failed_json, ensure_ascii=False)[:1500]
    return (
        original_prompt
        + "\n\n## Previous attempt was rejected by schema validation\n"
        + error_summary
        + "\n\n## Your previous output (truncated)\n"
        + preview
        + "\n\n## Instruction\nReturn a corrected JSON object that fixes the errors above. "
        + "Score fields must be integers 1-5. Lists must be JSON arrays, not strings. "
        + "Output valid JSON only, no markdown fences."
    )


def _parse_json_from_response(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    # Remove markdown code block if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback 1: extract largest JSON object via greedy regex
    # Handles thinking tokens, preamble text, or trailing commentary
    m = re.search(r'\{', text)
    if m:
        # Find the matching closing brace by counting braces
        depth = 0
        start = m.start()
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break

    # Fallback 2: truncated JSON repair — close open braces/brackets
    if m:
        start = m.start()
        # Take everything from first { to end, then try to close it
        partial = text[start:]
        # Count unclosed braces and brackets
        open_braces = partial.count('{') - partial.count('}')
        open_brackets = partial.count('[') - partial.count(']')
        # Remove trailing incomplete string (after last complete value)
        repaired = partial.rstrip()
        # Remove trailing comma or incomplete key-value
        repaired = re.sub(r',\s*"[^"]*$', '', repaired)  # trailing incomplete key
        repaired = re.sub(r',\s*$', '', repaired)  # trailing comma
        repaired += ']' * max(0, open_brackets) + '}' * max(0, open_braces)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # Fallback 3: regex for JSON with known key (primary_designs or scores)
    for key in ("primary_designs", "scores", "primary_design"):
        pat = re.compile(r'\{[^{}]*"' + key + r'".*\}', re.DOTALL)
        m2 = pat.search(text)
        if m2:
            try:
                return json.loads(m2.group())
            except json.JSONDecodeError:
                continue

    logger.warning("_parse_json_from_response: all fallbacks failed, preview: %s", text[:200])
    return None


def _is_aer_format(out: Dict[str, Any]) -> bool:
    """True if LLM returned AER-style JSON (primary_designs array or primary_design + scores)."""
    has_designs = isinstance(out.get("primary_designs"), list) or isinstance(out.get("primary_design"), str)
    return has_designs and isinstance(out.get("scores"), dict)


def _parse_running_variable_or_cutoff(s: str) -> tuple:
    """Parse design_evidence.running_variable_or_cutoff -> (running_variable_type, cutoff_value, cutoff_unit)."""
    if not s or not isinstance(s, str):
        return ("", "", "")
    s = s.strip()
    # e.g. "60 percent dues-paying" or "60 percent"
    m = re.search(r"(\d+)\s*percent", s, re.IGNORECASE)
    if m:
        return ("dues_paying_share", m.group(1), "percent")
    # e.g. "Age 65", "age 65"
    m = re.search(r"age\s*(\d+)", s, re.IGNORECASE)
    if m:
        return ("age", m.group(1), "years")
    # e.g. "Employees > 50", "employees > 50", "firm size 50"
    m = re.search(r"(?:employees?|firm\s*size)\s*[><=]?\s*(\d+)", s, re.IGNORECASE)
    if m:
        return ("employees", m.group(1), "employees")
    # e.g. "Income > 20000", "income 20000"
    m = re.search(r"income\s*[><=]?\s*(\d+)", s, re.IGNORECASE)
    if m:
        return ("income", m.group(1), "USD")
    # generic: "X Y" -> (y, x, y) e.g. "Population 10000" -> (population, 10000, population)
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if m:
        typ = re.sub(r"\d+", "", s).strip().lower().replace(" ", "_") or "threshold"
        return (typ or "threshold", m.group(1), typ or "units")
    return ("", "", "")


_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}
_US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
}



def _is_state_level_jurisdiction(jurisdiction: str) -> bool:
    """Return True if jurisdiction looks like a US state (not federal, not city/county)."""
    if not jurisdiction:
        return False
    j = jurisdiction.strip()
    j_lower = j.lower()
    # Exclude federal
    if j_lower in ("us", "usa", "united states", "federal"):
        return False
    # Exclude city/county patterns
    if any(k in j_lower for k in ("city of", "county of", "township", "borough")):
        return False
    # Check state abbreviation (e.g. "CO", "NY", "CA")
    if j.upper() in _US_STATES:
        return True
    # Check full state name (e.g. "Colorado", "New York")
    if j_lower in _US_STATE_NAMES:
        return True
    # Check patterns like "CO - Colorado" or "Colorado (CO)"
    for abbr in _US_STATES:
        if abbr in j.upper().split():
            return True
    for name in _US_STATE_NAMES:
        if name in j_lower:
            return True
    return False


def _inject_did_from_score_consistency(aer: Dict[str, Any]) -> None:
    """
    Safety net for prompt consistency rule: if LLM scored identification_did >= 2
    but did NOT include DID in primary_designs, auto-inject DID.
    """
    if aer.get("did_candidate"):
        return
    # Try top-level first, then dig into nested scores dict
    id_did = _safe_score(aer.get("identification_did"))
    if id_did is None:
        scores = aer.get("scores")
        if isinstance(scores, str):
            try:
                scores = json.loads(scores)
            except (json.JSONDecodeError, TypeError):
                scores = {}
        if isinstance(scores, dict):
            id_did = _safe_score(scores.get("identification_did"))
    if id_did is None or id_did < 2:
        return
    # Score >= 2 but no DID — inject
    aer["did_candidate"] = True
    designs_str = aer.get("primary_designs") or "[]"
    try:
        designs = json.loads(designs_str) if isinstance(designs_str, str) else designs_str
    except (json.JSONDecodeError, TypeError):
        designs = []
    if isinstance(designs, list):
        designs_upper = [str(d).upper() for d in designs if str(d).upper() != "NONE"]
        designs_upper.append("DID")
        aer["primary_designs"] = json.dumps(designs_upper, ensure_ascii=False)
    else:
        aer["primary_designs"] = json.dumps(["DID"], ensure_ascii=False)
    if aer.get("rd_candidate"):
        aer["design_type"] = "RD+DID"
    else:
        aer["design_type"] = "DID"
    aer["did_reasoning"] = (aer.get("did_reasoning") or "") + " [score-consistency injection: id_did >= 2]"


def _inject_did_from_interstate_comparison(aer: Dict[str, Any], policy: Policy) -> None:
    """
    Rule-based fallback: rescue state-level uniform policies as interstate DID candidates.
    Triggers when LLM classified as NONE but the policy has interstate DID potential.
    """
    # Already a DID candidate — nothing to do
    if aer.get("did_candidate"):
        return

    # Check primary_designs: must be ["NONE"] or not contain "DID"
    designs_str = aer.get("primary_designs") or "[]"
    try:
        designs = json.loads(designs_str) if isinstance(designs_str, str) else designs_str
    except (json.JSONDecodeError, TypeError):
        designs = []
    if isinstance(designs, list) and "DID" in [str(d).upper() for d in designs]:
        return

    # Must be state-level jurisdiction
    jurisdiction = policy.jurisdiction or aer.get("jurisdiction") or ""
    if not _is_state_level_jurisdiction(jurisdiction):
        return

    # Must not be proposed
    status = (aer.get("policy_status") or "").strip().lower()
    if status == "proposed":
        return

    # Must have some policy significance and treatment intensity
    ps = 0
    ti = 0
    try:
        ps = int(aer.get("policy_significance") or 0)
    except (TypeError, ValueError):
        pass
    try:
        ti = int(aer.get("treatment_intensity") or 0)
    except (TypeError, ValueError):
        pass
    # Threshold: ps>=3 AND ti>=2
    if ps < 3 or ti < 2:
        return

    # --- All conditions met: inject interstate DID ---
    aer["did_candidate"] = True

    # Update primary_designs
    if isinstance(designs, list):
        designs_upper = [str(d).upper() for d in designs if str(d).upper() != "NONE"]
        designs_upper.append("DID")
        aer["primary_designs"] = json.dumps(designs_upper, ensure_ascii=False)
    else:
        aer["primary_designs"] = json.dumps(["DID"], ensure_ascii=False)

    # Update design_type
    if aer.get("rd_candidate"):
        aer["design_type"] = "RD+DID"
    else:
        aer["design_type"] = "DID"

    # Set DID-specific fields
    aer["did_reasoning"] = (
        "Rule-based: State-level uniform policy with interstate DID potential "
        "(control = non-adopting states)"
    )
    aer["did_recommended_estimator"] = aer.get("did_recommended_estimator") or "Synthetic Control"

    # Set parallel_trends_risk to medium if not already set
    if not aer.get("parallel_trends_risk"):
        aer["parallel_trends_risk"] = "medium"

    # Don't lower policy_endogeneity_risk if LLM set it high
    if not aer.get("policy_endogeneity_risk"):
        aer["policy_endogeneity_risk"] = "medium"

    # Append red_flag
    interstate_flag = (
        "INTERSTATE DID: No within-bill control group; relies on cross-state comparison. "
        "Verify parallel trends with event study. Consider synthetic control (Abadie et al. 2010)."
    )
    existing_rf = aer.get("red_flags") or ""
    if "INTERSTATE DID" not in existing_rf:
        aer["red_flags"] = (existing_rf + " | " + interstate_flag).strip(" |")


def _map_aer_to_legacy(aer: Dict[str, Any], policy: Policy) -> None:
    """
    Map AER-style JSON into legacy flat schema (rd_candidate, did_candidate, treatment_description, etc.)
    and set feasibility from composite_score / verdict / red_flags. Modifies aer in place.
    """
    aer["intuition_pitch"] = (aer.get("intuition_pitch") or "").strip()

    # --- Support multi-design output (primary_designs array) ---
    designs_raw = aer.get("primary_designs")
    if isinstance(designs_raw, list):
        designs_upper = [str(d).strip().upper() for d in designs_raw if d]
    elif isinstance(aer.get("primary_design"), str):
        # Backward compatibility: single primary_design string
        d = aer["primary_design"].strip().upper()
        designs_upper = [d] if d else []
    else:
        designs_upper = []

    aer["rd_candidate"] = "RD" in designs_upper
    aer["did_candidate"] = "DID" in designs_upper
    # design_type: prefer RD+DID combo, else first recognised type
    if aer["rd_candidate"] and aer["did_candidate"]:
        aer["design_type"] = "RD+DID"
    elif aer["rd_candidate"]:
        aer["design_type"] = "RD"
    elif aer["did_candidate"]:
        aer["design_type"] = "DID"
    elif "IV" in designs_upper:
        aer["design_type"] = "IV"
    else:
        aer["design_type"] = ""
    # Store serialised array for CSV
    aer["primary_designs"] = json.dumps(designs_upper, ensure_ascii=False)
    # Backward-compat single field
    if not aer.get("primary_design"):
        aer["primary_design"] = designs_upper[0] if designs_upper else "None"

    # --- Parse all_rd_thresholds array + all_did_evidences array ---
    all_thresholds = aer.get("all_rd_thresholds") or []
    if not isinstance(all_thresholds, list):
        all_thresholds = []

    # Prefer new multi-DID list; fallback to legacy single design_evidence_did.
    # Pydantic's model_validator in schemas.py migrates legacy payloads, so by
    # the time we arrive here all_did_evidences is usually populated. The dict
    # fallback keeps us safe for pre-validation callers or cached pre-migration
    # rows being re-mapped.
    all_did = aer.get("all_did_evidences") or []
    if not isinstance(all_did, list):
        all_did = []
    if not all_did:
        legacy = aer.get("design_evidence_did") or {}
        if isinstance(legacy, dict) and legacy:
            all_did = [legacy]
    ev_did = all_did[0] if all_did else {}

    # Pick the best RD threshold (first one, or highest-quality later if scored)
    ev_rd = all_thresholds[0] if all_thresholds else {}

    # Treatment description: prefer best RD block, then DID block
    aer["treatment_description"] = (
        (ev_rd.get("treatment_group") or ev_did.get("treatment_group") or "").strip()
        or aer.get("treatment_description", "")
    )
    aer["group_description"] = (
        (ev_rd.get("control_group") or ev_did.get("control_group") or "").strip()
    )
    aer["quote_snippet"] = (
        (ev_rd.get("quote") or ev_did.get("quote") or "").strip()
        or aer.get("quote_snippet", "")
    )

    # Serialise evidence blocks for CSV. Both all_did_evidences and
    # design_evidence_did are written so downstream consumers (legacy CSV readers
    # + new chapter-split aggregation) can pick whichever shape they expect.
    aer["all_rd_thresholds"] = json.dumps(all_thresholds, ensure_ascii=False)
    aer["all_did_evidences"] = json.dumps(all_did, ensure_ascii=False)
    aer["design_evidence_did"] = json.dumps(ev_did, ensure_ascii=False) if isinstance(ev_did, dict) else str(ev_did or "")

    # Parse RD-specific fields from best threshold
    if aer.get("rd_candidate") and ev_rd:
        rv_new = (ev_rd.get("running_variable") or "").strip()
        cutoff_new = str(ev_rd.get("cutoff") or "").strip()
        direction_new = (ev_rd.get("direction") or "").strip()
        combined_rv = (rv_new + (" " + cutoff_new if cutoff_new else "")).strip()
        if combined_rv:
            rv_type, cutoff_val, cutoff_unit = _parse_running_variable_or_cutoff(combined_rv)
            if rv_type:
                aer["running_variable_type"] = rv_type
            if cutoff_new:
                aer["cutoff_value"] = cutoff_new
            elif cutoff_val:
                aer["cutoff_value"] = cutoff_val
            if cutoff_unit:
                aer["cutoff_unit"] = cutoff_unit
        # Keyword fallback: classify running variable type if regex failed
        if not aer.get("running_variable_type") and rv_new:
            rv_low = rv_new.lower()
            if any(k in rv_low for k in ("employee", "firm size", "headcount", "workers", "fte")):
                aer["running_variable_type"] = "employees"
                if not aer.get("cutoff_unit"):
                    aer["cutoff_unit"] = "employees"
            elif re.search(r'\bage\b', rv_low):
                aer["running_variable_type"] = "age"
            elif any(k in rv_low for k in ("income", "earnings", "wage")):
                aer["running_variable_type"] = "income"
            elif "percent" in rv_low or "dues" in rv_low:
                aer["running_variable_type"] = "dues_paying_share"
        if direction_new:
            aer["direction"] = direction_new
        # rd_type and threshold_structure from best threshold
        aer["rd_type"] = (ev_rd.get("rd_type") or "").strip().lower() or None
        aer["threshold_structure"] = (ev_rd.get("threshold_structure") or "").strip() or None

    # Stabilization: canonicalize running_variable_type to standard enum
    if aer.get("running_variable_type"):
        aer["running_variable_type"] = _canonicalize_rv(aer["running_variable_type"])

    # Stabilization: clean cutoff_value — keep only numeric/short values
    _cv = str(aer.get("cutoff_value") or "").strip()
    if _cv and (len(_cv) > 20 or not re.search(r'\d', _cv)):
        aer["cutoff_value"] = ""

    first_stage = aer.get("first_stage_analysis") or {}
    bite = (first_stage.get("bite_strength") or "").strip().lower()
    aer["bite_strength"] = first_stage.get("bite_strength")
    aer["mechanism"] = first_stage.get("mechanism")
    aer["enforcement_check"] = first_stage.get("enforcement_check")

    # manipulation_risk: trust LLM output; no post-hoc override
    # (prompt already instructs LLM to assess manipulation independently)

    # --- New Phase 2 fields (RD-specific) ---
    # rd_type and threshold_structure already set from best threshold above
    if not aer.get("rd_type"):
        aer["rd_type"] = None
    if not aer.get("threshold_structure"):
        aer["threshold_structure"] = None
    aer["dynamic_selection_risk"] = (aer.get("dynamic_selection_risk") or "").strip() or None
    aer["measurement_mismatch_risk"] = (aer.get("measurement_mismatch_risk") or "").strip() or None
    aer["power_risk"] = (aer.get("power_risk") or "").strip() or None
    aer["confound_severity"] = (aer.get("confound_severity") or "").strip() or None
    aer["data_access_difficulty"] = (aer.get("data_access_difficulty") or "").strip() or None

    # --- New Phase E fields (DID-specific assumption assessment) ---
    # prompt.20260420.2+ emits these per-DID inside all_did_evidences[].
    # Hydrate bill-level columns from the first DID (worst-case slot) so existing
    # downstream code / CSV columns / UI continue to work. If the LLM still wrote
    # bill-level values (legacy prompt), keep those.
    _did_risk_keys = (
        "parallel_trends_risk", "staggered_adoption_bias", "anticipation_risk",
        "spillover_risk", "policy_endogeneity_risk", "did_recommended_estimator",
        "did_control_quality",
    )
    if isinstance(ev_did, dict):
        for _k in _did_risk_keys:
            if not (aer.get(_k) or "").strip() and ev_did.get(_k):
                aer[_k] = ev_did.get(_k)
    aer["parallel_trends_risk"] = (aer.get("parallel_trends_risk") or "").strip() or None
    aer["staggered_adoption_bias"] = (aer.get("staggered_adoption_bias") or "").strip() or None
    aer["anticipation_risk"] = (aer.get("anticipation_risk") or "").strip() or None
    aer["spillover_risk"] = (aer.get("spillover_risk") or "").strip() or None
    aer["policy_endogeneity_risk"] = (aer.get("policy_endogeneity_risk") or "").strip() or None
    aer["did_recommended_estimator"] = (aer.get("did_recommended_estimator") or "").strip() or None
    aer["pre_period_data_available"] = (aer.get("pre_period_data_available") or "").strip() or None

    # Serialise list fields
    confounding = aer.get("confounding_policies") or []
    aer["confounding_policies"] = " | ".join(str(x) for x in confounding if x) if isinstance(confounding, list) else str(confounding or "")
    manip_channels = aer.get("manipulation_channels") or []
    aer["manipulation_channels"] = " | ".join(str(x) for x in manip_channels if x) if isinstance(manip_channels, list) else str(manip_channels or "")

    # --- P2 fix: derive 7 systematically empty fields ---

    # 1. manipulation_risk: derive from manipulation_channels (RD only)
    if aer.get("rd_candidate"):
        channels_raw = aer.get("manipulation_channels") or ""
        if isinstance(channels_raw, str):
            channels_list = [c.strip() for c in channels_raw.split("|") if c.strip()]
        else:
            channels_list = list(channels_raw) if channels_raw else []
        if not aer.get("manipulation_risk"):
            if len(channels_list) >= 2:
                aer["manipulation_risk"] = "high"
            elif len(channels_list) == 1:
                aer["manipulation_risk"] = "medium"
            else:
                aer["manipulation_risk"] = "low"

    # 2. rd_reasoning: list ALL thresholds found
    if aer.get("rd_candidate") and not (aer.get("rd_reasoning") or "").strip():
        thresholds_raw = aer.get("all_rd_thresholds") or "[]"
        if isinstance(thresholds_raw, str):
            try:
                thresholds_list = json.loads(thresholds_raw)
            except (json.JSONDecodeError, TypeError):
                thresholds_list = []
        else:
            thresholds_list = thresholds_raw if isinstance(thresholds_raw, list) else []
        parts = []
        for t in thresholds_list:
            if not isinstance(t, dict):
                continue
            rv = (t.get("running_variable") or "").strip()
            cutoff = str(t.get("cutoff") or "").strip()
            direction = (t.get("direction") or "").strip()
            if rv and cutoff:
                parts.append(f"RD at {rv} = {cutoff} ({direction})")
        if parts:
            aer["rd_reasoning"] = "; ".join(parts)
        elif aer.get("intuition_pitch"):
            aer["rd_reasoning"] = aer["intuition_pitch"]

    # 3. did_reasoning: concatenate across all DID evidences so a multi-DID
    #    chapter doesn't silently drop its 2nd/3rd design in the UI column.
    if aer.get("did_candidate") and not (aer.get("did_reasoning") or "").strip():
        did_parts = []
        for _i, _ev in enumerate(all_did or [ev_did] if ev_did else []):
            if not isinstance(_ev, dict):
                continue
            tg = (_ev.get("treatment_group") or "").strip()
            cg = (_ev.get("control_group") or "").strip()
            timing = (_ev.get("timing") or "").strip()
            segs = []
            if tg:
                segs.append(f"Treatment: {tg}")
            if cg:
                segs.append(f"Control: {cg}")
            if timing:
                segs.append(f"Timing: {timing}")
            if segs:
                prefix = f"[DID{_i+1}] " if len(all_did) > 1 else ""
                did_parts.append(prefix + "; ".join(segs))
        if did_parts:
            aer["did_reasoning"] = " || ".join(did_parts)

    # 4. design_variant: rd_type + threshold_structure
    if aer.get("rd_candidate") and not (aer.get("design_variant") or "").strip():
        rt = (aer.get("rd_type") or "").strip()
        ts = (aer.get("threshold_structure") or "").strip()
        if rt and ts:
            aer["design_variant"] = f"{rt}_{ts}"
        elif rt:
            aer["design_variant"] = rt

    # 5. staggered: from staggered_adoption_bias
    if aer.get("did_candidate") and aer.get("staggered") is None:
        sab = (aer.get("staggered_adoption_bias") or "").lower()
        aer["staggered"] = sab in ("moderate", "high")

    # 6. group_dimension: from did_control_quality
    if aer.get("did_candidate") and not (aer.get("group_dimension") or "").strip():
        dcq = (aer.get("did_control_quality") or "").lower()
        aer["group_dimension"] = {
            "interstate": "state",
            "border_county_possible": "county",
            "within_state": "firm_or_industry",
        }.get(dcq, "")

    # 7. treatment_timing: pull from first DID evidence (multi-DID aware)
    if aer.get("did_candidate") and not (aer.get("treatment_timing") or "").strip():
        if isinstance(ev_did, dict):
            aer["treatment_timing"] = (ev_did.get("timing") or "").strip()

    # research_angles: serialise to JSON string
    angles = aer.get("research_angles") or []
    aer["research_angles"] = json.dumps(angles, ensure_ascii=False) if isinstance(angles, list) else str(angles or "")

    # NOTE: confound severity → identification penalty is applied below (line ~411)
    # when scores dict is present.  A previous block here was dead code (always
    # overwritten) and has been removed.

    rr = aer.get("research_recommendation") or {}
    outcomes = rr.get("outcome_variables")
    if isinstance(outcomes, list):
        aer["potential_outcomes"] = ", ".join(str(x) for x in outcomes if x)
    elif isinstance(outcomes, str):
        aer["potential_outcomes"] = outcomes
    else:
        aer["potential_outcomes"] = aer.get("potential_outcomes", "")

    aer["verdict"] = (rr.get("verdict") or "").strip()
    red_flags = rr.get("red_flags")
    if isinstance(red_flags, list):
        aer["red_flags"] = " | ".join(str(x) for x in red_flags if x)
    else:
        aer["red_flags"] = str(red_flags or "").strip()

    scores = aer.get("scores") or {}

    # Promote per-DID identification_did (from all_did_evidences[]) to the bill-level
    # scores.identification_did column: take the MAX, so a strong-ID chapter with a
    # weak secondary design keeps its ceiling. Only backfill when the bill-level
    # slot is empty/default, so the LLM's explicit bill-level score still wins.
    if aer.get("did_candidate") and all_did:
        per_did_ids = []
        for _ev in all_did:
            if not isinstance(_ev, dict):
                continue
            _v = _ev.get("identification_did")
            if _v in (None, ""):
                continue
            try:
                per_did_ids.append(int(round(float(_v))))
            except (TypeError, ValueError):
                continue
        if per_did_ids:
            existing = scores.get("identification_did")
            try:
                existing_int = int(existing) if existing not in (None, "") else None
            except (TypeError, ValueError):
                existing_int = None
            # Default value is 1 (from schema); treat 0/1/None as "unset" for backfill
            if existing_int in (None, 0, 1):
                scores["identification_did"] = max(per_did_ids)
                aer["scores"] = scores

    # --- Phase 1.5: Parse identification (single or split RD/DID) ---
    # Check candidate status BEFORE using identification scores
    # (strict rules may have set candidate=False after LLM call)
    id_rd = _safe_score(scores.get("identification_rd")) if aer.get("rd_candidate") else None
    id_did = _safe_score(scores.get("identification_did")) if aer.get("did_candidate") else None
    id_single = _safe_score(scores.get("identification"))

    bill_id_str = aer.get("bill_id") or aer.get("policy_id") or "unknown"

    if id_rd is not None and id_did is not None:
        best_id = max(id_rd, id_did)
        aer["best_design"] = "RD" if id_rd >= id_did else "DID"
        aer["identification_rd"] = id_rd
        aer["identification_did"] = id_did
    elif id_rd is not None:
        best_id = id_rd
        aer["best_design"] = "RD"
    elif id_did is not None:
        best_id = id_did
        aer["best_design"] = "DID"
    elif id_single is not None:
        best_id = id_single
        aer["best_design"] = "RD" if aer.get("rd_candidate") else "DID" if aer.get("did_candidate") else None
        # Warning: RD+DID should have split scores but LLM returned single
        if aer.get("rd_candidate") and aer.get("did_candidate"):
            logger.warning(f"RD+DID but only single identification for {bill_id_str}")
    else:
        best_id = 0
        aer["best_design"] = None

    aer["identification"] = best_id

    # --- Interstate control quality: tag (info-only, no score cap) ---
    _GENERIC_PHRASES = [
        "without similar", "without comparable", "without such",
        "without equivalent", "lacking similar",
    ]
    did_cq = str(aer.get("did_control_quality") or "").lower()
    if did_cq == "interstate" and aer.get("did_candidate"):
        # Check control group description for generic template language.
        # Use the pre-extracted first DID evidence (ev_did) rather than
        # re-parsing the now-serialised design_evidence_did string.
        cg_text = str(ev_did.get("control_group") or "") if isinstance(ev_did, dict) else ""
        cg_text_lower = cg_text.lower()
        is_generic = any(p in cg_text_lower for p in _GENERIC_PHRASES)
        aer["interstate_control_quality"] = "generic" if is_generic else "named"
        # Info-only: no score cap. LLM is sole scoring authority (see design principle).
        # Previously capped identification_did at 3 for generic controls, but this
        # introduced cross-run instability (same bill capped in some runs, not others,
        # depending on exact LLM wording). Removed in prompt.20260306.2.
        if is_generic:
            generic_flag = (
                "GENERIC CONTROL GROUP: Control group described with template language. "
                "Consider naming specific comparison states for stronger identification."
            )
            existing_rf = aer.get("red_flags") or ""
            if "GENERIC CONTROL" not in existing_rf:
                aer["red_flags"] = (existing_rf + " | " + generic_flag).strip(" |")
    elif did_cq == "within_state" and aer.get("did_candidate"):
        aer["interstate_control_quality"] = "within_bill"
    else:
        aer["interstate_control_quality"] = ""

    # --- Python-computed composite score (补充2) ---
    ps_s = _safe_score(scores.get("policy_significance"), 0)
    ti_s = _safe_score(scores.get("treatment_intensity"), 0)
    df_s = _safe_score(scores.get("data_feasibility"), 0)
    aer["composite_score"] = round(W_ID * best_id + W_PS * ps_s + W_TI * ti_s + W_DF * df_s, 1)

    aer["policy_significance"] = ps_s
    aer["treatment_intensity"] = ti_s
    aer["data_feasibility"] = df_s

    # Initial feasibility (will be recalculated at end with all penalties)
    aer["feasibility_score"] = round(min(max(aer["composite_score"], 1.0), 5.0), 1)
    aer["feasibility_label"] = get_feasibility_label(aer["feasibility_score"])
    rec = [aer.get("verdict", "")] if aer.get("verdict") else []
    if aer.get("red_flags"):
        rec.append(aer["red_flags"])
    aer["feasibility_recommendations"] = " | ".join(rec) if rec else "See verdict and red_flags."
    aer["is_feasible"] = False  # will be set properly at the end

    # recommended_datasets is at top level in AER JSON
    if "recommended_datasets" not in aer or aer["recommended_datasets"] is None:
        aer["recommended_datasets"] = ""

    # Literature landscape (LLM-assessed)
    lit_landscape = aer.get("literature_landscape") or {}
    if isinstance(lit_landscape, dict):
        aer["llm_literature_saturation"] = (lit_landscape.get("saturation") or "").strip()
        aer["llm_literature_reasoning"] = (lit_landscape.get("reasoning") or "").strip()
        known = lit_landscape.get("known_similar_studies") or ""
        if isinstance(known, list):
            # Structured format: list of {citation, venue, title} dicts
            aer["llm_known_similar_studies"] = json.dumps(known, ensure_ascii=False)
        else:
            # LLM returned string instead of array — salvage text into reasoning
            extra = str(known).strip()
            if extra:
                existing = aer.get("llm_literature_reasoning") or ""
                aer["llm_literature_reasoning"] = (existing + " | " + extra).strip(" |")
            aer["llm_known_similar_studies"] = "[]"
        aer["unique_contribution"] = (lit_landscape.get("unique_contribution") or "").strip()
    else:
        aer.setdefault("unique_contribution", "")

    # --- Policy classification fields ---
    _VALID_TOPICS = {
        "labor_market", "taxation_fiscal", "social_insurance", "energy_environment",
        "finance_monetary", "product_market", "education", "public_health",
        "criminal_justice", "political_institutions", "technology_data",
        "agriculture_food", "immigration", "transportation_infrastructure",
        "government_administration", "housing_land_use", "veterans_military",
        "appropriations_budget", "trade_commerce", "ceremonial", "other",
    }
    # Fuzzy mapping: LLM free-text → valid topic
    _TOPIC_ALIASES = {
        "transport": "transportation_infrastructure",
        "transportation": "transportation_infrastructure",
        "infrastructure": "transportation_infrastructure",
        "government": "government_administration",
        "government_operations": "government_administration",
        "administrative": "government_administration",
        "regulatory": "government_administration",
        "procurement": "government_administration",
        "housing": "housing_land_use",
        "land_use": "housing_land_use",
        "real_estate": "housing_land_use",
        "zoning": "housing_land_use",
        "veterans": "veterans_military",
        "military": "veterans_military",
        "defense": "veterans_military",
        "appropriations": "appropriations_budget",
        "budget": "appropriations_budget",
        "fiscal": "taxation_fiscal",
        "tax": "taxation_fiscal",
        "health": "public_health",
        "healthcare": "public_health",
        "environment": "energy_environment",
        "energy": "energy_environment",
        "climate": "energy_environment",
        "labor": "labor_market",
        "employment": "labor_market",
        "workers": "labor_market",
        "crime": "criminal_justice",
        "police": "criminal_justice",
        "corrections": "criminal_justice",
        "agriculture": "agriculture_food",
        "food": "agriculture_food",
        "technology": "technology_data",
        "data": "technology_data",
        "cyber": "technology_data",
        "finance": "finance_monetary",
        "banking": "finance_monetary",
        "insurance": "social_insurance",
        "welfare": "social_insurance",
        "social_security": "social_insurance",
        "ceremonial": "ceremonial",
        "commemorative": "ceremonial",
        "memorial": "ceremonial",
        "trade": "trade_commerce",
        "commerce": "trade_commerce",
        "export": "trade_commerce",
        "import": "trade_commerce",
        "tariff": "trade_commerce",
        "tariffs": "trade_commerce",
        "sanctions": "trade_commerce",
        "customs": "trade_commerce",
        "international_trade": "trade_commerce",
        "foreign_trade": "trade_commerce",
    }
    topic = (aer.get("policy_topic") or "").strip().lower().replace(" ", "_")
    if topic not in _VALID_TOPICS:
        topic = _TOPIC_ALIASES.get(topic, "other")
    aer["policy_topic"] = topic
    raw_family = (aer.get("policy_family") or "").strip().lower().replace(" ", "_") or ""
    aer["policy_family"] = normalize_family(raw_family) if raw_family else ""

    # --- Rule-based confound detection: ALWAYS run, merge with LLM output ---
    # Info-only: populates confounding_policies/confound_severity/confounds_json.
    # No score penalty — LLM is the sole authority on confound impact via identification score.
    rule_confounds = detect_confounds(
        aer.get("running_variable_type", ""),
        aer.get("cutoff_value"),
        policy.jurisdiction or "",
    )

    # Parse LLM confound output into list[dict]
    llm_policies_raw = aer.get("confounding_policies") or ""
    if isinstance(llm_policies_raw, list):
        llm_policies_raw = " | ".join(str(x) for x in llm_policies_raw if x)
    llm_policies = [p.strip() for p in llm_policies_raw.split("|") if p.strip()]
    llm_sev = (aer.get("confound_severity") or "none").lower()

    # Bug 24: LLM has severity but no policy name → placeholder
    if llm_sev != "none" and not llm_policies:
        llm_confounds = [{"policy": "Unspecified (LLM-reported)", "severity": llm_sev, "type": "general"}]
    else:
        llm_confounds = [{"policy": p, "severity": llm_sev, "type": "general"} for p in llm_policies]

    # Bug 25: merge with dedup — rule engine priority, LLM abbreviation skip
    merged_confounds = list(rule_confounds)
    seen = {c["policy"] for c in rule_confounds}
    rule_names_lower = " ".join(c["policy"].lower() for c in rule_confounds)
    for c in llm_confounds:
        if c["policy"].lower() in rule_names_lower:
            continue  # LLM abbreviation (e.g. "ACA") already in rule full name
        if c["policy"] not in seen:
            seen.add(c["policy"])
            merged_confounds.append(c)

    aer["confounds"] = merged_confounds  # list[dict] for confounds_json CSV column
    import json as _json
    aer["confounds_json"] = _json.dumps(merged_confounds, ensure_ascii=False) if merged_confounds else ""
    aer["confounding_policies"] = " | ".join(c["policy"] for c in merged_confounds)
    aer["confound_severity"] = max(
        (c["severity"] for c in merged_confounds),
        key=lambda s: _SEV.get(s, 0),
        default="none",
    ) if merged_confounds else "none"

    # DID control quality fallback
    if aer.get("did_candidate") and not aer.get("did_control_quality"):
        aer["did_control_quality"] = "interstate"

    # Score source tracking
    aer["rd_score_source"] = "llm" if aer.get("rd_candidate") else ""
    aer["did_score_source"] = "llm" if aer.get("did_candidate") else ""

    # --- Fuzzy RD red_flags (only if LLM set rd_type) ---
    if aer.get("rd_candidate"):
        if aer.get("rd_type") == "fuzzy":
            fuzzy_flag = (
                "FUZZY RD: Use IV/2SLS with threshold-crossing as instrument for actual "
                "treatment receipt. Report first-stage F-statistic (Staiger-Stock rule: F > 10)."
            )
            existing_rf = aer.get("red_flags") or ""
            if "FUZZY RD" not in existing_rf:
                aer["red_flags"] = (existing_rf + " | " + fuzzy_flag).strip(" |")

    # --- Discrete running variable warning ---
    if aer.get("rd_candidate"):
        rv = (aer.get("running_variable_type") or "").lower()
        _DISCRETE_RV_TYPES = {"employees", "class_size", "firm_size", "headcount", "seats", "units", "age", "enrollment", "population"}
        if rv in _DISCRETE_RV_TYPES:
            discrete_flag = (
                "DISCRETE RUNNING VARIABLE: Limited bandwidth; "
                "report sensitivity to polynomial order and bandwidth choice."
            )
            existing_rf = aer.get("red_flags") or ""
            if "DISCRETE RUNNING VARIABLE" not in existing_rf:
                aer["red_flags"] = (existing_rf + " | " + discrete_flag).strip(" |")

    # NOTE (2026-07-10 audit): _inject_did_from_interstate_comparison IS ACTIVE —
    # it is called for every AER-format record at the _is_aer_format branch below.
    # A prior comment here claimed it was removed/not-called; that was never true.
    # Historical context: prompt.20260306.2 anti-over-rejection made rescues rare
    # (0 rescued in a 500-record sample). Behavior pinned by
    # src/tests/test_interstate_rescue.py. Removal is a pending product decision.

    # --- DID rule-based fallbacks (applied after LLM fields are set) ---
    if aer.get("did_candidate"):
        # P5 fix: staggered adoption bias moderate/high → override TWFE
        sab_check = (aer.get("staggered_adoption_bias") or "").lower()
        if sab_check in ("moderate", "high"):
            if (aer.get("did_recommended_estimator") or "").upper() == "TWFE":
                aer["did_recommended_estimator"] = "Callaway-Sant'Anna"

        # Rule 1: staggered flag → staggered_adoption_bias = "high" if LLM didn't set it
        staggered_val = str(aer.get("staggered") or "").lower()
        if staggered_val in ("true", "yes", "1") and not aer.get("staggered_adoption_bias"):
            aer["staggered_adoption_bias"] = "moderate"
            stagger_flag = (
                "STAGGERED ADOPTION: Run Goodman-Bacon (2021 AER) decomposition to visualize "
                "negative-weight cohorts. Use Callaway & Sant'Anna (2021 JoE) or "
                "Sun & Abraham (2021 JoE) for heterogeneity-robust ATT."
            )
            existing_rf = aer.get("red_flags") or ""
            if "STAGGERED ADOPTION" not in existing_rf:
                aer["red_flags"] = (existing_rf + " | " + stagger_flag).strip(" |")

        # Rule 2: RD+DID dual design → recommend Difference-in-Discontinuities,
        # but only when staggered adoption is NOT the dominant concern.
        # If staggered_adoption_bias is high, staggered-robust estimators take priority.
        if (
            aer.get("rd_candidate")
            and not aer.get("did_recommended_estimator")
            and (aer.get("staggered_adoption_bias") or "").lower() != "high"
        ):
            aer["did_recommended_estimator"] = "Difference-in-Discontinuities"

        # Rule 3: staggered adoption bias high → Callaway-Sant'Anna takes priority
        # (applies to both pure-DID and RD+DID staggered designs)
        if (
            (aer.get("staggered_adoption_bias") or "").lower() == "high"
            and not aer.get("did_recommended_estimator")
        ):
            aer["did_recommended_estimator"] = "Callaway-Sant'Anna"

    # DID identification penalties removed: prompt already instructs LLM to factor
    # parallel_trends_risk, staggered_adoption_bias, and policy_endogeneity_risk
    # into the identification score. Applying them again here was double-penalizing.

    # Final pass: calculate feasibility with all rule-based adjustments
    final_score = calculate_feasibility_score(aer)
    if final_score is not None:
        aer["feasibility_score"] = final_score
        aer["feasibility_label"] = get_feasibility_label(final_score)
        aer["evidence_tier"] = get_evidence_tier(final_score)
        aer["is_feasible"] = get_is_feasible(aer, final_score)
    else:
        aer["feasibility_score"] = 1
        aer["feasibility_label"] = "difficult"
        aer["evidence_tier"] = 3
        aer["is_feasible"] = False

    aer["text_sufficient"] = True
    aer["confidence"] = "high" if (aer.get("feasibility_score", 0) >= 4) else ("medium" if aer.get("feasibility_score", 0) >= 3 else "low")


def extract_rd_did(policy: Policy, *,
                   model_override: str = None,
                   api_key_override: str = None,
                   base_url_override: str = None) -> Dict[str, Any]:
    """
    Run AER-style LLM evaluation for one policy. Returns a dict with policy id, RD/DID flags,
    design_evidence, first_stage_analysis, weighted scores, and feasibility (composite_score).
    On API/parse error, returns minimal dict with policy_id and error key.
    """
    # Pre-filter: skip obvious ceremonial/non-substantive bills before LLM call
    if PREFILTER_ENABLED:
        skip, reason = should_skip_bill(policy.title, policy.raw_text or "")
        if skip:
            return {
                "policy_id": policy.id,
                "title": policy.title or "",
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
                "verdict": f"Pre-filtered: {reason}",
                "skipped_prefilter": reason,
                "policy_topic": "ceremonial",
            }

    if not OPENAI_API_KEY and not _API_KEYS:
        return {"policy_id": policy.id, "error": "OPENAI_API_KEY not set"}
    bill_id = str(policy.id) if policy.id else ""
    raw_text = policy.raw_text or ""

    # Pre-LLM design hints (Bug 22: recertification RD detection before LLM call)
    hints = generate_design_hints(policy.raw_text or "")
    if hints:
        hint_block = "DESIGN HINTS (pre-screened from bill text):\n" + "\n".join(f"- {h}" for h in hints)
        raw_text = hint_block + "\n\n--- BILL TEXT ---\n" + raw_text

    prompt = USER_PROMPT_TEMPLATE.format(
        bill_id=bill_id,
        title=policy.title or "",
        jurisdiction=policy.jurisdiction or "",
        introduced_date=policy.introduced_date or "N/A",
        raw_text=raw_text,
    )

    try:
        # Retry on JSON parse failure (some models occasionally return malformed output)
        out = None
        last_raw = ""
        for _parse_attempt in range(2):
            last_raw = _call_llm(prompt,
                                model_override=model_override,
                                api_key_override=api_key_override,
                                base_url_override=base_url_override)
            out = _parse_json_from_response(last_raw)
            if out:
                break
            logger.warning("JSON parse failed for %s (attempt %d/2), retrying...", policy.id, _parse_attempt + 1)
            time.sleep(0.3)
        if not out:
            return {
                "policy_id": policy.id,
                "error": "LLM did not return valid JSON after 2 attempts",
                "error_type": "JSONParseError",
                "raw_preview": last_raw[:500],
                "title": policy.title,
                "jurisdiction": policy.jurisdiction,
                "document_url": policy.document_url,
                "fulltext_chars": len(policy.raw_text or ""),
            }

        # Pydantic semantic validation + one validation-retry with error feedback.
        # Goal: eliminate NoneType-subscriptable crashes downstream by catching
        # shape errors (e.g. primary_designs as string, scores out of range) here.
        validated, verr = _validate_extraction(out)
        if verr is not None:
            logger.warning("Pydantic validation failed for %s: %s", policy.id, verr.splitlines()[0])
            retry_raw = _call_llm(
                _build_retry_prompt(prompt, out, verr),
                model_override=model_override,
                api_key_override=api_key_override,
                base_url_override=base_url_override,
            )
            retry_parsed = _parse_json_from_response(retry_raw)
            if retry_parsed:
                validated2, verr2 = _validate_extraction(retry_parsed)
                if verr2 is None:
                    out = validated2
                    verr = None
                else:
                    verr = verr2
            if verr is not None:
                return {
                    "policy_id": policy.id,
                    "error": "LLM output failed schema validation after retry",
                    "error_type": "ValidationError",
                    "validation_errors": verr,
                    "raw_preview": last_raw[:500],
                    "title": policy.title,
                    "jurisdiction": policy.jurisdiction,
                    "document_url": policy.document_url,
                    "fulltext_chars": len(policy.raw_text or ""),
                }
        else:
            out = validated

        out["policy_id"] = policy.id
        out["title"] = policy.title
        out["jurisdiction"] = policy.jurisdiction
        out["document_url"] = policy.document_url
        out["introduced_date"] = str(policy.introduced_date) if policy.introduced_date else None

        # policy_status priority: metadata (from data source) > inferred from text > LLM output
        meta_status = (policy.metadata or {}).get("policy_status", "")
        if meta_status:
            out["policy_status"] = meta_status
        else:
            inferred = _infer_policy_status_from_raw_text(policy.raw_text or "")
            if inferred:
                out["policy_status"] = inferred
            else:
                out["policy_status"] = out.get("policy_status", "unknown")

        if _is_aer_format(out):
            _map_aer_to_legacy(out, policy)
            _inject_did_from_score_consistency(out)
            _inject_rd_from_percent_recertification(policy, out)
            _inject_did_from_interstate_comparison(out, policy)

            # --- Literature LLM (Phase 4-5): only for RD/DID candidates ---
            design_type = out.get("design_type") or ""
            is_candidate = bool(design_type and design_type.upper() != "NONE")
            if is_candidate and not SKIP_LITERATURE:
                try:
                    lit_result = _call_literature_llm(out)
                    if lit_result:
                        _merge_literature_results(out, lit_result)
                except Exception as e:
                    logger.warning(f"Literature LLM failed for {out.get('policy_id')}: {e}")
            else:
                # Non-candidate rows: skip literature, clear any stale values
                out.setdefault("llm_literature_saturation", "")
                out.setdefault("llm_known_similar_studies", "[]")
                out.setdefault("research_angles", "[]")
                out.setdefault("unique_contribution", "")

            # Recalculate feasibility with literature bonus (may have changed)
            final_score = calculate_feasibility_score(out)
            if final_score is not None:
                out["feasibility_score"] = final_score
                out["feasibility_label"] = get_feasibility_label(final_score)
                out["evidence_tier"] = get_evidence_tier(final_score)
                out["is_feasible"] = get_is_feasible(out, final_score)
        else:
            # Fallback: legacy prompt response
            _apply_strict_rd_did_rules(out)
            _inject_rd_from_percent_recertification(policy, out)
            feasibility_score = calculate_feasibility_score(out)
            out["feasibility_score"] = feasibility_score
            out["feasibility_label"] = get_feasibility_label(feasibility_score)
            out["feasibility_recommendations"] = get_feasibility_recommendations(out, feasibility_score)

        return out
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error("extract_rd_did failed for %s: %s\n%s", policy.id, e, tb)
        return {
            "policy_id": policy.id,
            "error": str(e),
            "error_type": type(e).__name__,
            "error_traceback": tb,
            "title": policy.title,
            "jurisdiction": policy.jurisdiction,
            "document_url": policy.document_url,
            "fulltext_chars": len(policy.raw_text or ""),
        }
