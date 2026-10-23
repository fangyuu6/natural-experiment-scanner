"""Central source catalog shared by collection, fetch, and HTML display."""

from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Optional, Tuple
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceEntry:
    source_key: str
    display_name: str
    source_family: str
    domains: Tuple[str, ...] = ()
    ingestion_mode: str = "html"
    priority: int = 50
    dedupe_scope: str = "source"
    html_label: str = ""
    enabled_by_default: bool = True
    batch_module: str = ""
    batch_function: str = ""
    batch_kwargs: Mapping[str, object] = field(default_factory=dict)
    requires_api_key: str = ""


SOURCE_FAMILY_LABELS: Dict[str, str] = {
    "federal_legislation": "Federal Legislation",
    "federal_regulations": "Federal Regulations",
    "federal_agencies": "Federal Agencies",
    "state_legislature": "State Legislation",
    "state_local_executive": "State/Local Executive",
    "legal_reference": "Legal Reference",
    "archive": "Archive",
    "international": "International",
    "other": "Other",
}


def _entry(
    source_key: str,
    display_name: str,
    source_family: str,
    *,
    domains: Iterable[str] = (),
    ingestion_mode: str = "html",
    priority: int = 50,
    dedupe_scope: str = "source",
    html_label: str = "",
    enabled_by_default: bool = True,
    batch_module: str = "",
    batch_function: str = "",
    batch_kwargs: Optional[Mapping[str, object]] = None,
    requires_api_key: str = "",
) -> SourceEntry:
    return SourceEntry(
        source_key=source_key,
        display_name=display_name,
        source_family=source_family,
        domains=tuple(domains),
        ingestion_mode=ingestion_mode,
        priority=priority,
        dedupe_scope=dedupe_scope,
        html_label=html_label or display_name,
        enabled_by_default=enabled_by_default,
        batch_module=batch_module,
        batch_function=batch_function,
        batch_kwargs=dict(batch_kwargs or {}),
        requires_api_key=requires_api_key,
    )


SOURCE_CATALOG: Dict[str, SourceEntry] = {
    "congress": _entry(
        "congress",
        "Congress.gov",
        "federal_legislation",
        domains=("congress.gov", "api.congress.gov"),
        ingestion_mode="api",
        priority=10,
        dedupe_scope="bill_identity",
        html_label="Congress",
    ),
    "govinfo": _entry(
        "govinfo",
        "GovInfo",
        "federal_legislation",
        domains=("govinfo.gov", "api.govinfo.gov"),
        ingestion_mode="api",
        priority=20,
        dedupe_scope="bill_text",
        batch_module="ingestion.govinfo",
        batch_function="fetch_govinfo_batch",
    ),
    "regulations": _entry(
        "regulations",
        "Regulations.gov",
        "federal_regulations",
        domains=("regulations.gov",),
        ingestion_mode="api",
        priority=30,
        dedupe_scope="regulation_identity",
        html_label="Regulations.gov",
    ),
    "federal_register": _entry(
        "federal_register",
        "Federal Register",
        "federal_regulations",
        domains=("federalregister.gov",),
        ingestion_mode="api",
        priority=30,
        dedupe_scope="regulation_identity",
        html_label="Federal Register",
        batch_module="ingestion.federal_register",
        batch_function="fetch_federal_register_batch",
    ),
    "ecfr": _entry(
        "ecfr",
        "eCFR",
        "federal_regulations",
        domains=("ecfr.gov",),
        ingestion_mode="api",
        priority=40,
        dedupe_scope="regulation_text",
        html_label="eCFR",
        batch_module="ingestion.ecfr",
        batch_function="fetch_ecfr_batch",
    ),
    "federal_agencies": _entry(
        "federal_agencies",
        "Federal Agencies",
        "federal_agencies",
        domains=("cms.gov", "medicaid.gov", "fda.gov", "cdc.gov", "irs.gov"),
        ingestion_mode="html",
        priority=50,
        dedupe_scope="document",
        html_label="Federal Agencies",
        batch_module="ingestion.federal_agencies",
        batch_function="fetch_federal_agencies_batch",
    ),
    "open_states": _entry(
        "open_states",
        "Open States",
        "state_legislature",
        domains=("openstates.org",),
        ingestion_mode="api",
        priority=10,
        dedupe_scope="bill_identity",
        html_label="Open States",
    ),
    "open_states_bulk": _entry(
        "open_states_bulk",
        "Open States Bulk",
        "state_legislature",
        domains=(),
        ingestion_mode="api",
        priority=10,
        dedupe_scope="bill_identity",
        html_label="Open States Bulk",
    ),
    "legiscan": _entry(
        "legiscan",
        "State Legislation",
        "state_legislature",
        domains=("legiscan.com",),
        ingestion_mode="api",
        priority=10,
        dedupe_scope="bill_identity",
        html_label="State Legislation",
        batch_module="ingestion.legiscan",
        batch_function="fetch_legiscan_batch",
        requires_api_key="LEGISCAN_API_KEY",
    ),
    "state_executive_orders": _entry(
        "state_executive_orders",
        "State Executive Orders",
        "state_local_executive",
        domains=("flgov.com",),
        ingestion_mode="html",
        priority=20,
        dedupe_scope="document",
        html_label="State Executive Orders",
        batch_module="ingestion.state_executive_orders",
        batch_function="fetch_state_eos_batch",
    ),
    "legal_reference": _entry(
        "legal_reference",
        "Legal Reference",
        "legal_reference",
        domains=("uscode.house.gov", "law.cornell.edu", "codelibrary.amlegal.com"),
        ingestion_mode="html",
        priority=90,
        dedupe_scope="reference_only",
        html_label="Legal Reference",
        batch_module="ingestion.cornell_law",
        batch_function="fetch_cornell_law_batch",
    ),
    "govtrack": _entry(
        "govtrack",
        "GovTrack",
        "federal_legislation",
        domains=("govtrack.us",),
        ingestion_mode="api",
        priority=35,
        dedupe_scope="bill_identity",
        html_label="GovTrack",
        batch_module="ingestion.govtrack",
        batch_function="fetch_govtrack_bills_batch",
    ),
    "propublica": _entry(
        "propublica",
        "ProPublica Congress",
        "federal_legislation",
        domains=("propublica.org",),
        ingestion_mode="api",
        priority=35,
        dedupe_scope="bill_identity",
        html_label="ProPublica",
        batch_module="ingestion.propublica",
        batch_function="fetch_propublica_batch",
        requires_api_key="PROPUBLICA_API_KEY",
    ),
}


