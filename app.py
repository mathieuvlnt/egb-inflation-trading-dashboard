"""EGB & Inflation Trading Dashboard.

Streamlit application for sovereign yield analysis, relative value,
inflation products and portfolio risk.

Market data is sourced from public providers.
Pricing and risk scenarios use user-defined inputs.
"""
from datetime import date
from io import StringIO
from dateutil.relativedelta import relativedelta
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from market_data import (bund_history, france_tec_history, spain_bde_history,
    portugal_bpstat_history, ecb_country_10y_monthly, ecb_more_countries_monthly,
    eurostat_hicp, ecb_aaa_spot, read_official_csv, parse_webstat_multitenor, FRANCE_MULTI_URL,
    OBS_COLS, validate_observations)
from analytics import (latest_curve, slope_series, relative_spread,
    rolling_summary, model_reference_pv01, previous_move, position_pnl)
from fixed_income import (Curve, bootstrap_annual_par, bond_cashflows,
    yield_price, solve_z_spread, irs_annual, ois_annual,
    par_asset_swap_annual, zcis_settlement, zcis_mark_to_market)

st.set_page_config(page_title='EGB & Inflation Trading | HSBC Desk Case Study', page_icon='◈', layout='wide', initial_sidebar_state='expanded')
px.defaults.color_discrete_sequence=['#DB0011','#343B48','#A4555A','#69788B','#C98A43','#628577']
st.markdown('''<style>
:root {--desk-red:#DB0011;--desk-ink:#202124;--desk-gray:#F5F6F8;--desk-border:#E2E4E8}
.block-container{max-width:1220px;padding-top:1.6rem;padding-bottom:2.2rem}

[data-testid="stAppViewContainer"]{background:#FAFAF8;color:var(--desk-ink)}
[data-testid="stSidebar"]{background:#FFF1F2;border-right:1px solid var(--desk-border)}
[data-testid="stSidebar"] *{color:#262B32}
[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) *{color:var(--desk-red)!important;font-weight:600}
h1{font-size:2.05rem!important;letter-spacing:-.025em;color:#202124!important}
h2{font-size:1.4rem!important;color:#202124!important}
h3{font-size:1.08rem!important;color:#202124!important}
a{color:#BC0010}
[data-testid="stMetric"]{background:#FFFFFF;padding:.85rem 1rem;border-radius:9px;border:1px solid var(--desk-border);border-top:3px solid var(--desk-red);box-shadow:0 2px 10px rgba(0,0,0,.035)}
[data-testid="stMetricValue"]{font-size:1.45rem!important;color:#202124}
[data-testid="stCaptionContainer"]{line-height:1.4;color:#626873}
[data-testid="stButton"] button[kind="primary"]{background-color:var(--desk-red)!important;border-color:var(--desk-red)!important;color:white!important}
[data-testid="stButton"] button[kind="primary"]:hover{background-color:#B6000E!important}
[data-testid="stHorizontalBlock"]{gap:1rem}
[data-testid="stAppViewContainer"]::before {content: "";position: fixed;top: 0;left: 0;width: 100%;height: 10px;background-color: #DB0011;z-index: 999999;pointer-events: none;}            
.sidebar-signature {position: fixed;bottom: 28px;left: 18px;font-size: 1.45rem;font-weight: 700;color: #6F6F6F;letter-spacing: 0.2px;z-index: 999;}
</style>''',unsafe_allow_html=True)

@st.cache_data(ttl=7200,show_spinner=False)
def fetch_bund():return bund_history(start='2025-01-01',tenors=(2,5,7,10,15,20,30))
@st.cache_data(ttl=7200,show_spinner=False)
def fetch_france():return france_tec_history(start='2025-01-01')
@st.cache_data(ttl=7200,show_spinner=False)
def fetch_spain():return spain_bde_history(start='2025-01-01')
@st.cache_data(ttl=7200,show_spinner=False)
def fetch_portugal():return portugal_bpstat_history(start='2025-01-01')
@st.cache_data(ttl=86400,show_spinner=False)
def fetch_monthly():return ecb_country_10y_monthly(start='2024-01')
@st.cache_data(ttl=86400,show_spinner=False)
def fetch_other_monthly():return ecb_more_countries_monthly(start='2024-01')
@st.cache_data(ttl=86400,show_spinner=False)
def fetch_hicp(geo):return eurostat_hicp(geo=geo,category='TOTAL',measure='RCH_A',start='2024-01')
@st.cache_data(ttl=7200,show_spinner=False)
def fetch_zero():return ecb_aaa_spot(start='2026-09-01',tenors=(1,2,3,5,7,10))

SOURCES={
    'German Bunds':fetch_bund,'French TEC':fetch_france,
    'Spain sovereigns':fetch_spain,'Portugal sovereigns':fetch_portugal,
    'European 10Y monthly':fetch_monthly,'Semi-core 10Y monthly':fetch_other_monthly,
}

