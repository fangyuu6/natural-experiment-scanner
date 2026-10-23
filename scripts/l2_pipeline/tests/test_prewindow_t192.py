"""T192 (HUB_RULING_20261020_T192): history rows with pre_window_bound=true are already-treated-before-window states.

They are excluded from both arms and their year never feeds timing (cohort, event time, S3/RI counts, reconcile years,
audit sample, L3 export). Outcome-blind: rows and cards only.
"""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from scripts.l2_pipeline import prewindow, c42, c64_metadata, support, biennial, c70_l3, autopilot_history
from scripts.l2_pipeline.stages import s1_treatment as s1, s1_reconciliation as rec, s3_support
from scripts.l2_pipeline.intake import official_history
from scripts.l2_pipeline.agents import history as history_agent

MECH = 'mandatory X'


def row(unit, status='treated', year=None, bound=False, first_full=False, **extra):
    """Exact rows follow the C11 clock: best_evidence = operation year, start_period = operation year + 1."""
    r = dict(unit=unit, status=status, mechanism=MECH, locator='loc', source_id='s', confidence='high')
    if status == 'never_treated':
        r['start_period'] = None
    elif bound:
        r.update(start_period=year, best_evidence_start_year=year, pre_window_bound=True, evidence_status='known_start')
    else:
        r.update(start_period=year + 1, best_evidence_start_year=year, evidence_status='known_start')
    r.update(extra)
    return r


UNIVERSE = ['A', 'B', 'C', 'D', 'E']


def compare(a, b, universe=None):
    return s1.reconcile(a, b, universe or [r['unit'] for r in a])


class ReconcileTests(unittest.TestCase):
    def test_both_bound_with_different_years_match_and_neither_year_is_a_start(self):
        out = compare([row('A', year=1994, bound=True)], [row('A', year=1998, bound=True)])
        self.assertEqual(out['gaps'], [])
        self.assertEqual(out['counts'], {'known_match': 1})
        accepted = out['accepted'][0]
        self.assertTrue(prewindow.is_bound(accepted))
        self.assertEqual(accepted['pre_window_bound_years'], [1994, 1998])

    def test_bound_and_exact_year_not_after_the_bound_match_either_order(self):
        for a, b in ((row('A', year=1994, bound=True), row('A', year=1990)), (row('A', year=1990), row('A', year=1994, bound=True)),
                     (row('A', year=1994, bound=True), row('A', year=1994))):
            out = compare([a], [b])
            self.assertEqual(out['gaps'], [], (a, b))
            self.assertTrue(prewindow.is_bound(out['accepted'][0]))
            self.assertEqual(out['accepted'][0]['start_period'], 1994)          # the bound row, never the exact one
            self.assertNotIn(out['accepted'][0]['start_period'], (1991, 1995))

    def test_exact_year_after_the_bound_is_a_conflict(self):
        out = compare([row('A', year=1994, bound=True)], [row('A', year=1996)])
        self.assertEqual([g['kind'] for g in out['gaps']], ['known_conflict'])
        self.assertEqual(out['accepted'], [])

    def test_bound_against_never_treated_unknown_or_a_window_year_stays_a_gap(self):
        unknown = dict(unit='A', status='unknown', start_period=None, reason='r')
        for other, kind in ((row('A', status='never_treated'), 'known_conflict'), (row('A', year=2006), 'known_conflict'),
                            (unknown, 'one_sided_missing')):
            out = compare([row('A', year=1994, bound=True)], [other], ['A'])
            self.assertEqual([g['kind'] for g in out['gaps']], [kind])
            self.assertEqual(out['accepted'], [])

    def test_mixed_mechanisms_and_interruptions_follow_the_ordinary_rules(self):
        other = row('A', year=1994, bound=True, mechanism='other')
        self.assertEqual([g['kind'] for g in compare([row('A', year=1994, bound=True)], [other])['gaps']], ['known_conflict'])
        flagged = row('A', year=1994, bound=True, observed_interruptions=[dict(start='1996-01-01')])
        self.assertTrue(compare([flagged], [row('A', year=1994, bound=True)])['gaps'])

    def test_ordinary_rows_are_unchanged(self):
        out = compare([row('A', year=2008), row('B', status='never_treated')], [row('A', year=2008), row('B', status='never_treated')])
        self.assertEqual(out['counts'], {'known_match': 2})
        self.assertFalse(any(prewindow.is_bound(r) for r in out['accepted']))
        out = compare([row('A', year=2008)], [row('A', year=2009)])
        self.assertEqual([g['kind'] for g in out['gaps']], ['known_conflict'])

    def test_schema_acceptance_and_guards(self):
        for bad in (dict(pre_window_bound='yes'), dict(pre_window_bound=True, best_evidence_start_year=1990),
                    dict(pre_window_bound=True, best_evidence_start_year=None)):
            with self.assertRaises(ValueError):
                s1.reconcile([dict(row('A', year=1994, bound=True), **bad)], [row('A', year=1994, bound=True)], ['A'])
        # false / absent / a non-treated status never count as bound
        self.assertFalse(prewindow.is_bound(dict(row('A', year=1994), pre_window_bound=False)))
        self.assertFalse(prewindow.is_bound(dict(unit='A', status='unknown', start_period=None, pre_window_bound=True)))
        compare([dict(unit='A', status='unknown', start_period=None, pre_window_bound=True)], [row('A', status='never_treated')], ['A'])

    def test_audit_sample_never_draws_a_bound_state(self):
        rows = [row('S%d' % i, year=2000 + i) for i in range(12)] + [row('Z%d' % i, year=1990, bound=True) for i in range(5)]
        for seed in range(25):
            self.assertFalse({u for u in s1.audit_sample(rows, seed) if u.startswith('Z')})
        plain = [r for r in rows if not prewindow.is_bound(r)]
        self.assertEqual(s1.audit_sample(rows, 7), s1.audit_sample(plain, 7))


