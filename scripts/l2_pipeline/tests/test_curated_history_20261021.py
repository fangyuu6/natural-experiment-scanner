"""T218 (HUB_RULING_20261021_CURATED_HISTORY): curated-database history route. Outcome-blind: database rows and cards only."""
import copy
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from scripts.l2_pipeline import curated_history as ch, prewindow
from scripts.l2_pipeline.curated_history import Ineligible

REPO = Path(__file__).resolve().parents[3]
XLSX = REPO / 'data/l4/rand_firearm_law_database/raw/TL-A243-2-v4 State Firearm Law Database 6.0.xlsx'
SHA = '0293ce200822f10e1e537ba045192a64576b6f907217ec8049c20c38eb610141'
UNIVERSE = ['S%02d' % i for i in range(30)]
CLASS = 'test class'


def spec(**kw):
    s = dict(schema=ch.SCHEMA_MAPPING, mapping_id='m1', policy_family='pf', mechanism='mech', universe=list(UNIVERSE),
             window_start=1999, coverage_end_year=2024, transition_year_drop=True,
             database=dict(name='RAND State Firearm Law Database', version='6.0', xlsx_sha256=SHA),
             classes=[dict(law_class=CLASS, include=dict(subtype=['a', 'b']), exclude=[dict(subtype=['x'])])],
             candidate_log=[dict(timestamp='2026-10-21T00:00:00Z', candidate='c1', decision='chosen')], mappers=['A', 'B', 'C'])
    s.update(kw)
    return s


def drow(state, year, subtype='a', sup=None, law_id=None, month=3, day=4, note=None, disc=None, cls=CLASS):
    return dict(law_id=law_id or '%s%d' % (state, year), state=state, law_class=cls, subtype=subtype, guns='handgun',
                effect='restrictive', change='implement', note=note, year=year, month=month, day=day, citation='Cite %s %d' % (state, year),
                sup_year=sup, sup_month=None, sup_day=None, discrepancy=disc)


def rows_basic():
    out = []
    for i, state in enumerate(UNIVERSE[:20]):
        out.append(drow(state, 2000 + i % 15))
    out.append(drow('S20', 1990))                      # pre-window
    out.append(drow('S21', 1900, month=1, day=1, note='placeholder'))   # placeholder year, in force
    out.append(drow('S22', 2005, sup=2010))            # reversal
    out.append(drow('S23', 2001, sup=2003, law_id='S23a')); out.append(drow('S23', 2006, law_id='S23b'))   # gap
    out.append(drow('S24', 2030))                      # after coverage: ignored -> never treated
    out.append(drow('S25', 2002, subtype='x'))         # excluded subtype
    return out


