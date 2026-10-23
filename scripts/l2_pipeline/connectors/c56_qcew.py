"""NAICS6244 employment through the existing exact QCEW annual adapter."""
from dataclasses import replace
from .public_annual import qcew_annual

SID='bls_qcew_private_child_day_care_employment'


def construct(request):
    if dict(request.filters)!={'ownership':'private','industry':'6244'}:
        raise ValueError('QCEW child-day-care exact private NAICS6244 filters required')
    if request.frequency!='annual':raise ValueError('QCEW child-day-care requires annual data')
    return qcew_annual(replace(request,options=dict(request.options,value_field='annual_avg_emplvl',
        source_geography='state',license='US federal government public domain; BLS attribution')))
