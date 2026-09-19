"""Read-only first-hit outcome audit for the stored directional CashDemo preview.

Reconstructs the stored PREVIEW plan, then checks subsequent XAUUSD BID history
to determine whether TP or SL was touched first. This is a hypothetical audit:
the stored preview was not necessarily filled. No OpenAI and no order functions.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3

import cash_risk as risk

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "cash_demo.sqlite3"


def die(code: str) -> None:
    print(f"OUTCOME_STOP | {code} | NO_API | NO_ORDER")
    raise SystemExit(1)


def read_directional_preview():
    if not DB.is_file():
        die("LEDGER_NOT_FOUND")
    db=sqlite3.connect(DB.resolve().as_uri()+"?mode=ro",uri=True)
    try:
        rows=db.execute(
            "SELECT bar,payload,result FROM attempts "
            "WHERE mode='PREVIEW' AND status='SUCCESS' ORDER BY bar"
        ).fetchall()
    finally:
        db.close()
    found=[]
    for bar,payload_text,result_text in rows:
        try:
            payload=json.loads(payload_text);result=json.loads(result_text)
        except (TypeError,json.JSONDecodeError):
            die("INVALID_LEDGER_JSON")
        action=result.get("signal",{}).get("action")
        if action in {"BUY","SELL"}:
            found.append((int(bar),payload,result))
    if not found:die("NO_DIRECTIONAL_PREVIEW")
    if len(found)!=1:die("MULTIPLE_DIRECTIONAL_PREVIEWS_REVIEW_REQUIRED")
    return found[0]


def pos(value,code="INVALID_MARKET_VALUE"):
    if type(value) not in (int,float) or not math.isfinite(value) or value<=0:die(code)
    return float(value)


def first_hit_from_bars(bars,buy:bool,sl:float,tp:float):
    """Return (status, bar_time) from chronological BID OHLC bars."""
    for row in bars:
        low=float(row["low"]);high=float(row["high"]);t=int(row["time"])
        sl_hit=low<=sl if buy else high>=sl
        tp_hit=high>=tp if buy else low<=tp
        if sl_hit and tp_hit:return "AMBIGUOUS_SAME_M1",t
        if sl_hit:return "SL_FIRST",t
        if tp_hit:return "TP_FIRST",t
    return "UNRESOLVED",None


def resolve_same_minute_with_ticks(mt5,symbol,minute,buy,sl,tp):
    start=datetime.fromtimestamp(minute,tz=timezone.utc)
    end=datetime.fromtimestamp(minute+60,tz=timezone.utc)
    ticks=mt5.copy_ticks_range(symbol,start,end,mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks)==0:return "AMBIGUOUS_SAME_M1_NO_TICKS",0
    for tick in ticks:
        bid=float(tick["bid"])
        if not math.isfinite(bid) or bid<=0:continue
        sl_hit=bid<=sl if buy else bid>=sl
        tp_hit=bid>=tp if buy else bid<=tp
        if sl_hit and tp_hit:return "AMBIGUOUS_SAME_TICK",int(tick["time_msc"])
        if sl_hit:return "SL_FIRST",int(tick["time_msc"])
        if tp_hit:return "TP_FIRST",int(tick["time_msc"])
    return "AMBIGUOUS_LEVELS_NOT_IN_TICKS",0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commission-per-lot",type=float,required=True)
    parser.add_argument("--fixed-fee",type=float,required=True)
    args=parser.parse_args()
    if any(not math.isfinite(v) or v<0 for v in (args.commission_per_lot,args.fixed_fee)):
        die("INVALID_FEE_INPUT")

    bar,payload,result=read_directional_preview()
    action=result["signal"]["action"];buy=action=="BUY"
    quote=payload.get("current_quote");candles=payload.get("candles")
    if not isinstance(quote,dict) or not isinstance(candles,list) or not candles:
        die("MISSING_STORED_SNAPSHOT")
    bid=pos(quote.get("bid"));ask=pos(quote.get("ask"));atr=pos(candles[-1].get("atr14"))
    if ask<bid:die("INVALID_STORED_SPREAD")

    import MetaTrader5 as mt5
    if not mt5.initialize(r"C:\Program Files\MetaTrader 5\terminal64.exe"):
        die("MT5_INITIALIZE_FAILED")
    try:
        account=mt5.account_info();info=mt5.symbol_info("XAUUSD");tick=mt5.symbol_info_tick("XAUUSD")
        if (account is None or info is None or tick is None
                or account.trade_mode!=mt5.ACCOUNT_TRADE_MODE_DEMO
                or account.currency!="USD" or info.currency_profit!="USD"):
            die("XAUUSD_USD_DEMO_REQUIRED")
        if not mt5.symbol_select("XAUUSD",True):die("XAUUSD_SELECT_FAILED")

        plan=risk.broker_plan(mt5,"XAUUSD",bid,ask,atr,buy,
                              args.commission_per_lot,args.fixed_fee)

        # bar is the open timestamp of the CLOSED M5 candle used by the model.
        # A hypothetical fill could only occur after that candle closed.
        start_raw=bar+300
        end_raw=max(start_raw+60,int(tick.time)+60)
        start=datetime.fromtimestamp(start_raw,tz=timezone.utc)
        end=datetime.fromtimestamp(end_raw,tz=timezone.utc)
        bars=mt5.copy_rates_range("XAUUSD",mt5.TIMEFRAME_M1,start,end)
        if bars is None or len(bars)==0:die("M1_HISTORY_UNAVAILABLE")

        # Strictly exclude any malformed/older rows.
        bars=[row for row in bars if int(row["time"])>=start_raw]
        if not bars:die("NO_POST_SIGNAL_M1_BARS")

        status,hit_time=first_hit_from_bars(bars,buy,plan.sl,plan.tp)
        detail_time=hit_time or 0
        if status=="AMBIGUOUS_SAME_M1":
            status,detail_time=resolve_same_minute_with_ticks(
                mt5,"XAUUSD",hit_time,buy,plan.sl,plan.tp
            )

        highs=[float(x["high"]) for x in bars];lows=[float(x["low"]) for x in bars]
        favorable=(max(highs)-plan.entry if buy else plan.entry-min(lows))
        adverse=(plan.entry-min(lows) if buy else max(highs)-plan.entry)

        print(f"OUTCOME_SOURCE | bar={bar} | action={action} | HYPOTHETICAL_STORED_PREVIEW")
        print(f"PLAN | lot={plan.volume:.8f} | entry={plan.entry:.8f} | SL={plan.sl:.8f} | TP={plan.tp:.8f}")
        print(f"PLAN_CASH | stressed_loss_usd={plan.stressed_loss:.8f} | stressed_profit_usd={plan.stressed_profit:.8f}")
        print(f"HISTORY | m1_bars={len(bars)} | from_raw={start_raw} | to_raw={int(bars[-1]['time'])}")
        print(f"EXCURSION | favorable_price={favorable:.8f} | adverse_price={adverse:.8f}")
        print(f"OUTCOME | {status} | hit_time_raw={detail_time}")
        if status=="TP_FIRST":
            print("AUDIT_RESULT | hypothetical TP touched before SL | NO_API | NO_ORDER")
        elif status=="SL_FIRST":
            print("AUDIT_RESULT | hypothetical SL touched before TP | NO_API | NO_ORDER")
        elif status=="UNRESOLVED":
            print("AUDIT_RESULT | neither TP nor SL touched in available history | NO_API | NO_ORDER")
        else:
            print("AUDIT_RESULT | ordering cannot be proven from available history | NO_API | NO_ORDER")
        return 0
    finally:
        mt5.shutdown()


if __name__=="__main__":
    raise SystemExit(main())