class WindowTests(unittest.TestCase):
    def test_late_bound_year_becomes_a_gap_not_an_exclusion_by_bound(self):
        cmp = compare([row('A', year=2006, bound=True), row('B', year=1990, bound=True)],
                      [row('A', year=2006, bound=True), row('B', year=1990, bound=True)])
        prewindow.enforce_window(cmp, 2000)
        self.assertEqual([r['unit'] for r in cmp['accepted']], ['B'])
        self.assertEqual(cmp['gaps'], [dict(unit='A', kind='pre_window_bound_invalid')])
        self.assertEqual(cmp['counts']['known_match'], 1)

    def test_collector_rejects_a_bound_year_at_or_after_the_window_and_a_bad_status(self):
        job = SimpleNamespace(contract=dict(source_sha256='h', manual=dict(universe=['A']), target_window=[2000, 2020]))
        def value(r):
            return dict(history_output_schema='official_history_v9_1', source_pack_sha256='h', s1_ready=False, execution_authorized=False,
                        research_log=[dict(unit='A', query='q', finding='f')], time_spent_seconds=1,
                        sources=[dict(source_id='s', url='https://example.org/x', source_type='official')],
                        records=[dict(r, evidence_refs=[dict(source_id='s', locator='l', quote='q', supports='start_year', year=r.get('best_evidence_start_year'))])])
        good = row('A', year=1994, bound=True)
        history_agent.validate_history_output(job, value(good))
        for bad in (dict(good, start_period=2000, best_evidence_start_year=2000), dict(good, status='never_treated', start_period=None)):
            with self.assertRaises(ValueError):
                history_agent.validate_history_output(job, value(bad))
        # without the flag the exact-year rule is unchanged
        history_agent.validate_history_output(job, value(row('A', year=2001)))

    def test_evidence_downgrade_drops_the_flag(self):
        from scripts.l2_pipeline.agents import evidence_fetch as ef
        value = dict(records=[row('A', year=1994, bound=True)])
        decision = dict(unit='A', status='treated', known=False, basis=None, flags=[], detail='x')
        with mock.patch.object(ef, 'strict_packet', return_value=object()), mock.patch.object(ef, '_entries', return_value=[]), \
                mock.patch.object(ef, 'evaluate_rows', return_value=([decision], {})), \
                mock.patch.object(ef, 'evidence_report', return_value=dict(rule='r')):
            out, _ = ef.apply_evidence_rule(SimpleNamespace(), value)
        r = out['records'][0]
        self.assertEqual(r['status'], 'unknown')
        self.assertNotIn('pre_window_bound', r)
        self.assertFalse(prewindow.is_bound(r))


