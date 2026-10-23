"""REDESIGN W3 (HUB_RULING_20261017_REDESIGN section 4): intake replay of S3, the structural unit list on cards, history and audit reuse
by default, the intake throttle, rules R3. Synthetic data only (the intake record of the card-build test is an inline fixture, no data file is read);
no outcome value is read or estimated."""
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline import history_reuse, intake_replay, named_panel, redesign, rules
from scripts.l2_pipeline.autopilot import Autopilot, atomic, ingest, read
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests.test_named_panel_20261016 import q1_fixture
from scripts.l2_pipeline.tests.test_redesign_w1 import Base, rules_value
from scripts.l2_pipeline.tests.test_redesign_w2 import rules_value_r2

REPO = Path(__file__).resolve().parents[3]
HISTORY_REASON = 'ValueError: V9 known status requires a located quoted official evidence ref'
S3_PREFIX = 'S3_SIBLING: '

def synthetic_intake_record():
    """Inline intake record of the real record shape (adoption list, sources, one literature reference); no data file."""
    states = dict(AL=2002, AZ=2009, AR=2001, DE=1979, GA=2005, ID=1973, IN=2001, KS=1992, KY=1992, LA=1995, ME=1990, MA=1990, MI=1993,
                  MN=2003, MS=1992, MO=2005, MT=1995, NE=1995, NC=2011, ND=1991, OH=1991, OK=2005, PA=1991, SC=2003, SD=1980, TN=1991,
                  TX=2003, UT=1990, VA=2001, WV=2004, WI=1985)
    return dict(
        schema='bulk20261015_queue_v3_record_v1', created='2026-10-15', group='synthetic', record_id='synthetic_waiting_period_fertility',
        design_id='synthetic_waiting_period_fertility', seed=202615108, rating='C+', verdict='CONDITIONAL', verdict_note='synthetic fixture',
        policy=dict(name='abortion mandatory waiting period', family='abortion_waiting_period',
                    predicate_clean='State-year = 1 from the first calendar year in which a state statute requires a mandatory delay of at least 18 hours.',
                    predicate_with_provenance='Year per state from the synthetic adoption list; states dated before the first usable year are excluded.',
                    clock_note='Operative year = calendar year in which the delay first applies.'),
        population=dict(description='General fertility rate, women aged 15-44', filters=None),
        outcome=dict(series_id='nchs_summary_fertility_rate', outcome_unit='births per 1,000 females aged 15-44'),
        first_order=dict(target_outcome='births', target_description='general_fertility_rate_after_abortion_waiting_period',
                         relation='closest_proxy', target_population='Women aged 15-44 resident in the state',
                         proxy_justification='V6.3 same-construct series; the law bears on a small share of the group.',
                         expected_dilution='General fertility rate covers all women 15-44; no numeric dilution factor is inferred.',
                         literature=[dict(doi='10.1001/jama.1997.03550080063040', title='The Impact of Mississippi\'s Mandatory Delay Law on Abortions and Births',
                                          authors='Joyce T', year=1997, journal='JAMA', locator='Synthetic locator', locator_short='Joyce et al. 1997 (JAMA)',
                                          fit='yes - same construct', supports=['target_outcome'],
                                          license='unverified (publisher copyright; cite only)')]),
        adoption=dict(window=[1995, 2013], coverage_end='2013-12-31', complete_list=True, states=states,
                      never=['AK', 'CA', 'CO', 'CT', 'FL', 'HI', 'IA', 'IL', 'MD', 'NH', 'NJ', 'NM', 'NV', 'NY', 'OR', 'RI', 'VT', 'WA', 'WY'],
                      other_status={}, reversals_or_ambiguities=[],
                      sources=[dict(url='https://example.org/synthetic-adoption-list', locator='synthetic fixture list',
                                    license='synthetic', complete_list=True)]),
        notes=[], l1_cross_reference=[], disposition='ready', first_order_class='first_order', disposition_reason='')


def rules_value_r3(**changes):
    value = rules_value_r2(version='R3', structural=dict(max_removed_share=[1, 3], min_treated=10, min_cohorts=3, min_controls=5, min_audited_retained=5),
                           intake=dict(max_open_before_screen=30), history=dict(reuse='default'))
    value['park_reasons'] = value['park_reasons'] + [
        dict(id='HISTORY_VALIDATOR', gates=['history'], contains=['known status requires a located quoted'], disposition='PARK',
             reopen='check:certified_sibling'),
        dict(id='HISTORY_SOURCE_NOT_CERTIFIED', gates=['history'], contains=['History reuse source is not certified'], disposition='PARK',
             reopen='check:certified_sibling')]
    value.update(changes)
    return value


def r3_document(**changes):
    return rules.validate(rules_value_r3(**changes))


