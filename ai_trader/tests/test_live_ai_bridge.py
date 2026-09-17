"""Synthetic data, mocked OpenAI/MT5 and local loopback only. No paid requests."""
import ast
import copy
import http.client
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import live_ai_bridge as app
import live_ai_core as core

ROOT = Path(__file__).resolve().parents[1]
FORMING = 1_800_000_000
SOURCE = 'a' * 64
WALL = 1_790_000_000.0  # intentionally unrelated to broker/raw bar timezone


def observation(forming=FORMING, tick_s=3):
    return core.Observation(forming, forming-300, (forming+tick_s)*1000,
                            100., 100.02, .01, SOURCE)


def candles(obs=None, slope=.01):
    obs = obs or observation()
    result = []
    for i in range(120):
        c = 100. + i*slope
        result.append(dict(time=obs.closed-(119-i)*300, open=c-.02, high=c+.5,
                           low=c-.5, close=c, spread=2, tick_volume=100+i))
    return result


def signal(action='BUY'):
    return dict(action=action, confidence=70, market_regime='RANGE', reason='Uji sintetis.')


def response(action='BUY', status='completed'):
    return NS(status=status, output=[], output_text=json.dumps(signal(action)),
              model='test-model', id='response-test', usage=NS(input_tokens=500, output_tokens=55))


class PayloadTests(unittest.TestCase):
    def test_only_last_30_closed_candles_with_no_hint(self):
        p = core.build_payload(candles(), 'XAUUSD', observation())
        self.assertEqual(len(p['candles']), 30)
        self.assertEqual(p['candles'][-1]['bar_id_raw'], observation().closed)
        self.assertNotIn('local_filter', p)
        self.assertNotIn('candidate_direction', core.encode(p))
        self.assertNotIn('login', core.encode(p))
        self.assertNotIn('forming', p)
        self.assertTrue(all(b['bar_id_raw'] < FORMING for b in p['candles']))

    def test_current_candle_cannot_enter_closed_input(self):
        rows = candles()
        rows[-1]['time'] = FORMING
        with self.assertRaises(core.GuardError):
            core.build_payload(rows, 'XAUUSD', observation())

    def test_nonfinite_and_unexpected_fields_rejected(self):
        for field, val in [('close', float('nan')), ('close', -1), ('tick_volume', True)]:
            rows = candles(); rows[-1][field] = val
            with self.assertRaises(core.GuardError):
                core.build_payload(rows, 'XAUUSD', observation())
        rows = candles(); rows[-1]['future_return'] = 1
        with self.assertRaises(core.GuardError):
            core.build_payload(rows, 'XAUUSD', observation())

    def test_bad_ohlc_history_count_and_order_rejected(self):
        rows = candles(); rows[-1]['high'] = 50
        cases = [rows, candles()[:-1], list(reversed(candles()))]
        for value in cases:
            with self.assertRaises(core.GuardError):
                core.build_payload(value, 'XAUUSD', observation())

    def test_live_gate_has_no_buy_sell_dependency(self):
        for slope in [.01, -.01]:
            p = core.build_payload(candles(slope=slope), 'XAUUSD', observation())
            self.assertTrue(core.nondirectional_gate(p))
            p['current_quote']['spread_price'] = 100
            self.assertFalse(core.nondirectional_gate(p))

    def test_legacy_indicator_convention_is_explicit(self):
        up = core.build_payload(candles(slope=.01), 'XAUUSD', observation())
        down = core.build_payload(candles(slope=-.01), 'XAUUSD', observation())
        self.assertEqual(up['candles'][-1]['rsi14'], 100)
        self.assertEqual(down['candles'][-1]['rsi14'], 0)
        self.assertAlmostEqual(up['candles'][-1]['atr14'], 1.)
        self.assertGreater(up['candles'][-1]['ema20'], up['candles'][-1]['ema50'])

    def test_bad_tick_or_negative_bid_ask_rejected(self):
        for value in [replace(observation(), ask=99), replace(observation(), tick_msc=1),
                      replace(observation(), closed=FORMING), replace(observation(), bid=float('inf'))]:
            with self.assertRaises(core.GuardError):
                value.validate()


