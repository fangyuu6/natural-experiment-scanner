"""T187 (hub approval 2026-10-20, held designs): the C57 rebind may carry ONE hub-approved report-only added sensitivity. Everything else
still fails closed. Invented inputs only; no outcome value, no model call."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.l2_pipeline import c57
from scripts.l2_pipeline.c57 import (apply_addition, authorised_sensitivity, check_added_controls, science_equal, science_equal_added,
                                     sensitivity_sha256, validate_rebind, without_labels)
from scripts.l2_pipeline.population_resolution import bind, rebase_sources
from scripts.l2_pipeline.predictions import import_unchanged_predictions
from scripts.l2_pipeline.stages.s3_support import variant_treatment
from scripts.l2_pipeline.storage import ensure_once

DESIGN = 'invented_design'
ENTRY_DROP = {'changes': {'treatment': {'excluded_units': ['A', 'B', 'C']}}, 'name': 'drop_controls'}
ENTRY_DATE = {'changes': {'treatment': {'start_overrides': {'T1': 2010}}}, 'name': 'earlier_start'}


class Files:
    """A hub approval file and a register file in a throw-away docs root."""
    def __init__(self, test, entry=ENTRY_DROP, design=DESIGN):
        tmp = tempfile.TemporaryDirectory(); test.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve(); self.entry = entry; self.design = design
        self.approval = self.root / 'HUB_RULING_APPROVAL.md'
        self.approval.write_text('# ruling\n\n> "Hub approval 2026-10-20: open a new attempt for %s by the C57 route"\n' % design)
        self.register = self.root / 'HUB_RULING_REGISTER.md'
        self.register.write_text('cites %s\nC57-SENSITIVITY %s %s %s\n' % (self.approval.name, design, entry['name'], sensitivity_sha256(entry)))
        patcher = patch.object(c57, 'RULING_ROOT', self.root); patcher.start(); test.addCleanup(patcher.stop)

    def extra(self, **changes):
        value = dict(name=self.entry['name'], sensitivity_sha256=sensitivity_sha256(self.entry), approval=bind(self.approval),
                     register=bind(self.register), drop_screen_expected_support=True)
        return dict(value, **changes)


def old_card():
    from scripts.l2_pipeline.tests.test_pipeline import card
    c = card()
    c['analysis']['screen_expected_support'] = dict(treated_n=9)
    return c


class AuthorisedSensitivity(unittest.TestCase):
    def test_registered_entry_is_authorised(self):
        f = Files(self); self.assertTrue(authorised_sensitivity(f.extra(), copy.deepcopy(ENTRY_DROP), DESIGN))
        f2 = Files(self, ENTRY_DATE); self.assertTrue(authorised_sensitivity(f2.extra(), copy.deepcopy(ENTRY_DATE), DESIGN))

    def refuses(self, f, extra, entry, message, design=DESIGN):
        with self.assertRaisesRegex(ValueError, message):
            authorised_sensitivity(extra, entry, design)

    def test_wrong_design_wrong_hash_or_edited_entry_is_refused(self):
        f = Files(self)
        self.refuses(f, f.extra(), copy.deepcopy(ENTRY_DROP), 'no "Hub approval" line names the design', design='other_design')
        self.refuses(f, f.extra(sensitivity_sha256='0' * 64), copy.deepcopy(ENTRY_DROP), 'exact sensitivity')
        edited = copy.deepcopy(ENTRY_DROP); edited['changes']['treatment']['excluded_units'] = ['A', 'B']
        self.refuses(f, f.extra(), edited, 'differs from the registered one')
        self.refuses(f, f.extra(), None, 'differs from the registered one')

    def test_approval_line_must_be_a_hub_approval_line_naming_the_design(self):
        f = Files(self); stale = f.extra()
        f.approval.write_text('# ruling\nthe design %s is mentioned here but nobody approved it\n' % DESIGN)
        self.refuses(f, stale, copy.deepcopy(ENTRY_DROP), 'authority changed')          # hash no longer matches
        self.refuses(f, f.extra(), copy.deepcopy(ENTRY_DROP), 'no "Hub approval" line')

    def test_register_must_cite_the_approval_and_name_the_sensitivity(self):
        f = Files(self)
        f.register.write_text('C57-SENSITIVITY %s %s %s\n' % (DESIGN, ENTRY_DROP['name'], sensitivity_sha256(ENTRY_DROP)))
        self.refuses(f, f.extra(register=bind(f.register)), copy.deepcopy(ENTRY_DROP), 'does not cite the approval')
        f.register.write_text('cites %s\n' % f.approval.name)
        self.refuses(f, f.extra(register=bind(f.register)), copy.deepcopy(ENTRY_DROP), 'exact sensitivity')

    def test_hub_files_must_be_hub_rulings_under_the_docs_root(self):
        f = Files(self)
        other = f.root / 'notes.md'; other.write_text(f.approval.read_text())
        self.refuses(f, f.extra(approval=bind(other)), copy.deepcopy(ENTRY_DROP), 'HUB_RULING_')
        elsewhere = tempfile.TemporaryDirectory(); self.addCleanup(elsewhere.cleanup)
        out = Path(elsewhere.name) / 'HUB_RULING_X.md'; out.write_text(f.approval.read_text())
        self.refuses(f, f.extra(approval=bind(out)), copy.deepcopy(ENTRY_DROP), 'HUB_RULING_')

    def test_only_two_change_keys_one_at_a_time_and_malformed_extras_are_refused(self):
        for entry, message in (
                ({'changes': {'treatment': {'start_shift': 1}}, 'name': 'x'}, 'only treatment.excluded_units or treatment.start_overrides'),
                ({'changes': {'treatment': {'excluded_units': ['A'], 'start_overrides': {'T': 1}}}, 'name': 'x'}, 'only treatment'),
                ({'changes': {'analysis': {'min_controls': 1}}, 'name': 'x'}, 'only treatment'),
                ({'changes': {'treatment': {'excluded_units': ['B', 'A']}}, 'name': 'x'}, 'sorted list'),
                ({'changes': {'treatment': {'start_overrides': {'T': '2010'}}}, 'name': 'x'}, 'integer periods'),
                ({'changes': {'treatment': {'start_overrides': {}}}, 'name': 'x'}, 'integer periods'),
                ({'changes': {'treatment': {'excluded_units': ['A']}}, 'name': 'x', 'report_only': True}, 'differs from the registered one')):
            f = Files(self, entry)
            self.refuses(f, f.extra(), entry, message)
        f = Files(self)
        self.refuses(f, dict(f.extra(), surprise=1), copy.deepcopy(ENTRY_DROP), 'malformed')
        self.refuses(f, f.extra(drop_screen_expected_support='yes'), copy.deepcopy(ENTRY_DROP), 'malformed')


class ApplyAddition(unittest.TestCase):
    def rebased(self):
        card = rebase_sources(old_card(), Path('/tmp/old.json')); card['version'] = 'v2'
        card.setdefault('automation', {})['c57_implementation_revision'] = dict(science_unchanged=True, execution_authorized=False)
        return card

    def test_appends_the_entry_drops_only_the_screen_block_and_notes_the_revision(self):
        f = Files(self); before = self.rebased(); after = apply_addition(before, f.extra(), copy.deepcopy(ENTRY_DROP), DESIGN)
        self.assertEqual(after['sensitivities'], before['sensitivities'] + [ENTRY_DROP])
        self.assertNotIn('screen_expected_support', after['analysis'])
        self.assertEqual({k: v for k, v in after['analysis'].items()}, {k: v for k, v in before['analysis'].items() if k != 'screen_expected_support'})
        for key in ('treatment', 'outcome', 'first_order', 'sources', 'population', 'track', 'mechanism'):
            self.assertEqual(after[key], before[key])
        revision = after['automation']['c57_implementation_revision']
        self.assertIs(revision['science_unchanged'], False); self.assertIs(revision['primary_science_unchanged'], True)
        self.assertEqual(revision['added_sensitivity']['name'], 'drop_controls')
        self.assertEqual(before, self.rebased())                      # pure

    def test_refuses_a_duplicate_name_or_a_missing_screen_block(self):
        f = Files(self); card = self.rebased()
        card['sensitivities'] = card['sensitivities'] + [copy.deepcopy(ENTRY_DROP)]
        with self.assertRaisesRegex(ValueError, 'already registered'):apply_addition(card, f.extra(), copy.deepcopy(ENTRY_DROP), DESIGN)
        card = self.rebased(); del card['analysis']['screen_expected_support']
        with self.assertRaisesRegex(ValueError, 'no stale screen block'):apply_addition(card, f.extra(), copy.deepcopy(ENTRY_DROP), DESIGN)
        apply_addition(card, f.extra(drop_screen_expected_support=False), copy.deepcopy(ENTRY_DROP), DESIGN)


class ValidateRebind(unittest.TestCase):
    def setUp(self):
        self.files = Files(self)
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); self.root = Path(tmp.name).resolve()
        registry = self.root / 'registry'; registry.mkdir(); (registry / 'AUTOPILOT_STOP').touch()
        ensure_once(self.root / 'SPEC.json', {'science': True}); ensure_once(self.root / 'SUPPORT.json', {'mask': True})
        self.oldpath = ensure_once(self.root / 'old.json', old_card())
        auth = ensure_once(self.root / 'authority.json', dict(schema='l2_c57_mechanical_authorization_v1', designs=[DESIGN]))
        self.old = dict(card=str(self.oldpath), design_id=DESIGN)
        self.p = SimpleNamespace(registry=registry, backend=SimpleNamespace(root=lambda r: self.root), stop_record=lambda r: True)
        self.authority = bind(auth)
        self.ruling = dict(authority=self.authority, execution_authorized=False, superseded_status='SUPERSEDED_C57',
                           prior_spec=bind(self.root / 'SPEC.json'), prior_support=bind(self.root / 'SUPPORT.json'), added_sensitivity=self.files.extra())

    def new_card(self):
        new = rebase_sources(json.loads(self.oldpath.read_text()), self.oldpath); new['version'] = 'v2'
        new.setdefault('automation', {})['c57_implementation_revision'] = dict(prior_card=bind(self.oldpath), prior_spec=bind(self.root / 'SPEC.json'),
            authority=self.authority, science_unchanged=True, execution_authorized=False)
        return apply_addition(new, self.files.extra(), copy.deepcopy(ENTRY_DROP), DESIGN)

    def write(self, card, name='new.json'):
        path = self.root / name; path.write_text(json.dumps(card)); return path

    def test_exact_authorised_card_is_accepted(self):
        validate_rebind(self.p, self.old, self.ruling, self.write(self.new_card()))

    def test_every_other_change_is_still_a_changed_scientific_card(self):
        def change(fn):
            c = self.new_card(); fn(c); return c
        cases = {
            'primary analysis': lambda c: c['analysis'].__setitem__('min_controls', 99),
            'primary treatment': lambda c: c['treatment'].__setitem__('predicate', 'other'),
            'outcome': lambda c: c['outcome'].__setitem__('years', [1, 2]),
            'second sensitivity': lambda c: c['sensitivities'].append({'name': 'second', 'changes': {}}),
            'earlier sensitivity edited': lambda c: c['sensitivities'][0].__setitem__('name', 'renamed') if c['sensitivities'][:-1] else c.__setitem__('first_order', {}),
            'screen block kept': lambda c: c['analysis'].__setitem__('screen_expected_support', dict(treated_n=9)),
            'revision note dropped': lambda c: c['automation'].pop('c57_implementation_revision'),
        }
        for name, fn in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                validate_rebind(self.p, self.old, self.ruling, self.write(change(fn)))

    def test_without_the_ruling_claim_the_old_exact_card_rule_applies(self):
        no_claim = dict(self.ruling); del no_claim['added_sensitivity']
        with self.assertRaisesRegex(ValueError, 'scientific card'):validate_rebind(self.p, self.old, no_claim, self.write(self.new_card()))
        # and a plain version-only rebind is still accepted, as before
        plain = rebase_sources(json.loads(self.oldpath.read_text()), self.oldpath); plain['version'] = 'v2'
        plain.setdefault('automation', {})['c57_implementation_revision'] = dict(prior_card=bind(self.oldpath), prior_spec=bind(self.root / 'SPEC.json'),
            authority=self.authority, science_unchanged=True, execution_authorized=False)
        validate_rebind(self.p, self.old, no_claim, self.write(plain, 'plain.json'))

    def test_a_claim_with_a_card_that_adds_nothing_is_refused(self):
        plain = rebase_sources(json.loads(self.oldpath.read_text()), self.oldpath); plain['version'] = 'v2'
        plain.setdefault('automation', {})['c57_implementation_revision'] = dict(prior_card=bind(self.oldpath), prior_spec=bind(self.root / 'SPEC.json'),
            authority=self.authority, science_unchanged=True, execution_authorized=False)
        with self.assertRaises(ValueError):validate_rebind(self.p, self.old, self.ruling, self.write(plain, 'plain.json'))

    def test_consumed_design_is_still_refused(self):
        (self.root / 'EXECUTION_ONCE.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'consumed'):validate_rebind(self.p, self.old, self.ruling, self.write(self.new_card()))


def spec(sens, rows, screen=True, **extra):
    analysis = dict(controls='never_treated')
    if screen:analysis['screen_expected_support'] = dict(treated_n=9)
    return dict(version='v1', card_sha256='a', analysis=analysis, sensitivities=[dict(name=n, changes={}) for n in sens],
                support=dict(primary_passed=True, variants=[dict(variant=n, passed=True) for n in rows]), treatment=dict(excluded_units=['X']), **extra)


class ScienceEqualAdded(unittest.TestCase):
    def pair(self):
        old = spec(['s1', 's2'], ['primary', 's1', 's2'])
        new = spec(['s1', 's2', 'added'], ['primary', 's1', 's2', 'added'], screen=False)
        new.update(version='v2', card_sha256='b')
        return old, new

    def test_only_the_addition_and_the_screen_block_differ(self):
        old, new = self.pair(); self.assertEqual(science_equal_added(old, new, 'added', True), [])

    def test_the_variant_may_sit_before_a_variant_the_s1_audit_appends(self):
        old = spec(['s1', 'drop_unresolved'], ['primary', 's1', 'drop_unresolved'])
        new = spec(['s1', 'added', 'drop_unresolved'], ['primary', 's1', 'added', 'drop_unresolved'], screen=False)
        self.assertEqual(science_equal_added(old, new, 'added', True), [])

    def test_anything_else_is_reported(self):
        old, new = self.pair()
        for name, fn in {
            'primary support row': lambda n: n['support']['variants'][0].update(passed=False),
            'earlier sensitivity': lambda n: n['sensitivities'][0].update(changes={'treatment': {}}),
            'analysis': lambda n: n['analysis'].update(min_controls=1),
            'treatment': lambda n: n['treatment'].update(excluded_units=['X', 'Y']),
            'top-level field': lambda n: n.update(l3='new'),
            'second added variant': lambda n: (n['sensitivities'].append(dict(name='more', changes={})), n['support']['variants'].append(dict(variant='more', passed=True))),
            'variant row missing': lambda n: n['support'].update(variants=[r for r in n['support']['variants'] if r['variant'] != 'added']),
            'sensitivity missing': lambda n: n.update(sensitivities=n['sensitivities'][:-1]),
        }.items():
            candidate = copy.deepcopy(new); fn(candidate)
            with self.subTest(name):self.assertNotEqual(science_equal_added(old, candidate, 'added', True), [])
        kept = copy.deepcopy(new); kept['analysis']['screen_expected_support'] = dict(treated_n=9)
        self.assertEqual(science_equal_added(old, kept, 'added', True), ['analysis'])      # the block must really be gone
        self.assertEqual(science_equal_added(old, kept, 'added', False), [])
        old2, new2 = self.pair(); new2['analysis']['screen_expected_support'] = dict(treated_n=1)
        self.assertNotEqual(science_equal_added(old2, new2, 'added', False), [])

    def test_a_variant_already_in_the_old_spec_is_not_an_addition(self):
        old, new = self.pair(); old['sensitivities'].append(dict(name='added', changes={}))
        self.assertNotEqual(science_equal_added(old, new, 'added', True), [])

    def test_purpose_label_only_difference_passes(self):
        old, new = self.pair(); old['l3_export_v2'] = dict(geography='g'); new['l3_export_v2'] = dict(geography='g', purpose_label='not_applicable_r0')
        self.assertEqual(science_equal_added(old, new, 'added', True), [])
        old['l3_export_v2']['purpose_label'] = 'unavailable'
        self.assertEqual(science_equal_added(old, new, 'added', True), [])
        o, n = copy.deepcopy(old), copy.deepcopy(old); n['version'] = 'v2'; n['l3_export_v2']['purpose_label'] = 'x'
        self.assertEqual(science_equal(o, n), [])

    def test_any_other_difference_beside_purpose_label_still_fails(self):
        old, new = self.pair(); old['l3_export_v2'] = dict(geography='g'); new['l3_export_v2'] = dict(geography='g', purpose_label='unavailable')
        for name, fn in {
            'other export field': lambda n: n['l3_export_v2'].update(first_order=1),
            'export field changed': lambda n: n['l3_export_v2'].update(geography='h'),
            'export field removed': lambda n: n['l3_export_v2'].pop('geography'),
            'label at the wrong level': lambda n: n.update(purpose_label='x'),
            'similar name': lambda n: n['l3_export_v2'].update(purpose_direction_status='EVAL'),
            'analysis': lambda n: n['analysis'].update(min_controls=1),
        }.items():
            candidate = copy.deepcopy(new); fn(candidate)
            with self.subTest(name):self.assertNotEqual(science_equal_added(old, candidate, 'added', True), [])
        o = dict(old, l3_export_v2='not a dict'); self.assertNotEqual(science_equal(o, new), [])
        self.assertEqual(without_labels(dict(l3_export_v2=dict(purpose_label='a', z=1))), dict(l3_export_v2=dict(z=1)))


    def test_plain_science_equal_unchanged(self):
        old, new = self.pair(); self.assertNotEqual(science_equal(old, new), [])


class StartOverrides(unittest.TestCase):
    RECORDS = [dict(unit='T1', status='treated', start_period=2012, operative_period=2012), dict(unit='T2', status='treated', start_period=2005),
               dict(unit='N1', status='never_treated', start_period=None), dict(unit='N2', status='never_treated', start_period=None)]

    def card(self, **treatment):
        return dict(treatment=dict(universe=['T1', 'T2', 'N1', 'N2'], **treatment))

    def test_without_the_key_nothing_changes(self):
        got = variant_treatment(copy.deepcopy(self.RECORDS), self.card())
        self.assertEqual(got, copy.deepcopy(self.RECORDS))
        shifted = variant_treatment(copy.deepcopy(self.RECORDS), self.card(start_shift=1))
        self.assertEqual([r['start_period'] for r in shifted if r['status'] == 'treated'], [2013, 2006])

    def test_override_redates_only_the_named_treated_unit_and_keeps_no_operative_period(self):
        got = {r['unit']: r for r in variant_treatment(copy.deepcopy(self.RECORDS), self.card(start_overrides={'T1': 2010}))}
        self.assertEqual(got['T1']['start_period'], 2010); self.assertNotIn('operative_period', got['T1'])
        self.assertEqual(got['T2']['start_period'], 2005); self.assertIsNone(got['N1']['start_period'])
        self.assertEqual(got['T1']['status'], 'treated')

    def test_override_composes_with_excluded_units(self):
        got = variant_treatment(copy.deepcopy(self.RECORDS), self.card(start_overrides={'T1': 2010}, excluded_units=['N2']))
        self.assertEqual([r['unit'] for r in got], ['T1', 'T2', 'N1'])
        with self.assertRaisesRegex(ValueError, 'retained treated'):variant_treatment(copy.deepcopy(self.RECORDS), self.card(start_overrides={'T1': 2010}, excluded_units=['T1']))

    def test_bad_overrides_fail_closed(self):
        for bad in ({'T1': '2010'}, {'T1': 2010.0}, {'T1': True}, {'ZZ': 2010}, {'N1': 2010}, {}, [], None and {}):
            if bad is None:continue
            with self.subTest(bad), self.assertRaises(ValueError):
                variant_treatment(copy.deepcopy(self.RECORDS), self.card(start_overrides=bad))
        with self.assertRaisesRegex(ValueError, 'cannot combine'):
            variant_treatment(copy.deepcopy(self.RECORDS), self.card(start_overrides={'T1': 2010}, start_shift=1))

    def test_transition_year_drop_follows_the_new_date_in_support(self):
        from scripts.l2_pipeline.support import support_check
        years = list(range(2000, 2021)); units = ['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8'] + ['N%d' % i for i in range(8)]
        records = [dict(unit=u, status='treated', start_period=2008 + i % 4) for i, u in enumerate(units[:8])] + [dict(unit=u, status='never_treated', start_period=None) for u in units[8:]]
        keys = {(u, y) for u in units for y in years}
        cfg = dict(min_pre_periods=3, min_controls=5, min_cohorts=2, min_treated=6, min_post_periods=2)
        card = dict(treatment=dict(universe=units, start_overrides={'T4': 2006}))
        base = support_check(keys, variant_treatment(copy.deepcopy(records), dict(treatment=dict(universe=units))), cfg, period_years=years, transition_year_drop=True)
        var = support_check(keys, variant_treatment(copy.deepcopy(records), card), cfg, period_years=years, transition_year_drop=True)
        self.assertTrue(base['passed'] and var['passed'])
        def first_post_years(summary):
            return sorted({c.get('g', c.get('cohort')) for c in summary['cell_inventory']})
        # primary: T4 starts 2011 (cohort label 2012 after the operation-year drop); variant: 2006 (label 2007), the others unchanged
        self.assertIn(2012, first_post_years(base)); self.assertNotIn(2007, first_post_years(base))
        self.assertIn(2007, first_post_years(var)); self.assertEqual(sorted(set(first_post_years(var)) ^ set(first_post_years(base))), [2007])


class AddedControls(unittest.TestCase):
    TREATMENT = dict(records=[dict(unit='T', status='treated'), dict(unit='A', status='never_treated'), dict(unit='B', status='never_treated'),
                              dict(unit='C', status='never_treated'), dict(unit='D', status='never_treated')])

    def test_only_added_never_treated_controls_pass(self):
        entry = {'changes': {'treatment': {'excluded_units': ['A', 'B', 'X']}}, 'name': 'n'}
        self.assertEqual(check_added_controls(self.TREATMENT, entry, ['X']), ['A', 'B'])
        with self.assertRaisesRegex(ValueError, 'drops a primary exclusion'):check_added_controls(self.TREATMENT, entry, ['X', 'Y'])
        treated = {'changes': {'treatment': {'excluded_units': ['A', 'T']}}, 'name': 'n'}
        with self.assertRaisesRegex(ValueError, 'never-treated controls only: T'):check_added_controls(self.TREATMENT, treated, [])
        with self.assertRaises(ValueError):check_added_controls(self.TREATMENT, {'changes': {'treatment': {'excluded_units': ['X']}}, 'name': 'n'}, ['X'])
        self.assertEqual(check_added_controls(self.TREATMENT, {'changes': {'treatment': {'start_overrides': {'T': 1}}}, 'name': 'n'}, []), [])


class ForecastImportOfAddition(unittest.TestCase):
    def build(self, tmp, extra_rows):
        """Two design roots with the minimal files import_unchanged_predictions reads (one persona)."""
        from scripts.l2_pipeline.storage import sha
        out = []
        for name, rows in (('old', ['primary', 's1']), ('new', ['primary', 's1'] + extra_rows)):
            root = Path(tmp) / name; root.mkdir()
            primary = dict(variant='primary', cell_inventory=[dict(g=2005, t=2006, base_period=2004, post=True, treated=['A'], controls=['B', 'C'])])
            spec = dict(design_id='d', track='DiD', population={}, outcome=dict(outcome_unit='u'), analysis=dict(estimand='e', weights='w', controls='c', clock='k'),
                        support=dict(variants=[primary if r == 'primary' else dict(variant=r) for r in rows]))
            ensure_once(root / 'SPEC.json', spec); out.append(root)
        old, new = out
        persona = old / 'predictions/Card'; persona.mkdir(parents=True)
        original = persona / 'orig.json'; original.write_text('{"p":1}')
        sub = dict(persona='Card', design_id='d', spec_sha256=sha(old / 'SPEC.json'), point=1, interval=[0, 2], interval_level=.8, rationale='r', disclosures=[])
        ensure_once(persona / 'submission.json', sub)
        ensure_once(persona / 'COLLECTION.json', dict(validated=True, role='Card', design_id='d', stage='S5', original_path=str(original),
            original_sha256=sha(original), promoted_sha256=sha(persona / 'submission.json')))
        return old, new

    def test_added_variant_is_recorded_and_other_extra_rows_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = self.build(tmp, ['added'])
            proof = json.loads(import_unchanged_predictions(old, new, ['Card'], 'added').read_text())
            self.assertEqual(proof['added_sensitivity'], 'added'); self.assertIs(proof['primary_target_unchanged'], True)
        with tempfile.TemporaryDirectory() as tmp:
            old, new = self.build(tmp, ['added', 'sneaky'])
            proof = json.loads(import_unchanged_predictions(old, new, ['Card'], 'added').read_text())     # extra row is for validate_contract/science_equal_added to refuse
            self.assertEqual(proof['added_sensitivity'], 'added')
        with tempfile.TemporaryDirectory() as tmp:
            old, new = self.build(tmp, [])
            with self.assertRaisesRegex(ValueError, 'exactly one new SUPPORT row'):import_unchanged_predictions(old, new, ['Card'], 'added')
        with tempfile.TemporaryDirectory() as tmp:
            old, new = self.build(tmp, [])
            self.assertNotIn('added_sensitivity', json.loads(import_unchanged_predictions(old, new, ['Card']).read_text()))


class ForecastContractWithAddition(unittest.TestCase):
    def setUp(self):
        from scripts.l2_pipeline.storage import sha
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); self.root = Path(tmp.name).resolve()
        self.saved = self.card('v2', 'primary', 's1')
        meta = dict(contract=dict(card=self.saved)); job = self.root / 'job.md'
        job.write_text('## Execution metadata binding (data)\n```json\n' + json.dumps(meta) + '\n```\n')
        ensure_once(job.with_suffix('.job.json'), meta)
        ensure_once(self.root / 'predictions/Card/COLLECTION.json', dict(job_path=str(job), job_sha256=sha(job)))
        self.spec = ensure_once(self.root / 'SPEC.json', dict(invented=True))
        self.sha = sha

    def card(self, version, *variants):
        return dict(version=version, design_id='invented', analysis=dict(clock='annual', controls='not_yet_treated'),
                    support=dict(variants=[dict(variant=v) for v in variants]))

    def check(self, registered):
        from scripts.l2_pipeline.forecast_slots import validate_contract
        with patch('scripts.l2_pipeline.contract_card.registered_contract_card', return_value=registered):
            validate_contract(self.root, ['Card'])

    def proof(self, **changes):
        value = dict(schema='l2_unchanged_forecast_import_v1', primary_target_unchanged=True, target_spec_sha256=self.sha(self.spec), personas=['Card'])
        return ensure_once(self.root / 'FORECAST_IMPORT.json', dict(value, **changes))

    def test_only_the_named_support_row_is_ignored(self):
        self.proof(added_sensitivity='added'); self.check(self.card('v3', 'primary', 's1', 'added'))
        for bad in (self.card('v3', 'primary', 's1', 'added', 'other'), self.card('v3', 'primary', 's1', 'other')):
            with self.assertRaisesRegex(ValueError, 'differs from registered'):self.check(bad)
        changed = self.card('v3', 'primary', 's1', 'added'); changed['analysis']['clock'] = 'x'
        with self.assertRaisesRegex(ValueError, 'differs from registered'):self.check(changed)

    def test_without_the_named_variant_in_the_proof_an_extra_row_still_fails(self):
        self.proof()
        with self.assertRaisesRegex(ValueError, 'differs from registered'):self.check(self.card('v3', 'primary', 's1', 'added'))
        self.check(self.card('v3', 'primary', 's1'))


if __name__ == '__main__':
    unittest.main()