def empty():return pd.DataFrame(columns=OBS_COLS)

def attempt(source,refresh=False):
    """Lazy, once per session, with visible errors and no fake fallback."""
    k='attempt:'+source
    if k in st.session_state and not refresh:return
    st.session_state[k]=True
    try:
        with st.spinner(f'Loading official data: {source}…'):
            result=SOURCES[source]()
            if isinstance(result,tuple):frame,issues=result
            else:frame,issues=result,[]
            st.session_state['data:'+source]=frame
            st.session_state['issues:'+source]=issues
            st.session_state.pop('error:'+source,None)
    except Exception as exc:
        st.session_state['error:'+source]=str(exc)
        st.session_state['data:'+source]=empty()

def data(source):return st.session_state.get('data:'+source,empty())

def observations(*names):
    frames=[data(x) for x in names if not data(x).empty]
    return pd.concat(frames,ignore_index=True) if frames else empty()

def issue_note(sources):
    with st.expander('Data sources & download status',expanded=False):
        for name in sources:
            df=data(name)
            msg=st.session_state.get('error:'+name)
            if msg:st.warning(f'{name}: {msg[:350]}')
            elif not df.empty:st.write(f'**{name}:** {len(df):,} observations; last date {df.date.max():%Y-%m-%d}')
            else:st.caption(f'{name}: not loaded')
            for e in st.session_state.get('issues:'+name,[])[:4]:st.caption(str(e)[:250])
        st.caption('No missing observation is filled or simulated. Source and observation dates are preserved.')

def graph(fig,h=310):
    fig.update_layout(template='plotly_white',height=h,margin=dict(l=12,r=12,t=10,b=15),
      font=dict(size=12,color='#2B3038'),paper_bgcolor='rgba(0,0,0,0)',plot_bgcolor='rgba(0,0,0,0)',
      colorway=['#DB0011','#343B48','#A4555A','#69788B','#C98A43','#628577'],
      xaxis=dict(gridcolor='#ECEEF1',linecolor='#D6D9DF'),
      yaxis=dict(gridcolor='#ECEEF1',linecolor='#D6D9DF'),
      legend=dict(orientation='h',y=-0.28))
    st.plotly_chart(fig,use_container_width=True,config={'displaylogo':False})

def heading(name, strapline=""):
    st.title(name)
    if strapline:
        st.caption(strapline)

def reference_block(df,countries):
    rows=[]
    for country in countries:
        for _,r in latest_curve(df,country).iterrows():
            rows.append({'Country':country,'Y':int(r.tenor),'Yield (%)':float(r.yield_pct),
                         'Date':r.date.strftime('%Y-%m-%d'),'Source':r.source})
    if rows:
        x=pd.DataFrame(rows)
        st.dataframe(x,hide_index=True,use_container_width=True,
                     column_config={'Yield (%)':st.column_config.NumberColumn(format='%.3f')})
    else:st.info('No official observations loaded for this selection.')

def curve_panel(df,countries,title='Published yield curves'):
    st.subheader(title)
    if df.empty:st.info('No market observations loaded. Inspect Data sources & download status.');return
    curves=[]
    for country in countries:
        x=latest_curve(df,country)
        
        for definition,g in x.groupby('curve_type'):
            if len(g)>=2:
                g=g.copy();g['Series']=country+(' · TEC' if 'TEC' in definition else ' · benchmark/reference')
                curves.append(g)
    if not curves:
        st.info('Not enough maturities from one consistent yield definition. No artificial curve is created.');return
    frame=pd.concat(curves)
    graph(px.line(frame,x='tenor',y='yield_pct',color='Series',markers=True,
        labels={'tenor':'Reference maturity (years)','yield_pct':'Yield (%)'}))

def slopes_view(df,country,options):
    st.subheader(f'{country}: steepening / flattening')
    x=df[df.country==country]
    if x.empty:st.info('No official curve available.');return
    definitions=x.curve_type.unique().tolist()
    definition=st.selectbox('Yield definition',definitions,key=f'def:{country}')
    points=x[x.curve_type==definition]
    pairs=[]
    for a,b in options:
        h=slope_series(points,country,a,b,definition)
        if len(h)>=2:
            last=h.iloc[-1]
            change=last.slope_bp-h.iloc[-2].slope_bp
            pairs.append({'Slope':f'{a}s{b}s','Level (bp)':round(last.slope_bp,1),
                'Last change (bp)':round(change,1),
                'Move':'Steepening' if change>0.01 else 'Flattening' if change<-.01 else 'Unchanged',
                'Date':last.date.strftime('%Y-%m-%d')})
    if not pairs:st.info('Two matching reference maturities and at least two common publication dates are needed.');return
    st.dataframe(pd.DataFrame(pairs),hide_index=True,use_container_width=True)
    labels=[(int(i.split('s')[0]),int(i.split('s')[1])) for i in [r['Slope'] for r in pairs]]
    labels={f'{a}s{b}s':(a,b) for a,b in labels}
    choice=st.selectbox('Historical slope',list(labels),key=f'slope:{country}')
    a,b=labels[choice]
    history=slope_series(points,country,a,b,definition)
    graph(px.line(history.tail(180),x='date',y='slope_bp',labels={'date':'Date','slope_bp':'Slope (bp)'}),h=275)