class TransitionTests(unittest.TestCase):
    def test_startup_does_not_analyse_existing_candle(self):
        gate = core.TransitionGate()
        self.assertIsNone(gate.observe(observation(), 10, WALL)[0])
        self.assertIsNone(gate.observe(observation(tick_s=5), 12, WALL+2)[0])
        self.assertTrue(gate.tick_is_advancing(12))

    def test_exact_fresh_transition_identifies_closed_bar(self):
        gate = core.TransitionGate()
        gate.observe(observation(tick_s=299), 10, WALL)
        new = observation(FORMING+300, 1)
        self.assertEqual(gate.observe(new, 12, WALL+2)[0], FORMING)
        self.assertIsNone(gate.observe(new, 14, WALL+4)[0])

    def test_gap_resync_and_clock_jump_do_not_call(self):
        for nxt, mono, wall in [(observation(FORMING+600), 12, WALL+2),
                                (observation(FORMING+300), 40, WALL+30),
                                (observation(FORMING+300), 12, WALL+200)]:
            gate = core.TransitionGate(); gate.observe(observation(), 10, WALL)
            self.assertIsNone(gate.observe(nxt, mono, wall)[0])

    def test_tick_stall_expires_liveness(self):
        gate = core.TransitionGate()
        gate.observe(observation(), 10, WALL)
        gate.observe(observation(tick_s=5), 12, WALL+2)
        self.assertTrue(gate.tick_is_advancing(20))
        self.assertFalse(gate.tick_is_advancing(28))

    def test_source_change_blocks_transition(self):
        gate = core.TransitionGate(); gate.observe(observation(), 10, WALL)
        other = replace(observation(FORMING+300), source_id='b'*64)
        self.assertIsNone(gate.observe(other, 12, WALL+2)[0])


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'ledger.sqlite3'
        self.settings = dict(model='test-model', symbol='XAUUSD', source_id=SOURCE)
        self.ledger = core.AttemptLedger(self.path, self.settings)
        self.addCleanup(self.ledger.close)

    def test_persistent_cumulative_three_attempt_cap(self):
        for i in range(3):
            raw = FORMING+i*300
            status, sid = self.ledger.reserve(raw, {}, WALL)
            self.assertEqual(status, 'RESERVED')
            self.assertEqual(len(sid), 32)
            self.ledger.finish(raw, {'status': 'SUCCESS'})
        second = core.AttemptLedger(self.path, self.settings)
        try:
            self.assertEqual(second.count(), 3)
            self.assertEqual(second.reserve(FORMING+900, {}, WALL)[0], 'CALL_LIMIT_REACHED')
        finally:
            second.close()

    def test_duplicate_bar_is_not_repeated(self):
        self.ledger.reserve(FORMING, {}, WALL)
        self.ledger.finish(FORMING, {'status': 'SUCCESS'})
        self.assertEqual(self.ledger.reserve(FORMING, {}, WALL)[0], 'ALREADY_ATTEMPTED')
        self.assertEqual(self.ledger.count(), 1)

    def test_unresolved_attempt_blocks_future_spending(self):
        self.ledger.reserve(FORMING, {}, WALL)
        other = core.AttemptLedger(self.path, self.settings)
        try:
            self.assertTrue(other.blocked())
            self.assertEqual(other.reserve(FORMING+300, {}, WALL)[0], 'UNRESOLVED_ATTEMPT_STOP')
        finally:
            other.close()

    def test_settings_cannot_silently_change_on_restart(self):
        with self.assertRaises(core.GuardError):
            core.AttemptLedger(self.path, {**self.settings, 'model': 'other'})

    def test_finish_requires_reservation(self):
        with self.assertRaises(core.GuardError):
            self.ledger.finish(FORMING, {'status': 'SUCCESS'})


