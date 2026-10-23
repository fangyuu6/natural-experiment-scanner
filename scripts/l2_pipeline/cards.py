"""Dependency-free validation against our documented JSON Schema subset."""
from __future__ import annotations
import copy, json, re
from pathlib import Path
from typing import Any, Mapping
from .contracts import DesignCard

class CardError(ValueError): pass

def _check(value: Any, schema: Mapping[str, Any], path: str, errors: list[str]) -> None:
    if "anyOf" in schema:
        alternatives = []
        for alternative in schema["anyOf"]:
            candidate_errors: list[str] = []
            _check(value, alternative, path, candidate_errors)
            alternatives.append(candidate_errors)
        if not any(not candidate_errors for candidate_errors in alternatives):
            errors.append(path + ": no valid declared purpose route")
    kinds = {"object": dict, "array": list, "string": str, "boolean": bool}
    if "type" in schema and not isinstance(value, kinds[schema["type"]]):
        errors.append(path + ": wrong type"); return
    if "enum" in schema and value not in schema["enum"]: errors.append(path + ": invalid choice")
    if "const" in schema and value != schema["const"]: errors.append(path + ": invalid version")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0): errors.append(path + ": empty string")
        if "pattern" in schema and re.search(schema["pattern"], value) is None: errors.append(path + ": invalid format")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0): errors.append(path + ": too few items")
        for i, item in enumerate(value): _check(item, schema.get("items", {}), f"{path}[{i}]", errors)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value: errors.append(path + ": missing " + key)
        if schema.get("additionalProperties") is False:
            errors.extend(path + ": unknown " + k for k in value.keys() - properties.keys())
        for key in value.keys() & properties.keys(): _check(value[key], properties[key], path + "." + key, errors)

def validate_card(value: Any) -> list[str]:
    schema = json.loads(Path(__file__).with_name("design_card.schema.json").read_text())
    errors: list[str] = []; _check(value, schema, "$", errors)
    if errors: return errors
    nested_scale = value['outcome'].get('outcome_scale')
    if nested_scale is not None and nested_scale not in ('rate', 'count'):
        errors.append('Outcome scale must be rate or count')
    if nested_scale is not None and value.get('outcome_scale', nested_scale) != nested_scale:
        errors.append('Top-level and outcome outcome_scale disagree')
    from .outcome_transform import s0_issues
    errors.extend("outcome_transform: " + i for i in s0_issues(value))
    automation = value.get("automation")
    if isinstance(automation, dict) and "rules" in automation:
        from .rules import pinned
        try: pinned(value)
        except ValueError as exc: errors.append("automation.rules: " + str(exc))
    first = value.get("first_order", {})
    if first.get("purpose_direction") is not None and type(first["purpose_direction"]) is not int:
        errors.append("purpose_direction must be integer -1, 0, 1 or null")
    if first.get("purpose_route") == "statute" and not any(
            isinstance(ref, dict) and isinstance(ref.get("source_ref"), str) and ref["source_ref"].strip() and
            isinstance(ref.get("locator"), str) and ref["locator"].strip() and
            ("supports" not in ref or "target_outcome" in ref["supports"])
            for ref in first.get("statute_refs", [])):
        errors.append("Statute purpose route requires a located statute supporting target_outcome")
    source_ids=[row["source_id"] for row in value["sources"]]
    if len(set(source_ids))!=len(source_ids): errors.append("Duplicate source identity")
    filters = value["outcome"].get("population_filters", value["population"]["filters"])
    if filters != value["population"]["filters"]: errors.append("Outcome population filters differ from card")
    seen = set()
    for sensitivity in value["sensitivities"]:
        name = sensitivity.get("name")
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_-]+", name) is None or name == "primary" or name in seen:
            errors.append("Invalid/duplicate sensitivity name")
        else: seen.add(name)
        if not isinstance(sensitivity.get("changes", {}), dict): errors.append("Sensitivity changes must be an object")
    return errors

def load_card(path: Path) -> DesignCard:
    from .storage import public_input
    path = public_input(path)
    if path.suffix.lower() in {".yaml", ".yml"}:
        try: import yaml
        except ImportError as exc: raise CardError("YAML requires PyYAML; JSON cards need no extra dependency") from exc
        value = yaml.safe_load(path.read_text())
    else: value = json.loads(path.read_text())
    errors = validate_card(value)
    if errors: raise CardError("; ".join(errors))
    from .connectors.rates import prospective_card
    return DesignCard.from_mapping(prospective_card(value), path)

def variant_card(card: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
    """Every sensitivity uses the same primary construction path."""
    result = copy.deepcopy(dict(card))
    def merge(target: dict, patch: Mapping):
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict): merge(target[key], value)
            else: target[key] = copy.deepcopy(value)
    if set(changes) - {"outcome", "outcome_scale", "population", "treatment", "analysis", "transition_year_drop"}: raise CardError("Sensitivity changes outside registered scientific fields")
    merge(result, changes)
    errors = validate_card(result)
    if errors: raise CardError("; ".join(errors))
    from .connectors.rates import legacy_card, prospective_card
    if not legacy_card(card):
        result = prospective_card(result)
    return result