def spread_module(df,comparisons,benchmark='Germany',tenor=10,definition='MONTHLY average sovereign 10Y (convergence), not daily'):
    if df.empty:st.info('No compatible observations.');return
    parts=[]
    for country in comparisons:
        try:
            x=relative_spread(df,country,benchmark,tenor,definition,definition)
            if not x.empty:
                x['Market']=f'{country} – {benchmark}';parts.append(x)
        except ValueError:pass
    if not parts:st.info('No common observation dates for the selected definition.');return
    all_spreads=pd.concat(parts)
    graph(px.line(all_spreads,x='date',y='spread_bp',color='Market',labels={'date':'Observation month','spread_bp':'Spread (bp)'}))
    last=all_spreads.sort_values('date').groupby('Market').tail(1)
    st.dataframe(last[['Market','date','spread_bp']].rename(columns={'date':'Month','spread_bp':'Spread (bp)'}),
                 hide_index=True,use_container_width=True)

def home():
    heading('EGB & Inflation Trading | HSBC Desk Case Study')
    st.markdown('**Five-minute desk briefing:** curves, sovereign relative value, inflation and basic position risk — without proprietary data.')
    for src in ['German Bunds','French TEC','European 10Y monthly']:attempt(src)
    german=data('German Bunds');french=data('French TEC');monthly=data('European 10Y monthly')
    cols=st.columns(4)
    for col,country,tenor,frame in [(cols[0],'Germany',10,german),(cols[1],'France',10,french),
                                    (cols[2],'Italy',10,monthly),(cols[3],'Spain',10,monthly)]:
        x=latest_curve(frame,country)
        x=x[x.tenor==tenor]
        with col:
            if x.empty:st.metric(f'{country} 10Y','N/A')
            else:
                y=x.iloc[-1]
                st.metric(f'{country} 10Y',f'{y.yield_pct:.3f}%')
                st.caption(f'{y.date:%d %b %Y} · {"monthly average" if country in ["Italy","Spain"] else "daily"}')
    st.divider()
    a,b=st.columns(2)
    with a:
        st.subheader('Desk coverage')
        st.markdown('**01 · Core EGB 2–10Y** — Bund/OAT curves and 2s10s (Hugo Fuentes)')
        st.markdown('**02 · Southern EGB 3–15Y** — BTP/Bonos/Portugal, cash and ASW (Vincenzo Asaro)')
        st.markdown('**03 · Inflation** — HICP, breakevens, ZCIS (Hugo Guinamant & William Fortier)')
        st.markdown('**04 · Semi-core & Illiquid 2–10Y** — RV and inventory risk (Ouassim Loussaief)')
    with b:
        st.subheader('Market brief')
        st.markdown('**Monetary policy** · [ECB meeting calendar](https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html)')
        if not german.empty:
            sl=slope_series(german,'Germany',2,10)
            if not sl.empty:st.metric('German 2s10s',f'{sl.slope_bp.iloc[-1]:+.1f} bp')
        if not french.empty and not german.empty:
            st.caption('OAT–Bund analysis is in Core EGB, with the benchmark/TEC methodology difference documented.')
    issue_note(['German Bunds','French TEC','European 10Y monthly'])
    st.divider()
    risk_widget(observations('German Bunds','French TEC','Spain sovereigns','Portugal sovereigns','European 10Y monthly'))


def fallback_french_upload():
    """Offline fallback, including the user's genuine multi-column Webstat export."""
    with st.expander('France: import downloaded Webstat CSV if the direct link is blocked'):
        st.caption('Accepts the original Webstat selection CSV with many FRMOYTEC columns. No manual cleaning required for the identified export layout.')
        upload=st.file_uploader('Webstat TEC export (CSV)',type=['csv'],key='france_webstat_fallback')
        if upload:
            try:
                try: frame=parse_webstat_multitenor(upload.getvalue())
                except ValueError:frame=read_official_csv(upload,'France','authorized official download')
                st.session_state['data:French TEC']=frame
                st.session_state.pop('error:French TEC',None)
                st.success(f'{len(frame):,} dated French observations; maturities {sorted(frame.tenor.unique().tolist())}.')
            except Exception as exc:st.error(f'Unable to parse this CSV: {exc}')


