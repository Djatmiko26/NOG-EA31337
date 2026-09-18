"""Cash Ensemble V3.1.1: weighted regime-aware logics -> independent AI confirmation -> PREVIEW.

Research-only safety:
- separate ledger: data/cash_ensemble_v31.sqlite3
- exactly one newly closed M5 candle evaluated per --run
- local WAIT/VETO uses zero OpenAI calls
- directional consensus may use at most one OpenAI call per --run
- AI must independently agree with local direction and meet the regime-aware confidence threshold
- packets are PREVIEW only; no broker order functions or DEMO_SEND mode
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
import ensemble_weighted_v31 as ensemble

ROOT=Path(__file__).resolve().parent
DB=ROOT/"data"/"cash_ensemble_v31.sqlite3"
HOST,PORT="127.0.0.1",8765
VERSION="cash-ensemble-v31"
EVENT_CAP=40
API_CAP=12
DELIVERY_WINDOW_SECONDS=6


def log(message):
    print(time.strftime("[%Y-%m-%d %H:%M:%S UTC] ",time.gmtime())+message,flush=True)


def encode(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False)


def implementation():
    names=("cash_ensemble_v31.py","ensemble_weighted_v31.py","cash_demo.py",
           "cash_risk.py","live_ai_core.py","live_ai_bridge.py")
    return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names}


class Ledger:
    """Append-only evidence. Any uncertain API attempt blocks automatic continuation."""
    def __init__(self,path:Path,spec:dict):
        path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=5)
        try:
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    spec TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events(
                    bar INTEGER PRIMARY KEY,
                    sid TEXT UNIQUE NOT NULL,
                    local_json TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    api_used INTEGER NOT NULL CHECK(api_used IN (0,1)),
                    result TEXT
                );
            """)
            frozen=encode({**spec,"version":VERSION,"event_cap":EVENT_CAP,
                           "api_cap":API_CAP,"ensemble_version":ensemble.VERSION})
            with self.db:self.db.execute("INSERT OR IGNORE INTO meta VALUES(1,?)",(frozen,))
            saved=self.db.execute("SELECT spec FROM meta WHERE id=1").fetchone()[0]
            if saved!=frozen:raise ValueError("V31_FROZEN_SETTINGS_CHANGED_PRESERVE_LEDGER")
        except BaseException:
            self.db.close();raise

    def close(self):self.db.close()
    def event_count(self):return int(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    def api_count(self):return int(self.db.execute("SELECT COALESCE(SUM(api_used),0) FROM events").fetchone()[0])
    def blocked(self):
        return self.db.execute("SELECT 1 FROM events WHERE status!='SUCCESS' LIMIT 1").fetchone() is not None

    def reserve(self,bar:int,decision:ensemble.WeightedDecision,payload:dict):
        if type(bar) is not int or bar<=0:raise ValueError("V31_INVALID_BAR")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.blocked() or self.event_count()>=EVENT_CAP:
                return None
            if self.db.execute("SELECT 1 FROM events WHERE bar=?",(bar,)).fetchone():
                return None
            sid=secrets.token_hex(16)
            self.db.execute(
                "INSERT INTO events VALUES(?,?,?,?, 'STARTED',0,NULL)",
                (bar,sid,encode(decision.to_dict()),encode(payload))
            )
            return sid
        finally:self.db.commit()

    def mark_api(self,bar:int):
        with self.db:
            cur=self.db.execute(
                "UPDATE events SET api_used=1 WHERE bar=? AND status='STARTED' AND api_used=0",
                (bar,)
            )
            if cur.rowcount!=1:raise ValueError("V31_API_RESERVATION_FAILED")

    def finish(self,bar:int,result:dict,status="SUCCESS"):
        if status not in {"SUCCESS","ERROR","REFUSED","INVALID","INCOMPLETE"}:
            raise ValueError("V31_INVALID_STATUS")
        with self.db:
            cur=self.db.execute(
                "UPDATE events SET status=?,result=? WHERE bar=? AND status='STARTED'",
                (status,encode(result),bar)
            )
            if cur.rowcount!=1:raise ValueError("V31_UNRESERVED_OR_FINISHED_EVENT")


def status():
    if not DB.is_file():
        log(f"V31_LEDGER_NOT_STARTED | EVENTS=0/{EVENT_CAP} | API=0/{API_CAP} | API_CALLS=0")
        return
    db=sqlite3.connect(DB.resolve().as_uri()+"?mode=ro",uri=True)
    try:
        rows=list(db.execute(
            "SELECT bar,status,api_used,local_json,result FROM events ORDER BY bar"
        ))
        api=sum(int(r[2]) for r in rows);confirmed=local_wait=ai_block=0
        for bar,st,used,local_text,result_text in rows:
            local=json.loads(local_text)
            result=json.loads(result_text) if result_text else {}
            final=result.get("final_action","-")
            ai=result.get("ai_signal") or {}
            if final in {"BUY","SELL"}:confirmed+=1
            if result.get("reason")=="LOCAL_WAIT":local_wait+=1
            if result.get("reason") in {"AI_WAIT","AI_DISAGREE","AI_LOW_CONFIDENCE"}:ai_block+=1
            log(
                f"V31 | bar={bar} | {st} | regime={local.get('regime','-')} | "
                f"local={local.get('action','-')} | "
                f"score={local.get('buy_score',0):.2f}B/{local.get('sell_score',0):.2f}S | "
                f"core={local.get('core_count',0)} | rr={local.get('suggested_rr',0):.2f}R | "
                f"api={used} | ai={ai.get('action','-')}:{ai.get('confidence','-')} | "
                f"final={final} | delivery={result.get('delivery','-')}"
            )
        log(
            f"V31_STATUS | EVENTS={len(rows)}/{EVENT_CAP} | API={api}/{API_CAP} | "
            f"CONFIRMED_DIRECTIONAL={confirmed} | LOCAL_WAIT={local_wait} | "
            f"AI_BLOCK={ai_block} | API_CALLS=0"
        )
    finally:db.close()


def _log_decision(decision):
    log(
        f"V31_WEIGHTED | action={decision.action} | regime={decision.regime} | "
        f"score={decision.buy_score:.2f}B/{decision.sell_score:.2f}S | "
        f"margin={decision.margin:.2f} | core={decision.core_count} | "
        f"consensus={decision.consensus:.3f} | rr_suggest={decision.suggested_rr:.2f}R | "
        f"ai_min={decision.ai_min_confidence} | {decision.reason}"
    )
    for v in decision.votes:
        log(
            f"V31_VOTE | {v.name} | {v.signal} | weight={v.weight:.2f} | "
            f"strength={v.strength:.2f} | {v.reason}"
        )


def _fresh_publish_recheck(feed,state,core,before,payload,event_m,event_w):
    after,_=base.require_usd_demo(feed)
    end_m,end_w=time.monotonic(),time.time()
    elapsed=end_m-event_m
    if not 0<=elapsed<=40:return None
    if abs((end_w-event_w)-elapsed)>3:return None
    if (after.closed,after.forming,after.source_id,after.point)!=(before.closed,before.forming,before.source_id,before.point):
        return None
    if after.tick_msc<before.tick_msc or (after.tick_msc==before.tick_msc and elapsed>5):
        return None
    if (after.ask-after.bid)/payload["candles"][-1]["atr14"]>core.MAX_SPREAD_ATR:return None
    if not state.receiver_ready(end_m):return None
    return after,end_m,end_w


def process_event(ledger,state,client,core,feed,account,before,payload,decision,event_m,event_w):
    sid=ledger.reserve(before.closed,decision,payload)
    if sid is None:
        return {"status":"SKIPPED","delivery":"LEDGER_BLOCKED_DUPLICATE_OR_CAP","final_action":"WAIT"}

    if decision.action=="WAIT":
        result={"status":"SUCCESS","reason":"LOCAL_WAIT","delivery":"NOT_PUBLISHED",
                "final_action":"WAIT","ensemble":decision.to_dict(),"ai_signal":None}
        ledger.finish(before.closed,result)
        return result

    if ledger.api_count()>=API_CAP:
        result={"status":"SUCCESS","reason":"API_CAP","delivery":"NOT_PUBLISHED",
                "final_action":"WAIT","ensemble":decision.to_dict(),"ai_signal":None}
        ledger.finish(before.closed,result)
        return result

    now_m,now_w=time.monotonic(),time.time()
    if (not core.nondirectional_gate(payload) or not 0<=now_m-event_m<=8
            or abs((now_w-event_w)-(now_m-event_m))>3 or not state.receiver_ready(now_m)):
        result={"status":"SUCCESS","reason":"STALE_OR_NO_RECEIVER","delivery":"NOT_PUBLISHED",
                "final_action":"WAIT","ensemble":decision.to_dict(),"ai_signal":None}
        ledger.finish(before.closed,result)
        return result

    ledger.mark_api(before.closed)
    try:
        ai_result=core.request_analysis(client,feed.config.model,payload)
    except Exception as exc:
        err=base.safe_error(exc)
        result={**err,"reason":"API_ERROR","delivery":"NOT_PUBLISHED",
                "final_action":"WAIT","ensemble":decision.to_dict(),"ai_signal":None}
        ledger.finish(before.closed,result,status=err["status"])
        return result

    status_name=ai_result.get("status")
    if status_name!="SUCCESS":
        result={**ai_result,"reason":"AI_"+str(status_name),"delivery":"NOT_PUBLISHED",
                "final_action":"WAIT","ensemble":decision.to_dict(),"ai_signal":None}
        ledger.finish(before.closed,result,status=status_name)
        return result

    ai=core.validate_signal(ai_result["signal"])
    if ai["action"]=="WAIT":reason="AI_WAIT";final="WAIT"
    elif ai["action"]!=decision.action:reason="AI_DISAGREE";final="WAIT"
    elif ai["confidence"]<decision.ai_min_confidence:reason="AI_LOW_CONFIDENCE";final="WAIT"
    else:reason="CONFIRMED";final=decision.action

    result={**ai_result,"status":"SUCCESS","reason":reason,"delivery":"NOT_PUBLISHED",
            "final_action":final,"ensemble":decision.to_dict(),"ai_signal":ai}

    checked=_fresh_publish_recheck(feed,state,core,before,payload,event_m,event_w)
    body=None
    if checked is not None:
        _,end_m,end_w=checked
        issued=int(end_w);expires=min(issued+30,int(event_w+40))
        if expires>issued:
            body=base.packet("PREVIEW",sid,final,issued,expires,before.closed,account.login,
                             payload["candles"][-1]["atr14"],before.bid,before.ask)
            result.update(delivery="READY_FOR_EA",issued_at=issued,expires_at=expires)
    ledger.finish(before.closed,result)
    if body is not None:
        state.publish(body,before.closed,result["expires_at"],time.monotonic(),time.time())
    return result


def run_one(feed,core):
    from openai import OpenAI

    obs,account=base.require_usd_demo(feed)
    _,token_path=base.paths(feed);token=base.read_token(token_path)
    identity=(str(account.login),str(account.server))
    state=core.BridgeState("XAUUSD")
    server=client=worker=ledger=None;started=False
    try:
        ledger=Ledger(DB,{"symbol":"XAUUSD","model":feed.config.model,
                          "source_id":obs.source_id,"implementation":implementation(),
                          "instructions":core.INSTRUCTIONS,"schema":core.SCHEMA})
        if ledger.blocked():raise ValueError("V31_UNRESOLVED_EVENT_USE_STATUS")
        if ledger.event_count()>=EVENT_CAP:
            log(f"V31_EVENT_CAP_REACHED | {ledger.event_count()}/{EVENT_CAP}");return

        key=os.getenv("OPENAI_API_KEY","").strip()

        ThreadingHTTPServer.allow_reuse_address=True
        ThreadingHTTPServer.daemon_threads=True
        server=ThreadingHTTPServer((HOST,PORT),base.handler_for(state,token,identity))
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        if key:
            client=OpenAI(api_key=key,base_url="https://api.openai.com/v1",max_retries=0,timeout=25.)
        worker.start();started=True
        log(
            f"V31_READY | WEIGHTED_10_LOGICS | PREVIEW_ONLY | events={ledger.event_count()}/{EVENT_CAP} | "
            f"api={ledger.api_count()}/{API_CAP} | max_new_event=1"
        )

        gate=core.TransitionGate();previous="";heartbeat=time.monotonic()
        initial=ledger.event_count()
        while ledger.event_count()==initial:
            m,w=time.monotonic(),time.time();message="WAIT_NEW_CLOSED_CANDLE"
            try:
                obs,account=base.require_usd_demo(feed)
                event,message=gate.observe(obs,m,w)
                state.maintain(obs.closed,gate.tick_is_advancing(m),m,w)
                if not state.receiver_ready(m):
                    gate.reset();state.clear();message="V31_WAIT_CASH_EA"
                elif event is not None:
                    payload=feed.payload(obs)
                    decision=ensemble.evaluate(payload)
                    _log_decision(decision)
                    if decision.action!="WAIT" and ledger.api_count()<API_CAP and client is None:
                        raise ValueError("OPENAI_API_KEY_MISSING")
                    result=process_event(
                        ledger,state,client,core,feed,account,obs,payload,decision,m,w
                    )
                    log(
                        f"V31_RESULT | status={result.get('status','-')} | "
                        f"local={decision.action} | final={result.get('final_action','-')} | "
                        f"reason={result.get('reason','-')} | delivery={result.get('delivery','-')} | "
                        f"events={ledger.event_count()}/{EVENT_CAP} | api={ledger.api_count()}/{API_CAP}"
                    )
                    if result.get("delivery")=="READY_FOR_EA":
                        log(f"V31_DELIVERY_WINDOW | {DELIVERY_WINDOW_SECONDS}s | no second event")
                        time.sleep(DELIVERY_WINDOW_SECONDS)
                    break
            except core.GuardError:
                gate.reset();state.clear();message="V31_FEED_GUARD_RESYNC"
            except ValueError as exc:
                if str(exc).startswith("W31_"):
                    gate.reset();state.clear();message="V31_ENSEMBLE_INPUT_RESYNC"
                else:
                    raise
            if message!=previous:log(message);previous=message
            if time.monotonic()-heartbeat>=30:
                log(f"V31_HEARTBEAT | {message} | events={ledger.event_count()} | api={ledger.api_count()}")
                heartbeat=time.monotonic()
            time.sleep(core.POLL_SECONDS)
    finally:
        state.clear()
        if started:
            server.shutdown();worker.join(timeout=3)
        if server is not None:server.server_close()
        if client is not None:client.close()
        if ledger is not None:ledger.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    group.add_argument("--run",action="store_true")
    group.add_argument("--status",action="store_true")
    args=parser.parse_args()
    if not (args.run or args.status):parser.print_help();return 0
    if args.status:status();return 0

    feed=None
    try:
        import live_ai_core as core
        from live_ai_bridge import MT5Feed,config_from_env
        import MetaTrader5 as mt5
        config=replace(config_from_env(),symbol="XAUUSD")
        feed=MT5Feed(mt5,config);feed.connect();base.require_usd_demo(feed)
        run_one(feed,core);return 0
    except KeyboardInterrupt:
        log("V31_STOPPED | PREVIEW_ONLY | NO_ORDER_REQUESTED");return 0
    except Exception as exc:
        code=str(exc) if isinstance(exc,ValueError) and re.fullmatch(r"[A-Z0-9_]{1,120}",str(exc)) else type(exc).__name__
        log(f"V31_STOP | {code} | preserve V31 ledger");return 1
    finally:
        if feed is not None:feed.close()


if __name__=="__main__":
    raise SystemExit(main())