_SOURCE_ALIASES = {
    "congress.gov": "congress",
    "congress": "congress",
    "govinfo.gov": "govinfo",
    "govinfo": "govinfo",
    "regulations.gov": "regulations",
    "regulations": "regulations",
    "federal_register": "federal_register",
    "federal register": "federal_register",
    "executive_orders": "federal_register",
    "ecfr": "ecfr",
    "eCFR": "ecfr",
    "openstates": "open_states",
    "open_states": "open_states",
    "open_states_bulk": "open_states_bulk",
    "state_executive_orders": "state_executive_orders",
    "govtrack": "govtrack",
    "propublica": "propublica",
    "cornell_law": "legal_reference",
    "legal_reference": "legal_reference",
    "regulations.gov": "regulations",
    "federal_agencies": "federal_agencies",
}

_POLICY_ID_PREFIXES = (
    ("govinfo-", "govinfo"),
    ("ecfr-", "ecfr"),
    ("congress-", "congress"),
    ("regulations-", "regulations"),
    ("fedreg-", "federal_register"),
    ("eo-", "federal_register"),
    ("fed-eo-", "federal_register"),
    ("openstates-", "open_states"),
    ("state-eo-", "state_executive_orders"),
    ("USC-", "legal_reference"),
    ("regulations-", "regulations"),
    ("fda-", "federal_agencies"),
    ("cms-", "federal_agencies"),
    ("cdc-", "federal_agencies"),
    ("irs-", "federal_agencies"),
)


def get_source_entry(source_key: str) -> Optional[SourceEntry]:
    return SOURCE_CATALOG.get(source_key)


def source_family_label(source_family: str) -> str:
    return SOURCE_FAMILY_LABELS.get(source_family, SOURCE_FAMILY_LABELS["other"])


def match_source_by_url(url: str) -> Optional[SourceEntry]:
    if not url:
        return None
    try:
        host = urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return None
    for entry in SOURCE_CATALOG.values():
        for domain in entry.domains:
            domain = domain.lower().replace("www.", "")
            if domain and (host == domain or host.endswith(f".{domain}") or domain in host):
                return entry
    return None


def _strip_trial_prefix(policy_id: str) -> str:
    """Strip trial-{source}- prefix so trial run IDs are recognized by normal matching."""
    if not policy_id.startswith("trial-"):
        return policy_id
    rest = policy_id[len("trial-"):]
    for tag in ("both-", "openstates-", "legiscan-", "merged_csv-"):
        if rest.startswith(tag):
            return rest[len(tag):]
    return rest


def infer_source_key(
    raw_source: str = "",
    policy_id: str = "",
    document_url: str = "",
) -> str:
    source_norm = (raw_source or "").strip().lower()
    if source_norm in _SOURCE_ALIASES:
        return _SOURCE_ALIASES[source_norm]
    for alias, source_key in _SOURCE_ALIASES.items():
        if alias and alias in source_norm:
            return source_key
    # Strip trial- prefix for ID-based matching
    _pid = _strip_trial_prefix(policy_id)
    for prefix, source_key in _POLICY_ID_PREFIXES:
        if _pid.startswith(prefix):
            return source_key
    url_match = match_source_by_url(document_url)
    if url_match:
        return url_match.source_key
    if _pid.isdigit() or ("/" in _pid and not _pid.startswith("US")):
        return "legiscan"
    # trial-both/openstates/legiscan IDs with state code are state legislature
    if policy_id.startswith("trial-"):
        return "legiscan"
    return "other"


def html_source_label(
    raw_source: str = "",
    policy_id: str = "",
    document_url: str = "",
) -> str:
    source_key = infer_source_key(raw_source=raw_source, policy_id=policy_id, document_url=document_url)
    entry = get_source_entry(source_key)
    return entry.html_label if entry else SOURCE_FAMILY_LABELS["other"]


def html_source_family_label(
    raw_source: str = "",
    policy_id: str = "",
    document_url: str = "",
) -> str:
    source_key = infer_source_key(raw_source=raw_source, policy_id=policy_id, document_url=document_url)
    entry = get_source_entry(source_key)
    if not entry:
        return SOURCE_FAMILY_LABELS["other"]
    return source_family_label(entry.source_family)


def batch_source_map() -> Dict[str, Tuple[str, str, Dict[str, object]]]:
    mapping: Dict[str, Tuple[str, str, Dict[str, object]]] = {}
    for source_key, entry in SOURCE_CATALOG.items():
        if entry.batch_module and entry.batch_function:
            mapping[source_key] = (
                entry.batch_module,
                entry.batch_function,
                dict(entry.batch_kwargs),
            )
    return mapping


def default_batch_sources_without_keys() -> Tuple[str, ...]:
    keys = []
    for source_key, entry in SOURCE_CATALOG.items():
        if entry.batch_module and entry.batch_function and not entry.requires_api_key:
            keys.append(source_key)
    return tuple(sorted(keys))
