"""Risk-Reward Lab for exact Cash Pilot V2 preview receipts.

Research-only:
- reads V2 ledger and exact EA preview receipts
- derives 10 reward targets while preserving the exact EA stop/risk
- uses executable historical ticks: BID for BUY exits, ASK for SELL exits
- no OpenAI calls, no ledger writes, no broker order functions

This lab evaluates hypothetical first-hit outcomes. It does not simulate fills,
slippage beyond the declared stress assumption, or guarantee live profitability.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3

import cash_risk as risk

ROOT=Path(__file__).resolve().parent
DB=ROOT/"data"/"cash_pilot_v2.sqlite3"
RATIOS=(0.50,0.75,1.00,1.25,1.50,1.75,2.00,2.50,3.00,4.00)
CHUNK_SECONDS=1800


def stop(code):
    print(f"RR_LAB_STOP | {code} | NO_API | NO_ORDER")
    raise SystemExit(1)


def hash32(text):
    h=2166136261
    for ch in text:
        h=((h ^ ord(ch))*16777619)&0xffffffff
    return h


def positive(value):
    return type(value) in (int,float) and math.isfinite(value) and value>0


def load_directional():
    if not DB.is_file():stop("V2_LEDGER_NOT_FOUND")
    db=sqlite3.connect(DB.resolve().as_uri()+"?mode=ro",uri=True)
    try:
        rows=list(db.execute(
            "SELECT bar,sid,result FROM attempts WHERE status='SUCCESS' ORDER BY bar"
        ))
    finally:db.close()
    out=[]
    for bar,sid,result_text in rows:
        try:result=json.loads(result_text) if result_text else {}
        except (TypeError,json.JSONDecodeError):stop("INVALID_V2_LEDGER_JSON")
        action=result.get("signal",{}).get("action")
        if action in {"BUY","SELL"}:
            out.append((int(bar),str(sid),action,
                        result.get("signal",{}).get("confidence"),
                        result.get("signal",{}).get("market_regime")))
    return out


def load_receipts(data_path,login):
    path=Path(data_path)/"MQL5"/"Files"/"NOG_CashDemo"/"preview_receipts_v1.journal"
    if not path.is_file():return {}
    out={}
    for raw in path.read_text(encoding="utf-8",errors="strict").splitlines():
        if not raw.strip():continue
        p=raw.split(";")
        if len(p)!=15 or p[0]!="P1":continue
        base=";".join(p[:14])
        if str(hash32(base))!=p[14]:continue
        try:
            row_login=int(p[1]);bar=int(p[4]);tick_msc=int(p[6])
            volume=float(p[7]);entry=float(p[8]);sl=float(p[9]);tp=float(p[10])
            loss=float(p[11]);profit=float(p[12]);fee=float(p[13])
        except ValueError:
            continue
        sid=p[3];action=p[5]
        if (row_login!=int(login) or action not in {"BUY","SELL"}
                or not re.fullmatch(r"[0-9a-f]{32}",sid)
                or not all(positive(x) for x in (volume,entry,sl,tp,loss,profit))
                or not math.isfinite(fee) or fee<0):
            continue
        out[sid]={"bar":bar,"action":action,"tick_msc":tick_msc,"volume":volume,
                  "entry":entry,"sl":sl,"tp_1r":tp,"loss":loss,"profit_1r":profit,
                  "fee":fee}
    return out


def align_target(raw,buy,tick):
    if buy:return math.ceil((raw-1e-12)/tick)*tick
    return math.floor((raw+1e-12)/tick)*tick


def target_for_ratio(mt5,receipt,ratio,point,tick_size):
    buy=receipt["action"]=="BUY"
    typ=mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL
    sign=1 if buy else -1
    stress=risk.STRESS_POINTS*point
    stressed_open=receipt["entry"]+sign*stress
    probe_close=stressed_open+sign*tick_size
    gain_tick=mt5.order_calc_profit(
        typ,"XAUUSD",receipt["volume"],stressed_open,probe_close
    )
    if gain_tick is None or not positive(float(gain_tick)):
        raise ValueError("TARGET_TICK_PROFIT_UNAVAILABLE")
    target_usd=receipt["loss"]*ratio
    ticks=max(1,math.ceil((target_usd+receipt["fee"])/float(gain_tick)-1e-12))
    stressed_close=stressed_open+sign*ticks*tick_size
    raw_tp=stressed_close+sign*stress
    tp=align_target(raw_tp,buy,tick_size)
    check=mt5.order_calc_profit(
        typ,"XAUUSD",receipt["volume"],stressed_open,tp-sign*stress
    )
    if check is None or not math.isfinite(check) or check-receipt["fee"]<target_usd-1e-7:
        raise ValueError("TARGET_VALIDATION_FAILED")
    return float(tp),float(target_usd),float(check-receipt["fee"])


def iter_ticks(mt5,start_ms,end_ms):
    cursor=start_ms
    while cursor<=end_ms:
        chunk_end=min(end_ms,cursor+CHUNK_SECONDS*1000-1)
        ticks=mt5.copy_ticks_range(
            "XAUUSD",
            datetime.fromtimestamp(cursor/1000,tz=timezone.utc),
            datetime.fromtimestamp(chunk_end/1000,tz=timezone.utc),
            mt5.COPY_TICKS_ALL
        )
        if ticks is not None:
            for row in ticks:
                ms=int(row["time_msc"])
                if start_ms<=ms<=end_ms:yield row
        cursor=chunk_end+1


def evaluate_receipt(mt5,receipt,targets,end_ms):
    buy=receipt["action"]=="BUY"
    start_ms=max(receipt["bar"]*1000+300000,receipt["tick_msc"])
    states={ratio:{"status":"UNRESOLVED","hit_ms":0} for ratio in targets}
    unresolved=set(targets)
    seen=0
    for row in iter_ticks(mt5,start_ms,end_ms):
        bid=float(row["bid"]);ask=float(row["ask"])
        price=bid if buy else ask
        if not positive(price):continue
        seen+=1
        ms=int(row["time_msc"])
        stop_hit=price<=receipt["sl"] if buy else price>=receipt["sl"]
        for ratio in tuple(unresolved):
            tp=targets[ratio][0]
            target_hit=price>=tp if buy else price<=tp
            if target_hit:
                states[ratio]={"status":"TP_FIRST","hit_ms":ms}
                unresolved.remove(ratio)
            elif stop_hit:
                states[ratio]={"status":"SL_FIRST","hit_ms":ms}
                unresolved.remove(ratio)
        if not unresolved:break
    return states,seen,start_ms


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commission-per-lot",type=float,required=True)
    parser.add_argument("--fixed-fee",type=float,required=True)
    args=parser.parse_args()
    if any(not math.isfinite(x) or x<0 for x in (args.commission_per_lot,args.fixed_fee)):
        stop("INVALID_FEE_INPUT")

    directional=load_directional()
    if not directional:
        print("RR_LAB | DIRECTIONAL=0 | NO_API | NO_ORDER");return 0

    import MetaTrader5 as mt5
    if not mt5.initialize(r"C:\Program Files\MetaTrader 5\terminal64.exe"):
        stop("MT5_INITIALIZE_FAILED")
    try:
        account=mt5.account_info();info=mt5.symbol_info("XAUUSD")
        terminal=mt5.terminal_info();now_tick=mt5.symbol_info_tick("XAUUSD")
        if (account is None or info is None or terminal is None or now_tick is None
                or account.trade_mode!=mt5.ACCOUNT_TRADE_MODE_DEMO
                or account.currency!="USD" or info.currency_profit!="USD"):
            stop("XAUUSD_USD_DEMO_REQUIRED")
        if not mt5.symbol_select("XAUUSD",True):stop("XAUUSD_SELECT_FAILED")
        point=float(info.point);tick_size=float(info.trade_tick_size)
        if not positive(point) or not positive(tick_size):stop("INVALID_BROKER_PRICE_STEP")

        receipts=load_receipts(terminal.data_path,account.login)
        matched=[]
        skipped=0
        for bar,sid,action,confidence,regime in directional:
            r=receipts.get(sid)
            if r is None or r["bar"]!=bar or r["action"]!=action:
                skipped+=1;continue
            expected_fee=args.commission_per_lot*r["volume"]+args.fixed_fee
            if abs(r["fee"]-expected_fee)>1e-8:
                print(
                    f"RR_SIGNAL | bar={bar} | action={action} | "
                    f"FEE_MISMATCH receipt={r['fee']:.8f} expected={expected_fee:.8f}"
                )
                skipped+=1;continue
            matched.append((sid,r,confidence,regime))

        if not matched:
            print(f"RR_LAB | EXACT_RECEIPTS=0 | SKIPPED_OLD={skipped} | NO_API | NO_ORDER")
            return 0

        aggregate={ratio:{"tp":0,"sl":0,"unresolved":0,"durations":[]} for ratio in RATIOS}
        end_ms=int(now_tick.time_msc)

        for sid,r,confidence,regime in matched:
            targets={}
            try:
                for ratio in RATIOS:
                    targets[ratio]=target_for_ratio(mt5,r,ratio,point,tick_size)
            except ValueError as exc:
                print(f"RR_SIGNAL | bar={r['bar']} | action={r['action']} | PLAN_REJECT | {exc}")
                continue

            # 1R target should match the exact EA receipt within one tick.
            one_r_delta=abs(targets[1.0][0]-r["tp_1r"])
            if one_r_delta>tick_size+1e-9:
                print(f"RR_SIGNAL | bar={r['bar']} | action={r['action']} | RECEIPT_1R_MISMATCH={one_r_delta:.8f}")
                continue

            states,tick_count,start_ms=evaluate_receipt(mt5,r,targets,end_ms)
            print(
                f"RR_SIGNAL | bar={r['bar']} | action={r['action']} | "
                f"confidence={confidence} | regime={regime} | entry={r['entry']:.8f} | "
                f"SL={r['sl']:.8f} | risk_usd={r['loss']:.8f} | ticks={tick_count}"
            )
            for ratio in RATIOS:
                tp,target_usd,validated=targets[ratio]
                state=states[ratio]
                if state["status"]=="TP_FIRST":aggregate[ratio]["tp"]+=1
                elif state["status"]=="SL_FIRST":aggregate[ratio]["sl"]+=1
                else:aggregate[ratio]["unresolved"]+=1
                if state["hit_ms"]:
                    aggregate[ratio]["durations"].append((state["hit_ms"]-start_ms)/1000)
                print(
                    f"RR | {ratio:.2f}R | TP={tp:.8f} | target_usd={target_usd:.4f} | "
                    f"{state['status']} | hit_ms={state['hit_ms']}"
                )

        print(f"RR_SUMMARY | EXACT_SIGNALS={len(matched)} | SKIPPED_OLD={skipped}")
        for ratio in RATIOS:
            a=aggregate[ratio];resolved=a["tp"]+a["sl"]
            win_rate=(100*a["tp"]/resolved) if resolved else float("nan")
            expectancy=((a["tp"]*ratio-a["sl"])/resolved) if resolved else float("nan")
            avg_seconds=(sum(a["durations"])/len(a["durations"])) if a["durations"] else float("nan")
            wr="NA" if not math.isfinite(win_rate) else f"{win_rate:.2f}%"
            ex="NA" if not math.isfinite(expectancy) else f"{expectancy:.4f}R"
            dur="NA" if not math.isfinite(avg_seconds) else f"{avg_seconds:.1f}s"
            print(
                f"RR_METRIC | {ratio:.2f}R | TP_FIRST={a['tp']} | SL_FIRST={a['sl']} | "
                f"UNRESOLVED={a['unresolved']} | win_rate={wr} | expectancy={ex} | "
                f"avg_resolution={dur}"
            )
        print("RR_NOTE | research-only; small samples and hypothetical first-hit outcomes do not prove live profitability")
        print("RR_DONE | NO_API | NO_ORDER | NO_LEDGER_WRITE")
        return 0
    finally:
        mt5.shutdown()


if __name__=="__main__":
    raise SystemExit(main())
