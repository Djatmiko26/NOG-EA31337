"""Ten independent market-logic voters for XAUUSD M5 ensemble research.

Pure deterministic logic: no MT5 connection, no OpenAI, no order functions.
Consumes the existing live_ai_core payload of CLOSED candles plus current spread.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import median
from typing import Any

VERSION="ensemble-10-v1"
DIRECTIONAL_LOGICS=9
MIN_SUPPORT=6
MIN_MARGIN=3
MIN_CORE_SUPPORT=2
CORE_NAMES={"ema_trend","ema_slope","breakout","market_structure"}


@dataclass(frozen=True)
class Vote:
    name:str
    signal:str  # BUY / SELL / WAIT / VETO
    reason:str
    strength:float=0.0


@dataclass(frozen=True)
class EnsembleDecision:
    action:str
    buy_votes:int
    sell_votes:int
    wait_votes:int
    vetoes:int
    core_support:int
    support:int
    margin:int
    consensus:float
    suggested_rr:float
    reason:str
    votes:tuple[Vote,...]

    def to_dict(self)->dict[str,Any]:
        d=asdict(self)
        d["votes"]=[asdict(v) for v in self.votes]
        return d


def _num(value)->float:
    if type(value) not in (int,float) or not math.isfinite(value):
        raise ValueError("ENSEMBLE_NONFINITE_VALUE")
    return float(value)


def _signal(delta:float,eps:float=0.0)->str:
    if delta>eps:return "BUY"
    if delta<-eps:return "SELL"
    return "WAIT"


def _vote(name,signal,reason,strength=0.0):
    if signal not in {"BUY","SELL","WAIT","VETO"}:
        raise ValueError("ENSEMBLE_INVALID_SIGNAL")
    strength=max(0.0,min(1.0,float(strength)))
    return Vote(name,signal,reason,strength)


def _validate(payload):
    candles=payload.get("candles")
    quote=payload.get("current_quote")
    if not isinstance(candles,list) or len(candles)<20 or not isinstance(quote,dict):
        raise ValueError("ENSEMBLE_NEEDS_20_CLOSED_BARS")
    required={"open","high","low","close","tick_volume","ema20","ema50","atr14","rsi14"}
    for row in candles:
        if not required.issubset(row):
            raise ValueError("ENSEMBLE_MISSING_CANDLE_FIELDS")
        o,h,l,c=[_num(row[k]) for k in ("open","high","low","close")]
        if min(o,h,l,c)<=0 or h<max(o,l,c) or l>min(o,h,c):
            raise ValueError("ENSEMBLE_INVALID_OHLC")
        if type(row["tick_volume"]) is not int or row["tick_volume"]<0:
            raise ValueError("ENSEMBLE_INVALID_VOLUME")
        for k in ("ema20","ema50","atr14","rsi14"):_num(row[k])
    spread=_num(quote.get("spread_price"))
    if spread<0:raise ValueError("ENSEMBLE_INVALID_SPREAD")
    return candles,spread


def evaluate_votes(payload)->tuple[Vote,...]:
    c,spread=_validate(payload)
    x=c[-1];prev=c[-2]
    atr=_num(x["atr14"]);close=_num(x["close"]);open_=_num(x["open"])
    ema20=_num(x["ema20"]);ema50=_num(x["ema50"]);rsi=_num(x["rsi14"])
    if atr<=0:raise ValueError("ENSEMBLE_ATR_NOT_POSITIVE")

    votes=[]

    # 1. EMA trend alignment.
    if close>ema20>ema50:
        votes.append(_vote("ema_trend","BUY","close>ema20>ema50",min(1,(close-ema50)/atr)))
    elif close<ema20<ema50:
        votes.append(_vote("ema_trend","SELL","close<ema20<ema50",min(1,(ema50-close)/atr)))
    else:votes.append(_vote("ema_trend","WAIT","EMA alignment mixed"))

    # 2. EMA slopes agree.
    e20_old=_num(c[-4]["ema20"]);e50_old=_num(c[-6]["ema50"])
    s20=(ema20-e20_old)/atr;s50=(ema50-e50_old)/atr
    if s20>0.08 and s50>0.03:votes.append(_vote("ema_slope","BUY","EMA20/50 rising",min(1,abs(s20)+abs(s50))))
    elif s20<-0.08 and s50<-0.03:votes.append(_vote("ema_slope","SELL","EMA20/50 falling",min(1,abs(s20)+abs(s50))))
    else:votes.append(_vote("ema_slope","WAIT","EMA slopes not aligned"))

    # 3. 12-bar breakout with a small ATR confirmation.
    look=c[-13:-1]
    hi=max(_num(r["high"]) for r in look);lo=min(_num(r["low"]) for r in look)
    if close>=hi+0.03*atr:votes.append(_vote("breakout","BUY","12-bar upside breakout",min(1,(close-hi)/atr)))
    elif close<=lo-0.03*atr:votes.append(_vote("breakout","SELL","12-bar downside breakout",min(1,(lo-close)/atr)))
    else:votes.append(_vote("breakout","WAIT","no confirmed 12-bar breakout"))

    # 4. Pullback continuation around EMA20, only with established EMA trend.
    dist=abs(close-ema20)/atr
    if ema20>ema50 and _num(x["low"])<=ema20+0.20*atr and close>=ema20 and close>open_:
        votes.append(_vote("pullback","BUY","bull trend pullback reclaimed EMA20",max(0,1-dist)))
    elif ema20<ema50 and _num(x["high"])>=ema20-0.20*atr and close<=ema20 and close<open_:
        votes.append(_vote("pullback","SELL","bear trend pullback rejected EMA20",max(0,1-dist)))
    else:votes.append(_vote("pullback","WAIT","no trend-continuation pullback"))

    # 5. RSI momentum, deliberately neutral in the middle.
    if rsi>=58:votes.append(_vote("rsi_momentum","BUY",f"RSI={rsi:.1f} bullish",min(1,(rsi-50)/25)))
    elif rsi<=42:votes.append(_vote("rsi_momentum","SELL",f"RSI={rsi:.1f} bearish",min(1,(50-rsi)/25)))
    else:votes.append(_vote("rsi_momentum","WAIT",f"RSI={rsi:.1f} neutral"))

    # 6. Last closed candle impulse relative to ATR.
    body=close-open_;body_atr=abs(body)/atr
    if body_atr>=0.35:
        votes.append(_vote("candle_impulse","BUY" if body>0 else "SELL",
                           f"body/ATR={body_atr:.2f}",min(1,body_atr)))
    else:votes.append(_vote("candle_impulse","WAIT",f"body/ATR={body_atr:.2f} small"))

    # 7. Market structure: recent 4-bar range shifted versus prior 4 bars.
    recent=c[-4:];prior=c[-8:-4]
    recent_hi=max(_num(r["high"]) for r in recent);recent_lo=min(_num(r["low"]) for r in recent)
    prior_hi=max(_num(r["high"]) for r in prior);prior_lo=min(_num(r["low"]) for r in prior)
    if recent_hi>prior_hi and recent_lo>prior_lo:
        votes.append(_vote("market_structure","BUY","higher range high and low",min(1,(recent_lo-prior_lo)/atr)))
    elif recent_hi<prior_hi and recent_lo<prior_lo:
        votes.append(_vote("market_structure","SELL","lower range high and low",min(1,(prior_hi-recent_hi)/atr)))
    else:votes.append(_vote("market_structure","WAIT","range structure mixed"))

    # 8. Tick-volume impulse confirms candle direction.
    base_vol=median([int(r["tick_volume"]) for r in c[-11:-1]])
    vol=int(x["tick_volume"]);ratio=(vol/base_vol if base_vol>0 else 0.0)
    if ratio>=1.20 and body!=0:
        votes.append(_vote("volume_impulse","BUY" if body>0 else "SELL",
                           f"tick-volume ratio={ratio:.2f}",min(1,(ratio-1)/0.8)))
    else:votes.append(_vote("volume_impulse","WAIT",f"tick-volume ratio={ratio:.2f}"))

    # 9. ATR expansion + directional close versus previous close.
    old_atr=median([_num(r["atr14"]) for r in c[-11:-1]])
    atr_ratio=(atr/old_atr if old_atr>0 else 0)
    delta=close-_num(prev["close"])
    if atr_ratio>=1.05 and abs(delta)/atr>=0.15:
        votes.append(_vote("atr_expansion","BUY" if delta>0 else "SELL",
                           f"ATR expansion={atr_ratio:.2f}, delta/ATR={abs(delta)/atr:.2f}",
                           min(1,(atr_ratio-1)*3+abs(delta)/atr)))
    else:votes.append(_vote("atr_expansion","WAIT",f"ATR expansion={atr_ratio:.2f}"))

    # 10. Hard execution-quality veto. It never creates direction.
    spread_atr=spread/atr
    if spread_atr>0.10:
        votes.append(_vote("execution_quality","VETO",f"spread/ATR={spread_atr:.3f} too high",1))
    elif atr/close<0.00020:
        votes.append(_vote("execution_quality","VETO",f"ATR/close={atr/close:.6f} too low",1))
    else:
        votes.append(_vote("execution_quality","WAIT",f"quality OK spread/ATR={spread_atr:.3f}",0))

    return tuple(votes)


def aggregate_votes(votes:tuple[Vote,...])->EnsembleDecision:
    if len(votes)!=10 or len({v.name for v in votes})!=10:
        raise ValueError("ENSEMBLE_REQUIRES_EXACTLY_10_LOGICS")
    buy=sum(v.signal=="BUY" for v in votes)
    sell=sum(v.signal=="SELL" for v in votes)
    waits=sum(v.signal=="WAIT" for v in votes)
    veto=sum(v.signal=="VETO" for v in votes)
    direction="BUY" if buy>sell else "SELL" if sell>buy else "WAIT"
    support=max(buy,sell);margin=abs(buy-sell)
    core_support=sum(v.name in CORE_NAMES and v.signal==direction for v in votes) if direction!="WAIT" else 0
    consensus=support/DIRECTIONAL_LOGICS
    action=direction
    reason=""
    if veto:
        action="WAIT";reason="VETO execution quality"
    elif direction=="WAIT":
        action="WAIT";reason="directional tie"
    elif support<MIN_SUPPORT:
        action="WAIT";reason=f"support {support}<{MIN_SUPPORT}"
    elif margin<MIN_MARGIN:
        action="WAIT";reason=f"vote margin {margin}<{MIN_MARGIN}"
    elif core_support<MIN_CORE_SUPPORT:
        action="WAIT";reason=f"core support {core_support}<{MIN_CORE_SUPPORT}"
    else:
        reason=f"consensus {direction}: support={support}, margin={margin}, core={core_support}"

    # Research suggestion only; it is NOT sent as an order parameter.
    suggested_rr=(
        2.0 if support>=9 and margin>=7 else
        1.5 if support>=8 and margin>=6 else
        1.25 if support>=7 and margin>=5 else
        1.0
    )
    if action=="WAIT":suggested_rr=0.0

    return EnsembleDecision(action,buy,sell,waits,veto,core_support,support,margin,
                            round(consensus,6),suggested_rr,reason,votes)


def evaluate(payload)->EnsembleDecision:
    return aggregate_votes(evaluate_votes(payload))