class RulesR3Tests(Base):
    def test_r3_closed_schema_and_floors(self):
        value = r3_document()
        self.assertEqual(rules.structural_floors(value), named_panel.FLOORS)                 # the Q1/Q2 floors, now data
        self.assertIsNone(rules.structural_floors(rules.validate(rules_value_r2())))         # before R3 there are none
        self.assertIsNone(rules.structural_floors(None))
        for key in ('structural', 'intake', 'history'):
            broken = rules_value_r3()
            del broken[key]
            with self.assertRaisesRegex(ValueError, 'Missing rules section'):
                rules.validate(broken)
        for bad, message in ((rules_value_r3(extra=1), 'Unknown rules key'),
                             (rules_value_r3(structural=dict(max_removed_share=[1, 3], min_treated=10, min_cohorts=3, min_controls=5)), 'structural must be'),
                             (rules_value_r3(structural=dict(max_removed_share=[4, 3], min_treated=10, min_cohorts=3, min_controls=5, min_audited_retained=5)), 'structural must be'),
                             (rules_value_r3(structural=dict(max_removed_share=[1, 3], min_treated=0, min_cohorts=3, min_controls=5, min_audited_retained=5)), 'structural must be'),
                             (rules_value_r3(intake=dict(max_open_before_screen=0)), 'intake must be'),
                             (rules_value_r3(history=dict(reuse='off')), 'history must be')):
            with self.assertRaisesRegex(ValueError, message):
                rules.validate(bad)
        r2_with_r3_key = rules_value_r2(structural=dict(max_removed_share=[1, 3], min_treated=10, min_cohorts=3, min_controls=5, min_audited_retained=5))
        with self.assertRaisesRegex(ValueError, 'Unknown rules key'):                         # R2 code never reads the new sections
            rules.validate(r2_with_r3_key)
        self.publish()
        self.publish(rules_value_r2())
        pointer = self.publish(rules_value_r3())
        self.assertEqual(rules.current(self.repo)['version'], 'R3')
        self.assertEqual(rules.load(self.repo / pointer['path'])['history'], dict(reuse='default'))
        changed = [r for r in self.ledger_rows() if r.get('decision') == 'RULES_PUBLISHED'][-1]
        self.assertEqual(set(changed['sections']) >= {'structural', 'intake', 'history'}, True)

    def test_floors_from_rules_for_r3_cards_only(self):
        records = [dict(unit='T%d' % i, status='treated', start_period=2000 + i % 3) for i in range(12)]
        excluded, sample, controls = {'T0', 'T1', 'T2', 'T3', 'T4'}, ['T%d' % i for i in range(10)], ['C%d' % i for i in range(5)]
        default = named_panel.check_floors(records, excluded, sample, controls)
        self.assertEqual(default, named_panel.check_floors(records, excluded, sample, controls, floors=named_panel.FLOORS))   # Q1/Q2 call unchanged
        self.assertIn('more than 1/3 of the registered treated states removed', default[1])
        self.assertIn('fewer than 10 treated states retained', default[1])
        loose = rules.structural_floors(r3_document(structural=dict(max_removed_share=[1, 2], min_treated=6, min_cohorts=2, min_controls=3, min_audited_retained=2)))
        figures, failures = named_panel.check_floors(records, excluded, sample, controls, floors=loose)
        self.assertEqual(failures, [])
        self.assertEqual(figures['audited_retained'], 5)
        figures, failures = named_panel.check_floors(records, excluded, None, controls, floors=loose)   # no audit sample: that floor is skipped
        self.assertEqual((failures, figures['audited_retained'], figures['audited_total']), ([], None, None))
        strict = dict(loose, min_audited_retained=6)
        self.assertEqual(named_panel.check_floors(records, excluded, sample, controls, floors=strict)[1], ['fewer than 6 of the audited states retained'])
        self.assertEqual(named_panel.check_floors(records, excluded, None, controls, floors=strict)[1], [])


def fixture_payload():
    """The Q1 fixture card (window 2000-2008) as a card payload that registered_cards accepts, with its roster and sidecar keys."""
    records, card, series = q1_fixture()
    keys = {(u, y) for u in series for y in range(2000, 2009)}
    return card, records, set(series), keys


class StructuralListTests(unittest.TestCase):
    def test_build_time_list_equals_the_replay_list_and_the_receipt_is_checked(self):
        card, records, series, keys = fixture_payload()
        universe = [r['unit'] for r in records]
        adopters = {r['unit']: r['start_period'] for r in records if r['status'] == 'treated'}
        listed = intake_replay.structural_list(universe, series, adopters, (2000, 2008), dict(min_pre_periods=3, min_post_periods=2))
        self.assertEqual(listed, {'B0': 'adopted_before_window', 'B1': 'too_few_pre_periods', 'B2': 'too_few_post_periods',
                                  'C6': 'absent_from_outcome_series'})
        computed, _ = named_panel.structural_exclusions(card, records, sorted(series))
        self.assertEqual({u: v['reason'] for u, v in computed.items()}, listed)                 # the card list and the replay list agree
        value = intake_replay.receipt(listed)
        self.assertEqual(value, intake_replay.receipt(dict(reversed(list(listed.items())))))     # order-free hash
        payload = dict(card, treatment=dict(excluded_units=sorted(listed)), automation=dict(intake_replay=value))
        self.assertEqual(intake_replay.declared(payload), value)
        self.assertIsNone(intake_replay.declared(dict(card)))
        edited = deepcopy(payload)
        edited['automation']['intake_replay']['excluded_units']['B0'] = 'too_few_pre_periods'
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            intake_replay.declared(edited)
        short = deepcopy(payload)
        short['treatment']['excluded_units'].remove('B1')
        with self.assertRaisesRegex(ValueError, 'missing from treatment.excluded_units: B1'):
            intake_replay.declared(short)
        with self.assertRaisesRegex(ValueError, 'reasons must be one of'):
            intake_replay.receipt(dict(B0='because'))

    def test_card_build_lists_structural_units(self):
        sys.path.insert(0, str(REPO / 'scripts' / 'l1'))
        try:
            import b1015_cards as builder
        finally:
            sys.path.pop(0)
        record = synthetic_intake_record()
        old_pin = dict(version='R2', sha256='1' * 64, path='data/l2_pipeline/rules/RULES_R2.json')
        new_pin = dict(version='R3', sha256='2' * 64, path='data/l2_pipeline/rules/RULES_R3.json')
        with patch.object(rules, 'stamp', return_value=old_pin):
            before, _ = builder.build_card(deepcopy(record))
        with patch.object(rules, 'stamp', return_value=new_pin):
            after, diag = builder.build_card(deepcopy(record))
        self.assertNotIn('excluded_units', before['treatment'])                                  # R0-R2 cards: byte-identical to W2
        self.assertNotIn('intake_replay', before['automation'])
        expected = sorted(set(diag['left']) | set(diag['late']))
        self.assertTrue(expected)
        self.assertTrue(set(expected) <= set(after['treatment']['excluded_units']))
        self.assertEqual(sorted(after['treatment']['excluded_units']), sorted(after['automation']['intake_replay']['excluded_units']))
        self.assertTrue(set(after['automation']['intake_replay']['excluded_units'].values()) <= set(intake_replay.REASONS))
        self.assertEqual(intake_replay.declared(after), after['automation']['intake_replay'])
        self.assertEqual(after['automation']['rules'], new_pin)
        rest = lambda c: {k: v for k, v in c.items() if k not in ('treatment', 'automation')}
        self.assertEqual(rest(before), rest(after))
        self.assertEqual({k: v for k, v in after['treatment'].items() if k != 'excluded_units'}, before['treatment'])
        from scripts.l2_pipeline.cards import validate_card
        self.assertEqual(validate_card(after), [])


