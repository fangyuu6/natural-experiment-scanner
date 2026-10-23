"""Unsealed V8 metadata correction; retain prior artifacts and all rates."""
import json,shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from scripts.l2_pipeline.connectors import c46_core as core,c46_fars as fars,c46_build as build
from scripts.l2_pipeline.storage import ensure_once,sha
ROOT=core.REPO; OUT=ROOT/'data/l2_pipeline/autopilot/c46'; BASE=core.BASE
catalog=ROOT/'scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V8.json'
fragment=BASE/'CATALOG_FRAGMENT.json'; inventory=BASE/'SOURCE_INVENTORY.json'
specs=json.loads(fragment.read_text())['series']; before={}
for p in (catalog,fragment,inventory):
    dest=OUT/'pre_review_catalog'/p.name;dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
        if sha(dest)!=sha(p):raise ValueError('Revision source changed')
    else:shutil.copyfile(p,dest)
    before[str(p.relative_to(ROOT))]=dict(sha256=sha(p),retained_path=str(dest.relative_to(ROOT)))
years=list(range(2010,2025));paths=[(fars.FARS_BASE/'raw' if y==2024 else fars.EXISTING)/f'FARS{y}NationalCSV.zip' for y in years]
with ThreadPoolExecutor(max_workers=4) as pool: parsed=dict(zip(years,pool.map(fars.parse_archive,paths,years)))
changes={}
for sid in ('fars_motorcyclist_fatality_rate','fbi_hate_crime_incident_rate'):
    s=specs[sid];p=ROOT/s['artifact']['path'];payload=json.loads(p.read_text());count=0
    for m in payload['metadata']:
        if sid.startswith('fars') and m['year'] in parsed and m['unit'] in parsed[m['year']]['unknown_body']:
            count+=int(bool(m['linkage_certified']));m['linkage_certified']=0;m['source_issue']='unknown_motor_vehicle_body_type'
        elif sid.startswith('fbi') and (m.get('partial_year') or m.get('provisional')):
            count+=int(bool(m['annual_usable']));m['annual_usable']=0
    extra={k:v for k,v in s.items() if k not in {'series_id','adapter','adapter_version','years','geography','population','population_filters','units','frequency','source_url','release_lag','public_available','status','license','registered_connector','artifact','support_artifact','paths','source_sha256','observed_coverage'}}
    specs[sid]=core.save_panel(sid,payload['population_filters'],payload['rows'],payload['metadata'],payload['inventory'],payload['limitations'],payload['details'],s['population'],s['source_url'],**extra)
    new=json.loads((ROOT/specs[sid]['artifact']['path']).read_text())
    assert new['rows']==json.loads(p.read_text())['rows']
    changes[sid]=dict(closed_usable_cells=count,coverage=specs[sid]['observed_coverage'],outcome_rows_byte_content_equal=True)
for p in (catalog,fragment,inventory):p.unlink()
build.assemble(specs)
ensure_once(OUT/'METADATA_REVIEW_REVISION.json',dict(prior_files=before,new_catalog=dict(path=str(catalog.relative_to(ROOT)),sha256=sha(catalog)),corrections=changes,raw_changed=False,outcome_association_calls=0,actual_data_estimation_calls=0))
print({k:v['closed_usable_cells'] for k,v in changes.items()})
