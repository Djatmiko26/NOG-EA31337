"""Synthetic offline tests. No real terminal, API request, or user history."""
import ast
import contextlib
import copy
import io
import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import execution_baseline as lab
from strategy_filter import FilterConfig


def bars(count=1000, flat=False):
    result = []
    for i in range(count):
        p = 100.0 if flat else 100 + .02 * i + .3 * math.sin(i / 7)
        result.append(dict(time=1788000000 + 300 * i, open=p, high=p+.3,
                           low=p-.3, close=p+.05, spread=10, tick_volume=100))
    return result


def fake_mt5(records=None):
    return SimpleNamespace(initialize=Mock(return_value=True), shutdown=Mock(),
        terminal_info=Mock(return_value=SimpleNamespace(connected=True)),
        symbol_select=Mock(return_value=True),
        symbol_info=Mock(return_value=SimpleNamespace(point=.01, chart_mode=0)),
        copy_rates_from_pos=Mock(return_value=bars() if records is None else records),
        SYMBOL_CHART_MODE_BID=0, TIMEFRAME_M5=5, __version__="mock-mt5")


class ExecutionBaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'snapshot.json'

    def prepare(self, mt5=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return lab.prepare(self.path, mt5_path='test', count=1000,
                               mt5=mt5 or fake_mt5())

    def test_prepare_only_closed_bars_and_shutdown(self):
        terminal = fake_mt5()
        saved = self.prepare(terminal)
        terminal.copy_rates_from_pos.assert_called_once_with('XAUUSD', 5, 1, 1000)
        terminal.shutdown.assert_called_once()
        self.assertEqual(saved['chart_mode'], 'BID')
        self.assertEqual(saved['point'], .01)

    def test_no_login_or_secret_fields_saved(self):
        text = lab.encode(self.prepare())
        for name in ('OPENAI_API_KEY', 'account_login', 'password', 'terminal_path'):
            self.assertNotIn(name, text)

    def test_repeated_prepare_reuses_snapshot_no_terminal(self):
        saved = self.prepare()
        original = self.path.read_bytes()
        terminal = fake_mt5()
        self.assertEqual(self.prepare(terminal), saved)
        terminal.initialize.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)

    def test_last_chart_rejected_not_guessed(self):
        terminal = fake_mt5()
        terminal.symbol_info.return_value.chart_mode = 1
        with self.assertRaisesRegex(ValueError, 'chart BID'):
            self.prepare(terminal)
        terminal.shutdown.assert_called_once()
        terminal.copy_rates_from_pos.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_partial_history_rejected_not_silently_shortened(self):
        terminal = fake_mt5(bars(900))
        with self.assertRaisesRegex(ValueError, 'tidak lengkap'):
            self.prepare(terminal)
        self.assertFalse(self.path.exists())
        terminal.shutdown.assert_called_once()

    def test_initialize_failure_shuts_down(self):
        terminal = fake_mt5()
        terminal.initialize.return_value = False
        with self.assertRaises(ValueError):
            self.prepare(terminal)
        terminal.shutdown.assert_called_once()
        terminal.copy_rates_from_pos.assert_not_called()

    def test_corrupt_snapshot_stops_not_refetched(self):
        self.path.write_text('{corrupt', encoding='utf-8')
        terminal = fake_mt5()
        with self.assertRaises(ValueError):
            self.prepare(terminal)
        terminal.initialize.assert_not_called()

    def test_fingerprint_rejects_price_mutation(self):
        self.prepare()
        envelope = json.loads(self.path.read_text())
        envelope['body']['bars'][10]['open'] += .1
        self.path.write_text(json.dumps(envelope))
        with self.assertRaisesRegex(ValueError, 'Snapshot rusak'):
            lab.load_snapshot(self.path)

    def test_changed_implementation_is_rejected(self):
        self.prepare()
        envelope = json.loads(self.path.read_text())
        envelope['body']['implementation']['execution_baseline.py'] = 'different'
        envelope['sha256'] = lab.digest(envelope['body'])
        self.path.write_text(json.dumps(envelope))
        with self.assertRaisesRegex(ValueError, 'Kode berubah'):
            lab.load_snapshot(self.path)

    def test_unknown_snapshot_missing_is_not_created(self):
        with self.assertRaises(FileNotFoundError):
            lab.load_snapshot(self.path)
        self.assertFalse(self.path.exists())

    def test_nan_bool_negative_and_inconsistent_ohlc_rejected(self):
        for field, value in [('close',float('nan')), ('close',True), ('low',-1),
                             ('spread',-1), ('spread',1.5), ('high',1)]:
            with self.subTest(field=field, value=value):
                records=bars(3)
                records[1][field]=value
                with self.assertRaises(ValueError):
                    lab.validate_bars(records)

    def test_duplicate_or_reversed_bars_not_silently_removed(self):
        for value in (1788000000, 1788000000-300):
            records=bars(3)
            records[1]['time']=value
            with self.assertRaises(ValueError):
                lab.validate_bars(records)

    def test_gap_preserved_no_synthetic_bar(self):
        records=bars(5)
        records[-1]['time'] += 86400
        frame = lab.validate_bars(records)
        self.assertEqual(len(frame),5)
        self.assertEqual(frame.iloc[-1]['time'], records[-1]['time'])

    def test_buy_ask_entry_bid_exit(self):
        m=lab.quote_proxy(100.,110.,20.,30.,.01,'BUY',1)
        self.assertAlmostEqual(m['entry_quote_proxy'],100.2)
        self.assertEqual(m['exit_quote_proxy'],110.)
        self.assertAlmostEqual(m['spread_proxy_return_pct'],9.8)
        self.assertAlmostEqual(m['spread_drag_pp'],.2)

    def test_sell_bid_entry_ask_cover(self):
        m=lab.quote_proxy(100.,90.,20.,30.,.01,'SELL',1)
        self.assertEqual(m['entry_quote_proxy'],100.)
        self.assertAlmostEqual(m['exit_quote_proxy'],90.3)
        self.assertAlmostEqual(m['spread_proxy_return_pct'],9.7)
        self.assertAlmostEqual(m['spread_drag_pp'],.3)

    def test_flat_prices_pay_one_roundtrip_spread_not_two(self):
        for side in ('BUY','SELL'):
            m=lab.quote_proxy(100.,100.,20.,20.,.01,side,1)
            self.assertAlmostEqual(m['spread_proxy_return_pct'],-.2)

    def test_zero_proxy_is_explicit_frictionless_baseline(self):
        m=lab.quote_proxy(100.,90.,999.,999.,.01,'SELL',0)
        self.assertAlmostEqual(m['gross_bid_return_pct'],10.)
        self.assertEqual(m['spread_proxy_return_pct'],m['gross_bid_return_pct'])

    def test_stress_spread_cannot_improve_same_trade(self):
        for side in ('BUY','SELL'):
            values=[lab.quote_proxy(100.,103.,20.,30.,.01,side,x) for x in (0,1,2)]
            self.assertGreater(values[0]['spread_proxy_return_pct'],values[1]['spread_proxy_return_pct'])
            self.assertGreater(values[1]['spread_proxy_return_pct'],values[2]['spread_proxy_return_pct'])
            self.assertAlmostEqual(values[2]['spread_drag_pp'],2*values[1]['spread_drag_pp'])

    def test_invalid_fee_inputs_or_side_rejected(self):
        for kwargs in [dict(point=0),dict(multiplier=-1),dict(entry_bid=float('inf')),
                       dict(exit_spread=True),dict(direction='WAIT')]:
            args=dict(entry_bid=100.,exit_bid=101.,entry_spread=1.,exit_spread=1.,
                      point=.01,direction='BUY',multiplier=1)
            args.update(kwargs)
            with self.assertRaises(ValueError):
                lab.quote_proxy(**args)

    def test_delay_and_hold_use_future_opens(self):
        rules=lab.Rules()
        i,e,x,side=lab.schedule({200:'BUY'},rules)[0]
        self.assertEqual((i,e,x),(200,202,222))
        self.assertEqual(x-e,20)

    def test_no_signal_accepted_while_pending_or_active(self):
        selected=lab.schedule({i:'BUY' for i in range(200,400)},lab.Rules())
        for previous,next_one in zip(selected,selected[1:]):
            self.assertGreaterEqual(next_one[0],previous[2])
            self.assertGreater(next_one[1],previous[2])

    def test_development_exits_purged_before_late_boundary(self):
        rules=lab.Rules()
        parts,boundary,purged=lab.split_indices(5000,rules)
        self.assertEqual(purged,22)
        self.assertTrue(all(i+22 < boundary for i in parts['EARLY']))
        self.assertEqual(min(parts['LATE']),boundary)
        self.assertTrue(all(i+22 < 5000 for i in parts['LATE']))

    def test_history_too_short_stops(self):
        with self.assertRaises(ValueError):
            lab.split_indices(222,lab.Rules())

    def test_filter_context_contains_no_future_rows(self):
        df=lab.validate_bars(bars(250))
        seen=[]
        def inspect(context, **kwargs):
            seen.append(context.copy())
            return SimpleNamespace(qualifies=True,direction='BUY')
        lab.candidates(df,[200,201],.01,lab.Rules(),FilterConfig(min_score=5),inspect)
        self.assertEqual(len(seen[0]),120)
        self.assertEqual(int(seen[0].iloc[-1]['time']),int(df.iloc[200]['time']))
        self.assertLess(int(seen[0].iloc[-1]['time']),int(df.iloc[201]['time']))

    def test_future_price_changes_do_not_change_past_decisions(self):
        df=lab.validate_bars(bars(400))
        cfg=FilterConfig(min_score=5)
        expected=lab.candidates(df,list(range(200,220)),.01,lab.Rules(),cfg)
        changed=df.copy()
        for col in ('open','high','low','close'):
            changed.loc[220:,col] *= 2
        actual=lab.candidates(changed,list(range(200,220)),.01,lab.Rules(),cfg)
        self.assertEqual(expected,actual)

    def test_fill_bar_spread_not_used_as_known_at_its_open(self):
        rules=lab.Rules(context_bars=20,warmup_bars=20,hold_bars=5)
        df=lab.validate_bars(bars(100))
        df.loc[:,'spread']=10
        df.loc[22,'spread']=999
        def always(context,**kwargs):
            return SimpleNamespace(qualifies=True,direction='BUY')
        summaries,trades=lab.simulate(df,.01,rules,FilterConfig(min_score=5),always,lambda _:None)
        row=next(r for r in trades if r['signal_index']==20 and r['spread_multiplier']==1)
        self.assertEqual(row['entry_index'],22)
        self.assertEqual(row['entry_spread_lagged_points'],10)

    def test_all_spread_scenarios_share_exact_trade_schedule(self):
        rules=lab.Rules(context_bars=20,warmup_bars=20,hold_bars=5)
        def always(context,**kwargs):
            return SimpleNamespace(qualifies=True,direction='SELL')
        _,trades=lab.simulate(lab.validate_bars(bars(100)),.01,rules,
                             FilterConfig(min_score=5),always,lambda _:None)
        schedules=[]
        for mult in (0,1,2):
            schedules.append([(r['segment'],r['signal_index'],r['entry_index'],r['exit_index'])
                               for r in trades if r['spread_multiplier']==mult])
        self.assertEqual(schedules[0],schedules[1])
        self.assertEqual(schedules[1],schedules[2])

    def test_no_candidates_means_missing_performance_not_zero_profit(self):
        rules=lab.Rules(context_bars=20,warmup_bars=20,hold_bars=5)
        def never(context,**kwargs):
            return SimpleNamespace(qualifies=False,direction='NONE')
        summaries,trades=lab.simulate(lab.validate_bars(bars(100)),.01,rules,
                                     FilterConfig(min_score=5),never,lambda _:None)
        self.assertFalse(trades)
        self.assertTrue(all(s['n']==0 and s['mean_pct'] is None for s in summaries))

    def test_old_flat_rsi_seed_not_silently_changed(self):
        df=lab.validate_bars(bars(120,flat=True))
        self.assertEqual(float(lab.legacy_indicators(df).iloc[-1]['rsi14']),100.)

    def test_report_preserves_snapshot_and_contains_limitations(self):
        self.prepare()
        before=self.path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            text=lab.report(self.path,self.root/'report.md',self.root/'trades.csv')
        self.assertEqual(before,self.path.read_bytes())
        for phrase in ('OPENAI_CALLS=0','bukan hasil NET','bukan holdout baru',
                       'PROXY','Komisi, slippage'):
            self.assertIn(phrase,text)
        self.assertTrue((self.root/'trades.csv').is_file())

    def test_output_cannot_equal_snapshot(self):
        with self.assertRaises(ValueError):
            lab.report(self.path,self.path,self.root/'trades.csv')

    def test_no_openai_or_monitor_imports_or_order_calls(self):
        tree=ast.parse(Path(lab.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                self.assertFalse(any(n.name.split('.')[0] in {'openai','dotenv','market_monitor'} for n in node.names))
            if isinstance(node,ast.ImportFrom):
                self.assertNotIn((node.module or '').split('.')[0],{'openai','dotenv','market_monitor'})
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                self.assertNotIn(node.func.attr,{'order_send','login','account_info'})


if __name__=='__main__':
    unittest.main()
