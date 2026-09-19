"""Local test-signal API for NOG_SignalReceiver_DRYRUN.mq5; never trades.

No OpenAI, MT5 connection, .env, market data, or credentials are used.
Run normally, then type BUY, SELL, WAIT, EXPIRED, CLEAR or QUIT.
"""
from __future__ import annotations

import argparse
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST, PORT = "127.0.0.1", 8765
PROTOCOL = "NOG_DEMO_V1"
TTL_SECONDS = 30


def valid_symbol(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._#-]{1,64}", value) is not None


def make_packet(action: str, symbol: str, *, now: int | None = None,
                signal_id: str | None = None, expired: bool = False) -> bytes:
    if action not in {"BUY", "SELL", "WAIT"} or not valid_symbol(symbol):
        raise ValueError("Invalid action or symbol.")
    stamp = int(time.time()) if now is None else now
    sid = uuid.uuid4().hex if signal_id is None else signal_id
    if type(stamp) is not int or not 1_000_000_120 <= stamp <= 9_999_999_969:
        raise ValueError("Invalid epoch seconds.")
    if not isinstance(sid, str) or re.fullmatch(r"[a-f0-9]{32}", sid) is None:
        raise ValueError("Invalid test signal ID.")
    issued = stamp - 120 if expired else stamp
    fields = (PROTOCOL, "TEST_ONLY", sid, symbol, "M5", action,
              str(issued), str(issued + TTL_SECONDS))
    return ";".join(fields).encode("ascii")


class DemoState:
    def __init__(self, symbol: str):
        if not valid_symbol(symbol):
            raise ValueError("Invalid symbol.")
        self.symbol = symbol
        self._lock = threading.Lock()
        self._packet: bytes | None = None

    def publish(self, command: str) -> bytes | None:
        command = command.strip().upper()
        if command == "CLEAR":
            packet = None
        elif command in {"BUY", "SELL", "WAIT", "EXPIRED"}:
            packet = make_packet("BUY" if command == "EXPIRED" else command,
                                 self.symbol, expired=command == "EXPIRED")
        else:
            raise ValueError("Use BUY, SELL, WAIT, EXPIRED, CLEAR or QUIT.")
        with self._lock:
            self._packet = packet
        return packet

    def current(self) -> bytes | None:
        with self._lock:
            return self._packet  # do NOT renew the ID or expiry on HTTP polling


def handler_for(state: DemoState):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2.0)

        def do_GET(self):
            if self.path != "/signal":
                self.send_error(404)
                return
            body = state.current()
            self.send_response(204 if body is None else 200)
            self.send_header("Content-Type", "text/plain; charset=us-ascii")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body) if body else 0))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, *_args):
            pass  # keep the interactive console readable; no account data logged
    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="XAUUSD")
    args = parser.parse_args()
    if not valid_symbol(args.symbol):
        parser.error("Use the exact Market Watch name (ASCII letters/digits/._#-).")
    state = DemoState(args.symbol)
    try:
        server = HTTPServer((HOST, PORT), handler_for(state))
    except OSError as exc:
        print(f"BRIDGE STOP: port {PORT} unavailable ({type(exc).__name__}).")
        print("Do not start a second copy. Stop the previous demo server first.")
        return 1
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    print(f"DEMO API READY | http://{HOST}:{PORT}/signal | {args.symbol} M5", flush=True)
    print("TEST_ONLY | OPENAI_CALLS=0 | NO_ORDER_EXECUTION", flush=True)
    print("No signal yet. Attach the DRYRUN EA, then type BUY or EXPIRED.", flush=True)
    print("Commands: BUY / SELL / WAIT / EXPIRED / CLEAR / QUIT", flush=True)
    try:
        while True:
            command = input("demo> ").strip().upper()
            if command == "QUIT":
                break
            try:
                body = state.publish(command)
                if body is None:
                    print("CLEARED | receiver should show NO_SIGNAL", flush=True)
                else:
                    sid = body.decode("ascii").split(";")[2]
                    print(f"PUBLISHED TEST {command} | id={sid} | NO ORDER", flush=True)
            except ValueError as exc:
                print(str(exc), flush=True)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
    print("DEMO API STOPPED | NO ORDER WAS REQUESTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
