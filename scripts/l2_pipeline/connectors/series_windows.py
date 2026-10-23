"""Prospective series definition boundaries; historical bound catalogs untouched."""

def definition_metadata(series_id):
    if series_id not in {'cdc_opioid_dispensing','cdc_opioid_dispensing_rate'}:return {}
    return dict(series_breaks=[dict(year=2019,definition_change=True,
        before='pharmacy_location',after='prescriber_location')],
        definition_segments=[dict(years=[2006,2018],definition='pharmacy_location'),
                             dict(years=[2019,2024],definition='prescriber_location')],
        consistent_definition_window=[2006,2018],
        window_edge_rule='C55 §1; definition changes are window edges',
        window_authority='docs/plans/l2-pipeline/HUB_RULING_C55.md')
