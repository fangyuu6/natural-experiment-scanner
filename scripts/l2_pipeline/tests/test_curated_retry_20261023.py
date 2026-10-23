"""Q2 (HUB_RULING_20261023_PARK_REOPEN section 4): one extra blind-audit round for the curated-audit designs that parked inconclusive.
Synthetic repo and synthetic worker records only; no outcome, estimate or forecast value exists in any fixture."""
import json
import re
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline import curated_history as ch, curated_route as cr, final_decisions
from scripts.l2_pipeline.autopilot import atomic, read
from scripts.l2_pipeline.c42 import Park, RULE as C42_RULE
from scripts.l2_pipeline.requeue import requeue
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests.test_curated_route_20261021 import Fixture


def tree(root):
    return {str(p): sha(p) for p in sorted(Path(root).rglob('*')) if p.is_file()}


class RetryBase(Fixture):
    """An active curated design whose first audit (attempt 'old') parked CURATED_AUDIT_INCONCLUSIVE, plus the ruling and the retry card."""

    def setUp(self):
        super().setUp()
        self.design = self.card['design_id']
        for name, value in (('RETRY_DESIGNS', frozenset({self.design, 'other_a', 'other_b', 'other_c'})),):
            p = patch.object(cr, name, value)
            p.start()
            self.addCleanup(p.stop)
        case = self.pilot.case_dir(self.row)
        (case / 'STOP.json').unlink()
        for action in ('history', 'reconcile', 'audit'):
            self.pilot.checkpoint(self.row, action).unlink()
        self.row, self.payload = self.declare()
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[self.row]))
        for p in self.fake_xlsx():
            p.start()
            self.addCleanup(p.stop)
        (self.repo / C42_RULE).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / C42_RULE).write_text('synthetic C42 rule')
        self.ruling_file = self.repo / cr.RETRY_RULING
        self.ruling_file.parent.mkdir(parents=True, exist_ok=True)
        self.ruling_file.write_text('synthetic park-reopen ruling')
        self.make_first_audit()
        self.new_path = Path(self.row['card']).with_name('curated_retry.json')
        self.new_card = dict(deepcopy(self.payload), version='v10')
        atomic(self.new_path, self.new_card)

    # -- first audit: every sample state unreadable, replacements unreadable too -> inconclusive, as the four real designs did
    def make_first_audit(self):
        build = cr.prepare(self.pilot, self.row)
        self.first_build = build
        sample = build['plan']['sample']
        ensure_once(cr.round_dir(self.pilot, self.row) / 'r1.json', dict(tag='r1', units=sample, jobs=['/old/A.cur_r1.md', '/old/B.cur_r1.md']))
        self.drive(self.row, lambda jobs, units: {u: None for u in units})
        with self.assertRaises(Park) as caught:
            self.drive(self.row, lambda jobs, units: {u: None for u in units})
        self.first_park = caught.exception
        self.assertIn(cr.INCONCLUSIVE, str(caught.exception))
        ensure_once(self.pilot.case_dir(self.row) / 'PARKED.json', dict(
            design_id=self.design, attempt_id=self.row['attempt_id'], action='reconcile', status='PARKED',
            reason=str(caught.exception), metrics=caught.exception.metrics))

    def drive(self, row, records_for):
        def emit(path, **kw):
            return type('J', (), {'job_path': '/tmp/%s.%s.md' % (Path(path).stem, kw['attempt_id'])})()
        with patch.object(self.pilot.backend, 'jobs', return_value=['/x/A.md', '/x/B.md']), \
                patch('scripts.l2_pipeline.agents.history.emit_history_job', side_effect=emit), \
                patch.object(cr, 'collect_round', return_value=(True, [])), \
                patch.object(cr, 'fresh_records', side_effect=records_for):
            return cr.reconcile(self.pilot, row)

    def truth(self, units):
        db = {r['unit']: r for r in self.first_build['history']['accepted']}
        return {u: dict(unit=u, status=db[u]['status'], start_period=db[u]['start_period'],
                        best_evidence_start_year=db[u]['best_evidence_start_year'], locator='official',
                        **({'pre_window_bound': True} if db[u].get('pre_window_bound') else {})) for u in units}

    # -- ruling, receipt, requeue
    def receipt(self, version='v10'):
        return cr.build_receipt(self.pilot, self.row, version)

    def make_ruling(self, card=None, **changes):
        card = card or self.new_path
        value = dict(schema='l2_requeue_ruling_v1', design_id=self.design, prior_card_sha256=sha(self.row['card']), card_sha256=sha(card),
                     decision='resume', superseded_status=cr.STATUS, curated_retry_rule='Q2', execution_authorized=False,
                     authority=dict(path=str(self.ruling_file.resolve()), sha256=sha(self.ruling_file)), reason='Q2 test')
        value.update(changes)
        path = self.repo / 'data/l2_pipeline/rulings/q2.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def log_receipt(self, version='v10'):
        return ensure_once(cr.retry_path(self.repo, self.design), self.receipt(version))

    def go(self, **changes):
        self.log_receipt()
        return requeue(self.design, self.new_path, self.make_ruling(**changes), self.pilot)

    def retry_row(self):
        return read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]


