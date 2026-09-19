"""Read-only first-hit outcome audit for Cash Pilot V2 directional previews.

Uses the exact EA preview receipt when available. Older V2 samples without a
receipt fall back to stored quote/ATR reconstruction and are explicitly labeled.
Then checks post-signal BID history for TP-vs-SL order.
No OpenAI, no ledger writes, no broker order functions.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import re
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
            "SELECT bar,sid,payload,result FROM attempts "
            "WHERE status='SUCCESS' ORDER BY bar"
        ))
    finally:db.close()
    out=[]
    for bar,sid,payload_text,result_text in rows:
        try:
            payload=json.loads(payload_text)
            result=json.loads(result_text) if result_text else {}
        except (TypeError,json.JSONDecodeError):
            stop("INVALID_V2_LEDGER_JSON")
        action=result.get("signal",{}).get("action")
        if action in {"BUY","SELL"}:
            out.append((int(bar),str(sid),payload,action))
    return out


def hash32(text):
    h=2166136261
    for ch in text:
        h=((h ^ ord(ch))*16777619) & 0xffffffff
    return h


def load_receipts(data_path,login):
    path=Path(data_path)/"MQL5"/"Files"/"NOG_CashDemo"/"preview_receipts_v1.journal"
    if not path.is_file():return {}
    receipts={}
    for raw in path.read_text(encoding="utf-8",errors="strict").splitlines():
        if not raw.strip():continue
        parts=raw.split(";")
        if len(parts)!=15 or parts[0]!="P1":continue
        base=";".join(parts[:14])
        if str(hash32(base))!=parts[14]:continue
        try:
            row_login=int(parts[1]);bar=int(parts[4]);tick_msc=int(parts[6])
            volume=float(parts[7]);entry=float(parts[8]);sl=float(parts[9]);tp=float(parts[10])
            loss=float(parts[11]);profit=float(parts[12]);fee=float(parts[13])
        except ValueError:
            continue
        sid=parts[3];action=parts[5]
        if (row_login!=int(login) or action not in {"BUY","SELL"} or
                not re.fullmatch(r"[0-9a-f]{32}",sid) or
                not all(math.isfinite(x) and x>0 for x in (volume,entry,sl,tp,loss,profit)) or
                not math.isfinite(fee) or fee<0):
            continue
        receipts[sid]={"bar":bar,"action":action,"tick_msc":tick_msc,"volume":volume,
                       "entry":entry,"sl":sl,"tp":tp,"loss":loss,"profit":profit,"fee":fee}
    return receipts


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
        terminal=mt5.terminal_info()
        receipts=load_receipts(terminal.data_path if terminal is not None else "",account.login)

        counts={"TP_FIRST":0,"SL_FIRST":0,"UNRESOLVED":0,"AMBIGUOUS":0}
        exact=0;fallback=0
        for bar,sid,payload,action in attempts:
            quote=payload.get("current_quote",{})
            candles=payload.get("candles",[])
            if not candles:
                print(f"V2_OUTCOME | bar={bar} | action={action} | INVALID_SNAPSHOT")
                counts["AMBIGUOUS"]+=1;continue
            bid=quote.get("bid");ask=quote.get("ask");atr=candles[-1].get("atr14")
            if not all(positive(x) for x in (bid,ask,atr)) or ask<bid:
                print(f"V2_OUTCOME | bar={bar} | action={action} | INVALID_SNAPSHOT")
                counts["AMBIGUOUS"]+=1;continue
            receipt=receipts.get(sid)
            if receipt is not None and receipt["bar"]==bar and receipt["action"]==action:
                volume=receipt["volume"];entry=receipt["entry"];sl=receipt["sl"];tp=receipt["tp"]
                planned_loss=receipt["loss"];planned_profit=receipt["profit"];plan_source="EA_EXACT_RECEIPT"
                exact+=1
            else:
                try:
                    plan=risk.broker_plan(
                        mt5,"XAUUSD",float(bid),float(ask),float(atr),action=="BUY",
                        args.commission_per_lot,args.fixed_fee
                    )
                except ValueError as exc:
                    print(f"V2_OUTCOME | bar={bar} | action={action} | PLAN_REJECT | {exc}")
                    counts["AMBIGUOUS"]+=1;continue
                volume=plan.volume;entry=plan.entry;sl=plan.sl;tp=plan.tp
                planned_loss=plan.stressed_loss;planned_profit=plan.stressed_profit
                plan_source="STORED_RECONSTRUCTED";fallback+=1

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
            status,when=first_hit(bars,action=="BUY",sl,tp)
            if status=="AMBIGUOUS_SAME_M1":
                status,when=tick_resolve(mt5,when,action=="BUY",sl,tp)

            bucket=status if status in counts else "AMBIGUOUS"
            counts[bucket]+=1
            print(
                f"V2_OUTCOME | bar={bar} | action={action} | {status} | "
                f"plan_source={plan_source} | lot={volume:.8f} | entry={entry:.8f} | "
                f"SL={sl:.8f} | TP={tp:.8f} | planned_loss={planned_loss:.8f} | "
                f"planned_profit={planned_profit:.8f} | hit_time_raw={when}"
            )

        print(
            f"V2_AUDIT_SUMMARY | DIRECTIONAL={len(attempts)} | "
            f"TP_FIRST={counts['TP_FIRST']} | SL_FIRST={counts['SL_FIRST']} | "
            f"UNRESOLVED={counts['UNRESOLVED']} | AMBIGUOUS={counts['AMBIGUOUS']} | "
            f"EXACT_RECEIPT={exact} | FALLBACK_RECONSTRUCTED={fallback} | "
            "HYPOTHETICAL_ONLY | NO_API | NO_ORDER"
        )
        print("V2_EVIDENCE_NOTE | first-hit counts do not by themselves prove profitability or live fill quality")
        return 0
    finally:
        mt5.shutdown()


if __name__=="__main__":
    raise SystemExit(main())