def core():
    heading('01 · Core EGB Trading | 2–10Y - Hugo Fuentes','German and French government bonds; curve positioning and relative value')
    for src in ('German Bunds','French TEC'):attempt(src)
    fallback_french_upload()
    core_df=observations('German Bunds','French TEC')
    curve_panel(core_df,['Germany','France'],'Bund and OAT reference yield curves')
    countries=[x for x in ['Germany','France'] if x in set(core_df.country)]
    if countries:
        sel=st.selectbox('Curve-trading market',countries)
        slopes_view(core_df,sel,[(2,10),(5,10),(2,5)])
    if not data('German Bunds').empty and not data('French TEC').empty:
        st.subheader('OAT–Bund reference spread')
        st.caption('France = interpolated CNO-TEC; Germany = current Federal benchmark.')
        t=st.selectbox('Tenor', [2,5,7,10],index=3)
        try:
            x=relative_spread(core_df,'France','Germany',t)
            if not x.empty:
                graph(px.line(x.tail(200),x='date',y='spread_bp',labels={'date':'Date','spread_bp':'OAT–Bund spread (bp)'}))
                st.metric('Latest aligned observation',f'{x.spread_bp.iloc[-1]:.1f} bp')
            else:st.info('No aligned official dates for this tenor.')
        except ValueError as exc:st.warning(str(exc))
    else:st.info('OAT–Bund needs official French TEC data. The Webstat CSV source is attempted automatically; its error is shown below.')
    with st.expander('Pricing toolkit · bootstrap, IRS / OIS and Z-spread',expanded=False):
        core_pricing_tools()
    issue_note(['German Bunds','French TEC'])


def core_pricing_tools():
    t1,t2,t3=st.tabs(['Bootstrap','IRS / OIS','Z-spread (ECB AAA)'])
    with t1:
        st.caption('Annual par coupon instruments at maturities 1–5Y; single-curve educational bootstrap.')
        rates=[]; cols=st.columns(5)
        for i,c in enumerate(cols,1):rates.append(c.number_input(f'{i}Y (%)',value=2.0+0.1*i,step=.1,key=f'par{i}'))
        try:
            curve=bootstrap_annual_par([v/100 for v in rates])
            st.dataframe(pd.DataFrame({'Year':range(1,6),'Discount factor':curve.df_values}),hide_index=True,use_container_width=True)
        except Exception as e:st.warning(str(e))
    with t2:
        maturity=st.selectbox('Maturity',list(range(1,11)),index=4,key='swap_maturity')
        disc=st.number_input('Assumed annual discount zero rate (%)',value=2.5,step=.1,key='oisrate')/100
        proj=st.number_input('Assumed Euribor projection zero rate (%)',value=2.8,step=.1,key='euriborate')/100
        fixed=st.number_input('Fixed coupon (%)',value=2.7,step=.1,key='irsfixed')/100
        n=st.number_input('Notional (€)',value=1_000_000,step=100_000,key='irsnotional')
        try:
            tenors=tuple(range(1,maturity+1))
            d=Curve(tenors,tuple((1+disc)**(-i) for i in tenors))
            f=Curve(tenors,tuple((1+proj)**(-i) for i in tenors))
            irs=irs_annual(d,f,maturity,fixed,n)
            ois=ois_annual(d,maturity,fixed,n)
            a,b=st.columns(2)
            a.metric('IRS par fixed rate',f'{irs["par_rate"]*100:.3f}%');b.metric('Payer IRS model PV',f'€{irs["mtm"]:,.0f}')
            st.caption(f'Simplified OIS par: {ois["par_rate"]*100:.3f}%. Annual payments')
        except Exception as exc:st.warning(str(exc))
    with t3:
        st.caption('ECB euro-area AAA fitted zero curve is a sovereign reference, NOT €STR OIS discounting.')
        if st.button('Load ECB AAA daily zero curve',key='zeros'):
            try:st.session_state['ecb_zero'],st.session_state['zero_err']=fetch_zero()
            except Exception as e:st.error(str(e))
        z=st.session_state.get('ecb_zero',empty())
        if z.empty:st.info('Load ECB daily AAA curve to calculate Z-spread against an observed benchmark.')
        else:
            pv=st.number_input('User-supplied dirty price (per 100)',value=98.0,min_value=.01,key='dirty')
            coupon=st.number_input('Coupon (%)',value=3.0,min_value=0.0,key='zcoupon')
            maturity=st.selectbox('Remaining maturity (years)',[1,2,3,5],key='zmaturity')
            latest=z.groupby('date').filter(lambda f:len(f.tenor.unique())>=4)
            if latest.empty:st.info('Not enough complete points on one ECB curve date.')
            else:
                day=latest.date.max();sub=z[z.date==day].sort_values('tenor')
                curve=Curve.from_ecb_spot(tuple(sub.tenor),tuple(sub.yield_pct))
                settle=date.today();end=settle+relativedelta(years=maturity)
                try:
                    flows,_=bond_cashflows(settle,end,coupon/100,1)
                    zsp,resid=solve_z_spread(flows,curve,pv)
                    st.metric('Continuous Z-spread',f'{zsp*10000:.1f} bp')
                    st.caption(f'ECB AAA curve date {day:%Y-%m-%d}; pricing residual {resid:.2g}. Bond price is USER-ENTERED.')
                except Exception as e:st.warning(str(e))