class PacketStateTests(unittest.TestCase):
    def setUp(self):
        self.state = core.BridgeState('XAUUSD')
        self.body = core.ai_packet('SELL', 'XAUUSD', 'c'*32, int(WALL), int(WALL)+30, FORMING-300)

    def test_distinct_protocol_contains_raw_bar_id(self):
        fields = self.body.decode().split(';')
        self.assertEqual(len(fields), 9)
        self.assertEqual(fields[:2], ['NOG_LIVE_V1','AI_DRYRUN'])
        self.assertEqual(fields[-1], str(FORMING-300))
        self.assertNotIn('TEST_ONLY', fields)

    def test_bad_id_ttl_and_action_rejected(self):
        for action, sid, ttl in [('TRADE', 'c'*32, 30), ('BUY','bad',30), ('BUY','c'*32,31)]:
            with self.assertRaises(core.GuardError):
                core.ai_packet(action,'XAUUSD',sid,int(WALL),int(WALL)+ttl,FORMING-300)

    def test_poll_does_not_renew_expiry(self):
        self.state.publish(self.body,FORMING-300,int(WALL)+30,10,WALL)
        self.state.maintain(FORMING-300,True,18,WALL+8)
        self.assertEqual(self.state.current(18,WALL+8), self.body)
        self.assertIsNone(self.state.current(41,WALL+31))

    def test_main_stall_or_clock_rollback_fails_closed(self):
        for m,w in [(20,WALL+10),(11,WALL-10)]:
            self.state.publish(self.body,FORMING-300,int(WALL)+30,10,WALL)
            self.assertIsNone(self.state.current(m,w))

    def test_new_bar_and_stale_tick_clear_packet(self):
        for raw,fresh in [(FORMING,True),(FORMING-300,False)]:
            self.state.publish(self.body,FORMING-300,int(WALL)+30,10,WALL)
            self.state.maintain(raw,fresh,12,WALL+2)
            self.assertIsNone(self.state.current(12,WALL+2))

    def test_receiver_lease_has_short_lifetime(self):
        self.assertFalse(self.state.receiver_ready(10))
        self.state.receiver_seen(10)
        self.assertTrue(self.state.receiver_ready(15))
        self.assertFalse(self.state.receiver_ready(20))


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.ledger = core.AttemptLedger(Path(self.tmp.name)/'live.sqlite3', {'model':'test-model'})
        self.addCleanup(self.ledger.close)
        self.state = core.BridgeState('XAUUSD'); self.state.receiver_seen(100.)
        self.obs = observation()
        self.payload = core.build_payload(candles(), 'XAUUSD', self.obs)
        self.client = Mock(); self.client.responses.create.return_value = response()
        self.clock = [100.,WALL]
        self.after = replace(self.obs,tick_msc=self.obs.tick_msc+1000)

    def execute(self):
        return core.analyse_and_publish(self.ledger,self.state,self.client,'test-model','XAUUSD',
                                        self.payload,self.obs,100.,WALL,lambda:self.after,
                                        lambda:self.clock[0],lambda:self.clock[1])

    def test_end_to_end_paid_result_to_wire_after_persistence(self):
        def http(**kwargs):
            self.assertEqual(self.ledger.count(),1)  # before mock API begins
            self.assertTrue(self.ledger.blocked())
            return response('SELL')
        self.client.responses.create.side_effect=http
        result=self.execute()
        self.assertEqual(result['delivery'],'READY_FOR_DRYRUN')
        self.assertIn(b';SELL;',self.state.current(100.,WALL))
        self.assertFalse(self.ledger.blocked())
        self.execute()
        self.assertEqual(self.client.responses.create.call_count,1)

    def test_request_contains_only_payload_and_fixed_limits(self):
        self.execute()
        kwargs=self.client.responses.create.call_args.kwargs
        self.assertEqual(json.loads(kwargs['input']),self.payload)
        self.assertEqual(kwargs['max_output_tokens'],2048)
        self.assertEqual(kwargs['reasoning'],{'effort':'low'})
        self.assertFalse(kwargs['store'])
        self.assertNotIn('tools',kwargs)
        self.assertNotIn('local_filter',kwargs['input'])
        self.assertNotIn('source_id',kwargs['input'])

    def test_wait_is_valid_analysis_not_order(self):
        self.client.responses.create.return_value=response('WAIT')
        result=self.execute()
        self.assertEqual(result['status'],'SUCCESS')
        self.assertIn(b';WAIT;',self.state.current(100.,WALL))

    def test_no_receiver_does_not_spend(self):
        self.state.receiver_mono=None
        self.assertEqual(self.execute()['status'],'SKIPPED')
        self.client.responses.create.assert_not_called()
        self.assertEqual(self.ledger.count(),0)

    def test_no_quality_does_not_spend(self):
        self.payload['current_quote']['spread_price']=100.
        self.assertEqual(self.execute()['status'],'SKIPPED')
        self.client.responses.create.assert_not_called()

    def test_error_saved_not_wait_and_no_secret_exception_echo(self):
        self.client.responses.create.side_effect=RuntimeError('DO_NOT_ECHO_KEY')
        result=self.execute()
        self.assertEqual(result['status'],'ERROR')
        self.assertNotIn('signal',result)
        self.assertNotIn('DO_NOT_ECHO_KEY',json.dumps(result))
        self.assertTrue(self.ledger.blocked())
        self.assertIsNone(self.state.current(100,WALL))

    def test_interruption_leaves_reserved_unknown(self):
        self.client.responses.create.side_effect=KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):self.execute()
        self.assertEqual(self.ledger.count(),1)
        self.assertTrue(self.ledger.blocked())

    def test_incomplete_refusal_invalid_are_not_wait(self):
        for reply,expected in [(response(status='incomplete'),'INCOMPLETE'),
                               (NS(**{**response().__dict__,'output':[NS(content=[NS(type='refusal')])]}),'REFUSED'),
                               (NS(**{**response().__dict__,'output_text':'not json'}),'INVALID')]:
            self.client.responses.create.return_value=reply
            result=core.request_analysis(self.client,'test-model',self.payload)
            self.assertEqual(result['status'],expected)
            self.assertNotIn('signal',result)

    def test_late_or_clock_shift_response_not_published(self):
        def late(**kwargs):
            self.clock[:]=[150.,WALL+50]; self.state.receiver_seen(150.)
            return response()
        self.client.responses.create.side_effect=late
        result=self.execute()
        self.assertEqual(result['delivery'],'WITHHELD_TIME_OR_CLOCK')
        self.assertEqual(result['status'],'SUCCESS')  # API succeeded, delivery did not
        self.assertIsNone(self.state.current(150,WALL+50))

    def test_bar_changed_during_request_not_published(self):
        self.after=observation(FORMING+300)
        self.assertEqual(self.execute()['delivery'],'WITHHELD_BAR_OR_SOURCE_CHANGED')
        self.assertIsNone(self.state.current(100,WALL))

    def test_recheck_failure_does_not_publish(self):
        self.after=replace(self.after,ask=0)
        self.assertNotEqual(self.execute()['delivery'],'READY_FOR_DRYRUN')
        self.assertIsNone(self.state.current(100,WALL))

    def test_widened_spread_withholds_without_direction_fallback(self):
        self.after=replace(self.after,ask=110)
        self.assertEqual(self.execute()['delivery'],'WITHHELD_SPREAD_CHANGED')
        self.assertIsNone(self.state.current(100,WALL))


