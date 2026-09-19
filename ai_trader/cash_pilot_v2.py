"""Cash Pilot V2: one-call-at-a-time XAUUSD M5 PREVIEW research.

Safety properties:
- separate append-only ledger: data/cash_pilot_v2.sqlite3
- PREVIEW packets only; never requests DEMO_SEND
- at most ONE new OpenAI attempt per --run invocation
- no broker order function in this file
- V1 CashDemo ledger is never modified
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

import cash_demo as base

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "cash_pilot_v2.sqlite3"
HOST, PORT = "127.0.0.1", 8765
CAP = 12
VERSION = "cash-pilot-v2"


def log(message):
    print(time.strftime("[%Y-%m-%d %H:%M:%S UTC] ", time.gmtime()) + message, flush=True)


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def implementation():
    names = ("cash_pilot_v2.py", "cash_demo.py", "cash_risk.py",
             "live_ai_core.py", "live_ai_bridge.py")
    return {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in names
    }


class Ledger:
    """Immutable-spec V2 ledger. Reserved bars are never retried."""
    def __init__(self, path: Path, spec: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        try:
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    spec TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts(
                    bar INTEGER PRIMARY KEY,
                    sid TEXT UNIQUE NOT NULL,
                    mode TEXT NOT NULL CHECK(mode='PREVIEW'),
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    result TEXT
                );
            """)
            frozen = encode({**spec, "pilot_version": VERSION, "cap": CAP})
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO meta VALUES(1,?)", (frozen,))
            saved = self.db.execute("SELECT spec FROM meta WHERE id=1").fetchone()[0]
            if saved != frozen:
                raise ValueError("V2_FROZEN_SETTINGS_CHANGED_PRESERVE_LEDGER")
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def count(self):
        return int(self.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0])

    def blocked(self):
        return self.db.execute(
            "SELECT 1 FROM attempts WHERE status!='SUCCESS' LIMIT 1"
        ).fetchone() is not None

    def reserve(self, bar: int, mode: str, payload: dict):
        if type(bar) is not int or bar <= 0 or mode != "PREVIEW":
            raise ValueError("INVALID_V2_RESERVATION")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            duplicate = self.db.execute(
                "SELECT 1 FROM attempts WHERE bar=?", (bar,)
            ).fetchone()
            if self.blocked() or self.count() >= CAP or duplicate:
                return None
            sid = secrets.token_hex(16)
            self.db.execute(
                "INSERT INTO attempts VALUES(?,?,?,'STARTED',?,NULL)",
                (bar, sid, "PREVIEW", encode(payload))
            )
            return sid
        finally:
            self.db.commit()

    def finish(self, bar: int, result: dict):
        allowed = {"SUCCESS", "ERROR", "REFUSED", "INVALID", "INCOMPLETE"}
        if result.get("status") not in allowed:
            raise ValueError("INVALID_V2_FINISH_STATUS")
        with self.db:
            cur = self.db.execute(
                "UPDATE attempts SET status=?,result=? "
                "WHERE bar=? AND status='STARTED'",
                (result["status"], encode(result), bar)
            )
            if cur.rowcount != 1:
                raise ValueError("V2_UNRESERVED_OR_FINISHED_ATTEMPT")


def status():
    if not DB.is_file():
        log(f"V2_LEDGER_NOT_STARTED | ATTEMPTS=0/{CAP} | API_CALLS=0")
        return
    db = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = list(db.execute(
            "SELECT bar,status,result FROM attempts ORDER BY bar"
        ))
        directional = waits = 0
        for bar, st, result_text in rows:
            result = json.loads(result_text) if result_text else {}
            signal = result.get("signal", {})
            action = signal.get("action", "-")
            if action in {"BUY", "SELL"}:
                directional += 1
            elif action == "WAIT":
                waits += 1
            log(
                f"V2 | bar={bar} | {st} | action={action} | "
                f"confidence={signal.get('confidence','-')} | "
                f"regime={signal.get('market_regime','-')} | "
                f"delivery={result.get('delivery','UNKNOWN')}"
            )
        log(
            f"V2_STATUS | ATTEMPTS={len(rows)}/{CAP} | "
            f"DIRECTIONAL={directional} | WAIT={waits} | API_CALLS=0"
        )
    finally:
        db.close()