class ReplayTests(unittest.TestCase):
    FLOORS = rules.structural_floors(r3_document())

    def test_replay_statuses(self):
        card, records, series, keys = fixture_payload()
        # the card lists nothing: the replay names the units to add, S3 passes with them
        result = intake_replay.replay(deepcopy(card), records, series, keys, self.FLOORS)
        self.assertEqual(result['status'], 'NEEDS_EXCLUSIONS')
        self.assertEqual(result['missing'], {'B0': 'adopted_before_window', 'B1': 'too_few_pre_periods', 'B2': 'too_few_post_periods',
                                             'C6': 'absent_from_outcome_series'})
        self.assertIn('B1 (too_few_pre_periods)', result['reason'])
        self.assertEqual(result['outcome_values_read'], 0)
        # the card lists them (with the receipt): CARD, with the figures of the floors
        listed = {u: v for u, v in result['missing'].items()}
        full = deepcopy(card)
        full['treatment'] = dict(excluded_units=sorted(listed))
        full['automation'] = dict(intake_replay=intake_replay.receipt(listed))
        ok = intake_replay.replay(full, records, series, keys, self.FLOORS)
        self.assertEqual((ok['status'], ok['missing'], ok['failures'], ok['issues']), ('CARD', {}, [], []))
        self.assertEqual((ok['figures']['retained_treated'], ok['figures']['removed_treated']), (12, 3))
        # floors fail (needs 13 treated): not evaluable, the park text carries the failed floor
        strict = dict(self.FLOORS, min_treated=13)
        bad = intake_replay.replay(full, records, series, keys, strict)
        self.assertEqual(bad['status'], 'NOT_EVALUABLE')
        self.assertEqual(bad['reason'], S3_PREFIX + 'fewer than 13 treated states retained')
        # S3 itself fails on the sidecar (the controls are not observed): not evaluable with the issue keys
        no_controls = {(u, y) for (u, y) in keys if not u.startswith('C')}
        failed = intake_replay.replay(full, records, series, no_controls, self.FLOORS)
        self.assertEqual(failed['status'], 'NOT_EVALUABLE')
        self.assertTrue(failed['reason'].startswith(S3_PREFIX) and failed['issues'])
        self.assertFalse(failed['replay_passed'])
        # a card with no structural unit is judged by the unchanged S3 check alone: the floors do not apply
        plain_records = [r for r in records if r['unit'] not in {'B0', 'B1', 'B2', 'C6'}]
        plain = intake_replay.replay(deepcopy(card), plain_records, series - {'C6'}, {k for k in keys if k[0] != 'C6'}, strict)
        self.assertEqual((plain['status'], plain['failures']), ('CARD', []))

    def test_check_card_skips_what_is_not_replayable_and_uses_the_certified_roster(self):
        card, records, series, keys = fixture_payload()
        pilot = type('P', (), dict(repo=REPO))()
        r3 = r3_document()
        with patch.object(rules, 'of_payload', return_value=rules.validate(rules_value_r2())):
            self.assertEqual(intake_replay.check_card(pilot, 'x.json', dict(card, design_id='d'))['status'], 'SKIPPED')
        with patch.object(rules, 'of_payload', return_value=r3), patch.object(history_reuse, 'certified_roster', return_value=None):
            self.assertEqual(intake_replay.check_card(pilot, 'x.json', dict(card, design_id='d'))['status'], 'SKIPPED')
        certified = dict(source_design_id='src', records=records, sample=[], excluded_units=['T0'])
        with patch.object(rules, 'of_payload', return_value=r3), patch.object(history_reuse, 'certified_roster', return_value=certified), \
                patch.object(intake_replay, 'series_keys', return_value=(keys, series)):
            result = intake_replay.check_card(pilot, 'x.json', dict(card, design_id='d'))
        self.assertEqual((result['status'], result['source_design_id']), ('NEEDS_EXCLUSIONS', 'src'))
        self.assertIn('T0', result['excluded'])                                                  # the source's own exclusions are applied too


