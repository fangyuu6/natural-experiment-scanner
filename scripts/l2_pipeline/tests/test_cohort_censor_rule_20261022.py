"""R-S3C1: cohort that loses one member after a certified statutory interruption (HUB_RULING_20261022_S3_COHORT). Invented records only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.l2_pipeline import cohort_censor_rule as C
from scripts.l2_pipeline.support import support_check, ENGINE_MINIMUMS
from scripts.l2_pipeline.storage import sha

ID = 'b1011c_eers_electric'
BLOCK = dict(version='R-S3C1', min_share=0.75, min_members=4, ruling_sha256='a' * 64, statement=C.STATEMENT)
ANALYSIS = dict(min_pre_periods=3, min_controls=5, min_cohorts=2, min_treated=6)
KIND = dict(kind='repeal', source_ids=['s1'])


def rec(unit, status, start=None, **kw):
    return dict(dict(unit=unit, status=status, start_period=start), **kw)


def censored(**kw):
    return dict(censor_after_period=2010, certified_interruption=True, observed_interruptions=[dict(KIND, **kw)], status='treated', start_period=2005)


def panel(n_a=5, censor=1, interruption=None, extra_censor_at=None, early_gap=False):
    """Cohort A (2005, n_a members, `censor` of them interrupted after 2010), cohort B (2008, 6 members), 8 never-treated controls."""
    treatment = []
    for i in range(n_a):
        kw = {}
        if i < censor:
            kw = dict(censor_after_period=2010, certified_interruption=True, observed_interruptions=[interruption or dict(KIND)])
        treatment.append(rec('A%d' % i, 'treated', 2005, **kw))
    treatment += [rec('B%d' % i, 'treated', 2008) for i in range(6)]
    treatment += [rec('C%d' % i, 'never_treated') for i in range(8)]
    years = list(range(1995, 2016))
    keys = {(r['unit'], t) for r in treatment for t in years
            if not (r.get('censor_after_period') and t > r['censor_after_period'])}
    if early_gap:
        keys.discard(('A0', 2007))       # the member is missing BEFORE its own cutoff: a data gap
    return treatment, keys, years


def check(treatment, keys, years, block=BLOCK, **analysis):
    a = dict(ANALYSIS, **analysis)
    if block is not None:
        a['cohort_censor_rule'] = block
    return support_check(keys, treatment, a, period_years=years)


class DefaultPathTests(unittest.TestCase):
    def test_without_block_the_cohort_stays_unsupported_and_no_new_keys(self):
        t, k, y = panel()
        r = check(t, k, y, block=None)
        self.assertFalse(r['passed'])
        self.assertEqual(r['issues']['unsupported_post_cells'], 5)
        self.assertNotIn('cohort_censor', r)

    def test_full_cohort_result_identical_with_and_without_block(self):
        t, k, y = panel(censor=0)
        a = check(t, k, y, block=None)
        b = check(t, k, y)
        self.assertTrue(a['passed'])
        self.assertEqual({x: a[x] for x in a}, {x: b[x] for x in b if x != 'cohort_censor'})

    def test_thresholds_unchanged(self):
        self.assertEqual(ENGINE_MINIMUMS, dict(min_treated=6, min_cohorts=2, min_controls=5, min_pre_periods=3))
        self.assertEqual(C.MIN_MEMBERS, 4)
        self.assertEqual(str(C.MIN_SHARE), '3/4')

    def test_exports_default_has_no_cohort_censor_key(self):
        from scripts.l2_pipeline.stages.s7_export import exports
        import inspect
        self.assertIn('cohort_censor=None', str(inspect.signature(exports)).replace(' ', ''))


class RuleAppliesTests(unittest.TestCase):
    def test_one_statutory_censor_in_five_passes_with_engine_agreement(self):
        t, k, y = panel()
        r = check(t, k, y)
        self.assertTrue(r['passed'], r['issues'])
        self.assertEqual(r['issues'], {})
        info = r['cohort_censor']
        self.assertEqual(info['rule'], 'R-S3C1')
        self.assertEqual(len(info['cells']), 5)   # 2011..2015
        for c in info['cells']:
            self.assertEqual((c['n_treated'], c['n_roster'], c['share'], c['missing']), (4, 5, '4/5', ['A0']))
        self.assertEqual(info['cohorts'][0]['roster'], ['A0', 'A1', 'A2', 'A3', 'A4'])
        later = [c for c in r['cells'] if c['cohort'] == 2005 and c['period'] > 2010]
        self.assertTrue(later and all(c['treated'] == ['A1', 'A2', 'A3', 'A4'] for c in later))

    def test_member_floor_three_remaining_fails_even_at_exactly_075(self):
        t, k, y = panel(n_a=4)      # 3 of 4 = exactly 0.75 but only 3 members
        r = check(t, k, y)
        self.assertFalse(r['passed'])
        self.assertEqual(r['issues']['unsupported_post_cells'], 5)
        self.assertEqual(r['cohort_censor']['cells'], [])

    def test_four_remaining_of_five_passes_boundary(self):
        self.assertEqual(C.cell_decision(BLOCK, ['a', 'b', 'c', 'd', 'e'], ['b', 'c', 'd', 'e'], self.units(['a']), 2012), 'relaxed')

    def units(self, censored_units):
        base = {u: dict(unit=u, status='treated', start_period=2005) for u in 'abcdefgh'}
        for u in censored_units:
            base[u] = dict(base[u], **censored())
        return base

    def test_share_floor_boundaries_exact_integers(self):
        units = self.units(['a'])
        roster = list('abcdefgh')
        # 7 of 8 and 4 of 5 pass; one censored member can never push the share below 0.75 once 4 members remain.
        self.assertEqual(C.cell_decision(BLOCK, roster, roster[1:], units, 2012), 'relaxed')
        self.assertEqual(C.cell_decision(BLOCK, roster[:4], roster[1:4], units, 2012), 'unsupported')   # 3 of 4: members floor
        # two censored: refused whatever the share (at most one per cohort)
        units2 = self.units(['a', 'b'])
        self.assertEqual(C.cell_decision(BLOCK, roster, roster[2:], units2, 2012), 'unsupported')

    def test_two_censored_in_one_cohort_refused(self):
        t, k, y = panel(n_a=8, censor=2)
        r = check(t, k, y)
        self.assertFalse(r['passed'])
        self.assertEqual(r['issues']['unsupported_post_cells'], 5)

    def test_non_statutory_or_uncertified_censor_refused(self):
        for bad in (dict(kind='data_gap', source_ids=['s1']), dict(kind='repeal', source_ids=[]), dict(kind='repeal'), dict(source_ids=['x'])):
            t, k, y = panel(interruption=bad)
            self.assertFalse(check(t, k, y)['passed'], bad)
        t, k, y = panel()
        for r in t:
            r.pop('certified_interruption', None)
        self.assertFalse(check(t, k, y)['passed'])
        self.assertFalse(C.qualifies(dict(status='treated', censor_after_period=2010, certified_interruption=True, observed_interruptions=[])))
        for kind in ('repeal', 'termination', 'freeze'):
            self.assertTrue(C.qualifies(dict(status='treated', censor_after_period=2010, certified_interruption=True, observed_interruptions=[dict(kind=kind, source_ids=['s'])])))
        self.assertTrue(C.qualifies(dict(status='treated', censor_after_period=2012, certified_interruption=True, observed_interruptions=[dict(type='repeal', source_ids=['s'])])))

    def test_member_missing_before_its_cutoff_is_a_data_gap_and_refused(self):
        t, k, y = panel(early_gap=True)
        self.assertFalse(check(t, k, y)['passed'])

    def test_noncensored_missing_member_never_covered(self):
        t, k, y = panel(censor=0)
        k.discard(('A1', 2012))
        r = check(t, k, y)
        self.assertFalse(r['passed'])
        self.assertEqual(r['issues']['unsupported_post_cells'], 1)

    def test_control_floor_unchanged(self):
        t, k, y = panel()
        k = {x for x in k if not (x[0] in ('C0', 'C1', 'C2', 'C3') and x[1] >= 2011)}
        self.assertFalse(check(t, k, y)['passed'])

    def test_non_never_treated_comparison_refused(self):
        t, k, y = panel()
        with self.assertRaisesRegex(ValueError, 'never-treated'):
            check(t, k, y, controls='not_yet_treated', comparison_group='not_yet_treated')


class FixedCompositionTests(unittest.TestCase):
    def test_fixed_run_drops_cells_after_cutoff_without_relaxing(self):
        from scripts.l2_pipeline.stages.s3_support import variant_treatment
        t, k, y = panel()
        card = dict(treatment=dict(cohort_truncate_after={u: 2010 for u in ('A0', 'A1', 'A2', 'A3', 'A4')}))
        t2 = variant_treatment(t, card)
        k2 = {x for x in k if not (x[0].startswith('A') and x[1] > 2010)}
        r = check(t2, k2, y, block=dict(BLOCK, mode='fixed_composition'))
        self.assertTrue(r['passed'], r['issues'])
        self.assertEqual(max(c['period'] for c in r['cells'] if c['cohort'] == 2005), 2010)
        self.assertEqual(r['cohort_censor']['cells'], [])

    def test_truncate_rejects_non_treated_or_non_integer(self):
        from scripts.l2_pipeline.stages.s3_support import variant_treatment
        t, _, _ = panel()
        for bad in ({'C0': 2010}, {'A0': '2010'}, {}):
            with self.assertRaises(ValueError):
                variant_treatment(t, dict(treatment=dict(cohort_truncate_after=bad)))

    def test_truncate_absent_leaves_records_untouched(self):
        from scripts.l2_pipeline.stages.s3_support import variant_treatment
        t, _, _ = panel()
        self.assertEqual(variant_treatment(copy.deepcopy(t), dict(treatment={})), t)


class CardRuleTests(unittest.TestCase):
    def test_valid_block(self):
        self.assertEqual(C.validate_block(BLOCK), 'primary')
        self.assertEqual(C.validate_block(dict(BLOCK, mode='fixed_composition')), 'fixed_composition')

    def test_other_floors_or_wording_refused(self):
        for bad in (dict(min_share=0.8), dict(min_share=0.7), dict(min_members=3), dict(min_members=5), dict(min_members=True),
                    dict(version='R3.1'), dict(statement='pre-registered'), dict(ruling_sha256='xyz'), dict(mode='other')):
            with self.assertRaises(ValueError, msg=str(bad)):
                C.validate_block(dict(BLOCK, **bad))
        with self.assertRaises(ValueError):
            C.validate_block({k: v for k, v in BLOCK.items() if k != 'statement'})
        with self.assertRaises(ValueError):
            C.validate_block(dict(BLOCK, extra=1))

    def test_design_whitelist(self):
        self.assertEqual(len(C.ALLOWED_IDS), 5)
        self.assertIsNone(C.block_from_analysis({}, 'anything'))
        self.assertEqual(C.block_from_analysis(dict(cohort_censor_rule=BLOCK), ID), BLOCK)
        with self.assertRaisesRegex(ValueError, 'outside the ruling list'):
            C.block_from_analysis(dict(cohort_censor_rule=BLOCK), 'b1010_open_container')
        with self.assertRaises(ValueError):
            C.check_card(dict(design_id='b1010_open_container', analysis=dict(cohort_censor_rule=BLOCK)))
        self.assertIsNone(C.check_card(dict(design_id='b1010_open_container', analysis={})))

    def test_sensitivity_builder(self):
        oh = dict(censored(), unit='OH', start_period=2009, censor_after_period=2014)
        recs = [oh] + \
               [rec(u, 'treated', 2009) for u in ('HI', 'MD')] + [rec('C', 'never_treated')]
        cen, ros = C.censored_and_roster(recs)
        self.assertEqual(cen, {'OH': 2014})
        self.assertEqual(ros['OH'], ['HI', 'MD', 'OH'])
        fixed, dropped = C.build_card_sensitivities(cen, ros)
        self.assertEqual(fixed['changes']['treatment']['cohort_truncate_after'], {'HI': 2014, 'MD': 2014, 'OH': 2014})
        self.assertEqual(fixed['changes']['analysis'], dict(cohort_censor_rule=dict(mode='fixed_composition')))
        self.assertEqual(dropped['changes'], dict(treatment=dict(excluded_units=['OH'])))
        self.assertTrue(fixed['report_only'] and dropped['report_only'] and not fixed['gates_primary'])


class HashBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.card = self.tmp / 'CARD.json'
        self.card.write_text(json.dumps(dict(design_id=ID, version='v5')))
        t, k, y = panel()
        self.primary = check(t, k, y)
        self.payload = C.cells_payload(ID, sha(self.card), self.primary)
        from scripts.l2_pipeline.storage import write_once
        self.path = write_once(self.tmp / C.CELLS_FILE, self.payload)
        self.support = dict(variants=[dict(self.primary, variant='primary')], cohort_cells_sha256=sha(self.path))

    def test_payload_names_card_and_cells(self):
        self.assertEqual(self.payload['card_sha256'], sha(self.card))
        self.assertEqual(self.payload['tag'], 'cohort_member_censored')
        self.assertTrue(all('n_treated' in c and 'n_roster' in c for c in self.payload['cells']))
        self.assertTrue(C.verify_bound(self.tmp, self.card, self.support))

    def test_tampered_cells_card_or_support_refused(self):
        bad = json.loads(self.path.read_text())
        bad['cells'][0]['n_treated'] = 5
        self.path.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            C.verify_bound(self.tmp, self.card, self.support)

    def test_card_change_refused(self):
        self.card.write_text(json.dumps(dict(design_id=ID, version='v6')))
        with self.assertRaises(ValueError):
            C.verify_bound(self.tmp, self.card, self.support)

    def test_support_hash_mismatch_refused(self):
        with self.assertRaises(ValueError):
            C.verify_bound(self.tmp, self.card, dict(self.support, cohort_cells_sha256='0' * 64))

    def test_no_payload_without_censoring(self):
        t, k, y = panel(censor=0)
        self.assertIsNone(C.cells_payload(ID, 'x', check(t, k, y)))

    def test_export_block_tag_share_runs_and_verdict(self):
        reg = dict(primary=dict(result=dict(overall=dict(att=1.0, ci95=[0.5, 1.5]))),
                   rs3c1_fixed_composition=dict(result=dict(overall=dict(att=1.1, ci95=None))),
                   rs3c1_censored_member_dropped=dict(result=dict(overall=dict(att=0.9, ci95=[0.4, 1.6]))))
        out = C.export_block(self.tmp, dict(design_id=ID, analysis=dict(cohort_censor_rule=BLOCK)), dict(registered_results=reg))
        self.assertEqual((out['tag'], out['rule'], out['min_share_in_cells']), ('cohort_member_censored', 'R-S3C1', '4/5'))
        self.assertEqual(out['composition']['verdict'], 'stable')
        self.assertFalse(out['headline_withheld'])
        self.assertIn('mix cohorts of changing composition', out['mixing_statement'])
        self.assertEqual(out['cells_sha256'], sha(self.path))


class VerdictTests(unittest.TestCase):
    def run3(self, a, b, c, cib=None):
        return C.composition_sensitive(dict(att=a, ci95=[a - .1, a + .1]), dict(att=b, ci95=[b - .1, b + .1]), dict(att=c, ci95=cib or [c - .1, c + .1]))

    def test_stable(self):
        self.assertEqual(self.run3(1.0, 1.05, 0.95)['verdict'], 'stable')

    def test_sign_flip_vs_fixed_or_dropped(self):
        self.assertEqual(self.run3(1.0, -0.5, 1.0)['verdict'], 'composition_sensitive')
        self.assertEqual(self.run3(1.0, 1.0, -0.5)['verdict'], 'composition_sensitive')

    def test_primary_outside_interval_of_dropped(self):
        self.assertEqual(self.run3(1.0, 1.0, 2.0, cib=[1.8, 2.2])['verdict'], 'composition_sensitive')

    def test_near_zero_in_all_runs_interval_decides_not_sign(self):
        wide = lambda a: dict(att=a, ci95=[-1, 1])
        out = C.composition_sensitive(wide(0.1), wide(-0.1), wide(0.05))
        self.assertEqual(out['verdict'], 'stable')
        self.assertTrue(out['near_zero_in_all_runs'])

    def test_missing_run_not_evaluable_and_missing_ci_flagged(self):
        self.assertEqual(C.composition_sensitive(dict(att=1.0), {}, dict(att=1.0))['verdict'], 'not_evaluable')
        out = C.composition_sensitive(dict(att=1.0, ci95=[.5, 1.5]), dict(att=1.0), dict(att=1.0, ci95=None))
        self.assertTrue(out['ci_unavailable'])
        self.assertEqual(out['verdict'], 'stable')


class RulingHookTests(unittest.TestCase):
    def test_other_design_or_malformed_ruling_refused_before_any_io(self):
        old = dict(design_id='b1010_open_container', card='x')
        with self.assertRaisesRegex(ValueError, 'outside the ruling list'):
            C.validate_ruling(SimpleNamespace(repo='.'), old, dict(design_id='b1010_open_container'), {}, 'c')
        old = dict(design_id=ID, card='x')
        with self.assertRaisesRegex(ValueError, 'malformed'):
            C.validate_ruling(SimpleNamespace(repo='.'), old, dict(design_id=ID), dict(decision='accept'), 'c')

    def test_requeue_registers_status_and_rule(self):
        src = (Path(__file__).resolve().parents[1] / 'requeue.py').read_text()
        self.assertIn("'SUPERSEDED_S3_COHORT_RS3C1'", src)
        self.assertIn("implementation_rule')=='S3_COHORT_RS3C1'", src)


if __name__ == '__main__':
    unittest.main()
