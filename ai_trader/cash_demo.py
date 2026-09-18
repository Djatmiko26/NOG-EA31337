"""XAUUSD USD DEMO: cash loss ceiling $10, target $10. Default help, not trading.

--check / --install / --status: NO OpenAI requests, NO broker orders.
--run: paid PREVIEW; --run --demo-orders: explicit DEMO transport.
Paid budgets are mode-separated: PREVIEW=3 attempts, DEMO_SEND=1 attempt.\nThe append-only ledger is preserved; the EA defaults to no orders.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import threading
import time
import cash_risk as risk

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'cash_demo.sqlite3'
HOST, PORT, LEGACY_CAP = '127.0.0.1', 8765, 3
MODE_CAPS = {'PREVIEW':3,'DEMO_SEND':1}
EA_FILES = ('NOG_CashDemo.mq5', 'NOG_CashMath.mqh')
ERROR_CODES = {'insufficient_quota','credit_balance_exhausted','rate_limit_exceeded',
               'organization_usage_limit_exceeded','organization_spend_limit_exceeded',
               'project_spend_limit_exceeded','invalid_api_key','model_not_found'}


def log(message):
    print(time.strftime('[%Y-%m-%d %H:%M:%S UTC] ',time.gmtime())+message,flush=True)


def encode(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False)


class Ledger:
    """Persistent attempts with separate immutable PREVIEW/DEMO_SEND caps. A reserved bar is never retried."""
    def __init__(self,path: Path,spec: dict):
        path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=5)
        try:
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY CHECK(id=1),spec TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY CHECK(id=1),spec TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts(bar INTEGER PRIMARY KEY,sid TEXT UNIQUE,
                    mode TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,result TEXT);
            ''')
            text=encode({**spec,'cash_version':risk.VERSION,'api_cap':LEGACY_CAP,'loss_usd':10,'profit_usd':10})
            policy=encode({'PREVIEW':MODE_CAPS['PREVIEW'],'DEMO_SEND':MODE_CAPS['DEMO_SEND']})
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO meta VALUES(1,?)',(text,))
                self.db.execute('INSERT OR IGNORE INTO policy VALUES(1,?)',(policy,))
            if self.db.execute('SELECT spec FROM meta WHERE id=1').fetchone()[0]!=text:
                raise ValueError('FROZEN_SETTINGS_CHANGED_PRESERVE_LEDGER')
            if self.db.execute('SELECT spec FROM policy WHERE id=1').fetchone()[0]!=policy:
                raise ValueError('FROZEN_BUDGET_POLICY_CHANGED_PRESERVE_LEDGER')
        except BaseException:
            self.db.close();raise

    def close(self): self.db.close()
    def count(self,mode=None):
        if mode is None:return self.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
        if mode not in MODE_CAPS:raise ValueError('INVALID_MODE')
        return self.db.execute('SELECT COUNT(*) FROM attempts WHERE mode=?',(mode,)).fetchone()[0]
    def budget(self,mode):
        if mode not in MODE_CAPS:raise ValueError('INVALID_MODE')
        return self.count(mode),MODE_CAPS[mode]
    def blocked(self):
        return self.db.execute("SELECT 1 FROM attempts WHERE status!='SUCCESS' LIMIT 1").fetchone() is not None

    def reserve(self,bar:int,mode:str,payload:dict):
        if type(bar) is not int or bar<=0 or mode not in MODE_CAPS:
            raise ValueError('INVALID_RESERVATION')
        text=encode(payload)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.blocked() or self.count(mode)>=MODE_CAPS[mode] or self.db.execute('SELECT 1 FROM attempts WHERE bar=?',(bar,)).fetchone():
                return None
            sid=secrets.token_hex(16)
            self.db.execute("INSERT INTO attempts VALUES(?,?,?,'STARTED',?,NULL)",(bar,sid,mode,text))
            return sid
        finally:self.db.commit()

    def finish(self,bar:int,result:dict):
        if result.get('status') not in {'SUCCESS','ERROR','REFUSED','INVALID','INCOMPLETE'}:
            raise ValueError('INVALID_FINISH_STATUS')
        with self.db:
            cur=self.db.execute("UPDATE attempts SET status=?,result=? WHERE bar=? AND status='STARTED'",
                                (result['status'],encode(result),bar))
            if cur.rowcount!=1: raise ValueError('UNRESERVED_OR_FINISHED_ATTEMPT')

def safe_error(exc):
    code=getattr(exc,'code',None);body=getattr(exc,'body',None)
    if code is None and isinstance(body,dict):
        nested=body.get('error',body)
        if isinstance(nested,dict):code=nested.get('code')
    http=getattr(exc,'status_code',None)
    name=type(exc).__name__
    return {'status':'ERROR','error_type':name if re.fullmatch('[A-Za-z_]{1,80}',name) else 'APIError',
            'http_status':http if type(http) is int else None,
            'error_code':code if isinstance(code,str) and code in ERROR_CODES else 'UNKNOWN'}


def require_usd_demo(feed):
    obs=feed.observe();mt5=feed.mt5
    account,info=mt5.account_info(),mt5.symbol_info('XAUUSD')
    if (account is None or account.trade_mode!=getattr(mt5,'ACCOUNT_TRADE_MODE_DEMO',0)
        or account.currency!='USD' or feed.config.symbol!='XAUUSD' or info is None or info.currency_profit!='USD'):
        raise ValueError('XAUUSD_USD_DEMO_REQUIRED')
    if not risk.positive(account.equity,account.balance) or min(account.equity,account.balance)<100:
        raise ValueError('DEMO_BALANCE_EQUITY_BELOW_100')
    return obs,account


def preflight(feed,commission=None,fixed_fee=None):
    obs,_=require_usd_demo(feed);payload=feed.payload(obs)
    atr=payload['candles'][-1]['atr14']
    known=commission is not None and fixed_fee is not None
    if (commission is None)!=(fixed_fee is None):raise ValueError('SUPPLY_BOTH_FEE_ESTIMATES_OR_NEITHER')
    if known and any(type(v) not in (int,float) or not risk.math.isfinite(v) or v<0 for v in (commission,fixed_fee)):
        raise ValueError('INVALID_FEE_ESTIMATE')
    log('CASH_CHECK | DEMO USD | XAUUSD M5 | loss_ceiling=10.00 | profit_target=10.00')
    log('REFERENCE: $10 equals 10% of $100. Planned cash targets are NOT guarantees.')
    if not known:log('COMMISSION_UNKNOWN | price-only illustration assumes zero fees; NOT permission to send')
    for buy in (True,False):
        name='BUY' if buy else 'SELL'
        try:
            p=risk.broker_plan(feed.mt5,'XAUUSD',obs.bid,obs.ask,atr,buy,
                               commission if known else 0.,fixed_fee if known else 0.)
            log(f'{name} | lot={p.volume:.8f} | SL={p.sl:.8f} | TP={p.tp:.8f} | '
                f'estimated_stressed_loss={p.stressed_loss:.4f} USD | '
                f'estimated_stressed_profit={p.stressed_profit:.4f} USD | fee={p.fee:.4f}')
        except ValueError as exc:
            log(f'{name} | REJECT | {exc}')
    log('CHECK_DONE | OPENAI_CALLS=0 | NO_ORDER | NO_LEDGER_WRITE | not an OrderCheck or fill guarantee')


def paths(feed):
    info=feed.mt5.terminal_info()
    if info is None or not info.data_path:raise ValueError('TERMINAL_DATA_FOLDER_UNAVAILABLE')
    base=Path(info.data_path)/'MQL5'
    if not base.is_dir():raise ValueError('MQL5_FOLDER_MISSING')
    return base/'Experts',base/'Files'/'NOG_CashDemo'/'bridge.token'


def read_token(path):
    token=path.read_text(encoding='ascii').strip()
    if not re.fullmatch('[0-9a-f]{64}',token):raise ValueError('INVALID_LOCAL_TOKEN')
    return token


def install(feed,source=None):
    require_usd_demo(feed)
    source=ROOT/'mt5' if source is None else source
    experts,token_path=paths(feed)
    if not all((source/name).is_file() for name in EA_FILES):raise ValueError('PULL_MISSING_EA_FILES')
    experts.mkdir(parents=True,exist_ok=True)
    for name in EA_FILES:
        src,dst=source/name,experts/name
        if dst.exists() and dst.read_bytes()!=src.read_bytes():
            shutil.copy2(dst,dst.with_name(dst.name+'.backup.'+str(time.time_ns())))
        shutil.copy2(src,dst)
    token_path.parent.mkdir(parents=True,exist_ok=True)
    if not token_path.exists():
        with token_path.open('x',encoding='ascii') as stream:stream.write(secrets.token_hex(32))
        try:token_path.chmod(0o600)
        except OSError:pass
    read_token(token_path)
    log(f'INSTALLED | {experts/EA_FILES[0]} | Compile F7; default PREVIEW')
    log('TOKEN_READY_NOT_PRINTED | OPENAI_CALLS=0 | NO_ORDER | old files preserved')


def packet(mode,sid,action,issued,expiry,bar,login,atr,bid,ask):
    if mode not in {'PREVIEW','DEMO_SEND'} or action not in {'BUY','SELL','WAIT'}:
        raise ValueError('INVALID_MODE_ACTION')
    if not isinstance(sid,str) or not re.fullmatch('[a-f0-9]{32}',sid):raise ValueError('INVALID_ID')
    if any(type(v) is not int or not 0<v<10**12 for v in (issued,expiry,bar,login)) or not 0<expiry-issued<=30:
        raise ValueError('INVALID_TIME_OR_LOGIN')
    if not risk.positive(atr,bid,ask) or ask<bid:raise ValueError('INVALID_PRICES')
    return f'NOG_CASH_V1;{mode};{sid};XAUUSD;M5;{action};{issued};{expiry};{bar};{login};{atr:.8f};{bid:.8f};{ask:.8f}'.encode('ascii')


def handler_for(state,token,identity):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):super().setup();self.connection.settimeout(2)
        def do_GET(self):
            if self.path!='/signal':self.send_error(404);return
            value=self.headers.get('X-NOG-Token','')
            valid=(len(value)==64 and value.isascii() and secrets.compare_digest(value,token)
                and self.headers.get('X-NOG-Receiver')=='CASH_DEMO_V1'
                and self.headers.get('X-NOG-Account')==identity[0]
                and self.headers.get('X-NOG-Server')==identity[1]
                and self.headers.get('X-NOG-Symbol')=='XAUUSD'
                and self.headers.get('X-NOG-Timeframe')=='M5')
            if not valid:self.send_error(403);return
            mono,wall=time.monotonic(),time.time();state.receiver_seen(mono)
            body=state.current(mono,wall)
            self.send_response(200 if body else 204)
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Type','text/plain; charset=us-ascii')
            self.send_header('Content-Length',str(len(body) if body else 0));self.end_headers()
            if body:self.wfile.write(body)
        def log_message(self,*args):pass
    return Handler


def analyse(ledger,state,client,core,feed,mode,login,obs,payload,event_m,event_w,
            clocks=(time.monotonic,time.time)):
    mono,wall=clocks;state.clear();start_m,start_w=mono(),wall()
    if (payload.get('latest_closed_bar_raw')!=obs.closed or not core.nondirectional_gate(payload)
        or not state.receiver_ready(start_m) or not 0<=start_m-event_m<=8
        or abs(start_w-event_w-(start_m-event_m))>3):
        return {'status':'SKIPPED','delivery':'QUALITY_FRESHNESS_OR_RECEIVER'}
    sid=ledger.reserve(obs.closed,mode,payload)
    if sid is None:return {'status':'SKIPPED','delivery':'LEDGER_BLOCKED_OR_LIMIT'}
    try:result=core.request_analysis(client,feed.config.model,payload)
    except Exception as exc:result=safe_error(exc)
    result.update(delivery='NOT_PUBLISHED',mode=mode);body=None
    if result['status']=='SUCCESS':
        try:
            core.validate_signal(result['signal'])
            after,account=require_usd_demo(feed);end_m,end_w=mono(),wall();elapsed=end_m-event_m
            if not 0<=elapsed<=40 or abs(end_w-event_w-elapsed)>3:raise ValueError('WITHHELD_TIME')
            if (after.closed,after.forming,after.source_id,after.point)!=(obs.closed,obs.forming,obs.source_id,obs.point):
                raise ValueError('WITHHELD_SOURCE_BAR')
            if account.login!=login or after.tick_msc<obs.tick_msc or (after.tick_msc==obs.tick_msc and elapsed>5):
                raise ValueError('WITHHELD_SOURCE_TICK')
            if (after.ask-after.bid)/payload['candles'][-1]['atr14']>0.12 or not state.receiver_ready(end_m):
                raise ValueError('WITHHELD_SPREAD_RECEIVER')
            issued=int(end_w);expiry=min(issued+30,int(event_w+40))
            if expiry<=issued:raise ValueError('WITHHELD_EXPIRY')
            body=packet(mode,sid,result['signal']['action'],issued,expiry,obs.closed,login,
                        payload['candles'][-1]['atr14'],obs.bid,obs.ask)
            result.update(delivery='READY_FOR_EA',expires_at=expiry,issued_at=issued)
        except Exception:
            result['delivery']='WITHHELD_RECHECK_FAILED'
    result['elapsed_seconds']=max(0,mono()-start_m)
    ledger.finish(obs.closed,result)
    if body is not None:state.publish(body,obs.closed,result['expires_at'],mono(),wall())
    return result


def implementation():
    names=('cash_demo.py','cash_risk.py','live_ai_core.py','live_ai_bridge.py')
    return {n:hashlib.sha256((ROOT/n).read_text(encoding='utf-8').encode()).hexdigest() for n in names}


def run(feed,core,mode):
    from openai import OpenAI
    obs,account=require_usd_demo(feed);_,token_path=paths(feed);token=read_token(token_path)
    identity=(str(account.login),str(account.server))
    state=core.BridgeState('XAUUSD')
    server=HTTPServer((HOST,PORT),handler_for(state,token,identity))
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    ledger=client=None;started=False
    try:
        ledger=Ledger(DB,{'symbol':'XAUUSD','model':feed.config.model,'source_id':obs.source_id,
                          'implementation':implementation(),'instructions':core.INSTRUCTIONS,'schema':core.SCHEMA})
        if ledger.blocked():raise ValueError('UNRESOLVED_ATTEMPT_USE_STATUS_PRESERVE_LEDGER')
        used,cap=ledger.budget(mode)
        if used>=cap:log(f'API_CAP_REACHED | {mode}={used}/{cap} | no new request');return
        key=os.getenv('OPENAI_API_KEY','').strip()
        if not key:raise ValueError('OPENAI_API_KEY_MISSING')
        client=OpenAI(api_key=key,base_url='https://api.openai.com/v1',max_retries=0,timeout=25.)
        worker.start();started=True
        log(f'CASH_BRIDGE_READY | {mode} | XAUUSD M5 | MODE_BUDGET={ledger.count(mode)}/{MODE_CAPS[mode]}')
        log('Cash targets $10/$10; actual outcome not guaranteed. Ctrl+C stops signals, NOT open positions.')
        gate=core.TransitionGate();previous='';heartbeat=time.monotonic()
        while True:
            m,w=time.monotonic(),time.time();message='WAIT_NEW_CLOSED_CANDLE'
            try:
                obs,account=require_usd_demo(feed);event,message=gate.observe(obs,m,w)
                state.maintain(obs.closed,gate.tick_is_advancing(m),m,w)
                if not state.receiver_ready(m):
                    gate.reset();state.clear();message='WAIT_CASH_EA | no API before matching receiver polls'
                elif ledger.count(mode)>=MODE_CAPS[mode]:message=f'API_CAP_REACHED | {mode}={ledger.count(mode)}/{MODE_CAPS[mode]}; Ctrl+C to stop'
                elif event is not None:
                    payload=feed.payload(obs)
                    result=analyse(ledger,state,client,core,feed,mode,account.login,obs,payload,m,w)
                    log(f"OPENAI_RESULT | {result['status']} | action={result.get('signal',{}).get('action','-')} | "
                        f"delivery={result['delivery']} | {mode.lower()}={ledger.count(mode)}/{MODE_CAPS[mode]}")
                    if result['status'] not in {'SUCCESS','SKIPPED'}:
                        log(f"STOP_AFTER_ERROR | http={result.get('http_status','-')} | code={result.get('error_code','-')}");break
                    gate.reset()
                    if result['delivery']=='READY_FOR_EA':gate.progress_mono=time.monotonic()
            except (core.GuardError,ValueError):
                gate.reset();state.clear();message='FEED_GUARD_RESYNC | no signal published'
            if message!=previous:log(message);previous=message
            if time.monotonic()-heartbeat>=30:
                log(f'HEARTBEAT | {message} | {mode.lower()}={ledger.count(mode)}/{MODE_CAPS[mode]}');heartbeat=time.monotonic()
            time.sleep(core.POLL_SECONDS)
    finally:
        state.clear()
        if started:server.shutdown();worker.join(timeout=3)
        server.server_close()
        if client is not None:client.close()
        if ledger is not None:ledger.close()


def status():
    if not DB.is_file():log('CASH_LEDGER_NOT_STARTED | API_CALLS=0');return
    db=sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        rows=list(db.execute('SELECT bar,mode,status,result FROM attempts ORDER BY bar'))
        for bar,mode,st,text in rows:
            r=json.loads(text) if text else {}
            log(f"bar={bar} | {mode} | {st} | delivery={r.get('delivery','UNKNOWN')} | action={r.get('signal',{}).get('action','-')} | code={r.get('error_code','-')}")
        preview=sum(1 for _,mode,_,_ in rows if mode=='PREVIEW')
        demo=sum(1 for _,mode,_,_ in rows if mode=='DEMO_SEND')
        log(f'BUDGET | PREVIEW={preview}/{MODE_CAPS["PREVIEW"]} | DEMO_SEND={demo}/{MODE_CAPS["DEMO_SEND"]} | TOTAL={len(rows)} | API_CALLS=0 | ledger only')
    finally:db.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__);mode=parser.add_mutually_exclusive_group()
    for name in ('check','install','run','status'):mode.add_argument('--'+name,action='store_true')
    parser.add_argument('--demo-orders',action='store_true')
    parser.add_argument('--commission-per-lot',type=float,default=None)
    parser.add_argument('--fixed-fee',type=float,default=None)
    args=parser.parse_args()
    if args.demo_orders and not args.run:parser.error('--demo-orders requires --run')
    if not any((args.check,args.install,args.run,args.status)):parser.print_help();return 0
    if (args.commission_per_lot is not None or args.fixed_fee is not None) and not args.check:
        parser.error('Fee options are read-only --check estimates; configure verified fees separately in EA')
    feed=None
    try:
        if args.status:status();return 0
        import live_ai_core as core
        from live_ai_bridge import MT5Feed,config_from_env
        import MetaTrader5 as mt5
        # Dedicated XAUUSD program: never inherits the temporary EURUSD comparison.
        config=replace(config_from_env(),symbol='XAUUSD')
        feed=MT5Feed(mt5,config);feed.connect();require_usd_demo(feed)
        if args.check:preflight(feed,args.commission_per_lot,args.fixed_fee)
        elif args.install:install(feed)
        else:run(feed,core,'DEMO_SEND' if args.demo_orders else 'PREVIEW')
        return 0
    except KeyboardInterrupt:log('STOPPED | existing positions NOT closed; inspect MT5 Trade tab');return 0
    except Exception as exc:
        code=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[A-Z0-9_]{1,120}',str(exc)) else type(exc).__name__
        log(f'CASH_STOP | {code} | preserve ledgers; no automatic retry');return 1
    finally:
        if feed is not None:feed.close()


if __name__=='__main__':raise SystemExit(main())