class IngestBase(unittest.TestCase):
    """An R3-pinned example card in a temporary registry with the mock agent backend."""

    def setUp(self):
        from scripts.l2_pipeline.tests.test_autopilot import MockAgents
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.registry = self.repo / 'data/l2_pipeline/registry'
        self.backend = MockAgents(self.repo)
        self.pilot = Autopilot(self.registry, self.backend)
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[]))
        self.cards = self.repo / 'data/l2_pipeline/cards'
        self.cards.mkdir(parents=True)
        card = json.loads((Path(__file__).parents[1] / 'examples/design.json').read_text())
        card.update(design_id='intake_r3')
        card['analysis']['min_post_periods'] = 2
        card['treatment']['universe'] = ['A', 'B', 'C']
        card['treatment']['seed'] = 5
        card.setdefault('automation', {})['rules'] = dict(version='R3', sha256='a' * 64, path='data/l2_pipeline/rules/RULES_R3.json')
        atomic(self.cards / 'intake_r3.json', card)
        self.r3 = r3_document()
        from scripts.l2_pipeline import gate0
        self.real_persist = gate0.persist
        for target, value in (('scripts.l2_pipeline.rules.of_payload', lambda payload, repo=None: self.r3 if (payload.get('automation') or {}).get('rules') else None),
                              ('scripts.l2_pipeline.gate0.persist', lambda pilot, row: None)):   # the catalog gate is not under test here
            patcher = patch(target, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_ingest(self, replay, required=None):
        with patch.object(intake_replay, 'check_card', return_value=replay), patch.object(history_reuse, 'required_source', return_value=required):
            return ingest(self.cards, self.pilot)


class IngestTests(IngestBase):
    """The ingest hook: a failing certified roster is ingested as PRESCREEN_PARK with no packet; a passing one proceeds."""

    def test_intake_replay_sibling_roster_fails(self):
        """The park is the existing Gate 0 / prescreen one: an R3 card that reuses a certified history is replayed inside
        ``prescreen.evaluate`` and parked as S3_SIBLING with no packet, which Gate 0 records at ingest."""
        from scripts.l2_pipeline import gate0, prescreen
        from scripts.l2_pipeline.tests.test_gate0 import fixture
        root = self.repo / 'gate0_card'
        root.mkdir()
        card = fixture(root)
        units = card['treatment']['universe']
        card['treatment']['excluded_units'] = ['AL']
        card['automation'] = dict(rules=dict(version='R3', sha256='a' * 64, path='data/l2_pipeline/rules/RULES_R3.json'),
                                  history_reuse=dict(source_design_id='src', rule=history_reuse.RULING),
                                  intake_replay=intake_replay.receipt({'AL': 'adopted_before_window'}))
        records = [dict(unit='AL', status='treated', start_period=1995)]
        records += [dict(unit=u, status='treated', start_period=[2005, 2008, 2011][i % 3]) for i, u in enumerate(units[1:13])]
        records += [dict(unit=u, status='never_treated', start_period=None) for u in units[13:]]
        certified = dict(source_design_id='src', records=records, sample=[], excluded_units=[])
        with patch('scripts.l2_pipeline.population_resolution.choose', side_effect=ValueError('new construct')), \
                patch.object(prescreen, 'screen_ri', return_value=dict(passed=True, checks={})):
            ok = prescreen.evaluate(deepcopy(card), root, certified=certified)
            self.assertEqual(ok['status'], 'PROCEED')                                              # 12 treated retained: the floors hold
            self.assertEqual((ok['intake_replay']['status'], ok['intake_replay']['structural']), ('CARD', ['AL']))
            strict = deepcopy(self.r3)
            strict['structural']['min_treated'] = 13
            with patch.object(rules, 'of_payload', return_value=strict):
                parked = prescreen.evaluate(deepcopy(card), root, certified=certified)
            self.assertEqual(parked['status'], 'PRESCREEN_PARK')
            self.assertEqual(parked['reasons'], [S3_PREFIX + 'fewer than 13 treated states retained'])
            self.assertEqual((parked['outcome_values_read'], parked['actual_data_estimation_calls'], parked['variants']), (0, 0, []))
            # earlier rules never reach the replay: byte-identical to the W2 behaviour
            with patch.object(rules, 'of_payload', return_value=rules.validate(rules_value_r2())):
                self.assertNotIn('intake_replay', prescreen.evaluate(deepcopy(card), root, certified=certified))
            # Gate 0 turns the park into GATE0_PARK at ingest, with no packet and outside the L2 100
            row = dict(design_id='intake_r3', attempt_id='new', route='V9', card=str(ensure_once(root / 'card.json', card)),
                       card_sha256=sha(root / 'card.json'), work_dir=str(self.repo / 'work'), prescreen_enabled=True)
            with patch.object(rules, 'of_payload', return_value=strict), patch.object(history_reuse, 'certified_roster', return_value=certified), \
                    patch.object(gate0, 'audit_selected', return_value=False):
                receipt = self.real_persist(self.pilot, row)
                self.assertEqual((receipt['status'], receipt['reasons']), ('GATE0_PARK', parked['reasons']))
                self.assertEqual(gate0.next_status(self.pilot, row), 'GATE0_PARK')
            self.assertEqual(self.backend.launches, [])

    def test_passing_roster_proceeds_and_a_card_that_needs_a_list_or_a_link_is_refused(self):
        passing = dict(status='CARD', reason=None, source_design_id='src')
        self.assertEqual(self.run_ingest(passing)['ingested'], 1)
        row = read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]
        self.assertFalse((self.pilot.case_dir(row) / 'PRESCREEN.json').exists())
        self.assertEqual(self.pilot.next_action(row), 'history')
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[]))
        parked = dict(status='NOT_EVALUABLE', reason=S3_PREFIX + 'fewer than 13 treated states retained')
        self.assertEqual(self.run_ingest(parked)['ingested'], 1)                                   # Gate 0 (not this hook) records the park
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[]))
        needs = dict(status='NEEDS_EXCLUSIONS', reason='card lacks structural exclusions: DC (absent_from_outcome_series)')
        with self.assertRaisesRegex(ValueError, 'Intake replay: intake_r3 card lacks structural exclusions: DC'):
            self.run_ingest(needs)
        with self.assertRaisesRegex(ValueError, 'must carry automation.history_reuse naming src'):
            self.run_ingest(passing, required='src')
        self.assertEqual(read(self.registry / 'ACTIVE_DESIGNS.json')['designs'], [])              # nothing was admitted

    def test_cards_of_earlier_rules_are_not_replayed(self):
        card = read(self.cards / 'intake_r3.json')
        del card['automation']['rules']
        atomic(self.cards / 'intake_r3.json', card)
        with patch.object(intake_replay, 'check_card', wraps=intake_replay.check_card) as check:
            self.assertEqual(ingest(self.cards, self.pilot)['ingested'], 1)
        self.assertEqual(check.call_args.args[2]['design_id'], 'intake_r3')
        self.assertEqual(check.call_args_list[0].args[0], self.pilot)


