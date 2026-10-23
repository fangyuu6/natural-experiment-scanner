"""R-S3C1: a cohort that loses one member after a certified statutory interruption (HUB_RULING_20261022_S3_COHORT).

Only a card that carries `analysis.cohort_censor_rule` changes behaviour; an absent field means today's S3 (a (cohort, year)
cell is supported only when every roster member has its row) and byte-identical outputs. Exact integer arithmetic throughout.
The rule exists for exactly five closed designs; a card naming it for any other design id, or any other floor, is refused.
"""
from __future__ import annotations
import hashlib, json, re
from fractions import Fraction
from pathlib import Path

RULE = 'R-S3C1'
MIN_SHARE = Fraction(3, 4)          # remaining members / original cohort roster
MIN_MEMBERS = 4                     # remaining members in the cell
MAX_CENSORED_PER_COHORT = 1
ALLOWED_IDS = frozenset({
    'b1011c_eers_electric',
    'b1016q4_eers_electric_electric_power_co2_emissions',
    'b1011cfl_ui_part_time_worker_eligibility',
    'b1016q4_ui_part_time_worker_eligibility_ui_continued_weeks_claimed',
    'b1016q4_ui_part_time_worker_eligibility_ui_weeks_compensated',
})
INTERRUPTION_KINDS = frozenset({'repeal', 'termination', 'freeze'})   # certified statutory interruptions only
SUPERSEDED = 'SUPERSEDED_S3_COHORT_RS3C1'
IMPLEMENTATION = 'S3_COHORT_RS3C1'
RULING_PATH = 'docs/plans/l2-pipeline/HUB_RULING_20261022_S3_COHORT.md'
STATEMENT = 'added after the S3 stop, before any outcome calculation'
TAG = 'cohort_member_censored'
MIXING_STATEMENT = 'Aggregates mix cohorts of changing composition: later cells of an affected cohort use the members still in the design.'
FIXED = 'fixed_composition'          # run (ii): block `mode`; cells after the cutoff of an affected cohort are dropped, not relaxed
SENSITIVITY_FIXED = 'rs3c1_fixed_composition'
SENSITIVITY_DROPPED = 'rs3c1_censored_member_dropped'
CELLS_FILE = 'COHORT_CELLS.json'
_BLOCK_KEYS = frozenset({'version', 'min_share', 'min_members', 'ruling_sha256', 'statement'})
_HEX64 = re.compile(r'[0-9a-f]{64}')


def validate_block(block):
    """Returns the mode ('primary' or FIXED); ValueError for any other shape, floor, wording or hash."""
    if not isinstance(block, dict) or not (_BLOCK_KEYS <= set(block) <= _BLOCK_KEYS | {'mode'}):
        raise ValueError('R-S3C1 block must have exactly the keys version, min_share, min_members, ruling_sha256, statement (and optionally mode)')
    if block['version'] != RULE:
        raise ValueError('Unknown cohort censor rule')
    share = block['min_share']
    if type(share) not in (float, int) or Fraction(str(share)) != MIN_SHARE:
        raise ValueError('R-S3C1 share floor must be exactly 0.75')
    if type(block['min_members']) is not int or block['min_members'] != MIN_MEMBERS:
        raise ValueError('R-S3C1 member floor must be exactly 4')
    if not isinstance(block['ruling_sha256'], str) or not _HEX64.fullmatch(block['ruling_sha256']):
        raise ValueError('R-S3C1 ruling sha256 malformed')
    if block['statement'] != STATEMENT:
        raise ValueError('R-S3C1 statement differs from the ruling')
    mode = block.get('mode', 'primary')
    if mode not in ('primary', FIXED):
        raise ValueError('R-S3C1 mode unknown')
    return mode


