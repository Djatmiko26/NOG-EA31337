"""Synthetic data, mock model/feed and temporary files only. No paid API or orders."""
import ast
import contextlib
import copy
import http.client
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import guarded_demo as app


def observation():
    o=NS(closed=1789700100,forming=1789700400,source_id='source',tick_msc=1789700401000,
         bid=4300.,ask=4300.2,point=.01)
    o.validate=Mock()
    return o


def payload():
    return {'latest_closed_bar_raw':1789700100,'candles':[{'atr14':4.}],
            'current_quote':{'bid':4300.,'ask':4300.2}}


def signal():
    return {'status':'SUCCESS','signal':{'action':'BUY','confidence':65,'market_regime':'RANGE','reason':'synthetic'}}


class State:
    def __init__(self): self.packet=None; self.seen=False; self.ready=True
    def clear(self): self.packet=None
    def receiver_ready(self,m): return self.ready
    def receiver_seen(self,m): self.seen=True
    def current(self,m,w): return self.packet
    def publish(self,*args): self.packet=args[0]


class GuardedDemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.path=self.root/'ledger.sqlite3'
        self.ledger=app.Ledger(self.path,{'test':1})
        self.addCleanup(self.ledger.close)

    def test_cumulative_cap_survives_restart(self):
        for n in range(3):
            self.assertTrue(self.ledger.reserve(100+n,{}))
            self.ledger.finish(100+n,signal())
        other=app.Ledger(self.path,{'test':1})
        try:
            self.assertEqual(other.count(),3)
            self.assertIsNone(other.reserve(104,{}))
        finally: other.close()

    def test_settings_change_does_not_reset_budget(self):
        self.ledger.reserve(100,{})
        with self.assertRaisesRegex(ValueError,'SETTINGS'):
            app.Ledger(self.path,{'test':2})
        self.assertEqual(self.ledger.count(),1)

    def test_reservation_durable_in_second_connection(self):
        self.ledger.reserve(100,{})
        other=app.Ledger(self.path,{'test':1})
        try:
            self.assertTrue(other.blocked())
            self.assertIsNone(other.reserve(100,{}))
            self.assertIsNone(other.reserve(101,{}))
        finally: other.close()

    def test_duplicate_success_never_reserved_again(self):
        self.ledger.reserve(100,{})
        self.ledger.finish(100,signal())
        self.assertIsNone(self.ledger.reserve(100,{}))
        self.assertEqual(self.ledger.count(),1)

    def test_error_stops_new_requests(self):
        self.ledger.reserve(100,{})
        self.ledger.finish(100,{'status':'ERROR','http_status':429})
        self.assertTrue(self.ledger.blocked())
        self.assertIsNone(self.ledger.reserve(101,{}))

    def test_cannot_forge_unreserved_result(self):
        with self.assertRaises(ValueError): self.ledger.finish(100,signal())

    def test_cannot_overwrite_finished_result(self):
        self.ledger.reserve(100,{})
        self.ledger.finish(100,signal())
        with self.assertRaises(ValueError): self.ledger.finish(100,{'status':'ERROR'})

    def test_nonfinite_payload_is_not_reserved(self):
        with self.assertRaises(ValueError): self.ledger.reserve(100,{'x':float('nan')})
        self.assertEqual(self.ledger.count(),0)

    def test_mode_packet_binding(self):
        for mode in ('PREVIEW','DEMO_SEND'):
            p=app.packet(mode,'a'*32,'XAUUSD','WAIT',1789700400,1789700430,1789700100,123,4.,100.,100.1)
            parts=p.decode().split(';')
            self.assertEqual(len(parts),13)
            self.assertEqual(parts[:2],['NOG_GUARDED_V1',mode])
            self.assertEqual(parts[5],'WAIT')
            self.assertEqual(parts[9],'123')

    def test_old_protocol_mode_refused(self):
        with self.assertRaises(ValueError): app.packet('AI_DRYRUN','a'*32,'XAUUSD','BUY',1,2,1,123,1.,100.,101.)

    def test_packet_input_validation(self):
        base=['PREVIEW','a'*32,'XAUUSD','BUY',1789700400,1789700430,1789700100,123,4.,100.,100.1]
        for index,value in [(1,'evil'),(2,'XAU;BUY'),(3,'TRADE'),(4,True),(5,1789700400),
                            (5,1789700431),(8,float('nan')),(9,0.),(10,99.)]:
            args=list(base); args[index]=value
            with self.subTest(index=index,value=value),self.assertRaises(ValueError): app.packet(*args)

    def test_safe_429_code_retained_without_secrets(self):
        exc=RuntimeError('sk-real-secret do not print')
        exc.status_code=429
        exc.body={'error':{'code':'credit_balance_exhausted','message':'sensitive'}}
        result=app.safe_error(exc)
        self.assertEqual(result['error_code'],'credit_balance_exhausted')
        self.assertNotIn('secret',json.dumps(result))
        self.assertNotIn('sensitive',json.dumps(result))

    def test_unknown_error_code_redacted(self):
        exc=RuntimeError('unknown')
        exc.code='sk-sensitive-untrusted'
        self.assertEqual(app.safe_error(exc)['error_code'],'UNKNOWN')

    def call_analysis(self,mode='PREVIEW',after_change=None,response=None,elapsed=4.,ready=True,error=None):
        before=observation(); after=observation(); after.tick_msc+=1000
        if after_change: after_change(after)
        feed=NS(config=NS(model='test-model',symbol='XAUUSD'),observe=Mock(return_value=after))
        core=NS(request_analysis=Mock(return_value=response or signal(),side_effect=error),
                nondirectional_gate=Mock(return_value=True))
        state=State(); state.ready=ready
        times=iter([10.,10.+elapsed,10.+elapsed])
        walls=iter([1789700400.,1789700400.+elapsed,1789700400.+elapsed,1789700400.+elapsed])
        result=app.analyse(self.ledger,state,Mock(),core,feed,mode,123,before,payload(),10.,1789700400.,
                           clocks=(lambda:next(times),lambda:next(walls)))
        return result,state,core

    def test_success_persists_before_publish(self):
        result,state,core=self.call_analysis()
        self.assertEqual(result['delivery'],'READY_FOR_EA')
        self.assertTrue(state.packet.startswith(b'NOG_GUARDED_V1;PREVIEW;'))
        self.assertEqual(self.ledger.count(),1)
        self.assertFalse(self.ledger.blocked())
        self.assertEqual(core.request_analysis.call_args.args[2],payload())

    def test_demo_packet_requires_explicit_mode(self):
        result,state,_=self.call_analysis(mode='DEMO_SEND')
        self.assertTrue(state.packet.startswith(b'NOG_GUARDED_V1;DEMO_SEND;'))

    def test_no_receiver_no_paid_attempt(self):
        result,state,core=self.call_analysis(ready=False)
        self.assertEqual(self.ledger.count(),0)
        core.request_analysis.assert_not_called()

    def test_late_reply_never_published(self):
        result,state,_=self.call_analysis(elapsed=41.)
        self.assertEqual(result['delivery'],'WITHHELD_RECHECK')
        self.assertIsNone(state.packet)

    def test_changed_bar_never_published(self):
        result,state,_=self.call_analysis(after_change=lambda o:setattr(o,'closed',o.closed+300))
        self.assertEqual(result['delivery'],'WITHHELD_RECHECK')
        self.assertIsNone(state.packet)

    def test_changed_account_never_published(self):
        result,state,_=self.call_analysis(after_change=lambda o:setattr(o,'source_id','another'))
        self.assertIsNone(state.packet)

    def test_spread_spike_never_published(self):
        result,state,_=self.call_analysis(after_change=lambda o:setattr(o,'ask',4302.))
        self.assertIsNone(state.packet)

    def test_tick_not_advancing_never_published(self):
        result,state,_=self.call_analysis(after_change=lambda o:setattr(o,'tick_msc',observation().tick_msc))
        self.assertIsNone(state.packet)

    def test_api_error_is_not_wait(self):
        exc=RuntimeError('secret'); exc.status_code=429
        result,state,_=self.call_analysis(error=exc)
        self.assertEqual(result['status'],'ERROR')
        self.assertNotIn('signal',result)
        self.assertIsNone(state.packet)
        self.assertTrue(self.ledger.blocked())

    def test_incomplete_not_published(self):
        result,state,_=self.call_analysis(response={'status':'INCOMPLETE'})
        self.assertIsNone(state.packet)
        self.assertTrue(self.ledger.blocked())

    def test_keyboard_interrupt_remains_uncertain(self):
        with self.assertRaises(KeyboardInterrupt): self.call_analysis(error=KeyboardInterrupt())
        self.assertTrue(self.ledger.blocked())
        self.assertEqual(self.ledger.count(),1)

    def test_ledger_does_not_touch_existing_pilots(self):
        p=self.root/'openai_pilot.sqlite3'; p.write_bytes(b'keep-me')
        self.ledger.reserve(100,{})
        self.assertEqual(p.read_bytes(),b'keep-me')

    def test_install_finds_active_terminal_and_preserves_token(self):
        mql=self.root/'terminal'/'MQL5'; mql.mkdir(parents=True)
        feed=NS(mt5=NS(terminal_info=lambda:NS(data_path=str(mql.parent))))
        source=self.root/'source'; source.mkdir()
        for name in app.EA_FILES: (source/name).write_text('test source')
        with contextlib.redirect_stdout(io.StringIO()):
            app.install(feed,source)
            _,path=app.installation_paths(feed)
            token=path.read_bytes()
            app.install(feed,source)
        self.assertEqual(token,path.read_bytes())
        self.assertEqual(len(token),64)
        self.assertTrue((mql/'Experts'/app.EA_FILES[0]).exists())

    def test_install_backs_up_changed_destination(self):
        mql=self.root/'terminal'/'MQL5'; mql.mkdir(parents=True)
        feed=NS(mt5=NS(terminal_info=lambda:NS(data_path=str(mql.parent))))
        source=self.root/'source'; source.mkdir()
        for name in app.EA_FILES: (source/name).write_text('source')
        with contextlib.redirect_stdout(io.StringIO()): app.install(feed,source)
        dst=mql/'Experts'/app.EA_FILES[0]; dst.write_text('old')
        with contextlib.redirect_stdout(io.StringIO()): app.install(feed,source)
        backups=list(dst.parent.glob(dst.name+'.backup.*'))
        self.assertEqual(backups[0].read_text(),'old')

    def test_invalid_token_refused(self):
        p=self.root/'token'; p.write_text('wrong')
        with self.assertRaises(ValueError): app.read_token(p)

    def test_mql_guards_and_no_python_order_path_static(self):
        root=Path(app.__file__).resolve().parent
        ea=(root/'mt5'/'NOG_GuardedDemo.mq5').read_text()
        self.assertIn('input bool InpEnableDemoOrders=false',ea)
        self.assertIn('ACCOUNT_TRADE_MODE_DEMO',ea)
        self.assertIn('if(s.mode!="DEMO_SEND"||!InpEnableDemoOrders)',ea)
        self.assertEqual(ea.count('OrderSend(request,result)'),1)
        self.assertIn('g_daily++; g_total++; g_halt=1;',ea)
        self.assertIn('if(!OrderCheck(request,checked)',ea)
        tree=ast.parse(Path(app.__file__).read_text())
        calls={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        self.assertNotIn('order_send',calls)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.state=State()
        self.token='a'*64
        self.server=app.HTTPServer(('127.0.0.1',0),app.handler_for(self.state,self.token,('123','Broker-Demo'),'XAUUSD'))
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self,headers=None,path='/signal'):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=2)
        try:
            conn.request('GET',path,headers=headers or {})
            res=conn.getresponse(); return res.status,res.read()
        finally: conn.close()

    def headers(self):
        return {'X-NOG-Receiver':'GUARDED_DEMO_V1','X-NOG-Token':self.token,
                'X-NOG-Account':'123','X-NOG-Server':'Broker-Demo','X-NOG-Symbol':'XAUUSD','X-NOG-Timeframe':'M5'}

    def test_unauthenticated_request_rejected(self):
        self.assertEqual(self.get()[0],403)
        self.assertFalse(self.state.seen)

    def test_wrong_account_rejected(self):
        h=self.headers(); h['X-NOG-Account']='124'
        self.assertEqual(self.get(h)[0],403)
        self.assertFalse(self.state.seen)

    def test_wrong_receiver_rejected(self):
        h=self.headers(); h['X-NOG-Receiver']='LIVE_DRYRUN_V1'
        self.assertEqual(self.get(h)[0],403)

    def test_good_receiver_empty_response(self):
        self.assertEqual(self.get(self.headers()),(204,b''))
        self.assertTrue(self.state.seen)

    def test_good_receiver_gets_exact_packet(self):
        self.state.packet=b'wire'
        self.assertEqual(self.get(self.headers()),(200,b'wire'))

    def test_unknown_path_404(self):
        self.assertEqual(self.get(self.headers(),'/else')[0],404)


if __name__=='__main__': unittest.main()
