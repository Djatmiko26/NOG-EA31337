"""No-API, no-order localhost transport soak for NOG_CashDemo EA.

Validates the same receiver/token/account/server/symbol/timeframe headers as the
cash bridge and always returns HTTP 204. It never imports OpenAI, never writes
the CashDemo SQLite ledger, and contains no broker order function.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
import threading
import time

HOST, PORT = "127.0.0.1", 8765
RECEIVER = "CASH_DEMO_V1"


def stamp(msg: str) -> None:
    print(time.strftime("[%Y-%m-%d %H:%M:%S UTC] ", time.gmtime()) + msg, flush=True)


def load_identity():
    import MetaTrader5 as mt5
    if not mt5.initialize(r"C:\Program Files\MetaTrader 5\terminal64.exe"):
        raise RuntimeError("MT5_INITIALIZE_FAILED")
    try:
        account = mt5.account_info()
        terminal = mt5.terminal_info()
        info = mt5.symbol_info("XAUUSD")
        if (account is None or terminal is None or info is None
                or account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO
                or account.currency != "USD"
                or info.currency_profit != "USD"):
            raise RuntimeError("XAUUSD_USD_DEMO_REQUIRED")
        token_path = Path(terminal.data_path) / "MQL5" / "Files" / "NOG_CashDemo" / "bridge.token"
        token = token_path.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", token):
            raise RuntimeError("INVALID_LOCAL_TOKEN")
        return token, str(account.login), str(account.server)
    finally:
        mt5.shutdown()


class StableServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 32


def handler_for(token: str, login: str, server: str, stats: dict, lock: threading.Lock):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def do_GET(self):
            with lock:
                stats["requests"] += 1
            if self.path != "/signal":
                with lock:
                    stats["rejected"] += 1
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                return

            value = self.headers.get("X-NOG-Token", "")
            valid = (
                len(value) == 64
                and value.isascii()
                and secrets.compare_digest(value, token)
                and self.headers.get("X-NOG-Receiver") == RECEIVER
                and self.headers.get("X-NOG-Account") == login
                and self.headers.get("X-NOG-Server") == server
                and self.headers.get("X-NOG-Symbol") == "XAUUSD"
                and self.headers.get("X-NOG-Timeframe") == "M5"
            )
            if not valid:
                with lock:
                    stats["rejected"] += 1
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                return

            with lock:
                stats["accepted"] += 1
                stats["last_seen"] = time.monotonic()
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

        def log_message(self, *_):
            pass

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=180)
    args = parser.parse_args()
    if not 30 <= args.seconds <= 900:
        raise SystemExit("--seconds must be between 30 and 900")

    token, login, server_name = load_identity()
    stats = {"requests": 0, "accepted": 0, "rejected": 0, "last_seen": 0.0}
    lock = threading.Lock()
    server = StableServer((HOST, PORT), handler_for(token, login, server_name, stats, lock))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    stamp(f"TRANSPORT_SOAK_READY | {HOST}:{PORT} | seconds={args.seconds} | NO_API | NO_ORDER | NO_LEDGER_WRITE")
    started = time.monotonic()
    next_report = started + 30
    try:
        while time.monotonic() - started < args.seconds:
            time.sleep(0.2)
            now = time.monotonic()
            if now >= next_report:
                with lock:
                    age = -1 if stats["last_seen"] == 0 else now - stats["last_seen"]
                    stamp(
                        f"SOAK_HEARTBEAT | requests={stats['requests']} | accepted={stats['accepted']} | "
                        f"rejected={stats['rejected']} | last_valid_age_s={age:.1f}"
                    )
                next_report += 30
    except KeyboardInterrupt:
        stamp("SOAK_INTERRUPTED")
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()

    with lock:
        stamp(
            f"SOAK_DONE | requests={stats['requests']} | accepted={stats['accepted']} | "
            f"rejected={stats['rejected']} | NO_API | NO_ORDER | NO_LEDGER_WRITE"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
