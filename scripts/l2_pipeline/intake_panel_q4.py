"""INTAKE_PANEL Q4 (HUB_RULING_20261016_INTAKE_PANEL.md): reopen 12 of the 14 history parks once, under the fetch-and-hash route.

All 14 parked at history before the Q3 fetch route existed. ``arrestee_dna`` and ``power_sector_co2`` (0 unclassified units)
stay terminal. Each of the other 12 goes through the existing card-bound requeue as a new attempt: the card is the previous
card with only its version bumped (written beside the old card, so relative source paths stay valid), decision ``resume``,
status SUPERSEDED_INTAKE_PANEL_Q4. History restarts from scratch (nothing of the old attempt is retained or deleted); the
new history packets are claimed under the Q3 route. Nothing is dispatched, fitted or deleted here; idempotent.
"""
from pathlib import Path

from .storage import ensure_once, sha

RULING = 'docs/plans/l2-pipeline/HUB_RULING_20261016_INTAKE_PANEL.md'
STATUS = 'SUPERSEDED_INTAKE_PANEL_Q4'
TASK = 'WINDOW_20261017'
STAY_TERMINAL = ('b1011c_power_sector_co2_cap', 'b1012cr_arrestee_dna_collection')
CLASS = ('b1010b_keg_registration', 'b1010b_motorcycle_helmet_enactment', 'b1011c_contraceptive_coverage_mandate',
         'b1011c_power_sector_co2_cap', 'b1011cfl_click_through_affiliate_nexus_sales_tax', 'b1011eh_complete_streets_statute',
         'b1011tl_dl_unauthorized_immigrants', 'b1011tl_pay_range_default_disclosure', 'b1011tl_snap_call_center',
         'b1011tl_snap_phone_interview_waiver', 'b1011tl_state_ice_detainer_limit_violent_crime', 'b1011tl_wd_good_faith_covenant',
         'b1012cr_arrestee_dna_collection', 'c36_cspp_w_abortion_parental_notice_1983_2014')
REOPEN = tuple(d for d in CLASS if d not in STAY_TERMINAL)


def candidates(pilot):
    """Rows of the 12 that are parked with history not done (a reopened design has a new attempt and no longer qualifies)."""
    from .autopilot import read
    rows = []
    for row in read(pilot.registry / 'ACTIVE_DESIGNS.json')['designs']:
        if row['design_id'] in REOPEN and pilot.next_action(row) == 'PARK' and pilot.done(row, 'history') is None:
            rows.append(row)
    return rows


def apply(pilot=None, task=TASK):
    from .autopilot import Autopilot, read
    from .ops.watchdog import clean_stop, guard
    from .requeue import requeue
    p = pilot or Autopilot()
    out = p.repo / 'data/l2_pipeline/autopilot/intake_panel/q4'
    authority = p.repo / RULING
    done = []
    with guard(p.registry):
        if not (p.registry / 'AUTOPILOT_STOP').exists() or read(p.registry / 'CODE_TASK.lock', {}).get('task') != task:
            raise ValueError(task + ' stopped code window required')
        clean_stop(p.repo)
        for row in candidates(p):
            payload = read(row['card'])
            payload['version'] = 'v' + str(int(payload['version'][1:]) + 1)
            card = ensure_once(Path(row['card']).with_name(Path(row['card']).stem + '_' + payload['version'] + '.json'), payload)
            ruling = ensure_once(out / 'rulings' / (row['design_id'] + '.json'), dict(
                schema='l2_requeue_ruling_v1', design_id=row['design_id'], prior_card_sha256=sha(row['card']), card_sha256=sha(card),
                decision='resume', superseded_status=STATUS,
                authority=dict(path=str(authority.resolve()), sha256=sha(authority)),
                reason='INTAKE_PANEL Q4: one reopen of a history park that predates the fetch-and-hash route; card unchanged except its version',
                execution_authorized=False))
            done.append(requeue(row['design_id'], card, ruling, p))
    return done
