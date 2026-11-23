"""Market data retrieval and processing from official sources.

Includes CSV parsing, data validation and normalization.
"""
from datetime import date
from io import StringIO
from typing import Any
from concurrent.futures import ThreadPoolExecutor
import json
import re
import csv
import pandas as pd
import requests

BBK = 'https://api.statistiken.bundesbank.de/rest/data/BBSSY/'
ECB = 'https://data-api.ecb.europa.eu/service/data/YC/'
EUROSTAT = 'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr'

BUND_KEYS = {
    2: 'D.REN.EUR.A610.000000WT0202.A',
    5: 'D.REN.EUR.A620.000000WT0505.A',
    7: 'D.REN.EUR.A607.000000WT7070.A',
    10: 'D.REN.EUR.A630.000000WT1010.A',
    15: 'D.REN.EUR.A615.000000WT1515.A',
    20: 'D.REN.EUR.A612.000000WT2020.A',
    30: 'D.REN.EUR.A640.000000WT3030.A',
}
ECB_SPOT_BASE = 'B.U2.EUR.4F.G_N_A.SV_C_YM.SR_'
OBS_COLS = ['date','country','tenor','yield_pct','source','curve_type','source_url']


def get(url: str, params: dict) -> requests.Response:
    try:
        resp = requests.get(url, params=params, headers={'Accept': 'text/csv,application/json', 'User-Agent': 'EGB-Inflation-Desk-Research/1.0'}, timeout=24)
        resp.raise_for_status()
        return resp
    except requests.RequestException as exc:
        raise ValueError(f'Official download unavailable: {exc}') from exc


def parse_sdmx_csv(raw: str) -> pd.DataFrame:
    """Parse ECB and Bundesbank SDMX CSV files.

    Handles comma and semicolon delimiters, selects columns by name
    and drops missing observations.
    """
    if not raw or not raw.strip():
        raise ValueError('Empty SDMX CSV returned by provider')

    text = raw.lstrip('\ufeff')
    first_line = next((line for line in text.splitlines() if line.strip()), '')
    delimiter = ';' if first_line.count(';') > first_line.count(',') else ','
    try:
        df = pd.read_csv(StringIO(text), sep=delimiter, dtype=str, low_memory=False)
    except (UnicodeDecodeError, pd.errors.ParserError, ValueError) as exc:
        raise ValueError(f'Invalid CSV from provider: {exc}') from exc

    df.columns = (df.columns.astype(str).str.strip()
                  .str.replace('\ufeff', '', regex=False))
    if not {'TIME_PERIOD', 'OBS_VALUE'}.issubset(df.columns):
        raise ValueError(
            f'Unexpected SDMX fields {list(df.columns)[:12]}; '
            f'detected delimiter {delimiter!r}; data not used'
        )

    out = df[['TIME_PERIOD', 'OBS_VALUE']].rename(
        columns={'TIME_PERIOD': 'date', 'OBS_VALUE': 'yield_pct'}
    ).copy()
    out['date'] = pd.to_datetime(out['date'].str.strip(), errors='coerce')
    out['yield_pct'] = pd.to_numeric(out['yield_pct'].str.strip(), errors='coerce')
    out = out.dropna(subset=['date', 'yield_pct']).sort_values('date')
    if out.empty or out.duplicated('date').any():
        raise ValueError('Missing or duplicated official observations')
    if (out['yield_pct'].abs() > 100).any():
        raise ValueError('Unexpected yield units: official percentage exceeds 100')
    return out.reset_index(drop=True)


def validate_observations(df: pd.DataFrame) -> pd.DataFrame:
    missing = set(OBS_COLS) - set(df.columns)
    if missing:
        raise ValueError(f'Missing observed market columns: {sorted(missing)}')
    out = df[OBS_COLS].copy()
    out['date'] = pd.to_datetime(out['date'], errors='coerce').dt.normalize()
    out['country'] = out['country'].astype(str).str.strip()
    out['tenor'] = pd.to_numeric(out['tenor'], errors='coerce')
    out['yield_pct'] = pd.to_numeric(out['yield_pct'], errors='coerce')
    if out[['date','country','tenor','yield_pct']].isna().any().any():
        raise ValueError('Missing date, country, tenor, or yield in market data')
    if out.duplicated(['date','country','tenor','curve_type']).any():
        raise ValueError('Duplicate country/tenor/date/definition; choose one official series')
    if ((out['tenor'] <= 0) | (out['tenor'] > 100) | (out['yield_pct'].abs() > 100)).any():
        raise ValueError('Unreasonable tenor/yield values or incompatible units')
    if out['source'].astype(str).str.strip().eq('').any():
        raise ValueError('Market source is mandatory')
    return out.sort_values(['date','country','tenor']).reset_index(drop=True)


