"""Offline CDC YRBS weighted prevalence. No policy join or associations."""
import csv,gzip,json,math
from collections import defaultdict
from pathlib import Path
from . import c46_core
from .common import STATES
from ..storage import ensure_once,sha

REPO=Path(__file__).resolve().parents[3]
SOURCE=REPO/'data/l2_pipeline/connectors_v2/cdc_yrbs_sadc/extracted'
BASE=REPO/'data/l2_pipeline/connectors_v2/c63_yrbs'
CONSTRUCTS=dict(cig_current='youth_cigarette_smoking',evp_current='youth_electronic_vapor_use',alc_current='youth_alcohol_use',
    binge='youth_binge_drinking',suicide_attempt='adolescent_suicide_attempt',text_driving='youth_texting_while_driving',
    mj_current='youth_marijuana_use',seatbelt_rarely_never='youth_seat_belt_nonuse',
    ride_drinking_driver='youth_riding_with_drinking_driver',drink_drive='youth_drinking_and_driving')
FILTERS={'grades':'9-12','breakout':'Overall'}


def aggregate(paths,indicators):
    """Single streaming pass, keeping release/site/wave separate until selection."""
    totals=defaultdict(lambda:[0,0.,0.]);latest={};rows=0
    for path in paths:
        with gzip.open(path,'rt',newline='') as f:
            reader=csv.DictReader(f)
            if not set(indicators)<=set(reader.fieldnames):raise ValueError('Missing mapped indicator')
            for r in reader:
                release=int(r['release']);year=int(r['year']);site=r['sitecode']
                if year%2!=1 or not 1991<=year<=release:raise ValueError('Invalid YRBS wave')
                key=(site,year);latest[key]=max(release,latest.get(key,0));rows+=1
                try:w=float(r['weight'])
                except ValueError:continue
                if not math.isfinite(w) or w<=0:continue
                for indicator in indicators:
                    if r[indicator] not in {'1','2'}:continue
                    a=totals[(release,site,year,indicator)];a[0]+=1;a[1]+=w
                    if r[indicator]=='1':a[2]+=w
    selected={}
    for (site,year),release in latest.items():
        for indicator in indicators:
            n,weight,yes=totals.get((release,site,year,indicator),(0,0.,0.))
            suspect=site=='VT' and year==2013
            suppressed=0<n<100
            selected[(site,year,indicator)]=dict(release=release,n=n,weight=weight,
                value=100*yes/weight if n>=100 and weight>0 and not suspect else None,
                suppressed=suppressed,suspect=suspect)
    return selected,rows


