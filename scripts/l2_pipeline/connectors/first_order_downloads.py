"""No-key official downloads; exact source byte and rights inventory.

Explicitly invoked by the hub, never by an outcome adapter. No credentials,
account creation, retries, overwrite or access-control bypass.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .common import ConnectorError, ConnectorNotReady

LICENSE = 'US federal government public domain; cite source agency'
HOSTS = {'api.census.gov', 'www2.census.gov', 'data.cdc.gov', 'nces.ed.gov', 'ucr.fbi.gov',
         'www.ncbi.nlm.nih.gov', 'www.cdc.gov', 'ftp.cdc.gov',
         'static.nhtsa.gov', 'data-openjustice.doj.ca.gov'}


def _url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password:
        raise ConnectorError('First-order download must be credential-free official HTTPS')
    if any(re.search(r'key|token|password|secret|auth', k, re.I)
            for k in urllib.parse.parse_qs(parsed.query)):
        raise ConnectorError('Credential-bearing query refused')


def _plan(url, filename, agency, **extra):
    _url(url)
    rights = LICENSE + ('; statistical use only, no reidentification' if 'NCHS' in agency else '')
    return dict(url=url, filename=filename, license=rights, source_agency=agency,
        size_bytes=None, sha256=None, status='planned_not_downloaded', **extra)


def first_order_download_plan(request):
    name = request.options['connector']
    plans = []
    if name == 'acs1_health_insurance':
        for year in request.years:
            if year == 2020 or year < 2008:
                raise ConnectorNotReady('Standard ACS1 insurance coverage available2008+ excluding2020')
            if year >= 2022:
                filename = f'acsdt1y{year}-b27001.dat'
                url = f'https://www2.census.gov/programs-surveys/acs/summary_file/{year}/table-based-SF/data/1YRData/{filename}'
            else:
                filename = f'acs1_{year}.json'
                # All B27001 estimate cells; keep below keyless API limit.
                age = request.filters.get('age_group', '19-25')
                bins = {'under6':3,'6-18':6,'19-25':9,'26-34':12,'35-44':15,'45-54':18,'55-64':21,'65-74':24,'75+':27}
                if age not in bins: raise ConnectorNotReady('Unsupported ACS insurance age bin')
                variables = [f'B27001_{bins[age]+offset+extra:03d}E' for offset in (0,28) for extra in (0,1)]
                query = urllib.parse.urlencode({'get':','.join(['NAME']+variables), 'for':'state:*'})
                url = f'https://api.census.gov/data/{year}/acs/acs1?{query}'
            plans.append(_plan(url,filename,'US Census ACS1',format='ACS B27001 published cells'))
    elif name == 'brfss_state_prevalence':
        filters = request.filters
        if set(filters) != {'questionid','responseid','breakoutid','data_value_type'}:
            raise ConnectorError('BRFSS download requires published cell identifiers')
        # Quote values as SQL string data, never arbitrary query clauses.
        def literal(v): return "'"+str(v).replace("'","''")+"'"
        where = ' AND '.join(k+'='+literal(v) for k,v in filters.items())
        where += f' AND year >= {min(request.years)} AND year <= {max(request.years)}'
        query = urllib.parse.urlencode({'$where':where,'$limit':50000,'$order':'year,locationabbr'})
        plans.append(_plan('https://data.cdc.gov/resource/dttw-5yxu.csv?'+query,
            'brfss_'+str(filters['questionid'])+f'_{min(request.years)}_{max(request.years)}.csv','CDC BRFSS',
            format='Published state prevalence CSV', max_rows=50000))
    elif name in ('ipeds_completions','ipeds_enrollment'):
        for year in request.years:
            for filename in [f'HD{year}.zip', f'C{year}_A.zip' if name=='ipeds_completions' else f'EF{year}A.zip']:
                plans.append(_plan('https://nces.ed.gov/ipeds/datacenter/data/'+filename,filename,'NCES IPEDS',format='CSV ZIP'))
    elif name == 'nchs_mortality':
        from .first_order import NCHS_SUPPORTED_CAUSES
        overdose = request.options.get('measure_id','').startswith('drug_overdose')
        if not overdose and request.filters.get('cause_name') not in NCHS_SUPPORTED_CAUSES:
            raise ConnectorNotReady('first-order outcome unavailable: cause not published in final NCHS source')
        dataset = '44rk-q6r2' if overdose else 'bi63-dtpu'
        where = f'year >= {min(request.years)} AND year <= {max(request.years)}'
        if overdose: where += " AND sex='Both Sexes' AND age='All Ages' AND race='All Races-All Origins'"
        query = urllib.parse.urlencode({'$where':where,'$limit':50000,'$order':'year,state'})
        plans.append(_plan('https://data.cdc.gov/resource/'+dataset+'.csv?'+query,
            f'nchs_{dataset}_{min(request.years)}_{max(request.years)}.csv','CDC NCHS final NVSS',
            format='Published final state mortality CSV',max_rows=50000))
    elif name == 'fbi_ucr_state':
        for year in request.years:
            if year <2010 or year>2019: raise ConnectorNotReady('Legacy UCR only2010–2019')
            plans.append(_plan(f'https://ucr.fbi.gov/crime-in-the-u.s/{year}/crime-in-the-u.s.-{year}/tables/table-5',
                f'ucr_table5_{year}.html','FBI UCR',format='Official HTML state crime table5', allow_html=True))
    elif name == 'fars_state':
        from .fars import fars_download_plan
        return fars_download_plan(request)
    elif name == 'ca_doj_crime':
        from .state_crime import ca_crime_download_plan
        return ca_crime_download_plan(request)
    elif name == 'cdc_natality':
        from .natality import natality_download_plan
        return natality_download_plan(request)
    else:
        raise ConnectorNotReady('No first-order download planner for '+name)
    return tuple(plans)


def scan_source_text(payload):
    """Skill initial normalization/heuristic check; optional installed ML scanner.

    A heuristic check is disclosed as such, never described as a clean ML scan.
    The result has data-level authority and is not proof that content is safe.
    """
    import unicodedata
    import base64
    import codecs
    import html
    text = unicodedata.normalize('NFKC',payload.decode('utf-8','replace'))
    text = ''.join(c for c in text if c not in '\u200b\u200c\u200d\u2060\ufeff' and not 0xE0000 <= ord(c) <= 0xE0080)
    text = html.unescape(text)
    decoded=[]
    for token in re.findall(r'[A-Za-z0-9+/=]{20,}',text):
        try:
            candidate=base64.b64decode(token,validate=True).decode('utf-8')
            if candidate.isprintable():decoded.append(candidate)
        except (ValueError,UnicodeError):pass
    text+='\n'+'\n'.join(decoded)+'\n'+codecs.decode(text,'rot_13')
    patterns = [r'ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions',
        r'(?:system|developer)\s*(?:prompt|message)\s*:',r'(?:send|reveal|print)\s+(?:your\s+)?(?:api\s*key|password|credentials)',
        r'you\s+are\s+(?:chatgpt|an?\s+ai\s+assistant)']
    hits = [p for p in patterns if re.search(p,text,re.I)]
    report = dict(method='normalized heuristic initial check', suspicious_patterns=hits,
        ml_scan='not available: llm_guard not installed', clean_is_proof_of_safety=False)
    try:
        from llm_guard.input_scanners import PromptInjection
    except ImportError:
        return report
    _,valid,risk = PromptInjection(threshold=0.5).scan(text)
    report.update(ml_scan='LLM Guard PromptInjection', ml_valid=valid, ml_risk=risk)
    return report


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def fetch_first_order_public(plan, destination, *, max_bytes=100_000_000, timeout_seconds=45):
    _url(plan['url'])
    if not plan.get('license'): raise ConnectorError('Source rights missing')
    destination = Path(destination)
    if any(p in {'results','insight','did_samples','.codex','.aws','.wrangler'} for p in destination.resolve().parts):
        raise ConnectorError('Protected download destination')
    if destination.name.startswith('.env') or destination.name in ('auth.json','credentials','credentials.json'):
        raise ConnectorError('Credential/configuration destination refused')
    if destination.exists(): raise FileExistsError(destination)
    try:
        req = urllib.request.Request(plan['url'],headers={'User-Agent':'L2-first-order-public-data/1.0'})
        with urllib.request.build_opener(_Redirect()).open(req,timeout=timeout_seconds) as response:
            _url(response.geturl()); final_url=response.geturl()
            payload=response.read(max_bytes+1); content_type=response.headers.get('Content-Type','')
    except (urllib.error.URLError,TimeoutError) as exc:
        raise ConnectorNotReady('Official public file unavailable; no retry or authentication attempted') from exc
    if not payload or len(payload)>max_bytes: raise ConnectorError('Empty or oversized public source')
    html = 'html' in content_type.lower() or payload.lstrip().lower().startswith((b'<html',b'<!doctype'))
    if html and not plan.get('allow_html'): raise ConnectorNotReady('HTML response refused; no bot-check bypass')
    if html and re.search(rb'recaptcha|checking your browser|verify you are human|access denied|sign in to continue',payload,re.I):
        raise ConnectorNotReady('Public source returned an access/bot-check page; no bypass attempted')
    scan = scan_source_text(payload) if html else None
    if scan and (scan['suspicious_patterns'] or scan.get('ml_valid') is False):
        raise ConnectorNotReady('Suspicious source instructions found; preserve as data and review separately')
    if plan.get('max_rows'):
        import csv,io
        count = sum(1 for _ in csv.DictReader(io.StringIO(payload.decode('utf-8-sig'))))
        if count >= plan['max_rows']: raise ConnectorError('Public CSV reaches query cap; refuse truncated extract')
    if destination.suffix=='.json': json.loads(payload)
    if destination.suffix=='.zip' and not payload.startswith(b'PK'): raise ConnectorError('ZIP signature missing')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('xb') as handle: handle.write(payload)
    return dict(plan,path=str(destination),final_url=final_url,size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),status='downloaded',retries=0,source_text_check=scan)
