"""Synthetic tests for weighted ensemble V3.1. No MT5/OpenAI/orders."""
import unittest

import ensemble_weighted_v31 as e


NAMES=[
    "market_structure","ema_trend","breakout","ema_slope","pullback",
    "rsi_momentum","candle_impulse","volume_impulse","atr_expansion",
    "execution_quality",
]


def vote(name,signal):
    weight=0.0 if name=="execution_quality" else e.WEIGHTS[name]
    return e.Vote(name,signal,weight,1.0 if signal in {"BUY","SELL","VETO"} else 0.0,"test")


class AggregateTests(unittest.TestCase):
    def test_core_sell_outweighs_many_minor_buy_votes(self):
        signals={
            "market_structure":"SELL","ema_trend":"SELL","breakout":"SELL","ema_slope":"SELL",
            "pullback":"BUY","rsi_momentum":"BUY","candle_impulse":"BUY",
            "volume_impulse":"BUY","atr_expansion":"BUY","execution_quality":"WAIT",
        }
        d=e.aggregate_votes(tuple(vote(n,signals[n]) for n in NAMES),"BEAR_TREND")
        self.assertEqual(d.action,"SELL")
        self.assertAlmostEqual(d.sell_score,7.0)
        self.assertAlmostEqual(d.buy_score,3.5)
        self.assertEqual(d.core_count,4)

    def test_range_suppresses_directional_entry(self):
        signals={n:"BUY" for n in NAMES}
        signals["execution_quality"]="WAIT"
        d=e.aggregate_votes(tuple(vote(n,signals[n]) for n in NAMES),"RANGE")
        self.assertEqual(d.action,"WAIT")
        self.assertIn("range",d.reason)

    def test_veto_always_waits(self):
        signals={n:"SELL" for n in NAMES}
        signals["execution_quality"]="VETO"
        d=e.aggregate_votes(tuple(vote(n,signals[n]) for n in NAMES),"BEAR_TREND")
        self.assertEqual(d.action,"WAIT")
        self.assertEqual(d.vetoes,1)

    def test_volatile_requires_stronger_core_support(self):
        signals={
            "market_structure":"BUY","ema_trend":"BUY","breakout":"WAIT","ema_slope":"WAIT",
            "pullback":"BUY","rsi_momentum":"BUY","candle_impulse":"BUY",
            "volume_impulse":"BUY","atr_expansion":"BUY","execution_quality":"WAIT",
        }
        d=e.aggregate_votes(tuple(vote(n,signals[n]) for n in NAMES),"VOLATILE")
        self.assertEqual(d.action,"WAIT")
        self.assertIn("core support",d.reason)

    def test_strong_trend_gets_longer_rr(self):
        signals={n:"SELL" for n in NAMES}
        signals["execution_quality"]="WAIT"
        d=e.aggregate_votes(tuple(vote(n,signals[n]) for n in NAMES),"BEAR_TREND")
        self.assertEqual(d.action,"SELL")
        self.assertEqual(d.suggested_rr,2.0)
        self.assertEqual(d.ai_min_confidence,70)

    def test_unclear_raises_ai_threshold(self):
        signals={n:"SELL" for n in NAMES}
        signals["execution_quality"]="WAIT"
        d=e.aggregate_votes(tuple(vote(n,signals[n]) for n in NAMES),"UNCLEAR")
        self.assertEqual(d.action,"SELL")
        self.assertEqual(d.ai_min_confidence,75)


class PayloadTests(unittest.TestCase):
    def payload(self,trend="bear",spread=.2):
        rows=[]
        base=4400.0
        for i in range(30):
            if trend=="bear":
                close=base-i*1.1
                ema20=close+2.0
                ema50=close+5.0
                rsi=38.0
            else:
                close=base+i*1.1
                ema20=close-2.0
                ema50=close-5.0
                rsi=62.0
            rows.append({
                "open":close+(0.7 if trend=="bear" else -0.7),
                "high":close+1.0,"low":close-1.0,"close":close,
                "tick_volume":100+i*2,
                "ema20":ema20,"ema50":ema50,"atr14":5.0,"rsi14":rsi,
            })
        return {"candles":rows,"current_quote":{"spread_price":spread}}

    def test_evaluate_returns_ten_weighted_votes(self):
        d=e.evaluate(self.payload("bear"))
        self.assertEqual(len(d.votes),10)
        self.assertEqual(len({v.name for v in d.votes}),10)
        self.assertIn(d.regime,{"BEAR_TREND","VOLATILE","UNCLEAR","RANGE","BULL_TREND"})

    def test_bad_spread_vetoes(self):
        d=e.evaluate(self.payload("bull",spread=1.0))
        self.assertEqual(d.action,"WAIT")
        self.assertTrue(any(v.name=="execution_quality" and v.signal=="VETO" for v in d.votes))


if __name__=="__main__":
    unittest.main()
