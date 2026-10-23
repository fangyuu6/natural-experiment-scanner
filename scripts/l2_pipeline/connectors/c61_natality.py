"""Receipt-bound 1995 GFR splice; original C56 catalog/artifacts are immutable."""
from copy import deepcopy
import csv,json
from pathlib import Path
from . import outcome_v12
from .c46_core import REPO,coverage,source_record
from .common import NAMES,ConnectorError
from ..storage import ensure_once,sha
BASE=REPO/'data/l2_pipeline/connectors_v2/c61'
RAW=REPO/'data/l2_pipeline/connectors_v2/wonder/raw'
SID='nchs_summary_fertility_rate'


def transcribed_gfr(births,population):
    def read(path):
        rows=list(csv.DictReader(Path(path).open(),delimiter='\t'))
        keys=[(NAMES[r['State']],int(r['Year'])) for r in rows]
        if len(keys)!=len(set(keys)):raise ConnectorError('Duplicate WONDER transcription key')
        return dict(zip(keys,rows))
    b,p=read(births),read(population)
    if set(b)!=set(p) or len(b)!=51 or {y for _,y in b}!={1995}:raise ConnectorError('WONDER transcription coverage mismatch')
    result={}
    for key,r in b.items():
        count=int(r['Births'].replace(',',''));den=int(p[key]['FemalePopulation15_44'].replace(',',''))
        if int(r['FemalePopulation15_44'].replace(',',''))!=den or count<=9 or den<=0:raise ConnectorError('WONDER transcription denominator/suppression mismatch')
        result[key]=dict(value=count/den*1000,births=count,female_population_15_44=den)
    if sum(r['births'] for r in result.values())!=3899589 or sum(r['female_population_15_44'] for r in result.values())!=60367765:
        raise ConnectorError('WONDER displayed totals mismatch')
    return result


def build():
    catalog=deepcopy(outcome_v12.catalog(verify_sources=False));spec=catalog['series'][SID]
    original=REPO/spec['artifact']['path']
    if sha(original)!=spec['artifact']['sha256']:raise ConnectorError('C56 natality artifact changed')
    data=json.loads(original.read_text());prior=deepcopy(data)
    births=RAW/'wonder_natality_state__D10_1995.tsv';pop=RAW/'wonder_population_female_15_44__D178_1995.tsv'
    sources=[source_record(p) for p in (births,pop)];values=transcribed_gfr(births,pop)
    for r in data['rows']:
        if r['year']==1995:
            v=values[r['unit'],r['year']]
            r.update(outcome_value=v['value'],source='WONDER transcription',births=v['births'],female_population_15_44=v['female_population_15_44'])
    for m in data['metadata']:
        if m['year']==1995:m.update(available=1,annual_usable=1,linkage_certified=1,missing_reason='',source_issue='WONDER transcription')
    data['inventory']+=sources
    data['limitations']=[x for x in data['limitations'] if not x.startswith('1995 ')]
    data['limitations'].append('1995: WONDER results-table transcription; all-age births / resident females 15–44 × 1000. Bridged-race intercensal denominator vintage may differ from adjacent report vintages.')
    data['details'].update(c61_splice=dict(year=1995,source='WONDER transcription',formula='all births / female population 15–44 × 1000',original_artifact=spec['artifact'],count_total=3899589,denominator_total=60367765),outcome_association_calls=0)
    artifact=ensure_once(BASE/'parsed'/(SID+'.json'),data)
    support=ensure_once(BASE/'support'/(SID+'.json'),dict(series_id=SID,population_filters=data['population_filters'],metadata=data['metadata'],coverage=coverage(data['metadata']),artifact_path=str(artifact.relative_to(REPO)),artifact_sha256=sha(artifact),outcome_values_in_sidecar=False,outcome_association_calls=0))
    spec.update(adapter='c61_artifact',adapter_version='c61_gfr_splice_v1',artifact=dict(path=str(artifact.relative_to(REPO)),sha256=sha(artifact)),support_artifact=dict(path=str(support.relative_to(REPO)),sha256=sha(support)),paths=[str(artifact.relative_to(REPO))],source_sha256={str(artifact.relative_to(REPO)):sha(artifact)},observed_coverage=coverage(data['metadata']))
    catalog.update(schema_version=13,catalog_id='first_order_outcomes_v13',inherited_catalog=dict(path=str(outcome_v12.CATALOG_PATH.relative_to(REPO)),sha256=sha(outcome_v12.CATALOG_PATH)),registered_entrypoint='scripts.l2_pipeline.connectors.outcome_v13.construct',registration_entrypoint='scripts.l2_pipeline.connectors.outcome_v13.register',acquisition_entrypoint='scripts.l2_pipeline.connectors.c61_natality.build')
    for s in catalog['series'].values():s['registered_connector']='outcome_v13_'+s['series_id']
    ensure_once(Path(__file__).with_name('OUTCOME_CATALOG_V13.json'),catalog)
    previous=Path(__file__).with_name('OUTCOME_POPULATIONS_V12.json');metadata=json.loads(previous.read_text());metadata.update(catalog_id=catalog['catalog_id'],inherited_population_metadata=dict(path=str(previous.relative_to(REPO)),sha256=sha(previous)))
    ensure_once(Path(__file__).with_name('OUTCOME_POPULATIONS_V13.json'),metadata)
    unchanged=all([r for r in prior[k] if r['year']!=1995]==[r for r in data[k] if r['year']!=1995] for k in ['rows','metadata'])
    ensure_once(BASE/'GFR_SPLICE.json',dict(source_files=sources,new_downloads=[],cells_filled=51,other_years_unchanged=unchanged,original_artifact_unchanged=sha(original)==data['details']['c61_splice']['original_artifact']['sha256'],source='WONDER transcription',outcome_association_calls=0))
    return spec

if __name__=='__main__':print(build()['observed_coverage']['by_year']['1995'])