class ReceiptAndRequeueTests(RetryBase):
    def test_receipt_binds_the_first_audit_and_is_written_once(self):
        value = self.receipt()
        first = value['first_audit']
        self.assertEqual((first['xlsx_sha256'], first['mapping_sha256']), (self.first_build['xlsx_sha256'], ch.mapping_sha(self.spec)))
        self.assertEqual((first['seed'], first['sample'], first['plan_sha256']),
                         (self.first_build['plan']['seed'], self.first_build['plan']['sample'], ch.digest(self.first_build['plan'])))
        self.assertEqual((value['rounds_allowed'], value['retry_attempt_id'], value['tag_suffix']), (1, 'requeue_v10', 'b'))
        self.assertEqual(set(first['round_files']), {'r1.json', 'r2.json'})
        path = self.log_receipt()
        self.assertEqual(ensure_once(path, value), path)                      # same bytes: fine
        with self.assertRaises(ValueError):
            ensure_once(path, self.receipt('v11'))                            # a different second receipt can never be written

    def test_requeue_makes_one_new_attempt_at_history_and_leaves_the_first_audit_untouched(self):
        before = tree(self.repo / 'data/l2_pipeline/autopilot')
        first_work = tree(self.pilot.backend.root(self.row))
        value = self.go()
        self.assertEqual((value['restart_action'], value['retained_actions'], value['curated_retry']), ('history', [], True))
        row = self.retry_row()
        self.assertEqual((row['attempt_id'], self.pilot.next_action(row)), ('requeue_v10', 'history'))
        after = tree(self.repo / 'data/l2_pipeline/autopilot')
        for path, digest in before.items():
            if path.endswith('ACTIVE_DESIGNS.json'):
                continue
            self.assertEqual(after.get(path), digest, path)                    # r1/r2 files, verdict, PARKED.json of the first attempt: byte-identical
        self.assertEqual(first_work, tree(self.pilot.backend.root(self.row)))
        self.assertEqual(cr.retry_receipt(self.repo, row)['retry_attempt_id'], 'requeue_v10')
        self.assertIsNone(cr.retry_receipt(self.repo, self.row))               # the first attempt is not a retry attempt
        self.assertEqual(self.go(), value)                                     # idempotent

    def test_receipt_is_required_before_the_requeue(self):
        with self.assertRaisesRegex(ValueError, 'receipt logged'):
            requeue(self.design, self.new_path, self.make_ruling(), self.pilot)
        self.assertEqual(self.retry_row(), self.row)

    def test_second_extra_round_is_refused(self):
        self.go()
        row = self.retry_row()
        # the retry attempt ends inconclusive too (closed): a further requeue request, with any card, is refused
        ensure_once(self.pilot.case_dir(row) / 'PARKED.json', dict(design_id=self.design, attempt_id=row['attempt_id'], action='reconcile',
                    status='PARKED', reason=cr.INCONCLUSIVE + ': x', metrics=dict(kind=cr.PARK_KIND, route='not_evaluable')))
        third = Path(row['card']).with_name('curated_third.json')
        atomic(third, dict(deepcopy(self.new_card), version='v11'))
        with self.assertRaisesRegex(ValueError, 'extra round is spent'):
            requeue(self.design, third, self.make_ruling(card=third, prior_card_sha256=sha(row['card'])), self.pilot)
        with self.assertRaises(ValueError):
            ensure_once(cr.retry_path(self.repo, self.design), self.receipt('v11'))      # and no second receipt can be logged
        self.assertEqual(self.retry_row(), row)

    def test_only_the_named_attempt_and_version(self):
        self.log_receipt()
        other = Path(self.row['card']).with_name('curated_v12.json')
        atomic(other, dict(deepcopy(self.new_card), version='v12'))
        with self.assertRaisesRegex(ValueError, 'exactly one extra round'):
            requeue(self.design, other, self.make_ruling(card=other), self.pilot)

    def test_ruling_must_be_exact(self):
        self.log_receipt()
        for changes, message in ((dict(decision='accept'), 'malformed'), (dict(execution_authorized=True), 'malformed'),
                                 (dict(superseded_status='SUPERSEDED_C46'), 'malformed'), (dict(curated_retry_rule='Q3'), 'malformed'),
                                 (dict(authority=dict(path=str(self.repo / 'other.md'), sha256=sha(self.ruling_file))), 'authority')):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                requeue(self.design, self.new_path, self.make_ruling(**changes), self.pilot)
        with self.assertRaisesRegex(ValueError, '(?i)fresh history'):
            requeue(self.design, self.new_path, self.make_ruling(fresh_history_rule='R5'), self.pilot)
        self.assertEqual(self.retry_row(), self.row)

    def test_card_may_differ_only_in_version_no_mapping_change(self):
        self.log_receipt()
        for name, change in (('treatment', lambda c: c['treatment'].update(excluded_units=['S01'])),
                             ('mapping', lambda c: c['automation']['history_source'].update(mapping_sha256='1' * 64)),
                             ('analysis', lambda c: c['analysis'].update(min_controls=3))):
            card = deepcopy(self.new_card)
            change(card)
            path = Path(self.row['card']).with_name('curated_bad_%s.json' % name)
            atomic(path, card)
            with self.subTest(name), self.assertRaises(ValueError):
                requeue(self.design, path, self.make_ruling(card=path), self.pilot)
        self.assertEqual(self.retry_row(), self.row)

    def test_only_an_inconclusive_curated_park_qualifies(self):
        self.log_receipt()
        parked = self.pilot.case_dir(self.row) / 'PARKED.json'
        value = read(parked)
        for bad in (dict(value, reason='CURATED_AUDIT_FAIL: x', metrics=dict(value['metrics'], route='fresh_coding')),
                    dict(value, action='history'), dict(value, metrics=dict(value['metrics'], kind='OTHER'))):
            parked.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError, 'CURATED_AUDIT_INCONCLUSIVE'):
                requeue(self.design, self.new_path, self.make_ruling(), self.pilot)
        parked.write_text(json.dumps(value))
        (self.pilot.case_dir(self.row) / 'PARKED_C83_FINAL.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'finally parked'):
            requeue(self.design, self.new_path, self.make_ruling(), self.pilot)

    def test_design_outside_the_four_is_refused(self):
        self.log_receipt()
        with patch.object(cr, 'RETRY_DESIGNS', frozenset({'other_a', 'other_b', 'other_c', 'other_d'})):
            with self.assertRaisesRegex(ValueError, 'outside the ruling list'):
                requeue(self.design, self.new_path, self.make_ruling(), self.pilot)

    def test_the_four_names_are_frozen(self):
        real = {'b1020rn_campus_ccw_ban_violent_crime', 'b1020rn_dv_firearm_removal_at_scene_firearm_homicide',
                'b1020rn_mental_health_commitment_prohibitor_firearm_homicide', 'b1020rn_straw_purchase_law_firearm_homicide'}
        patched = cr.RETRY_DESIGNS
        src = Path(cr.__file__).read_text()
        block = src[src.index('RETRY_DESIGNS = frozenset({'):src.index('SWITCH_DESIGNS = (')]
        self.assertEqual(set(re.findall(r"'(b1020rn_[a-z0-9_]+)'", block)), real)
        self.assertEqual((cr.RETRY_RULE, cr.RETRY_SUFFIX), ('Q2', 'b'))
        self.assertIsInstance(patched, frozenset)

    def test_changed_first_audit_since_logging_is_refused(self):
        self.log_receipt()
        (cr.round_dir(self.pilot, self.row) / 'r3.json').write_text(json.dumps(dict(tag='r3', units=[], jobs=[])))
        with self.assertRaisesRegex(ValueError, 'first audit changed'):
            requeue(self.design, self.new_path, self.make_ruling(), self.pilot)


class SameAuditTests(RetryBase):
    def retry_prepare(self):
        self.go()
        return cr.prepare(self.pilot, self.retry_row())

    def test_retry_attempt_reproduces_the_same_audit_without_a_new_claim(self):
        ledger_dir = self.repo / cr.LEDGER_DIR
        ledger_before = {p.name: p.read_bytes() for p in ledger_dir.glob('*.json')}
        build = self.retry_prepare()
        for key in ('xlsx_sha256', 'mapping_sha256'):
            self.assertEqual(build[key], self.first_build[key])
        for key in ('seed', 'sample', 'backup_order', 'strata', 'random_part'):
            self.assertEqual(build['plan'][key], self.first_build['plan'][key])
        self.assertEqual(ch.digest(build['plan']), ch.digest(self.first_build['plan']))
        self.assertEqual(ledger_before, {p.name: p.read_bytes() for p in ledger_dir.glob('*.json')})      # first audit's claim and result untouched

    def test_a_non_retry_second_attempt_still_cannot_draw_again(self):
        # the same prepare() on any attempt without a receipt keeps the one-audit-per-key rule
        card = dict(deepcopy(self.payload), version='v55')
        path = Path(self.row['card']).with_name('curated_v55.json')
        atomic(path, card)
        row2 = dict(self.row, attempt_id='requeue_v55', card=str(path), card_sha256=sha(path))
        self.assertIsNone(cr.retry_receipt(self.repo, row2))
        with self.assertRaisesRegex(ValueError, 'already claimed'):
            cr.prepare(self.pilot, row2)

    def test_changed_sample_seed_or_hash_is_refused_in_the_retry_attempt(self):
        self.go()
        row = self.retry_row()
        plan = deepcopy(self.first_build['plan'])
        for field, value in (('sample', plan['sample'][:-1] + ['ZZ']), ('seed', plan['seed'] + 1), ('backup_order', plan['backup_order'][::-1] + ['ZZ'])):
            with self.subTest(field), patch.object(ch, 'audit_plan', return_value=dict(plan, **{field: value})):
                with self.assertRaisesRegex(ValueError, 'CURATED_RETRY_MISMATCH'):
                    cr.prepare(self.pilot, row)
                self.assertFalse((self.pilot.backend.root(row) / 'CURATED_BUILD.json').exists())
        with patch.object(ch, 'file_sha', return_value='f' * 64):          # a different database file is not the pinned workbook
            with self.assertRaises(Exception):
                cr.prepare(self.pilot, row)

    def test_changed_database_sha_is_refused_at_the_requeue(self):
        self.log_receipt()
        with patch.object(ch, 'file_sha', return_value='f' * 64):
            with self.assertRaisesRegex(ValueError, 'pinned version'):
                requeue(self.design, self.new_path, self.make_ruling(), self.pilot)

    def test_changed_mapping_is_refused_at_the_requeue(self):
        self.log_receipt()
        spec = deepcopy(self.spec)
        spec['classes'] = list(spec.get('classes', []))[:-1] or spec.get('classes')
        spec['notes'] = 'edited after the first audit'
        self.write_spec(spec)
        with self.assertRaises(ValueError):                                # card declaration hash no longer matches the file
            requeue(self.design, self.new_path, self.make_ruling(), self.pilot)


class RoundTests(RetryBase):
    def start(self, row, ledger_seen=None):
        def emit(path, **kw):
            if ledger_seen is not None:
                ledger_seen.append(any(json.loads(s).get('decision') == 'CURATED_RETRY_ROUND_LOGGED'
                                       for s in (self.registry / 'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()))
            return type('J', (), {'job_path': '/tmp/%s.%s.md' % (Path(path).stem, kw['attempt_id'])})()
        with patch.object(self.pilot.backend, 'jobs', return_value=['/x/A.md', '/x/B.md']), \
                patch('scripts.l2_pipeline.agents.history.emit_history_job', side_effect=emit), \
                patch.object(cr, 'collect_round', return_value=(False, [])):
            return cr.start(self.pilot, row)

    def test_retry_round_has_its_own_tag_fresh_packets_and_is_logged_before_they_exist(self):
        self.go()
        row = self.retry_row()
        seen = []
        self.assertIsNone(self.start(row, seen))
        inventory = read(cr.round_dir(self.pilot, row) / 'r1b.json')
        self.assertEqual((inventory['tag'], inventory['units']), ('r1b', self.first_build['plan']['sample']))
        self.assertTrue(seen and all(seen))                                 # the ledger row exists when the first packet is emitted
        self.assertTrue(all('cur_r1b' in j and 'cur_r1.' not in j for j in inventory['jobs']))
        self.assertFalse((cr.round_dir(self.pilot, row) / 'r1.json').exists())
        first = read(cr.round_dir(self.pilot, self.row) / 'r1.json')
        self.assertFalse(set(first['jobs']) & set(inventory['jobs']))
        rows = [json.loads(s) for s in (self.registry / 'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()]
        logged = [r for r in rows if r.get('decision') == 'CURATED_RETRY_ROUND_LOGGED']
        self.assertEqual(len(logged), 1)
        self.start(row)
        self.assertEqual(len([r for r in (json.loads(s) for s in (self.registry / 'AUTOPILOT_LEDGER.jsonl').read_text().splitlines())
                              if r.get('decision') == 'CURATED_RETRY_ROUND_LOGGED']), 1)    # not repeated every tick

    def test_tags_cannot_cross_attempts(self):
        self.go()
        row = self.retry_row()
        sample = self.first_build['plan']['sample']
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            cr.round_jobs(self.pilot, row, 'r1', sample)
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            cr.round_jobs(self.pilot, self.row, 'r1b', sample)

    def test_retry_packet_reusing_a_first_audit_packet_is_refused(self):
        self.go()
        row = self.retry_row()
        reused = read(cr.round_dir(self.pilot, self.row) / 'r1.json')['jobs']
        with patch.object(cr, 'round_jobs', return_value=dict(tag='r1b', units=[], jobs=reused)), patch.object(cr, 'collect_round', return_value=(True, [])):
            with self.assertRaisesRegex(ValueError, 'reuses a first-audit packet'):
                cr.start(self.pilot, row)

    def test_retry_is_evaluated_on_its_own_rounds_alone_and_can_pass(self):
        self.go()
        row = self.retry_row()
        cr.prepare(self.pilot, row)
        sample = self.first_build['plan']['sample']
        ensure_once(cr.round_dir(self.pilot, row) / 'r1b.json', dict(tag='r1b', units=sample, jobs=['/new/A.cur_r1b.md', '/new/B.cur_r1b.md']))
        seen_jobs = []

        def records(jobs, units):
            seen_jobs.append(list(jobs))
            return self.truth(units)
        first_files = tree(cr.round_dir(self.pilot, self.row))
        value = self.drive(row, records)
        self.assertEqual(value['verdict'], 'pass')
        self.assertEqual(seen_jobs, [['/new/A.cur_r1b.md', '/new/B.cur_r1b.md']])         # only the retry round's packets were read
        verdict = read(self.pilot.backend.root(row) / 'CURATED_VERDICT.json')
        self.assertTrue(verdict['verdict']['passed'])
        self.assertEqual({c['unit'] for c in verdict['checks']}, set(sample))
        self.assertEqual(first_files, tree(cr.round_dir(self.pilot, self.row)))            # r1 and r2 of the first attempt: byte-identical
        result = read(cr.retry_result_path(self.repo, self.design))
        self.assertEqual((result['verdict'], result['passed'], result['closed_for_good']), ('pass', True, False))
        ledger = json.loads(next((self.repo / cr.LEDGER_DIR).glob('*.json')).read_text())
        self.assertEqual([a['result'] for a in ledger['audits']], ['inconclusive'])         # the first audit's recorded result is not overwritten

    def test_second_inconclusive_closes_for_good(self):
        self.go()
        row = self.retry_row()
        cr.prepare(self.pilot, row)
        sample = self.first_build['plan']['sample']
        ensure_once(cr.round_dir(self.pilot, row) / 'r1b.json', dict(tag='r1b', units=sample, jobs=['/new/A.cur_r1b.md', '/new/B.cur_r1b.md']))
        none = lambda jobs, units: {u: None for u in units}
        self.assertIsNone(self.drive(row, none))                                           # replacement round asked for, tagged r2b
        self.assertTrue((cr.round_dir(self.pilot, row) / 'r2b.json').is_file())
        self.assertFalse((cr.round_dir(self.pilot, row) / 'r2.json').exists())
        with self.assertRaises(Park) as caught:
            self.drive(row, none)
        exc = caught.exception
        self.assertIn(cr.INCONCLUSIVE, str(exc))
        self.assertIn('closed for good', str(exc))
        self.assertEqual((exc.metrics['route'], exc.metrics['final']), ('not_evaluable', True))   # autopilot writes PARKED_C83_FINAL.json for final=True
        result = read(cr.retry_result_path(self.repo, self.design))
        self.assertEqual((result['verdict'], result['closed_for_good']), ('inconclusive', True))
        self.assertIn("exc.metrics.get('final')", Path(cr.__file__).with_name('autopilot.py').read_text())
        # a requeue after that is refused, and the closure row is a valid FINAL_DECISIONS row of an approved gate
        ensure_once(self.pilot.case_dir(row) / 'PARKED_C83_FINAL.json', dict(design_id=self.design, attempt_id=row['attempt_id'], action='reconcile',
                    status='PARKED', reason=str(exc), metrics=exc.metrics))
        entry = cr.closure_row(self.pilot, row)
        final_decisions.validate(entry)
        self.assertEqual((entry['gate'], entry['attempt'], entry['decision'], entry['state_at_closure']),
                         ('HISTORY_INSUFFICIENT', 'requeue_v10', 'NOT_EVALUABLE', 'PARK'))
        self.assertIsNone(cr.closure_row(self.pilot, self.row))                              # the first attempt has no closure row
        third = Path(row['card']).with_name('curated_third.json')
        atomic(third, dict(deepcopy(self.new_card), version='v11'))
        with self.assertRaises(ValueError):
            requeue(self.design, third, self.make_ruling(card=third, prior_card_sha256=sha(row['card'])), self.pilot)

    def test_a_failed_retry_is_not_closed_by_this_rule(self):
        # a real error found in the retry keeps the existing route (fresh coding); only the second INCONCLUSIVE is final
        self.go()
        row = self.retry_row()
        cr.prepare(self.pilot, row)
        sample = self.first_build['plan']['sample']
        ensure_once(cr.round_dir(self.pilot, row) / 'r1b.json', dict(tag='r1b', units=sample, jobs=['j1', 'j2']))
        db = {r['unit']: r for r in self.first_build['history']['accepted']}
        never = next(u for u in sample if db[u]['status'] == 'never_treated')

        def records(jobs, units):
            out = self.truth(units)
            out[never] = dict(unit=never, status='treated', start_period=2011, best_evidence_start_year=2010, locator='x')
            return out
        with self.assertRaises(Park) as caught:
            self.drive(row, records)
        self.assertEqual(caught.exception.metrics['route'], 'fresh_coding')
        self.assertNotIn('final', caught.exception.metrics)
        self.assertFalse(read(cr.retry_result_path(self.repo, self.design))['closed_for_good'])

    def test_pass_rule_and_thresholds_unchanged(self):
        self.assertEqual((ch.MAX_UNRESOLVED, ch.EXTENSION, ch.SAMPLE_FLOOR, ch.SAMPLE_SHARE, ch.MAX_AMBIGUOUS_SHARE), (5, 5, 10, 0.25, 0.20))
        src = Path(ch.__file__).read_text()
        self.assertIn('if unverifiable_share > 1 / 3:', src)
        self.assertIn("return dict(out, passed=False, verdict='inconclusive', route='not_evaluable')", src)
        plan = self.first_build['plan']
        checks = [dict(unit=u, status='unverified', official_locator=None, phase='sample') for u in plan['sample']]
        verdict = ch.evaluate(plan, checks)
        self.assertEqual((verdict['verdict'], verdict['route'], verdict['unverifiable_share']), ('inconclusive', 'not_evaluable', 1.0))


class SharedPackTests(RetryBase):
    def write_pack(self, entries):
        path = self.repo / cr.SONNET_QUEUE / 'SHARED_PACK' / 'INDEX.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(schema='l2_shared_pack_v1', entries=entries)))

    def page(self, n, designs):
        return dict(url='https://p%d' % n, sha256=str(n) * 64, units=['AL'], designs=designs)

    def test_snapshot_is_in_the_receipt_and_pages_in_it_are_fixed_in_advance(self):
        self.write_pack([self.page(1, [self.design]), self.page(2, [])])       # tagged to one design: served by url to every packet anyway
        value = self.receipt()
        self.assertEqual(value['shared_pack']['entries_sha256'], cr.shared_pack_state(self.repo)['entries_sha256'])
        self.assertEqual(len(value['shared_pack']['entries']), 2)
        self.assertEqual(cr.pages_not_offered_to_all(value['shared_pack'], cr.shared_pack_state(self.repo)), [])

    def test_a_page_added_after_the_receipt_must_be_tagged_to_all_four_or_to_none(self):
        everyone = sorted(cr.RETRY_DESIGNS)
        self.write_pack([self.page(1, [])])
        self.go()
        row = self.retry_row()
        receipt = cr.retry_receipt(self.repo, row)
        self.write_pack([self.page(1, []), self.page(2, everyone), self.page(3, []), self.page(4, ['unrelated'])])
        cr.log_retry_round(self.pilot, row, receipt)                           # all four / none / unrelated: fine
        self.write_pack([self.page(1, []), self.page(5, [self.design])])
        with self.assertRaisesRegex(ValueError, 'some but not all four'):
            cr.log_retry_round(self.pilot, row, receipt)

    def test_no_pack_at_all_is_fine(self):
        self.assertEqual(cr.shared_pack_state(self.repo)['entries'], [])
        self.go()


if __name__ == '__main__':
    unittest.main()