def fresh(history, unit, year=None, status='treated', **kw):
    base = {r['unit']: r for r in history['accepted']}[unit]
    y = base['best_evidence_start_year'] if year is None else year
    return dict(unit=unit, status=status, best_evidence_start_year=y if status == 'treated' else None, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.spec = spec()
        self.h = ch.build_history(rows_basic(), self.spec)
        self.plan = ch.audit_plan(self.h, self.spec, 'design_x')

    def check(self, unit, status='match', phase='sample', **kw):
        return dict(unit=unit, status=status, official_locator='loc', phase=phase, **kw)

    def all_match(self, units):
        return [self.check(u) for u in units]


class TestSourceAndMapping(Base):
    def test_real_workbook_pinned_and_readable(self):
        if not XLSX.is_file():
            self.skipTest('workbook not mounted')
        self.assertEqual(ch.file_sha(XLSX), SHA)
        rows = ch.load_rows(XLSX)
        self.assertEqual(len(rows), 1902)
        self.assertEqual({r['state'] for r in rows} - {None}, {r['state'] for r in rows if r['state']})
        self.assertEqual(len({r['state'] for r in rows}), 51)

    def test_non_qualifying_databases_refused(self):
        for name in ('SPID', 'CSPP', 'NCSL tracker', 'Everytown'):
            s = spec(database=dict(name=name, version='1', xlsx_sha256=SHA))
            with self.assertRaises(Ineligible):
                ch.validate_mapping(s)
        with self.assertRaises(Ineligible):
            ch.validate_mapping(spec(database=dict(name='APIS', version='1', xlsx_sha256=SHA)))

    def test_wrong_version_hash_coverage_mappers_log(self):
        for bad in (spec(database=dict(name='RAND State Firearm Law Database', version='5.0', xlsx_sha256=SHA)),
                    spec(database=dict(name='RAND State Firearm Law Database', version='6.0', xlsx_sha256='0' * 64)),
                    spec(coverage_end_year=2026), spec(mappers=['A', 'B']), spec(candidate_log=[]), spec(window_start='1999'),
                    spec(universe=['A', 'A'])):
            with self.assertRaises(Ineligible):
                ch.validate_mapping(bad)

    def test_ambiguity_over_20_percent_is_ineligible(self):
        rows = [drow('S%02d' % i, 2000, subtype='z') for i in range(5)] + [drow('S10', 2000)]
        with self.assertRaises(Ineligible):
            ch.build_history(rows, self.spec)

    def test_mapping_hash_stable_and_sensitive(self):
        self.assertEqual(ch.mapping_sha(spec()), ch.mapping_sha(spec()))
        self.assertNotEqual(ch.mapping_sha(spec()), ch.mapping_sha(spec(mapping_id='m2')))


class TestBuild(Base):
    def by(self):
        return {r['unit']: r for r in self.h['accepted']}

    def test_exact_clock(self):
        r = self.by()['S00']
        self.assertEqual((r['status'], r['best_evidence_start_year'], r['start_period']), ('treated', 2000, 2001))
        self.assertEqual(r['db_provenance']['law_ids'], ['S002000'])

    def test_pre_window_bound_rows_pass_t192_validation(self):
        r = self.by()['S20']
        self.assertTrue(r['pre_window_bound'])
        self.assertEqual((r['start_period'], r['best_evidence_start_year']), (1990, 1990))
        prewindow.validate_row(r); prewindow.validate_window(r, 1999)

    def test_placeholder_year_is_bound_only(self):
        r = self.by()['S21']
        self.assertTrue(r['pre_window_bound']); self.assertEqual(r['start_period'], 1998)
        self.assertTrue(r['db_provenance']['flags']['placeholder_year'])

    def test_placeholder_year_not_in_force_excluded(self):
        rows = rows_basic() + [drow('S26', 1900, sup=1990, note='placeholder')]
        h = ch.build_history(rows, self.spec)
        self.assertEqual(h['exclusion_reasons']['S26'], 'placeholder_year_not_in_force_at_window_start')

    def test_reversal_and_reenactment_excluded(self):
        self.assertEqual(self.h['exclusion_reasons']['S22'], 'reversal')
        self.assertEqual(self.h['exclusion_reasons']['S23'], 'repeal_and_reenactment')
        self.assertNotIn('S22', self.by())

    def test_after_coverage_and_excluded_subtype_are_never_treated(self):
        self.assertEqual(self.by()['S24']['status'], 'never_treated')
        self.assertEqual(self.by()['S25']['status'], 'never_treated')
        self.assertIsNone(self.by()['S24']['start_period'])

    def test_never_treated_is_flagged_absence(self):
        self.assertTrue(self.by()['S29']['db_provenance']['absence_of_row'])

    def test_no_transition_drop(self):
        s = spec(transition_year_drop=False)
        h = ch.build_history(rows_basic(), s)
        r = {x['unit']: x for x in h['accepted']}['S00']
        self.assertEqual(r['start_period'], 2000)


class TestPlan(Base):
    def test_deterministic_and_unshoppable_seed(self):
        again = ch.audit_plan(ch.build_history(rows_basic(), spec()), spec(), 'design_x')
        self.assertEqual(again, self.plan)
        other = ch.audit_plan(self.h, self.spec, 'design_y')
        self.assertNotEqual(other['seed'], self.plan['seed'])
        s2 = spec(mapping_id='m2')
        self.assertNotEqual(ch.audit_plan(ch.build_history(rows_basic(), s2), s2, 'design_x')['seed'], self.plan['seed'])

    def test_size_rule(self):
        n = self.plan['n_adopting']
        self.assertGreater(n, 12)
        self.assertGreaterEqual(self.plan['k'], max(10, -(-n // 4)))
        self.assertEqual(len(self.plan['sample']), self.plan['k'])

    def test_forced_strata(self):
        by = {r['unit']: r for r in self.h['accepted']}
        treated = sorted((r['best_evidence_start_year'], r['unit']) for r in by.values()
                         if r['status'] == 'treated' and not r.get('pre_window_bound'))
        sample = set(self.plan['sample'])
        self.assertTrue({u for _, u in treated[:2]} <= sample)
        self.assertTrue({u for _, u in treated[-2:]} <= sample)
        self.assertGreaterEqual(sum(by[u]['status'] == 'never_treated' for u in sample), 2)
        self.assertTrue(any(by[u].get('pre_window_bound') for u in sample))
        self.assertIn('S21', sample)   # placeholder

    def test_backup_pool_disjoint_and_random_part_listed(self):
        self.assertFalse(set(self.plan['backup_order']) & set(self.plan['sample']))
        self.assertEqual(self.plan['extension_size'], 5)
        for u in self.plan['random_part']:
            self.assertEqual(self.plan['strata'][u], ['random_draw'])

    def test_small_universe_is_census(self):
        s = spec(universe=UNIVERSE[:8])
        h = ch.build_history(rows_basic(), s)
        p = ch.audit_plan(h, s, 'd')
        self.assertTrue(p['census'])
        self.assertEqual(p['sample'], sorted(r['unit'] for r in h['accepted']))
        self.assertEqual(p['backup_order'], [])

    def test_packet_blind_and_chunked(self):
        card = dict(mechanism='m', treatment=dict(predicate='p', coverage_end='2024-12-31', start_period_kind='operative_year',
                                                  transition_year_drop=True))
        packet = ch.audit_packet(self.plan, card)
        text = json.dumps(packet)
        self.assertNotIn('S002000', text); self.assertNotIn('Cite', text); self.assertNotIn(SHA, text)
        with self.assertRaises(ValueError):
            ch.assert_blind(dict(units=['A'], db_provenance={}))
        specs = ch.audit_chunk_specs(dict(self.plan, sample=['U%d' % i for i in range(23)]))
        self.assertEqual([len(s['completion_units']) for s in specs], [10, 10, 3])
        for s in specs:
            self.assertEqual(set(s['chunk']), {'index', 'total', 'source_prefix'})


class TestClassifyEvaluate(Base):
    def by(self):
        return {r['unit']: r for r in self.h['accepted']}

    def test_classify(self):
        by, sp = self.by(), self.spec
        self.assertEqual(ch.classify(by['S00'], fresh(self.h, 'S00'), sp)['status'], 'match')
        c = ch.classify(by['S00'], fresh(self.h, 'S00', year=2003), sp)
        self.assertEqual((c['status'], c['corrected_start_period']), ('timing', 2004))
        self.assertEqual(ch.classify(by['S00'], fresh(self.h, 'S00', status='never_treated'), sp)['status'], 'predicate')
        self.assertEqual(ch.classify(by['S29'], fresh(self.h, 'S29', status='treated', year=2005), sp)['status'], 'predicate')
        self.assertEqual(ch.classify(by['S29'], fresh(self.h, 'S29', status='never_treated'), sp)['status'], 'match')
        self.assertEqual(ch.classify(by['S20'], fresh(self.h, 'S20', year=1985), sp)['status'], 'match')   # bound: any pre-window year
        self.assertEqual(ch.classify(by['S20'], fresh(self.h, 'S20', year=2001), sp)['status'], 'timing')
        self.assertEqual(ch.classify(by['S00'], None, sp)['status'], 'unverified')
        self.assertEqual(ch.classify(by['S00'], dict(fresh(self.h, 'S00'), operation_date='2000-07-01'), sp)['status'], 'detail')

    def test_all_match_passes(self):
        v = ch.evaluate(self.plan, self.all_match(self.plan['sample']))
        self.assertTrue(v['passed']); self.assertEqual(v['route'], 'db_audited')

    def test_predicate_error_fails_to_fresh(self):
        c = self.all_match(self.plan['sample']); c[0] = self.check(c[0]['unit'], 'predicate')
        v = ch.evaluate(self.plan, c)
        self.assertEqual((v['verdict'], v['route']), ('fail', 'fresh_coding'))

    def test_two_timing_errors_fail(self):
        c = self.all_match(self.plan['sample'])
        for i in (0, 1): c[i] = self.check(c[i]['unit'], 'timing', corrected_start_period=2001)
        self.assertEqual(ch.evaluate(self.plan, c)['verdict'], 'fail')

    def test_one_timing_error_needs_extension_then_passes(self):
        c = self.all_match(self.plan['sample']); c[0] = self.check(c[0]['unit'], 'timing', corrected_start_period=2001)
        v = ch.evaluate(self.plan, c)
        self.assertEqual((v['verdict'], v['route']), ('extend', 'extend'))
        ext = [self.check(u, phase='extension') for u in v['extension_units']]
        v2 = ch.evaluate(self.plan, c + ext)
        self.assertTrue(v2['passed'], v2)
        self.assertEqual(v2['known_start_errors'], 1)

    def test_extension_with_error_fails(self):
        c = self.all_match(self.plan['sample']); c[0] = self.check(c[0]['unit'], 'timing', corrected_start_period=2001)
        ext_units = ch.evaluate(self.plan, c)['extension_units']
        ext = [self.check(u, phase='extension') for u in ext_units]
        ext[0] = self.check(ext_units[0], 'detail', phase='extension')
        self.assertEqual(ch.evaluate(self.plan, c + ext)['verdict'], 'fail')

    def test_uncorrected_timing_error_fails(self):
        c = self.all_match(self.plan['sample']); c[0] = self.check(c[0]['unit'], 'timing')
        ext = [self.check(u, phase='extension') for u in ch.evaluate(self.plan, c)['extension_units']]
        v = ch.evaluate(self.plan, c + ext)
        self.assertEqual(v['verdict'], 'fail'); self.assertFalse(v['known_errors_corrected'])

    def test_common_cause_fails_even_with_extension(self):
        c = self.all_match(self.plan['sample'])
        c[0] = self.check(c[0]['unit'], 'timing', corrected_start_period=2001, cause='enactment_for_effective')
        c[1] = self.check(c[1]['unit'], 'timing', corrected_start_period=2001, cause='enactment_for_effective')
        self.assertTrue(ch.evaluate(self.plan, c)['systematic_cause'])
        self.assertEqual(ch.evaluate(self.plan, c)['verdict'], 'fail')

    def test_unverified_replaced_once_from_backup_order(self):
        c = self.all_match(self.plan['sample']); c[0] = self.check(c[0]['unit'], 'unverified')
        c = [x for x in c if x['status'] != 'unverified' or True]
        repl = self.plan['backup_order'][0]
        v = ch.evaluate(self.plan, c + [self.check(repl, phase='replacement')])
        self.assertTrue(v['passed']); self.assertEqual(v['replacements'], [repl])
        self.assertEqual(v['unverified_database_units'], [c[0]['unit']])
        self.assertEqual(v['unresolved'], 0)

    def test_replacement_out_of_order_rejected(self):
        c = self.all_match(self.plan['sample']); c[0] = self.check(c[0]['unit'], 'unverified')
        with self.assertRaises(ValueError):
            ch.evaluate(self.plan, c + [self.check(self.plan['backup_order'][3], phase='replacement')])

    def test_more_than_a_third_unverifiable_is_inconclusive(self):
        c = self.all_match(self.plan['sample'])
        for i in range(len(c) // 3 + 1): c[i] = self.check(c[i]['unit'], 'unverified')
        v = ch.evaluate(self.plan, c)
        self.assertEqual((v['verdict'], v['route'], v['passed']), ('inconclusive', 'not_evaluable', False))

    def test_three_details_need_extension(self):
        c = self.all_match(self.plan['sample'])
        for i in range(3): c[i] = self.check(c[i]['unit'], 'detail')
        self.assertEqual(ch.evaluate(self.plan, c)['verdict'], 'extend')

    def test_missing_or_duplicate_checks_rejected(self):
        c = self.all_match(self.plan['sample'])
        with self.assertRaises(ValueError): ch.evaluate(self.plan, c[1:])
        with self.assertRaises(ValueError): ch.evaluate(self.plan, c + [c[0]])
        bad = list(c); bad[0] = dict(c[0], official_locator=None)
        with self.assertRaises(ValueError): ch.evaluate(self.plan, bad)

    def test_corrections_keep_database_value_beside_official(self):
        unit = next(u for u in self.plan['sample'] if self.by()[u]['status'] == 'treated' and not self.by()[u].get('pre_window_bound'))
        c = self.check(unit, 'timing', corrected_start_period=2011)
        fixed = {r['unit']: r for r in ch.apply_corrections(self.h, [c], self.spec)}[unit]
        self.assertEqual((fixed['start_period'], fixed['best_evidence_start_year']), (2011, 2010))
        self.assertEqual(fixed['audit_correction']['database_value']['start_period'], self.by()[unit]['start_period'])
        self.assertEqual(self.by()[unit]['start_period'], {r['unit']: r for r in self.h['accepted']}[unit]['start_period'])  # input untouched


class TestDeclarationLedgerCertify(Base):
    def decl(self, **kw):
        d = dict(kind='curated_database', mapping_sha256='a' * 64, reason='access', rule=ch.RULING)
        d.update(kw)
        return {'automation': {'history_source': d}}

    def test_declaration(self):
        self.assertIsNone(ch.declaration({}))
        self.assertEqual(ch.declaration(self.decl())['kind'], 'curated_database')
        for bad in (dict(reason='results'), dict(rule='x'), dict(kind='other'), dict(mapping_sha256='short'), dict(extra=1)):
            with self.assertRaises(ValueError): ch.declaration(self.decl(**bad))
        both = self.decl(); both['automation']['history_reuse'] = {'source_design_id': 'a', 'rule': 'r'}
        with self.assertRaises(ValueError): ch.declaration(both)

    def test_ledger_one_audit_one_reaudit(self):
        with tempfile.TemporaryDirectory() as d:
            ch.claim_audit(d, self.spec, UNIVERSE, 'des1')
            with self.assertRaises(ValueError): ch.claim_audit(d, self.spec, UNIVERSE, 'des1')       # same key
            with self.assertRaises(ValueError): ch.claim_audit(d, spec(mapping_id='m2'), UNIVERSE, 'des2')  # passed/open: use reuse
            ch.record_result(d, self.spec, UNIVERSE, 'fail')
            with self.assertRaises(ValueError): ch.record_result(d, self.spec, UNIVERSE, 'fail')
            with self.assertRaises(ValueError): ch.claim_audit(d, spec(mapping_id='m2'), UNIVERSE, 'des2')  # needs ruling
            ch.claim_audit(d, spec(mapping_id='m2'), UNIVERSE, 'des2', reaudit_ruling='docs/x.md')
            ch.record_result(d, spec(mapping_id='m2'), UNIVERSE, 'fail')
            with self.assertRaises(ValueError):
                ch.claim_audit(d, spec(mapping_id='m3'), UNIVERSE, 'des3', reaudit_ruling='docs/y.md')   # never a second re-audit

    def passed(self):
        checks = self.all_match(self.plan['sample'])
        return checks, ch.evaluate(self.plan, checks)

    def test_certify_refuses_non_pass(self):
        with tempfile.TemporaryDirectory() as d:
            c = self.all_match(self.plan['sample']); c[0] = self.check(c[0]['unit'], 'predicate')
            with self.assertRaises(ValueError):
                ch.certify(d, self.h, self.spec, self.plan, c, ch.evaluate(self.plan, c))

    def test_certify_writes_certified_history_equivalent(self):
        checks, v = self.passed()
        with tempfile.TemporaryDirectory() as d:
            out = ch.certify(d, self.h, self.spec, self.plan, checks, v)
            cert = json.loads(Path(out['certificate']).read_text())
            self.assertEqual(cert['history_source']['label'], 'db_audited')
            self.assertEqual(cert['rule'], ch.CERT_RULE)
            self.assertTrue(cert['audit']['passed'])
            self.assertEqual(cert['audit']['sample_size'], len(self.plan['sample']))
            self.assertEqual(set(cert['comparison']), {'accepted', 'agreement', 'counts', 'gaps', 'paired_known'})
            src = json.loads(Path(out['source']).read_text())
            self.assertEqual(src['database']['xlsx_sha256'], SHA)
            self.assertIn('Hoch et al.', src['citation'])
            self.assertEqual(json.loads(Path(out['provenance']).read_text())['source_type'], 'curated_database_audited')
            tr = json.loads(Path(out['treatment']).read_text())
            self.assertIn('S20', tr['pre_window_bound_units'])
            self.assertNotIn('S20', {r['unit'] for r in tr['records']})   # bound states leave both arms
            self.assertNotIn('S22', {r['unit'] for r in tr['records']})   # reversal excluded
            ch.certify(d, self.h, self.spec, self.plan, checks, v)         # idempotent (ensure_once)

    def test_certify_applies_corrections_with_one_timing_error(self):
        checks = self.all_match(self.plan['sample'])
        by = {r['unit']: r for r in self.h['accepted']}
        unit = next(u for u in self.plan['sample'] if by[u]['status'] == 'treated' and not by[u].get('pre_window_bound'))
        i = [c['unit'] for c in checks].index(unit)
        checks[i] = self.check(unit, 'timing', corrected_start_period=2012)
        ext = [self.check(u, phase='extension') for u in ch.evaluate(self.plan, checks)['extension_units']]
        checks += ext
        v = ch.evaluate(self.plan, checks)
        with tempfile.TemporaryDirectory() as d:
            out = ch.certify(d, self.h, self.spec, self.plan, checks, v)
            rec = {r['unit']: r for r in json.loads(Path(out['certificate']).read_text())['comparison']['accepted']}[unit]
            self.assertEqual(rec['start_period'], 2012)


class TestOutcomeBlind(unittest.TestCase):
    def test_module_imports_no_outcome_code(self):
        text = inspect.getsource(ch)
        for bad in ('connectors', 'outcome', 'estimator', 'forecast', 'engine.', 'panels'):
            self.assertNotIn('import ' + bad, text)
            self.assertNotIn('from .' + bad, text)
        self.assertNotIn('effect_value', text)


if __name__ == '__main__':
    unittest.main()