def build(source=SOURCE,base=BASE):
    source=Path(source);base=Path(base)
    mapping=json.loads((source/'INDICATOR_MAPPING.json').read_text())
    if not set(CONSTRUCTS)<=mapping['indicators'].keys():raise ValueError('Mapping lacks stable indicators')
    paths=sorted(source.glob('*.csv.gz'));inventory=[];sources=[]
    for path in paths:
        receipt=path.with_name(path.name+'.receipt.json');r=json.loads(receipt.read_text())
        if (r['output_sha256']!=sha(path) or r['output_bytes']!=path.stat().st_size or
            r['mapping_sha256']!=sha(source/'INDICATOR_MAPPING.json')):raise ValueError('Extraction binding changed')
        raw=source.parent/r['source_zip']['relative_path'];download=json.loads(raw.with_name(raw.name+'.receipt.json').read_text())
        if download['sha256']!=r['source_zip']['sha256'] or sha(raw)!=download['sha256']:raise ValueError('Raw source binding changed')
        inventory.append(dict(path=str(path.relative_to(REPO)),sha256=sha(path),size_bytes=path.stat().st_size,
            url=download['url'],license=download['license'],receipt_path=str(receipt.relative_to(REPO)),receipt_sha256=sha(receipt)))
        sources.append(dict(file=str(raw.relative_to(REPO)),url=download['url'],size_bytes=raw.stat().st_size,license=download['license'],new_download=False))
    for name in ['INDICATOR_MAPPING.json','SITE_CODES.csv','ROWCOUNT_CHECK.csv']:
        path=source/name;inventory.append(dict(path=str(path.relative_to(REPO)),sha256=sha(path),size_bytes=path.stat().st_size,url='https://www.cdc.gov/yrbs/data/index.html',license='Derived from existing CDC public-use extraction; see source receipts'))
    cells,nrows=aggregate(paths,CONSTRUCTS)
    sites=sorted({k[0] for k in cells});specs={};summaries={}
    site_map={r['sitecode']:r['postal_guess_from_name'] for r in csv.DictReader((source/'SITE_CODES.csv').open())}
    if any(site_map[s]!=s for s in sites if s not in {'AZB','NYA'}):raise ValueError('Unreviewed site crosswalk')
    old=c46_core.BASE;c46_core.BASE=base
    try:
        for indicator,construct in CONSTRUCTS.items():
            sid='yrbs_'+indicator+'_prevalence';observed=sorted({y for (s,y,i),v in cells.items() if i==indicator and v['n']})
            years=list(range(min(observed),max(observed)+1,2));rows=[];metadata=[]
            reverse={site_map[s]:s for s in sites if s!='NYA'}
            # NYA remains a distinct companion site. Its cells never replace NY.
            for unit in sorted(set(STATES.values())|{'NYA'}):
                site='NYA' if unit=='NYA' else reverse.get(unit)
                for y in years:
                    v=cells.get((site,y,indicator),{});available=v.get('value') is not None
                    usable=available and unit!='NYA'
                    reason='VT 2013 guide row-count mismatch' if v.get('suspect') else 'CDC n<100 suppression' if v.get('suppressed') else 'No eligible weighted respondents in selected release' if not available else 'NY-excl-NYC; not statewide NY' if unit=='NYA' else ''
                    rows.append(dict(unit=unit,year=y,outcome_value=v.get('value'),outcome_unit='percent of high-school students (weighted prevalence)',
                        sample_n=v.get('n',0),selected_release=v.get('release'),sitecode=site,geography='NY-excl-NYC' if unit=='NYA' else 'state'))
                    metadata.append(dict(unit=unit,year=y,frequency='biennial',available=available,annual_usable=usable,
                        linkage_certified=unit!='NYA',suppressed=v.get('suppressed',False),unreliable=v.get('suspect',False),
                        missing_reason=reason,series_break='seatbelt qn8 definition changed; stable raw-q8 derived series used' if indicator=='seatbelt_rarely_never' and y>=2021 else '',source_issue=reason))
            limitations=['Odd-year waves; no annual interpolation. n<100 suppressed; weighted point prevalence only, no survey variance.',
                'Latest release per site/wave; CT DE LA ME RI SD WV retain older releases; missing 2023 waves stay missing.',
                'NYA is NY-excl-NYC and never spliced into statewide NY; excluded from state linkage.',
                'VT 2013 suspect, unavailable for estimation; grade labels refer to survey universe, missing grade responses are retained.']
            specs[sid]=c46_core.save_panel(sid,FILTERS,rows,metadata,inventory,limitations,
                dict(indicator=indicator,formula='100 * sum(weight * (indicator == 1)) / sum(weight), valid 1/2 responses and finite positive weights',
                     suppression_min_n=100,source_microdata_rows=nrows,mapping_sha256=sha(source/'INDICATOR_MAPPING.json')),
                'State-site high-school students grades 9-12', 'https://www.cdc.gov/yrbs/data/index.html',frequency='biennial')
            specs[sid].update(adapter='c63_yrbs_artifact',registered_connector='outcome_v14_'+sid,series_breaks=limitations)
            summaries[sid]=dict(years=years,source_sites=len({s for (s,y,i),v in cells.items() if i==indicator and v['n']}),
                statewide_units=len({m['unit'] for m in metadata if m['annual_usable']}),usable_cells=sum(m['annual_usable'] for m in metadata),
                coverage=specs[sid]['observed_coverage'],NYA_spliced=False)
    finally:c46_core.BASE=old
    ensure_once(base/'SERIES.json',specs)
    ensure_once(base/'COVERAGE.json',dict(source_sites=sites,source_site_count=len(sites),microdata_rows=nrows,series=summaries,outcome_association_calls=0))
    ensure_once(base/'SOURCE_FILES.json',dict(new_downloads=[],reused=sources))
    return specs

if __name__=='__main__':build()
