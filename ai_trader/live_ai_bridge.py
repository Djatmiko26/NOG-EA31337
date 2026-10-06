"""MT5 live CLOSED candles -> OpenAI -> loopback receiver; strictly DRYRUN.

--check: MT5/data/config check only; zero OpenAI calls and no database writes.
--run: explicit paid mode; cumulative cap of THREE attempts in a new ledger.
--status: read local ledger only. No arguments means help, not paid execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from live_ai_core import (
    AI_BARS, ATTEMPT_CAP, CONTEXT_BARS, POLL_SECONDS, VERSION,
    AttemptLedger, BridgeState, GuardError, Observation, TransitionGate,
    analyse_and_publish, build_payload, digest, nondirectional_gate, symbol_ok,
)

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "data" / "live_ai_bridge.sqlite3"
HOST, PORT = "127.0.0.1", 8765
RECEIVER_HEADER = "LIVE_DRYRUN_V1"


@dataclass(frozen=True)
class Config:
    symbol: str = "XAUUSD"
    model: str = "gpt-5.6-luna"  # user may choose explicitly in their LOCAL .env
    mt5_path: str = r"C:\Program Files\MetaTrader 5\terminal64.exe"


def log(text: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{stamp}] {text}", flush=True)


def config_from_env() -> Config:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    config = Config(symbol=os.getenv("SYMBOL", "XAUUSD").strip(),
                    model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna").strip(),
                    mt5_path=os.getenv("MT5_PATH", Config.mt5_path).strip())
    if not symbol_ok(config.symbol):
        raise GuardError("SYMBOL_INVALID")
    if re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", config.model) is None:
        raise GuardError("OPENAI_MODEL_INVALID")
    if os.getenv("TIMEFRAME", "M5").strip().upper() != "M5":
        raise GuardError("THIS_BRIDGE_REQUIRES_M5")
    return config


class MT5Feed:
    def __init__(self, module, config: Config):
        self.mt5, self.config = module, config
        self.source_id: str | None = None

    def connect(self) -> None:
        if not self.mt5.initialize(self.config.mt5_path):
            raise GuardError("MT5_INITIALIZE_FAILED_CHECK_DEMO_LOGIN")
        if not self.mt5.symbol_select(self.config.symbol, True):
            raise GuardError("SYMBOL_NOT_AVAILABLE")
        self.observe()

    def close(self) -> None:
        self.mt5.shutdown()

    def observe(self) -> Observation:
        mt5, symbol = self.mt5, self.config.symbol
        terminal, account, info = mt5.terminal_info(), mt5.account_info(), mt5.symbol_info(symbol)
        if terminal is None or not terminal.connected or account is None:
            raise GuardError("MT5_DISCONNECTED")
        if account.trade_mode != getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0):
            raise GuardError("DEMO_ACCOUNT_REQUIRED")
        if info is None or info.chart_mode != getattr(mt5, "SYMBOL_CHART_MODE_BID", 0):
            raise GuardError("BID_CHART_REQUIRED")
        # Raw login/server/path never enter API input, console or the database.
        sid = digest([str(account.login), str(account.server), str(terminal.path)])
        if self.source_id is not None and sid != self.source_id:
            raise GuardError("DEMO_ACCOUNT_OR_TERMINAL_CHANGED_RESTART_REQUIRED")
        self.source_id = sid
        bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 2)
        tick = mt5.symbol_info_tick(symbol)
        if bars is None or len(bars) != 2 or tick is None:
            raise GuardError("CURRENT_BARS_OR_TICK_UNAVAILABLE")
        obs = Observation(int(bars[-1]["time"]), int(bars[-2]["time"]), int(tick.time_msc),
                          float(tick.bid), float(tick.ask), float(info.point), sid)
        obs.validate()
        return obs

    def payload(self, expected: Observation) -> dict:
        rates = self.mt5.copy_rates_from_pos(self.config.symbol, self.mt5.TIMEFRAME_M5, 1, CONTEXT_BARS)
        if rates is None or len(rates) != CONTEXT_BARS:
            raise GuardError("NEED_120_CLOSED_BARS_LOAD_MT5_CHART_HISTORY")
        records = [{"time": int(r["time"]), "open": float(r["open"]),
                    "high": float(r["high"]), "low": float(r["low"]), "close": float(r["close"]),
                    "spread": int(r["spread"]), "tick_volume": int(r["tick_volume"])} for r in rates]
        current = self.observe()
        if (current.closed, current.forming, current.source_id) != (expected.closed, expected.forming, expected.source_id):
            raise GuardError("BAR_CHANGED_DURING_READ_WAIT_NEXT_BAR")
        return build_payload(records, self.config.symbol, current)


def handler_for(state: BridgeState):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2.)

        def do_GET(self):
            if self.path != "/signal":
                self.send_error(404)
                return
            # Compatibility handshake, NOT authentication. Old TEST receiver is not compatible.
            if (self.headers.get("X-NOG-Receiver") != RECEIVER_HEADER or
                    self.headers.get("X-NOG-Symbol") != state.symbol or
                    self.headers.get("X-NOG-Timeframe") != "M5"):
                self.send_error(409, "Use NOG_LiveAI_DRYRUN on the matching demo M5 chart")
                return
            mono, wall = time.monotonic(), time.time()
            state.receiver_seen(mono)
            body = state.current(mono, wall)
            self.send_response(200 if body else 204)
            self.send_header("Content-Type", "text/plain; charset=us-ascii")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body) if body else 0))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, *_args):
            pass
    return Handler


def implementation() -> dict:
    return {name: hashlib.sha256((BASE_DIR / name).read_text(encoding="utf-8").encode()).hexdigest()
            for name in ("live_ai_core.py", "live_ai_bridge.py")}


def status() -> None:
    if not DB_FILE.is_file():
        log("No live ledger yet | attempted=0/3 | OPENAI_CALLS=0")
        return
    db = sqlite3.connect(DB_FILE.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        for row in db.execute("SELECT bar_raw,status,result FROM attempts ORDER BY requested_at"):
            result = json.loads(row[2]) if row[2] else {}
            log(f"bar_raw={row[0]} | status={row[1]} | delivery={result.get('delivery', 'UNKNOWN')} | "
                f"action={result.get('signal', {}).get('action', '-')} | "
                f"input_tokens={result.get('input_tokens', '?')} | output_tokens={result.get('output_tokens', '?')} | "
                f"http={result.get('http_status', '-')} | error={result.get('error_type', '-')}")
        used = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        log(f"ATTEMPTS={used}/{ATTEMPT_CAP} | local ledger only | OPENAI_CALLS=0")
    finally:
        db.close()


def run(config: Config, feed: MT5Feed, client_factory=None) -> None:
    # Bind first: stop if demo bridge still owns the port; never auto-kill another process.
    state = BridgeState(config.symbol)
    try:
        server = HTTPServer((HOST, PORT), handler_for(state))
    except OSError:
        raise GuardError("PORT_8765_BUSY_TYPE_QUIT_IN_OLD_DEMO_SERVER") from None
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    ledger = client = None
    started = False
    try:
        feed.connect()
        ledger = AttemptLedger(DB_FILE, {"symbol": config.symbol, "model": config.model,
                              "timeframe": "M5", "source_id": feed.source_id,
                              "implementation": implementation()})
        if ledger.blocked():
            raise GuardError("UNRESOLVED_LIVE_ATTEMPT_USE_STATUS_DO_NOT_DELETE_DATABASE")
        if ledger.count() >= ATTEMPT_CAP:
            log("CALL_LIMIT_REACHED | 3/3 already reserved | no new API calls")
            return
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise GuardError("OPENAI_API_KEY_MISSING_KEEP_IN_LOCAL_ENV")
        if client_factory is None:
            from openai import OpenAI
            client_factory = OpenAI
        client = client_factory(api_key=key, base_url="https://api.openai.com/v1",
                                max_retries=0, timeout=25.)
        worker.start()
        started = True
        log(f"LIVE AI BRIDGE READY | {config.symbol} M5 | model={config.model} | attempts={ledger.count()}/{ATTEMPT_CAP}")
        log("PAID_API_ON_NEW_CLOSED_BAR | NO_LOCAL_DIRECTION_HINT | NO_ORDER_EXECUTION")
        log(f"http://{HOST}:{PORT}/signal | attach NOG_LiveAI_DRYRUN | Ctrl+C to stop")
        gate, last_message = TransitionGate(), ""
        last_heartbeat = time.monotonic()
        while True:
            now_mono, now_wall = time.monotonic(), time.time()
            message = "WAIT_NEW_CLOSED_CANDLE"
            try:
                obs = feed.observe()
                event, message = gate.observe(obs, now_mono, now_wall)
                state.maintain(obs.closed, gate.tick_is_advancing(now_mono), now_mono, now_wall)
                if not state.receiver_ready(now_mono):
                    gate.reset()
                    state.clear()
                    message = "WAIT_EA_RECEIVER | no API call before compatible receiver is polling"
                elif ledger.count() >= ATTEMPT_CAP:
                    message = f"CALL_LIMIT_REACHED | {ATTEMPT_CAP}/{ATTEMPT_CAP} | no more API requests; Ctrl+C to stop"
                elif event is not None:
                    state.clear()
                    payload = feed.payload(obs)
                    if not nondirectional_gate(payload):
                        message = "LOCAL_SKIP_SPREAD_ATR | no API call | no directional score"
                    else:
                        log(f"NEW_CLOSED_CANDLE | bar_raw={event} | sending {AI_BARS} closed bars to OpenAI")
                        result = analyse_and_publish(ledger, state, client, config.model, config.symbol,
                                                     payload, obs, now_mono, now_wall, feed.observe,
                                                     time.monotonic, time.time)
                        signal = result.get("signal", {})
                        log(f"OPENAI_RESULT | status={result['status']} | action={signal.get('action', '-')} | "
                            f"confidence_score={signal.get('confidence', '-')} | delivery={result['delivery']} | "
                            f"attempts={ledger.count()}/{ATTEMPT_CAP}")
                        if signal:
                            # No model output becomes a command; reason is display/log data only.
                            reason = " ".join(signal["reason"].split())[:500]
                            log(f"REASON | {reason}")
                        if result["status"] not in {"SUCCESS", "SKIPPED"}:
                            log(f"STOP_AFTER_API_ERROR | http={result.get('http_status', '-')} | "
                                f"type={result.get('error_type', result['status'])} | no retry")
                            break
                        gate.reset()  # synchronous request may have missed a transition
                        if result.get("delivery") == "READY_FOR_DRYRUN":
                            gate.progress_mono = time.monotonic()  # post-call tick was just checked
                        message = "WAIT_NEW_CLOSED_CANDLE"
            except GuardError as exc:
                state.clear()
                gate.reset()
                message = str(exc) + " | no signal published"
            if message != last_message:
                log(message)
                last_message = message
            if time.monotonic() - last_heartbeat >= 30:
                log(f"HEARTBEAT | {message} | attempts={ledger.count()}/{ATTEMPT_CAP} | NO_ORDER_EXECUTION")
                last_heartbeat = time.monotonic()
            time.sleep(POLL_SECONDS)
    finally:
        state.clear()
        if started:
            server.shutdown()
            worker.join(timeout=3)
        server.server_close()
        if client is not None:
            client.close()
        if ledger is not None:
            ledger.close()
        feed.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if not any((args.check, args.run, args.status)):
        parser.print_help()
        return 0
    try:
        if args.status:
            status()
            return 0
        config = config_from_env()
        import MetaTrader5 as mt5
        feed = MT5Feed(mt5, config)
        if args.check:
            try:
                feed.connect()
                obs = feed.observe()
                payload = feed.payload(obs)
                from openai import OpenAI  # import check only, no client or paid request
                log(f"CHECK_OK | DEMO | {config.symbol} M5 | model={config.model} | bars={CONTEXT_BARS} | "
                    f"payload_bars={len(payload['candles'])} | gate_pass={nondirectional_gate(payload)}")
                log("OPENAI_CALLS=0 | NO_DATABASE_WRITE | NO_ORDER_EXECUTION")
                log("Check does not prove API access or live tick progression. --run waits for a NEW closed candle.")
            finally:
                feed.close()
        else:
            run(config, feed)
        return 0
    except KeyboardInterrupt:
        log("LIVE BRIDGE STOPPED | interrupted requests remain reserved | NO_ORDER_EXECUTION")
        return 0
    except GuardError as exc:
        log(f"LIVE STOP | {exc}")
    except ImportError:
        log("LIVE STOP | MISSING_PACKAGE | use C:\\venv\\Scripts\\python.exe or install requirements.txt")
    except Exception as exc:
        # No raw exception text: it could contain credentials from external libraries.
        log(f"LIVE STOP | {type(exc).__name__} | use --status; do not delete databases or retry blindly")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
