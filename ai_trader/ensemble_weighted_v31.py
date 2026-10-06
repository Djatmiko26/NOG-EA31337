"""Weighted 10-logic ensemble for XAUUSD M5 research.

Pure deterministic component:
- no MT5 connection
- no OpenAI
- no order functions

Core ideas:
- market structure and trend receive larger weights than secondary indicators
- execution quality is a hard veto, never a directional vote
- regime gating changes how much evidence is required
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import median
from typing import Any

VERSION="weighted-ensemble-v31"

WEIGHTS={
    "market_structure":2.0,
    "ema_trend":2.0,
    "breakout":1.5,
    "ema_slope":1.5,
    "pullback":1.0,
    "rsi_momentum":0.75,
    "candle_impulse":0.75,
    "volume_impulse":0.5,
    "atr_expansion":0.5,
}
CORE={"market_structure","ema_trend","breakout","ema_slope"}
TOTAL_DIRECTIONAL_WEIGHT=sum(WEIGHTS.values())

BASE_MIN_SCORE=6.0
BASE_MIN_MARGIN=3.0
BASE_MIN_CORE_COUNT=2


@dataclass(frozen=True)
class Vote:
    name:str
    signal:str  # BUY / SELL / WAIT / VETO
    weight:float
    strength:float
    reason:str


@dataclass(frozen=True)
class WeightedDecision:
    action:str
    regime:str
    buy_score:float
    sell_score:float
    score:float
    margin:float
    core_count:int
    buy_count:int
    sell_count:int
    wait_count:int
    vetoes:int
    consensus:float
    suggested_rr:float
    ai_min_confidence:int
    reason:str
    votes:tuple[Vote,...]

    def to_dict(self)->dict[str,Any]:
        d=asdict(self)
        d["votes"]=[asdict(v) for v in self.votes]
        return d


def _num(value)->float:
    if type(value) not in (int,float) or not math.isfinite(value):
        raise ValueError("W31_NONFINITE_VALUE")
    return float(value)


def _vote(name,signal,reason,strength=0.0)->Vote:
    if name=="execution_quality":
        weight=0.0
    else:
        if name not in WEIGHTS:raise ValueError("W31_UNKNOWN_LOGIC")
        weight=WEIGHTS[name]
    if signal not in {"BUY","SELL","WAIT","VETO"}:
        raise ValueError("W31_INVALID_SIGNAL")
    strength=max(0.0,min(1.0,float(strength)))
    return Vote(name,signal,weight,strength,reason)


def _validate(payload):
    candles=payload.get("candles")
    quote=payload.get("current_quote")
    if not isinstance(candles,list) or len(candles)<20 or not isinstance(quote,dict):
        raise ValueError("W31_NEEDS_20_CLOSED_BARS")
    required={"open","high","low","close","tick_volume","ema20","ema50","atr14","rsi14"}
    for row in candles:
        if not required.issubset(row):raise ValueError("W31_MISSING_FIELDS")
        o,h,l,c=[_num(row[k]) for k in ("open","high","low","close")]
        if min(o,h,l,c)<=0 or h<max(o,l,c) or l>min(o,h,c):
            raise ValueError("W31_INVALID_OHLC")
        if type(row["tick_volume"]) is not int or row["tick_volume"]<0:
            raise ValueError("W31_INVALID_VOLUME")
        for k in ("ema20","ema50","atr14","rsi14"):_num(row[k])
    spread=_num(quote.get("spread_price"))
    if spread<0:raise ValueError("W31_INVALID_SPREAD")
    return candles,spread


def _features(payload):
    c,spread=_validate(payload)
    x=c[-1];prev=c[-2]
    atr=_num(x["atr14"]);close=_num(x["close"]);open_=_num(x["open"])
    ema20=_num(x["ema20"]);ema50=_num(x["ema50"]);rsi=_num(x["rsi14"])
    if atr<=0:raise ValueError("W31_ATR_NOT_POSITIVE")

    look12=c[-13:-1]
    high12=max(_num(r["high"]) for r in look12)
    low12=min(_num(r["low"]) for r in look12)

    e20_old=_num(c[-4]["ema20"])
    e50_old=_num(c[-6]["ema50"])
    slope20=(ema20-e20_old)/atr
    slope50=(ema50-e50_old)/atr

    recent=c[-4:];prior=c[-8:-4]
    recent_hi=max(_num(r["high"]) for r in recent)
    recent_lo=min(_num(r["low"]) for r in recent)
    prior_hi=max(_num(r["high"]) for r in prior)
    prior_lo=min(_num(r["low"]) for r in prior)

    old_atr=median([_num(r["atr14"]) for r in c[-11:-1]])
    atr_expansion=atr/old_atr if old_atr>0 else 0.0
    bar_range=(_num(x["high"])-_num(x["low"]))/atr
    ema_gap=abs(ema20-ema50)/atr
    spread_atr=spread/atr
    body=close-open_
    body_atr=abs(body)/atr
    delta=close-_num(prev["close"])
    delta_atr=abs(delta)/atr

    base_vol=median([int(r["tick_volume"]) for r in c[-11:-1]])
    volume_ratio=(int(x["tick_volume"])/base_vol) if base_vol>0 else 0.0

    structure=(
        "BUY" if recent_hi>prior_hi and recent_lo>prior_lo else
        "SELL" if recent_hi<prior_hi and recent_lo<prior_lo else
        "WAIT"
    )
    breakout=(
        "BUY" if close>=high12+0.03*atr else
        "SELL" if close<=low12-0.03*atr else
        "WAIT"
    )

    return {
        "candles":c,"x":x,"prev":prev,"atr":atr,"close":close,"open":open_,
        "ema20":ema20,"ema50":ema50,"rsi":rsi,"slope20":slope20,"slope50":slope50,
        "high12":high12,"low12":low12,"structure":structure,"breakout":breakout,
        "atr_expansion":atr_expansion,"bar_range_atr":bar_range,"ema_gap_atr":ema_gap,
        "spread_atr":spread_atr,"body":body,"body_atr":body_atr,"delta":delta,
        "delta_atr":delta_atr,"volume_ratio":volume_ratio,
    }


def detect_regime(payload)->str:
    f=_features(payload)
    trend_up=(f["ema20"]>f["ema50"] and f["slope20"]>0.05 and f["slope50"]>0.015)
    trend_down=(f["ema20"]<f["ema50"] and f["slope20"]<-0.05 and f["slope50"]<-0.015)

    # Large expansion/range means execution uncertainty is elevated even if direction is clear.
    if f["atr_expansion"]>=1.35 or f["bar_range_atr"]>=1.80:
        return "VOLATILE"
    if trend_up and f["structure"]!="SELL" and f["ema_gap_atr"]>=0.08:
        return "BULL_TREND"
    if trend_down and f["structure"]!="BUY" and f["ema_gap_atr"]>=0.08:
        return "BEAR_TREND"
    if f["ema_gap_atr"]<0.10 and f["breakout"]=="WAIT" and f["structure"]=="WAIT":
        return "RANGE"
    return "UNCLEAR"


def evaluate_votes(payload)->tuple[Vote,...]:
    f=_features(payload)
    x=f["x"];atr=f["atr"];close=f["close"];open_=f["open"]
    ema20=f["ema20"];ema50=f["ema50"];rsi=f["rsi"]
    votes=[]

    # 1. Market structure: highest-priority directional evidence.
    if f["structure"]=="BUY":
        strength=min(1,max(0,(_num(x["low"])-min(_num(r["low"]) for r in f["candles"][-8:-4]))/atr))
        votes.append(_vote("market_structure","BUY","higher recent high and low",strength))
    elif f["structure"]=="SELL":
        strength=min(1,max(0,(max(_num(r["high"]) for r in f["candles"][-8:-4])-_num(x["high"]))/atr))
        votes.append(_vote("market_structure","SELL","lower recent high and low",strength))
    else:votes.append(_vote("market_structure","WAIT","structure mixed"))

    # 2. EMA trend alignment.
    if close>ema20>ema50:
        votes.append(_vote("ema_trend","BUY","close>ema20>ema50",min(1,(close-ema50)/atr)))
    elif close<ema20<ema50:
        votes.append(_vote("ema_trend","SELL","close<ema20<ema50",min(1,(ema50-close)/atr)))
    else:votes.append(_vote("ema_trend","WAIT","EMA alignment mixed"))

    # 3. Breakout.
    if f["breakout"]=="BUY":
        votes.append(_vote("breakout","BUY","confirmed 12-bar upside breakout",min(1,(close-f["high12"])/atr)))
    elif f["breakout"]=="SELL":
        votes.append(_vote("breakout","SELL","confirmed 12-bar downside breakout",min(1,(f["low12"]-close)/atr)))
    else:votes.append(_vote("breakout","WAIT","no confirmed 12-bar breakout"))

    # 4. EMA slope.
    if f["slope20"]>0.08 and f["slope50"]>0.03:
        votes.append(_vote("ema_slope","BUY","EMA20/50 rising",min(1,abs(f["slope20"])+abs(f["slope50"]))))
    elif f["slope20"]<-0.08 and f["slope50"]<-0.03:
        votes.append(_vote("ema_slope","SELL","EMA20/50 falling",min(1,abs(f["slope20"])+abs(f["slope50"]))))
    else:votes.append(_vote("ema_slope","WAIT","EMA slopes not aligned"))

    # 5. Pullback continuation.
    dist=abs(close-ema20)/atr
    if ema20>ema50 and _num(x["low"])<=ema20+0.20*atr and close>=ema20 and close>open_:
        votes.append(_vote("pullback","BUY","bull pullback reclaimed EMA20",max(0,1-dist)))
    elif ema20<ema50 and _num(x["high"])>=ema20-0.20*atr and close<=ema20 and close<open_:
        votes.append(_vote("pullback","SELL","bear pullback rejected EMA20",max(0,1-dist)))
    else:votes.append(_vote("pullback","WAIT","no continuation pullback"))

    # 6. RSI momentum.
    if rsi>=58:votes.append(_vote("rsi_momentum","BUY",f"RSI={rsi:.1f}",min(1,(rsi-50)/25)))
    elif rsi<=42:votes.append(_vote("rsi_momentum","SELL",f"RSI={rsi:.1f}",min(1,(50-rsi)/25)))
    else:votes.append(_vote("rsi_momentum","WAIT",f"RSI={rsi:.1f} neutral"))

    # 7. Candle impulse.
    if f["body_atr"]>=0.35:
        votes.append(_vote("candle_impulse","BUY" if f["body"]>0 else "SELL",
                           f"body/ATR={f['body_atr']:.2f}",min(1,f["body_atr"])))
    else:votes.append(_vote("candle_impulse","WAIT",f"body/ATR={f['body_atr']:.2f} small"))

    # 8. Tick-volume impulse.
    if f["volume_ratio"]>=1.20 and f["body"]!=0:
        votes.append(_vote("volume_impulse","BUY" if f["body"]>0 else "SELL",
                           f"tick-volume ratio={f['volume_ratio']:.2f}",
                           min(1,(f["volume_ratio"]-1)/0.8)))
    else:votes.append(_vote("volume_impulse","WAIT",f"tick-volume ratio={f['volume_ratio']:.2f}"))

    # 9. ATR expansion + close displacement.
    if f["atr_expansion"]>=1.05 and f["delta_atr"]>=0.15 and f["delta"]!=0:
        votes.append(_vote("atr_expansion","BUY" if f["delta"]>0 else "SELL",
                           f"ATR expansion={f['atr_expansion']:.2f}, delta/ATR={f['delta_atr']:.2f}",
                           min(1,(f["atr_expansion"]-1)*3+f["delta_atr"])))
    else:votes.append(_vote("atr_expansion","WAIT",f"ATR expansion={f['atr_expansion']:.2f}"))

    # 10. Execution-quality hard veto.
    if f["spread_atr"]>0.10:
        votes.append(_vote("execution_quality","VETO",f"spread/ATR={f['spread_atr']:.3f} too high",1))
    elif atr/close<0.00020:
        votes.append(_vote("execution_quality","VETO",f"ATR/close={atr/close:.6f} too low",1))
    else:
        votes.append(_vote("execution_quality","WAIT",f"quality OK spread/ATR={f['spread_atr']:.3f}",0))

    return tuple(votes)


def _suggest_rr(regime:str,score:float,margin:float)->float:
    if regime=="VOLATILE":return 1.0
    if regime=="UNCLEAR":return 1.0
    if score>=8.5 and margin>=6.0:return 2.0
    if score>=7.5 and margin>=5.0:return 1.5
    if score>=6.5 and margin>=4.0:return 1.25
    return 1.0


def _ai_threshold(regime:str)->int:
    return 75 if regime in {"VOLATILE","UNCLEAR"} else 70


def aggregate_votes(votes:tuple[Vote,...],regime:str)->WeightedDecision:
    if len(votes)!=10 or len({v.name for v in votes})!=10:
        raise ValueError("W31_REQUIRES_EXACTLY_10_LOGICS")
    if regime not in {"BULL_TREND","BEAR_TREND","RANGE","VOLATILE","UNCLEAR"}:
        raise ValueError("W31_INVALID_REGIME")

    buy_score=sum(v.weight for v in votes if v.signal=="BUY")
    sell_score=sum(v.weight for v in votes if v.signal=="SELL")
    buy_count=sum(v.signal=="BUY" for v in votes)
    sell_count=sum(v.signal=="SELL" for v in votes)
    wait_count=sum(v.signal=="WAIT" for v in votes)
    vetoes=sum(v.signal=="VETO" for v in votes)

    direction="BUY" if buy_score>sell_score else "SELL" if sell_score>buy_score else "WAIT"
    score=max(buy_score,sell_score)
    margin=abs(buy_score-sell_score)
    core_count=sum(v.name in CORE and v.signal==direction for v in votes) if direction!="WAIT" else 0
    consensus=score/TOTAL_DIRECTIONAL_WEIGHT

    min_score=BASE_MIN_SCORE
    min_margin=BASE_MIN_MARGIN
    min_core=BASE_MIN_CORE_COUNT
    if regime in {"VOLATILE","UNCLEAR"}:
        min_score=7.0;min_margin=4.0;min_core=3

    action=direction
    reason=""
    if vetoes:
        action="WAIT";reason="execution-quality veto"
    elif regime=="RANGE":
        action="WAIT";reason="range regime: directional entry suppressed"
    elif direction=="WAIT":
        action="WAIT";reason="weighted score tie"
    elif regime=="BULL_TREND" and direction!="BUY":
        action="WAIT";reason="direction conflicts with bull regime"
    elif regime=="BEAR_TREND" and direction!="SELL":
        action="WAIT";reason="direction conflicts with bear regime"
    elif score<min_score:
        action="WAIT";reason=f"weighted score {score:.2f}<{min_score:.2f}"
    elif margin<min_margin:
        action="WAIT";reason=f"weighted margin {margin:.2f}<{min_margin:.2f}"
    elif core_count<min_core:
        action="WAIT";reason=f"core support {core_count}<{min_core}"
    else:
        reason=(f"weighted {direction}: score={score:.2f}, margin={margin:.2f}, "
                f"core={core_count}, regime={regime}")

    rr=0.0 if action=="WAIT" else _suggest_rr(regime,score,margin)
    return WeightedDecision(
        action,regime,round(buy_score,4),round(sell_score,4),round(score,4),
        round(margin,4),core_count,buy_count,sell_count,wait_count,vetoes,
        round(consensus,6),rr,_ai_threshold(regime),reason,votes
    )


def evaluate(payload)->WeightedDecision:
    regime=detect_regime(payload)
    return aggregate_votes(evaluate_votes(payload),regime)
