"""C21 additions preserve V4 contracts and fail closed on uncollected series."""
from copy import deepcopy
from dataclasses import replace
import unittest

from scripts.l2_pipeline import connectors
from .common import ConnectorRequest, ConnectorError, ConnectorNotReady
from . import outcome_v5


class V5Tests(unittest.TestCase):
    def test_registration_is_additive_and_idempotent(self):
        old={k:deepcopy(v) for k,v in connectors.CONNECTOR_CATALOG.items() if not k.startswith('outcome_v5_')}
        builders={k:v for k,v in connectors.ADAPTERS.items() if not k.startswith('outcome_v5_')}
        self.assertEqual(outcome_v5.register(),outcome_v5.register())
        for k,v in old.items():self.assertEqual(connectors.CONNECTOR_CATALOG[k],v)
        for k,v in builders.items():self.assertIs(connectors.ADAPTERS[k],v)

    def request(self,sid,**updates):
        spec=outcome_v5.catalog()['series'][sid]
        out=dict(connector='outcome_v5_'+sid,series_id=sid,measure_id=sid,
                 frequency='annual',outcome_scale='rate',population_filters=spec['population_filters'],
                 paths=spec.get('local_source_paths',[]))
        out.update(updates)
        from .wonder_registry import ROOT
        return ConnectorRequest(out,out['population_filters'],(2018,),('AL',),'annual',ROOT)

    def test_existing_homicide_rate_v5_retains_population_contract(self):
        sid='wonder_firearm_homicide_deaths';request=self.request(sid)
        panel=connectors.construct(request)
        self.assertEqual(len(panel.rows),1)
        self.assertEqual(panel.rows[0]['outcome_scale'],'rate')
        self.assertTrue(connectors.target_mapped('outcome_v5_'+sid,'intimate_partner_homicide_mortality',sid))
        wrong=replace(request,filters=dict(request.filters,sex='female'),
                      options=dict(request.options,population_filters=dict(request.filters,sex='female')))
        with self.assertRaises(ConnectorError):connectors.construct(wrong)

    def test_pending_exports_cannot_become_available_by_name(self):
        for sid in ('wonder_homicide_deaths','wonder_firearm_homicide_deaths_female'):
            spec=outcome_v5.catalog()['series'][sid]
            if not spec['public_available']:
                with self.assertRaises(ConnectorNotReady):connectors.construct(self.request(sid))
                self.assertFalse(connectors.CONNECTOR_CATALOG['outcome_v5_'+sid]['available'])

    def test_v6_verified_homicide_series_use_exact_sex_specific_denominators(self):
        from . import outcome_v6
        from .wonder_registry import ROOT
        data=outcome_v6.catalog()
        for sid in ('wonder_homicide_deaths','wonder_firearm_homicide_deaths_female'):
            spec=data['series'][sid]
            self.assertTrue(spec['browser_receipts_verified'])
            filters=spec['population_filters']
            out=dict(connector='outcome_v6_'+sid,series_id=sid,measure_id=sid,
                     frequency='annual',outcome_scale='rate',population_filters=filters,
                     paths=spec['local_source_paths'])
            panel=connectors.construct(ConnectorRequest(out,filters,(1999,2018),('AL','SD'),'annual',ROOT))
            self.assertEqual(len(panel.rows),4)
            self.assertTrue(all(r['outcome_scale']=='rate' for r in panel.rows))
            if sid.endswith('_female'):
                self.assertEqual(filters['sex'],'female')
                self.assertTrue(any(not r['available'] for r in panel.metadata))
            self.assertEqual(panel.details['outcome_association_calls'],0)

    def test_full_browser_semantic_labels_are_equivalent_but_wrong_sex_is_refused(self):
        from .wonder import _verify_c21_semantics
        receipt=dict(series_id='wonder_homicide_deaths',population_filters=dict(
            injury_intent='Homicide (V22=3)',injury_mechanism='All mechanisms (V23=*All*)',sex='all',age='all ages'))
        _verify_c21_semantics(receipt,receipt['series_id'])
        receipt['series_id']='wonder_firearm_homicide_deaths_female'
        receipt['population_filters'].update(injury_mechanism='Firearm (GRINJ-006)',sex='Female (V7=F)')
        _verify_c21_semantics(receipt,receipt['series_id'])
        receipt['population_filters']['sex']='all'
        with self.assertRaises(ConnectorError):_verify_c21_semantics(receipt,receipt['series_id'])


if __name__=='__main__':unittest.main()
