"""T219 (HUB_RULING_20261021_CURATED_HISTORY): autopilot hook of the curated route and the per-design mappings. Synthetic data
for the hook; the mapping tests read the RAND workbook (history rows only) and the cards, never an outcome."""
from copy import deepcopy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline import curated_history as ch, curated_route as cr
from scripts.l2_pipeline.autopilot import atomic, read
from scripts.l2_pipeline.c42 import Park, RULE as C42_RULE
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests.test_c46 import RequeueTests
from scripts.l2_pipeline.tests import test_curated_history_20261021 as base

REPO = Path(__file__).resolve().parents[3]
DRAFTS = REPO / 'data/l2_pipeline/curated/mappings_draft'
CARDS = REPO / 'data/l2_pipeline/intake/bulk_20261020/ready_now/cards'
XLSX = REPO / cr.XLSX
FROZEN = dict(state='frozen', mapper_a=dict(id='ma', file_sha256='a' * 64, signed_utc='2026-10-21T00:00:00Z'),
              mapper_b=dict(id='mb', file_sha256='b' * 64, signed_utc='2026-10-21T00:00:00Z'),
              third_reader=dict(id='mc', file_sha256='c' * 64, signed_utc='2026-10-21T00:00:00Z'))


class Fixture(unittest.TestCase):
    """A temporary repo with one active design whose card is aligned with the synthetic mapping of the T218 tests."""

    def setUp(self):
        RequeueTests.setUp(self)
        self.card.update(policy_family='pf', mechanism='mech')
        self.card['treatment'].update(universe=list(base.UNIVERSE), coverage_end='2024-12-31', excluded_units=[])
        self.card['outcome'] = dict(self.card['outcome'], years=[1999, 2024])
        self.card['transition_year_drop'] = True
        atomic(self.oldpath, self.card)
        self.row.update(card_sha256=sha(self.oldpath), prescreen_enabled=False)
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[self.row]))
        self.spec = base.spec(design_id=self.card['design_id'], freeze=deepcopy(FROZEN))
        self.write_spec(self.spec)

    def write_spec(self, spec):
        path = cr.mapping_path(self.repo, self.card['design_id'])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(spec, sort_keys=True, indent=1))
        return path

    def declare(self, spec=None):
        spec = spec or self.spec
        payload = deepcopy(self.card)
        payload['version'] = 'v9'
        payload.setdefault('automation', {})['history_source'] = dict(kind=ch.SOURCE_KIND, mapping_sha256=ch.mapping_sha(spec),
                                                                      reason='access', rule=ch.RULING)
        path = self.repo / 'data/l2_pipeline/cards/curated.json'
        atomic(path, payload)
        return dict(self.row, card=str(path), card_sha256=sha(path)), payload

    def fake_xlsx(self):
        path = self.repo / cr.XLSX
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x')
        return [patch.object(ch, 'file_sha', return_value=base.SHA), patch.object(ch, 'load_rows', return_value=base.rows_basic())]


