"""Dynamic EMA Pullback Strategy specification implemented as pure research logic.

Source specification:
- XAUUSD M5
- EMA50 / EMA200 trend filter
- Candle [2] pullback
- Candle [1] trigger
- min trigger body default 60 pips = 6.0 price units
- opposite wick <= 20% of candle range
- SL beyond trigger wick by 10 pips = 1.0 price unit
- TP default 1.5R
- BEP at +15 pips = +1.5 price units
- trailing distance 20 pips = 2.0 price units
- max active positions default 3

This module contains no MT5 connection, OpenAI, or order functions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Sequence

VERSION="dynamic-ema-pullback-v1"


@dataclass(frozen=True)
class Config:
    fast_ema:int=50
    slow_ema:int=200
    pip_price:float=0.1
    min_body_pips:float=60.0
    max_opposite_wick_ratio:float=0.20
    sl_buffer_pips:float=10.0
    risk_reward:float=1.5
    breakeven_trigger_pips:float=15.0
    trailing_pips:float=20.0
    fee_buffer_price:float=0.0
    max_active_positions:int=3

    @property
    def min_body_price(self)->float:return self.min_body_pips*self.pip_price
    @property
    def sl_buffer_price(self)->float:return self.sl_buffer_pips*self.pip_price
    @property
    def breakeven_trigger_price(self)->float:return self.breakeven_trigger_pips*self.pip_price
    @property
    def trailing_price(self)->float:return self.trailing_pips*self.pip_price

    def validate(self)->None:
        nums=(self.pip_price,self.min_body_pips,self.max_opposite_wick_ratio,
              self.sl_buffer_pips,self.risk_reward,self.breakeven_trigger_pips,
              self.trailing_pips,self.fee_buffer_price)
        if any(type(x) not in (int,float) or not math.isfinite(x) for x in nums):
            raise ValueError("DYN_EMA_NONFINITE_CONFIG")
        if (self.fast_ema<2 or self.slow_ema<=self.fast_ema or self.pip_price<=0
                or self.min_body_pips<=0 or not 0<=self.max_opposite_wick_ratio<=1
                or self.sl_buffer_pips<0 or self.risk_reward<=0
                or self.breakeven_trigger_pips<0 or self.trailing_pips<=0
                or self.fee_buffer_price<0 or self.max_active_positions<1):
            raise ValueError("DYN_EMA_INVALID_CONFIG")


@dataclass(frozen=True)
class Bar:
    time:int
    open:float
    high:float
    low:float
    close:float


@dataclass(frozen=True)
class Decision:
    action:str  # BUY / SELL / WAIT
    reason:str
    trigger_time:int
    ema_fast:float
    ema_slow:float
    pullback_close:float
    trigger_body:float
    opposite_wick_ratio:float
    entry_reference:float
    sl:float
    tp:float
    risk_distance:float

    def to_dict(self)->dict[str,Any]:return asdict(self)


@dataclass(frozen=True)
class ManagementDecision:
    new_sl:float
    breakeven_active:bool
    trailing_active:bool
    reason:str


def _finite(*values)->bool:
    return all(type(x) in (int,float) and math.isfinite(x) for x in values)


def _validate_bar(bar:Bar)->None:
    if (type(bar.time) is not int or bar.time<=0
            or not _finite(bar.open,bar.high,bar.low,bar.close)
            or min(bar.open,bar.high,bar.low,bar.close)<=0
            or bar.high<max(bar.open,bar.low,bar.close)
            or bar.low>min(bar.open,bar.high,bar.close)):
        raise ValueError("DYN_EMA_INVALID_BAR")


def ema(values:Sequence[float],period:int)->list[float]:
    if period<2 or len(values)<period:
        raise ValueError("DYN_EMA_INSUFFICIENT_HISTORY")
    if any(not _finite(x) or x<=0 for x in values):
        raise ValueError("DYN_EMA_INVALID_CLOSE")
    alpha=2.0/(period+1.0)
    out=[]
    value=float(values[0])
    for x in values:
        value=value+alpha*(float(x)-value)
        out.append(value)
    return out


def evaluate(bars:Sequence[Bar],config:Config=Config())->Decision:
    """Evaluate latest two CLOSED bars.

    bars[-1] is Candle [1] trigger.
    bars[-2] is Candle [2] pullback.
    """
    config.validate()
    if len(bars)<config.slow_ema+20:
        raise ValueError("DYN_EMA_NEEDS_MORE_HISTORY")
    for bar in bars:_validate_bar(bar)
    for a,b in zip(bars,bars[1:]):
        if b.time<=a.time:raise ValueError("DYN_EMA_UNORDERED_BARS")

    closes=[b.close for b in bars]
    ef=ema(closes,config.fast_ema)
    es=ema(closes,config.slow_ema)

    trigger=bars[-1];pullback=bars[-2]
    fast=float(ef[-1]);slow=float(es[-1])
    body=abs(trigger.close-trigger.open)
    rng=trigger.high-trigger.low
    if rng<=0:
        return Decision("WAIT","zero-range trigger",trigger.time,fast,slow,pullback.close,
                        body,1.0,trigger.close,0.0,0.0,0.0)

    bullish=trigger.close>trigger.open
    bearish=trigger.close<trigger.open
    upper_wick=trigger.high-max(trigger.open,trigger.close)
    lower_wick=min(trigger.open,trigger.close)-trigger.low

    if fast>slow:
        opposite=upper_wick/rng
        trend_ok=True
        pullback_ok=(pullback.close<ef[-2] or pullback.close<es[-2])
        signal_ok=(bullish and body>=config.min_body_price
                   and opposite<=config.max_opposite_wick_ratio)
        if trend_ok and pullback_ok and signal_ok:
            entry=trigger.close
            sl=trigger.low-config.sl_buffer_price
            risk=entry-sl
            if risk<=0:return _wait(trigger,fast,slow,pullback,body,opposite,"invalid BUY risk distance")
            tp=entry+config.risk_reward*risk
            return Decision("BUY","EMA50>EMA200 + Candle[2] pullback + bullish trigger",
                            trigger.time,fast,slow,pullback.close,body,opposite,
                            entry,sl,tp,risk)
        reason=_reason("BUY",pullback_ok,bullish,body,opposite,config)
        return _wait(trigger,fast,slow,pullback,body,opposite,reason)

    if fast<slow:
        opposite=lower_wick/rng
        pullback_ok=(pullback.close>ef[-2] or pullback.close>es[-2])
        signal_ok=(bearish and body>=config.min_body_price
                   and opposite<=config.max_opposite_wick_ratio)
        if pullback_ok and signal_ok:
            entry=trigger.close
            sl=trigger.high+config.sl_buffer_price
            risk=sl-entry
            if risk<=0:return _wait(trigger,fast,slow,pullback,body,opposite,"invalid SELL risk distance")
            tp=entry-config.risk_reward*risk
            if tp<=0:return _wait(trigger,fast,slow,pullback,body,opposite,"invalid SELL TP")
            return Decision("SELL","EMA50<EMA200 + Candle[2] pullback + bearish trigger",
                            trigger.time,fast,slow,pullback.close,body,opposite,
                            entry,sl,tp,risk)
        reason=_reason("SELL",pullback_ok,bearish,body,opposite,config)
        return _wait(trigger,fast,slow,pullback,body,opposite,reason)

    return _wait(trigger,fast,slow,pullback,body,0.0,"EMA50 equals EMA200")


def _reason(side,pullback_ok,direction_ok,body,wick_ratio,config)->str:
    failed=[]
    if not pullback_ok:failed.append("Candle[2] pullback")
    if not direction_ok:failed.append("trigger direction")
    if body<config.min_body_price:failed.append("trigger body")
    if wick_ratio>config.max_opposite_wick_ratio:failed.append("opposite wick")
    return side+" conditions failed: "+(",".join(failed) if failed else "unknown")


def _wait(trigger,fast,slow,pullback,body,wick_ratio,reason)->Decision:
    return Decision("WAIT",reason,trigger.time,fast,slow,pullback.close,body,wick_ratio,
                    trigger.close,0.0,0.0,0.0)


def can_open(active_positions:int,config:Config=Config())->bool:
    config.validate()
    if type(active_positions) is not int or active_positions<0:
        raise ValueError("DYN_EMA_INVALID_ACTIVE_POSITIONS")
    return active_positions<config.max_active_positions


def manage_position(side:str,entry:float,current_price:float,current_sl:float,
                    config:Config=Config())->ManagementDecision:
    """Research implementation of Auto BEP + trailing from the specification.

    fee_buffer_price is explicit because the source says "Entry + Buffer Fee"
    but does not define a numeric fee buffer.
    """
    config.validate()
    if side not in {"BUY","SELL"} or not _finite(entry,current_price,current_sl) or min(entry,current_price)<=0:
        raise ValueError("DYN_EMA_INVALID_MANAGEMENT_INPUT")
    if current_sl<0:raise ValueError("DYN_EMA_INVALID_SL")

    if side=="BUY":
        profit=current_price-entry
        if profit<config.breakeven_trigger_price:
            return ManagementDecision(current_sl,False,False,"BEP threshold not reached")
        bep=entry+config.fee_buffer_price
        trailed=current_price-config.trailing_price
        candidate=max(bep,trailed)
        new_sl=max(current_sl,candidate)
        return ManagementDecision(new_sl,True,trailed>bep,"BUY BEP/trailing")

    profit=entry-current_price
    if profit<config.breakeven_trigger_price:
        return ManagementDecision(current_sl,False,False,"BEP threshold not reached")
    bep=entry-config.fee_buffer_price
    trailed=current_price+config.trailing_price
    candidate=min(bep,trailed)
    new_sl=candidate if current_sl<=0 else min(current_sl,candidate)
    return ManagementDecision(new_sl,True,trailed<bep,"SELL BEP/trailing")
