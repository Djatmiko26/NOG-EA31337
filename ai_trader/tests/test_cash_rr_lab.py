"""Synthetic tests for Cash Risk-Reward Lab. No MT5 network, API, or orders."""
import math
from pathlib import Path
import unittest
from types import SimpleNamespace as NS

import cash_rr_lab as rr


class FakeMT5:
    ORDER_TYPE_BUY=0
    ORDER_TYPE_SELL=1
    def order_calc_profit(self,typ,symbol,volume,open_price,close_price):
        # XAUUSD contract 100: P/L = direction * delta * volume * 100.
        sign=1 if typ==self.ORDER_TYPE_BUY else -1
        return sign*(close_price-open_price)*volume*100.0


class TargetTests(unittest.TestCase):
    def receipt(self,action="SELL"):
        return {
            "action":action,"entry":4377.32,"volume":0.01,"sl":4386.92,
            "tp_1r":4366.92,"loss":10.0,"profit_1r":10.0,"fee":0.0,
            "bar":1789739400,"tick_msc":1789739700000,
        }

    def test_sell_one_r_matches_exact_receipt(self):
        r=self.receipt("SELL")
        tp,target,validated=rr.target_for_ratio(FakeMT5(),r,1.0,0.01,0.01)
        self.assertAlmostEqual(tp,4366.92,places=8)
        self.assertAlmostEqual(target,10.0,places=8)
        self.assertGreaterEqual(validated,10.0-1e-8)

    def test_sell_half_r_and_two_r(self):
        r=self.receipt("SELL")
        tp05,_,_=rr.target_for_ratio(FakeMT5(),r,0.5,0.01,0.01)
        tp20,_,_=rr.target_for_ratio(FakeMT5(),r,2.0,0.01,0.01)
        self.assertAlmostEqual(tp05,4371.92,places=8)
        self.assertAlmostEqual(tp20,4356.92,places=8)

    def test_buy_targets_move_up(self):
        r=self.receipt("BUY")
        r.update(entry=4387.83,sl=4378.23,tp_1r=4398.23)
        tp05,_,_=rr.target_for_ratio(FakeMT5(),r,0.5,0.01,0.01)
        tp10,_,_=rr.target_for_ratio(FakeMT5(),r,1.0,0.01,0.01)
        tp20,_,_=rr.target_for_ratio(FakeMT5(),r,2.0,0.01,0.01)
        self.assertLess(tp05,tp10)
        self.assertLess(tp10,tp20)
        self.assertAlmostEqual(tp10,4398.23,places=8)


class PriceSideTests(unittest.TestCase):
    def test_source_has_correct_executable_side(self):
        text=Path(rr.__file__).read_text(encoding="utf-8")
        self.assertIn("price=bid if buy else ask",text)
        self.assertNotIn("OrderSend(",text)
        self.assertNotIn("order_send(",text)
        self.assertIn("NO_API",text)
        self.assertIn("NO_ORDER",text)

    def test_ratios_are_expected_set(self):
        self.assertEqual(rr.RATIOS,(0.50,0.75,1.00,1.25,1.50,1.75,2.00,2.50,3.00,4.00))


if __name__=="__main__":
    unittest.main()
