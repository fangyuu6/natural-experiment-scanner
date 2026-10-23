"""C70 writers and Gate 0 share a strict value-free sidecar schema."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline.connectors import c70_build as c70,c46_core as core
from scripts.l2_pipeline.connectors.common import ConnectorError
from scripts.l2_pipeline.prescreen import flags
from scripts.l2_pipeline.storage import ensure_once,sha

class SupportSchemaTests(unittest.TestCase):
    def test_builder_and_reader_share_reviewed_metadata_fields(self):
        examples={
            'ftc':dict(unit_term='complaints',contributor_exclusion=True),
            'brfss':dict(question_id='_HCVU651'),
            'ipeds_enrollment':dict(institution_coverage=.95,institution_coverage_sensitivity_99=False,institutions_expected=100,institutions_reporting=95),
            'ipeds_completions':dict(institution_coverage=.99,institution_coverage_sensitivity_99=True,institutions_expected=100,institutions_reporting=99)}
        original=core.SUPPORT_FIELDS.copy()
        for name,extra in examples.items():
            with self.subTest(series=name),tempfile.TemporaryDirectory() as d:
                root=Path(d).resolve()
                with patch.object(core,'REPO',root),patch.object(core,'BASE',root/'data/fixture'),patch.object(c70,'UNITS',('AL',)):
                    spec=c70.save(name,{},[2001],{('AL',2001):123.456},[],name,'https://example.invalid','percent',[],{('AL',2001):extra})
                side=json.loads((root/spec['support_artifact']['path']).read_text())
                self.assertEqual(flags(spec,root),{('AL',2001)})
                self.assertFalse(side['outcome_values_in_sidecar'])
                self.assertEqual(set(side['metadata'][0]) & set(extra),set(extra))
                self.assertNotIn('outcome_value',side['metadata'][0]);self.assertNotIn('123.456',json.dumps(side))
                self.assertEqual(core.SUPPORT_FIELDS,original)
        self.assertEqual(c70.EXTRA_SUPPORT,core.C70_SUPPORT_FIELDS)

    def test_unknown_or_outcome_fields_rejected_by_builder_and_reader(self):
        for field in ('outcome_value','value','count','population','denominator','effect_estimate','unknown_flag'):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as d:
                root=Path(d).resolve()
                with patch.object(core,'REPO',root),patch.object(core,'BASE',root/'data/fixture'),patch.object(c70,'UNITS',('AL',)):
                    with self.assertRaisesRegex(ConnectorError,'Non-metadata'):
                        c70.save('invented',{},[2001],{('AL',2001):1.},[],'invented','https://example.invalid','percent',[],{('AL',2001):{field:123.456}})
                path=ensure_once(root/'support.json',dict(series_id='invented',outcome_values_in_sidecar=False,metadata=[dict(unit='AL',year=2001,**{field:123.456})]))
                spec=dict(series_id='invented',support_artifact=dict(path=str(path),sha256=sha(path)))
                with self.assertRaisesRegex(ValueError,'Non-metadata'):flags(spec,root)
                self.assertFalse((root/'data/fixture').exists())

    def test_existing_four_c70_series_are_accepted_without_rewriting_sidecars(self):
        root=Path(__file__).resolve().parents[3]
        catalog=json.loads((root/'scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V15.json').read_text())
        for sid in ('ftc_csn_idtheft_reports_per_100k','brfss_health_coverage_pct_age18_64','ipeds_enrollment_per_population','ipeds_completions_per_population'):
            with self.subTest(series=sid):
                spec=catalog['series'][sid];path=root/spec['support_artifact']['path'];before=sha(path)
                self.assertTrue(flags(spec,root));self.assertEqual(sha(path),before)

if __name__=='__main__':unittest.main()