def southern_snapshot(df, monthly):
    """Available public 3–15Y reference points, with date/frequency shown for every tenor.

    Does not interpolate or construct fictitious quotes. An ECB monthly Italy 10Y
    appears explicitly as such when a multi-tenor Italian source is unavailable.
    """
    st.subheader('3–15Y desk watchlist · latest published references')
    rows=[]
    for country in ('Italy', 'Spain', 'Portugal'):
        current=latest_curve(df,country)
        row={'Country':country}
        tags=[]
        for tenor in (3,5,10,15):
            found=current[current.tenor.eq(tenor)]
            source=''
            if found.empty and tenor==10:
                found=latest_curve(monthly,country)
                found=found[found.tenor.eq(10)]
                source='ECB monthly'
            if found.empty:
                row[f'{tenor}Y (%)']='—'
                continue
            quote=found.sort_values('date').iloc[-1]
            tag=source or ('BPstat monthly' if 'BPstat Monthly' in str(quote.curve_type)
                           else 'BPstat daily' if 'BPstat Daily' in str(quote.curve_type)
                           else 'Official reference')
            row[f'{tenor}Y (%)']=f'{quote.yield_pct:.3f} · {quote.date:%Y-%m-%d}'
            if tag not in tags:tags.append(tag)
        row['3s15s (bp)']='—'
        for definition in current.curve_type.unique():
            hist=slope_series(df,country,3,15,definition)
            if not hist.empty:
                row['3s15s (bp)']=f'{hist.slope_bp.iloc[-1]:+.1f}'
                break
        row['Available source types']=', '.join(tags) if tags else 'No observations'
        rows.append(row)
    st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
    st.caption('Each cell shows yield (%) and its observation date. A dash means unavailable, not zero.')
    if latest_curve(df,'Italy').empty:
        st.info('Italy: only the ECB 10Y monthly reference is available here. A BTP 3–15Y curve requires multi-tenor observations from Banca d’Italia (CSV above); missing yields are not invented.')

