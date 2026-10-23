"""R3.1: RI feasibility floor 0.80 for seven named designs (HUB_RULING_20261022_GATE_RELAX section 1).

The ruling of 2026-10-22 named five designs. HUB_RULING_20261023_PARK_REOPEN section 4, Q1 (named panel, 5-0 after round 2)
added exactly two more, the EERS pair `b1011c_eers_electric` and `b1016q4_eers_electric_electric_power_co2_emissions`
(RI199 179/199, RI999 898/999, every infeasible draw coded "RI lead inventory changed under relabeling"). Floor stays exactly 0.80,
one shot, same tag `rescued_R3.1`, same wording. The list is FROZEN at these seven: no further extension without a new panel ruling.

Only a card that carries `analysis.ri_feasibility_floor` changes behaviour. Absent field means today's
0.90 floor and byte-identical outputs. All comparisons use exact integer arithmetic.
"""
from __future__ import annotations
import hashlib, math, re
from fractions import Fraction
from pathlib import Path

RULE = 'R3.1'
FLOOR = 0.8
DEFAULT_FLOOR = 0.9
R31_DESIGNS = frozenset({
    'b1010_open_container', 'b1010_truth_in_sentencing', 'b1010b_gdl_holding_night',
    'b1010b_opioid_prescriber_cme', 'b1010b_pain_clinic_law',
    # HUB_RULING_20261023_PARK_REOPEN section 4, Q1 (panel 5-0); list frozen after this addition:
    'b1011c_eers_electric', 'b1016q4_eers_electric_electric_power_co2_emissions',
})
SUPERSEDED = 'SUPERSEDED_GATE_RELAX_R31'
IMPLEMENTATION = 'GATE_RELAX_R31'
RULING_PATH = 'docs/plans/l2-pipeline/HUB_RULING_20261022_GATE_RELAX.md'
STATEMENT = 'added after the RI stop, before any forecast or outcome calculation'
PARK_REASON = 'STOPPED_PRE_RUN: RI199/RI999 feasibility below 90%'
PARK_ACTIONS = ('ri', None)   # a gate-0 park carried forward from an RI attempt has action None (b1010b_gdl_holding_night)
LEAD_TEXT = 'RI lead inventory changed under relabeling'
FAMILY_TEXT = 'RI supported family changes under relabeling'
REASON_CODES = ('lead_inventory', 'support_geometry')
CONDITIONAL_STATEMENT = ('The randomization p-value uses only the share f of the 999 fixed relabelings that could be built '
    '(f between 0.80 and 0.90, below the usual 0.90). It is conditional on those assignments, not an unconditional '
    'exact p-value; the interval is the headline.')
_BLOCK_KEYS = frozenset({'rule', 'floor', 'ruling_sha256', 'statement'})
_HEX64 = re.compile(r'[0-9a-f]{64}')


def required_draws(n, floor):
    """Smallest integer count of feasible draws out of n that reaches `floor` exactly."""
    if type(n) is not int or n < 0:
        raise ValueError('Draw count must be a non-negative integer')
    return math.ceil(Fraction(str(floor)) * n)


def checked_floor(floor):
    if type(floor) not in (float, int) or floor != FLOOR:
        raise ValueError('R3.1 floor must be exactly 0.8')
    return FLOOR


def _validated_block(block):
    if not isinstance(block, dict) or set(block) != _BLOCK_KEYS:
        raise ValueError('R3.1 block must have exactly the keys rule, floor, ruling_sha256, statement')
    if block['rule'] != RULE:
        raise ValueError('Unknown RI floor rule')
    checked_floor(block['floor'])
    if not isinstance(block['ruling_sha256'], str) or not _HEX64.fullmatch(block['ruling_sha256']):
        raise ValueError('R3.1 ruling sha256 malformed')
    if block['statement'] != STATEMENT:
        raise ValueError('R3.1 statement differs from the ruling')
    return FLOOR


