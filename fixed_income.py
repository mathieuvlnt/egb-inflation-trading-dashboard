"""Fixed income pricing and valuation functions.

Prices are quoted per 100 of notional; rates are expressed as decimals.
"""
from dataclasses import dataclass
from datetime import date
from math import exp, isfinite
from dateutil.relativedelta import relativedelta
import numpy as np
from scipy.optimize import brentq

@dataclass(frozen=True)
class Curve:
    """Positive discount factors with log-linear interpolation"""
    t: tuple[float,...]
    df_values: tuple[float,...]

    def __post_init__(self):
        t=np.asarray(self.t,float); d=np.asarray(self.df_values,float)
        if not len(t) or len(t)!=len(d) or not np.isfinite(t).all() or not np.isfinite(d).all() or np.any(t<=0) or np.any(np.diff(t)<=0) or np.any(d<=0):
            raise ValueError('Curve needs increasing positive times and discount factors')

    def df(self,t):
        arr=np.asarray(t,dtype=float)
        if not np.isfinite(arr).all() or np.any(arr<0) or np.any(arr>self.t[-1]+1e-9):
            raise ValueError('Cash flow date outside curve support; no extrapolation')
        result=np.exp(np.interp(arr, [0,*self.t], [0,*np.log(self.df_values)]))
        return float(result) if result.ndim==0 else result

    @classmethod
    def from_ecb_spot(cls,tenor_years,spot_rates_percent):
        """ECB continuously compounded AAA zero spot rates -> exp(-z*T)"""
        t=np.asarray(tenor_years,float);r=np.asarray(spot_rates_percent,float)/100
        if not np.isfinite(r).all(): raise ValueError('Nonfinite zero rates')
        return cls(tuple(t),tuple(np.exp(-r*t)))

    @classmethod
    def from_input_discount_factors(cls,tenors,dfs):
        return cls(tuple(float(v) for v in tenors),tuple(float(v) for v in dfs))


def bootstrap_annual_par(par_rates: list[float]) -> Curve:
    """Single-curve bootstrap using annual par coupon rates.

    Par rates are expressed as decimals, with maturities from 1 to N years.
    """
    factors=[]
    for rate in par_rates:
        if not isfinite(rate) or rate<=-1:
            raise ValueError('Annual par rate must be finite and above -100%')
        d=(1-rate*sum(factors))/(1+rate)
        if d<=0: raise ValueError('Quotes imply nonpositive discount factor')
        factors.append(d)
    if not factors: raise ValueError('At least one par instrument needed')
    return Curve(tuple(float(i) for i in range(1,len(factors)+1)),tuple(factors))


