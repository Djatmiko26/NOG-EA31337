import unittest

import pandas as pd

from outcome_labeller import (
    compute_outcome,
    directional_excursions_pct,
    directional_return_pct,
)


class OutcomeLabellerTests(unittest.TestCase):
    def test_directional_returns(self):
        self.assertAlmostEqual(directional_return_pct(100.0, 105.0, "BUY"), 5.0)
        self.assertAlmostEqual(directional_return_pct(100.0, 95.0, "SELL"), 5.0)
        self.assertAlmostEqual(directional_return_pct(100.0, 105.0, "SELL"), -5.0)
        self.assertIsNone(directional_return_pct(100.0, 105.0, "WAIT"))

    def test_directional_excursions(self):
        future = pd.DataFrame(
            {
                "high": [101.0, 104.0, 103.0],
                "low": [99.0, 98.0, 97.0],
            }
        )

        buy_mfe, buy_mae = directional_excursions_pct(100.0, future, "BUY")
        sell_mfe, sell_mae = directional_excursions_pct(100.0, future, "SELL")

        self.assertAlmostEqual(buy_mfe, 4.0)
        self.assertAlmostEqual(buy_mae, -3.0)
        self.assertAlmostEqual(sell_mfe, 3.0)
        self.assertAlmostEqual(sell_mae, -4.0)

    def test_compute_outcome_uses_5_10_20_future_closed_bars(self):
        decision = pd.Series(
            {
                "candle_epoch_raw": 1000,
                "candle_time_server": "2026-09-16T12:00:00",
                "candle_time_utc": "2026-09-16T09:00:00+00:00",
                "symbol": "XAUUSD",
                "timeframe": "M5",
                "close": 100.0,
                "atr14": 2.0,
                "filter_qualifies": True,
                "filter_direction": "BUY",
                "filter_bull_score": 5,
                "filter_bear_score": 1,
                "openai_called": True,
                "action": "BUY",
                "confidence": 80,
                "market_regime": "BULL_TREND",
            }
        )

        future = pd.DataFrame(
            {
                "time": list(range(1001, 1021)),
                "open": [100.0 + i * 0.1 for i in range(20)],
                "high": [101.0 + i * 0.2 for i in range(20)],
                "low": [99.0 - i * 0.05 for i in range(20)],
                "close": [100.5 + i * 0.25 for i in range(20)],
            }
        )

        outcome = compute_outcome(decision, future)

        self.assertAlmostEqual(outcome["close_5"], future.iloc[4]["close"])
        self.assertAlmostEqual(outcome["close_10"], future.iloc[9]["close"])
        self.assertAlmostEqual(outcome["close_20"], future.iloc[19]["close"])
        self.assertGreater(outcome["filter_return_20_pct"], 0)
        self.assertGreater(outcome["openai_mfe_20_pct"], 0)
        self.assertLessEqual(outcome["openai_mae_20_pct"], 0)


if __name__ == "__main__":
    unittest.main()