class ThrottleTests(IngestBase):
    def test_throttle_blocks_over_limit(self):
        from scripts.l2_pipeline.ops import ingest_gate
        paths = [self.cards / 'intake_r3.json']
        rows = [dict(design_id='w%d' % i, attempt_id='test', work_dir=str(self.repo / 'data/l2_pipeline/work'), card='invented', card_sha256='invented',
                     route='V9', managed_execution=True) for i in range(4)]
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=rows))
        self.assertEqual(ingest_gate.waiting_before_screen(self.pilot), 4)                        # fresh designs wait at history
        small = deepcopy(self.r3)
        small['intake'] = dict(max_open_before_screen=5)
        with patch('scripts.l2_pipeline.rules.of_payload', return_value=small):
            self.assertIsNone(ingest_gate.throttle(self.pilot, paths))                             # 4 + 1 = 5 fits
            small['intake'] = dict(max_open_before_screen=4)
            with self.assertRaisesRegex(ValueError, 'INGEST_THROTTLED: 4 designs wait before screen and 1 new cards would exceed max_open_before_screen 4'):
                ingest_gate.throttle(self.pilot, paths)
            with patch.object(ingest_gate, 'throttle', side_effect=ValueError('INGEST_THROTTLED: stop')), self.assertRaisesRegex(ValueError, 'INGEST_THROTTLED'):
                ingest(self.cards, self.pilot)                                                     # ingest calls it before anything is admitted
        # a design that reached screen, or that parked, no longer counts
        with patch.object(Autopilot, 'next_action', side_effect=lambda row: {'w0': 'audit', 'w1': 'STOP', 'w2': 'screen', 'w3': 'history'}[row['design_id']]):
            self.assertEqual(ingest_gate.waiting_before_screen(self.pilot), 2)
        # cards of earlier rules versions are never limited
        with patch('scripts.l2_pipeline.rules.of_payload', return_value=rules.validate(rules_value_r2())):
            self.assertIsNone(ingest_gate.throttle(self.pilot, paths))
        # the pre-existing canary rule is untouched by the new function
        self.assertTrue(callable(ingest_gate.check))