class AdapterHTTPTests(unittest.TestCase):
    def fake_mt5(self):
        m=Mock()
        m.ACCOUNT_TRADE_MODE_DEMO=0; m.SYMBOL_CHART_MODE_BID=0; m.TIMEFRAME_M5=5
        m.initialize.return_value=True; m.symbol_select.return_value=True
        m.terminal_info.return_value=NS(connected=True,path='terminal-path')
        m.account_info.return_value=NS(trade_mode=0,login=12345,server='DEMO')
        m.symbol_info.return_value=NS(chart_mode=0,point=.01)
        m.symbol_info_tick.return_value=NS(time_msc=(FORMING+3)*1000,bid=100.,ask=100.02)
        m.copy_rates_from_pos.side_effect=lambda symbol,tf,pos,count: ([{'time':FORMING-300},{'time':FORMING}]
                                                                    if (pos,count)==(0,2) else candles())
        return m

    def test_adapter_uses_120_position_one_closed_bars(self):
        m=self.fake_mt5(); feed=app.MT5Feed(m,app.Config())
        feed.connect(); obs=feed.observe(); payload=feed.payload(obs)
        self.assertEqual(payload['latest_closed_bar_raw'],FORMING-300)
        self.assertIn((('XAUUSD',5,1,120),), [tuple(c)[:1] for c in m.copy_rates_from_pos.call_args_list])
        self.assertNotIn('12345',json.dumps(payload))

    def test_adapter_rejects_live_account_last_chart_and_disconnect(self):
        for what in ('live','last','disconnect'):
            m=self.fake_mt5()
            if what=='live':m.account_info.return_value.trade_mode=2
            if what=='last':m.symbol_info.return_value.chart_mode=1
            if what=='disconnect':m.terminal_info.return_value.connected=False
            with self.assertRaises(core.GuardError): app.MT5Feed(m,app.Config()).connect()

    def test_adapter_account_change_rejected(self):
        m=self.fake_mt5(); feed=app.MT5Feed(m,app.Config());feed.connect()
        m.account_info.return_value.login=67890
        with self.assertRaises(core.GuardError):feed.observe()

    def test_loopback_http_handshake_and_payload(self):
        state=core.BridgeState('XAUUSD')
        server=HTTPServer(('127.0.0.1',0),app.handler_for(state))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            headers={'X-NOG-Receiver':app.RECEIVER_HEADER,'X-NOG-Symbol':'XAUUSD','X-NOG-Timeframe':'M5'}
            def get(path='/signal',hdr=headers):
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
                try:
                    conn.request('GET',path,headers=hdr);r=conn.getresponse();return r.status,r.read()
                finally:conn.close()
            self.assertEqual(get(hdr={})[0],409)
            self.assertFalse(state.receiver_ready(time.monotonic()))
            self.assertEqual(get()[0],204)
            self.assertTrue(state.receiver_ready(time.monotonic()))
            w,m=time.time(),time.monotonic()
            body=core.ai_packet('WAIT','XAUUSD','c'*32,int(w),int(w)+30,FORMING-300)
            state.publish(body,FORMING-300,int(w)+30,m,w)
            self.assertEqual(get(),(200,body))
            self.assertEqual(get('/other')[0],404)
        finally:
            server.shutdown();server.server_close();thread.join(2)

    def test_busy_port_makes_no_api_or_mt5_connection(self):
        server=HTTPServer(('127.0.0.1',0),app.handler_for(core.BridgeState('XAUUSD')))
        try:
            feed,client=Mock(),Mock()
            with patch.object(app,'PORT',server.server_port):
                with self.assertRaises(core.GuardError):app.run(app.Config(),feed,client)
            feed.connect.assert_not_called();client.assert_not_called()
        finally:server.server_close()

    def test_help_without_sitepackages_or_database(self):
        result=subprocess.run([sys.executable,'-S',str(ROOT/'live_ai_bridge.py')],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--run',result.stdout)
        self.assertFalse((ROOT/'data'/'live_ai_bridge.sqlite3').exists())

    def test_run_loop_is_bounded_and_does_not_replay_on_restart(self):
        with tempfile.TemporaryDirectory() as td:
            dbpath=Path(td)/'live.sqlite3'
            step=[0]
            clock=lambda:100.+step[0]*2
            wall=lambda:WALL+step[0]*2
            def obs():return observation(FORMING+(step[0]//2)*300,1+step[0]%2)
            def sleep(_):
                step[0]+=1
                if step[0]>9:raise KeyboardInterrupt
            feed=Mock(source_id=SOURCE)
            feed.observe.side_effect=obs
            feed.payload.side_effect=lambda expected: core.build_payload(candles(expected),'XAUUSD',expected)
            factory=Mock();factory.return_value.responses.create.return_value=response('WAIT')
            packets=[]
            real_publish=core.BridgeState.publish
            def publish(state,*args,**kwargs):
                packets.append(args[0]);return real_publish(state,*args,**kwargs)
            with patch.object(app,'DB_FILE',dbpath), patch.object(app,'PORT',0), \
                 patch.object(core.BridgeState,'receiver_ready',return_value=True), \
                 patch.object(core.BridgeState,'publish',publish), \
                 patch.object(app.time,'monotonic',clock), patch.object(app.time,'time',wall), \
                 patch.object(app.time,'sleep',sleep), patch.object(app,'log'), \
                 patch.dict(os.environ,{'OPENAI_API_KEY':'unit-test-only'}):
                with self.assertRaises(KeyboardInterrupt):app.run(app.Config(model='test-model'),feed,factory)
                self.assertEqual(factory.return_value.responses.create.call_count,3)
                self.assertEqual(len(packets),3)
                self.assertTrue(all(b';WAIT;' in packet for packet in packets))
                self.assertEqual(factory.call_args.kwargs['max_retries'],0)
                self.assertEqual(factory.call_args.kwargs['timeout'],25.)
                factory.reset_mock()
                app.run(app.Config(model='test-model'),feed,factory)
                factory.assert_not_called()  # capped journal cannot be reset by rerun
            with sqlite3.connect(dbpath) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],3)

    def test_check_mode_reads_mt5_but_never_constructs_api_client(self):
        m=self.fake_mt5();sdk=Mock()
        with tempfile.TemporaryDirectory() as td, patch.object(app,'DB_FILE',Path(td)/'none.sqlite3'), \
             patch.object(app,'config_from_env',return_value=app.Config()), \
             patch.dict(sys.modules,{'MetaTrader5':m,'openai':NS(OpenAI=sdk)}), \
             patch.object(sys,'argv',['live_ai_bridge.py','--check']), patch.object(app,'log'):
            self.assertEqual(app.main(),0)
            sdk.assert_not_called()
            m.shutdown.assert_called_once()
            self.assertFalse(app.DB_FILE.exists())

    def test_no_order_calls_or_secret_literal_in_new_sources(self):
        for name in ('live_ai_bridge.py','live_ai_core.py'):
            source=(ROOT/name).read_text()
            ast.parse(source)
            for forbidden in ('order_send(', 'order_check(', 'positions_modify(', 'sk-proj-'):
                self.assertNotIn(forbidden,source)
        mq5=(ROOT/'mt5'/'NOG_LiveAI_DRYRUN.mq5').read_text()
        for forbidden in ('OrderSend(', 'OrderSendAsync(', 'CTrade', '#import'):
            self.assertNotIn(forbidden,mq5)
        self.assertIn('iTime(_Symbol,PERIOD_M5,1)',mq5)
        self.assertIn('AI_DRYRUN',mq5)
        self.assertIn('ACCEPTED_AI_SIGNAL',mq5)


if __name__=='__main__':unittest.main()