def block_from_analysis(analysis, design_id=None):
    """None for the default path; the validated block otherwise. With design_id, also refuses ids outside the five."""
    if not isinstance(analysis, dict):
        raise ValueError('Card analysis must be an object')
    if 'cohort_censor_rule' not in analysis:
        return None
    block = analysis['cohort_censor_rule']
    validate_block(block)
    if design_id is not None and design_id not in ALLOWED_IDS:
        raise ValueError('R-S3C1 names a design outside the ruling list')
    return block


def check_card(card):
    """Whole-card check used by S3 and the numerical declaration: whitelist plus every sensitivity block."""
    block = block_from_analysis(card.get('analysis') or {}, card.get('design_id'))
    for s in card.get('sensitivities') or []:
        if 'cohort_censor_rule' in ((s.get('changes') or {}).get('analysis') or {}) and block is None:
            raise ValueError('R-S3C1 sensitivity without a primary block')
    return block


def qualifies(record):
    """A unit qualifies only through a certified statutory interruption fixed in the S1 history (never a data gap)."""
    if not isinstance(record, dict) or record.get('status') != 'treated':
        return False
    end = record.get('censor_after_period')
    if type(end) is not int or record.get('certified_interruption') is not True:
        return False
    return any(isinstance(i, dict) and (i.get('kind') or i.get('type')) in INTERRUPTION_KINDS
               and isinstance(i.get('source_ids'), list) and i['source_ids'] for i in record.get('observed_interruptions') or [])


def share(n_remaining, n_roster):
    return Fraction(n_remaining, n_roster)


def cell_decision(block, treated, complete, units, t):
    """'relaxed' when the cell is supported under the rule, 'skip' (run ii only), else 'unsupported' (old S3 geometry: dropped)."""
    mode = validate_block(block)
    missing = sorted(set(treated) - set(complete))
    if not missing:
        return 'unsupported'      # not called for a full cell
    if mode == FIXED:
        late = all(units[u].get('censor_after_period') is not None and t > units[u]['censor_after_period'] for u in treated)
        return 'skip' if not complete and late else 'unsupported'
    if len(missing) > MAX_CENSORED_PER_COHORT or any(not qualifies(units[u]) for u in missing):
        return 'unsupported'
    if any(t <= units[u]['censor_after_period'] for u in missing):
        return 'unsupported'      # the member is missing before its own cutoff: a data gap, never covered
    if share(len(complete), len(treated)) < MIN_SHARE or len(complete) < MIN_MEMBERS:
        return 'unsupported'
    return 'relaxed'


def cell_record(cohort, period, treated, complete):
    return dict(cohort=cohort, period=period, n_treated=len(complete), n_roster=len(treated),
                share=str(share(len(complete), len(treated))), missing=sorted(set(treated) - set(complete)))


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def cells_sha256(payload):
    return hashlib.sha256(encoded(payload)).hexdigest()


def cells_payload(design_id, card_sha256, support_summary):
    """The cell list (with n_treated/N per cell), bound to the card bytes; None when the rule did not act."""
    info = support_summary.get('cohort_censor')
    if not info or not info.get('cells'):
        return None
    return dict(schema='l2_cohort_cells_rs3c1_v1', design_id=design_id, rule=RULE, card_sha256=card_sha256,
                tag=TAG, mixing_statement=MIXING_STATEMENT, cells=info['cells'], cohorts=info['cohorts'])


def verify_bound(design_dir, card_path, support):
    """Before any estimate: COHORT_CELLS.json is the exact export of the primary SUPPORT record and names this card's bytes."""
    design_dir = Path(design_dir)
    path = design_dir / CELLS_FILE
    primary = next(r for r in support['variants'] if r['variant'] == 'primary')
    expected = cells_payload(json.loads(Path(card_path).read_text())['design_id'], hashlib.sha256(Path(card_path).read_bytes()).hexdigest(), primary)
    if expected is None or not path.is_file() or json.loads(path.read_text()) != expected:
        raise ValueError('R-S3C1 cohort cell list differs from the supported primary geometry or is not bound to the card')
    if support.get('cohort_cells_sha256') != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError('R-S3C1 cohort cell list hash differs from SUPPORT.json')
    return True


