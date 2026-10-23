"""REDESIGN W1 (HUB_RULING_20261017_REDESIGN): rules file, chain by rules, screen before dispatch, purpose label, park table,
requeue/reopen tool, freeze of rules and label, V3 limits. Synthetic data only; no outcome value is read or estimated."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.l2_pipeline import redesign, rules
from scripts.l2_pipeline.autopilot import Autopilot, Backend, atomic, ingest, read
from scripts.l2_pipeline.c42 import Park
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests.test_gate0 import fixture as gate0_fixture

S3_REASON = 'S3 Disposition.STOPPED: Registered support insufficient'
LEGACY_REASON = 'ValueError: NEEDS_RULING: V6.2 target/population requires new interpretation'
CHAIN = ['history', 'reconcile', 'screen', 'audit', 'S0', 'S1', 'S2', 'S3', 'S4', 'ri', 'purpose', 'purpose-direction', 'forecasts',
         'lock', 'draft-dry-load', 'draft', 'prep', 'approve-prep', 'calibrate', 'seal', 'dry-load', 'sealed', 'approve-final',
         'execute', 'S7', 'register']


def rules_value(**changes):
    value = dict(schema='l2_rules_v1', version='R1', chain=CHAIN, purpose=dict(mode='label'), screen=dict(shifts=[0], pool_rule=False),
                 reconcile=dict(min_retained_treated=6, min_retained_controls=5),
                 audit=dict(max_known_errors=2, max_unresolved=5, sample_size=10),
                 park_reasons=[
                     dict(id='S3_SUPPORT', gates=['S3', 'screen'], contains=['Registered support insufficient'], disposition='PARK', reopen='never'),
                     dict(id='RI_FEASIBILITY', gates=['ri', 'screen'], contains=['feasibility below 90%'], disposition='PARK', reopen='never'),
                     dict(id='S2_NO_FILES', gates=['S2', 'S0'], contains=['No local public files declared'], disposition='PARK', reopen='never'),
                     dict(id='PURPOSE_LEGACY', gates=['purpose', 'S0', 'S4'], contains=['requires new interpretation'], disposition='PARK',
                          reopen='on_new_rules')])
    value.update(changes)
    return value


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve()
        self.registry = self.repo / 'data/l2_pipeline/registry'
        self.pilot = Autopilot(self.registry, Backend(self.repo))
        patcher = patch.object(rules, 'REPO', self.repo)   # first_order_gate has no repo argument
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cards = self.repo / 'data/l2_pipeline/cards'
        self.cards.mkdir(parents=True)

    def publish(self, value=None):
        source = self.repo / 'rules_source.json'
        source.write_text(json.dumps(value or rules_value()))
        return rules.publish(source, 'test publish', self.repo)

    def pin(self, version='R1'):
        return rules.descriptor(version, self.repo)

    def make_row(self, design='d1', attempt='a1', pinned=True, mutate=None, version='v1'):
        card = gate0_fixture(self.cards)
        card.update(design_id=design, version=version)
        if pinned:
            card.setdefault('automation', {})['rules'] = self.pin()
        if mutate:
            mutate(card)
        path = self.cards / (design + '_' + version + '.json')
        atomic(path, card)
        row = dict(design_id=design, attempt_id=attempt, card=str(path), card_sha256=sha(path), route='V9',
                   work_dir=str(self.repo / 'data/l2_pipeline/work'))
        manifest = read(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[]))
        manifest['designs'] = [r for r in manifest['designs'] if r['design_id'] != design] + [row]
        atomic(self.registry / 'ACTIVE_DESIGNS.json', manifest)
        return row

    def done(self, row, *actions):
        for action in actions:
            ensure_once(self.pilot.checkpoint(row, action), dict(action=action))

    def ledger_rows(self):
        path = self.registry / 'AUTOPILOT_LEDGER.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def reconciliation(self, row, treated=10, never=10):
        """RECONCILIATION.json as the reconcile step leaves it: agreed rows only, first-full clock = operation year + 1."""
        root = self.pilot.backend.root(row)
        card = read(row['card'])
        units = card['treatment']['universe']
        rows = [dict(unit=u, status='treated', start_period=2006 + 3 * (i // 5), best_evidence_start_year=2005 + 3 * (i // 5),
                     mechanism='one', locator='table1') for i, u in enumerate(units[:treated])]
        rows += [dict(unit=u, status='never_treated', start_period=None, mechanism='one', locator='table1')
                 for u in units[treated:treated + never]]
        ensure_once(root / 'RECONCILIATION.json', dict(historical_comparison=dict(accepted=rows, gaps=[], counts={})))
        return rows


class RulesFileTests(Base):
    def test_1_rules_hash_binds_card_ingest_ctx_and_ledger(self):
        self.publish()
        row = self.make_row()
        self.assertEqual(self.pilot.backend.ctx(row).card.design_id, 'd1')            # right sha accepted
        wrong = self.make_row('d2', mutate=lambda c: c['automation']['rules'].update(sha256='0' * 64))
        with self.assertRaisesRegex(ValueError, 'rules pin differs'):
            self.pilot.backend.ctx(wrong)
        fake = SimpleNamespace(design_id='d3', payload=dict(automation=dict(rules=dict(self.pin(), sha256='1' * 64))))
        with patch('scripts.l2_pipeline.cards.load_card', return_value=fake), \
                patch('scripts.l2_pipeline.c42.completeness', return_value=[]), \
                patch('scripts.l2_pipeline.ops.ingest_gate.check', return_value=None):
            card_path = self.cards / 'x.json'
            card_path.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'rules pin differs'):
                ingest(self.cards, self.pilot, card_paths=[card_path])
        self.pilot.ledger(row, 'S0', 'PLAN', 'accept')
        self.pilot.ledger(self.make_row('r0', pinned=False), 'S0', 'PLAN', 'accept')
        by_design = {r['design_id']: r for r in self.ledger_rows() if r.get('decision') == 'PLAN'}
        self.assertEqual(by_design['d1']['rules_sha256'], self.pin()['sha256'])
        self.assertNotIn('rules_sha256', by_design['r0'])                              # R0 rows are unchanged
        bad = self.cards / 'badpin.json'
        card = read(row['card'])
        card['automation']['rules'] = dict(self.pin(), path='data/l2_pipeline/rules/elsewhere.json')
        self.assertTrue(any('automation.rules' in e for e in __import__('scripts.l2_pipeline.cards', fromlist=['x']).validate_card(card)))
        del bad

    def test_2_rules_publish_write_once_and_closed_schema(self):
        pointer = self.publish()
        before = (self.repo / pointer['path']).read_bytes()
        self.assertEqual(pointer['sha256'], sha(self.repo / pointer['path']))
        second = rules_value(version='R2', note='second', chain=[a for a in CHAIN if a != 'sealed'],      # a real R2 since window W2
                             reconcile=dict(min_retained_treated=6, min_retained_controls=5, count_audit_resolvable=True),
                             audit=dict(conflict_rows=True, conflict_cap=10, exclude_prior_sample=False),
                             forecast=dict(mode='bundle'), review=dict(mode='single'), direction=dict(third_coder=False))
        with patch.object(rules, 'REPO', self.repo):
            moved = self.publish(second)
            self.assertEqual(rules.current(self.repo)['version'], 'R2')
            self.assertNotEqual(moved['sha256'], pointer['sha256'])
            with self.assertRaisesRegex(ValueError, 'already exists'):
                self.publish(second)
        self.assertEqual((self.repo / pointer['path']).read_bytes(), before)           # R1 bytes untouched
        published = [r for r in self.ledger_rows() if r.get('decision') == 'RULES_PUBLISHED']
        self.assertEqual([r['version'] for r in published], ['R1', 'R2'])
        for bad, message in ((rules_value(extra=1), 'Unknown rules key'),
                             (rules_value(chain=['S0', 'nonsense']), 'known actions'),
                             (rules_value(chain=['S0', 'S0']), 'distinct'),
                             (rules_value(park_reasons=[dict(id='X', gates=['S3'], contains=[], disposition='PARK', reopen='never')]), 'non-empty text'),
                             (rules_value(park_reasons=[dict(id='X', gates=['S3'], contains=['a'], disposition='PARK', reopen='sometimes')]), 'reopen'),
                             (rules_value(version='R9'), 'not supported')):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                rules.validate(bad)
        shipped = Path(__file__).resolve().parents[3] / 'docs/plans/l2-pipeline/rules/RULES_R1.json'
        if shipped.exists():
            self.assertEqual(rules.validate(json.loads(shipped.read_text()))['version'], 'R1')
        self.assertEqual(rules.classify(rules_value(), 'S3', S3_REASON)['id'], 'S3_SUPPORT')
        self.assertIsNone(rules.classify(rules_value(), 'S3', 'something new'))
        self.assertIsNone(rules.classify(rules_value(), 'ri', S3_REASON))             # gate list is part of the match
        self.assertEqual(rules.classify(rules_value(), None, S3_REASON)['id'], 'S3_SUPPORT')   # a record with no action: text alone

    def test_3_chain_by_rules(self):
        self.publish()
        row = self.make_row()
        self.done(row, 'history', 'reconcile')
        self.assertEqual(self.pilot.next_action(row), 'screen')
        self.done(row, 'screen', 'audit', 'S0', 'S1', 'S2', 'S3', 'S4')
        self.assertEqual(self.pilot.next_action(row), 'ri')                            # purpose is not before ri
        self.done(row, 'ri')
        self.assertEqual(self.pilot.next_action(row), 'purpose')
        legacy = self.make_row('r0', pinned=False)
        self.done(legacy, 'history', 'reconcile')
        self.assertEqual(self.pilot.next_action(legacy), 'audit')                      # R0 order exactly as before
        self.done(legacy, 'audit')
        self.assertEqual(self.pilot.next_action(legacy), 'purpose')


class ScreenTests(Base):
    def setUp(self):
        super().setUp()
        for target, value in (('scripts.l2_pipeline.population_resolution.choose', dict(side_effect=ValueError('new construct'))),
                              ('scripts.l2_pipeline.prescreen.screen_ri', dict(return_value=dict(passed=True, checks={})))):
            patcher = patch(target, **value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.publish()

    def test_4_screen_parks_proven_failure_before_audit(self):
        row = self.make_row()
        self.done(row, 'history', 'reconcile')
        self.reconciliation(row, treated=3)                                            # min_treated is 6: fails even a complete rectangle
        with patch('scripts.l2_pipeline.prescreen.evaluate', wraps=__import__('scripts.l2_pipeline.prescreen', fromlist=['x']).evaluate) as ev:
            with self.assertRaises(Park) as caught:
                redesign.screen_action(self.pilot, row)
        self.assertEqual(str(caught.exception), S3_REASON)
        kwargs = ev.call_args.kwargs
        self.assertEqual((kwargs['shifts'], kwargs['pool_rule']), ((0,), False))
        self.assertEqual(sorted(r['status'] for r in kwargs['roster']).count('treated'), 3)
        self.assertEqual(redesign.class_of(self.pilot, row, 'screen', str(caught.exception))['id'], 'S3_SUPPORT')
        self.assertFalse((self.pilot.case_dir(row) / 'jobs' / 'audit.json').exists())
        self.assertEqual(self.pilot.pending, [])
        self.assertEqual(caught.exception.metrics['outcome_values_read'], 0)

    def test_5a_screen_inconclusive_proceeds(self):
        row = self.make_row()
        self.done(row, 'history', 'reconcile')
        self.reconciliation(row)                                                       # no sidecar: an optimistic rectangle passes
        value = redesign.screen_action(self.pilot, row)
        self.assertEqual(value['status'], 'PROCEED')
        self.assertTrue(value['variants'] and all(v['passed'] is None for v in value['variants']))
        self.assertEqual(value['rules_sha256'], self.pin()['sha256'])

    def test_5b_screen_skipped_when_audit_done(self):
        row = self.make_row()
        self.done(row, 'history', 'reconcile', 'audit')
        self.assertEqual(self.pilot.next_action(row), 'S0')
        other = self.make_row('d2', mutate=lambda c: None)
        self.done(other, 'history', 'reconcile')
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[dict(read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][1], route='V8b')]))
        self.assertEqual(self.pilot.next_action(dict(other, route='V8b')), 'audit')    # V8b has no screen


class GateAndLabelTests(Base):
    def first_order_card(self):
        from scripts.l2_pipeline.tests.test_first_order_gate import card
        value = card()
        value['first_order']['evidence_status'] = 'pending_policy_specific_verification'
        value['first_order'].pop('statute_refs')
        value['first_order'].pop('literature_refs')
        return value

    def test_6_first_order_gate_label_mode(self):
        from scripts.l2_pipeline.first_order import first_order_gate
        self.publish()
        plain = self.first_order_card()
        issues = ' | '.join(first_order_gate(plain)['issues'])
        self.assertIn('Statute-purpose/literature leads require policy-specific verification', issues)
        self.assertIn('V6.2 located statute purpose or at least two literature studies required', issues)
        labelled = deepcopy(plain)
        labelled['automation'] = dict(rules=self.pin())
        report = first_order_gate(labelled)
        self.assertTrue(report['passed'], report)
        # the third string (V6 coverage card without hub acceptance)
        v6 = deepcopy(plain)
        v6['automation'] = dict(coverage_window=dict(rule='coverage_bounded_windows_v6', parked=False))
        bounded = deepcopy(v6)
        with patch('scripts.l2_pipeline.coverage.coverage_bounded_card', return_value=bounded), \
                patch('scripts.l2_pipeline.first_order.target_mapping', return_value=dict(mapped=True, issues=[])), \
                patch('scripts.l2_pipeline.coverage.legal_window_issues', return_value=[]):
            self.assertIn('V6 purpose-verification job and explicit hub acceptance required', first_order_gate(v6)['issues'])
            both = deepcopy(v6)
            both['automation']['rules'] = self.pin()
            bounded_both = deepcopy(both)
            with patch('scripts.l2_pipeline.coverage.coverage_bounded_card', return_value=bounded_both):
                self.assertNotIn('V6 purpose-verification job and explicit hub acceptance required', first_order_gate(both)['issues'])
        # an outcome-registration issue stays in label mode
        broken = deepcopy(labelled)
        broken['first_order']['availability'] = 'maybe'
        self.assertIn('Declare first_order.availability', first_order_gate(broken)['issues'])

    def label_job(self, design, label_mode=True):
        contract = dict(card_sha256='c' * 64, internal_persona_lens='Card', population_catalog=dict(constructs=['x']), label_mode=label_mode)
        return SimpleNamespace(design_id=design, role='purpose_verifier', contract=contract)

    def test_7_purpose_label_never_stops(self):
        from scripts.l2_pipeline.agents.purpose import validate_purpose_output
        self.publish()
        weak = dict(design_id='w', role='purpose_verifier', card_sha256='c' * 64, internal_persona_lens='Card', status='UNRESOLVED',
                    target_outcome='x', target_population='y', confirmation='no marker', citations=[dict(source_ref='not a url')],
                    unresolved_reason='publisher blocked', tried=['openalex: no oa_url', 'nber: not there'])
        normalized = validate_purpose_output(self.label_job('w'), weak)
        self.assertEqual((normalized['status'], normalized['citations']), ('UNRESOLVED', []))
        with self.assertRaises(ValueError):                                              # the same answer is still a collector error without label mode
            validate_purpose_output(self.label_job('w', label_mode=False), weak)
        with self.assertRaisesRegex(ValueError, 'what was tried'):
            validate_purpose_output(self.label_job('w'), dict(weak, tried=[]))

        def case(design, collect=None, purpose=None):
            row = self.make_row(design)
            self.done(row, 'history', 'reconcile', 'screen', 'audit', 'S0', 'S1', 'S2', 'S3', 'S4', 'ri')
            ctx = self.pilot.backend.ctx(row)
            if purpose is not None:
                ensure_once(ctx.design_dir / 'PURPOSE_VERIFICATION.json', purpose)
            with patch.object(self.pilot.backend, 'jobs', return_value=['/job']), \
                    patch.object(self.pilot, 'collect_submissions', **(collect or dict(return_value=['/job']))):
                value = redesign.purpose_label_action(self.pilot, row)
            ensure_once(self.pilot.checkpoint(row, 'purpose'), value)
            self.assertEqual(self.pilot.next_action(row), 'purpose-direction')
            self.assertTrue((ctx.design_dir / 'PURPOSE_LABEL.json').exists())
            self.assertEqual(value['rules_sha256'], self.pin()['sha256'])
            return row, value
        _, value = case('u1', purpose=dict(status='UNRESOLVED', unresolved_reason='blocked', tried=['a']))
        self.assertEqual((value['label'], value['verifier_status']), ('unresolved', 'UNRESOLVED'))
        _, value = case('u2', collect=dict(side_effect=ValueError('§7 replacement failed; HUB_QUEUE: bad citation')))
        self.assertEqual((value['label'], value['verifier_status']), ('unresolved', 'REJECTED_AFTER_REPLACEMENT'))
        first = read(self.cards / 'd1_v1.json') if (self.cards / 'd1_v1.json').exists() else None
        del first
        row = self.make_row('u3')
        self.done(row, 'history', 'reconcile', 'screen', 'audit', 'S0', 'S1', 'S2', 'S3', 'S4', 'ri')
        payload = read(row['card'])
        target, population = payload['first_order']['target_outcome'], redesign._population(payload)
        _, value = case('v1', purpose=dict(status='VERIFIED', target_outcome=target, target_population=population))
        self.assertEqual((value['label'], value['matches_card_target'], value['matches_card_population']), ('target_matched', True, True))
        _, value = case('v2', purpose=dict(status='VERIFIED', target_outcome=target, target_population='all adults and children'))
        self.assertEqual((value['label'], value['matches_card_population']), ('broader_than_target', False))
        # a dead packet reaches the hub escalation: label, never a STOP
        dead = self.make_row('u4')
        self.done(dead, 'history', 'reconcile', 'screen', 'audit', 'S0', 'S1', 'S2', 'S3', 'S4', 'ri')
        sidecar = self.repo / 'data/l2_pipeline/purpose.job.json'
        sidecar.write_text(json.dumps(dict(contract=dict(label_mode=True))))
        from scripts.l2_pipeline.agents.purpose_direction import transport_failure
        with patch('scripts.l2_pipeline.agents.purpose_direction.is_direction', return_value=False):
            self.assertTrue(transport_failure(self.pilot, dead, str(self.repo / 'data/l2_pipeline/purpose.md'), 'lease expired three times'))
        self.assertEqual(read(self.pilot.backend.ctx(dead).design_dir / 'PURPOSE_LABEL.json')['verifier_status'], 'NO_RESULT')
        self.assertEqual(self.pilot.next_action(dead), 'purpose-direction')


class ParkTableTests(Base):
    def run_tick(self, reason):
        self.publish()
        pinned = self.make_row('p1')
        legacy = self.make_row('r0', pinned=False)
        self.done(pinned, 'history', 'reconcile', 'screen', 'audit')
        self.done(legacy, 'history', 'reconcile', 'audit', 'purpose', 'purpose-direction')
        with patch.object(Backend, 'perform', side_effect=ValueError(reason)), patch('scripts.l2_pipeline.autopilot.Autopilot.schedule'):
            self.pilot.tick(max_steps=1)
        return pinned, legacy

    def test_8_park_table_classifies(self):
        pinned, legacy = self.run_tick('No local public files declared')
        parked = read(self.pilot.case_dir(pinned) / 'PARKED.json')
        self.assertEqual((parked['class_id'], parked['reopen'], parked['rules_sha256']), ('S2_NO_FILES', 'never', self.pin()['sha256']))
        self.assertEqual(self.pilot.next_action(pinned), 'PARK')
        self.assertTrue(any(r['decision'] == 'PARK_CLASSIFIED' and r['design_id'] == 'p1' for r in self.ledger_rows()))
        queue = read(self.registry / 'HUB_QUEUE.json', dict(entries=[]))['entries']
        self.assertEqual([e['design_id'] for e in queue], ['r0'])                      # R0: STOP exactly as before, pinned: no hub entry
        self.assertTrue((self.pilot.case_dir(legacy) / 'STOP.json').exists())

    def test_8b_unknown_reason_is_a_stop(self):
        pinned, _ = self.run_tick('a reason nobody classified')
        self.assertTrue((self.pilot.case_dir(pinned) / 'STOP.json').exists())
        self.assertIn('p1', [e['design_id'] for e in read(self.registry / 'HUB_QUEUE.json')['entries']])


class RequeueAndReopenTests(Base):
    def setUp(self):
        super().setUp()
        self.publish()
        self.row = self.make_row('q1', pinned=False)
        self.pilot.ledger({}, 'loop', 'CLEAN_STOP', 'record')
        self.registry.joinpath('AUTOPILOT_STOP').touch()
        self.pilot.stop(self.row, 'purpose', LEGACY_REASON)
        self.done(self.row, 'history', 'reconcile', 'audit')
        root = self.pilot.backend.root(self.row)
        for name in ('RECONCILIATION.json', 'CODING_LOCK.json', 'AUDIT_SELECTION.json'):
            ensure_once(root / name, dict(fixture=name))
        self.authority = self.repo / 'docs/plans/l2-pipeline/HUB_RULING_20261017_REDESIGN.md'
        self.authority.parent.mkdir(parents=True)
        self.authority.write_text('fixture ruling')

    def new_card(self, **changes):
        card = read(self.row['card'])
        card.update(version='v2')
        card.setdefault('automation', {})['rules'] = self.pin()
        for key, value in changes.items():
            card[key] = value
        path = self.cards / 'q1_v2.json'
        atomic(path, card)
        ruling = self.repo / 'data/l2_pipeline/rulings/redesign.json'
        atomic(ruling, dict(schema='l2_requeue_ruling_v1', design_id='q1', prior_card_sha256=self.row['card_sha256'], card_sha256=sha(path),
                            decision='resume', superseded_status='SUPERSEDED_REDESIGN_R1', implementation_rule='REDESIGN_R1',
                            rules=self.pin(), reason='fixture', execution_authorized=False,
                            authority=dict(path=str(self.authority), sha256=sha(self.authority))))
        return path, ruling

    def test_9_requeue_redesign_r1(self):
        from scripts.l2_pipeline.requeue import requeue
        path, ruling = self.new_card(outcome_scale='count' if read(self.row['card']).get('outcome_scale') != 'count' else 'rate')
        with self.assertRaisesRegex(ValueError, 'only in automation.rules'):
            requeue('q1', path, ruling, self.pilot, scheduler_owned=True)
        path, ruling = self.new_card()
        old_ruling = ensure_once(self.pilot.backend.root(self.row) / 'PURPOSE_HUB_RULING_V62.json', dict(
            schema_version='6.2', purpose_rule='V6.2', decision='accept', design_id='q1', card_sha256=self.row['card_sha256'],
            execution_authorized=False, card_patch={}))
        self.done(self.row, 'purpose')
        ensure_once(self.pilot.checkpoint(self.row, 'purpose'), read(old_ruling)) if not self.pilot.checkpoint(self.row, 'purpose').exists() else None
        value = requeue('q1', path, ruling, self.pilot, scheduler_owned=True)
        self.assertEqual(value['restart_action'], 'S0')
        self.assertEqual(value['retained_actions'], ['history', 'reconcile', 'audit', 'purpose'])
        new_row = read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]
        self.assertEqual(self.pilot.next_action(new_row), 'S0')
        self.assertEqual(read(self.pilot.backend.root(new_row) / 'PURPOSE_LABEL.json')['label'], 'target_matched')
        self.assertEqual(read(self.registry / 'HUB_QUEUE.json')['entries'][0]['status'], 'SUPERSEDED_REDESIGN_R1')
        self.assertEqual(requeue('q1', path, ruling, self.pilot, scheduler_owned=True), value)   # second call: same receipt
        self.assertEqual(rules.pinned(read(new_row['card']))['version'], 'R1')

    def test_9b_class_that_never_reopens_is_refused(self):
        from scripts.l2_pipeline.requeue import requeue
        other = self.make_row('q2', pinned=False)
        self.pilot.stop(other, 'S3', S3_REASON)
        self.done(other, 'history', 'reconcile', 'audit')
        card = read(other['card'])
        card.update(version='v2')
        card.setdefault('automation', {})['rules'] = self.pin()
        path = self.cards / 'q2_v2.json'
        atomic(path, card)
        ruling = self.repo / 'data/l2_pipeline/rulings/redesign2.json'
        atomic(ruling, dict(schema='l2_requeue_ruling_v1', design_id='q2', prior_card_sha256=other['card_sha256'], card_sha256=sha(path),
                            decision='resume', superseded_status='SUPERSEDED_REDESIGN_R1', implementation_rule='REDESIGN_R1',
                            rules=self.pin(), reason='fixture', execution_authorized=False,
                            authority=dict(path=str(self.authority), sha256=sha(self.authority))))
        with self.assertRaisesRegex(ValueError, 'not reopenable'):
            requeue('q2', path, ruling, self.pilot, scheduler_owned=True)

    def test_10_reopen_request_once_per_version(self):
        frozen = self.make_row('fz', pinned=False)
        self.pilot.stop(frozen, 'purpose', LEGACY_REASON)
        self.done(frozen, 'history', 'reconcile', 'audit')
        ensure_once(self.pilot.backend.root(frozen) / 'FREEZE_PREP.json', dict(x=1))
        forecast = self.make_row('fc', pinned=False)
        self.pilot.stop(forecast, 'S4', 'ValueError: Purpose citation must identify supported fields')
        self.done(forecast, 'history', 'reconcile', 'audit')
        ensure_once(self.pilot.case_dir(forecast) / 'jobs/forecasts.json', dict(jobs=[]))
        never = self.make_row('nv', pinned=False)
        self.pilot.stop(never, 'S3', S3_REASON)
        plan = {item['design_id']: item for item in redesign.plan(self.pilot, 'R1')}
        self.assertEqual({k for k, v in plan.items() if v['eligible']}, {'q1'})
        self.assertIn('frozen', plan['fz']['why_not'])
        self.assertIn('forecast inventory', plan['fc']['why_not'])
        self.assertIn('reopen=never', plan['nv']['why_not'])
        redesign.classify_all(self.pilot, 'R1')
        self.assertEqual(len(list((self.pilot.registry.parent / 'autopilot/redesign_r1/classes').glob('*.json'))), 4)
        atomic(self.registry / redesign.REQUEST, dict(scope='all', rules_version='R1'))
        with patch.object(Backend, 'perform', return_value=dict(ok=1)), patch('scripts.l2_pipeline.autopilot.Autopilot.schedule'):
            self.pilot.tick(max_steps=1)
            receipts = list((self.pilot.registry.parent / 'autopilot/requeues/q1').glob('*/REQUEUE.json'))
            self.assertEqual(len(receipts), 1)
            self.assertEqual(read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]['attempt_id'], 'requeue_v2')
            before = self.ledger_rows()
            atomic(self.registry / redesign.REQUEST, dict(scope='all', rules_version='R1'))   # a second request for the same version
            self.pilot.tick(max_steps=1)
        self.assertEqual(len(list((self.pilot.registry.parent / 'autopilot/requeues/q1').glob('*/REQUEUE.json'))), 1)
        self.assertEqual(len(list((self.pilot.registry.parent / 'autopilot/redesign_r1/requests').glob('*.json'))), 1)
        self.assertEqual([r for r in self.ledger_rows() if r.get('decision') == 'SUPERSEDED_REDESIGN_R1'], [r for r in before if r.get('decision') == 'SUPERSEDED_REDESIGN_R1'])


class FreezeAndLimitsTests(Base):
    def test_11_lock_freezes_rules_and_label(self):
        from scripts.l2_pipeline import c64, registration
        self.publish()
        row = self.make_row('f1')
        ctx = self.pilot.backend.ctx(row)
        path = registration._snapshot_rules(ctx)
        self.assertEqual(sha(path), self.pin()['sha256'])                               # exact bytes of the pinned rules file
        self.assertEqual(registration._snapshot_rules(ctx), path)                       # idempotent
        self.assertIsNone(registration._snapshot_rules(self.pilot.backend.ctx(self.make_row('f0', pinned=False))))
        ensure_once(ctx.design_dir / 'PURPOSE_LABEL.json', dict(label='broader_than_target'))
        ensure_once(ctx.design_dir / 'PREDICTIONS_LOCK.json', dict(files={}, original_files={}))
        roles = registration._inventory(ctx)
        self.assertEqual((roles['RULES_SNAPSHOT.json'], roles['PURPOSE_LABEL.json']), ('verification', 'verification'))
        with patch('scripts.l2_pipeline.c64_metadata.export_contract', return_value=dict(geography={})), \
                patch('scripts.l2_pipeline.c64_metadata.geography', return_value={}):
            self.assertEqual(c64.contract(dict(ctx.card.payload), repo=self.repo, root=ctx.design_dir)['purpose_label'], 'broader_than_target')
            r0 = self.pilot.backend.ctx(self.make_row('f2', pinned=False))
            self.assertEqual(c64.contract(dict(r0.card.payload), repo=self.repo, root=r0.design_dir)['purpose_label'], 'not_applicable_r0')

    def test_12_v3_limits_default_unchanged(self):
        from scripts.l2_pipeline.stages.s1_treatment import evaluate_v3
        sample = ['U%d' % i for i in range(10)]

        def checks(errors, unresolved):
            out = []
            for i, unit in enumerate(sample):
                status = 'known_error' if i < errors else 'unresolved' if i < errors + unresolved else 'match'
                out.append(dict(unit=unit, status=status, official_locator='p1', **({'corrected_start_period': 2000} if status == 'known_error' else {})))
            return out
        self.assertTrue(evaluate_v3(sample, checks(2, 5))['passed'])                     # today's 2 / 5
        self.assertFalse(evaluate_v3(sample, checks(3, 0))['passed'])
        self.assertFalse(evaluate_v3(sample, checks(0, 6))['passed'])
        self.assertEqual(evaluate_v3(sample, checks(2, 5)), evaluate_v3(sample, checks(2, 5), limits=None))
        wide = dict(max_known_errors=3, max_unresolved=5)
        self.assertTrue(evaluate_v3(sample, checks(3, 0), limits=wide)['passed'])
        self.assertEqual(rules.audit_limits(dict(automation=dict(rules=self.publish() and self.pin())), self.repo), dict(max_known_errors=2, max_unresolved=5))
        self.assertIsNone(rules.audit_limits({}, self.repo))


if __name__ == '__main__':
    unittest.main()