class ReuseFixtureTests(unittest.TestCase):
    """History reuse over the synthetic certified source of the YIELD_FIXES tests (built once per test, torn down with it)."""

    def setUp(self):
        from scripts.l2_pipeline.tests.test_yield_fixes_20261016 import HistoryReuseTests
        self.h = HistoryReuseTests('test_reuse_materialises_the_certified_history_and_s1_accepts_it')
        self.h.setUp()
        self.addCleanup(self.h.tearDown)
        self.r3 = r3_document()
        self.r2 = rules.validate(rules_value_r2())

    def payload(self):
        return read(self.h.target_row['card'])

    def test_reuse_required_when_certified_sibling_exists(self):
        h = self.h
        payload = self.payload()
        undeclared = deepcopy(payload)
        del undeclared['automation']['history_reuse']
        undeclared['design_id'] = 'new_card'
        with patch.object(rules, 'of_payload', return_value=self.r3):
            self.assertEqual(history_reuse.required_source(h.pilot, undeclared), h.source_id)
            self.assertIsNone(history_reuse.required_source(h.pilot, payload))                    # already carries the link
            different = deepcopy(undeclared)
            different['treatment']['predicate'] = 'a different policy'
            self.assertIsNone(history_reuse.required_source(h.pilot, different))                  # not the same policy: no sibling
            other_universe = deepcopy(undeclared)
            other_universe['treatment']['universe'] = other_universe['treatment']['universe'][:-1]
            self.assertIsNone(history_reuse.required_source(h.pilot, other_universe))
        with patch.object(rules, 'of_payload', return_value=self.r2):
            self.assertIsNone(history_reuse.required_source(h.pilot, undeclared))                 # R2 cards: behaviour unchanged
        with patch.object(rules, 'of_payload', return_value=None):
            self.assertIsNone(history_reuse.required_source(h.pilot, undeclared))
        # a source that is between attempts still counts: the card waits for it (never runs a second A/B coding)
        with patch.object(rules, 'of_payload', return_value=self.r3):
            h.pilot.checkpoints.pop((h.source_id, 'S1'))
            h.pilot.next_action = lambda row: 'S0'
            self.assertEqual(history_reuse.required_source(h.pilot, undeclared), h.source_id)
            h.pilot.next_action = lambda row: 'PARK'                                              # parked before S1: never certified, no sibling
            self.assertIsNone(history_reuse.required_source(h.pilot, undeclared))

    def test_source_parked_dependents_classified(self):
        h = self.h
        payload = self.payload()
        h.pilot.checkpoints.pop((h.source_id, 'S1'))
        h.pilot.next_action = lambda row: 'PARK'
        with patch.object(rules, 'of_payload', return_value=self.r3):
            with self.assertRaises(ValueError) as found:
                history_reuse.verify_source(h.pilot, h.target_row, payload, h.source_id)
        self.assertNotIsInstance(found.exception, history_reuse.SourceNotReady)
        text = 'ValueError: ' + str(found.exception)
        klass = rules.classify(self.r3, 'history', text)
        self.assertEqual((klass['id'], klass['reopen']), ('HISTORY_SOURCE_NOT_CERTIFIED', 'check:certified_sibling'))
        # not parked, or rules before R3: the old wait
        for of_payload, action in ((self.r3, 'S0'), (self.r2, 'PARK'), (None, 'STOP')):
            h.pilot.next_action = lambda row, a=action: a
            with patch.object(rules, 'of_payload', return_value=of_payload):
                with self.assertRaises(history_reuse.SourceNotReady):
                    history_reuse.verify_source(h.pilot, h.target_row, payload, h.source_id)

    def test_reuse_waits_then_proceeds(self):
        h = self.h
        row = dict(h.target_row, prescreen_enabled=False)
        calls = []

        def reuse(pilot, r):
            calls.append(1)
            if len(calls) == 1:
                raise history_reuse.SourceNotReady('History reuse source S1 is not READY: ' + h.source_id)
            return dict(source_design_id=h.source_id, record='r.json', checkpoints=dict(history=dict(jobs=[])))
        events = []
        pilot = type('P', (), dict(ledger=lambda self_, r, a, d, k, **kw: events.append((a, d)), reuse_action=Autopilot.reuse_action))()
        with patch.object(history_reuse, 'reuse', side_effect=reuse):
            self.assertIsNone(pilot.reuse_action(row, 'history'))                                 # source not ready: wait, never STOP
            self.assertEqual(pilot.reuse_action(row, 'history'), dict(jobs=[]))
        self.assertEqual(events, [('history', 'WAIT_HISTORY_REUSE_SOURCE'), ('history', 'HISTORY_REUSED')])

    def test_audit_resolutions_copied(self):
        h = self.h
        source_file = h.ctx.design_dir / 'AUDIT_RESOLUTIONS.json'
        source_file.write_text(json.dumps(dict(schema='fixture', note='the resolutions of the source audit')))
        with patch('scripts.l2_pipeline.intake.official_history.load_audit_resolutions', return_value=None):
            value = history_reuse.reuse(h.pilot, h.target_row)
            record = read(value['record'])
            self.assertEqual(record['source_artifacts']['AUDIT_RESOLUTIONS.json']['sha256'], sha(source_file))
            self.assertEqual(record['copies']['AUDIT_RESOLUTIONS.json'], str(h.target_ctx.design_dir / 'AUDIT_RESOLUTIONS.json'))
            copy = h.target_ctx.design_dir / 'AUDIT_RESOLUTIONS.json'
            self.assertEqual(copy.read_bytes(), source_file.read_bytes())
            copy.unlink()                                                                         # a crash between the copies is repaired
            self.assertEqual(history_reuse.reuse(h.pilot, h.target_row), value)
            self.assertEqual(copy.read_bytes(), source_file.read_bytes())
            original = source_file.read_text()
            source_file.write_text(original + ' ')
            try:
                with self.assertRaisesRegex(ValueError, 'source artifact changed'):
                    history_reuse.reuse(h.pilot, h.target_row)
            finally:
                source_file.write_text(original)
        # a source without the file reuses as before: nothing optional in the record
        source_file.unlink()
        other, ctx = h.target('second_target')
        h.pilot.contexts['second_target'] = ctx
        atomic(h.pilot.registry / 'ACTIVE_DESIGNS.json', dict(designs=[h.source_row, other]))
        with patch('scripts.l2_pipeline.intake.official_history.load_audit_resolutions', return_value=None):
            record = read(history_reuse.reuse(h.pilot, other)['record'])
        self.assertNotIn('AUDIT_RESOLUTIONS.json', record['source_artifacts'])
        self.assertEqual(set(record['source_artifacts']), set(history_reuse.REQUIRED))


