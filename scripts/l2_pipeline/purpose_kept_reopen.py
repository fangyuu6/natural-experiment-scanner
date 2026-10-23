"""Window 2026-10-17: put the five C85 D8 designs back to the purpose-direction step with their accepted purpose.

``c85.reopen_v31`` requeued them with decision ``resume``; ``requeue`` then dropped the purpose each had accepted, the purpose
job ran again, its prose line never equalled the card's machine id, and all five STOPped at ``purpose``
(YIELD_DIAGNOSTIC_20261017 class B). With the ``requeue`` fix (``kept_purpose``) a new ``resume`` attempt keeps the accepted
purpose of the nearest earlier attempt (here: the v3 attempt) because first_order, population and outcome are unchanged.

Same authority and same decision as the original reopen (HUB_RULING_C85_D8_REMAINDER_CENSUS, owner approval 2026-10-12):
nothing new is decided; the code defect is repaired. Each design is reopened once (idempotent), the card is the previous
card with only its version bumped (written beside it), nothing is dispatched, fitted or deleted.
"""
from pathlib import Path

from .storage import ensure_once, sha

STATUS = 'SUPERSEDED_PURPOSE_KEPT'
TASK = 'WINDOW_20261017'
CLASS = ('b1010_interlock_all_offender', 'b1010b_pain_clinic_law', 'b1011cfl_state_constitutional_tax_expenditure_limit',
         'bulk10_health_sunday_alcohol_sales_repeal', 'b1011cfl_supermajority_tax_increase_requirement')


def candidates(pilot):
    """Rows of the five that are STOPped at purpose with no purpose checkpoint and an earlier accepted purpose that still applies."""
    from .autopilot import read
    from .c70_l3 import protected
    from .requeue import kept_purpose
    rows = []
    for row in read(pilot.registry / 'ACTIVE_DESIGNS.json')['designs']:
        if row['design_id'] not in CLASS or pilot.next_action(row) != 'STOP' or pilot.done(row, 'purpose') is not None:
            continue
        stop = pilot.stop_record(row)
        if not stop or stop.get('action') != 'purpose' or protected(pilot.backend.root(row)):
            continue
        payload = read(row['card'])
        if kept_purpose(pilot, row, payload) is None:
            continue
        rows.append(row)
    return rows


def stamp_pin(pilot):
    """The rules descriptor the new cards carry: the pointer's version (REDESIGN W2 ruling, item 5: one rules version per run, the five
    designs have no sealed work, so they are stamped like every other reopened design). None only when no rules were ever published
    (a repository without the rules file); a pointer below R2 refuses, because the five must not run on R0 or R1."""
    from . import rules
    pointer = rules.current(pilot.repo)
    if pointer is None:
        return None
    if rules.number(pointer['version']) < 2:
        raise ValueError('Rules R2 must be published before the five D8 designs are reopened (pointer: ' + pointer['version'] + ')')
    return rules.descriptor(pointer['version'], pilot.repo)


def apply(pilot=None, task=None):
    """Run in any stopped code window. ``task`` defaults to the name in CODE_TASK.lock when that is a WINDOW_* task (it was
    hard-wired to WINDOW_20261017, so the call refused in every later window); the lock must name the task and the loop be stopped."""
    from .autopilot import Autopilot, read
    from .c85 import RULE
    from .ops.watchdog import clean_stop, guard
    from .requeue import requeue
    p = pilot or Autopilot()
    out = p.repo / 'data/l2_pipeline/autopilot/purpose_kept'
    authority = p.repo / RULE
    done = []
    with guard(p.registry):
        held = read(p.registry / 'CODE_TASK.lock', {}).get('task')
        if task is None:   # the open window's own name, but only for a window task (WINDOW_*), never another lock holder
            task = held if isinstance(held, str) and held.startswith('WINDOW_') else TASK
        if not (p.registry / 'AUTOPILOT_STOP').exists() or held != task:
            raise ValueError(task + ' stopped code window required')
        clean_stop(p.repo)
        pin = stamp_pin(p)
        rows = candidates(p)
        if pin:   # all five must be reopenable under the pinned rules before anything is written
            from . import rules
            from .requeue import retained_reason
            for row in rows:
                found = rules.classify(rules.of_payload(dict(automation=dict(rules=pin)), p.repo), rules.retained_action(p, row), retained_reason(p, row))
                if found is None or found['reopen'] != 'on_new_rules':
                    raise ValueError('Not reopenable under rules ' + pin['version'] + ': ' + row['design_id'])
        for row in rows:
            payload = read(row['card'])
            payload['version'] = 'v' + str(int(payload['version'][1:]) + 1)
            if pin:
                payload.setdefault('automation', {})['rules'] = pin   # the only other diff
            card = ensure_once(Path(row['card']).with_name(Path(row['card']).stem + '_' + payload['version'] + '.json'), payload)
            ruling = ensure_once(out / 'rulings' / (row['design_id'] + '.json'), dict(
                schema='l2_requeue_ruling_v1', design_id=row['design_id'], prior_card_sha256=sha(row['card']), card_sha256=sha(card),
                decision='resume', superseded_status=STATUS, prior_stop=p.stop_record(row),
                **(dict(implementation_rule='REDESIGN_R1', rules=pin) if pin else {}),
                authority=dict(path=str(authority.resolve()), sha256=sha(authority)),
                reason='C85 D8 reopen (owner approval 2026-10-12) repeated with the requeue fix that keeps the accepted purpose; '
                       'card unchanged except its version' + (' and automation.rules ' + pin['version'] + ' (HUB_RULING_20261017_W2_OPEN_ITEMS item 5)' if pin else ''),
                execution_authorized=False))
            done.append(requeue(row['design_id'], card, ruling, p))
    return done


def main(argv=None):
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', help='task name of the open code window (default: the name in CODE_TASK.lock)')
    from .autopilot import Autopilot
    if stamp_pin(Autopilot()) is None:   # the command line never runs on R0
        raise SystemExit('Rules R2 must be published first')
    print(json.dumps(apply(task=parser.parse_args(argv).task), indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
