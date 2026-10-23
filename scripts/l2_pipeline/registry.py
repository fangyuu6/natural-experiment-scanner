"""Append-only validated local registry snapshots; no mutation of the live registry."""
from __future__ import annotations
import copy
from typing import Any, Mapping, Sequence
from scripts.pipeline.registry.validate import validate_registry
from scripts.pipeline.registry.export_static import export_static, inline_safe_json


def update_snapshot(previous: dict, *, outcome_specs: Sequence[dict], runs: Sequence[dict],
                    design_updates: Mapping[str,dict], generated_at: str,
                    artifact_root=None) -> tuple[dict,dict]:
    errors=validate_registry(previous,artifact_root)
    if errors: raise ValueError("Previous registry invalid: "+"; ".join(errors))
    current=copy.deepcopy(previous); current["generated_at"]=generated_at
    for collection, additions, identity in (("outcome_specs",outcome_specs,"outcome_spec_id"),("runs",runs,"run_id")):
        seen={r[identity] for r in current[collection]}
        for row in additions:
            if row[identity] in seen: raise ValueError("Registry identity cannot be overwritten: "+row[identity])
            current[collection].append(copy.deepcopy(row));seen.add(row[identity])
    by_id={r["design_id"]:r for r in current["designs"]}
    for design_id, updates in design_updates.items():
        design=by_id[design_id]
        if "design_id" in updates and updates["design_id"]!=design_id: raise ValueError("Design identity changed")
        design.update(copy.deepcopy(updates))
    errors=validate_registry(current,artifact_root,previous)
    if errors: raise ValueError("Registry transition invalid: "+"; ".join(errors))
    return current,export_static(current,artifact_root,previous)
