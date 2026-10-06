"""Read-only audit of stored directional CashDemo PREVIEW attempts.

Reads data/cash_demo.sqlite3 in SQLite read-only mode and uses the stored quote/ATR
with current XAUUSD contract specifications to reconstruct the cash-risk plan.
No OpenAI client, WebRequest server, trade request, or order function exists here.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sqlite3
import sys

import cash_risk as risk

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "cash_demo.sqlite3"


def stop(code: str) -> None:
    print(f"AUDIT_STOP | {code} | NO_API | NO_ORDER")
    raise SystemExit(1)


def read_directional_preview() -> tuple[int, dict, dict]:
    if not DB.is_file():
        stop("LEDGER_NOT_FOUND")
    db = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = db.execute(
            "SELECT bar,payload,result FROM attempts "
            "WHERE mode='PREVIEW' AND status='SUCCESS' ORDER BY bar"
        ).fetchall()
    finally:
        db.close()
    directional = []
    for bar, payload_text, result_text in rows:
        try:
            payload = json.loads(payload_text)
            result = json.loads(result_text)
        except (TypeError, json.JSONDecodeError):
            stop("INVALID_LEDGER_JSON")
        action = result.get("signal", {}).get("action")
        if action in {"BUY", "SELL"}:
            directional.append((int(bar), payload, result))
    if not directional:
        stop("NO_DIRECTIONAL_PREVIEW")
    if len(directional) != 1:
        stop("MULTIPLE_DIRECTIONAL_PREVIEWS_REVIEW_REQUIRED")
    return directional[0]


def finite_positive(value) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        stop("INVALID_STORED_MARKET_VALUE")
    return float(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commission-per-lot", type=float, required=True,
                        help="verified round-trip USD commission per lot")
    parser.add_argument("--fixed-fee", type=float, required=True,
                        help="verified round-trip fixed USD fee per position")
    args = parser.parse_args()
    if any(not math.isfinite(v) or v < 0 for v in (args.commission_per_lot, args.fixed_fee)):
        stop("INVALID_FEE_INPUT")

    bar, payload, result = read_directional_preview()
    action = result["signal"]["action"]
    if payload.get("latest_closed_bar_raw") != bar:
        stop("LEDGER_BAR_PAYLOAD_MISMATCH")
    candles = payload.get("candles")
    quote = payload.get("current_quote")
    if not isinstance(candles, list) or not candles or not isinstance(quote, dict):
        stop("MISSING_STORED_MARKET_SNAPSHOT")
    atr = finite_positive(candles[-1].get("atr14"))
    bid = finite_positive(quote.get("bid"))
    ask = finite_positive(quote.get("ask"))
    if ask < bid:
        stop("INVALID_STORED_SPREAD")

    import MetaTrader5 as mt5
    if not mt5.initialize(r"C:\Program Files\MetaTrader 5\terminal64.exe"):
        stop("MT5_INITIALIZE_FAILED")
    try:
        account = mt5.account_info()
        info = mt5.symbol_info("XAUUSD")
        if (account is None or info is None
                or account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO
                or account.currency != "USD"
                or info.currency_profit != "USD"):
            stop("XAUUSD_USD_DEMO_REQUIRED")
        if not mt5.symbol_select("XAUUSD", True):
            stop("XAUUSD_SELECT_FAILED")

        plan = risk.broker_plan(
            mt5, "XAUUSD", bid, ask, atr, action == "BUY",
            args.commission_per_lot, args.fixed_fee
        )

        print(f"AUDIT_SOURCE | bar={bar} | action={action} | STORED_PREVIEW")
        print(f"STORED_MARKET | bid={bid:.8f} | ask={ask:.8f} | atr14={atr:.8f}")
        print(
            f"PLAN | {action} | lot={plan.volume:.8f} | entry={plan.entry:.8f} | "
            f"SL={plan.sl:.8f} | TP={plan.tp:.8f}"
        )
        print(
            f"CASH | stressed_loss_usd={plan.stressed_loss:.8f} | "
            f"stressed_profit_usd={plan.stressed_profit:.8f} | fee_usd={plan.fee:.8f}"
        )
        print(
            "AUDIT_PASS | STORED_QUOTE + CURRENT_CONTRACT_SPECS | "
            "NO_API | NO_ORDER | fill/outcome NOT guaranteed"
        )
        return 0
    except ValueError as exc:
        code = str(exc)
        stop(code if code and len(code) <= 120 else "RISK_PLAN_REJECTED")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
