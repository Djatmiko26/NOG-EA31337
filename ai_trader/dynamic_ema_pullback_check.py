"""Read-only MT5 check for Dynamic EMA Pullback Strategy.

No OpenAI, no broker orders, no ledger writes.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from dynamic_ema_pullback import Bar,Config,evaluate

MT5_PATH=r"C:\Program Files\MetaTrader 5\terminal64.exe"
SYMBOL="XAUUSD"
HISTORY_BARS=260


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pip-price",type=float,default=0.1)
    parser.add_argument("--min-body-pips",type=float,default=60.0)
    parser.add_argument("--max-wick-ratio",type=float,default=0.20)
    parser.add_argument("--sl-buffer-pips",type=float,default=10.0)
    parser.add_argument("--rr",type=float,default=1.5)
    args=parser.parse_args()

    import MetaTrader5 as mt5
    if not mt5.initialize(MT5_PATH):
        print("DYN_EMA_STOP | MT5_INITIALIZE_FAILED | NO_API | NO_ORDER")
        return 1
    try:
        account=mt5.account_info();info=mt5.symbol_info(SYMBOL)
        if (account is None or info is None
                or account.trade_mode!=mt5.ACCOUNT_TRADE_MODE_DEMO
                or account.currency!="USD" or info.currency_profit!="USD"):
            print("DYN_EMA_STOP | XAUUSD_USD_DEMO_REQUIRED | NO_API | NO_ORDER")
            return 1
        if not mt5.symbol_select(SYMBOL,True):
            print("DYN_EMA_STOP | XAUUSD_SELECT_FAILED | NO_API | NO_ORDER")
            return 1
        rates=mt5.copy_rates_from_pos(SYMBOL,mt5.TIMEFRAME_M5,1,HISTORY_BARS)
        if rates is None or len(rates)<HISTORY_BARS:
            print(f"DYN_EMA_STOP | NEED_{HISTORY_BARS}_CLOSED_M5_BARS | NO_API | NO_ORDER")
            return 1
        bars=[Bar(int(r["time"]),float(r["open"]),float(r["high"]),
                  float(r["low"]),float(r["close"])) for r in rates]
        cfg=Config(
            pip_price=args.pip_price,
            min_body_pips=args.min_body_pips,
            max_opposite_wick_ratio=args.max_wick_ratio,
            sl_buffer_pips=args.sl_buffer_pips,
            risk_reward=args.rr,
        )
        d=evaluate(bars,cfg)
        print(
            f"DYN_EMA | XAUUSD M5 | action={d.action} | trigger_bar={d.trigger_time} | "
            f"EMA50={d.ema_fast:.8f} | EMA200={d.ema_slow:.8f}"
        )
        print(
            f"TRIGGER | body={d.trigger_body:.8f} | opposite_wick_ratio={d.opposite_wick_ratio:.4f} | "
            f"pullback_close={d.pullback_close:.8f}"
        )
        if d.action in {"BUY","SELL"}:
            print(
                f"PLAN | {d.action} | entry_reference={d.entry_reference:.8f} | "
                f"SL={d.sl:.8f} | TP={d.tp:.8f} | risk_distance={d.risk_distance:.8f} | "
                f"RR={cfg.risk_reward:.2f}"
            )
        print(f"REASON | {d.reason}")
        print(
            "DYN_EMA_DONE | SPEC_RESEARCH_ONLY | NO_API | NO_ORDER | "
            "market fill/spread/fees not simulated"
        )
        return 0
    except ValueError as exc:
        print(f"DYN_EMA_STOP | {exc} | NO_API | NO_ORDER")
        return 1
    finally:
        mt5.shutdown()


if __name__=="__main__":
    raise SystemExit(main())
