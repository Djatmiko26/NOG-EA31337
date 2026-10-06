"""Synthetic tests for Dynamic EMA Pullback Historical Lab. No MT5/API/orders."""
import unittest

import dynamic_ema_pullback as dyn
import dynamic_ema_pullback_lab as lab


def hb(time,o,h,l,c,spread=20):
    return lab.HistBar(time,o,h,l,c,spread)


class OutcomeTests(unittest.TestCase):
    def test_buy_uses_bid_for_exit(self):
        bars=[
            hb(100,100,101,99,100),
            hb(400,100,106,98,105),
            hb(700,105,111,104,110),
        ]
        s=lab.Signal(1,400,"BUY",105,95,10,105,0,5,.1,100,90)
        out=lab.evaluate_outcome(s,bars,0.01,0.5)
        self.assertEqual(out.status,"TP_FIRST")
        self.assertEqual(out.resolve_index,2)

    def test_sell_uses_ask_approx_for_stop(self):
        # BID high is below SL, but ASK high including 0.20 spread reaches it.
        bars=[
            hb(100,100,101,99,100,20),
            hb(400,100,101,99,100,20),
            hb(700,100,109.9,98,100,20),
        ]
        s=lab.Signal(1,400,"SELL",100,110,10,100,.2,5,.1,90,100)
        out=lab.evaluate_outcome(s,bars,0.01,1.0)
        self.assertEqual(out.status,"SL_FIRST")

    def test_same_bar_both_is_ambiguous(self):
        bars=[
            hb(100,100,101,99,100),
            hb(400,100,101,99,100),
            hb(700,100,111,89,100),
        ]
        s=lab.Signal(1,400,"BUY",100,90,10,100,0,5,.1,100,90)
        out=lab.evaluate_outcome(s,bars,0.01,1.0)
        self.assertEqual(out.status,"AMBIGUOUS")

    def test_max_hold_can_leave_unresolved(self):
        bars=[
            hb(100,100,101,99,100),
            hb(400,100,101,99,100),
            hb(700,100,102,98,100),
            hb(1000,100,111,99,110),
        ]
        s=lab.Signal(1,400,"BUY",100,90,10,100,0,5,.1,100,90)
        out=lab.evaluate_outcome(s,bars,0.01,1.0,max_hold_bars=1)
        self.assertEqual(out.status,"UNRESOLVED")


class MetricTests(unittest.TestCase):
    def test_expectancy(self):
        signals=[
            lab.Signal(1,1,"BUY",100,90,10,100,0,5,.1,1,1),
            lab.Signal(2,2,"BUY",100,90,10,100,0,5,.1,1,1),
            lab.Signal(3,3,"BUY",100,90,10,100,0,5,.1,1,1),
            lab.Signal(4,4,"BUY",100,90,10,100,0,5,.1,1,1),
        ]
        outcomes=[
            lab.Outcome("TP_FIRST",2,1),
            lab.Outcome("TP_FIRST",3,1),
            lab.Outcome("SL_FIRST",4,1),
            lab.Outcome("SL_FIRST",5,1),
        ]
        m=lab.metrics(signals,outcomes,1.5)
        self.assertAlmostEqual(m["win"],0.5)
        self.assertAlmostEqual(m["expectancy"],0.25)
        self.assertEqual(m["max_losing_streak"],2)

    def test_position_cap(self):
        signals=[
            lab.Signal(1,1,"BUY",100,90,10,100,0,5,.1,1,1),
            lab.Signal(2,2,"BUY",100,90,10,100,0,5,.1,1,1),
            lab.Signal(3,3,"BUY",100,90,10,100,0,5,.1,1,1),
            lab.Signal(4,4,"BUY",100,90,10,100,0,5,.1,1,1),
        ]
        outcomes=[
            lab.Outcome("TP_FIRST",10,9),
            lab.Outcome("TP_FIRST",10,8),
            lab.Outcome("TP_FIRST",10,7),
            lab.Outcome("TP_FIRST",10,6),
        ]
        accepted,_=lab.apply_position_cap(signals,outcomes,3)
        self.assertEqual(len(accepted),3)


class SourceSafetyTests(unittest.TestCase):
    def test_ratios_expected(self):
        self.assertEqual(lab.RATIOS,(0.50,0.75,1.00,1.25,1.50,1.75,2.00,2.50,3.00,4.00))

    def test_no_order_or_openai_primitives(self):
        from pathlib import Path
        text=Path(lab.__file__).read_text(encoding="utf-8")
        self.assertNotIn("OrderSend(",text)
        self.assertNotIn("order_send(",text)
        self.assertNotIn("from openai",text.lower())
        self.assertIn("NO_API",text)
        self.assertIn("NO_ORDER",text)


if __name__=="__main__":
    unittest.main()
