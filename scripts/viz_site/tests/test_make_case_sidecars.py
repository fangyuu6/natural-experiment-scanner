"""Display-rule tests from synthetic fixtures and frozen records; no executor calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.did_samples.cases import case_sidecar as frozen
from scripts.viz_site import make_case_sidecars as wrapper


def registry(alternatives=0):
    def row(point, role='estimate'):
        return dict(role=role, outcome_column='y', population='all',
                    calendar_years=[2000, 2001], window_id='primary',
                    control_group='never_treated', interval_metadata=dict(method='unit Gaussian multiplier', nominal_level=.95), overall=dict(att=point, ci95=[-1, 1]))

    records = {'headline': row(.1), 'headline/placebo': row(.01, 'placebo'),
               'headline/omit/A': row(.08, 'omission'),
               'headline/omit/B': row(.12, 'omission')}
    for index in range(3):
        records[f'headline/variant{index}/placebo'] = row(.02 + index / 100, 'placebo')
    for index in range(alternatives):
        records[f'alternative{index}'] = row(.2 + index / 100, 'primary_alternative')
    return records


class CaseSidecarsTests(unittest.TestCase):
    def test_treatment_date_failure_counts_unique_jurisdictions_and_preserves_other_reasons(self):
        missing = [dict(unit=unit, **{'pass': False}, source1_date=None, source2_date=None)
                   for unit in ('A', 'B', 'A')]
        date_check = dict(check='treatment_two_source_dates', **{'pass': False},
                          details=dict(checks=missing))
        row = dict(evidence=dict(replication_audit=dict(checks=[date_check], complete=False)))
        result = dict(status='INTERNAL_ONLY', registered_results={'headline': row, 'variant': row},
                      stop_issues=[f'{identity}: {wrapper.AUDIT_ISSUE}' for identity in ('headline', 'variant')],
                      gate_table=[dict(gate='computed_comparison_audits', **{'pass': False})])
        execution = frozen.pair('Execution stopped after an error.', '执行因错误而停止。')
        audit = frozen.pair('The published comparison audit was incomplete or failed.',
                            '已发表估计的比较审计未完成或未通过。')
        placebo = frozen.pair('Placebo estimate exceeded its preset limit.', '安慰剂估计超过预设界限。')
        original = deepcopy(result)
        reasons = wrapper.display_stop_reasons(result, [execution, audit, placebo])
        self.assertEqual(reasons[1:], [placebo])
        self.assertIn('Source dates missing for 2 jurisdictions', reasons[0]['en'])
        self.assertIn('2 个辖区缺少来源日期', reasons[0]['zh'])
        self.assertEqual(result, original)
        # Independent execution failures and other failed audit items stay visible.
        result['stop_issues'].append('headline: numeric execution failed')
        row['evidence']['replication_audit']['checks'].append(
            dict(check='panel_manifest_hashes', **{'pass': False}))
        self.assertEqual(wrapper.display_stop_reasons(result, [execution, audit, placebo])[1:],
                         [execution, audit, placebo])
        result['stop_issues'].pop()
        result['stop_issues'].append('execution_error: recorded execution failure')
        self.assertIn(execution, wrapper.display_stop_reasons(result, [execution, audit]))
        result['stop_issues'].pop()
        result['stop_reasons'] = ['execution_error', 'computed_comparison_audits']
        self.assertIn(execution, wrapper.display_stop_reasons(result, [execution, audit]))

    def test_date_audit_override_requires_missing_dates_and_failed_gate(self):
        reasons = [frozen.pair('Execution stopped after an error.', '执行因错误而停止。')]
        check = dict(check='treatment_two_source_dates', **{'pass': False}, details=dict(checks=[
            dict(unit='A', **{'pass': False}, source1_date='2000-01-01', source2_date='2001-01-01')]))
        result = dict(status='INTERNAL_ONLY', gate_table=[
            dict(gate='computed_comparison_audits', **{'pass': False})],
            registered_results={'headline': dict(evidence=dict(replication_audit=dict(checks=[check])))},
            stop_issues=[f'headline: {wrapper.AUDIT_ISSUE}'])
        self.assertEqual(wrapper.display_stop_reasons(result, reasons), reasons)
        check['details']['checks'][0].update(source1_date=None, source2_date=None)
        result['gate_table'][0]['pass'] = True
        self.assertEqual(wrapper.display_stop_reasons(result, reasons), reasons)
        result['gate_table'][0]['pass'] = False
        result['status'] = 'PUBLISHABLE_EXAMPLE'
        self.assertEqual(wrapper.display_stop_reasons(result, reasons), reasons)

    def test_comparison_wording_uses_statuses_without_guessing_from_missing_dates(self):
        details = [dict(unit=u, **{'pass': False}, source1_date=None, source2_date=None)
                   for u in ('A', 'B', 'A')]
        check = dict(check='treatment_two_source_dates', **{'pass': False},
                     details=dict(checks=details))
        result = dict(status='INTERNAL_ONLY', gate_table=[
            dict(gate='computed_comparison_audits', **{'pass': False})],
            registered_results={'main': dict(evidence=dict(replication_audit=dict(checks=[check])))})
        treatment = {u: dict(policy_status='never_treated') for u in ('A', 'B')}
        reason = wrapper.display_stop_reasons(result, [], treatment=treatment)[0]
        self.assertIn('2 never-treated comparison states', reason['en'])
        self.assertIn('2 个始终未处理的对照州', reason['zh'])
        # One treated or unknown status must not be described as all comparisons.
        for status in ('treated', None):
            treatment['B']['policy_status'] = status
            self.assertIn('2 jurisdictions', wrapper.display_stop_reasons(
                result, [], treatment=treatment)[0]['en'])
        # Result statuses alone suffice; table metadata is only a fallback.
        for detail in details:
            detail['policy_status'] = 'never_treated'
        self.assertEqual(wrapper.display_stop_reasons(result, [])[0], reason)
        details[:] = [details[1]]
        self.assertIn('Source dates missing for 1 never-treated comparison state.',
                      wrapper.display_stop_reasons(result, [])[0]['en'])

    def test_real_stopped_reasons_follow_bound_result_evidence(self):
        for identity in ('primary_seat_belt', 'adult_use_cannabis'):
            with self.subTest(case_id=identity):
                case = json.loads((wrapper.ROOT / f'data/did_samples/{identity}/case.json').read_text())
                result = json.loads((wrapper.ROOT / f'scripts/did_samples/results/cases/{identity}.json').read_text())
                labels_path = next(path for path in case['hashes'] if path.endswith('/labels.json'))
                labels = json.loads((wrapper.ROOT / labels_path).read_text())
                original = deepcopy(result)
                declaration = case['treatment']
                mapping = declaration['columns']
                logical = ('policy_status', 'effective_date', 'date_precision')
                unit = mapping[declaration['unit_column']]
                treatment = {r[unit]: {name: r[mapping[name]] for name in logical} for r in frozen.project_csv(
                    wrapper.ROOT / declaration['path'], [unit] + [mapping[name] for name in logical])}
                record = wrapper.build_sidecar(case, result, labels, treatment=treatment)
                stored_projection = json.loads((wrapper.ROOT /
                    f'scripts/did_samples/results/cases/{identity}_site.json').read_text())
                for field in ('figures', 'status', 'shown'):
                    self.assertEqual(record[field], stored_projection[field], field)
                for estimate_id, estimate in record['primary_estimates'].items():
                    bound = result['registered_results'][estimate_id]['overall']
                    self.assertEqual(estimate['estimate'], bound['att'])
                    self.assertEqual(estimate['interval'], bound['ci95'])
                if identity == 'primary_seat_belt':
                    self.assertEqual(record['stop_reasons'], [frozen.pair(
                        'Date-audit labels were inconsistent for 12 never-treated comparison states.',
                        '12 个始终未处理对照州的日期核验标签与记录不一致。')])
                else:
                    self.assertEqual(record['stop_reasons'], [frozen.pair(
                        'Placebo estimate exceeded its preset limit.',
                        '安慰剂估计超过预设界限。')])
                self.assertTrue(record['primary_estimates'])
                self.assertFalse(record['shown'])
                self.assertEqual(result, original)

    def test_unchanged_diagnostics_give_byte_identical_output_except_provenance(self):
        scratch = Path('/tmp/scratch/'
                       'bdface0a-3d58-4f5e-aef6-12ae959fa031/scratchpad/page_ext')
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            root = Path(directory)
            (root / 'spec.md').write_text('Synthetic specification\n')
            labels = dict(version=1, case_id='synthetic', policy=frozen.pair('Policy', '政策'),
                          treatment=frozen.pair('Treatment', '处理'),
                          outcomes={'y': {key: frozen.pair('Synthetic', '合成')
                                          for key in ('outcome', 'population', 'unit')}},
                          rows={'headline': frozen.pair('Headline', '主估计'),
                                'headline/placebo': frozen.pair('Placebo', '安慰剂')})
            (root / 'labels.json').write_text(json.dumps(labels))
            case = dict(case_id='synthetic', data_kind='synthetic', panel={},
                        benchmark=dict(author='Synthetic', year=2000, outlet='Synthetic'),
                        spec=dict(path='spec.md', sha256=frozen.digest(root / 'spec.md')),
                        hashes={'labels.json': frozen.digest(root / 'labels.json')})
            case['declaration_sha256'] = frozen.declaration_hash(case)
            (root / 'case.json').write_text(json.dumps(case))
            records = registry()
            records = {key: value for key, value in records.items() if '/variant' not in key}
            for row in records.values():
                row['interval_metadata'] = dict(method='unit Gaussian multiplier', nominal_level=.95)
            result = dict(case_id='synthetic', status='INTERNAL_ONLY', stop_reasons=['execution_error'],
                          primary_estimates={'headline': records['headline']}, registered_results=records,
                          input_hashes=dict(case_json=frozen.digest(root / 'case.json'), spec=case['spec']['sha256']))
            (root / 'result.json').write_text(json.dumps(result))
            (root / 'addendum.json').write_text(json.dumps(dict(version=1, cases={})))
            arguments = (root / 'case.json', root / 'result.json', root / 'labels.json')
            frozen.generate_sidecar(*arguments, root=root, output=root / 'frozen.json')
            record = wrapper.generate_sidecar(*arguments, root=root, output=root / 'wrapper.json',
                                              addendum_path=root / 'addendum.json')
            self.assertEqual(json.loads((root / 'wrapper.json').read_text()), record)
            del record['provenance']['display_wrapper']
            encoded = (json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
            self.assertEqual(encoded, (root / 'frozen.json').read_bytes())
            with self.assertRaises(FileExistsError):
                wrapper.generate_sidecar(*arguments, root=root, output=root / 'wrapper.json',
                                         addendum_path=root / 'addendum.json')
            with self.assertRaisesRegex(frozen.SidecarError, 'limited to the derived'):
                wrapper.generate_sidecar(*arguments, root=root, output=root / 'result.json',
                                         addendum_path=root / 'addendum.json', replace=True)
            sidecar_path = root / 'synthetic_site.json'
            sidecar_path.write_text('outdated derived display')
            replaced = wrapper.generate_sidecar(*arguments, root=root, output=sidecar_path,
                                               addendum_path=root / 'addendum.json', replace=True)
            self.assertEqual(json.loads(sidecar_path.read_text()), replaced)
            self.assertFalse(sidecar_path.with_suffix('.json.regenerated').exists())
            self.assertEqual(json.loads((root / 'result.json').read_text()), result)

    def test_headline_only_placebo_and_unchanged_omission_range(self):
        records = registry()
        original = deepcopy(records)
        selected, diagnostics = wrapper.select_other_rows(records, ['headline'])
        frozen_selected, frozen_diagnostics = frozen.select_other_rows(records, ['headline'])
        self.assertEqual(selected, frozen_selected)
        self.assertEqual(diagnostics, [frozen_diagnostics[0], frozen_diagnostics[1]])
        self.assertEqual(diagnostics[0]['ids'], ['headline/omit/A', 'headline/omit/B'])
        self.assertEqual(diagnostics[0]['point_range'], [.08, .12])
        self.assertEqual(diagnostics[1], dict(kind='placebo', ids=['headline/placebo']))
        self.assertEqual(records, original)

    def test_guard_is_deferred_then_reapplied_at_eight(self):
        records = registry(6)
        with self.assertRaisesRegex(frozen.SidecarError, 'exceeds eight rows'):
            frozen.select_other_rows(records, ['headline'])
        selected, diagnostics = wrapper.select_other_rows(records, ['headline'])
        self.assertEqual(len(selected) + len(diagnostics), 8)
        with self.assertRaisesRegex(frozen.SidecarError, '7 estimates.*2 diagnostics = 9'):
            wrapper.select_other_rows(registry(7), ['headline'])
        # The imported frozen selector still enforces its original guard.
        with self.assertRaisesRegex(frozen.SidecarError, 'exceeds eight rows'):
            frozen.select_other_rows(records, ['headline'])

    def test_missing_headline_placebo_is_not_replaced_by_variant(self):
        records = registry()
        del records['headline/placebo']
        _, diagnostics = wrapper.select_other_rows(records, ['headline'])
        self.assertEqual([row['kind'] for row in diagnostics], ['range'])

    def test_labels_fill_missing_words_only(self):
        addition = dict(version=1, cases={'synthetic': {'benchmark_unit': frozen.pair('unit', '单位')}})
        labels = dict(case_id='synthetic', policy=frozen.pair('Policy', '政策'))
        original = deepcopy(labels)
        self.assertEqual(wrapper.merge_labels(labels, addition)['benchmark_unit'], frozen.pair('unit', '单位'))
        self.assertEqual(labels, original)
        labels['benchmark_unit'] = frozen.pair('declared', '已声明')
        self.assertEqual(wrapper.merge_labels(labels, addition), labels)
        addition['cases']['synthetic']['estimate'] = 1
        with self.assertRaisesRegex(frozen.SidecarError, 'words only'):
            wrapper.merge_labels(labels, addition)

    def test_prose_overrides_keep_frozen_labels_and_all_numerical_fields(self):
        addition = dict(version=1, cases={'synthetic': {'display_words': {
            'policy': frozen.pair('Capitalized policy', '政策'),
            'source_caption': frozen.pair('Author (2020), Table 1.', 'Author（2020），表 1。')}}})
        labels = dict(case_id='synthetic', policy=frozen.pair('original policy', '原政策'))
        original = deepcopy(labels)
        self.assertEqual(wrapper.merge_labels(labels, addition), original)
        sidecar = dict(case_id='synthetic', policy=labels['policy'], shown=False,
                       primary_estimates={'main': dict(estimate=.123456789, interval=[-.2, .3])},
                       status='INTERNAL_ONLY')
        numeric = deepcopy(sidecar['primary_estimates'])
        record = wrapper.apply_display_words(sidecar, addition)
        self.assertEqual(record['primary_estimates'], numeric)
        self.assertEqual(record['policy'], addition['cases']['synthetic']['display_words']['policy'])
        self.assertFalse(record['shown'])
        self.assertEqual(record['status'], 'INTERNAL_ONLY')
        self.assertEqual(labels, original)
        addition['cases']['synthetic']['display_words']['estimate'] = frozen.pair('1', '1')
        with self.assertRaisesRegex(frozen.SidecarError, 'words only'):
            wrapper.merge_labels(labels, addition)


if __name__ == '__main__':
    unittest.main()