class ExclusionTests(unittest.TestCase):
    def cfg(self):
        return dict(universe=['A', 'B', 'C'], excluded_units=[], excluded_conflict_states=[], excluded_conflict_state_reasons={})

    def test_c42_excludes_a_bound_state_from_both_arms_with_its_reason(self):
        cmp = dict(accepted=[row('A', year=1994, bound=True), row('B', year=2008)], gaps=[])
        cfg = c42.exclusion_scope(self.cfg(), cmp, [dict(records=[]), dict(records=[])])
        self.assertEqual(cfg['excluded_units'], ['A'])
        self.assertEqual(cfg['excluded_conflict_states'], ['A'])
        self.assertEqual(cfg['excluded_conflict_state_reasons'], {'A': ['pre_window_bound']})

    def test_c42_without_bound_rows_is_unchanged(self):
        cmp = dict(accepted=[row('B', year=2008)], gaps=[])
        cfg = c42.exclusion_scope(self.cfg(), cmp, [dict(records=[]), dict(records=[])])
        self.assertEqual((cfg['excluded_units'], cfg['excluded_conflict_state_reasons']), ([], {}))

    def test_engine_records_skip_excluded_bound_rows_and_refuse_retained_ones(self):
        rows = [row('A', year=1994, bound=True), row('B', year=2008)]
        out = official_history.engine_records(rows, {'A'})
        self.assertEqual([r['unit'] for r in out], ['B'])
        self.assertEqual(out[0]['start_period'], 2008)          # operation year; unchanged clock for ordinary rows
        with self.assertRaises(ValueError):
            official_history.engine_records(rows, set())

    def test_reconcile_report_counts_bound_states_out_of_cohorts_controls_and_dated_starts(self):
        years = list(range(2000, 2021))
        def card(extra=None):
            return SimpleNamespace(payload=dict(outcome=dict(years=[2000, 2020], period_years=years), treatment=dict(start_period_kind='operative_year'),
                                                analysis=dict(min_pre_periods=3, min_controls=5, min_treated=2, min_cohorts=2, min_post_periods=2)))
        base = [row('T1', year=2008), row('T2', year=2008), row('T3', year=2012)] + [row('N%d' % i, status='never_treated') for i in range(6)]
        plain = rec.v4_window_check(card(), dict(accepted=base, gaps=[]))
        with_bound = rec.v4_window_check(card(), dict(accepted=base + [row('P1', year=1990, bound=True), row('P2', year=1985, bound=True)], gaps=[]))
        self.assertEqual(with_bound['pre_window_bound_units'], ['P1', 'P2'])
        for key in ('treated_cohorts', 'retained_treated_units', 'never_treated_units', 'dated_treated_count', 'dated_cohort_count', 'feasible'):
            self.assertEqual(plain[key], with_bound[key], key)
        self.assertTrue(all('P1' not in c['units'] and 'P2' not in c['units'] for c in with_bound['treated_cohorts']))
        self.assertNotIn('pre_window_bound_units', plain)

    def test_not_yet_treated_pool_never_contains_a_bound_state(self):
        years = list(range(2000, 2021))
        card = SimpleNamespace(payload=dict(outcome=dict(years=[2000, 2020], period_years=years), treatment={},
                                            analysis=dict(min_pre_periods=3, min_controls=5, min_treated=2, min_cohorts=2, min_post_periods=2)))
        rows = [row('T%d' % i, year=2005 + i) for i in range(8)] + [row('P', year=1990, bound=True)]
        out = rec.v4_window_check(card, dict(accepted=rows, gaps=[]))
        self.assertEqual(out['comparison_group'], 'not_yet_treated')
        for c in out['treated_cohorts']:
            for controls in c.get('comparison_cells', {}).values():
                self.assertNotIn('P', controls)

    def test_late_bound_year_blocks_the_v4_window(self):
        card = SimpleNamespace(payload=dict(outcome=dict(years=[2000, 2020]), treatment={}, analysis=dict(min_pre_periods=3, min_controls=5)))
        out = rec.v4_window_check(card, dict(accepted=[row('P', year=2003, bound=True)] + [row('N%d' % i, status='never_treated') for i in range(6)], gaps=[]))
        self.assertFalse(out['feasible'])
        self.assertIn('pre_window_bound year not before the window start 2000', out['reason'])


