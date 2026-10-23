import copy
import unittest
from scripts.l2_pipeline.predictions import primary_forecast_target


class ForecastTargetTests(unittest.TestCase):
    def fixture(self):
        return dict(design_id='fixture',track='DiD',population={'filters':{}},outcome={'outcome_unit':'pp'},
                    analysis={'estimand':'ATT','weights':'cohort counts','controls':'never','clock':'annual'},
                    support={'variants':[{'variant':'primary','cells':[
                        dict(cohort=2004,period=2005,base_period=2003,treated=['T'],controls=['C'])]}]})

    def test_support_schema_and_pre_diagnostics_preserve_target(self):
        old=self.fixture();new=copy.deepcopy(old)
        new['support']['variants'][0]['cell_inventory']=[
            dict(g=2004,t=2002,base_period=2003,treated=['T'],controls=['C'],post=False),
            dict(g=2004,t=2005,base_period=2003,treated=['T'],controls=['C'],post=True)]
        self.assertEqual(primary_forecast_target(old),primary_forecast_target(new))

    def test_membership_and_unit_changes_require_new_forecasts(self):
        old=self.fixture()
        for change in ('membership','unit'):
            new=copy.deepcopy(old)
            if change=='membership':new['support']['variants'][0]['cells'][0]['treated']=['U']
            else:new['outcome']['outcome_unit']='kWh'
            self.assertNotEqual(primary_forecast_target(old),primary_forecast_target(new))
