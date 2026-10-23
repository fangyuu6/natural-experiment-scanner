"""Apply supplied C9 card rulings without changing sources or stage readiness."""
from __future__ import annotations

import copy
from collections.abc import Mapping

from .first_order import _C9_TRAFFIC_TARGETS

RULE = "HUB_RULINGS_C9"
DILUTION_NOTE = (
    "C9 / V6.1: Total traffic deaths measures the accepted traffic-fatality "
    "construct over a broader population; including deaths outside alcohol-involved "
    "crashes is expected to dilute the policy effect."
)


def apply_c9_card_ruling(card: Mapping) -> dict:
    """Return a fresh card; existing citations, histories and S1 rules survive."""
    revised = copy.deepcopy(dict(card))
    design = revised.get("design_id")
    first = revised.get("first_order", {})
    if design in _C9_TRAFFIC_TARGETS:
        if (first.get("target_outcome") != _C9_TRAFFIC_TARGETS[design] or
                first.get("primary_outcome") != "traffic_deaths_count" or
                revised.get("outcome", {}).get("measure_id") != "traffic_deaths_count"):
            raise ValueError("C9 traffic ruling does not match the reviewed target/primary")
        first.update(relation="closest_proxy", proxy_justification=DILUTION_NOTE)
        notes = revised.setdefault("notes", [])
        if not isinstance(notes, list):
            raise ValueError("Card notes must be a list")
        if DILUTION_NOTE not in notes:
            notes.append(DILUTION_NOTE)
    elif design == "run3_driver_license_compact":
        refs = first.get("statute_refs")
        if not isinstance(refs, list) or not refs or first.get("literature_refs") != []:
            raise ValueError("C9 compact ruling requires existing statutes and empty literature")
        first["purpose_route"] = "statute"
    else:
        raise ValueError("No C9 card ruling for this design")
    revised.setdefault("automation", {})["c9_ruling"] = {
        "rule": RULE, "execution_authorized": False, "s1_still_required": True,
        "outcome_values_read": 0, "outcome_association_calls": 0,
    }
    return revised
