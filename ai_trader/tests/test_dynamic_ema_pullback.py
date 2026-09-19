"""Synthetic tests for Dynamic EMA Pullback strategy. No MT5/OpenAI/orders."""
import unittest

import dynamic_ema_pullback as d


def uptrend_bars():
    bars=[]
    for i in range(258):
        c=1000+i*0.8
        bars.append(d.Bar(1700000000+i*300,c-0.2,c+0.5,c-0.5,c))
    # Candle [2]: pullback close below EMA50.
    bars.append(d.Bar(1700000000+258*300,1182.0,1183.0,1179.0,1180.0))
    # Candle [1]: bullish trigger, 7.0 body, small upper wick.
    bars.append(d.Bar(1700000000+259*300,1180.0,1187.5,1179.5,1187.0))
    return bars


def downtrend_bars():
    bars=[]
    for i in range(258):
        c=1400-i*0.8
        bars.append(d.Bar(1700000000+i*300,c+0.2,c+0.5,c-0.5,c))
    # Candle [2]: pullback close above EMA50.
    bars.append(d.Bar(1700000000+258*300,1218.0,1221.0,1217.0,1220.0))
    # Candle [1]: bearish trigger, 7.0 body, small lower wick.
    bars.append(d.Bar(1700000000+259*300,1220.0,1220.5,1212.5,1213.0))
    return bars


class EntryTests(unittest.TestCase):
    def test_buy_signal_matches_spec(self):
        x=d.evaluate(uptrend_bars())
        self.assertEqual(x.action,"BUY")
        self.assertGreater(x.ema_fast,x.ema_slow)
        self.assertGreaterEqual(x.trigger_body,6.0)
        self.assertLessEqual(x.opposite_wick_ratio,0.20)
        self.assertAlmostEqual(x.sl,1178.5,places=8)
        risk=1187.0-1178.5
        self.assertAlmostEqual(x.tp,1187.0+1.5*risk,places=8)

    def test_sell_signal_matches_spec(self):
        x=d.evaluate(downtrend_bars())
        self.assertEqual(x.action,"SELL")
        self.assertLess(x.ema_fast,x.ema_slow)
        self.assertGreaterEqual(x.trigger_body,6.0)
        self.assertLessEqual(x.opposite_wick_ratio,0.20)
        self.assertAlmostEqual(x.sl,1221.5,places=8)
        risk=1221.5-1213.0
        self.assertAlmostEqual(x.tp,1213.0-1.5*risk,places=8)

    def test_bad_opposite_wick_rejects_buy(self):
        bars=uptrend_bars()
        t=bars[-1]
        bars[-1]=d.Bar(t.time,t.open,1191.0,t.low,t.close)
        x=d.evaluate(bars)
        self.assertEqual(x.action,"WAIT")
        self.assertIn("opposite wick",x.reason)

    def test_small_body_rejects(self):
        bars=uptrend_bars()
        t=bars[-1]
        bars[-1]=d.Bar(t.time,1182.0,1187.5,1179.5,1187.0)
        x=d.evaluate(bars)
        self.assertEqual(x.action,"WAIT")
        self.assertIn("trigger body",x.reason)

    def test_needs_long_history_for_ema200(self):
        with self.assertRaisesRegex(ValueError,"MORE_HISTORY"):
            d.evaluate(uptrend_bars()[:100])


class PositionLimitTests(unittest.TestCase):
    def test_default_max_three(self):
        self.assertTrue(d.can_open(0))
        self.assertTrue(d.can_open(2))
        self.assertFalse(d.can_open(3))
        self.assertFalse(d.can_open(4))


class ManagementTests(unittest.TestCase):
    def test_buy_breakeven_before_trailing(self):
        cfg=d.Config()
        x=d.manage_position("BUY",100.0,101.6,95.0,cfg)
        self.assertTrue(x.breakeven_active)
        self.assertFalse(x.trailing_active)
        self.assertAlmostEqual(x.new_sl,100.0)

    def test_buy_trailing_only_moves_stop_forward(self):
        x=d.manage_position("BUY",100.0,104.0,101.0)
        self.assertTrue(x.trailing_active)
        self.assertAlmostEqual(x.new_sl,102.0)
        y=d.manage_position("BUY",100.0,103.0,102.5)
        self.assertAlmostEqual(y.new_sl,102.5)

    def test_sell_breakeven_and_trailing(self):
        x=d.manage_position("SELL",100.0,96.0,105.0)
        self.assertTrue(x.breakeven_active)
        self.assertTrue(x.trailing_active)
        self.assertAlmostEqual(x.new_sl,98.0)


class ConfigTests(unittest.TestCase):
    def test_source_default_equivalences(self):
        cfg=d.Config()
        self.assertAlmostEqual(cfg.min_body_price,6.0)
        self.assertAlmostEqual(cfg.sl_buffer_price,1.0)
        self.assertAlmostEqual(cfg.breakeven_trigger_price,1.5)
        self.assertAlmostEqual(cfg.trailing_price,2.0)
        self.assertAlmostEqual(cfg.risk_reward,1.5)


if __name__=="__main__":
    unittest.main()