def ri_floor(design_id, analysis):
    """None for the default 0.90 path; 0.80 for a valid R3.1 card block; ValueError otherwise."""
    if not isinstance(analysis, dict):
        raise ValueError('Card analysis must be an object')
    if 'ri_feasibility_floor' not in analysis:
        return None
    if design_id not in R31_DESIGNS:
        raise ValueError('R3.1 names a design outside the ruling list')
    return _validated_block(analysis['ri_feasibility_floor'])


def floor_from_declaration(declaration):
    """Floor carried by a numerical declaration (key `ri_feasibility_floor_rule`), or None for the default."""
    block = declaration.get('ri_feasibility_floor_rule') if isinstance(declaration, dict) else None
    if block is None:
        return None
    return ri_floor(declaration.get('ri_feasibility_floor_design') or declaration.get('key'), {'ri_feasibility_floor': block})


def reason_code(reason):
    """Allowed reason code of an infeasible draw, or None when the draw is uncoded."""
    if not isinstance(reason, str):
        return None
    if reason == LEAD_TEXT or reason.startswith(FAMILY_TEXT):
        return 'lead_inventory'
    if any(reason.startswith(s) for s in _engine_failures()):
        return 'support_geometry'
    return None


def _engine_failures():
    from .engine.ri import FAILURES
    return FAILURES


def export_block(feasibility_999, feasibility_199):
    """Export fields for an R3.1 design; the default path adds nothing."""
    return dict(ri_floor_version=RULE, ri199_feasible_fraction=feasibility_199['feasible_fraction'],
                ri999_feasible_fraction=feasibility_999['feasible_fraction'],
                conditional_reference_statement=CONDITIONAL_STATEMENT)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_ruling(pilot, old, new_payload, decision, card_path):
    """Requeue hook for R3.1: the new card may differ from the old card only in `version` and `analysis.ri_feasibility_floor`."""
    from .autopilot import read
    design = old['design_id']
    if design not in R31_DESIGNS or new_payload.get('design_id', design) != design:
        raise ValueError('R3.1 ruling names a design outside the ruling list')
    if (decision.get('decision') != 'resume' or decision.get('execution_authorized') is not False
            or decision.get('superseded_status') != SUPERSEDED or decision.get('implementation_rule') != IMPLEMENTATION):
        raise ValueError('R3.1 ruling malformed')
    authority = decision.get('authority')
    ruling_file = Path(pilot.repo) / RULING_PATH
    if (not isinstance(authority, dict) or Path(str(authority.get('path', ''))).resolve() != ruling_file.resolve()
            or authority.get('sha256') != _sha(ruling_file)):
        raise ValueError('R3.1 authority is not the 2026-10-22 gate-relax ruling')
    old_payload = read(old['card'])
    if not isinstance(old_payload, dict):
        raise ValueError('Prior card unreadable')
    block = (new_payload.get('analysis') or {}).get('ri_feasibility_floor')
    if ri_floor(design, new_payload.get('analysis') or {}) != FLOOR:
        raise ValueError('R3.1 card block missing')
    if block['ruling_sha256'] != _sha(ruling_file):
        raise ValueError('R3.1 card names a different ruling')
    if 'ri_feasibility_floor' in (old_payload.get('analysis') or {}):
        raise ValueError('Prior card already carries an R3.1 block')
    parked = read(pilot.case_dir(old) / 'PARKED.json', {}) or {}
    if parked.get('action') not in PARK_ACTIONS or parked.get('reason') != PARK_REASON:
        raise ValueError('R3.1 requires the old attempt parked at ri (or a carried RI park) with the registered reason')
    keys = set(old_payload) | set(new_payload)
    changed = {k for k in keys if old_payload.get(k) != new_payload.get(k)} - {'version'}
    if changed - {'analysis'}:
        raise ValueError('R3.1 card changes more than analysis')
    old_analysis = dict(old_payload.get('analysis') or {})
    new_analysis = dict(new_payload.get('analysis') or {})
    new_analysis.pop('ri_feasibility_floor', None)
    if new_analysis != old_analysis:
        raise ValueError('R3.1 card changes analysis beyond ri_feasibility_floor')
    return dict(design_id=design, floor=FLOOR, card=str(card_path))
