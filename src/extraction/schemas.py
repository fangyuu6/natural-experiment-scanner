"""Pydantic schemas for AER-style LLM extraction output.

Mirrors the JSON template in prompts_aer.py:USER_PROMPT_TEMPLATE. Used by
llm_analyzer for (a) tool_use schema generation and (b) semantic validation
of raw JSON responses.

Fields use Optional+None rather than required, because the LLM may omit
sections when a design doesn't apply (e.g. design_evidence_did for pure-RD
bills). Enums accept bare strings to avoid retries on trivial casing issues.
"""
from __future__ import annotations

from typing import List, Optional, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict


# --- Shared enums (kept as Literal for lightweight tool_use schemas) ---

RiskLevel = Literal["low", "medium", "high"]
ConfoundSev = Literal["none", "manageable", "severe", "fatal"]
StaggeredBias = Literal["none", "moderate", "high"]
PrePeriod = Literal["yes", "no", "unclear"]
PowerRisk = Literal["adequate", "marginal", "insufficient"]
MeasMismatch = Literal["none", "moderate", "severe"]
DataAccess = Literal["public", "restricted_application", "rdc_only", "state_agency_only"]
DIDControlQuality = Literal["border_county_possible", "interstate", "within_state", ""]
DIDEstimator = Literal[
    "TWFE", "Callaway-Sant'Anna", "Sun-Abraham",
    "Synthetic Control", "Difference-in-Discontinuities",
]
RDType = Literal["sharp", "fuzzy"]
ThresholdStructure = Literal["notch", "kink", "standard"]
Direction = Literal["above_cutoff", "below_cutoff"]
DesignLabel = Literal["RD", "DID", "IV", "Bunching", "EventStudy", "None"]

PolicyTopic = Literal[
    "labor_market", "taxation_fiscal", "social_insurance", "energy_environment",
    "finance_monetary", "product_market", "education", "public_health",
    "criminal_justice", "political_institutions", "technology_data",
    "agriculture_food", "immigration", "transportation_infrastructure",
    "government_administration", "housing_land_use", "veterans_military",
    "appropriations_budget", "trade_commerce", "other",
]


class RDThreshold(BaseModel):
    model_config = ConfigDict(extra="allow")
    running_variable: str = ""
    cutoff: Union[str, float, int] = ""
    direction: Optional[str] = None
    treatment_group: str = ""
    control_group: str = ""
    rd_type: Optional[str] = None
    threshold_structure: Optional[str] = None
    quote: str = ""

    @field_validator("cutoff", mode="before")
    @classmethod
    def _stringify_cutoff(cls, v):
        if v is None:
            return ""
        return str(v).strip()


class DIDEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    treatment_group: str = ""
    control_group: str = ""
    timing: str = ""
    quote: str = ""
    # Per-DID risk assessments. A chapter may host multiple DID designs with
    # different identification profiles (e.g. staggered-timing rollout vs firm-size
    # split). Keep these per-design rather than collapsing to one bill-level value.
    parallel_trends_risk: Optional[str] = None
    staggered_adoption_bias: Optional[str] = None
    anticipation_risk: Optional[str] = None
    spillover_risk: Optional[str] = None
    policy_endogeneity_risk: Optional[str] = None
    did_recommended_estimator: Optional[str] = None
    did_control_quality: Optional[str] = None
    identification_did: Optional[int] = None

    @field_validator(
        "parallel_trends_risk", "staggered_adoption_bias", "anticipation_risk",
        "spillover_risk", "policy_endogeneity_risk", "did_control_quality",
        mode="before",
    )
    @classmethod
    def _lower_strip(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s.lower() if s else None

    @field_validator("identification_did", mode="before")
    @classmethod
    def _int_or_none(cls, v):
        if v is None or v == "":
            return None
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return None


class Scores(BaseModel):
    model_config = ConfigDict(extra="allow")
    policy_significance: int = Field(ge=1, le=5)
    identification: int = Field(ge=1, le=5)
    identification_rd: int = Field(ge=1, le=5, default=1)
    identification_did: int = Field(ge=1, le=5, default=1)
    treatment_intensity: int = Field(ge=1, le=5)
    data_feasibility: int = Field(ge=1, le=5)

    @field_validator("*", mode="before")
    @classmethod
    def _clamp_int(cls, v):
        if isinstance(v, float):
            return int(round(v))
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
        return v


class FirstStageAnalysis(BaseModel):
    model_config = ConfigDict(extra="allow")
    enforcement_check: str = ""
    bite_strength: Optional[str] = None
    mechanism: Optional[str] = None


class ResearchRecommendation(BaseModel):
    model_config = ConfigDict(extra="allow")
    outcome_variables: List[str] = Field(default_factory=list)
    verdict: str = ""
    red_flags: List[str] = Field(default_factory=list)

    @field_validator("outcome_variables", "red_flags", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [s.strip() for s in v.split("|") if s.strip()]
        return v


class AERExtraction(BaseModel):
    """Top-level extraction schema matching USER_PROMPT_TEMPLATE in prompts_aer.py."""
    model_config = ConfigDict(extra="allow")

    bill_id: str = ""
    primary_designs: List[str] = Field(default_factory=list)
    intuition_pitch: str = ""

    all_rd_thresholds: List[RDThreshold] = Field(default_factory=list)
    all_did_evidences: List[DIDEvidence] = Field(default_factory=list)
    # DEPRECATED (kept for backward-compat with pre-20260420.2 records).
    # Old single-DID field; new runs write `all_did_evidences` instead.
    # A model_validator migrates legacy payloads into the list at parse time.
    design_evidence_did: Optional[DIDEvidence] = None

    confounding_policies: List[str] = Field(default_factory=list)
    confound_severity: Optional[str] = None

    parallel_trends_risk: Optional[str] = None
    staggered_adoption_bias: Optional[str] = None
    anticipation_risk: Optional[str] = None
    spillover_risk: Optional[str] = None
    policy_endogeneity_risk: Optional[str] = None
    did_recommended_estimator: Optional[str] = None
    pre_period_data_available: Optional[str] = None

    manipulation_channels: List[str] = Field(default_factory=list)
    dynamic_selection_risk: Optional[str] = None

    measurement_mismatch_risk: Optional[str] = None
    power_risk: Optional[str] = None
    data_access_difficulty: Optional[str] = None

    first_stage_analysis: Optional[FirstStageAnalysis] = None
    did_control_quality: Optional[str] = None

    scores: Optional[Scores] = None

    recommended_datasets: str = ""
    research_recommendation: Optional[ResearchRecommendation] = None

    policy_topic: str = "other"
    policy_family: str = ""

    @field_validator(
        "primary_designs", "confounding_policies", "manipulation_channels",
        mode="before",
    )
    @classmethod
    def _coerce_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            return [s]
        return v

    @field_validator("all_rd_thresholds", "all_did_evidences", mode="before")
    @classmethod
    def _coerce_thresholds(cls, v):
        import json as _json
        if v is None:
            return []
        if isinstance(v, str):
            try:
                v = _json.loads(v)
            except Exception:
                return []
        if isinstance(v, dict):
            return [v]
        return v

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_did(cls, data):
        """Pre-20260420.2 records emit `design_evidence_did` (single dict) plus
        bill-level DID-risk fields. New schema expects `all_did_evidences` (list)
        with per-DID risk fields inside. If the new field is empty but the legacy
        single-dict is present, migrate them into a one-element list so the rest
        of the pipeline sees a consistent shape."""
        if not isinstance(data, dict):
            return data
        new = data.get("all_did_evidences")
        # Skip migration when new field already has content
        if isinstance(new, list) and len(new) > 0:
            return data
        if isinstance(new, dict) and new:
            return data
        if isinstance(new, str) and new.strip() not in ("", "[]", "null"):
            return data
        legacy = data.get("design_evidence_did")
        if not legacy:
            return data
        import json as _json
        if isinstance(legacy, str):
            try:
                legacy = _json.loads(legacy)
            except Exception:
                legacy = None
        if not isinstance(legacy, dict):
            return data
        migrated = dict(legacy)
        for key in (
            "parallel_trends_risk", "staggered_adoption_bias", "anticipation_risk",
            "spillover_risk", "policy_endogeneity_risk", "did_recommended_estimator",
            "did_control_quality",
        ):
            if migrated.get(key) in (None, "") and data.get(key) not in (None, ""):
                migrated[key] = data.get(key)
        # Promote bill-level identification_did score to per-DID
        scores = data.get("scores")
        if isinstance(scores, dict) and migrated.get("identification_did") in (None, ""):
            if scores.get("identification_did") not in (None, ""):
                migrated["identification_did"] = scores.get("identification_did")
        data["all_did_evidences"] = [migrated]
        return data

    @field_validator("design_evidence_did", "first_stage_analysis", "research_recommendation", mode="before")
    @classmethod
    def _coerce_obj(cls, v):
        import json as _json
        if v is None or v == "":
            return None
        if isinstance(v, str):
            try:
                parsed = _json.loads(v)
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                return None
        return v

    @field_validator(
        "confound_severity", "parallel_trends_risk", "staggered_adoption_bias",
        "anticipation_risk", "spillover_risk", "policy_endogeneity_risk",
        "measurement_mismatch_risk", "power_risk", "dynamic_selection_risk",
        "did_control_quality", "policy_topic",
        mode="before",
    )
    @classmethod
    def _lower_strip(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s.lower() if s else None


# --- Tool-use schema helper ---

def aer_extraction_tool_schema() -> dict:
    """Return an OpenAI-compatible tool definition for emit_design(...).

    Compatible with OpenRouter's tool_use endpoints. Pydantic generates
    JSON schema with $defs, which OpenAI/OpenRouter accept inline.
    """
    schema = AERExtraction.model_json_schema()
    return {
        "type": "function",
        "function": {
            "name": "emit_design",
            "description": (
                "Emit the natural-experiment design evaluation for the bill. "
                "Call this once with the full analysis. All fields must be in English."
            ),
            "parameters": schema,
        },
    }