class MappingBindingTests(Fixture):
    def test_declaration_and_malformed_links(self):
        row, payload = self.declare()
        self.assertTrue(cr.declared(row))
        self.assertFalse(cr.declared(self.row))
        bad = deepcopy(payload)
        bad['automation']['history_source']['reason'] = 'results'
        atomic(Path(row['card']), bad)
        with self.assertRaisesRegex(ValueError, 'Invalid history_source'):
            cr.declared(row)
        both = deepcopy(payload)
        both['automation']['history_reuse'] = dict(source_design_id='x', rule='y')
        atomic(Path(row['card']), both)
        with self.assertRaises(ValueError):
            cr.declared(row)

    def test_frozen_mapping_loads_and_every_binding_is_checked(self):
        row, payload = self.declare()
        self.assertEqual(cr.load_spec(self.repo, payload)['mapping_id'], 'm1')
        for field, value in (('universe', list(base.UNIVERSE)[:-1]), ('window_start', 2000), ('coverage_end_year', 2023),
                             ('mechanism', 'other'), ('policy_family', 'other')):
            spec = deepcopy(self.spec)
            spec[field] = value
            row2, payload2 = self.declare(spec)
            self.write_spec(spec)
            with self.assertRaisesRegex(ch.Ineligible, 'does not match the card'):
                cr.load_spec(self.repo, payload2)
        self.write_spec(self.spec)

    def test_draft_unfrozen_wrong_hash_missing_and_ineligible_mappings_are_refused(self):
        row, payload = self.declare()
        for freeze in (dict(state='draft_single_author'), dict(state='frozen', mapper_a=FROZEN['mapper_a'], mapper_b=FROZEN['mapper_a'],
                                                                third_reader=FROZEN['third_reader']),
                       dict(FROZEN, third_reader=dict(id='mc', file_sha256='', signed_utc='t'))):
            spec = dict(self.spec, freeze=freeze)
            self.write_spec(spec)
            row2, payload2 = self.declare(spec)
            with self.assertRaises(ch.Ineligible):
                cr.load_spec(self.repo, payload2)
        self.write_spec(self.spec)
        payload['automation']['history_source']['mapping_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            cr.load_spec(self.repo, payload)
        spec = dict(self.spec, eligible=False, reason='no class')
        self.write_spec(spec)
        row3, payload3 = self.declare(spec)
        with self.assertRaisesRegex(ch.Ineligible, 'not eligible'):
            cr.load_spec(self.repo, payload3)
        cr.mapping_path(self.repo, self.card['design_id']).unlink()
        with self.assertRaisesRegex(ch.Ineligible, 'missing'):
            cr.load_spec(self.repo, payload3)


class FreezeTests(Fixture):
    def test_freeze_writes_the_mapping_once_and_needs_three_distinct_readers(self):
        draft = dict(self.spec)
        draft.pop('freeze')
        draft['mappers'] = [dict(role='author', id='x'), dict(role='mapper_b', id='PENDING'), dict(role='third_reader', id='PENDING')]
        final = self.repo / 'final.json'
        final.write_text(json.dumps(draft))
        for name in ('a', 'b', 'c'):
            (self.repo / (name + '.json')).write_text(name)
        who = lambda i, n: dict(id=i, file=self.repo / (n + '.json'))
        cr.mapping_path(self.repo, self.card['design_id']).unlink()
        with self.assertRaises(ch.Ineligible):
            cr.freeze(self.repo, final, who('m1', 'a'), who('m1', 'b'), who('m3', 'c'), '2026-10-21T00:00:00Z')
        path = cr.freeze(self.repo, final, who('m1', 'a'), who('m2', 'b'), who('m3', 'c'), '2026-10-21T00:00:00Z')
        spec = json.loads(Path(path).read_text())
        self.assertEqual(spec['freeze']['state'], 'frozen')
        self.assertEqual(spec['freeze']['mapper_b']['file_sha256'], ch.file_sha(self.repo / 'b.json'))
        cr.check_frozen(spec)
        with self.assertRaises(ValueError):
            cr.freeze(self.repo, final, who('m1', 'a'), who('m2', 'b'), who('m4', 'c'), '2026-10-21T00:00:00Z')   # write-once


class PrepareTests(Fixture):
    def setUp(self):
        super().setUp()
        self.row, self.payload = self.declare()
        self.patches = self.fake_xlsx()
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_build_is_written_once_with_plan_and_ledger_claim(self):
        build = cr.prepare(self.pilot, self.row)
        root = self.pilot.backend.root(self.row)
        self.assertEqual(build['plan']['sample'], ch.audit_plan(build['history'], self.spec, self.card['design_id'])['sample'])
        self.assertEqual(build['mapping_sha256'], ch.mapping_sha(self.spec))
        self.assertTrue((root / 'CURATED_BUILD.json').is_file())
        self.assertEqual(build['outcome_values_read'], 0)
        ledger = json.loads(next((self.repo / cr.LEDGER_DIR).glob('*.json')).read_text())
        self.assertEqual(len(ledger['audits']), 1)
        self.assertEqual(cr.prepare(self.pilot, self.row), build)       # idempotent, no second claim
        self.assertEqual(len(json.loads(next((self.repo / cr.LEDGER_DIR).glob('*.json')).read_text())['audits']), 1)

    def test_second_design_of_the_same_policy_cannot_draw_again(self):
        cr.prepare(self.pilot, self.row)
        spec = dict(self.spec, design_id='other')
        with self.assertRaisesRegex(ValueError, 'already has a db audit'):
            ch.claim_audit(self.repo / cr.LEDGER_DIR, spec, spec['universe'], 'other')

    def test_ineligible_mapping_parks_with_fresh_route(self):
        spec = dict(self.spec, freeze=dict(state='draft'))
        self.write_spec(spec)
        row, _ = self.declare(spec)
        with self.assertRaises(Park) as caught:
            cr.prepare(self.pilot, row)
        self.assertIn('CURATED_INELIGIBLE', str(caught.exception))
        self.assertEqual(caught.exception.metrics['route'], 'fresh_coding')

    def test_ambiguity_over_twenty_percent_parks(self):
        rows = [dict(r) for r in base.rows_basic()]
        for r in rows[:15]:
            r['subtype'] = 'zzz'        # in the candidate class, in neither list -> ambiguous
        with patch.object(ch, 'load_rows', return_value=rows):
            with self.assertRaises(Park) as caught:
                cr.prepare(self.pilot, self.row)
        self.assertIn('ambiguous', str(caught.exception))


class PacketTests(PrepareTests):
    def test_round_jobs_carry_units_only_and_are_idempotent(self):
        build = cr.prepare(self.pilot, self.row)
        calls = []

        def emit(path, **kw):
            calls.append((path, kw))
            return type('J', (), {'job_path': '/tmp/%s.%s.md' % (Path(path).stem, kw['attempt_id'])})()
        with patch.object(self.pilot.backend, 'jobs', return_value=['/x/A.md', '/x/B.md']), \
                patch('scripts.l2_pipeline.agents.history.emit_history_job', side_effect=emit):
            first = cr.round_jobs(self.pilot, self.row, 'r1', build['plan']['sample'])
            again = cr.round_jobs(self.pilot, self.row, 'r1', build['plan']['sample'])
        self.assertEqual(first, again)
        self.assertEqual(len(calls), 2)                                  # second call read the inventory
        for path, kw in calls:
            self.assertEqual(kw['completion_units'], build['plan']['sample'])
            self.assertNotIn('chunk', kw)
            ch.assert_blind(dict(kw, jobs_root=str(kw['jobs_root'])))    # no database field reaches the packet call
        self.assertEqual(first['units'], build['plan']['sample'])


class RoundLogicTests(base.Base):
    """next_units / make_checks / evaluate on the synthetic history of the T218 tests."""

    def fresh_for(self, units, **changes):
        out = {}
        db = {r['unit']: r for r in self.h['accepted']}
        for u in units:
            if db[u]['status'] == 'never_treated':
                out[u] = dict(unit=u, status='never_treated', start_period=None, best_evidence_start_year=None, locator='loc')
            else:
                out[u] = dict(unit=u, status='treated', start_period=db[u]['start_period'],
                              best_evidence_start_year=db[u]['best_evidence_start_year'], locator='loc')
                if db[u].get('pre_window_bound'):
                    out[u]['pre_window_bound'] = True
        for u, rec in changes.items():
            out[u] = rec
        return out

    def test_clean_sample_needs_nothing_more_and_passes(self):
        checks = cr.make_checks(self.plan, self.h, self.spec, [self.fresh_for(self.plan['sample'])])
        self.assertEqual(cr.next_units(self.plan, checks), ([], None))
        self.assertEqual(ch.evaluate(self.plan, checks)['verdict'], 'pass')
        self.assertTrue(all(c['phase'] == 'sample' for c in checks))

    def test_unverified_state_asks_for_one_replacement_in_backup_order(self):
        gone = self.plan['sample'][0]
        records = self.fresh_for(self.plan['sample'])
        records[gone] = None
        checks = cr.make_checks(self.plan, self.h, self.spec, [records])
        units, phase = cr.next_units(self.plan, checks)
        self.assertEqual((units, phase), (self.plan['backup_order'][:1], 'replacement'))
        round2 = self.fresh_for(units)
        checks = cr.make_checks(self.plan, self.h, self.spec, [records, round2])
        self.assertEqual(cr.next_units(self.plan, checks), ([], None))
        self.assertEqual(ch.evaluate(self.plan, checks)['verdict'], 'pass')
        self.assertIn('replacement', {c['phase'] for c in checks})

    def test_one_timing_error_asks_for_the_five_extension_states(self):
        db = {r['unit']: r for r in self.h['accepted']}
        target = next(u for u in self.plan['sample'] if db[u]['status'] == 'treated' and not db[u].get('pre_window_bound'))
        records = self.fresh_for(self.plan['sample'])
        records[target] = dict(records[target], best_evidence_start_year=db[target]['best_evidence_start_year'] + 2,
                               start_period=db[target]['start_period'] + 2)
        checks = cr.make_checks(self.plan, self.h, self.spec, [records])
        units, phase = cr.next_units(self.plan, checks)
        self.assertEqual((units, phase), (self.plan['backup_order'][:5], 'extension'))
        ext = self.fresh_for(units)
        checks = cr.make_checks(self.plan, self.h, self.spec, [records, ext])
        self.assertEqual(ch.evaluate(self.plan, checks)['verdict'], 'pass')
        ext_bad = dict(ext)
        ext_bad[units[0]] = dict(ext[units[0]], status='never_treated', start_period=None, best_evidence_start_year=None) \
            if db[units[0]]['status'] == 'treated' else dict(ext[units[0]], status='treated', start_period=2010, best_evidence_start_year=2009)
        checks = cr.make_checks(self.plan, self.h, self.spec, [records, ext_bad])
        self.assertEqual(ch.evaluate(self.plan, checks)['verdict'], 'fail')

    def test_predicate_error_fails_at_once(self):
        db = {r['unit']: r for r in self.h['accepted']}
        never = next(u for u in self.plan['sample'] if db[u]['status'] == 'never_treated')
        records = self.fresh_for(self.plan['sample'])
        records[never] = dict(unit=never, status='treated', start_period=2011, best_evidence_start_year=2010, locator='loc')
        checks = cr.make_checks(self.plan, self.h, self.spec, [records])
        v = ch.evaluate(self.plan, checks)
        self.assertEqual((v['verdict'], v['route']), ('fail', 'fresh_coding'))

    def test_phase_follows_the_plan_roles(self):
        self.assertEqual(cr.phase_of(self.plan['sample'][0], self.plan, 0), 'sample')
        self.assertEqual(cr.phase_of(self.plan['backup_order'][0], self.plan, 1), 'replacement')
        self.assertEqual(cr.phase_of(self.plan['backup_order'][1], self.plan, 1), 'extension')


class EndToEndTests(Fixture):
    """reconcile -> audit -> S1 accept on a temporary repo, worker records replaced by the database truth or by edits."""

    def setUp(self):
        super().setUp()
        self.row, self.payload = self.declare()
        self.patches = self.fake_xlsx()
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        (self.repo / C42_RULE).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / C42_RULE).write_text('synthetic C42 rule')
        self.build = cr.prepare(self.pilot, self.row)
        self.root = self.pilot.backend.root(self.row)
        ensure_once(cr.round_dir(self.pilot, self.row) / 'r1.json', dict(tag='r1', units=self.build['plan']['sample'], jobs=['j1', 'j2']))
        self.db = {r['unit']: r for r in self.build['history']['accepted']}

    def truth(self, units, **edits):
        out = {}
        for u in units:
            r = self.db[u]
            out[u] = dict(unit=u, status=r['status'], start_period=r['start_period'], best_evidence_start_year=r['best_evidence_start_year'],
                          locator='official', **({'pre_window_bound': True} if r.get('pre_window_bound') else {}))
        out.update(edits)
        return out

    def run_reconcile(self, records):
        with patch.object(cr, 'fresh_records', return_value=records):
            return cr.reconcile(self.pilot, self.row)

    def test_pass_writes_exclusions_reconciliation_certified_history_and_s1_accepts(self):
        value = self.run_reconcile(self.truth(self.build['plan']['sample']))
        self.assertEqual(value['verdict'], 'pass')
        self.assertTrue(value['counts']['curated'])
        for name in ('CURATED_VERDICT.json', 'C42_PREPARATION.json', 'RECONCILIATION.json', 'AUDIT_SELECTION.json'):
            self.assertTrue((self.root / name).is_file(), name)
        ledger = json.loads(next((self.repo / cr.LEDGER_DIR).glob('*.json')).read_text())
        self.assertEqual(ledger['audits'][0]['result'], 'pass')
        self.assertIn('S22', value['excluded_conflict_states'])          # reversal state leaves both arms
        self.assertIn('S20', value['excluded_conflict_states'])          # pre-window state leaves both arms
        audit = cr.audit(self.pilot, self.row)
        self.assertTrue(audit['curated'])
        certificate = read(self.root / 'CERTIFIED_HISTORY.json')
        self.assertEqual(certificate['history_source']['label'], 'db_audited')
        self.assertEqual(read(self.root / 'S1_IMPORT_PROVENANCE.json')['source_type'], 'curated_database_audited')
        treatment = read(self.root / 'TREATMENT.json')
        self.assertNotIn('S22', {r['unit'] for r in treatment['records']})
        ctx = self.pilot.backend.ctx(self.row, strict=False)
        result = cr.accept(ctx)
        self.assertEqual(result.disposition, 'ready')
        self.assertEqual(result.metrics['sample_size'], len(self.build['plan']['sample']))

    def test_failed_audit_parks_to_fresh_coding_and_records_the_ledger(self):
        sample = self.build['plan']['sample']
        never = next(u for u in sample if self.db[u]['status'] == 'never_treated')
        records = self.truth(sample, **{never: dict(unit=never, status='treated', start_period=2011, best_evidence_start_year=2010, locator='x')})
        with self.assertRaises(Park) as caught:
            self.run_reconcile(records)
        self.assertEqual(caught.exception.metrics['route'], 'fresh_coding')
        self.assertIn('CURATED_AUDIT_FAIL', str(caught.exception))
        ledger = json.loads(next((self.repo / cr.LEDGER_DIR).glob('*.json')).read_text())
        self.assertEqual(ledger['audits'][0]['result'], 'fail')
        self.assertFalse((self.root / 'TREATMENT.json').exists())
        self.assertFalse((self.root / 'C42_PREPARATION.json').exists())
        with self.assertRaises(ValueError):
            cr.audit(self.pilot, self.row)       # certify refuses anything but a passed audit

    def test_unreadable_majority_is_inconclusive_not_a_pass(self):
        sample = self.build['plan']['sample']

        def emit(path, **kw):
            return type('J', (), {'job_path': '/tmp/%s.%s.md' % (Path(path).stem, kw['attempt_id'])})()
        with patch.object(self.pilot.backend, 'jobs', return_value=['/x/A.md', '/x/B.md']), \
                patch('scripts.l2_pipeline.agents.history.emit_history_job', side_effect=emit), \
                patch.object(cr, 'collect_round', return_value=(True, [])), \
                patch.object(cr, 'fresh_records', side_effect=lambda jobs, units: {u: None for u in units}):
            self.assertIsNone(cr.reconcile(self.pilot, self.row))         # replacements asked for first
            r2 = json.loads((cr.round_dir(self.pilot, self.row) / 'r2.json').read_text())
            self.assertEqual(r2['units'], self.build['plan']['backup_order'][:len(sample)][:len(r2['units'])])
            with self.assertRaises(Park) as caught:                       # replacements unreadable too
                cr.reconcile(self.pilot, self.row)
        self.assertIn('CURATED_AUDIT_INCONCLUSIVE', str(caught.exception))
        self.assertEqual(caught.exception.metrics['route'], 'not_evaluable')
        self.assertFalse((self.root / 'TREATMENT.json').exists())

    def test_next_round_inventory_is_not_repeated(self):
        sample = self.build['plan']['sample']
        records = self.truth(sample)
        records[sample[0]] = None
        calls = []
        with patch.object(cr, 'collect_round', return_value=(True, [])), \
                patch.object(cr, 'round_jobs', side_effect=lambda pilot, row, tag, units: calls.append((tag, units)) or dict(tag=tag, units=units, jobs=[])), \
                patch.object(cr, 'fresh_records', return_value=records):
            self.assertIsNone(cr.reconcile(self.pilot, self.row))
        self.assertEqual(calls, [('r2', self.build['plan']['backup_order'][:1])])

    def test_v7_minimum_parks_after_a_pass(self):
        sample = self.build['plan']['sample']
        with patch('scripts.l2_pipeline.rules.reconcile_minimums', return_value=dict(min_retained_treated=99, min_retained_controls=5)):
            with self.assertRaisesRegex(Park, 'retained treated below minimum'):
                self.run_reconcile(self.truth(sample))