class TimingGuardTests(unittest.TestCase):
    def treatment(self):
        return [row('A', year=2008), row('P', year=1990, bound=True), row('N', status='never_treated')]

    def ledger(self):
        return dict(as_of_date='2002-12-31', interruption_coverage_certified=True, onset_years={'P': 2001},
                    searches=[dict(unit='P', searched_through='2002-12-31', evidence_ref='search')],
                    rows=[dict(unit='P', year=y, status='in_force') for y in (2001, 2002)],
                    blind_attestation=dict(estimate_read=False, forecast_read=False), coders=['c1', 'c2'])

    def test_no_timing_path_accepts_a_bound_row(self):
        analysis = dict(min_pre_periods=1, min_controls=1, min_treated=1, min_cohorts=1)
        with self.assertRaisesRegex(ValueError, "pre_window_bound rows must not reach"):
            support.operation_years(self.treatment(), False)
        with self.assertRaisesRegex(ValueError, "pre_window_bound rows must not reach"):
            support.standard_annual_declaration(self.treatment(), analysis, list(range(2000, 2012)))
        with self.assertRaisesRegex(ValueError, "pre_window_bound rows must not reach"):
            support.support_check({(u, t) for u in 'APN' for t in range(2000, 2012)}, self.treatment(), analysis, period_years=list(range(2000, 2012)))
        with self.assertRaisesRegex(ValueError, "pre_window_bound rows must not reach"):
            s3_support.variant_treatment(self.treatment(), dict(treatment={}))
        with self.assertRaisesRegex(ValueError, "pre_window_bound rows must not reach"):
            biennial.normalize(self.treatment(), analysis, [2000, 2002, 2004], False)
        with self.assertRaisesRegex(ValueError, "pre_window_bound rows must not reach"):
            c70_l3.validate_interruption_ledger(self.ledger(), ['P'], dict(records=[row('P', year=1990, bound=True)]))

    def test_without_the_bound_row_the_cohorts_are_unchanged(self):
        analysis = dict(min_pre_periods=1, min_controls=1, min_treated=1, min_cohorts=1)
        rows = [r for r in self.treatment() if not prewindow.is_bound(r)]
        out = support.standard_annual_declaration(rows, analysis, list(range(2000, 2012)))
        self.assertEqual(out['cohorts'], {'A': 2009})
        self.assertNotIn('P', out['never_controls'])

    def test_a_downgraded_flag_row_is_not_bound_and_passes_nothing_as_timing(self):
        unknown = dict(unit='P', status='unknown', start_period=None, pre_window_bound=True)
        support.operation_years([unknown], False)   # not bound: no refusal, and it has no start to read


class GeographyTests(unittest.TestCase):
    def card(self, reasons=None):
        return dict(outcome=dict(years=[2000, 2010], frequency='annual'),
                    treatment=dict(universe=['A', 'N', 'P'], excluded_units=['P'] if reasons else [], excluded_conflict_states=['P'] if reasons else [],
                                   excluded_conflict_state_reasons=reasons or {}, window=dict(start_year=2000, end_year=2010)))

    def records(self):
        return [dict(unit='A', status='treated', start_period=2005, operative_intervals=[dict(start_year=2005, end_year=2020)]),
                dict(unit='N', status='never_treated', start_period=None)]

    def test_c42_reason_marks_the_bound_state_as_a_known_pre_window_adopter(self):
        treatment = dict(audit=dict(passed=True), coverage=dict(start_year=1990, end_year=2020), records=self.records())
        out = c64_metadata.geography(self.card({'P': ['pre_window_bound']}), treatment)
        self.assertEqual(out['always_treated_units'], ['P'])
        self.assertEqual(out['U'], ['A', 'N'])
        self.assertNotIn('P', out['unresolved_baseline_units'])

    def test_a_retained_bound_row_is_always_treated_and_never_dated(self):
        treatment = dict(audit=dict(passed=True), coverage=dict(start_year=1990, end_year=2020),
                         records=self.records() + [row('P', year=1990, bound=True)])
        out = c64_metadata.geography(self.card(), treatment)
        self.assertEqual(out['always_treated_units'], ['P'])
        self.assertEqual(out['U'], ['A', 'N'])

    def test_other_reasons_do_not_mark_always_treated(self):
        treatment = dict(audit=dict(passed=True), coverage=dict(start_year=1990, end_year=2020), records=self.records())
        out = c64_metadata.geography(self.card({'P': ['known_conflict']}), treatment)
        self.assertEqual(out['always_treated_units'], [])