def south():
    heading('02 · Southern EGB | 3–15Y - Vincenzo Asaro','Italy, Spain, Portugal | sovereign relative value, cash bonds and asset swaps')
    for src in ('Spain sovereigns','Portugal sovereigns','European 10Y monthly'):attempt(src)
    df=observations('Spain sovereigns','Portugal sovereigns')
    st.caption('Daily Spanish references and BPstat Portugal (some series monthly). Italian official daily BMK0200 can be supplied through CSV if not publicly exportable.')
    with st.expander('Italy · import published Banca d’Italia BMK0200 data (optional)'):
        st.markdown('[Banca d’Italia government bond benchmark tables](https://statistiche.bancaditalia.it/)')
        f=st.file_uploader('Official Italy file (normalized date,3Y,5Y,10Y,15Y or date,tenor,yield_pct)',type=['csv'],key='italy_csv_upload')
        if f is not None:
            try:
                italian_data = read_official_csv(
                    f, 'Italy', 'Banca d’Italia BMK0200')
                st.session_state['italy_market_data'] = italian_data
                st.success('Italy observations loaded into this private session.')
            except Exception as e:
                st.session_state.pop('italy_market_data', None)
                st.warning(str(e))
    with st.expander('Spain / Portugal · alternative provider CSV if an API is unavailable'):
        cc=st.selectbox('Country', ['Spain','Portugal'],key='south_upload_country')
        f2=st.file_uploader('Official CSV normalized as date,3Y,5Y,10Y or date,tenor,yield_pct',type=['csv'],key='south_csv')
        if f2:
            try:
                frame=read_official_csv(f2,cc)
                st.session_state['south_custom:'+cc]=frame
                st.success(f'{len(frame)} user-imported observations for {cc}.')
            except Exception as e:st.warning(str(e))
    italy=st.session_state.get('italy_market_data')
    for cc in ['Spain','Portugal']:
        custom=st.session_state.get('south_custom:'+cc)
        if isinstance(custom, pd.DataFrame) and not custom.empty:
            df = df[df.country.ne(cc)]
            df = pd.concat([df, custom], ignore_index=True)
    if isinstance(italy, pd.DataFrame) and not italy.empty:
        df = pd.concat([df, italy], ignore_index=True)

    # like-frequency Portuguese government-yield series,
    if not df.empty:
        portugal=df.country.eq('Portugal') & df.curve_type.str.startswith('BPstat ',na=False)
        daily=portugal & df.curve_type.str.contains('BPstat Daily',regex=False,na=False)
        monthly_mask=portugal & df.curve_type.str.contains('BPstat Monthly/other',regex=False,na=False)
        df=df.copy()
        df.loc[daily,'curve_type']='BPstat Daily sovereign reference'
        df.loc[monthly_mask,'curve_type']='BPstat Monthly/other sovereign reference'

    monthly = data('European 10Y monthly')
    southern_snapshot(df, monthly)
    st.markdown("### Data Availability & Research Focus")
    st.markdown("""
    **Due to the limited availability of freely accessible, consistent
    multi-maturity sovereign bond data, this analysis focuses primarily
    on Spain and, where data are available, Portugal.**
    
    Italian government bonds remain covered through the 10Y relative
    value analysis below.
    """)
    
    st.markdown(
        "**Unfortunately, a personal Bloomberg Terminal is still "
        "slightly outside my student budget... for now!**")
    
    curve_panel(
        df,
        ['Italy', 'Spain', 'Portugal'],
        '3–15Y southern cash-reference curves · available maturities')
    if not df.empty:
        choice=st.selectbox('Country for slope monitoring',[c for c in ['Italy','Spain','Portugal'] if c in df.country.unique()])
        if choice=='Portugal':
            st.caption('Portugal: 3Y is published daily; available 5Y/10Y are monthly. Only slopes with matching observation dates AND frequency are calculated.')
        slopes_view(df,choice,[(3,10),(5,10),(5,15),(3,15)])
    st.subheader('Southern European 10Y relative value (ECB monthly)')
    st.caption('Monthly comparison uses like-for-like ECB/ESCB convergence series, not daily executable BTP/Bonos/Bund quotes.')
    monthly=data('European 10Y monthly')
    spread_module(monthly,['Italy','Spain','Portugal'])
    st.subheader('Monthly market note · export')
    note=['SOUTHERN EUROPEAN GOVERNMENT BONDS — SOURCE-BASED MONITOR',
          f'Prepared: {date.today():%Y-%m-%d}', 'References: ECB/ESCB monthly 10Y convergence yields (not daily cash quotes).','']
    for country in ('Italy','Spain','Portugal'):
        x=latest_curve(monthly,country)
        x=x[x.tenor==10]
        if x.empty:note.append(country+': data unavailable')
        else:
            r=x.iloc[-1]
            note.append(f'{country}: {r.yield_pct:.3f}% 10Y | observation month {r.date:%Y-%m}')
    note+=['','Market risks to monitor: issuance calendars, ECB policy, sovereign spreads, liquidity and repo.','No trade recommendation.']
    st.download_button('Download desk market note (TXT)','\n'.join(note),file_name='southern_egb_market_note.txt')
    with st.expander('Bond / asset swap pricing · simplified conventions',expanded=False):
        asw_tool()
    issue_note(['Spain sovereigns','Portugal sovereigns','European 10Y monthly'])


def asw_tool():
    st.caption('Par asset swap with annual cashflows at a coupon date.')
    a,b=st.columns(2)
    price=a.number_input('Bond dirty price per 100',value=98.0,min_value=.01,key='aswprice')
    coupon=b.number_input('Bond coupon (%)',value=3.0,key='aswcoupon')
    years=a.selectbox('Maturity (years)',[2,3,5,10],index=2,key='aswyears')
    ois=b.number_input('Assumed discount zero (%)',value=2.5,key='aswois')/100
    eur=a.number_input('Assumed forward zero (%)',value=2.8,key='asweur')/100
    curve_times=tuple(range(1,years+1))
    try:
        disc=Curve(curve_times,tuple((1+ois)**(-i) for i in curve_times))
        proj=Curve(curve_times,tuple((1+eur)**(-i) for i in curve_times))
        r=par_asset_swap_annual(price,coupon/100,disc,proj,years)
        st.metric('Par ASW spread (model)',f'{r["asw_spread"]*10000:.1f} bp')
        st.caption(f'Package repricing residual: {r["package_residual"]:.2g} per 100. Not equal to bond yield minus swap rate.')
    except Exception as e:st.warning(str(e))


