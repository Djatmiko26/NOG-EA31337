"""Resume Live AI DRYRUN after one reviewed HTTP 429 billing/quota error.

Preserves the original live_ai_bridge.sqlite3 unchanged. Creates/uses a SECOND
ledger with a cumulative cap of TWO attempts, so original 1 + retry 2 = 3 total.
No order execution is added. The existing NOG_LiveAI_DRYRUN EA is reused.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import live_ai_core as core
import live_ai_bridge as bridge

BASE_DIR = Path(__file__).resolve().parent
ORIGINAL_DB = BASE_DIR / "data" / "live_ai_bridge.sqlite3"
RETRY_DB = BASE_DIR / "data" / "live_ai_bridge_after_429.sqlite3"
REMAINING_CAP = 2


def verify_original_429() -> dict:
    if not ORIGINAL_DB.is_file():
        raise core.GuardError("ORIGINAL_LEDGER_MISSING_DO_NOT_CREATE_RETRY")
    db = sqlite3.connect(ORIGINAL_DB.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = list(db.execute(
            "SELECT bar_raw,status,result FROM attempts ORDER BY requested_at"
        ))
    finally:
        db.close()
    if len(rows) != 1:
        raise core.GuardError("EXPECTED_EXACTLY_ONE_ORIGINAL_ATTEMPT_REVIEW_STATUS")
    bar_raw, status, result_text = rows[0]
    try:
        result = json.loads(result_text) if result_text else {}
    except json.JSONDecodeError:
        raise core.GuardError("ORIGINAL_429_RESULT_INVALID") from None
    if not (
        status == "ERROR"
        and result.get("http_status") == 429
        and result.get("error_type") == "RateLimitError"
        and result.get("delivery") == "NOT_PUBLISHED"
    ):
        raise core.GuardError("ORIGINAL_ATTEMPT_IS_NOT_REVIEWED_429")
    return {"bar_raw": bar_raw, "result": result}


def configure_retry_cap() -> None:
    # Intentional separate-generation cap. Existing source behavior remains otherwise identical.
    core.ATTEMPT_CAP = REMAINING_CAP
    bridge.ATTEMPT_CAP = REMAINING_CAP
    bridge.DB_FILE = RETRY_DB


def combined_status() -> None:
    original = verify_original_429()
    print(
        f"ORIGINAL | bar_raw={original['bar_raw']} | status=ERROR | http=429 | "
        "error=RateLimitError | preserved=YES"
    )
    if not RETRY_DB.is_file():
        print("RETRY | attempts=0/2 | combined=1/3 | OPENAI_CALLS=0")
        return
    db = sqlite3.connect(RETRY_DB.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = list(db.execute(
            "SELECT bar_raw,status,result FROM attempts ORDER BY requested_at"
        ))
    finally:
        db.close()
    for bar_raw, status, result_text in rows:
        result = json.loads(result_text) if result_text else {}
        print(
            f"RETRY | bar_raw={bar_raw} | status={status} | "
            f"delivery={result.get('delivery','UNKNOWN')} | "
            f"action={result.get('signal',{}).get('action','-')} | "
            f"http={result.get('http_status','-')} | "
            f"error={result.get('error_type','-')}"
        )
    print(f"RETRY | attempts={len(rows)}/2 | combined={1+len(rows)}/3 | OPENAI_CALLS=0")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--status", action="store_true")
    args = parser.parse_args()

    try:
        verify_original_429()
        configure_retry_cap()
        if args.status:
            combined_status()
            return 0

        config = bridge.config_from_env()
        import MetaTrader5 as mt5
        feed = bridge.MT5Feed(mt5, config)
        bridge.log(
            "RESUME_AFTER_REVIEWED_429 | original attempt preserved | "
            "remaining_cap=2 | NO_ORDER_EXECUTION"
        )
        bridge.run(config, feed)
        return 0
    except KeyboardInterrupt:
        bridge.log("RETRY BRIDGE STOPPED | NO_ORDER_EXECUTION")
        return 0
    except core.GuardError as exc:
        bridge.log(f"RETRY STOP | {exc}")
    except ImportError:
        bridge.log("RETRY STOP | MISSING_PACKAGE | use C:\\venv\\Scripts\\python.exe")
    except Exception as exc:
        bridge.log(
            f"RETRY STOP | {type(exc).__name__} | do not delete either ledger; inspect --status"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
