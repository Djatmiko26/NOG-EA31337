"""Synthetic arithmetic, temporary ledgers, mock model/feed, and loopback only."""
import ast
import contextlib
import copy
import dataclasses
import io
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import cash_demo as app
import cash_risk as risk


def inputs(buy=True):
    return (buy,4338.,4338.2,5.07979775,.01,.1,.2,.01,.01,100.,100.,100.,0.,0.)


def fixture():
    account=NS(currency='USD',equity=100000.,balance=100000.,trade_mode=0,login=123,server='TEST-DEMO')
    info=NS(currency_profit='USD',trade_tick_size=.01,point=.01,volume_min=.01,volume_step=.01,
            volume_max=10.,volume_limit=0.,trade_stops_level=10)
    mt=Mock(ORDER_TYPE_BUY=0,ORDER_TYPE_SELL=1,ACCOUNT_TRADE_MODE_DEMO=0)
    mt.account_info.return_value=account;mt.symbol_info.return_value=info
    mt.order_calc_profit.side_effect=lambda typ,sym,v,a,b: (b-a)*v*100*(1 if typ==0 else -1)
    obs=NS(closed=1700000000,forming=1700000300,source_id='f'*64,point=.01,tick_msc=1700000300000,bid=4338.,ask=4338.2)
    feed=Mock(mt5=mt,config=NS(symbol='XAUUSD',model='mock-model'))
    feed.observe.return_value=obs
    payload={'latest_closed_bar_raw':obs.closed,'candles':[{'atr14':5.07979775}],
             'current_quote':{'bid':4338.,'ask':4338.2}}
    feed.payload.return_value=payload
    return feed,account,info,obs,payload


def signal(action='BUY'):
    return {'action':action,'confidence':61,'market_regime':'RANGE','reason':'Synthetic test.'}


