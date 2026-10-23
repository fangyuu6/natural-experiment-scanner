"""Prospective ruling changes retain immutable legal clocks and audit authority."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline import c21
from scripts.l2_pipeline.contracts import DesignCard, StageContext
from scripts.l2_pipeline.storage import ensure_once, sha


class C21HistoryTests(unittest.TestCase):
    def history(self, unit):
        return dict(records=[dict(unit=unit,status='never_treated',start_period=None)],
                    annual_rows=[dict(unit=unit,year=y,active=0,value=0) for y in range(1994,2026)],
                    events=[],coverage=dict(start_year=1994,end_year=2025))

    def test_january_first_il_and_midyear_va_have_different_full_year_masks(self):
        for unit,rule,date,full in [('IL',8,'1996-01-01',1996),('VA',7,'2021-07-01',2022)]:
            original=self.history(unit); snapshot=deepcopy(original)
            result=c21.amend_history(original,dict(unit=unit),rule)
            self.assertEqual(original,snapshot)
            row=result['records'][0]
            self.assertEqual(row['operational_date'],date)
            self.assertEqual(row['first_full_start_period'],full)
            self.assertEqual(row['transition_year_drop'],unit=='VA')
            self.assertFalse(result['legal_certification_granted'])

    def test_ut_rejecting_one_candidate_does_not_certify_lifetime_absence(self):
        source=self.history('UT')
        result=c21.amend_history(source,dict(unit='UT'),6)
        self.assertFalse(result['records'][0]['candidate_law_treated'])
        self.assertFalse(result['records'][0]['history_absence_certified'])
        self.assertEqual(result['annual_rows'],source['annual_rows'])

    def test_carry_rejects_treatment_clock_and_window_changes(self):
        old=c21.read(c21.DVRO/'cards/dvro.json')
        for field in ('clock','outcome_years'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as folder:
                root=Path(folder); source=ensure_once(root/'old.json',old)
                new=deepcopy(old);new['version']='v22'
                new['outcome']['series_id']='wonder_firearm_homicide_deaths'
                new['treatment']['c21_s1_carry']=dict(source_card=dict(path=str(source),sha256=sha(source)))
                if field=='clock':new['transition_year_drop']=False
                else:new['outcome']['period_years']=new['outcome']['period_years'][1:]
                card=DesignCard.from_mapping(new,root/'new.json')
                with self.assertRaisesRegex(ValueError,'clock|window'):
                    c21.carry_s1(StageContext(card,root,c21.REPO))


if __name__=='__main__':unittest.main()
