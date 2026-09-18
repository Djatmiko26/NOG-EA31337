"""Read-only first-hit outcome audit for Cash Pilot V2 directional previews.

Uses stored quote/ATR and current XAUUSD contract specs to reconstruct the
hypothetical cash plan, then checks post-signal BID history for TP-vs-SL order.
No OpenAI, no ledger writes, no broker order functions.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3

import cash_risk as risk

ROOT=Path(__file__).resolve().parent
DB=ROOT/"data"/"cash_pilot_v2.sqlite3"


def stop(code):
    print(f"V2_AUDIT_STOP | {code} | NO_API | NO_ORDER")
    raise SystemExit(1)


def positive(value):
    return type(value) in (int,float) and math.isfinite(value) and value>0


def first_hit(bars,buy,sl,tp):
    for row in bars:
        low=float(row["low"]);high=float(row["high"]);t=int(row["time"])
        sl_hit=low<=sl if buy else high>=sl
        tp_hit=high>=tp if buy else low<=tp
        if sl_hit and tp_hit:return "AMBIGUOUS_SAME_M1",t
        if sl_hit:return "SL_FIRST",t
        if tp_hit:return "TP_FIRST",t
    return "UNRESOLVED",0


def tick_resolve(mt5,minute,buy,sl,tp):
    start=datetime.fromtimestamp(minute,tz=timezone.utc)
    end=datetime.fromtimestamp(minute+60,tz=timezone.utc)
    ticks=mt5.copy_ticks_range("XAUUSD",start,end,mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks)==0:return "AMBIGUOUS_NO_TICKS",0
    for row in ticks:
        bid=float(row["bid"])
        if not positive(bid):continue
        sl_hit=bid<=sl if buy else bid>=sl
        tp_hit=bid>=tp if buy else bid<=tp
        if sl_hit and tp_hit:return "AMBIGUOUS_SAME_TICK",int(row["time_msc"])
        if sl_hit:return "SL_FIRST",int(row["time_msc"])
        if tp_hit:return "TP_FIRST",int(row["time_msc"])
    return "AMBIGUOUS_LEVELS_NOT_IN_TICKS",0


def load_directional():
    if not DB.is_file():stop("V2_LEDGER_NOT_FOUND")
    db=sqlite3.connect(DB.resolve().as_uri()+"?mode=ro",uri=True)
    try:
        rows=list(db.execute(
            "SELECT bar,payload,result FROM attempts "
            "WHERE status='SUCCESS' ORDER BY bar"
        ))
    finally:db.close()
    out=[]
    for bar,payload_text,result_text in rows:
        try:
            payload=json.loads(payload_text)
            result=json.loads(result_text) if result_text else {}
        except (TypeError,json.JSONDecodeError):
            stop("INVALID_V2_LEDGER_JSON")
        action=result.get("signal",{}).get("action")
        if action in {"BUY","SELL"}:
            out.append((int(bar),payload,action))
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commission-per-lot",type=float,required=True)
    parser.add_argument("--fixed-fee",type=float,required=True)
    args=parser.parse_args()
    if any(not math.isfinite(x) or x<0 for x in (args.commission_per_lot,args.fixed_fee)):
        stop("INVALID_FEE_INPUT")

    attempts=load_directional()
    if not attempts:
        print("V2_AUDIT | DIRECTIONAL=0 | nothing to label | NO_API | NO_ORDER")
        return 0

    import MetaTrader5 as mt5
    if not mt5.initialize(r"C:\Program Files\MetaTrader 5\terminal64.exe"):
        stop("MT5_INITIALIZE_FAILED")
    try:
        account=mt5.account_info();info=mt5.symbol_info("XAUUSD")
        tick=mt5.symbol_info_tick("XAUUSD")
        if (account is None or info is None or tick is None
                or account.trade_mode!=mt5.ACCOUNT_TRADE_MODE_DEMO
                or account.currency!="USD" or info.currency_profit!="USD"):
            stop("XAUUSD_USD_DEMO_REQUIRED")
        if not mt5.symbol_select("XAUUSD",True):stop("XAUUSD_SELECT_FAILED")

        counts={"TP_FIRST":0,"SL_FIRST":0,"UNRESOLVED":0,"AMBIGUOUS":0}
        for bar,payload,action in attempts:
            quote=payload.get("current_quote",{})
            candles=payload.get("candles",[])
            if not candles:
                print(f"V2_OUTCOME | bar={bar} | action={action} | INVALID_SNAPSHOT")
                counts["AMBIGUOUS"]+=1;continue
            bid=quote.get("bid");ask=quote.get("ask");atr=candles[-1].get("atr14")
            if not all(positive(x) for x in (bid,ask,atr)) or ask<bid:
                print(f"V2_OUTCOME | bar={bar} | action={action} | INVALID_SNAPSHOT")
                counts["AMBIGUOUS"]+=1;continue
            try:
                plan=risk.broker_plan(
                    mt5,"XAUUSD",float(bid),float(ask),float(atr),action=="BUY",
                    args.commission_per_lot,args.fixed_fee
                )
            except ValueError as exc:
                print(f"V2_OUTCOME | bar={bar} | action={action} | PLAN_REJECT | {exc}")
                counts["AMBIGUOUS"]+=1;continue

            start_raw=bar+300
            end_raw=max(start_raw+60,int(tick.time)+60)
            bars=mt5.copy_rates_range(
                "XAUUSD",mt5.TIMEFRAME_M1,
                datetime.fromtimestamp(start_raw,tz=timezone.utc),
                datetime.fromtimestamp(end_raw,tz=timezone.utc)
            )
            if bars is None or len(bars)==0:
                print(f"V2_OUTCOME | bar={bar} | action={action} | HISTORY_UNAVAILABLE")
                counts["UNRESOLVED"]+=1;continue
            bars=[x for x in bars if int(x["time"])>=start_raw]
            status,when=first_hit(bars,action=="BUY",plan.sl,plan.tp)
            if status=="AMBIGUOUS_SAME_M1":
                status,when=tick_resolve(mt5,when,action=="BUY",plan.sl,plan.tp)

            bucket=status if status in counts else "AMBIGUOUS"
            counts[bucket]+=1
            print(
                f"V2_OUTCOME | bar={bar} | action={action} | {status} | "
                f"lot={plan.volume:.8f} | entry={plan.entry:.8f} | "
                f"SL={plan.sl:.8f} | TP={plan.tp:.8f} | hit_time_raw={when}"
            )

        print(
            f"V2_AUDIT_SUMMARY | DIRECTIONAL={len(attempts)} | "
            f"TP_FIRST={counts['TP_FIRST']} | SL_FIRST={counts['SL_FIRST']} | "
            f"UNRESOLVED={counts['UNRESOLVED']} | AMBIGUOUS={counts['AMBIGUOUS']} | "
            "HYPOTHETICAL_ONLY | NO_API | NO_ORDER"
        )
        print("V2_EVIDENCE_NOTE | first-hit counts do not by themselves prove profitability or live fill quality")
        return 0
    finally:
        mt5.shutdown()


if __name__=="__main__":
    raise SystemExit(main())
