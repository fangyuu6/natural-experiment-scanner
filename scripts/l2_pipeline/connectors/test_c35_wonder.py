import csv,io,tempfile,unittest
from pathlib import Path
from .c35_wonder import mask_cmf_text,COMPARABILITY,validate_semantics
from .wonder import parse_export
from .common import ConnectorError

class C35WONDERTests(unittest.TestCase):
    def text(self):
        return '"Notes"\t"State"\t"Year"\tDeaths\tPopulation\tCrude Rate\n\tAlabama\t1979\t7\t100000\t7.0\n\tAlabama\t1980\tSuppressed\t100000\t2.0\n\tAlabama\t1981\t23\t100000\t23.0\n'
    def test_masks_count_and_rate_and_preserves_raw(self):
        raw=self.text();derived,n=mask_cmf_text(raw)
        self.assertEqual(n,1);self.assertEqual(raw,self.text())
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'cmf.tsv';p.write_text(derived)
            counts=parse_export(p,measure='Deaths');rates=parse_export(p,measure='Crude Rate')
        for y in [1979,1980]:
            self.assertTrue(counts['AL',y]['suppressed']);self.assertIsNone(counts['AL',y]['value'])
            self.assertIsNone(rates['AL',y]['value']);self.assertEqual(counts['AL',y]['source_value'],'Suppressed')
        self.assertEqual(counts['AL',1981]['value'],23);self.assertEqual(rates['AL',1981]['value'],23)
    def test_threshold_and_zero_permission(self):
        txt=self.text().replace('\t7\t','\t0\t');derived,n=mask_cmf_text(txt)
        self.assertEqual(n,1)
        txt=self.text().replace('\t7\t','\t10\t');derived,n=mask_cmf_text(txt)
        self.assertEqual(n,0)
    def test_idempotent_mask(self):
        derived,n=mask_cmf_text(self.text());second,n2=mask_cmf_text(derived)
        self.assertEqual(second,derived);self.assertEqual(n2,0)
    def test_ratio_only_all_homicide_unapplied(self):
        self.assertEqual(COMPARABILITY['ratio'],1.002);self.assertFalse(COMPARABILITY['applied'])
        self.assertEqual(COMPARABILITY['use'],'metadata_only')
    def test_wrong_population_rejected(self):
        r=dict(series_id='wonder_homicide_deaths',years_requested='1979-1998',database_code='D16',legal_intervention_excluded=True,
               population_filters=dict(age='all ages',sex='female',injury_intent='Homicide',injury_mechanism='All'))
        with self.assertRaises(ConnectorError):validate_semantics(r,r['series_id'],1979,1998)

if __name__=='__main__':unittest.main()