def _sign(x):
    return (x > 0) - (x < 0)


def composition_sensitive(primary, fixed, dropped):
    """Ruling point 5. Each run is dict(att=float, ci95=[lo, hi] or None). Verdict composition_sensitive / stable / not_evaluable.

    Sign of (i) differing from (ii) or (iii), or the pooled ATT of (i) outside the interval of (iii), makes it composition_sensitive;
    when the estimate is near zero in all runs (every run's interval contains 0) the interval test decides, not the sign.
    A run without an att is not evaluable; (iii) without an interval cannot run the interval test (reported as ci_unavailable)."""
    runs = dict(primary=primary, fixed=fixed, dropped=dropped)
    if any(not isinstance(r, dict) or not isinstance(r.get('att'), (int, float)) for r in runs.values()):
        return dict(verdict='not_evaluable', reasons=['a run has no estimate'])
    reasons = []
    ci = dropped.get('ci95')
    have_ci = isinstance(ci, (list, tuple)) and len(ci) == 2 and all(isinstance(v, (int, float)) for v in ci)

    def straddles(r):
        iv = r.get('ci95')
        return isinstance(iv, (list, tuple)) and len(iv) == 2 and iv[0] <= 0 <= iv[1]
    near_zero = all(straddles(r) for r in runs.values())
    if not near_zero:
        for name in ('fixed', 'dropped'):
            if _sign(primary['att']) != _sign(runs[name]['att']):
                reasons.append('sign differs from ' + name)
    if have_ci and not (ci[0] <= primary['att'] <= ci[1]):
        reasons.append('primary ATT outside the interval of the dropped-member run')
    return dict(verdict='composition_sensitive' if reasons else 'stable', reasons=reasons,
                near_zero_in_all_runs=near_zero, ci_unavailable=not have_ci)


def run_estimate(registered_results, name):
    r = (registered_results or {}).get(name)
    overall = (((r or {}).get('result') or {}).get('overall')) if isinstance(r, dict) else None
    if not isinstance(overall, dict):
        return None
    return dict(att=overall.get('att'), ci95=overall.get('ci95'))


def export_block(design_dir, card, result):
    """S7 export fields for an R-S3C1 design: tag, rule, the bound cell list, share per cell, three runs side by side, verdict."""
    block = block_from_analysis(card.get('analysis') or {}, card.get('design_id'))
    if block is None:
        return None
    design_dir = Path(design_dir)
    cells = json.loads((design_dir / CELLS_FILE).read_text())
    registered = (result or {}).get('registered_results') or {}
    runs = dict(primary=run_estimate(registered, 'primary'), fixed_composition=run_estimate(registered, SENSITIVITY_FIXED),
                censored_member_dropped=run_estimate(registered, SENSITIVITY_DROPPED))
    verdict = composition_sensitive(runs['primary'] or {}, runs['fixed_composition'] or {}, runs['censored_member_dropped'] or {})
    shares = [Fraction(c['share']) for c in cells['cells']]
    return dict(rule=RULE, tag=TAG, statement=STATEMENT, ruling_sha256=block['ruling_sha256'],
                cells_sha256=hashlib.sha256((design_dir / CELLS_FILE).read_bytes()).hexdigest(),
                cohorts=cells['cohorts'], min_share_in_cells=str(min(shares)) if shares else None,
                cells=cells['cells'], mixing_statement=MIXING_STATEMENT, runs=runs, composition=verdict,
                headline_withheld=verdict['verdict'] == 'composition_sensitive')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def censored_and_roster(records, excluded=()):
    """From the certified S1 records of the retained universe: {unit: cutoff} of qualifying members and {unit: its cohort roster}."""
    treated = [r for r in records if r.get('status') == 'treated' and r['unit'] not in set(excluded)]
    censored = {r['unit']: r['censor_after_period'] for r in treated if qualifies(r)}
    roster = {u: sorted(r['unit'] for r in treated if r['start_period'] == next(x for x in treated if x['unit'] == u)['start_period'])
              for u in censored}
    return censored, roster