def bond_cashflows(settlement:date,maturity:date,coupon_rate:float,frequency:int=1):
    """Bond cashflows with regular, unadjusted coupon dates and ACT/ACT accrual.

    Excludes ex-coupon conventions, irregular first coupons, holiday
    adjustments and inflation-linked bonds.
    """
    if not settlement<maturity: raise ValueError('Settlement must precede maturity')
    if frequency not in (1,2,4): raise ValueError('Supported coupon frequency: 1, 2, 4')
    if coupon_rate<0 or not isfinite(coupon_rate): raise ValueError('Invalid coupon')
    dates=[maturity]
    while dates[-1]>settlement:
        dates.append(maturity-relativedelta(months=12//frequency*len(dates)))
    dates=dates[::-1]
    prev,next_date=dates[0],dates[1]
    fraction=(settlement-prev).days/(next_date-prev).days
    accrued=100*coupon_rate/frequency*fraction
    flows=[]
    for i,d in enumerate(dates[1:]):
        amount=100*coupon_rate/frequency+(100 if d==maturity else 0)
        t=(d-settlement).days/365.0
        flows.append((t,amount,d))
    return flows,accrued


def yield_price(settlement,maturity,coupon_rate,frequency,yield_decimal):
    flows,accrued=bond_cashflows(settlement,maturity,coupon_rate,frequency)
    if yield_decimal<=-frequency: raise ValueError('Yield outside pricing domain')

    if coupon_rate > 0:
        fraction=accrued/(100*coupon_rate/frequency)
        times=[(i+1-fraction)/frequency for i in range(len(flows))]
    else:
        times=[t for t,amount,_ in flows]
    dirty=sum(amount*(1+yield_decimal/frequency)**(-frequency*t) for t,(_,amount,_) in zip(times,flows))
    return {'dirty':dirty,'clean':dirty-accrued,'accrued':accrued}


def price_from_curve(flows,curve:Curve,z:float=0.0):
    return float(sum(amount*curve.df(t)*exp(-z*t) for t,amount,*_ in flows))


def solve_z_spread(flows,curve:Curve,observed_dirty:float):
    if not isfinite(observed_dirty) or observed_dirty<=0:
        raise ValueError('Observed dirty price must be positive')
    f=lambda z:price_from_curve(flows,curve,z)-observed_dirty
    low,high=-1.0,1.0
    while f(low)<0 and low>-32: low*=2
    while f(high)>0 and high<32: high*=2
    if f(low)*f(high)>0: raise ValueError('Cannot bracket Z-spread for supplied data')
    z=brentq(f,low,high,xtol=1e-12)
    return z,price_from_curve(flows,curve,z)-observed_dirty


def parallel_pv01(flows,curve:Curve,z:float=0.0):
    """Signed price change for a +1bp *continuous parallel discount curve shift*."""
    return price_from_curve(flows,curve,z+1e-4)-price_from_curve(flows,curve,z)


def irs_annual(discount_curve:Curve,projection_curve:Curve,maturity:int,fixed_rate:float,notional:float,payer:bool=True):
    """Spot-start, annual reset/payment, simplified multicurve IRS"""
    if int(maturity)!=maturity or maturity<1 or notional<=0:
        raise ValueError('Positive integer maturity and notional required')
    periods=range(1,maturity+1)
    discounts=np.asarray([discount_curve.df(i) for i in periods])
    forwards=np.asarray([projection_curve.df(i-1)/projection_curve.df(i)-1 for i in periods])
    annuity=float(discounts.sum())
    fixed_pv=notional*fixed_rate*annuity
    floating_pv=notional*float(np.dot(forwards,discounts))
    par_rate=floating_pv/(notional*annuity)
    mtm=(floating_pv-fixed_pv)*(1 if payer else -1)
    return dict(par_rate=par_rate,fixed_pv=fixed_pv,floating_pv=floating_pv,annuity=annuity,mtm=mtm,forwards=forwards)


def ois_annual(discount_curve:Curve,maturity:int,fixed_rate:float,notional:float,payer:bool=True):
    """Single-curve idealized par OIS"""
    if int(maturity)!=maturity or maturity<1 or notional<=0: raise ValueError('Invalid OIS inputs')
    a=sum(discount_curve.df(i) for i in range(1,maturity+1))
    pv_float=notional*(1-discount_curve.df(maturity))
    pv_fixed=notional*fixed_rate*a
    return dict(par_rate=pv_float/notional/a,fixed_pv=pv_fixed,floating_pv=pv_float,
                mtm=(pv_float-pv_fixed)*(1 if payer else -1),annuity=a)


def par_asset_swap_annual(bond_dirty_per100:float,bond_coupon_decimal:float,
                          discount_curve:Curve,projection_curve:Curve,maturity:int):
    """Par asset swap pricing with annual cashflows.

    Assumes settlement on a coupon date, excluding accrued interest and stubs.
    Calculates the spread required to bring the bond-swap package to par.
    """
    if not bond_dirty_per100>0 or bond_coupon_decimal<0: raise ValueError('Invalid bond price/coupon')
    irs=irs_annual(discount_curve,projection_curve,maturity,bond_coupon_decimal,100,payer=False)
    a=irs['annuity']; floating=irs['floating_pv']; coupon_pv=irs['fixed_pv']
    spread=(100-bond_dirty_per100+coupon_pv-floating)/(100*a)
    swap_pv=floating+100*spread*a-coupon_pv
    residual=bond_dirty_per100+swap_pv-100
    return dict(asw_spread=spread,swap_pv=swap_pv,package_residual=residual,annuity=a,
                floating_pv=floating,fixed_pv=coupon_pv)


def zcis_settlement(notional,initial_index,final_index,fixed_rate,maturity,receive_inflation=True):
    if min(notional,initial_index,final_index,maturity)<=0: raise ValueError('Positive inputs required')
    payoff=notional*(final_index/initial_index-(1+fixed_rate)**maturity)
    return payoff*(1 if receive_inflation else -1)


def zcis_mark_to_market(notional,market_swap_quote,fixed_rate,maturity,discount_curve:Curve,receive_inflation=True):
    """ZC inflation swap mark-to-market using a user-supplied par rate and OIS discount curve.

    Excludes indexation lag, seasonality and basis effects.
    """
    if notional<=0 or maturity<=0: raise ValueError('Invalid ZCIS inputs')
    pv=notional*discount_curve.df(maturity)*((1+market_swap_quote)**maturity-(1+fixed_rate)**maturity)
    return pv*(1 if receive_inflation else -1)
