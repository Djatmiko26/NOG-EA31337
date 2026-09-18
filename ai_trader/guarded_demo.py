"""Guarded DEMO bridge. Default: help. --check/--install/--status cost no API tokens.

--run: three additional paid preview attempts; --run --demo-orders: a separate
three-attempt DEMO phase. EA defaults to preview and independently enforces risk.
No Python broker order calls. Old ledgers/modules are never rewritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
CAP = 3
HOST, PORT = '127.0.0.1', 8765
EA_FILES = ('NOG_GuardedDemo.mq5', 'NOG_RiskMath.mqh')
KNOWN_ERRORS = {'insufficient_quota', 'credit_balance_exhausted', 'rate_limit_exceeded',
                'organization_usage_limit_exceeded', 'organization_spend_limit_exceeded',
                'project_spend_limit_exceeded', 'invalid_api_key', 'model_not_found'}


def log(text: str) -> None:
    print(time.strftime('[%Y-%m-%d %H:%M:%S UTC] ', time.gmtime()) + text, flush=True)


def encode(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding='utf-8').encode('utf-8')).hexdigest()


class Ledger:
    """New bounded phase; attempts are durable BEFORE the request, never deleted."""
    def __init__(self, path: Path, spec: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        try:
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY CHECK(id=1), spec TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                    bar INTEGER PRIMARY KEY, sid TEXT UNIQUE, status TEXT NOT NULL,
                    payload TEXT NOT NULL, result TEXT, acknowledged INTEGER NOT NULL DEFAULT 0);
            ''')
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO meta VALUES(1,?)', (encode(spec),))
            if self.db.execute('SELECT spec FROM meta WHERE id=1').fetchone()[0] != encode(spec):
                raise ValueError('FROZEN_SETTINGS_CHANGED: preserve ledger, review changes')
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def count(self) -> int:
        return self.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]

    def blocked(self) -> bool:
        return bool(self.db.execute("SELECT 1 FROM attempts WHERE status!='SUCCESS' AND acknowledged=0 LIMIT 1").fetchone())

    def reserve(self, bar: int, payload: dict) -> str | None:
        if type(bar) is not int or bar <= 0:
            raise ValueError('INVALID_BAR')
        text = encode(payload)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.blocked() or self.count() >= CAP or self.db.execute('SELECT 1 FROM attempts WHERE bar=?', (bar,)).fetchone():
                return None
            sid = secrets.token_hex(16)
            self.db.execute("INSERT INTO attempts VALUES(?,?,'STARTED',?,NULL,0)", (bar, sid, text))
            return sid
        finally:
            self.db.commit()

    def finish(self, bar: int, result: dict):
        if result.get('status') not in {'SUCCESS','ERROR','REFUSED','INVALID','INCOMPLETE'}:
            raise ValueError('INVALID_RESULT_STATUS')
        with self.db:
            cursor = self.db.execute("UPDATE attempts SET status=?,result=? WHERE bar=? AND status='STARTED'",
                                     (result['status'], encode(result), bar))
            if cursor.rowcount != 1:
                raise ValueError('ATTEMPT_NOT_RESERVED_OR_ALREADY_FINISHED')


def safe_error(exc: Exception) -> dict:
    code = getattr(exc, 'code', None)
    body = getattr(exc, 'body', None)
    if code is None and isinstance(body, dict):
        error = body.get('error', body)
        if isinstance(error, dict):
            code = error.get('code')
    # No raw error message, authorization headers, IDs or request bodies are logged.
    name = type(exc).__name__
    return {'status': 'ERROR', 'http_status': getattr(exc, 'status_code', None)
            if type(getattr(exc, 'status_code', None)) is int else None,
            'error_type': name if re.fullmatch(r'[A-Za-z_]{1,80}', name) else 'APIError',
            'error_code': code if isinstance(code, str) and code in KNOWN_ERRORS else 'UNKNOWN'}


def packet(mode: str, sid: str, symbol: str, action: str, wall: int, expiry: int,
           bar: int, login: int, atr: float, bid: float, ask: float) -> bytes:
    if mode not in {'PREVIEW', 'DEMO_SEND'} or action not in {'BUY','SELL','WAIT'}:
        raise ValueError('INVALID_MODE_OR_ACTION')
    if not re.fullmatch(r'[0-9a-f]{32}', sid) or not re.fullmatch(r'[A-Za-z0-9._#-]{1,64}', symbol):
        raise ValueError('INVALID_ID_OR_SYMBOL')
    if any(type(v) is not int or not 0 < v < 10**12 for v in (wall, expiry, bar, login)) or not 0 < expiry-wall <= 30:
        raise ValueError('INVALID_EPOCH_OR_TTL')
    if any(type(v) not in (int,float) or not math.isfinite(v) or v <= 0 for v in (atr,bid,ask)) or ask < bid:
        raise ValueError('INVALID_PRICES')
    return f'NOG_GUARDED_V1;{mode};{sid};{symbol};M5;{action};{wall};{expiry};{bar};{login};{atr:.8f};{bid:.8f};{ask:.8f}'.encode('ascii')


def handler_for(state, token: str, identity: tuple[str,str], symbol: str):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def do_GET(self):
            if self.path != '/signal':
                self.send_error(404); return
            supplied = self.headers.get('X-NOG-Token', '')
            valid = (len(supplied) == 64 and supplied.isascii() and secrets.compare_digest(supplied, token)
                     and self.headers.get('X-NOG-Receiver') == 'GUARDED_DEMO_V1'
                     and self.headers.get('X-NOG-Account') == identity[0]
                     and self.headers.get('X-NOG-Server') == identity[1]
                     and self.headers.get('X-NOG-Symbol') == symbol
                     and self.headers.get('X-NOG-Timeframe') == 'M5')
            if not valid:
                self.send_error(403); return
            mono, wall = time.monotonic(), time.time()
            state.receiver_seen(mono)
            body = state.current(mono, wall)
            self.send_response(200 if body else 204)
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Type','text/plain; charset=us-ascii')
            self.send_header('Content-Length', str(len(body) if body else 0))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, *_args):
            pass
    return Handler


def installation_paths(feed) -> tuple[Path,Path]:
    terminal = feed.mt5.terminal_info()
    if terminal is None or not terminal.data_path:
        raise ValueError('ACTIVE_TERMINAL_DATA_FOLDER_UNAVAILABLE')
    base = Path(terminal.data_path) / 'MQL5'
    if not base.is_dir():
        raise ValueError('ACTIVE_MQL5_FOLDER_NOT_FOUND')
    return base / 'Experts', base / 'Files' / 'NOG_GuardedDemo' / 'bridge.token'


def install(feed, source: Path = ROOT / 'mt5') -> None:
    experts, token_path = installation_paths(feed)
    experts.mkdir(parents=True, exist_ok=True)
    for name in EA_FILES:
        src, dst = source / name, experts / name
        if not src.is_file():
            raise ValueError('EA_SOURCE_MISSING_PULL_GIT')
        if dst.exists() and dst.read_bytes() != src.read_bytes():
            backup = dst.with_name(dst.name + '.backup.' + str(time.time_ns()))
            shutil.copy2(dst, backup)
        shutil.copy2(src, dst)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    if not token_path.exists():
        with token_path.open('x', encoding='ascii') as fh:
            fh.write(secrets.token_hex(32))
        try:
            token_path.chmod(0o600)
        except OSError:
            pass
    read_token(token_path)
    log(f'INSTALLED | {experts / EA_FILES[0]}')
    log('Open this installed EA in MetaEditor, F7. Default PREVIEW, Algo Trading OFF.')
    log('LOCAL_TOKEN_CREATED_OR_REUSED | OPENAI_CALLS=0 | NO_ORDER')


def read_token(path: Path) -> str:
    token = path.read_text(encoding='ascii').strip()
    if not re.fullmatch(r'[0-9a-f]{64}', token):
        raise ValueError('LOCAL_TOKEN_INVALID: do not share token or API key')
    return token


def preflight(feed, capital: float = 100.) -> None:
    obs = feed.observe()
    payload = feed.payload(obs)
    mt5 = feed.mt5
    account, info = mt5.account_info(), mt5.symbol_info(feed.config.symbol)
    budget = min(capital, account.equity, account.balance) * .0025
    atr = payload['candles'][-1]['atr14']
    size, minimum = float(info.trade_tick_size), float(info.volume_min)
    if min(size,minimum,atr,budget) <= 0 or not all(math.isfinite(v) for v in (size,minimum,atr,budget)):
        raise ValueError('INVALID_RISK_SPECIFICATION')
    stops = float(info.trade_stops_level) * obs.point
    stress = 20 * obs.point
    log(f'RISK_CHECK | DEMO | {feed.config.symbol} M5 | capital_cap={capital:.2f} {account.currency} | budget={budget:.4f}')
    log(f'BROKER | minimum_lot={minimum:g} | step={info.volume_step:g} | ATR14={atr:.8f}')
    for action, typ in [('BUY',mt5.ORDER_TYPE_BUY),('SELL',mt5.ORDER_TYPE_SELL)]:
        buy = action == 'BUY'
        entry = obs.ask if buy else obs.bid
        sl = (math.floor(min(entry-1.5*atr,obs.bid-stops-size)/size)*size if buy
              else math.ceil(max(entry+1.5*atr,obs.ask+stops+size)/size)*size)
        loss = mt5.order_calc_profit(typ,feed.config.symbol,minimum,
                                    entry+(stress if buy else -stress),sl+(-stress if buy else stress))
        if loss is None or not math.isfinite(loss) or loss >= 0:
            raise ValueError('BROKER_ORDER_CALC_PROFIT_FAILED')
        verdict = 'MIN_LOT_EXCEEDS_BUDGET' if -loss > budget*.8 else 'PRICE_RISK_FITS_BEFORE_COMMISSION'
        log(f'{action} | SL={sl:.8f} | minimum_stressed_price_loss={-loss:.4f} {account.currency} | {verdict}')
    log('COMMISSION_UNKNOWN until verified in EA input. These are risk estimates, not signals or fill guarantees.')
    log('CHECK_DONE | OPENAI_CALLS=0 | NO_ORDER | no ledger writes')


def analyse(ledger, state, client, core, feed, mode: str, login: int,
            obs, payload: dict, event_mono: float, event_wall: float, clocks=(time.monotonic,time.time)) -> dict:
    mono, wall = clocks
    state.clear()
    if payload.get('latest_closed_bar_raw') != obs.closed or not core.nondirectional_gate(payload):
        return {'status':'SKIPPED','delivery':'PAYLOAD_OR_QUALITY_GATE'}
    start_m, start_w = mono(), wall()
    if not state.receiver_ready(start_m) or not 0 <= start_m-event_mono <= 8 or abs((start_w-event_wall)-(start_m-event_mono))>3:
        return {'status':'SKIPPED','delivery':'NO_FRESH_RECEIVER'}
    sid = ledger.reserve(obs.closed, payload)
    if sid is None:
        return {'status':'SKIPPED','delivery':'LEDGER_BLOCKED_OR_LIMIT'}
    try:
        result = core.request_analysis(client, feed.config.model, payload)
    except Exception as exc:
        result = safe_error(exc)
    result['delivery'] = 'NOT_PUBLISHED'
    body = None
    if result['status'] == 'SUCCESS':
        try:
            after = feed.observe()
            after.validate()
            m,w = mono(),wall()
            if (not 0 <= m-event_mono <= 40 or abs((w-event_wall)-(m-event_mono))>3
                or after.source_id!=obs.source_id or after.closed!=obs.closed or after.forming!=obs.forming
                or after.point!=obs.point or after.tick_msc<=obs.tick_msc or not state.receiver_ready(m)):
                raise ValueError('POST_RESPONSE_FRESHNESS_OR_SOURCE')
            atr = payload['candles'][-1]['atr14']
            if (after.ask-after.bid)/atr > .12:
                raise ValueError('POST_RESPONSE_SPREAD')
            issued, expires = int(w), min(int(w)+30,int(event_wall+40))
            q = payload['current_quote']
            body = packet(mode,sid,feed.config.symbol,result['signal']['action'],issued,expires,obs.closed,
                          login,atr,q['bid'],q['ask'])
            result.update(delivery='READY_FOR_EA', expires=expires, signal_id=sid)
        except Exception:
            result['delivery'] = 'WITHHELD_RECHECK'  # validated response retained; never replayed
    result['finished_at'] = wall()
    ledger.finish(obs.closed,result)
    if body:
        state.publish(body,obs.closed,result['expires'],mono(),wall())
    return result


def run(feed, core, mode: str) -> None:
    state = core.BridgeState(feed.config.symbol)
    account = feed.mt5.account_info()
    identity = str(account.login), str(account.server)
    if not identity[1].isascii() or any(c in identity[1] for c in '\r\n'):
        raise ValueError('LOCAL_SERVER_NAME_FORMAT_UNSUPPORTED')
    _, keyfile = installation_paths(feed)
    token = read_token(keyfile)
    # Bind before creating ledger/client. Never kill a different process.
    server = HTTPServer((HOST,PORT),handler_for(state,token,identity,feed.config.symbol))
    ledger = client = None
    started = False
    worker = threading.Thread(target=server.serve_forever,daemon=True)
    try:
        files = ('guarded_demo.py','live_ai_core.py','live_ai_bridge.py')
        spec = {'version':'guarded-demo-v1','cap':CAP,'mode':mode,'model':feed.config.model,
                'symbol':feed.config.symbol,'source_id':feed.source_id,
                'implementation':{n:file_hash(ROOT/n) for n in files},
                'ea':{n:file_hash(ROOT/'mt5'/n) for n in EA_FILES}}
        dbname = 'guarded_demo_orders.sqlite3' if mode=='DEMO_SEND' else 'guarded_demo_preview.sqlite3'
        ledger = Ledger(DATA/dbname,spec)
        if ledger.blocked():
            raise ValueError('UNRESOLVED_API_ATTEMPT: use --status; no automatic retry')
        if ledger.count()>=CAP:
            log('API_LIMIT_REACHED | no new calls | preserve ledger'); return
        api_key = os.getenv('OPENAI_API_KEY','').strip()
        if not api_key:
            raise ValueError('API_KEY_MISSING: keep .env local')
        from openai import OpenAI
        client = OpenAI(api_key=api_key,base_url='https://api.openai.com/v1',max_retries=0,timeout=25)
        worker.start(); started=True
        log(f'GUARDED_BRIDGE_READY | mode={mode} | attempts={ledger.count()}/{CAP} | NEW_PAID_PHASE')
        log('EA independently blocks real accounts. No guarantee of profit. Ctrl+C stops new signals, NOT existing positions.')
        gate = core.TransitionGate()
        last_heartbeat, previous = time.monotonic(), ''
        while True:
            m,w = time.monotonic(),time.time()
            message = 'WAIT_NEW_CLOSED_CANDLE'
            try:
                obs = feed.observe()
                event,message = gate.observe(obs,m,w)
                state.maintain(obs.closed,gate.tick_is_advancing(m),m,w)
                if not state.receiver_ready(m):
                    gate.reset(); state.clear(); message='WAIT_AUTHENTICATED_EA | no API call'
                elif ledger.count()>=CAP:
                    message='API_LIMIT_REACHED | no more calls'
                elif event is not None:
                    positions,orders=feed.mt5.positions_get(),feed.mt5.orders_get()
                    if positions is None or orders is None:
                        raise ValueError('POSITIONS_OR_ORDERS_UNAVAILABLE')
                    payload=feed.payload(obs)
                    if positions or orders:
                        message='SKIP_ACCOUNT_BUSY | no API call'
                    elif not core.nondirectional_gate(payload):
                        message='SKIP_SPREAD | no API call'
                    else:
                        log(f'NEW_CLOSED_CANDLE | raw_bar={event}')
                        result=analyse(ledger,state,client,core,feed,mode,account.login,obs,payload,m,w)
                        log(f"AI_RESULT | status={result['status']} | action={result.get('signal',{}).get('action','-')} | delivery={result['delivery']} | attempts={ledger.count()}/{CAP}")
                        if result['status'] not in {'SUCCESS','SKIPPED'}:
                            log(f"API_STOP | http={result.get('http_status','-')} | code={result.get('error_code','-')} | NOT_WAIT | no retry")
                            break
                        gate.reset()
                        if result['delivery']=='READY_FOR_EA':
                            gate.progress_mono=time.monotonic()
            except core.GuardError as exc:
                gate.reset(); state.clear(); message=str(exc)
            if message!=previous:
                log(message); previous=message
            if time.monotonic()-last_heartbeat>=30:
                log(f'HEARTBEAT | {message} | attempts={ledger.count()}/{CAP}')
                last_heartbeat=time.monotonic()
            time.sleep(2)
    finally:
        state.clear()
        if started:
            server.shutdown(); worker.join(timeout=3)
        server.server_close()
        if client is not None: client.close()
        if ledger is not None: ledger.close()


def status() -> None:
    for name in ('guarded_demo_preview.sqlite3','guarded_demo_orders.sqlite3'):
        path = DATA/name
        if not path.exists():
            log(f'{name} | not started | API_CALLS=0'); continue
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
        try:
            rows=list(db.execute('SELECT bar,status,result FROM attempts ORDER BY bar'))
            for bar,st,body in rows:
                res=json.loads(body) if body else {}
                log(f"{name} | bar={bar} | {st} | action={res.get('signal',{}).get('action','-')} | delivery={res.get('delivery','UNKNOWN')} | code={res.get('error_code','-')}")
            log(f'{name} | attempts={len(rows)}/{CAP} | API_CALLS=0')
        finally:
            db.close()


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group()
    for name in ('check','install','status','run'):
        modes.add_argument('--'+name,action='store_true')
    parser.add_argument('--demo-orders',action='store_true',help='EXPLICIT separate paid DEMO phase; EA must also be armed')
    args=parser.parse_args()
    if args.demo_orders and not args.run:
        parser.error('--demo-orders requires --run')
    if not any((args.check,args.install,args.status,args.run)):
        parser.print_help(); return 0
    feed=None
    try:
        if args.status:
            status(); return 0
        import live_ai_core as core
        from live_ai_bridge import MT5Feed, config_from_env
        import MetaTrader5 as mt5
        feed=MT5Feed(mt5,config_from_env()); feed.connect()
        if args.check:
            preflight(feed)
        elif args.install:
            install(feed); preflight(feed)
        else:
            run(feed,core,'DEMO_SEND' if args.demo_orders else 'PREVIEW')
        return 0
    except KeyboardInterrupt:
        log('STOPPED | existing DEMO positions are NOT closed; inspect MT5 Trade tab'); return 0
    except (ValueError,FileNotFoundError,ImportError) as exc:
        # Only local validation messages are shown; SDK exception messages are never echoed.
        log(f'GUARDED_STOP | {type(exc).__name__} | {str(exc)[:240]}')
    except Exception as exc:
        log(f'GUARDED_STOP | {type(exc).__name__} | inspect --status; preserve all ledgers')
    finally:
        if feed is not None: feed.close()
    return 1


if __name__=='__main__':
    raise SystemExit(main())