def build_card_sensitivities(censored, roster):
    """The two report-only comparison runs. censored: {unit: cutoff}; roster: {unit: [cohort members incl. the unit]}."""
    members = sorted({m for u in censored for m in roster[u]})
    cutoffs = {m: min(censored[u] for u in censored if m in roster[u]) for m in members}
    fixed = dict(name=SENSITIVITY_FIXED, report_only=True, gates_primary=False, changes=dict(
        treatment=dict(cohort_truncate_after=cutoffs), analysis=dict(cohort_censor_rule=dict(mode=FIXED))))
    dropped = dict(name=SENSITIVITY_DROPPED, report_only=True, gates_primary=False, changes=dict(
        treatment=dict(excluded_units=sorted(censored))))
    return [fixed, dropped]


def validate_ruling(pilot, old, new_payload, decision, card_path):
    """Requeue hook: the new card may differ from the old only in `version`, the block, and the two appended sensitivities."""
    from .autopilot import read
    design = old['design_id']
    if design not in ALLOWED_IDS or new_payload.get('design_id', design) != design:
        raise ValueError('R-S3C1 ruling names a design outside the ruling list')
    if (decision.get('decision') != 'resume' or decision.get('execution_authorized') is not False
            or decision.get('superseded_status') != SUPERSEDED or decision.get('implementation_rule') != IMPLEMENTATION):
        raise ValueError('R-S3C1 ruling malformed')
    authority = decision.get('authority')
    ruling_file = Path(pilot.repo) / RULING_PATH
    if (not isinstance(authority, dict) or Path(str(authority.get('path', ''))).resolve() != ruling_file.resolve()
            or authority.get('sha256') != _sha(ruling_file)):
        raise ValueError('R-S3C1 authority is not the 2026-10-22 S3 cohort ruling')
    old_payload = read(old['card'])
    if not isinstance(old_payload, dict):
        raise ValueError('Prior card unreadable')
    block = check_card(new_payload)
    if block is None or validate_block(block) != 'primary':
        raise ValueError('R-S3C1 card block missing')
    if block['ruling_sha256'] != _sha(ruling_file):
        raise ValueError('R-S3C1 card names a different ruling')
    if 'cohort_censor_rule' in (old_payload.get('analysis') or {}):
        raise ValueError('Prior card already carries an R-S3C1 block')
    stop = pilot.stop_record(old) or read(pilot.case_dir(old) / 'PARKED.json', {}) or {}
    if stop.get('action') not in ('S3', None) or 'Registered support insufficient' not in str(stop.get('reason', '')):
        raise ValueError('R-S3C1 requires the old attempt stopped at S3 with the registered-support reason')
    old_sens = list(old_payload.get('sensitivities') or [])
    new_sens = list(new_payload.get('sensitivities') or [])
    if new_sens[:len(old_sens)] != old_sens or [s.get('name') for s in new_sens[len(old_sens):]] != [SENSITIVITY_FIXED, SENSITIVITY_DROPPED]:
        raise ValueError('R-S3C1 card changes sensitivities beyond the two appended comparison runs')
    keys = set(old_payload) | set(new_payload)
    changed = {k for k in keys if old_payload.get(k) != new_payload.get(k)} - {'version', 'sensitivities'}
    if changed - {'analysis', 'treatment'}:
        raise ValueError('R-S3C1 card changes more than analysis and the comparison runs')
    new_analysis = dict(new_payload.get('analysis') or {})
    new_analysis.pop('cohort_censor_rule', None)
    if new_analysis != dict(old_payload.get('analysis') or {}):
        raise ValueError('R-S3C1 card changes analysis beyond cohort_censor_rule')
    if new_payload.get('treatment') != old_payload.get('treatment'):
        raise ValueError('R-S3C1 card changes treatment')
    return dict(design_id=design, card=str(card_path))
