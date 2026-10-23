"""Outcome-blind C42 exclusions and bulk intake completeness checks."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

from .storage import ensure_once, sha, public_input

RULE = 'docs/plans/l2-pipeline/HUB_RULING_C42_UNKNOWN_STATES.md'


class Park(ValueError):
    def __init__(self, reason, metrics=None):
        super().__init__(reason)
        self.metrics = metrics or {}


def exclusion_scope(cfg, comparison, values):
    """Keep disagreements as evidence; never turn an unknown into a control."""
    from .intake.official_history import conflict_scope
    cfg = deepcopy(cfg)
    reasons = deepcopy(cfg.get('excluded_conflict_state_reasons', {}))
    for gap in comparison['gaps']:
        reasons.setdefault(gap['unit'], [])
        if gap['kind'] not in reasons[gap['unit']]:
            reasons[gap['unit']].append(gap['kind'])
    from . import prewindow
    for unit in prewindow.bound_units(comparison.get('accepted', [])):
        # T192: an already-treated-before-window state leaves both arms; its bound year is evidence only.
        reasons.setdefault(unit, [])
        if prewindow.REASON not in reasons[unit]:
            reasons[unit].append(prewindow.REASON)
    certified = {r['unit'] for r in comparison.get('accepted',[]) if r.get('certified_interruption')}
    for role, value in zip(('A', 'B'), values):
        for row in value['records']:
            if row.get('manual_evidence_conflict'):
                reasons.setdefault(row['unit'], []).append(role+':'+row['manual_evidence_conflict'])
            for field in ('observed_interruptions', 'history_uncertain'):
                if row.get(field) and not (field=='observed_interruptions' and row['unit'] in certified):
                    reasons.setdefault(row['unit'], []).append(role + ':' + field)
    cfg['certified_spell_end_periods'] = {r['unit']:r['censor_after_period'] for r in comparison.get('accepted',[]) if r.get('censor_after_period') is not None}
    cfg['excluded_conflict_state_reasons'] = {u: sorted(set(r)) for u, r in sorted(reasons.items())}
    cfg['excluded_conflict_states'] = sorted(set(cfg.get('excluded_conflict_states', [])) | set(reasons))
    cfg['excluded_units'] = sorted(set(cfg.get('excluded_units', [])) | set(cfg['excluded_conflict_states']))
    conflict_scope(cfg, [g['unit'] for g in comparison['gaps']])
    return cfg


def register_exclusions(ctx, cfg, evidence):
    """Write-once scientific card overlay; bind originals, evidence and ruling."""
    payload = deepcopy(dict(ctx.card.payload))
    payload['treatment'] = cfg
    ruling = ctx.repo / RULE
    bindings = list(evidence) + [dict(path=str(ctx.card.path), sha256=sha(ctx.card.path)),
                               dict(path=str(ruling), sha256=sha(ruling))]
    path = ensure_once(ctx.design_dir / 'C42_PREPARATION.json', dict(
        design_id=ctx.card.design_id, version=ctx.card.version,
        original_card_sha256=sha(ctx.card.path), prepared_payload=payload,
        evidence=bindings, rule=RULE, outcome_values_read=0, execution_authorized=False))
    return path


def effective_context(ctx, resolutions=True):
    """The card with the C42 exclusions. ``resolutions=False`` is the card as S0 saw it: the states the fresh audit dated leave the
    conflict exclusions only through AUDIT_RESOLUTIONS.json, which S1 writes after S0 has kept its CARD.json."""
    path = ctx.design_dir / 'C42_PREPARATION.json'
    if not path.exists():
        return ctx
    value = json.loads(path.read_text())
    if (value['design_id'] != ctx.card.design_id or value['version'] != ctx.card.version
            or value['original_card_sha256'] != sha(ctx.card.path)
            or value.get('outcome_values_read') != 0 or value.get('execution_authorized') is not False):
        raise ValueError('C42 scientific card binding changed')
    for item in value['evidence']:
        if sha(public_input(Path(item['path']))) != item['sha256']:
            raise ValueError('C42 original evidence/ruling changed: ' + item['path'])
    payload = deepcopy(dict(ctx.card.payload))
    # This overlay changes exclusions only. All other declarations stay bound
    # to the intake or a separately verified prospective preparation.
    cfg = value['prepared_payload']['treatment']
    allowed = {'excluded_units', 'excluded_conflict_states', 'excluded_conflict_state_reasons', 'universe', 'certified_spell_end_periods'}
    original = json.loads(public_input(ctx.card.path).read_text())['treatment']
    if {k:v for k,v in cfg.items() if k not in allowed} != {k:v for k,v in original.items() if k not in allowed}:
        raise ValueError('C42 overlay changed scientific fields beyond exclusions')
    # NAMED_PANEL Q1 (2026-10-16): a later card version may add structural exclusions that its own
    # automation.named_panel_q1 record lists; the retained overlay never has to contain them.
    authorized = set(((payload.get('automation') or {}).get('named_panel_q1') or {}).get('excluded_units', {}))
    # REDESIGN R2: a state the fresh audit dated leaves the conflict exclusions; only the units of a hash-valid
    # AUDIT_RESOLUTIONS.json may do so (the loader recomputes them from the audited answer and raises on any edit).
    from .intake.official_history import load_audit_resolutions
    audited = load_audit_resolutions(ctx.design_dir) if resolutions else None
    resolved = {r['unit'] for r in audited['resolved']} if audited else set()
    if resolved - set(cfg['excluded_conflict_states']) or resolved & set(original.get('excluded_units', [])):
        raise ValueError('Audit resolutions name a state the overlay did not exclude as a conflict')
    authorized |= resolved
    if (set(original.get('excluded_units', [])) - set(cfg['excluded_units'])) - authorized:
        raise ValueError('C42 overlay removed existing exclusions')
    treatment = deepcopy(payload['treatment'])
    treatment['excluded_units'] = sorted((set(treatment.get('excluded_units', [])) | set(cfg['excluded_units'])) - resolved)
    for key in ('excluded_conflict_states','excluded_conflict_state_reasons'):
        treatment[key] = deepcopy(cfg[key])
    if resolved:
        treatment['excluded_conflict_states'] = [u for u in treatment['excluded_conflict_states'] if u not in resolved]
        treatment['excluded_conflict_state_reasons'] = {u: r for u, r in treatment['excluded_conflict_state_reasons'].items() if u not in resolved}
    if cfg.get('certified_spell_end_periods'):
        treatment['certified_spell_end_periods'] = deepcopy(cfg['certified_spell_end_periods'])
    treatment.setdefault('universe',deepcopy(cfg['universe']))
    payload['treatment'] = treatment
    from .intake.official_history import conflict_scope
    conflict_scope(payload['treatment'], payload['treatment']['excluded_conflict_states'])
    return replace(ctx, card=replace(ctx.card, payload=payload))


def completeness(value):
    from .cards import validate_card
    import re
    errors = [re.sub(r'^(.*): missing (\w+)$', r'\1.\2: missing', e) for e in validate_card(value)]
    if not isinstance(value, dict):
        return errors
    required = ('clock', 'controls', 'min_treated', 'min_controls', 'min_cohorts',
                'min_pre_periods', 'min_post_periods', 'estimand', 'weights',
                'inference', 'diagnostics', 'stop_rules')
    analysis = value.get('analysis', {})
    if isinstance(analysis, dict):
        errors += ['$.analysis.' + key + ': missing' for key in required if key not in analysis]
    treatment = value.get('treatment', {})
    if isinstance(treatment, dict) and not treatment.get('predicate'):
        errors.append('$.treatment.predicate: missing')
    outcome = value.get('outcome', {})
    if isinstance(outcome, dict) and not outcome.get('period_years') and not outcome.get('years'):
        errors.append('$.outcome.period_years/years: missing declared window')
    if 'first_order' not in value:
        errors.append('$.first_order: missing prospective purpose declaration')
    return errors


def post_support_gate(support, analysis):
    """Enforce the unchanged two-post rule using registered metadata only."""
    primary = next((r for r in support['variants'] if r['variant']=='primary'), None)
    if primary is None:
        raise Park('V7 primary post support evidence missing')
    cells = primary.get('cells', primary.get('cell_inventory', []))
    posts = {}
    for cell in cells:
        if cell.get('post', True):
            cohort = cell.get('cohort', cell.get('g'))
            period = cell.get('period', cell.get('t'))
            if cohort is None or period is None:
                raise Park('V7 primary post support metadata incomplete')
            posts.setdefault(cohort,set()).add(period)
    minimum = max(2, analysis.get('min_post_periods',2))
    counts = {str(g):len(t) for g,t in sorted(posts.items())}
    metrics = dict(post_periods_by_cohort=counts, min_post_periods=minimum,
                   retained_treated=len(primary.get('retained_treated',[])),
                   retained_controls=len(primary.get('comparison_controls',primary.get('never_controls',[]))),
                   comparison_group=primary.get('comparison_group','never_treated'),outcome_values_read=0)
    if not counts or any(n < minimum for n in counts.values()):
        raise Park('V7 supported post periods below minimum',metrics)
    return metrics