def inflation():
    heading('03 · Inflation Trading - Hugo Guinamant & William Fortier','HICP, inflation-linked bonds, breakevens & ZC inflation swaps')
    geo_name={'EA':'Euro area','FR':'France','IT':'Italy','ES':'Spain','PT':'Portugal'}
    geo=st.selectbox('Eurostat HICP location',list(geo_name),format_func=lambda x:geo_name[x])
    key='hicp:'+geo
    if key not in st.session_state:
        try:
            with st.spinner('Loading Eurostat observed HICP…'):st.session_state[key]=fetch_hicp(geo)
        except Exception as e:st.session_state[key]=pd.DataFrame();st.session_state[key+':err']=str(e)
    hicp=st.session_state[key]
    if hicp.empty:
        st.warning(f'HICP could not be retrieved: {st.session_state.get(key+":err","No observations")[:250]}')
    else:
        last=hicp.iloc[-1]
        st.metric(f'{geo_name[geo]} · latest annual HICP',f'{last.inflation_pct:.2f}%',help=f'Observation month: {last.date:%Y-%m}')
        graph(px.line(hicp.tail(36),x='date',y='inflation_pct',labels={'date':'Observation month','inflation_pct':'HICP YoY (%)'}))
        st.caption('Observed inflation is monthly')
    left,right=st.columns(2)
    with left:
        st.subheader('Inflation linkers · OATi / OAT€i')
        st.caption('OATi: French CPI ex tobacco. OAT€i: Euro-area HICP ex tobacco. Daily index lag, interpolation and floors not modeled.')
        base=st.number_input('Contractual base index (input)',min_value=.001,value=100.0,key='indexbase')
        ref=st.number_input('Reference index (input)',min_value=.001,value=108.0,key='indexcurrent')
        st.metric('Simple indexation factor',f'{ref/base:.5f}')
        st.markdown('[AFT OAT€i official indexation information](https://www.aft.gouv.fr/fr/oateuroi-principaux-chiffres)')
    with right:
        st.subheader('Breakeven calculator')
        n=st.number_input('User-supplied nominal yield (%)',value=3.0,key='be_nom')
        r=st.number_input('User-supplied real yield (%)',value=1.0,key='be_real')
        st.metric('Approx. breakeven',f'{n-r:.2f}%')
        st.caption('Nominal minus real yield, not a pure inflation forecast.')
    with st.expander('Zero-coupon inflation swap · payoff & model PV'):
        nc=st.number_input('Notional (€)',min_value=1_000,value=1_000_000,key='zcisN')
        a,b=st.columns(2)
        first=a.number_input('Initial reference index',min_value=.01,value=100.0,key='zcisI0')
        final=b.number_input('Final observed index / payoff scenario',min_value=.01,value=115.0,key='zcisIT')
        strike=a.number_input('Contractual fixed inflation rate (%)',value=2.0,key='zcisK')/100
        maturity=b.selectbox('Contract years',[1,2,3,5,10],index=3,key='zcisT')
        receive=st.radio('Direction',['Receive inflation','Pay inflation'],horizontal=True)
        result=zcis_settlement(nc,first,final,strike,maturity,receive=='Receive inflation')
        st.metric('Illustrative settlement payoff',f'€{result:,.2f}')
        st.caption('This is a terminal payoff using the indices you entered.')
    st.caption('Source: [Eurostat HICP](https://ec.europa.eu/eurostat/web/hicp/information-data) · Values have monthly publication lag.')


def semi():
    heading('04 · Semi-Core & Less Liquid EGB | 2–10Y - Ouassim Loussaief',
        'France, Spain, Belgium, Portugal, Slovenia, Slovakia and backup core markets')
    for src in ('European 10Y monthly','Semi-core 10Y monthly'):attempt(src)
    st.subheader('Cross-country market relative value')
    st.caption('10Y ECB monthly harmonized reference series.')
    monthly=observations('European 10Y monthly','Semi-core 10Y monthly')
    chosen=st.multiselect('Semi-core / peripheral markets',
        [x for x in ['France','Belgium','Spain','Portugal','Slovenia','Slovakia'] if x in monthly.country.unique()],
        default=[x for x in ['France','Belgium','Spain','Portugal'] if x in monthly.country.unique()])
    spread_module(monthly,chosen)
    st.subheader('Market-making inventory scenario')
    opts=monthly[monthly.country.isin(chosen)].sort_values('date').groupby(['country','tenor']).tail(1)
    if opts.empty:st.info('Load official ECB monthly market references to run this risk scenario.')
    else:
        options={f'{r.country} {int(r.tenor)}Y · {r.date:%Y-%m}':r for _,r in opts.iterrows()}
        label=st.selectbox('Reference market',list(options));ref=options[label]
        col1,col2,col3=st.columns(3)
        side=col1.selectbox('Position',['Long','Short'])
        nominal=col2.number_input('Notional (€)',min_value=100_000,value=1_000_000,step=100_000)
        shock=col3.slider('Hypothetical yield shock (bp)',-50,50,10)
        pv01=model_reference_pv01(nominal,ref.yield_pct,int(ref.tenor),side)
        col1.metric('Model signed PV01',f'€{pv01:,.0f}/bp')
        col2.metric('First-order scenario P&L',f'€{position_pnl(pv01,shock):+,.0f}')
        st.caption(f'{ref.country}: observed {ref.yield_pct:.3f}% (monthly {ref.date:%Y-%m}). Model assumes a par annual-coupon bond.')
    issue_note(['European 10Y monthly','Semi-core 10Y monthly'])


