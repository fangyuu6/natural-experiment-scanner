"""Prospective population-selection metadata belonging to the current V8 catalog.

The adjunct adds only reviewed construct/population semantics. V8 source and
series bytes bound by existing cards stay intact. No source values are opened.
"""
from pathlib import Path
import json
from . import outcome_v8
from .common import ConnectorError

C50_TABLE = Path(__file__).with_name('CONSTRUCT_EQUIVALENCES_C50.json')
PATH = Path(__file__).with_name('OUTCOME_POPULATIONS_V8.json')
V9_PATH = Path(__file__).with_name('OUTCOME_POPULATIONS_V9.json')
CURRENT_PATH = Path(__file__).with_name('OUTCOME_POPULATIONS_V19.json')


def current_catalog():
    identity=json.loads(CURRENT_PATH.read_text())['catalog_id']
    allowed={'first_order_outcomes_v'+str(v):v for v in (9,10,11,12,13,14,15,16,17,18,19)}
    module=catalog_module(allowed[identity])
    return catalog(source=module.catalog(verify_sources=False),path=CURRENT_PATH)


def catalog_module(version):
    from . import outcome_v9,outcome_v10,outcome_v11
    if version==19:
        from . import outcome_v19
        return outcome_v19
    if version==18:
        from . import outcome_v18
        return outcome_v18
    if version==17:
        from . import outcome_v17
        return outcome_v17
    if version==16:
        from . import outcome_v16
        return outcome_v16
    if version==15:
        from . import outcome_v15
        return outcome_v15
    if version==14:
        from . import outcome_v14
        return outcome_v14
    if version==13:
        from . import outcome_v13
        return outcome_v13
    if version==12:
        from . import outcome_v12
        return outcome_v12
    return {9:outcome_v9,10:outcome_v10,11:outcome_v11}[version]


def catalog(*,source=None,path=None):
    source = source or outcome_v8.catalog(verify_sources=False)
    meta = json.loads((path or PATH).read_text())
    if meta['catalog_id'] != source['catalog_id']:
        raise ConnectorError('Population metadata belongs to a different catalog')
    for sid, entry in meta['series'].items():
        if sid not in source['series'] or not entry.get('construct'):
            raise ConnectorError('Unknown population metadata series/construct')
        variants = entry.get('variants', [entry])
        for variant in variants:
            ages = variant['ages']
            if len(ages) != 2 or any(type(v) is not int for v in ages) or not 0 <= ages[0] <= ages[1] <= 120:
                raise ConnectorError('Invalid population age interval')
    return source, meta


def target_metadata(target, *, family=None, meta=None, construct=None):
    meta = meta or catalog()[1]
    pointer=meta.get('construct_equivalences')
    if pointer:
        from ..storage import sha
        repo=Path(__file__).resolve().parents[3]
        table=repo/pointer['path']
        if sha(table)!=pointer['sha256']:raise ConnectorError('C49 construct-equivalence table changed')
        matches=[e for e in json.loads(table.read_text())['entries'] if e['card_construct']==target]
        if matches:
            e=matches[0]
            authority=repo/e['citation'].split(' §')[0]
            if sha(authority)!=e['authority_sha256']:raise ConnectorError('C49 equivalence authority changed')
            return next(t for t in meta['targets'] if t.get('equivalence_citation')==e['citation'] and target in t['aliases'])
    # Prospective C50 semantics are additive; previously bound JSON/table
    # bytes remain untouched. Only reviewed aliases and construct IDs match.
    from ..c50 import equivalence_target
    reviewed=equivalence_target(target,construct=construct,meta=meta,family=family)
    if reviewed:return reviewed
    for entry in sorted(meta['targets'],key=lambda e:not bool(e.get('family'))):
        if target in entry['aliases'] and (not entry.get('family') or family == entry['family']):
            return entry
    return None


def same_construct(target, series, *, version=8):
    if version in (12,13,14,15,16,17,18,19):
        module=catalog_module(version)
        source,meta=catalog(source=module.catalog(verify_sources=False),
            path=Path(__file__).with_name('OUTCOME_POPULATIONS_V'+str(version)+'.json'))
    elif version==11:
        from . import outcome_v11
        source,meta=catalog(source=outcome_v11.catalog(verify_sources=False),path=Path(__file__).with_name('OUTCOME_POPULATIONS_V11.json'))
    elif version==10:
        from . import outcome_v10
        source,meta=catalog(source=outcome_v10.catalog(verify_sources=False),path=Path(__file__).with_name('OUTCOME_POPULATIONS_V10_REVIEW1.json'))
    elif version==9:
        from . import outcome_v9
        source,meta=catalog(source=outcome_v9.catalog(verify_sources=False),path=V9_PATH)
    else:source,meta=catalog()
    definition = target_metadata(target, meta=meta)
    entry = meta['series'].get(series)
    if version==8 and entry is None:
        _,current=current_catalog()
        entry=current['series'].get(series)
        definition=target_metadata(target,meta=current)
    return bool(entry and series in source['series'] and
                (entry['construct']==target or definition and entry['construct'] == definition['construct']))
