from __future__ import annotations

import unittest

import pandas as pd

from strategy_filter import FilterConfig, evaluate_setup


class StrategyFilterTests(unittest.TestCase):
    def test_bullish_candidate_passes(self) -> None:
        rows = []
        for index in range(20):
            close = 100.0 + index * 0.30
            rows.append(
                {
                    "open": close - 0.10,
                    "high": close + 0.20,
                    "low": close - 0.20,
                    "close": close,
                    "spread": 2,
                    "ema20": close - 0.20,
                    "ema50": close - 0.60,
                    "rsi14": 58.0,
                    "atr14": 0.60,
                }
            )

        decision = evaluate_setup(
            pd.DataFrame(rows),
            point=0.01,
            config=FilterConfig(),
        )

        self.assertTrue(decision.qualifies)
        self.assertEqual(decision.direction, "BUY")
        self.assertGreaterEqual(decision.bull_score, 4)

    def test_low_volatility_is_skipped(self) -> None:
        rows = []
        for index in range(20):
            close = 100.0 + (index % 2) * 0.001
            rows.append(
                {
                    "open": close,
                    "high": close + 0.01,
                    "low": close - 0.01,
                    "close": close,
                    "spread": 1,
                    "ema20": 100.0,
                    "ema50": 100.0,
                    "rsi14": 50.0,
                    "atr14": 0.02,
                }
            )

        decision = evaluate_setup(
            pd.DataFrame(rows),
            point=0.01,
            config=FilterConfig(),
        )

        self.assertFalse(decision.qualifies)
        self.assertIn("low volatility", decision.reason)

    def test_large_spread_is_skipped(self) -> None:
        rows = []
        for index in range(20):
            close = 100.0 + index * 0.30
            rows.append(
                {
                    "open": close - 0.10,
                    "high": close + 0.20,
                    "low": close - 0.20,
                    "close": close,
                    "spread": 20,
                    "ema20": close - 0.20,
                    "ema50": close - 0.60,
                    "rsi14": 58.0,
                    "atr14": 0.60,
                }
            )

        decision = evaluate_setup(
            pd.DataFrame(rows),
            point=0.01,
            config=FilterConfig(),
        )

        self.assertFalse(decision.qualifies)
        self.assertIn("spread too large", decision.reason)


if __name__ == "__main__":
    unittest.main()
