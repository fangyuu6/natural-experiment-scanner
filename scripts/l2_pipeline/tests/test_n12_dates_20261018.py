"""N12 date fields: the current V9 history template asks for them; they survive collect, reconcile and S1 export. Invented fixtures only."""
import copy, hashlib, json, tempfile, unittest
from pathlib import Path
from scripts.l2_pipeline.agents import history
from scripts.l2_pipeline.agents.collect import collect_job
from scripts.l2_pipeline.agents.contract_tests import golden_output, validate_template
from scripts.l2_pipeline.agents.contract_tests.packets import emit_fixture_job
from scripts.l2_pipeline.intake.official_history import engine_records
from scripts.l2_pipeline.stages.s1_treatment import reconcile

FIELDS = ('enactment_date', 'operational_implementation_date', 'operational_date_precision')
TEMPLATES = Path(history.__file__).parent / 'templates'
# sha256 of the pre-fix current template (v9_c48): it is bound into every job already emitted and must stay byte-identical.
C48_SHA256 = '5a93004846a804b220a6852c9b365f6854ce2febeb5106d21f74a5080d06c712'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class TemplateVersion(unittest.TestCase):
    def test_current_template_is_the_new_version_and_names_the_fields(self):
        self.assertEqual(history.CURRENT_TEMPLATE.parent.name, 'v9_t191')
        text = history.CURRENT_TEMPLATE.read_text()
        for name in FIELDS:
            self.assertGreaterEqual(text.count(name), 2, name)  # contract sentence and machine schema
        self.assertIn('Do not guess', text)
        self.assertIn('null', text)

    def test_old_templates_are_untouched_so_emitted_jobs_keep_their_hash(self):
        self.assertEqual(sha(TEMPLATES / 'v9_c48/official_history_coder_fx10.md'), C48_SHA256)
        for name in FIELDS:
            self.assertNotIn(name, (TEMPLATES / 'v9_c48/official_history_coder_fx10.md').read_text())
            self.assertNotIn(name, history.TEMPLATE.read_text())

    def test_new_template_differs_from_c48_only_by_the_date_addition(self):
        old = (TEMPLATES / 'v9_c48/official_history_coder_fx10.md').read_text().splitlines()
        new = history.CURRENT_TEMPLATE.read_text().splitlines()
        removed = [l for l in old if l not in new]
        self.assertEqual(removed, [])


class Emit(unittest.TestCase):
    def job_and_value(self, tmp):
        job = emit_fixture_job(Path(tmp).resolve(), 's1_official_history_v9')
        return job, golden_output(job)

    def test_emitted_job_uses_new_template_and_prompt_names_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            job, _ = self.job_and_value(tmp)
            self.assertEqual(Path(job.template_path).parent.name, 'v9_t191')
            self.assertEqual(job.template_sha256, sha(job.template_path))
            prompt = Path(job.job_path).read_text()
            for name in FIELDS:
                self.assertIn(name, prompt)

    def test_schema_accepts_iso_dates_nulls_and_absent_fields_and_rejects_bad_precision(self):
        with tempfile.TemporaryDirectory() as tmp:
            job, value = self.job_and_value(tmp)
            validate_template(job, value)  # no date fields: still valid
            v = copy.deepcopy(value)
            v['records'][0].update(enactment_date='2022-05-10', operational_implementation_date='2023-01-01', operational_date_precision='day')
            validate_template(job, v)
            v['records'][1].update(enactment_date=None, operational_implementation_date=None, operational_date_precision=None)
            validate_template(job, v)
            bad = copy.deepcopy(v); bad['records'][0]['operational_date_precision'] = 'week'
            with self.assertRaisesRegex(ValueError, 'enum'):
                validate_template(job, bad)
            bad = copy.deepcopy(v); bad['records'][0]['enactment_date'] = 20220510
            with self.assertRaisesRegex(ValueError, 'type'):
                validate_template(job, bad)

    def test_collect_keeps_date_fields_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            job, value = self.job_and_value(tmp)
            value['records'][0].update(enactment_date='2022-05-10', operational_implementation_date='2023-01-01', operational_date_precision='day')
            p = Path(job.output_path); p.parent.mkdir(parents=True); p.write_text(json.dumps(value))
            receipt = collect_job(job.job_path)
            out = json.loads(Path(receipt['promoted_path']).read_text())
            self.assertEqual({k: out['records'][0][k] for k in FIELDS},
                             dict(enactment_date='2022-05-10', operational_implementation_date='2023-01-01', operational_date_precision='day'))


class FlowToTreatment(unittest.TestCase):
    def row(self, unit, **kw):
        return dict(dict(unit=unit, status='treated', start_period=2024, best_evidence_start_year=2023, mechanism='m', locator='s1'), **kw)

    def test_accepted_row_carries_dates_into_engine_records(self):
        dated = dict(enactment_date='2022-05-10', operational_implementation_date='2023-01-01', operational_date_precision='day')
        a = [self.row('AL', **dated), self.row('AK')]
        b = [self.row('AL', **dated), self.row('AK')]
        accepted = reconcile(a, b, ['AL', 'AK'])['accepted']
        out = {r['unit']: r for r in engine_records(copy.deepcopy(accepted), set())}
        self.assertEqual((out['AL']['enactment_date'], out['AL']['effective_date'], out['AL']['date_precision']), ('2022-05-10', '2023-01-01', 'day'))
        self.assertEqual((out['AK']['enactment_date'], out['AK']['effective_date'], out['AK']['date_precision']), (None, None, None))
        self.assertEqual((out['AL']['start_period'], out['AK']['start_period']), (2023, 2023))

    def test_non_iso_values_stay_null_and_clock_unchanged(self):
        r = self.row('AL', enactment_date='2022', operational_implementation_date='2023-01', operational_date_precision='month')
        out = engine_records([copy.deepcopy(r)], set())[0]
        self.assertEqual((out['enactment_date'], out['effective_date'], out['date_precision']), (None, None, None))
        self.assertEqual(out['start_period'], 2023)


if __name__ == '__main__':
    unittest.main()