class SiblingReopenTests(Base):
    """R3 reopens a HISTORY park only onto an exact-match certified sibling; the new card differs only in rules, version and that link."""

    def setUp(self):
        super().setUp()
        self.publish()
        self.publish(rules_value_r2())
        self.publish(rules_value_r3())
        self.row = self.make_row('h1', pinned=False)
        self.pilot.ledger({}, 'loop', 'CLEAN_STOP', 'record')
        self.registry.joinpath('AUTOPILOT_STOP').touch()
        self.pilot.stop(self.row, 'history', HISTORY_REASON)
        self.authority = self.repo / redesign.RULING
        self.authority.parent.mkdir(parents=True, exist_ok=True)
        self.authority.write_text('fixture ruling')
        self.patch = patch.object(history_reuse, 'candidates', return_value=['src'])
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def card_with(self, link, version='R3'):
        card = read(self.row['card'])
        card.update(version='v2')
        card.setdefault('automation', {})['rules'] = self.pin(version)
        if link is not None:
            card['automation']['history_reuse'] = link
        path = self.cards / 'h1_v2.json'
        atomic(path, card)
        ruling = self.repo / 'data/l2_pipeline/rulings/sibling.json'
        atomic(ruling, dict(schema='l2_requeue_ruling_v1', design_id='h1', prior_card_sha256=self.row['card_sha256'], card_sha256=sha(path),
                            decision='resume', superseded_status='SUPERSEDED_REDESIGN_R1', implementation_rule='REDESIGN_R1',
                            rules=self.pin(version), reason='fixture', execution_authorized=False,
                            authority=dict(path=str(self.authority), sha256=sha(self.authority))))
        return path, ruling

    def test_plan_reopens_only_with_a_certified_sibling_and_only_under_r3(self):
        plan = {i['design_id']: i for i in redesign.plan(self.pilot, 'R3')}['h1']
        self.assertEqual((plan['class_id'], plan['reopen'], plan['eligible']), ('HISTORY_VALIDATOR', 'check:certified_sibling', True))
        self.assertFalse({i['design_id']: i for i in redesign.plan(self.pilot, 'R2')}['h1']['eligible'])   # R2: HISTORY never reopens
        with patch.object(history_reuse, 'candidates', return_value=[]):
            item = {i['design_id']: i for i in redesign.plan(self.pilot, 'R3')}['h1']
        self.assertFalse(item['eligible'])
        self.assertIn('no exact-match certified sibling', item['why_not'])
        declared = read(self.row['card'])
        declared['automation'] = dict(declared.get('automation', {}), history_reuse=dict(source_design_id='src', rule=history_reuse.RULING))
        atomic(Path(self.row['card']), declared)
        self.assertIsNone(redesign.certified_sibling_source(self.pilot, self.row))               # the card already names it: no second one

    def test_requeue_adds_exactly_the_link(self):
        from scripts.l2_pipeline.requeue import requeue
        link = dict(source_design_id='src', rule=history_reuse.RULING)
        for bad_link, message in ((None, 'only in automation.rules, version and the certified-sibling link'),
                                  (dict(link, source_design_id='elsewhere'), 'only in automation.rules, version and the certified-sibling link')):
            path, ruling = self.card_with(bad_link)
            with self.assertRaisesRegex(ValueError, message):
                requeue('h1', path, ruling, self.pilot, scheduler_owned=True)
        card = read(self.row['card'])
        card['analysis'] = dict(card['analysis'], min_controls=card['analysis'].get('min_controls', 5) + 1)
        path, ruling = self.card_with(link)
        changed = read(path)
        changed['analysis'] = card['analysis']
        atomic(path, changed)
        atomic(ruling, dict(read(ruling), card_sha256=sha(path)))
        with self.assertRaisesRegex(ValueError, 'only in automation.rules, version and the certified-sibling link'):
            requeue('h1', path, ruling, self.pilot, scheduler_owned=True)                          # the link does not license any other change
        path, ruling = self.card_with(link)
        value = requeue('h1', path, ruling, self.pilot, scheduler_owned=True)
        self.assertEqual(value['restart_action'], 'history')
        new_row = read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]
        self.assertEqual(read(new_row['card'])['automation']['history_reuse'], link)
        self.assertTrue(history_reuse.declared(new_row))
        self.assertEqual(self.pilot.next_action(new_row), 'history')

    def test_reopen_request_names_the_sibling_and_runs_once(self):
        atomic(self.registry / redesign.REQUEST, dict(scope=['HISTORY_VALIDATOR'], rules_version='R3'))
        results = redesign.apply_open_requests(self.pilot)
        self.assertEqual(results, [dict(design_id='h1', restart='history')])
        new_row = read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]
        card = read(new_row['card'])
        self.assertEqual((card['automation']['history_reuse']['source_design_id'], rules.pinned(card)['version']), ('src', 'R3'))
        receipt = read(self.pilot.registry.parent / 'autopilot/redesign_r3/requests/h1.json')
        self.assertEqual((receipt['class_id'], receipt['restart_action']), ('HISTORY_VALIDATOR', 'history'))
        self.assertEqual(redesign.apply_open_requests(self.pilot), [])                              # once per (design, version)
        errors = [r for r in self.ledger_rows() if r.get('decision') == 'REDESIGN_APPLY_ERROR']
        self.assertEqual(errors, [])


class SecondOutcomeTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(REPO / 'scripts' / 'l1'))
        try:
            import b1016_reuse_cards as module
        finally:
            sys.path.pop(0)
        self.module = module
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        (self.repo / 'data/l2_pipeline/registry').mkdir(parents=True)
        self.roots = {}
        terminal = dict(runs=[dict(design_id='est', study_estimated=True)], pre_run_stops=[])
        atomic(self.repo / 'data/l2_pipeline/registry/TERMINAL_RUNS.json', terminal)
        for name in ('ok', 'bad', 'new', 'est'):
            (self.repo / 'roots' / name).mkdir(parents=True)
        for name, passed in (('ok', True), ('bad', False)):
            atomic(self.repo / 'roots' / name / 'SUPPORT.json', dict(variants=[dict(variant='primary', passed=passed)]))
        rows = {n: dict(design_id=n) for n in ('ok', 'bad', 'new', 'est')}
        patcher = patch.object(module, 'active_rows', return_value=rows)
        patcher.start()
        self.addCleanup(patcher.stop)

        class Backend:
            def __init__(self, repo):
                self.repo = repo

            def root(self_, row):
                return self.repo / 'roots' / row['design_id']
        patcher = patch('scripts.l2_pipeline.autopilot.Backend', Backend)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_second_outcome_needs_source_s3(self):
        original = self.module.source_s3_status
        status = lambda name: original(name, self.repo)
        self.assertEqual([status(n) for n in ('ok', 'bad', 'new', 'est', 'absent')], ['passed', 'failed', 'unknown', 'passed', 'unknown'])
        r3 = dict(version='R3', sha256='2' * 64, path='data/l2_pipeline/rules/RULES_R3.json')
        with patch.object(rules, 'stamp', return_value=r3), patch.object(self.module, 'source_s3_status', side_effect=lambda d, repo=None: status(d)):
            for design, word in (('bad', 'failed'), ('new', 'unknown')):
                with self.assertRaisesRegex(SystemExit, 'has not passed S3 \\(status %s\\)' % word):
                    self.module.build_one(dict(source_design_id=design))
            with patch.object(self.module, 'source_card', side_effect=KeyError('reached the builder')), self.assertRaises(KeyError):
                self.module.build_one(dict(source_design_id='ok'))                                  # a passed source goes on to the builder
        with patch.object(rules, 'stamp', return_value=dict(r3, version='R2')), \
                patch.object(self.module, 'source_card', side_effect=KeyError('reached the builder')), self.assertRaises(KeyError):
            self.module.build_one(dict(source_design_id='bad'))                                     # earlier rules: no new gate


class UiFamilyTests(unittest.TestCase):
    """HUB_RULING_20261017_W3_OPEN_ITEMS item 1: the UI benefit-receipt family (table OUTCOME_POPULATIONS_V17B)."""
    ORDER = ['dol_eta5159_state_ui_' + u for u in ('initial_claims', 'first_payments', 'weeks_compensated', 'continued_weeks_claimed')]

    def choose(self, target, construct=None, meta=None):
        from scripts.l2_pipeline import population_resolution
        from scripts.l2_pipeline.connectors import population_v63
        source, current = population_v63.current_catalog()
        purpose = dict(status='VERIFIED', target_outcome=target, target_construct=construct)
        return population_resolution.choose(dict(policy_family='unemployment_insurance'), purpose, source, meta or current)

    def test_ui_family_ranking_deterministic(self):
        from scripts.l2_pipeline.connectors import population_v63
        self.assertEqual(population_v63.CURRENT_PATH.name, 'OUTCOME_POPULATIONS_V19.json')
        _, meta = population_v63.current_catalog()
        target = next(t for t in meta['targets'] if t['construct'] == 'ui_benefit_receipt')
        self.assertEqual(target['series_order'], self.ORDER)
        self.assertEqual(target['preferred_series'], self.ORDER[0])
        self.assertEqual(sorted(sid for sid, e in meta['series'].items() if e['construct'] == 'ui_benefit_receipt'), sorted(self.ORDER))
        first = self.choose('ui_benefit_receipt')
        self.assertEqual(first['selected']['series_id'], self.ORDER[0])
        self.assertEqual(first['decision'], 'accept')
        self.assertEqual(self.choose('ui_benefit_receipt'), first)                              # same result on repeat
        for alias in ('ui_initial_claims', 'ui_first_payments', 'ui_weeks_compensated', 'ui_continued_weeks_claimed'):
            self.assertEqual(self.choose(alias)['selected']['series_id'], self.ORDER[0])        # the old ids map to the family
        # without the order the four series are a four-way tie (same ages, filters, unit class, window): the V6.3 ranking cannot break it
        loose = deepcopy(meta)
        for t in loose['targets']:
            t.pop('preferred_series', None)
        with self.assertRaisesRegex(ValueError, 'equal population and supported-window series tie'):
            self.choose('ui_benefit_receipt', meta=loose)

    def test_pdmp_all_drug_stays_new_construct(self):
        from scripts.l2_pipeline.c42 import Park
        from scripts.l2_pipeline.connectors import population_v63
        _, meta = population_v63.current_catalog()
        ui = {sid for sid, e in meta['series'].items() if e['construct'] == 'ui_benefit_receipt'}
        # an all-drug target never resolves into the UI family, and the PDMP prescription-opioid construct is unchanged
        with self.assertRaisesRegex(Park, 'genuinely new construct question'):
            self.choose('drug_overdose_deaths')
        all_drug = self.choose('all_drug_overdose_deaths', construct='all_drug_overdose_deaths')
        self.assertEqual(all_drug['construct'], 'all_drug_overdose_deaths')
        self.assertFalse(ui & {c['series_id'] for c in all_drug['candidates']})
        opioid = self.choose('prescription_opioid_overdose_deaths')
        self.assertEqual(opioid['construct'], 'prescription_opioid_overdose_deaths')
        self.assertNotEqual(opioid['construct'], all_drug['construct'])
        self.assertFalse(ui & {c['series_id'] for c in opioid['candidates']})


if __name__ == '__main__':
    unittest.main()
