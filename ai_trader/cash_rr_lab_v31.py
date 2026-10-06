"""Risk-Reward Lab for confirmed Cash Ensemble V3.1 weighted previews.

Reads V3.1 evidence plus exact EA preview receipts and evaluates the same ten
reward ratios used by the existing RR lab. Research-only: no API, no orders,
no ledger writes.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sqlite3

import cash_rr_lab as rr

ROOT=Path(__file__).resolve().parent
DB=ROOT/"data"/"cash_ensemble_v31.sqlite3"


def stop(code):
    print(f"V31_RR_STOP | {code} | NO_API | NO_ORDER")
    raise SystemExit(1)


def load_confirmed():
    if not DB.is_file():stop("V31_LEDGER_NOT_FOUND")
    db=sqlite3.connect(DB.resolve().as_uri()+"?mode=ro",uri=True)
    try:
        rows=list(db.execute(
            "SELECT bar,sid,local_json,result FROM events "
            "WHERE status='SUCCESS' ORDER BY bar"
        ))
    finally:db.close()
    out=[]
    for bar,sid,local_text,result_text in rows:
        try:
            local=json.loads(local_text)
            result=json.loads(result_text) if result_text else {}
        except (TypeError,json.JSONDecodeError):
            stop("INVALID_V31_LEDGER_JSON")
        final=result.get("final_action")
        if final in {"BUY","SELL"} and result.get("delivery")=="READY_FOR_EA":
            ai=result.get("ai_signal") or {}
            out.append({
                "bar":int(bar),"sid":str(sid),"action":final,
                "suggested_rr":float(local.get("suggested_rr",0.0)),
                "buy_score":float(local.get("buy_score",0.0)),
                "sell_score":float(local.get("sell_score",0.0)),
                "local_regime":local.get("regime"),
                "ai_confidence":ai.get("confidence"),
                "ai_regime":ai.get("market_regime"),
            })
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commission-per-lot",type=float,required=True)
    parser.add_argument("--fixed-fee",type=float,required=True)
    args=parser.parse_args()
    if any(not math.isfinite(x) or x<0 for x in (args.commission_per_lot,args.fixed_fee)):
        stop("INVALID_FEE_INPUT")

    confirmed=load_confirmed()
    if not confirmed:
        print("V31_RR | CONFIRMED_DIRECTIONAL=0 | NO_API | NO_ORDER");return 0

    import MetaTrader5 as mt5
    if not mt5.initialize(r"C:\Program Files\MetaTrader 5\terminal64.exe"):
        stop("MT5_INITIALIZE_FAILED")
    try:
        account=mt5.account_info();info=mt5.symbol_info("XAUUSD")
        terminal=mt5.terminal_info();tick=mt5.symbol_info_tick("XAUUSD")
        if (account is None or info is None or terminal is None or tick is None
                or account.trade_mode!=mt5.ACCOUNT_TRADE_MODE_DEMO
                or account.currency!="USD" or info.currency_profit!="USD"):
            stop("XAUUSD_USD_DEMO_REQUIRED")
        if not mt5.symbol_select("XAUUSD",True):stop("XAUUSD_SELECT_FAILED")
        point=float(info.point);tick_size=float(info.trade_tick_size)
        receipts=rr.load_receipts(terminal.data_path,account.login)
        end_ms=int(tick.time_msc)

        agg={ratio:{"tp":0,"sl":0,"unresolved":0} for ratio in rr.RATIOS}
        suggested={"tp":0,"sl":0,"unresolved":0,"count":0}
        exact=skipped=0

        for item in confirmed:
            receipt=receipts.get(item["sid"])
            if (receipt is None or receipt["bar"]!=item["bar"]
                    or receipt["action"]!=item["action"]):
                skipped+=1;continue
            expected_fee=args.commission_per_lot*receipt["volume"]+args.fixed_fee
            if abs(receipt["fee"]-expected_fee)>1e-8:
                skipped+=1;continue

            targets={}
            try:
                for ratio in rr.RATIOS:
                    targets[ratio]=rr.target_for_ratio(mt5,receipt,ratio,point,tick_size)
            except ValueError as exc:
                print(f"V31_RR_SIGNAL | bar={item['bar']} | PLAN_REJECT | {exc}")
                skipped+=1;continue

            states,tick_count,_=rr.evaluate_receipt(mt5,receipt,targets,end_ms)
            exact+=1
            print(
                f"V31_RR_SIGNAL | bar={item['bar']} | action={item['action']} | "
                f"weighted={item['buy_score']:.2f}B/{item['sell_score']:.2f}S | "
                f"local_regime={item['local_regime']} | ai_conf={item['ai_confidence']} | "
                f"ai_regime={item['ai_regime']} | suggested_rr={item['suggested_rr']:.2f}R | "
                f"ticks={tick_count}"
            )
            for ratio in rr.RATIOS:
                st=states[ratio]["status"]
                if st=="TP_FIRST":agg[ratio]["tp"]+=1
                elif st=="SL_FIRST":agg[ratio]["sl"]+=1
                else:agg[ratio]["unresolved"]+=1
                tp=targets[ratio][0]
                marker=" | SUGGESTED" if abs(ratio-item["suggested_rr"])<1e-9 else ""
                print(f"V31_RR | {ratio:.2f}R | TP={tp:.8f} | {st}{marker}")

            sr=item["suggested_rr"]
            if sr in states and sr>0:
                suggested["count"]+=1
                st=states[sr]["status"]
                if st=="TP_FIRST":suggested["tp"]+=1
                elif st=="SL_FIRST":suggested["sl"]+=1
                else:suggested["unresolved"]+=1

        print(f"V31_RR_SUMMARY | EXACT={exact} | SKIPPED={skipped}")
        for ratio in rr.RATIOS:
            a=agg[ratio];resolved=a["tp"]+a["sl"]
            wr=(100*a["tp"]/resolved) if resolved else float("nan")
            expectancy=((a["tp"]*ratio-a["sl"])/resolved) if resolved else float("nan")
            wr_text="NA" if not math.isfinite(wr) else f"{wr:.2f}%"
            ex_text="NA" if not math.isfinite(expectancy) else f"{expectancy:.4f}R"
            print(
                f"V31_RR_METRIC | {ratio:.2f}R | TP_FIRST={a['tp']} | "
                f"SL_FIRST={a['sl']} | UNRESOLVED={a['unresolved']} | "
                f"win_rate={wr_text} | expectancy={ex_text}"
            )
        print(
            f"V31_SUGGESTED_RR | COUNT={suggested['count']} | TP_FIRST={suggested['tp']} | "
            f"SL_FIRST={suggested['sl']} | UNRESOLVED={suggested['unresolved']}"
        )
        print("V31_RR_NOTE | research-only; small samples do not prove live profitability")
        print("V31_RR_DONE | NO_API | NO_ORDER | NO_LEDGER_WRITE")
        return 0
    finally:
        mt5.shutdown()


if __name__=="__main__":
    raise SystemExit(main())