def bund_history(start: str = '2025-01-01', end: str | None = None, tenors=(2,5,7,10,15,20,30)):
    end = end or date.today().isoformat()
    
    tenors = tuple(tenors)
    for tenor in tenors:
        if tenor not in BUND_KEYS:
            raise ValueError(f'Unverified Bundesbank tenor: {tenor}')

    def download_tenor(tenor):
        url = BBK + BUND_KEYS[tenor]
        try:
            r = get(url, {'startPeriod': start, 'endPeriod': end, 'format': 'sdmx_csv'})
            df = parse_sdmx_csv(r.text)
            df['country'] = 'Germany'
            df['tenor'] = tenor
            df['source'] = 'Deutsche Bundesbank'
            df['curve_type'] = 'Current Federal security benchmark; original maturity'
            df['source_url'] = url
            return df, None
        except ValueError as exc:
            return None, f'Bund {tenor}Y: {exc}'

    with ThreadPoolExecutor(max_workers=min(len(tenors), 7)) as pool:
        fetched = list(pool.map(download_tenor, tenors))
    frames = [frame for frame, _ in fetched if frame is not None]
    errors = [error for _, error in fetched if error]
    if not frames:
        raise ValueError('No Bundesbank series loaded. ' + ' | '.join(errors))
    return validate_observations(pd.concat(frames, ignore_index=True)), errors


def ecb_aaa_spot(start='2025-01-01', end: str | None = None, tenors=(1,2,3,5,7,10,15,20,30)):
    end = end or date.today().isoformat()
    frames, errors = [], []
    for tenor in tenors:
        url = ECB + ECB_SPOT_BASE + f'{tenor}Y'
        try:
            resp = get(url, {'startPeriod': start, 'endPeriod': end, 'format':'csvdata'})
            df = parse_sdmx_csv(resp.text)
            df['country'] = 'Euro area AAA'
            df['tenor'] = tenor
            df['source'] = 'European Central Bank'
            df['curve_type'] = 'Euro-area AAA fitted continuous zero-coupon spot'
            df['source_url'] = url
            frames.append(df)
        except ValueError as exc:
            errors.append(f'ECB AAA {tenor}Y: {exc}')
    if not frames:
        raise ValueError('No ECB AAA spot curves loaded. ' + ' | '.join(errors))
    return validate_observations(pd.concat(frames,ignore_index=True)), errors


def parse_eurostat_json(payload: dict) -> pd.DataFrame:
    """Parse a single JSON-stat series and validate its dimensions."""
    dims, sizes = payload['id'], payload['size']
    if 'time' not in dims or any(s != 1 for d,s in zip(dims,sizes) if d != 'time'):
        raise ValueError('Eurostat response contains multiple categories, not one series')
    time_dim = payload['dimension']['time']['category']['index']
    values = payload.get('value')
    if values is None:
        raise ValueError('Eurostat has no observations for this query')
    rows = []
    for label, ix in time_dim.items():
        val = values[ix] if isinstance(values,list) and ix < len(values) else values.get(str(ix)) if isinstance(values,dict) else None
        if val is not None:
            rows.append((pd.Timestamp(label), float(val)))
    if not rows:
        raise ValueError('Eurostat has no non-missing observations')
    return pd.DataFrame(rows,columns=['date','inflation_pct']).sort_values('date').reset_index(drop=True)


def eurostat_hicp(geo='EA', category='TOTAL', start='2024-01', measure='RCH_A'):
    """Retrieve Eurostat HICP data using the ECOICOP 2 classification.

    Supports annual inflation rates (RCH_A) and price indices (I25).
    """
    params = {'lang':'EN','freq':'M','geo':geo,'coicop18':category,'unit':measure,'sinceTimePeriod':start}
    resp = get(EUROSTAT,params)
    try:
        output = parse_eurostat_json(resp.json())
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f'Eurostat dataset/selection unavailable: {exc}') from exc
    output['geo'], output['category'],output['source'],output['unit'] = geo,category,'Eurostat prc_hicp_minr',measure
    return output



