"""Read-only L2 export adapter; no pooling, outcome fitting or release."""
from pathlib import Path
import json
import statistics
from scripts.l2_pipeline.c64 import resolve_references
from scripts.l2_pipeline.forecast_slots import n10_eligible_record


def read_export(path):
    path=Path(path);raw=json.loads(path.read_text())
    new_format=isinstance(raw,dict) and ('forecast_independence' in raw or
        isinstance(raw.get('result'),dict) and '$ref' in raw['result'])
    value=resolve_references(raw,path.parent)
    required={'design_id','spec_version','status','result','selection_denominator'}
    if new_format:required.add('l3_export_v2')
    if not isinstance(value,dict) or not required<=set(value):
        raise ValueError('Incomplete l3_meta export contract')
    if value['selection_denominator']!='all_queued_designs_including_stops':
        raise ValueError('L3 selection denominator changed')
    if 'l3_export_v2' not in value:value['l3_export_status']='FIELD_MISSING_LEGACY'
    independence=value.get('forecast_independence',{})
    if independence.get('SINGLE_CONSENSUS') is True:
        independence=dict(independence,n10_eligible=False)
        value['forecast_independence']=independence
    return value


def forecast_summary(slots, lock):
    """Validate the frozen roster without changing the legacy Q3 denominator."""
    if not lock and not slots:
        return dict(forecast_slot_count=0,forecast_median=None,forecast_median_of_five=None,
            forecast_roster_kind='no_locked_forecasts')
    personas=(lock or {}).get('personas',[])
    prospective=bool((lock or {}).get('forecast_slots'))
    expected=3 if prospective else 5
    if (not isinstance(personas,list) or len(personas)!=expected or
            any(not isinstance(p,str) or not p for p in personas) or len(set(personas))!=expected or
            len(slots)!=expected or {s.get('persona') for s in slots}!=set(personas) or
            any(s.get('lock_verified') is not True for s in slots) or
            prospective and set(lock['forecast_slots'])!=set(personas)):
        raise ValueError('Forecast slots differ from the verified locked roster')
    median=statistics.median(s['center'] for s in slots)
    return dict(forecast_slot_count=expected,forecast_median=median,
        forecast_median_of_five=median if expected==5 else None,
        forecast_roster_kind='prospective_three' if prospective else 'legacy_five')


def n10_eligible(value):
    """N10 membership of an export: ONE rule, `forecast_slots.n10_eligible_record` (not SINGLE_CONSENSUS, >= 3 personas, >= 2 distinct worker ids, n_eff > 1).

    Worker data is read from the export's own forecast_slots; missing or null worker ids, or an export without slots, are not eligible
    (Q3 ruling 3b/3c, HUB_RULING_20261023_PARK_REOPEN.md). Nothing is written: a one-worker lock whose stored flag says True simply reads as not independent.
    """
    return n10_eligible_record(value.get('forecast_independence',{}))