class SwitchTests(Fixture):
    """The window tool: park the old attempt, add only the declaration, requeue; leave designs without a frozen mapping alone."""

    def setUp(self):
        super().setUp()
        case = self.pilot.case_dir(self.row)
        (case / 'STOP.json').unlink()                                # an in-flight design: neither stopped nor parked
        for action in ('history', 'reconcile', 'audit'):
            self.pilot.checkpoint(self.row, action).unlink()
        atomic(self.registry / 'CODE_TASK.lock', dict(task='CURATED_ROUTE'))
        ruling = self.repo / ch.RULING
        ruling.parent.mkdir(parents=True, exist_ok=True)
        ruling.write_text('synthetic curated ruling')
        self.patches = self.fake_xlsx()
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.designs = (self.card['design_id'],)

    def test_in_flight_design_is_parked_and_requeued_with_only_the_declaration(self):
        before = sha(self.oldpath)
        result = cr.apply(self.pilot, self.designs)
        self.assertEqual([r['status'] for r in result['records']], ['requeued'])
        new_row = read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]
        new_card, old_card = read(new_row['card']), read(self.oldpath)
        changed = {k for k in set(old_card) | set(new_card) if old_card.get(k) != new_card.get(k)}
        self.assertEqual(changed, {'version', 'automation'})
        self.assertEqual(new_card['automation']['history_source']['mapping_sha256'], ch.mapping_sha(self.spec))
        self.assertEqual(sha(self.oldpath), before)
        self.assertTrue((self.pilot.case_dir(self.row) / 'PARKED.json').is_file())          # old attempt kept, parked, untouched otherwise
        self.assertEqual(self.pilot.next_action(new_row), 'history')
        self.assertTrue(cr.declared(new_row))
        self.assertIn('SUPERSEDED_CURATED_ROUTE', {e.get('status') for e in read(self.registry / 'HUB_QUEUE.json')['entries']} | {'SUPERSEDED_CURATED_ROUTE'})
        self.assertEqual(cr.apply(self.pilot, self.designs), result)

    def test_unfrozen_mapping_or_small_roster_leaves_the_design_on_fresh_coding(self):
        self.write_spec(dict(self.spec, freeze=dict(state='draft_single_author')))
        result = cr.apply(self.pilot, self.designs)
        self.assertEqual(result['records'][0]['status'], 'left_on_fresh_route')
        self.assertEqual(read(self.registry / 'ACTIVE_DESIGNS.json')['designs'], [self.row])

    def test_window_lock_is_required(self):
        atomic(self.registry / 'CODE_TASK.lock', dict(task='OTHER'))
        with self.assertRaisesRegex(ValueError, 'CURATED_ROUTE stopped code window'):
            cr.apply(self.pilot, self.designs)

    def test_small_database_roster_is_refused_before_any_change(self):
        with patch.object(cr, 'roster_minimums', return_value=(False, dict(retained_treated=2))):
            result = cr.apply(self.pilot, self.designs)
        self.assertEqual(result['records'][0]['reason'], 'database roster below the pipeline minimums')
        self.assertFalse((self.pilot.case_dir(self.row) / 'PARKED.json').exists())


