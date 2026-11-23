"""Core analytics for sovereign yields, spreads and portfolio risk."""
import numpy as np
import pandas as pd


def latest_curve(market, country, definition=None):
    x=market[market.country.eq(country)].copy()
    if definition is not None:
        x=x[x.curve_type.eq(definition)]
    if x.empty:return x
    # Each tenor gets its own last available date; expose staleness rather than fill.
    return x.sort_values('date').groupby(['tenor','curve_type'],as_index=False).tail(1).sort_values('tenor')


def slope_series(market,country,short=2,long=10,definition=None):
    x=market[market.country.eq(country)].copy()
    if definition is not None:x=x[x.curve_type.eq(definition)]
    if x.empty:return pd.DataFrame(columns=['date','slope_bp'])
    x=x[x.tenor.isin([short,long])]
    if x.duplicated(['date','tenor']).any():
        raise ValueError('Multiple conflicting yield definitions: select one source/methodology')
    wide=x.pivot(index='date',columns='tenor',values='yield_pct')
    if short not in wide or long not in wide:return pd.DataFrame(columns=['date','slope_bp'])
    out=((wide[long]-wide[short])*100).dropna().rename('slope_bp').reset_index()
    return out.sort_values('date')


def relative_spread(market,country,benchmark,tenor=10,definition_a=None,definition_b=None):
    a=market[(market.country==country)&(market.tenor==tenor)]
    b=market[(market.country==benchmark)&(market.tenor==tenor)]
    if definition_a:a=a[a.curve_type==definition_a]
    if definition_b:b=b[b.curve_type==definition_b]
    if a.duplicated('date').any() or b.duplicated('date').any():
        raise ValueError('Ambiguous definitions: choose one source for each country')
    out=a[['date','yield_pct']].merge(b[['date','yield_pct']],on='date',suffixes=('_country','_benchmark'))
    out['spread_bp']=(out.yield_pct_country-out.yield_pct_benchmark)*100
    return out.sort_values('date')


def rolling_summary(x,window=60):
    x=pd.Series(x).dropna().astype(float)
    if len(x)<3:return None
    x=x.tail(window)
    sd=float(x.std(ddof=1))
    return {'current':float(x.iloc[-1]),'mean':float(x.mean()),
            'z':float((x.iloc[-1]-x.mean())/sd) if sd>1e-12 else float('nan'),
            'percentile':float((x<=x.iloc[-1]).mean()*100),'n':len(x)}


def model_reference_pv01(nominal_eur,reference_yield_pct,tenor,side='Long'):
    """Estimate bond DV01 using an annual-coupon par bond.

    Coupon is set to the reference yield, with a 1 bp yield bump.
    """
    n=int(tenor)
    if n!=tenor or n<1 or not (0<nominal_eur and -5<reference_yield_pct<25):
        raise ValueError('Valid integer maturity, positive notional and reasonable yield required')
    y=reference_yield_pct/100
    coupon=100*y
    def pv(rate):return sum(coupon/(1+rate)**t for t in range(1,n+1))+100/(1+rate)**n
    pv01=(pv(y+0.0001)-pv(y))*nominal_eur/100
    return float(pv01 if side=='Long' else -pv01)


def previous_move(market,country,tenor,curve_type):
    x=market[(market.country==country)&(market.tenor==tenor)&(market.curve_type==curve_type)].sort_values('date')
    if len(x)<2:return None
    a,b=x.iloc[-2],x.iloc[-1]
    return {'start':a.date,'end':b.date,'move_bp':float((b.yield_pct-a.yield_pct)*100),'yield_pct':float(b.yield_pct)}


def position_pnl(pv01_eur_bp,delta_yield_bp):
    return float(pv01_eur_bp)*float(delta_yield_bp)
