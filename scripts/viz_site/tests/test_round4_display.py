"""Round-4 projection contract; stored records only, with no estimation or writes."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from scripts.did_samples.cases import case_sidecar as frozen
from scripts.viz_site import build, make_case_assets, make_case_sidecars as wrapper


COUNTS = {'cigarette_tax': 12, 'shall_issue': 8, 'minimum_wage_step': 18,
          'right_to_work': 22, 'renewable_portfolio': 17,
          'death_penalty_abolition': 23, 'salary_history_ban': 34,
          'smoke_free_air': 15, 'primary_seat_belt': 16, 'adult_use_cannabis': 40}
ROWS = {'cigarette_tax': 2, 'shall_issue': 4, 'minimum_wage_step': 6,
        'right_to_work': 4, 'renewable_portfolio': 4,
        'death_penalty_abolition': 4, 'salary_history_ban': 6, 'smoke_free_air': 4}
STOP_ROWS = {'primary_seat_belt': 7, 'adult_use_cannabis': 6}


def records(name):
    root = wrapper.ROOT
    case = json.loads((root / f'data/did_samples/{name}/case.json').read_text())
    result = json.loads((root / f'scripts/did_samples/results/cases/{name}.json').read_text())
    site = json.loads((root / f'scripts/did_samples/results/cases/{name}_site.json').read_text())
    declaration = case['treatment']
    mapping = declaration['columns']
    unit = mapping[declaration['unit_column']]
    fields = ('policy_status', 'effective_date', 'date_precision')
    treatment = {r[unit]: {k: r[mapping[k]] for k in fields}
                 for r in frozen.project_csv(root / declaration['path'],
                                            [unit] + [mapping[k] for k in fields])}
    labels = json.loads((root / site['provenance']['labels_path']).read_text())
    addenda = json.loads(wrapper.ADDENDA.read_text())
    return case, result, site, treatment, wrapper.merge_labels(labels, addenda)


def scalar(identity, interval, *, column='y', role='estimate', point=.1, **settings):
    return dict(id=identity, role=role, outcome_column=column, population='all',
                calendar_years=[2000, 2001], window_id='primary',
                control_group='never_treated',
                interval_metadata=dict(method='unit Gaussian multiplier', nominal_level=.95),
                overall=dict(att=point, ci95=interval), **settings)


class Round4ProjectionTests(unittest.TestCase):
    def test_stored_counts_rows_and_frozen_projection(self):
        for name, count in COUNTS.items():
            with self.subTest(case=name):
                case, result, site, treatment, labels = records(name)
                # Isolate frozen primary/figure projection from its superseded
                # variant-placebo selection, which can exceed the original cap.
                builder = wrapper._with_globals(frozen.build_sidecar,
                    select_other_rows=lambda registry, headline_ids, primary_ids: ([], []))
                baseline = builder(case, result, labels, treatment=(
                    treatment if result['status'] == 'PUBLISHABLE_EXAMPLE' else None))
                self.assertEqual(site['design']['primary_comparison_jurisdictions'], count)
                self.assertEqual(wrapper.primary_comparison_block(
                    case, result, treatment=treatment)['primary_comparison_jurisdictions'], count)
                self.assertEqual(site['status'], baseline['status'])
                self.assertEqual(site['shown'], baseline['shown'])
                self.assertEqual(site['figures'], baseline['figures'])
                make_case_assets.validate_case_sidecar(site, name)
                # All original scalar precision, period coordinates and interval
                # construction remain exactly bound; prose may be overridden.
                for identity, row in baseline['primary_estimates'].items():
                    for key in ('estimate', 'interval', 'interval_metadata', 'periods',
                                'calendar_years', 'time_basis', 'exploratory'):
                        self.assertEqual(site['primary_estimates'][identity][key], row[key], key)
                registry, _ = frozen.display_registry(result['registered_results'], labels)
                for row in site['other_estimates']:
                    if row.get('kind') == 'range':
                        values = [frozen.overall(registry[i])['att'] for i in row['ids']]
                        self.assertEqual(row['point_range'], [min(values), max(values)])
                        continue
                    for identity in row['ids']:
                        bound = registry[identity]
                        self.assertEqual(row['estimate'], frozen.overall(bound)['att'])
                        self.assertEqual(row['interval'], frozen.interval(bound))
                        self.assertEqual(row['interval_metadata'], bound['interval_metadata'])
                        self.assertEqual(row['calendar_years'], frozen.years(bound))
                        self.assertEqual(row['periods'], frozen.years(bound))
                        self.assertEqual(row['exploratory'], bool(bound.get('exploratory', False)))
                if name in ROWS:
                    self.assertEqual(len(site['other_estimates']), ROWS[name])
                else:
                    self.assertEqual(len(site['other_estimates']), STOP_ROWS[name])
                for identity, row in site['primary_estimates'].items():
                    self.assertEqual(row['comparison_tier'],
                                     result['registered_results'][identity]['comparison']['tier'])

    def test_required_flips_match_exact_bound_intervals(self):
        expected = {
            'smoke_free_air': 'primary/y/never_treated/drop_large_cigarette_tax_overlap',
            'death_penalty_abolition': 'w1996_2019/y/never_treated/ny_taylor_clock',
            'shall_issue': 'primary/y_xrape/never_treated/drop_mixed_controls'}
        for name, identity in expected.items():
            with self.subTest(case=name):
                _, result, site, _, _ = records(name)
                row = next(row for row in site['other_estimates'] if identity in row['ids'])
                bound = result['registered_results'][identity]
                self.assertEqual(row['estimate'], frozen.overall(bound)['att'])
                self.assertEqual(row['interval'], frozen.interval(bound))
                primary = next(p for p in result['primary_estimates'].values()
                               if frozen.outcome_key(p) == frozen.outcome_key(bound))
                contains = lambda values: values[0] <= 0 <= values[1]
                self.assertNotEqual(contains(row['interval']), contains(frozen.interval(primary)))

    def test_ny_alias_ignores_only_confirmed_rationale(self):
        _, result, site, _, _ = records('death_penalty_abolition')
        first = 'w1996_2019/y/never_treated/include_with_source_date'
        second = 'w1996_2019/y/never_treated/ny_lavalle_clock'
        left, right = (deepcopy(result['registered_results'][i]) for i in (first, second))
        self.assertTrue(wrapper.display_same_scalar(left, right))
        self.assertTrue(any({first, second} <= set(row['ids']) for row in site['other_estimates']))
        for key, replacement in [('sample_mask', {'exclude_units': ['XX']}),
                                 ('exposure_history', {'NY': [{'start': '2005-01-01', 'end': None}]}),
                                 ('control_group', 'not_yet_treated')]:
            changed = deepcopy(right)
            changed[key] = replacement
            self.assertFalse(wrapper.display_same_scalar(left, changed), key)
        changed = deepcopy(right)
        changed['overall']['att'] += 1e-12
        self.assertFalse(wrapper.display_same_scalar(left, changed))
        changed = deepcopy(right)
        changed['interval_metadata']['method'] = 'different interval'
        self.assertFalse(wrapper.display_same_scalar(left, changed))
        changed = deepcopy(right)
        changed['result']['event_study'][0]['att'] += 1e-12
        self.assertFalse(wrapper.display_same_scalar(left, changed))
        changed = deepcopy(right)
        changed['id'] = 'unconfirmed_alias'
        self.assertFalse(wrapper.display_same_scalar(left, changed))

    def test_count_rejects_mismatch_and_partial_quarter(self):
        case, result, site, treatment, _ = records('salary_history_ban')
        jurisdictions = site['design']['primary_comparison_metadata']['jurisdictions']
        self.assertTrue(all(u not in jurisdictions for u in ('IL', 'ME', 'WA')))
        changed = deepcopy(result)
        primary = next(iter(changed['primary_estimates'].values()))
        next(c for c in primary['result']['group_time'] if c['post'])['n_control'] = 35
        with self.assertRaisesRegex(ValueError, 'disagrees with actual post cells'):
            wrapper.primary_comparison_block(case, changed, treatment=treatment)
        # Moving an actual partial-quarter date beyond the window adds a control;
        # the bound 34 cells must block that fabricated 35-jurisdiction inventory.
        altered = deepcopy(treatment)
        altered['IL']['effective_date'] = '2019-10-01'
        with self.assertRaisesRegex(ValueError, 'disagrees with actual post cells'):
            wrapper.primary_comparison_block(case, result, treatment=altered)

    def test_coverage_copies_bound_scope_and_threshold_is_strict(self):
        shortfalls = {'right_to_work': (151, 200, .755),
                      'salary_history_ban': (172, 200, .86),
                      'death_penalty_abolition': (85, 100, .85),
                      'renewable_portfolio': (179, 200, .895)}
        for name, expected in shortfalls.items():
            case, result, site, _, _ = records(name)
            point = site['checks']['calibration']['point_interval_coverage']
            self.assertEqual(tuple(point[k] for k in ('covered', 'repetitions', 'rate')), expected)
            for key, value in result['calibration']['coverage'].items():
                self.assertEqual(point[key], value)
            self.assertEqual(point['scope'], 'pooled_declared_main_outcome_point_intervals')
            self.assertEqual(point['window'], case['windows']['primary'])
            self.assertEqual(set(point['outcome_columns']),
                             {frozen.outcome_key(p) for p in result['primary_estimates'].values()})
        _, _, original, _, _ = records('right_to_work')
        for language in ('en', 'zh'):
            errs = build.Errors()
            ctx = dict(lang=language, page='worked_examples',
                       strings=build.load_dict('worked_examples', language, errs))
            for rate, visible in ((.899999999, True), (.90, False), (.95, False), (None, False)):
                site = deepcopy(original)
                site['checks']['calibration']['point_interval_coverage']['rate'] = rate
                selection = dict(examples=[], omitted=[], cases=[site], prepared=1, shown=1)
                html = build.render_case_page(selection, ctx, errs, '')
                self.assertEqual('we-coverage' in html, visible, (language, rate))

    def test_asset_validation_blocks_corrupt_optional_display_metadata(self):
        _, _, site, _, _ = records('right_to_work')
        for field, invalid in (('covered', 152), ('repetitions', 0),
                               ('rate', float('nan')), ('scope', 'event_study_band'),
                               ('outcome_columns', []), ('nominal_level', 0)):
            damaged = deepcopy(site)
            damaged['checks']['calibration']['point_interval_coverage'][field] = invalid
            with self.assertRaisesRegex(ValueError, 'point interval coverage'):
                make_case_assets.validate_case_sidecar(damaged, 'right_to_work')
        for invalid in (23, True, -1):
            damaged = deepcopy(site)
            damaged['design']['primary_comparison_jurisdictions'] = invalid
            with self.assertRaisesRegex(ValueError, 'primary comparison inventory'):
                make_case_assets.validate_case_sidecar(damaged, 'right_to_work')
        damaged = deepcopy(site)
        damaged['design']['primary_comparison_metadata']['jurisdictions'][0] = \
            damaged['design']['primary_comparison_metadata']['jurisdictions'][1]
        with self.assertRaisesRegex(ValueError, 'primary comparison inventory'):
            make_case_assets.validate_case_sidecar(damaged, 'right_to_work')


class Round4SelectionTests(unittest.TestCase):
    def selected(self, registry, headlines=('main',)):
        selected, diagnostics = wrapper.select_other_rows(registry, list(headlines))
        return {identity for ids in selected for identity in ids}, diagnostics

    def test_symmetric_zero_boundary_and_unrounded_selection(self):
        for primary_ci, variant_ci, required in (
                ([-1, 1], [1e-12, 1], True),
                ([1e-12, 1], [-1e-12, 1], True),
                ([0, 1], [-1, 0], False),
                ([-1, 1], [0, 1], False)):
            records = {'main': scalar('main', primary_ci),
                       'alternative': scalar('alternative', variant_ci)}
            selected, _ = self.selected(records)
            self.assertEqual('alternative' in selected, required)

    def test_existing_materiality_threshold_is_strict(self):
        for point, required in ((.70, False), (.7000000000000001, True)):
            registry = {'main': scalar('main', [-1, 1], point=0),
                        'alternative': scalar('alternative', [-1, 1], point=point)}
            self.assertEqual('alternative' in self.selected(registry)[0], required)

    def test_match_each_outcome_and_keep_actual_window_population(self):
        registry = {'main': scalar('main', [-1, 1]),
                    'second': scalar('second', [.01, 1], column='y2'),
                    'y_alternative': scalar('y_alternative', [.01, 1]),
                    'y2_alternative': scalar('y2_alternative', [.02, 1], column='y2'),
                    'unmatched': scalar('unmatched', [.01, 1], column='new')}
        registry['y_alternative'].update(calendar_years=[1999, 2000], population='different', exploratory=True)
        original = deepcopy(registry)
        selected, _ = self.selected(registry, ('main', 'second'))
        self.assertEqual(selected, {'y_alternative'})
        self.assertEqual(registry, original)
        registry['unmatched']['variant_kind'] = 'outcome'
        self.assertIn('unmatched', self.selected(registry, ('main', 'second'))[0])

    def test_invalid_intervals_and_metadata_block_generation(self):
        for field, invalid in [('ci95', None), ('ci95', [2, 1]), ('ci95', [0, float('nan')])]:
            registry = {'main': scalar('main', [-1, 1]),
                        'alternative': scalar('alternative', [.01, 1])}
            registry['alternative']['overall'][field] = invalid
            with self.assertRaises(ValueError):
                self.selected(registry)
        for metadata in ({}, {'method': None, 'nominal_level': .95},
                         {'method': 'unit Gaussian multiplier', 'nominal_level': None},
                         {'method': '', 'nominal_level': .95},
                         {'method': 'unit Gaussian multiplier', 'nominal_level': 0},
                         {'method': 'unit Gaussian multiplier', 'nominal_level': 1.1}):
            registry = {'main': scalar('main', [-1, 1]),
                        'alternative': scalar('alternative', [.01, 1])}
            registry['alternative']['interval_metadata'] = metadata
            with self.assertRaisesRegex(ValueError, 'interval method/level'):
                self.selected(registry)

    def test_cap_retains_registry_order_and_diagnostics_without_truncation(self):
        registry = {'main': scalar('main', [-1, 1])}
        for index in range(8):
            identity = f'flip{index}'
            registry[identity] = scalar(identity, [.01, 1], sample_mask={'exclude_units': [str(index)]})
        selected, diagnostics = wrapper.select_other_rows(registry, ['main'])
        self.assertEqual(selected, [[f'flip{i}'] for i in range(8)])
        self.assertEqual(diagnostics, [])
        original = deepcopy(registry)
        registry['main/placebo'] = scalar('main/placebo', [-1, 1], role='placebo')
        with self.assertRaisesRegex(ValueError, 'exceeds eight rows'):
            self.selected(registry)
        self.assertEqual({k: v for k, v in registry.items() if k != 'main/placebo'}, original)
        registry.pop('main/placebo')
        registry['flip8'] = scalar('flip8', [.01, 1], sample_mask={'exclude_units': ['8']})
        with self.assertRaisesRegex(ValueError, 'exceeds eight rows'):
            self.selected(registry)


if __name__ == '__main__':
    unittest.main()