# Verified ECB country-level monthly Maastricht/convergence 10Y government yields.
ECB_IRS = 'https://data-api.ecb.europa.eu/service/data/IRS/'
ECB_IRS_COUNTRIES = {
    'DE': 'Germany', 'FR': 'France', 'IT': 'Italy',
    'ES': 'Spain', 'PT': 'Portugal',
}


def ecb_country_10y_monthly(start: str = '2023-01', end: str | None = None):
    """Fetch monthly ECB/ESCB 10Y sovereign yield data for five euro-area countries.

    Returns observations and download errors, with monthly data
    identified separately from daily market references.
    """
    end = (end or date.today().isoformat())[:7]
    start = str(start)[:7]
    def download_country(item):
        country_code, name = item
        key = f'M.{country_code}.L.L40.CI.0000.EUR.N.Z'
        url = ECB_IRS + key
        try:
            response = get(url, {'startPeriod': start, 'endPeriod': end, 'format': 'csvdata'})
            frame = parse_sdmx_csv(response.text)
            frame['country'] = name
            frame['tenor'] = 10
            frame['source'] = 'ECB/ESCB IRS monthly convergence government yield'
            frame['curve_type'] = 'MONTHLY average sovereign 10Y (convergence), not daily'
            frame['source_url'] = url
            return frame, None
        except ValueError as exc:
            return None, f'{name} monthly 10Y: {exc}'

    with ThreadPoolExecutor(max_workers=5) as pool:
        fetched = list(pool.map(download_country, ECB_IRS_COUNTRIES.items()))
    frames = [frame for frame, _ in fetched if frame is not None]
    errors = [error for _, error in fetched if error]
    if not frames:
        raise ValueError('No ECB monthly sovereign series loaded. ' + ' | '.join(errors))
    return validate_observations(pd.concat(frames, ignore_index=True)), errors

def read_authorized_market_csv(upload) -> pd.DataFrame:
    """Fetch monthly ECB/ESCB 10Y sovereign yields for five euro-area countries.

    Returns observations and download errors.
    Monthly and daily data are kept separate.
    """
    data = pd.read_csv(upload)
    out = validate_observations(data)
    out['source'] = 'USER-IMPORTED (unverified): '+out['source']
    return out


def aligned_spread(df:pd.DataFrame, country:str, benchmark:str, tenor:float) -> pd.DataFrame:
    a=df[(df.country==country)&(df.tenor==tenor)][['date','yield_pct']].rename(columns={'yield_pct':'country_pct'})
    b=df[(df.country==benchmark)&(df.tenor==tenor)][['date','yield_pct']].rename(columns={'yield_pct':'benchmark_pct'})
    out=a.merge(b,on='date',how='inner',validate='one_to_one').sort_values('date')
    out['spread_bp']=(out.country_pct-out.benchmark_pct)*100
    return out


def curve_snapshot(df, day):
    data=df[df.date.eq(pd.Timestamp(day))]
    return data.pivot_table(index='tenor',columns='country',values='yield_pct',aggfunc='first')


# Official country-level multi-maturity sources.
SPAIN_DAILY_CSV = 'https://www.bde.es/webbe/es/estadisticas/compartido/datos/csv/ti_1_3.csv'
BPSTAT_API = 'https://bpstat.bportugal.pt/data/v1'
PORTUGAL_SERIES = {3: 12099455, 5: 12099462, 10: 12099464}


def _bde_date(raw: str):
    """Parse official BDE daily dates"""
    import re
    m = re.fullmatch(r'\s*(\d{1,2})\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,9})\s*(\d{4})\s*', str(raw))
    if not m:
        return pd.NaT
    months = {'ENE':1,'JAN':1,'FEB':2,'MAR':3,'ABR':4,'APR':4,'MAY':5,
              'JUN':6,'JUL':7,'AGO':8,'AUG':8,'SEP':9,'SET':9,'OCT':10,
              'NOV':11,'DIC':12,'DEC':12}
    mon = months.get(m.group(2)[:3].upper())
    try:
        return pd.Timestamp(year=int(m.group(3)), month=mon, day=int(m.group(1))) if mon else pd.NaT
    except ValueError:
        return pd.NaT