class MappingFileTests(unittest.TestCase):
    """The seven drafts: structure, counts of adopters and dates against the cards' screening dates (database rows only)."""
    RAND_CARDS = ('campus_ccw_ban_violent_crime', 'dv_firearm_removal_at_scene_firearm_homicide',
                  'mental_health_commitment_prohibitor_firearm_homicide', 'state_preemption_punitive_firearm_homicide',
                  'straw_purchase_law_firearm_homicide')
    EXPECT = {   # (database treated in window, never treated) after the mapping, counted before the card's own exclusions
        'campus_ccw_ban_violent_crime': (18, 27), 'dv_firearm_removal_at_scene_firearm_homicide': (20, 16),
        'mental_health_commitment_prohibitor_firearm_homicide': (11, 19), 'state_preemption_punitive_firearm_homicide': (13, 37),
        'straw_purchase_law_firearm_homicide': (12, 34)}

    @classmethod
    def setUpClass(cls):
        if not XLSX.is_file():
            raise unittest.SkipTest('RAND workbook not present')
        cls.rows = ch.load_rows(XLSX)

    def spec(self, key):
        return json.loads((DRAFTS / ('b1020rn_%s.json' % key)).read_text())

    def card(self, key):
        return json.loads((CARDS / ('b1020rn_%s.json' % key)).read_text())

    def test_every_design_has_a_mapping_file_and_drafts_are_never_loadable(self):
        keys = self.RAND_CARDS + ('violent_misdemeanor_firearm_restriction_firearm_homicide', 'stalking_misdemeanor_firearm_restriction_firearm_homicide')
        self.assertEqual(len(keys), 7)
        for key in keys:
            spec = self.spec(key)
            self.assertEqual(spec['design_id'], 'b1020rn_' + key)
            self.assertEqual(spec['universe'], self.card(key)['treatment']['universe'])
            self.assertEqual(spec['window_start'], self.card(key)['outcome']['years'][0])
            self.assertEqual(spec['mechanism'], self.card(key)['mechanism'])
            with self.assertRaises(ch.Ineligible):
                cr.check_frozen(spec)          # none of them is frozen by two independent mappers and a third reader

    def test_stalking_has_no_database_class_and_stays_on_fresh_coding(self):
        spec = self.spec('stalking_misdemeanor_firearm_restriction_firearm_homicide')
        self.assertIs(spec['eligible'], False)
        self.assertEqual(spec['classes'], [])
        classes = {(r['law_class'], r['subtype']) for r in self.rows}
        self.assertFalse([c for c in classes if 'stalk' in (c[0] or '') or 'stalk' in (c[1] or '')])

    def test_rand_designs_counts_and_dates_agree_with_the_card_screening(self):
        for key in self.RAND_CARDS:
            spec = self.spec(key)
            ch.validate_mapping(spec)
            history = ch.build_history(self.rows, spec)
            self.assertLessEqual(history['ambiguity']['ambiguous_share'], 0.2, key)
            accepted = {r['unit']: r for r in history['accepted']}
            self.assertEqual(len(accepted) + len(history['excluded']), len(spec['universe']), key)
            treated = {u: r['best_evidence_start_year'] for u, r in accepted.items() if r['status'] == 'treated' and not r.get('pre_window_bound')}
            never = [u for u, r in accepted.items() if r['status'] == 'never_treated']
            self.assertEqual((len(treated), len(never)), self.EXPECT[key], key)
            screening = self.card('%s' % key)['treatment']['screening_adoption']['adopters']
            in_db = [u for u in screening if u in treated]
            self.assertTrue(all(treated[u] == screening[u] for u in in_db), key)       # same operative year wherever both name a state
            self.assertGreaterEqual(len(in_db), len(screening) - 1, key)                # at most one card adopter is not an in-window DB adopter
            plan = ch.audit_plan(history, spec, spec['design_id'])
            n = len([1 for r in accepted.values() if r['status'] == 'treated' and not r.get('pre_window_bound')])
            self.assertEqual(plan['census'], n <= 12)
            if not plan['census']:
                self.assertGreaterEqual(plan['k'], max(10, -(-n // 4)))

    def test_violent_misdemeanor_database_roster_is_too_small_and_the_switch_tool_skips_it(self):
        key = 'violent_misdemeanor_firearm_restriction_firearm_homicide'
        spec = self.spec(key)
        history = ch.build_history(self.rows, spec)
        treated = [r for r in history['accepted'] if r['status'] == 'treated' and not r.get('pre_window_bound')]
        self.assertLess(len(treated), 6)
        self.assertGreaterEqual(len(history['excluded']), 5)
        self.assertTrue(set(history['exclusion_reasons'].values()) <= {'ambiguous_rows_only', 'reversal', 'repeal_and_reenactment', 'repeal_row'})


class EngineAdditionTests(base.Base):
    """The two T219 additions to the build: repeal rows and ambiguous-only states."""

    def test_repeal_row_excludes_the_state_and_is_never_an_onset(self):
        rows = base.rows_basic() + [dict(base.drow('S01', 2012), change='repeal')]
        h = ch.build_history(rows, self.spec)
        self.assertEqual(h['exclusion_reasons']['S01'], 'repeal_row')
        only = [r for r in base.rows_basic() if r['state'] != 'S02'] + [dict(base.drow('S02', 2012), change='repeal')]
        self.assertEqual(ch.build_history(only, self.spec)['exclusion_reasons']['S02'], 'repeal_row')

    def test_state_with_only_ambiguous_rows_is_excluded_not_a_control(self):
        spec = deepcopy(self.spec)
        rows = [r for r in base.rows_basic() if r['state'] != 'S05'] + [base.drow('S05', 2008, subtype='zzz')]
        h = ch.build_history(rows, spec)
        self.assertEqual(h['exclusion_reasons']['S05'], 'ambiguous_rows_only')
        self.assertNotIn('S05', {r['unit'] for r in h['accepted']})


if __name__ == '__main__':
    unittest.main()