def run_one(feed, core):
    from openai import OpenAI

    obs, account = base.require_usd_demo(feed)
    _, token_path = base.paths(feed)
    token = base.read_token(token_path)
    identity = (str(account.login), str(account.server))
    state = core.BridgeState("XAUUSD")

    ledger = client = server = worker = None
    started = False
    try:
        ledger = Ledger(DB, {
            "symbol": "XAUUSD",
            "model": feed.config.model,
            "source_id": obs.source_id,
            "implementation": implementation(),
            "instructions": core.INSTRUCTIONS,
            "schema": core.SCHEMA,
        })
        if ledger.blocked():
            raise ValueError("V2_UNRESOLVED_ATTEMPT_USE_STATUS")
        if ledger.count() >= CAP:
            log(f"V2_CAP_REACHED | {ledger.count()}/{CAP} | NO_API")
            return

        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise ValueError("OPENAI_API_KEY_MISSING")

        ThreadingHTTPServer.allow_reuse_address = True
        ThreadingHTTPServer.daemon_threads = True
        server = ThreadingHTTPServer((HOST, PORT), base.handler_for(state, token, identity))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        client = OpenAI(
            api_key=key,
            base_url="https://api.openai.com/v1",
            max_retries=0,
            timeout=25.0,
        )
        worker.start()
        started = True

        initial = ledger.count()
        log(
            f"V2_READY | PREVIEW_ONLY | attempts={initial}/{CAP} | "
            "max_new_this_run=1"
        )
        log("V2_WAIT_CASH_EA | no API before matching receiver polls")

        gate = core.TransitionGate()
        previous = ""
        heartbeat = time.monotonic()

        while ledger.count() == initial:
            mono, wall = time.monotonic(), time.time()
            message = "WAIT_NEW_CLOSED_CANDLE"
            try:
                obs, account = base.require_usd_demo(feed)
                event, message = gate.observe(obs, mono, wall)
                state.maintain(
                    obs.closed, gate.tick_is_advancing(mono), mono, wall
                )

                if not state.receiver_ready(mono):
                    gate.reset()
                    state.clear()
                    message = "V2_WAIT_CASH_EA"
                elif event is not None:
                    payload = feed.payload(obs)
                    result = base.analyse(
                        ledger, state, client, core, feed, "PREVIEW",
                        account.login, obs, payload, mono, wall
                    )
                    log(
                        f"V2_RESULT | {result['status']} | "
                        f"action={result.get('signal',{}).get('action','-')} | "
                        f"delivery={result['delivery']} | "
                        f"attempts={ledger.count()}/{CAP}"
                    )
                    if result["status"] not in {"SUCCESS", "SKIPPED"}:
                        log(
                            f"V2_STOP_AFTER_ERROR | "
                            f"http={result.get('http_status','-')} | "
                            f"code={result.get('error_code','-')}"
                        )
                    if result.get("delivery") == "READY_FOR_EA":
                        log("V2_DELIVERY_WINDOW | 6s | no second API call")
                        time.sleep(6)
                    break
            except (core.GuardError, ValueError):
                gate.reset()
                state.clear()
                message = "FEED_GUARD_RESYNC"

            if message != previous:
                log(message)
                previous = message
            if time.monotonic() - heartbeat >= 30:
                log(
                    f"V2_HEARTBEAT | {message} | "
                    f"attempts={ledger.count()}/{CAP}"
                )
                heartbeat = time.monotonic()
            time.sleep(core.POLL_SECONDS)
    finally:
        state.clear()
        if started:
            server.shutdown()
            worker.join(timeout=3)
        if server is not None:
            server.server_close()
        if client is not None:
            client.close()
        if ledger is not None:
            ledger.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run", action="store_true")
    group.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if not (args.run or args.status):
        parser.print_help()
        return 0
    if args.status:
        status()
        return 0

    feed = None
    try:
        import live_ai_core as core
        from live_ai_bridge import MT5Feed, config_from_env
        import MetaTrader5 as mt5

        config = replace(config_from_env(), symbol="XAUUSD")
        feed = MT5Feed(mt5, config)
        feed.connect()
        base.require_usd_demo(feed)
        run_one(feed, core)
        return 0
    except KeyboardInterrupt:
        log("V2_STOPPED | PREVIEW_ONLY | NO_ORDER_REQUESTED")
        return 0
    except Exception as exc:
        code = (
            str(exc)
            if isinstance(exc, ValueError)
            and re.fullmatch(r"[A-Z0-9_]{1,120}", str(exc))
            else type(exc).__name__
        )
        log(f"V2_STOP | {code} | preserve V2 ledger")
        return 1
    finally:
        if feed is not None:
            feed.close()


if __name__ == "__main__":
    raise SystemExit(main())