def parse_spain_ti_1_3(raw: str, start='2025-01-01') -> pd.DataFrame:
    """Parse daily Spanish government bond yields from BdE TI-1-3.

    Uses official series aliases: 3Y=8, 5Y=10, 10Y=11, 15Y=12.
    """
    import csv
    from io import StringIO
    import re
    rows=list(csv.reader(StringIO(raw.lstrip('\ufeff'))))
    if len(rows)<8:
        raise ValueError('Banco de España TI-1-3: incomplete CSV')
    metadata=rows[:6]
    alias_line=next((r for r in metadata if r and 'ALIAS' in r[0].upper()), None)
    if alias_line is None:
        raise ValueError('Banco de España TI-1-3: cannot locate series aliases')
    freq_line=next((r for r in metadata if r and ('FRECUENCIA' in r[0].upper() or 'FREQUENCY' in r[0].upper())), None)

    target={8:3,10:5,11:10,12:15}
    columns={}
    for index,label in enumerate(alias_line[1:],1):
        match=re.search(r'(?i)TI[_\s.-]*1[_\s.-]*3[.]?(\d+)(?:$|\D)',label.strip())
        if not match: continue
        slot=int(match.group(1))
        if slot not in target: continue
        if freq_line and index<len(freq_line) and freq_line[index].strip().upper() not in {'DIARIA','LABORABLE','DAILY','BUSINESS'}:
            continue
        columns[index]=target[slot]
    if not columns:
        raise ValueError('BdE TI-1-3: expected government bond columns 8/10/11/12 not identified in source aliases; CSV has not been used')
    first=pd.Timestamp(start)
    values=[]
    for row in rows[6:]:
        if not row:continue
        day=_bde_date(row[0])
        if pd.isna(day) or day<first:continue
        for idx,tenor in columns.items():
            if idx>=len(row):continue
            entry=row[idx].strip().replace(' ', '')
            if entry in ('','...','_','-'):continue
            try: val=float(entry)
            except ValueError: continue
            if not -10<val<30:
                raise ValueError(f'BdE {tenor}Y suspicious yield {val}; data not used')
            values.append((day,'Spain',tenor,val,'Banco de España, TI 1.3 daily government bond yield',
                           'Spanish secondary-market government bonds; reference maturity',SPAIN_DAILY_CSV))
    if not values:
        raise ValueError('BdE TI-1-3 returned no valid daily government yields for requested period')
    return validate_observations(pd.DataFrame(values,columns=OBS_COLS))


def spain_bde_history(start='2025-01-01') -> pd.DataFrame:
    response=get(SPAIN_DAILY_CSV,{})
   
    data=response.content
    try: raw=data.decode('utf-8-sig')
    except UnicodeDecodeError: raw=data.decode('cp1252')
    return parse_spain_ti_1_3(raw,start)


def parse_bpstat_jsonstat(payload: dict, tenor: int, series_id: int, start='2025-01-01',
                          source_url: str='') -> pd.DataFrame:
    
    import math
    ids=payload.get('id',[])
    sizes=payload.get('size',[])
    dims=payload.get('dimension',{})
    if not ids or len(ids)!=len(sizes):
        raise ValueError('BPstat: invalid JSON-stat dimensions')
    timedim=next((i for i,x in enumerate(ids) if x.lower() in {'reference_date','time','date','time_period'}),None)
    if timedim is None:
        role=payload.get('role',{}).get('time',[])
        timedim=ids.index(role[0]) if role and role[0] in ids else None
    if timedim is None:
        raise ValueError('BPstat: could not identify date dimension')
    if any(n!=1 for i,n in enumerate(sizes) if i!=timedim):
        raise ValueError('BPstat: expected a single series, got multiple categories')
    categories=dims[ids[timedim]]['category']['index']
    if isinstance(categories,list): categories={v:i for i,v in enumerate(categories)}
    vals=payload.get('value',{})
    frame=[]
    for day,index in categories.items():
        dt=pd.to_datetime(day,errors='coerce')
        if pd.isna(dt) or dt<pd.Timestamp(start): continue
        val=vals[index] if isinstance(vals,list) and index<len(vals) else vals.get(str(index)) if isinstance(vals,dict) else None
        if val is None:continue
        val=float(val)
        if not math.isfinite(val) or not -10<val<30:
            raise ValueError(f'BPstat {series_id}: invalid bond yield value {val}')
        frame.append((dt,'Portugal',tenor,val,'Banco de Portugal, BPstat series '+str(series_id),
                      'Government bond yield; official BPstat frequency and tenor',source_url))
    if not frame:
        raise ValueError(f'BPstat series {series_id}: no valid observations')
    return validate_observations(pd.DataFrame(frame,columns=OBS_COLS))


