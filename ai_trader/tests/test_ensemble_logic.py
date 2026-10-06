"""Synthetic tests for ten-logic ensemble engine. No MT5/OpenAI/orders."""
import unittest

import ensemble_logic as e


def vote(name,signal):
    return e.Vote(name,signal,"test",1.0 if signal in {"BUY","SELL","VETO"} else 0.0)


class AggregateTests(unittest.TestCase):
    def names(self):
        return ["ema_trend","ema_slope","breakout","pullback","rsi_momentum",
                "candle_impulse","market_structure","volume_impulse",
                "atr_expansion","execution_quality"]

    def test_strong_buy_passes(self):
        names=self.names()
        signals=["BUY","BUY","BUY","BUY","BUY","BUY","BUY","WAIT","WAIT","WAIT"]
        d=e.aggregate_votes(tuple(vote(n,s) for n,s in zip(names,signals)))
        self.assertEqual(d.action,"BUY")
        self.assertEqual(d.buy_votes,7)
        self.assertGreaterEqual(d.core_support,2)
        self.assertEqual(d.suggested_rr,1.25)

    def test_weak_consensus_waits(self):
        names=self.names()
        signals=["BUY","BUY","BUY","BUY","BUY","WAIT","WAIT","WAIT","WAIT","WAIT"]
        d=e.aggregate_votes(tuple(vote(n,s) for n,s in zip(names,signals)))
        self.assertEqual(d.action,"WAIT")
        self.assertIn("support",d.reason)

    def test_margin_conflict_waits(self):
        names=self.names()
        signals=["BUY","BUY","BUY","BUY","BUY","BUY","SELL","SELL","SELL","WAIT"]
        d=e.aggregate_votes(tuple(vote(n,s) for n,s in zip(names,signals)))
        self.assertEqual(d.action,"BUY")  # 6 vs 3 => required margin 3

        signals=["BUY","BUY","BUY","BUY","BUY","SELL","SELL","SELL","WAIT","WAIT"]
        d=e.aggregate_votes(tuple(vote(n,s) for n,s in zip(names,signals)))
        self.assertEqual(d.action,"WAIT")

    def test_veto_always_waits(self):
        names=self.names()
        signals=["BUY"]*9+["VETO"]
        d=e.aggregate_votes(tuple(vote(n,s) for n,s in zip(names,signals)))
        self.assertEqual(d.action,"WAIT")
        self.assertEqual(d.vetoes,1)
        self.assertEqual(d.suggested_rr,0.0)

    def test_exactly_ten_unique_logics_required(self):
        with self.assertRaisesRegex(ValueError,"EXACTLY_10"):
            e.aggregate_votes(tuple(vote("x"+str(i),"BUY") for i in range(9)))


class PayloadTests(unittest.TestCase):
    def payload(self,spread=.2):
        rows=[]
        price=4300.0
        for i in range(30):
            close=price+i*1.0
            rows.append({
                "open":close-.7,"high":close+.8,"low":close-1.0,"close":close,
                "tick_volume":100+i*3,
                "ema20":close-2.0,"ema50":close-5.0,
                "atr14":5.0,"rsi14":62.0,
            })
        return {"candles":rows,"current_quote":{"spread_price":spread}}

    def test_evaluate_returns_ten_votes(self):
        d=e.evaluate(self.payload())
        self.assertEqual(len(d.votes),10)
        self.assertEqual(len({v.name for v in d.votes}),10)
        self.assertIn(d.action,{"BUY","SELL","WAIT"})

    def test_bad_spread_vetoes(self):
        d=e.evaluate(self.payload(spread=1.0))
        self.assertEqual(d.action,"WAIT")
        self.assertTrue(any(v.name=="execution_quality" and v.signal=="VETO" for v in d.votes))


if __name__=="__main__":
    unittest.main()