class PrepareTests(unittest.TestCase):
    def test_bound_rows_with_different_years_do_not_raise_a_best_evidence_conflict(self):
        """The prepare() best_evidence loop skips bound rows (source check of the guard, plus the behaviour on the accepted row)."""
        import inspect
        src = inspect.getsource(autopilot_history.prepare)
        self.assertIn('prewindow.is_bound(row)', src)
        self.assertIn('prewindow.enforce_window', src)
        cmp = compare([row('A', year=1994, bound=True)], [row('A', year=1998, bound=True)])
        self.assertTrue(prewindow.is_bound(cmp['accepted'][0]))

    def test_treatment_json_names_the_bound_units_only_when_there_are_any(self):
        import inspect
        src = inspect.getsource(autopilot_history.accept)
        self.assertIn("pre_window_bound_units", src)
        self.assertEqual(prewindow.bound_units([row('A', year=2008)]), [])


# --- end to end through the real emitter, collector, prepare and accept (fixtures of test_autopilot) -----------------------------

from scripts.l2_pipeline.tests.test_autopilot import OfficialBridgeTests as _Bridge, read as _read   # noqa: E402


class BoundEndToEnd(_Bridge):
    def make_bound(self, years):
        from scripts.l2_pipeline.agents import collect_job
        for job, year in zip(self.jobs, years):
            out = __import__('pathlib').Path(job.output_path)
            value = _read(out)
            record = next(r for r in value['records'] if r['unit'] == 'U2')
            record.update(start_period=year, best_evidence_start_year=year, pre_window_bound=True)
            for ref in record['evidence_refs']:
                ref['year'] = year
            out.write_text(json.dumps(value))
            for name in ('COLLECTION.json', 'NORMALIZATION.json', 'NORMALIZED_HISTORY.json'):
                (out.parent / name).unlink(missing_ok=True)
            collect_job(job.job_path)

    def test_bound_state_is_excluded_from_both_arms_and_never_audited_or_dated(self):
        from scripts.l2_pipeline.autopilot_history import accept
        self.policy['excluded_units'] = ['U0']
        self.make_bound((1990, 1993))
        job = self.prepare()
        report = _read(self.ctx.design_dir / 'RECONCILIATION.json')
        self.assertEqual(report['excluded_conflict_state_reasons']['U2'], ['pre_window_bound'])
        self.assertNotIn('best_evidence_year_conflict', json.dumps(report['excluded_conflict_state_reasons']))
        self.assertNotIn('U2', job.contract['sample'])
        self.assertEqual(len(job.contract['sample']), 10)
        self.assertNotIn('U2', [r['unit'] for r in report['audit_eligible_records']])
        self.assertTrue(prewindow.is_bound(next(r for r in report['historical_comparison']['accepted'] if r['unit'] == 'U2')))
        self.assertEqual(report['counts']['retained_treated'], 11)     # U0..U11 without U2
        self.audit(job)
        result = accept(self.ctx, job.job_path)
        self.assertEqual(result.disposition, 'ready')
        treatment = _read(self.ctx.design_dir / 'TREATMENT.json')
        self.assertNotIn('U2', [r['unit'] for r in treatment['records']])
        self.assertIn('U2', treatment['excluded_primary_units'])
        self.assertEqual(treatment['pre_window_bound_units'], ['U2'])
        cfg = c42.effective_context(self.ctx).card.payload['treatment']
        self.assertIn('U2', cfg['excluded_units'])
        self.assertEqual(c64_metadata.geography(c42.effective_context(self.ctx).card.payload, treatment)['always_treated_units'], ['U2'])

    def test_bound_year_at_or_after_the_window_is_rejected_by_the_collector(self):
        with self.assertRaises(ValueError):
            self.make_bound((1990, 1994))

    def test_no_bound_rows_leaves_treatment_json_without_the_new_key(self):
        from scripts.l2_pipeline.autopilot_history import accept
        job = self.prepare(); self.audit(job)
        accept(self.ctx, job.job_path)
        self.assertNotIn('pre_window_bound_units', _read(self.ctx.design_dir / 'TREATMENT.json'))


for _name in dir(_Bridge):
    if _name.startswith('test') and _name not in BoundEndToEnd.__dict__:
        setattr(BoundEndToEnd, _name, None)


if __name__ == '__main__':
    unittest.main()
