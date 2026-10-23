"""Assemble V11 from pinned public bytes; never fit or dispatch designs."""
from copy import deepcopy
import json
from pathlib import Path
from . import outcome_v10, c56_eia, c56_natality, c56_qcew
from .c56_core import REPO, BASE
from .wonder import write_once
from ..storage import sha
CATALOG=Path(__file__).with_name('OUTCOME_CATALOG_V11.json')
POPULATIONS=Path(__file__).with_name('OUTCOME_POPULATIONS_V11.json')


def assemble():
    old=outcome_v10.catalog(verify_sources=False);catalog=deepcopy(old)
    catalog.update(schema_version=11,catalog_id='first_order_outcomes_v11',
        inherited_catalog=dict(path=str(outcome_v10.CATALOG_PATH.relative_to(REPO)),sha256=sha(outcome_v10.CATALOG_PATH)),
        registered_entrypoint='scripts.l2_pipeline.connectors.outcome_v11.construct',
        registration_entrypoint='scripts.l2_pipeline.connectors.outcome_v11.register',
        acquisition_entrypoint='scripts.l2_pipeline.connectors.c56_build.assemble')
    new={**c56_eia.build(),**c56_natality.build()}
    new[c56_qcew.SID]=dict(series_id=c56_qcew.SID,adapter='c56_qcew',adapter_version='existing_qcew_annual_v1',
        geography='workplace state',years=list(range(2001,2025)),population='Private UI-covered jobs in NAICS6244 Child Day Care Services; workplace state; no worker/child demographics',
        population_filters={'ownership':'private','industry':'6244'},units='payroll jobs',frequency=['annual'],
        source_url='https://data.bls.gov/cew/data/api/{year}/a/industry/6244.csv',release_lag='6–9 months; revised',
        public_available=True,status='implemented_local_adapter_no_new_download',license='US federal government public domain; BLS attribution',
        construct='child_day_care_payroll_employment',source_validation_required=True,observed_coverage={'basis':'publisher_declared; local coverage unverified'})
    catalog['series'].update(new)
    for sid,s in catalog['series'].items():s['registered_connector']='outcome_v11_'+sid
    for sid,s in new.items():catalog['targets'][s['construct']]=dict(series=[k for k,v in new.items() if v['construct']==s['construct']],selection_authorized=True)
    catalog['targets']['private_sector_weekly_wage']=dict(series=['bls_qcew_private_weekly_wage','bls_ces_private_weekly_earnings'],selection_authorized=True)
    catalog['targets']['wage_level']=deepcopy(catalog['targets']['private_sector_weekly_wage'])
    previous=Path(__file__).with_name('OUTCOME_POPULATIONS_V10_REVIEW1.json');meta=json.loads(previous.read_text())
    meta.update(catalog_id=catalog['catalog_id'],inherited_population_metadata=dict(path=str(previous.relative_to(REPO)),sha256=sha(previous)))
    for sid,s in new.items():
        if sid=='nchs_summary_fertility_rate':continue # retain reviewed births/GFR semantics
        meta['series'][sid]=dict(construct=s['construct'],ages=[0,120],restrictions={},filters=s['population_filters'],
            population_basis=s['population'],population_unit='payroll_job' if sid==c56_qcew.SID else 'electricity_kWh' if 'price' in sid else 'state_measure',
            metadata_basis='C56 source definitions; no empirical eligible-subgroup shares or outcome associations')
    for sid in ['bls_qcew_private_weekly_wage','bls_ces_private_weekly_earnings']:
        meta['series'][sid]=dict(construct='private_sector_weekly_wage',ages=[16,120],restrictions={},filters={},
            population_basis=catalog['series'][sid]['population'],population_unit='payroll_job',
            metadata_basis='BLS published all-private establishment wages; no demographic attribution; C56 requested wage metadata')
    for construct in sorted({s['construct'] for s in new.values()}):
        meta['targets'].append(dict(construct=construct,aliases=[construct],ages=[0,120],restrictions={},rule='C56 explicit published construct'))
    meta['targets'].append(dict(construct='private_sector_weekly_wage',aliases=['private_sector_weekly_wage','wage_level'],
        ages=[16,120],restrictions={},preferred_series='bls_qcew_private_weekly_wage',
        rule='C56: UI-covered private jobs include sectors outside CES private nonfarm; no worker demographics or employer-size eligibility attribution'))
    write_once(CATALOG,catalog);write_once(POPULATIONS,meta)
    write_once(BASE/'CATALOG_FRAGMENT.json',dict(series=new,downloads=[],outcome_association_calls=0))
    return catalog,meta

if __name__=='__main__':assemble()
