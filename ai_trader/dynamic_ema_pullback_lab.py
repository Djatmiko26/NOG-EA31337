"""Historical lab for Dynamic EMA Pullback Strategy on XAUUSD M5.

Research-only:
- reads CLOSED M5 history from MT5
- generates signals from EMA50/EMA200 + Candle[2]/Candle[1] rules
- evaluates ten reward ratios against the same initial stop
- approximates executable price sides from BID OHLC + historical bar spread
- reports both all-signal results and results respecting max active positions
- no OpenAI, no broker orders, no ledger writes by default

Important: MT5 bar spread is an approximation for historical ASK. Same-bar SL/TP
hits are marked AMBIGUOUS because M5 OHLC cannot prove intrabar ordering.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
from statistics import mean
from typing import Iterable

import dynamic_ema_pullback as dyn

MT5_PATH=r"C:\Program Files\MetaTrader 5\terminal64.exe"
SYMBOL="XAUUSD"
RATIOS=(0.50,0.75,1.00,1.25,1.50,1.75,2.00,2.50,3.00,4.00)
WARMUP_EXTRA=30


@dataclass(frozen=True)
class HistBar:
    time:int
    open:float
    high:float
    low:float
    close:float
    spread_points:int


@dataclass(frozen=True)
class Signal:
    index:int
    time:int
    action:str
    entry:float
    sl:float
    risk_distance:float
    trigger_close:float
    trigger_spread_price:float
    body:float
    wick_ratio:float
    ema50:float
    ema200:float


@dataclass(frozen=True)
class Outcome:
    status:str
    resolve_index:int
    resolve_bars:int


def _positive(value)->bool:
    return type(value) in (int,float) and math.isfinite(value) and value>0


def _validate_bars(bars:list[HistBar])->None:
    if len(bars)<250:raise ValueError("DYN_LAB_NEEDS_AT_LEAST_250_BARS")
    previous=0
    for b in bars:
        if (type(b.time) is not int or b.time<=previous
                or not all(_positive(x) for x in (b.open,b.high,b.low,b.close))
                or b.high<max(b.open,b.low,b.close)
                or b.low>min(b.open,b.high,b.close)
                or type(b.spread_points) is not int or b.spread_points<0):
            raise ValueError("DYN_LAB_INVALID_HISTORY")
        previous=b.time


def generate_signals(bars:list[HistBar],point:float,cfg:dyn.Config)->list[Signal]:
    cfg.validate();_validate_bars(bars)
    if not _positive(point):raise ValueError("DYN_LAB_INVALID_POINT")

    closes=[b.close for b in bars]
    ema50=dyn.ema(closes,cfg.fast_ema)
    ema200=dyn.ema(closes,cfg.slow_ema)
    start=max(cfg.slow_ema+WARMUP_EXTRA,2)
    signals=[]

    for i in range(start,len(bars)-1):
        trigger=bars[i]
        pullback=bars[i-1]
        fast=ema50[i];slow=ema200[i]
        body=abs(trigger.close-trigger.open)
        rng=trigger.high-trigger.low
        if rng<=0:continue

        bullish=trigger.close>trigger.open
        bearish=trigger.close<trigger.open
        upper=(trigger.high-max(trigger.open,trigger.close))/rng
        lower=(min(trigger.open,trigger.close)-trigger.low)/rng
        spread_price=trigger.spread_points*point

        if fast>slow:
            pullback_ok=(pullback.close<ema50[i-1] or pullback.close<ema200[i-1])
            if (pullback_ok and bullish and body>=cfg.min_body_price
                    and upper<=cfg.max_opposite_wick_ratio):
                # BUY market order executes on ASK. Bar OHLC is BID on this symbol.
                entry=trigger.close+spread_price
                sl=trigger.low-cfg.sl_buffer_price
                risk=entry-sl
                if risk>0:
                    signals.append(Signal(i,trigger.time,"BUY",entry,sl,risk,
                                          trigger.close,spread_price,body,upper,fast,slow))
        elif fast<slow:
            pullback_ok=(pullback.close>ema50[i-1] or pullback.close>ema200[i-1])
            if (pullback_ok and bearish and body>=cfg.min_body_price
                    and lower<=cfg.max_opposite_wick_ratio):
                # SELL market order executes on BID.
                entry=trigger.close
                sl=trigger.high+cfg.sl_buffer_price
                risk=sl-entry
                if risk>0:
                    signals.append(Signal(i,trigger.time,"SELL",entry,sl,risk,
                                          trigger.close,spread_price,body,lower,fast,slow))
    return signals


def tp_for(signal:Signal,ratio:float)->float:
    if ratio<=0 or not math.isfinite(ratio):raise ValueError("DYN_LAB_INVALID_RR")
    return (signal.entry+signal.risk_distance*ratio if signal.action=="BUY"
            else signal.entry-signal.risk_distance*ratio)


def bar_sides(bar:HistBar,point:float)->tuple[float,float,float,float]:
    """Return BID low/high and approximate ASK low/high."""
    spread=bar.spread_points*point
    return bar.low,bar.high,bar.low+spread,bar.high+spread


def evaluate_outcome(signal:Signal,bars:list[HistBar],point:float,ratio:float,
                     max_hold_bars:int=0)->Outcome:
    tp=tp_for(signal,ratio)
    end=len(bars)
    if max_hold_bars>0:end=min(end,signal.index+1+max_hold_bars)

    for j in range(signal.index+1,end):
        bid_low,bid_high,ask_low,ask_high=bar_sides(bars[j],point)
        if signal.action=="BUY":
            sl_hit=bid_low<=signal.sl
            tp_hit=bid_high>=tp
        else:
            sl_hit=ask_high>=signal.sl
            tp_hit=ask_low<=tp

        if sl_hit and tp_hit:return Outcome("AMBIGUOUS",j,j-signal.index)
        if tp_hit:return Outcome("TP_FIRST",j,j-signal.index)
        if sl_hit:return Outcome("SL_FIRST",j,j-signal.index)

    return Outcome("UNRESOLVED",-1,max(0,end-1-signal.index))


def max_losing_streak(statuses:Iterable[str])->int:
    best=current=0
    for status in statuses:
        if status=="SL_FIRST":
            current+=1;best=max(best,current)
        elif status=="TP_FIRST":
            current=0
    return best


def metrics(signals:list[Signal],outcomes:list[Outcome],ratio:float)->dict:
    tp=sum(o.status=="TP_FIRST" for o in outcomes)
    sl=sum(o.status=="SL_FIRST" for o in outcomes)
    amb=sum(o.status=="AMBIGUOUS" for o in outcomes)
    unr=sum(o.status=="UNRESOLVED" for o in outcomes)
    resolved=tp+sl
    win=(tp/resolved if resolved else math.nan)
    expectancy=(win*ratio-(1-win)) if resolved else math.nan
    resolution=[o.resolve_bars for o in outcomes if o.status in {"TP_FIRST","SL_FIRST"}]
    statuses=[o.status for o in outcomes if o.status in {"TP_FIRST","SL_FIRST"}]
    return {
        "signals":len(signals),"tp":tp,"sl":sl,"amb":amb,"unr":unr,
        "resolved":resolved,"win":win,"expectancy":expectancy,
        "avg_bars":mean(resolution) if resolution else math.nan,
        "max_losing_streak":max_losing_streak(statuses),
    }


def apply_position_cap(signals:list[Signal],outcomes:list[Outcome],max_positions:int):
    if max_positions<1:raise ValueError("DYN_LAB_INVALID_MAX_POSITIONS")
    accepted_s=[];accepted_o=[];active_resolve=[]

    for signal,outcome in zip(signals,outcomes):
        active_resolve=[x for x in active_resolve if x>signal.index]
        if len(active_resolve)>=max_positions:continue
        accepted_s.append(signal);accepted_o.append(outcome)
        # Unresolved/ambiguous remains active to the end of the available sample.
        resolve=(outcome.resolve_index if outcome.resolve_index>=0 else 10**18)
        active_resolve.append(resolve)
    return accepted_s,accepted_o


def fmt_pct(value):
    return "NA" if not math.isfinite(value) else f"{value*100:.2f}%"


def fmt_r(value):
    return "NA" if not math.isfinite(value) else f"{value:.4f}R"


def fmt_bars(value):
    return "NA" if not math.isfinite(value) else f"{value:.1f}"


def load_mt5_history(count:int,start_pos:int=1):
    import MetaTrader5 as mt5
    if not mt5.initialize(MT5_PATH):
        raise RuntimeError("MT5_INITIALIZE_FAILED")
    try:
        account=mt5.account_info();info=mt5.symbol_info(SYMBOL)
        if (account is None or info is None
                or account.trade_mode!=mt5.ACCOUNT_TRADE_MODE_DEMO
                or account.currency!="USD" or info.currency_profit!="USD"):
            raise RuntimeError("XAUUSD_USD_DEMO_REQUIRED")
        if not mt5.symbol_select(SYMBOL,True):raise RuntimeError("XAUUSD_SELECT_FAILED")
        if type(start_pos) is not int or start_pos<1:
            raise RuntimeError("INVALID_START_POS")
        rates=mt5.copy_rates_from_pos(SYMBOL,mt5.TIMEFRAME_M5,start_pos,count)
        if rates is None or len(rates)<min(count,500):
            raise RuntimeError("M5_HISTORY_UNAVAILABLE")
        raw=sorted(rates,key=lambda r:int(r["time"]))
        bars=[HistBar(int(r["time"]),float(r["open"]),float(r["high"]),
                      float(r["low"]),float(r["close"]),int(r["spread"])) for r in raw]
        return bars,float(info.point)
    finally:
        mt5.shutdown()


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bars",type=int,default=20000)
    p.add_argument("--start-pos",type=int,default=1,
                   help="MT5 closed-bar offset: 1=latest closed window; 20001=older non-overlapping 20k window")
    p.add_argument("--pip-price",type=float,default=0.1)
    p.add_argument("--min-body-pips",type=float,default=60.0)
    p.add_argument("--max-wick-ratio",type=float,default=0.20)
    p.add_argument("--sl-buffer-pips",type=float,default=10.0)
    p.add_argument("--max-active-positions",type=int,default=3)
    p.add_argument("--max-hold-bars",type=int,default=0,
                   help="0 = no strategy-imposed time stop; otherwise M5 bars")
    args=p.parse_args()

    if args.bars<500 or args.bars>300000:
        raise SystemExit("--bars must be between 500 and 300000")
    if args.start_pos<1:
        raise SystemExit("--start-pos must be >= 1")
    if args.max_hold_bars<0:raise SystemExit("--max-hold-bars must be >= 0")

    cfg=dyn.Config(
        pip_price=args.pip_price,
        min_body_pips=args.min_body_pips,
        max_opposite_wick_ratio=args.max_wick_ratio,
        sl_buffer_pips=args.sl_buffer_pips,
        max_active_positions=args.max_active_positions,
    )

    try:
        bars,point=load_mt5_history(args.bars,args.start_pos)
        signals=generate_signals(bars,point,cfg)
    except (ValueError,RuntimeError) as exc:
        print(f"DYN_LAB_STOP | {exc} | NO_API | NO_ORDER")
        return 1

    buys=sum(s.action=="BUY" for s in signals)
    sells=sum(s.action=="SELL" for s in signals)
    print(
        f"DYN_LAB_READY | bars={len(bars)} | start_pos={args.start_pos} | "
        f"from_raw={bars[0].time} | to_raw={bars[-1].time} | "
        f"signals={len(signals)} | BUY={buys} | SELL={sells} | point={point:.8f} | "
        f"pip_price={cfg.pip_price:.4f} | max_positions={cfg.max_active_positions} | "
        f"max_hold_bars={args.max_hold_bars} | NO_API | NO_ORDER"
    )
    print(
        "DYN_LAB_ASSUMPTION | BUY entry uses trigger BID close + bar spread; "
        "SELL entry uses trigger BID close; SELL exits use BID+bar-spread ASK approximation"
    )

    for ratio in RATIOS:
        outcomes=[evaluate_outcome(s,bars,point,ratio,args.max_hold_bars) for s in signals]
        allm=metrics(signals,outcomes,ratio)
        capped_s,capped_o=apply_position_cap(signals,outcomes,cfg.max_active_positions)
        capm=metrics(capped_s,capped_o,ratio)

        print(
            f"DYN_RR | {ratio:.2f}R | ALL | signals={allm['signals']} | "
            f"TP_FIRST={allm['tp']} | SL_FIRST={allm['sl']} | "
            f"AMBIGUOUS={allm['amb']} | UNRESOLVED={allm['unr']} | "
            f"win_rate={fmt_pct(allm['win'])} | expectancy={fmt_r(allm['expectancy'])} | "
            f"avg_resolution_bars={fmt_bars(allm['avg_bars'])} | "
            f"max_losing_streak={allm['max_losing_streak']}"
        )
        print(
            f"DYN_RR | {ratio:.2f}R | CAP{cfg.max_active_positions} | "
            f"accepted={capm['signals']} | TP_FIRST={capm['tp']} | SL_FIRST={capm['sl']} | "
            f"AMBIGUOUS={capm['amb']} | UNRESOLVED={capm['unr']} | "
            f"win_rate={fmt_pct(capm['win'])} | expectancy={fmt_r(capm['expectancy'])} | "
            f"avg_resolution_bars={fmt_bars(capm['avg_bars'])} | "
            f"max_losing_streak={capm['max_losing_streak']}"
        )

    print(
        "DYN_LAB_NOTE | first-hit bar outcomes are hypothetical; same-bar SL+TP is ambiguous; "
        "historical spread is an approximation; BEP/trailing not included in these RR metrics"
    )
    print("DYN_LAB_DONE | NO_API | NO_ORDER | NO_LEDGER_WRITE")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
