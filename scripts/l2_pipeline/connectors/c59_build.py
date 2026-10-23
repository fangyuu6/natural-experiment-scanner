"""Additive V12 assembly from pinned source bytes, without design or outcome associations."""
from copy import deepcopy
import csv,json
from pathlib import Path
from . import outcome_v11,c59_fiscal,c59_bps,c59_ui,c59_survey
from .c59_core import REPO,BASE
from .wonder import write_once
from ..storage import sha
CATALOG=Path(__file__).with_name('OUTCOME_CATALOG_V12.json')
POPULATIONS=Path(__file__).with_name('OUTCOME_POPULATIONS_V12.json')


def survey_specs():
    cached=BASE/'surveys/SERIES_SPECS.json'
    if not cached.is_file():return c59_survey.build()
    specs=json.loads(cached.read_text());checked=set()
    for spec in specs.values():
        for key in ('artifact','support_artifact'):
            pointer=spec[key];path=REPO/pointer['path']
            if sha(path)!=pointer['sha256']:raise ValueError('C59 cached survey artifact changed')
        artifact=json.loads((REPO/spec['artifact']['path']).read_text())
        for item in artifact['inventory']:
            if item['path'] not in checked:
                if sha(REPO/item['path'])!=item['sha256']:raise ValueError('C59 cached survey source changed')
                if 'receipt_path' in item and sha(REPO/item['receipt_path'])!=item['receipt_sha256']:raise ValueError('C59 cached survey receipt changed')
                checked.add(item['path'])
    return specs


def assemble():
    catalog=deepcopy(outcome_v11.catalog(verify_sources=False))
    catalog.update(schema_version=12,catalog_id='first_order_outcomes_v12',
        inherited_catalog={'path':str(outcome_v11.CATALOG_PATH.relative_to(REPO)),'sha256':sha(outcome_v11.CATALOG_PATH)},
        registered_entrypoint='scripts.l2_pipeline.connectors.outcome_v12.construct',
        registration_entrypoint='scripts.l2_pipeline.connectors.outcome_v12.register',
        acquisition_entrypoint='scripts.l2_pipeline.connectors.c59_build.assemble')
    new={**c59_fiscal.build(),**c59_ui.build(),**c59_bps.build(),**survey_specs()}
    previous=Path(__file__).with_name('OUTCOME_POPULATIONS_V11.json');meta=json.loads(previous.read_text())
    meta.update(catalog_id=catalog['catalog_id'],inherited_population_metadata={'path':str(previous.relative_to(REPO)),'sha256':sha(previous)})
    for sid,spec in new.items():
        pm=spec.pop('population_metadata',{});construct=spec['construct'];aliases=pm.pop('aliases',[construct])
        metadata=dict(construct=construct,ages=[0,120],restrictions={},filters=spec['population_filters'],
            population_basis=spec['population'],population_unit='state_measure',
            metadata_basis='C59 source-declared construct and population; outcome-only descriptive aggregation; no policy associations')
        metadata.update(pm);meta['series'][sid]=metadata
        target=dict(construct=construct,aliases=aliases,ages=metadata['ages'],restrictions=metadata['restrictions'],rule='C59 source-defined exact construct')
        # Preserve prior reviewed aliases; add only novel exact targets.
        matched=next((t for t in meta['targets'] if t.get('construct')==construct and t.get('ages')==target['ages'] and t.get('restrictions')==target['restrictions']),None)
        if matched is None:meta['targets'].append(target)
        else:
            for alias in aliases:
                if alias not in matched['aliases']:matched['aliases'].append(alias)
        for alias in set(aliases+[construct]):
            dest=catalog['targets'].setdefault(alias,dict(series=[],selection_authorized=True))
            if sid not in dest['series']:dest['series'].append(sid)
    catalog['series'].update(new)
    for sid,spec in catalog['series'].items():spec['registered_connector']='outcome_v12_'+sid
    write_once(CATALOG,catalog);write_once(POPULATIONS,meta)
    receipts={}
    for spec in new.values():
        artifact=json.loads((REPO/spec['artifact']['path']).read_text())
        for r in artifact['inventory']:receipts[r['path']]=r
    downloads=[]
    for p in sorted((BASE/'docs').glob('*.receipt.json')):
        r=json.loads(p.read_text());r['path']=str(p.with_name(p.name.removesuffix('.receipt.json')).relative_to(REPO));downloads.append(r)
    write_once(BASE/'DOWNLOADS_C59.json',{'files':downloads,'all_requests_use_generic_user_agent':True})
    write_once(BASE/'SOURCE_INVENTORY.json',dict(sources=list(receipts.values()),outcome_association_calls=0))
    write_once(BASE/'CATALOG_FRAGMENT.json',dict(series=new,outcome_association_calls=0))
    report=BASE/'BUILD_REPORT.md'
    lines=['# C59 catalog V12 connector report','',
        'Only outcome series and descriptive availability were constructed; no policy associations or fits.',
        'All previous catalog and population JSON bytes remain unchanged. V12 adds '+str(len(new))+' locally bound series (including a new QCEW6244 local identity, preserving the V11 adapter).',
        'Sources and every reused/downloaded file URL, byte size, license and hash: SOURCE_INVENTORY.json; original 797-file inventory: ../DOWNLOAD_MANIFEST_20261011.csv.',
        'Every new download (URL, exact byte size, license, hash): DOWNLOADS_C59.json. Includes ETA5159 map and unsuccessful public DOL recipiency form probes; no authentication or bypass.',
        'Initial local PDF text normalization/heuristic injection scan found no matching patterns; ML detector unavailable. Scan is not proof of safety.',
        'UI recipiency skipped: exact all-unemployed denominator not on disk; LAUS rounded rates are not a valid substitute. Official Chartbook CSV returned empty and year requests returned only default1976. See UI_RECIPIENCY_STATUS.json.',
        'ASFIN uses clean labeled 2012–2024 aggregates; older unlabeled layouts not spliced without comparable verified aggregates. State-only fiscal sources exclude DC.',
        'STC and permits retain 2025 as missing when the pinned population denominator is absent. No extrapolation or interpolation.',
        '', '|Series|Available cells|Years|','|---|---:|---|']
    for sid,s in new.items():lines.append(f"|{sid}|{s['observed_coverage']['available_cells']}|{min(s['years'])}–{max(s['years'])}|")
    report.write_text('\n'.join(lines)+'\n')
    return catalog,meta

if __name__=='__main__':assemble()