def portugal_bpstat_history(start='2025-01-01',tenors=(3,5,10)):
    """Retrieve BPstat data using dynamically resolved domain and dataset IDs.

    Validates series metadata and preserves the original observation frequency.
    """
    frames,issues=[],[]
    for tenor in tenors:
        sid=PORTUGAL_SERIES.get(tenor)
        if sid is None:
            issues.append(f'Portugal {tenor}Y: no verified series id');continue
        try:
            meta_url=f'{BPSTAT_API}/series/'
            r=get(meta_url,{'lang':'EN','series_ids':str(sid)})
            meta=r.json()
            if not isinstance(meta,list) or len(meta)!=1:
                raise ValueError('series metadata absent/ambiguous')
            info=meta[0]
            label=' '.join(str(info.get(k,'')) for k in ('label','short_label','description'))
            
            import re
            if not re.search(rf'(?<!\d){tenor}(?!\d)',label):
                raise ValueError(f'series {sid} metadata does not confirm {tenor}Y: {label[:100]}')
            domain=info['domain_ids'][0]
            dataset=info['dataset_id']
            url=f'{BPSTAT_API}/domains/{domain}/datasets/{dataset}/'
            result=get(url,{'lang':'EN','series_ids':str(sid)})
            df=parse_bpstat_jsonstat(result.json(),tenor,sid,start,url)
            # distinct curve_type for any statistical comparisons and risk P&L.
            frequency='Daily' if (df.date.diff().dropna().dt.days.le(7).mean()>.7) else 'Monthly/other'
            df['curve_type']=f'BPstat {frequency} government yield; maturity {tenor}Y'
            frames.append(df)
        except (ValueError, KeyError, IndexError, TypeError, requests.RequestException) as exc:
            issues.append(f'Portugal {tenor}Y: {exc}')
    if not frames:
        raise ValueError('No BPstat series loaded. ' + ' | '.join(issues))
    return validate_observations(pd.concat(frames,ignore_index=True)),issues



#TEC1, TEC10, TEC15
FRANCE_MULTI_URL = 'https://webstat.banque-france.fr/export/csv-columns/fr/selection/5385693'

def _date_cell(value):
    raw = str(value or '').strip().strip('"').replace('T00:00:00','').replace(' 00:00:00','')
    if not re.fullmatch(r'(?:\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[/-]\d{2}[/-]\d{4})', raw):
        return pd.NaT
    return pd.to_datetime(raw, dayfirst=bool(re.match(r'^\d{2}[/-]',raw)), errors='coerce')

def _french_number(value):
    val=str(value or '').strip().replace('\xa0','').replace(' ','').replace('%','')
    if val.lower() in {'','-','...','.','na','n/a','null'}: return None

    if ',' in val and '.' in val: return None
    try: value=float(val.replace(',','.'))
    except (ValueError,TypeError): return None
    if not (-10 < value < 30): raise ValueError('Unreasonable French TEC yield; check units')
    return value

def parse_webstat_multitenor(content: bytes | str, start='2025-01-01') -> pd.DataFrame:
    """Parse multi-column Banque de France Webstat exports.

Identifies TEC maturities using series codes or column labels,
independently of column order.
"""
    if isinstance(content,bytes):
        try: raw=content.decode('utf-8-sig')
        except UnicodeDecodeError:raw=content.decode('cp1252')
    else:raw=str(content).lstrip('\ufeff')
    if not raw.strip() or raw.lstrip().lower().startswith(('<html','<!doctype html','{')):
        raise ValueError('Webstat export is not a valid CSV')
    head=next((r for r in raw.splitlines() if r.strip()),'')
    sep=';' if head.count(';')>=head.count(',') else ','
    rows=list(csv.reader(StringIO(raw),delimiter=sep))
    if len(rows)<2:raise ValueError('Webstat export contains too few rows')

    cols={}
    for i,row in enumerate(rows[:16]):
        for j,cell in enumerate(row):
            m=re.search(r'FRMOYTEC(\d{1,2})(?:\D|$)',str(cell),re.I)
            if m:cols[j]=int(m.group(1))
    if not cols:
        for row in rows[:16]:
            for j,cell in enumerate(row):
                m=re.search(r'(?:TEC\s*|[ÉE]ch[ée]ance\s+Constante\s*[-:]?\s*)(\d{1,2})\s*(?:ans?|Y)?',str(cell),re.I)
                if m:cols[j]=int(m.group(1))
    cols={j:t for j,t in cols.items() if t in {1,2,3,5,7,10,15,20,25,30}}
    if not cols:raise ValueError('No identifiable FRMOYTEC maturities found in export metadata')
    
    date_counts={}
    for row in rows:
        for j,cell in enumerate(row):
            if not pd.isna(_date_cell(cell)):date_counts[j]=date_counts.get(j,0)+1
    if not date_counts:raise ValueError('Webstat CSV contains no recognized daily dates')
    date_col=max(date_counts,key=date_counts.get)
    if date_col in cols:raise ValueError('Webstat date column overlaps an identified TEC series')
    values=[]
    for row in rows:
        if date_col>=len(row):continue
        day=_date_cell(row[date_col])
        if pd.isna(day) or day<pd.Timestamp(start):continue
        for j,tenor in cols.items():
            if j>=len(row):continue
            y=_french_number(row[j])
            if y is None:continue
            values.append((day,'France',tenor,y,'Banque de France / Webstat selection 5385693',
                           'Daily CNO-TEC constant-maturity actuarial yield',FRANCE_MULTI_URL))
    if not values:raise ValueError('Webstat CSV had TEC headings but no valid dated rates')
    return validate_observations(pd.DataFrame(values,columns=OBS_COLS))