class RiskTests(unittest.TestCase):
    def test_buy_and_sell_10_cash_targets(self):
        for buy in (True,False):
            p=risk.make_plan(*inputs(buy))
            self.assertAlmostEqual(p.volume,.01)
            self.assertLessEqual(p.stressed_loss,10+1e-8)
            self.assertGreaterEqual(p.stressed_profit,10-1e-8)
            self.assertLessEqual(abs(p.stressed_loss-10),.011)

    def test_fee_and_fixed_fee_are_accounted(self):
        a=list(inputs());a[-2:]=[7.,.2];p=risk.make_plan(*a)
        self.assertAlmostEqual(p.fee,p.volume*7+.2)
        self.assertLessEqual(p.stressed_loss,10+1e-8)
        self.assertGreaterEqual(p.stressed_profit,10-1e-8)

    def test_stop_not_tightened_inside_atr(self):
        for buy in (True,False):
            p=risk.make_plan(*inputs(buy))
            self.assertGreaterEqual(abs(p.entry-p.sl)+1e-8,1.5*5.07979775)

    def test_high_atr_is_rejected_not_forced_lot(self):
        a=list(inputs());a[3]=100.
        with self.assertRaises(ValueError):risk.make_plan(*a)

    def test_lot_cap_is_ceiling_not_fixed_point_one(self):
        p=risk.make_plan(*inputs());self.assertEqual(p.volume,.01)
        a=list(inputs());a[3]=.01;p=risk.make_plan(*a)
        self.assertLessEqual(p.volume,.10+1e-12)

    def test_tick_grid(self):
        for buy in (True,False):
            a=list(inputs(buy));a[4]=.25;p=risk.make_plan(*a)
            self.assertAlmostEqual(p.sl/.25,round(p.sl/.25))
            self.assertAlmostEqual(p.tp/.25,round(p.tp/.25))

    def test_invalid_inputs(self):
        for i in range(1,len(inputs())):
            for bad in (float('nan'),float('inf'),True):
                a=list(inputs());a[i]=bad
                with self.assertRaises(ValueError):risk.make_plan(*a)

    def test_unknown_negative_commission(self):
        a=list(inputs());a[-2]=-1
        with self.assertRaises(ValueError):risk.make_plan(*a)

    def test_fixed_fee_exhaustion(self):
        a=list(inputs());a[-1]=10
        with self.assertRaises(ValueError):risk.make_plan(*a)

    def test_broker_recheck(self):
        feed,*_=fixture()
        for buy in (True,False):
            p=risk.broker_plan(feed.mt5,'XAUUSD',4338,4338.2,5.07979775,buy,0,0)
            self.assertLessEqual(p.stressed_loss,10+1e-8)
        self.assertEqual(feed.mt5.order_calc_profit.call_count,8)
        feed.mt5.order_send.assert_not_called()

    def test_non_linear_broker_result_rejected(self):
        feed,*_=fixture()
        orig=feed.mt5.order_calc_profit.side_effect
        feed.mt5.order_calc_profit.side_effect=lambda t,s,v,a,b: orig(t,s,v,a,b)*(1.1 if abs(b-a)>1 else 1)
        with self.assertRaisesRegex(ValueError,'BROKER_FINAL_CASH_MISMATCH'):
            risk.broker_plan(feed.mt5,'XAUUSD',4338,4338.2,5.07979775,True,0,0)

    def test_currency_real_account_wrong_symbol_rejected(self):
        feed,account,*_=fixture()
        account.currency='EUR'
        with self.assertRaises(ValueError):risk.broker_plan(feed.mt5,'XAUUSD',4338,4338.2,5,True,0,0)
        account.currency='USD';account.trade_mode=2
        with self.assertRaises(ValueError):risk.broker_plan(feed.mt5,'XAUUSD',4338,4338.2,5,True,0,0)
        account.trade_mode=0
        with self.assertRaises(ValueError):risk.broker_plan(feed.mt5,'EURUSD',4338,4338.2,5,True,0,0)

    def test_equity_guard(self):
        feed,account,*_=fixture();account.equity=90
        with self.assertRaisesRegex(ValueError,'AT_LEAST_100'):
            risk.broker_plan(feed.mt5,'XAUUSD',4338,4338.2,5,True,0,0)

    def test_no_false_dollar_label_on_cent_account(self):
        feed,account,*_=fixture();account.currency='USC'
        with self.assertRaisesRegex(ValueError,'USD_DEMO'):app.require_usd_demo(feed)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'ledger.sqlite3';self.ledger=app.Ledger(self.path,{'test':1})
        self.addCleanup(self.ledger.close)

    def test_caps_are_separate_preview_three_demo_send_one(self):
        for bar in (100,101,102):
            self.assertIsNotNone(self.ledger.reserve(bar,'PREVIEW',{}))
            self.ledger.finish(bar,{'status':'SUCCESS'})
        self.assertIsNone(self.ledger.reserve(103,'PREVIEW',{}))
        self.assertIsNotNone(self.ledger.reserve(200,'DEMO_SEND',{}))
        self.ledger.finish(200,{'status':'SUCCESS'})
        self.assertIsNone(self.ledger.reserve(201,'DEMO_SEND',{}))
        self.assertEqual(self.ledger.count('PREVIEW'),3)
        self.assertEqual(self.ledger.count('DEMO_SEND'),1)
        self.assertEqual(self.ledger.count(),4)

    def test_legacy_ledger_without_policy_migrates_without_reset(self):
        self.ledger.reserve(100,'PREVIEW',{})
        self.ledger.finish(100,{'status':'SUCCESS'})
        self.ledger.close()
        db=sqlite3.connect(self.path)
        with db:db.execute('DROP TABLE policy')
        db.close()
        self.ledger=app.Ledger(self.path,{'test':1})
        self.assertEqual(self.ledger.count('PREVIEW'),1)
        self.assertEqual(self.ledger.count('DEMO_SEND'),0)
        self.assertEqual(self.ledger.budget('PREVIEW'),(1,3))
        self.assertEqual(self.ledger.budget('DEMO_SEND'),(0,1))

    def test_reservation_survives_other_connection(self):
        self.ledger.reserve(100,'PREVIEW',{})
        other=app.Ledger(self.path,{'test':1})
        try:self.assertTrue(other.blocked());self.assertEqual(other.count(),1)
        finally:other.close()

    def test_settings_frozen(self):
        with self.assertRaises(ValueError):app.Ledger(self.path,{'test':2})

    def test_error_blocks_not_wait(self):
        self.ledger.reserve(100,'PREVIEW',{})
        self.ledger.finish(100,{'status':'ERROR','http_status':429})
        self.assertTrue(self.ledger.blocked());self.assertIsNone(self.ledger.reserve(200,'PREVIEW',{}))

    def test_success_not_replayed(self):
        self.ledger.reserve(100,'PREVIEW',{});self.ledger.finish(100,{'status':'SUCCESS'})
        self.assertIsNone(self.ledger.reserve(100,'DEMO_SEND',{}))

    def test_unreserved_finish_rejected(self):
        with self.assertRaises(ValueError):self.ledger.finish(100,{'status':'SUCCESS'})

    def test_status_read_only_and_no_env(self):
        before=self.path.read_bytes()
        with patch.object(app,'DB',self.path),contextlib.redirect_stdout(io.StringIO()):app.status()
        self.assertEqual(before,self.path.read_bytes())


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.ledger=app.Ledger(self.root/'test.sqlite3',{})
        self.addCleanup(self.ledger.close)
        self.feed,self.acc,self.info,self.obs,self.payload=fixture()
        self.core=Mock();self.core.nondirectional_gate.return_value=True
        self.core.request_analysis.return_value={'status':'SUCCESS','signal':signal()}
        self.state=Mock();self.state.receiver_ready.return_value=True

    def invoke(self):
        return app.analyse(self.ledger,self.state,Mock(),self.core,self.feed,'PREVIEW',123,
                           self.obs,self.payload,100.,1700000400.,clocks=(lambda:102.,lambda:1700000402.))

    def test_attempt_reserved_before_call_and_published(self):
        def request(*args):
            self.assertEqual(self.ledger.count(),1);self.assertTrue(self.ledger.blocked())
            return {'status':'SUCCESS','signal':signal('WAIT')}
        self.core.request_analysis.side_effect=request
        r=self.invoke();self.assertEqual(r['delivery'],'READY_FOR_EA')
        self.assertIn(b';WAIT;',self.state.publish.call_args.args[0])
        self.assertEqual(self.core.request_analysis.call_args.args[2],self.payload)

    def test_failure_not_published_and_safe_error(self):
        exc=RuntimeError('SECRET_KEY_DO_NOT_PRINT');exc.status_code=429;exc.code='insufficient_quota'
        self.core.request_analysis.side_effect=exc
        r=self.invoke();self.assertEqual(r['status'],'ERROR');self.assertEqual(r['error_code'],'insufficient_quota')
        self.assertNotIn('SECRET_KEY',json.dumps(r));self.state.publish.assert_not_called()

    def test_crash_stays_reserved(self):
        self.core.request_analysis.side_effect=KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.invoke()
        self.assertTrue(self.ledger.blocked());self.assertEqual(self.ledger.count(),1)

    def test_bar_changes_response_withheld(self):
        self.feed.observe.return_value=NS(**{**vars(self.obs),'closed':self.obs.closed+300,'forming':self.obs.forming+300})
        r=self.invoke();self.assertEqual(r['status'],'SUCCESS');self.assertEqual(r['delivery'],'WITHHELD_RECHECK_FAILED')
        self.state.publish.assert_not_called()

    def test_old_result_withheld(self):
        values=iter((102.,145.,145.))
        r=app.analyse(self.ledger,self.state,Mock(),self.core,self.feed,'PREVIEW',123,self.obs,self.payload,
                      100.,1700000400.,clocks=(lambda:next(values),lambda:1700000445.))
        # A clock mismatch before the request means no paid attempt.
        self.assertEqual(r['status'],'SKIPPED');self.assertEqual(self.ledger.count(),0)

    def test_completed_after_deadline_is_saved_not_published(self):
        ms=iter((102.,145.,145.));ws=iter((1700000402.,1700000445.))
        r=app.analyse(self.ledger,self.state,Mock(),self.core,self.feed,'PREVIEW',123,self.obs,self.payload,
                      100.,1700000400.,clocks=(lambda:next(ms),lambda:next(ws)))
        self.assertEqual(r['status'],'SUCCESS');self.assertEqual(r['delivery'],'WITHHELD_RECHECK_FAILED')
        self.assertEqual(self.ledger.count(),1);self.state.publish.assert_not_called()

    def test_no_receiver_no_call(self):
        self.state.receiver_ready.return_value=False
        self.assertEqual(self.invoke()['status'],'SKIPPED');self.core.request_analysis.assert_not_called()

    def test_no_hint_in_input_forwarding(self):
        self.invoke()
        sent=self.core.request_analysis.call_args.args[2]
        self.assertNotIn('local_filter',sent);self.assertNotIn('commission',sent);self.assertNotIn('login',sent)

    def test_packet_fields_and_ttl(self):
        p=app.packet('PREVIEW','a'*32,'BUY',1700000000,1700000030,1699999900,123,5.,4338.,4338.2)
        self.assertEqual(len(p.decode().split(';')),13);self.assertTrue(p.startswith(b'NOG_CASH_V1;'))
        with self.assertRaises(ValueError):app.packet('PREVIEW','a'*32,'BUY',1700000000,1700000100,1699999900,123,5.,4338.,4338.2)

    def test_preflight_unknown_fee_no_order(self):
        out=io.StringIO()
        with contextlib.redirect_stdout(out):app.preflight(self.feed)
        self.assertIn('COMMISSION_UNKNOWN',out.getvalue());self.assertIn('NO_LEDGER_WRITE',out.getvalue())
        self.feed.mt5.order_send.assert_not_called()

    def test_preflight_known_fee(self):
        out=io.StringIO()
        with contextlib.redirect_stdout(out):app.preflight(self.feed,7.,.1)
        self.assertNotIn('COMMISSION_UNKNOWN',out.getvalue());self.assertIn('BUY | lot=',out.getvalue())

    def test_installer_keeps_old_files_and_token(self):
        base=self.root/'terminal';(base/'MQL5'/'Experts').mkdir(parents=True)
        self.feed.mt5.terminal_info.return_value=NS(data_path=str(base))
        source=self.root/'source';source.mkdir()
        for name in app.EA_FILES:(source/name).write_text('new '+name)
        old=base/'MQL5'/'Experts'/'NOG_GuardedDemo.mq5';old.write_text('old')
        with contextlib.redirect_stdout(io.StringIO()):app.install(self.feed,source)
        token=base/'MQL5'/'Files'/'NOG_CashDemo'/'bridge.token';value=token.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):app.install(self.feed,source)
        self.assertEqual(token.read_bytes(),value);self.assertEqual(old.read_text(),'old')

    def test_http_auth_and_old_receiver_rejected(self):
        self.state.current.return_value=None
        server=app.HTTPServer(('127.0.0.1',0),app.handler_for(self.state,'a'*64,('123','TEST-DEMO')))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url=f'http://127.0.0.1:{server.server_port}/signal'
            with self.assertRaises(HTTPError) as e:urlopen(url,timeout=2)
            self.assertEqual(e.exception.code,403)
            headers={'X-NOG-Token':'a'*64,'X-NOG-Receiver':'CASH_DEMO_V1','X-NOG-Account':'123',
                     'X-NOG-Server':'TEST-DEMO','X-NOG-Symbol':'XAUUSD','X-NOG-Timeframe':'M5'}
            with urlopen(Request(url,headers=headers),timeout=2) as r:self.assertEqual(r.status,204)
            headers['X-NOG-Receiver']='GUARDED_DEMO_V1'
            with self.assertRaises(HTTPError):urlopen(Request(url,headers=headers),timeout=2)
        finally:server.shutdown();thread.join();server.server_close()

    def test_ea_defaults_no_orders_cash_rechecks_and_persistent_cap(self):
        text=(Path(app.__file__).parent/'mt5'/'NOG_CashDemo.mq5').read_text()
        self.assertIn('InpEnableDemoOrders=false',text);self.assertIn('InpAcknowledgeHighRisk=false',text)
        self.assertIn('InpRoundTripFixedFee=-1.0',text);self.assertEqual(text.count('OrderSend('),1)
        reserve=text.index('g_daily++;g_total++;g_halt=1;');send=text.index('OrderSend(')
        self.assertLess(reserve,send);self.assertIn('if(!Save())',text[reserve:send])
        self.assertIn('checked.retcode!=0',text);self.assertIn('g_total>=1',text)
        self.assertIn('AccountInfoString(ACCOUNT_CURRENCY)=="USD"',text)
        self.assertNotIn('OrderSendAsync(',text)


if __name__=='__main__':unittest.main()