def risk_widget(df):
    st.subheader('Portfolio Risk & P&L')
    st.caption('Select a market reference and add a position. Risk uses a simple bond model; P&L uses the last observed yield movement.')
    if 'book' not in st.session_state:st.session_state.book=[]
    if df.empty:st.info('No real rate observations loaded for position selection.');return
    x=df.sort_values('date').groupby(['country','tenor','curve_type']).tail(1)
    options={f'{r.country} {int(r.tenor)}Y · {r.date:%Y-%m-%d} · {"monthly" if "MONTHLY" in r.curve_type else "daily/reference"}':r for _,r in x.iterrows()}
    if not options:return
    a,b,c=st.columns([2,1,1])
    sel=a.selectbox('Reference',list(options),key='risk_market')
    direction=b.selectbox('Position',['Long','Short'],key='risk_side')
    size=c.number_input('Nominal (€)',min_value=100_000,value=1_000_000,step=100_000,key='risk_notional')
    if st.button('Add hypothetical position',type='primary'):
        r=options[sel]
        pv01=model_reference_pv01(size,r.yield_pct,int(r.tenor),direction)
        move=previous_move(df,r.country,r.tenor,r.curve_type)
        st.session_state.book.append({'Market':f'{r.country} {int(r.tenor)}Y','Side':direction,
           'Notional (€)':int(size),'PV01 (€/bp)':pv01,'Yield date':r.date.strftime('%Y-%m-%d'),
           'Frequency':'Monthly' if 'MONTHLY' in r.curve_type else 'Daily/reference',
           'Move (bp)':move['move_bp'] if move else None,
           'Estimated P&L (€)':position_pnl(pv01,move['move_bp']) if move else None})
    if st.session_state.book:
        st.markdown('**Positions added to the portfolio**')
        book=pd.DataFrame(st.session_state.book)
        st.dataframe(book,hide_index=True,use_container_width=True,column_config={
            'Notional (€)':st.column_config.NumberColumn(format='%d'),
            'PV01 (€/bp)':st.column_config.NumberColumn(format='%.2f'),
            'Move (bp)':st.column_config.NumberColumn(format='%+.1f'),
            'Estimated P&L (€)':st.column_config.NumberColumn(format='%.2f')
        })
        c1,c2=st.columns(2)
        c1.metric('Net signed PV01',f'€{book["PV01 (€/bp)"].sum():,.0f}/bp')
        c2.metric('Gross DV01',f'€{book["PV01 (€/bp)"].abs().sum():,.0f}/bp')
        st.caption('P&L figures below are grouped by observed frequency. Monthly and daily changes are NOT added together.')
        st.dataframe(book.groupby('Frequency',dropna=False)['Estimated P&L (€)'].sum(min_count=1).reset_index(),hide_index=True,
            column_config={'Estimated P&L (€)':st.column_config.NumberColumn(format='%.2f')})
        st.download_button('Export positions & risk (CSV)',book.to_csv(index=False).encode('utf-8'),file_name='desk_risk_report.csv')
        if st.button('Clear positions',key='clear_book'):st.session_state.book=[];st.rerun()
    else:
        st.caption('Add one or two positions to display their exposures and estimated contribution.')


st.sidebar.markdown('### ◈ EGB / INFLATION')
page=st.sidebar.radio('Desk modules',[
    'Home · Market Brief',
    '01 · Core EGB | 2–10Y - Hugo Fuentes',
    '02 · Southern EGB | 3–15Y - Vincenzo Asaro',
    '03 · Inflation Trading - Hugo Guinamant & William Fortier',
    '04 · Semi-Core / Illiquid | 2–10Y - Ouassim Loussaief',
],label_visibility='visible')
st.sidebar.divider()
if st.sidebar.button('↻ Refresh official data'):
    st.cache_data.clear()
    for k in list(st.session_state):
        if k.startswith(('attempt:','data:','error:','issues:','hicp:')): del st.session_state[k]
    st.rerun()
st.sidebar.markdown(
'<div class="sidebar-signature">Mathieu Voluntario</div>',
unsafe_allow_html=True
)
if page.startswith('Home'):home()
elif page.startswith('01'):core()
elif page.startswith('02'):south()
elif page.startswith('03'):inflation()
else:semi()