def france_tec_history(start='2025-01-01') -> pd.DataFrame:
    resp=get(FRANCE_MULTI_URL,{})
    return parse_webstat_multitenor(resp.content,start)

def read_official_csv(upload,country,source_name='Official source, user-provided'):
    """Fallback for provider CSVs explicitly normalized as date,tenor,yield_pct or TEC columns."""
    raw=upload.read() if hasattr(upload,'read') else bytes(upload)
    try: s=raw.decode('utf-8-sig')
    except UnicodeDecodeError:s=raw.decode('cp1252')
    first=next((x for x in s.splitlines() if x.strip()),'')
    delimiter=';' if first.count(';')>first.count(',') else ','
    df=pd.read_csv(StringIO(s),sep=delimiter,dtype=str)
    df.columns=[str(x).strip() for x in df.columns]
    d=next((x for x in df if x.lower() in {'date','fecha','datum','jour','data'}),None)
    if d is None:raise ValueError('CSV must have a date column')
    if {'tenor','yield_pct'}.issubset(df):
        out=df[[d,'tenor','yield_pct']].rename(columns={d:'date'})
    else:
        tenors={}
        for col in df.columns:
            m=re.fullmatch(r'(?:TEC\s*)?(\d{1,2})\s*(?:Y|YR|ANS?)?',col,re.I)
            if m:tenors[col]=int(m.group(1))
        if not tenors:raise ValueError('CSV needs tenor/yield_pct or TEC2,TEC5,TEC10 columns')
        out=df.melt(id_vars=d,value_vars=list(tenors),var_name='tenor',value_name='yield_pct').rename(columns={d:'date'})
        out['tenor']=out.tenor.map(tenors)
    out['yield_pct']=pd.to_numeric(out.yield_pct.astype(str).str.replace(',','.',regex=False),errors='coerce')
    out=out.dropna(subset=['yield_pct']).copy()
    out['country']=country;out['source']='User-imported '+source_name
    out['curve_type']='User-supplied government yield reference (unverified)'
    out['source_url']='User CSV import'
    return validate_observations(out[OBS_COLS])

ECB_EXTENDED = {'BE':'Belgium','SI':'Slovenia','SK':'Slovakia','NL':'Netherlands',
                'FI':'Finland','AT':'Austria','IE':'Ireland','DK':'Denmark'}

def ecb_more_countries_monthly(start='2024-01',codes=('BE','SI','SK','NL','FI','AT','IE','DK')):
    from concurrent.futures import ThreadPoolExecutor
    def load(code):
        if code not in ECB_EXTENDED:return None,f'Unknown country {code}'
        url=ECB_IRS+f'M.{code}.L.L40.CI.0000.EUR.N.Z'
        try:
            frame=parse_sdmx_csv(get(url,{'startPeriod':start,'format':'csvdata'}).text)
            frame['country']=ECB_EXTENDED[code];frame['tenor']=10
            frame['source']='ECB convergence sovereign yields'
            frame['curve_type']='MONTHLY average sovereign 10Y (convergence), not daily'
            frame['source_url']=url
            return frame,None
        except Exception as e:return None,f'{code}: {e}'
    with ThreadPoolExecutor(max_workers=6) as pool: results=list(pool.map(load,codes))
    frames=[f for f,e in results if f is not None]
    errors=[e for f,e in results if e]
    if not frames:raise ValueError('No extra country series downloaded. '+' | '.join(errors))
    return validate_observations(pd.concat(frames,ignore_index=True)),errors
