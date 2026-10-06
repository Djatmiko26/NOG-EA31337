import unittest

import pandas as pd

from research_report import (
    build_research_report,
    confidence_bucket,
    summarize_returns,
)


class ResearchReportTests(unittest.TestCase):
    def test_confidence_bucket_boundaries(self):
        self.assertEqual(confidence_bucket(59), "00-59")
        self.assertEqual(confidence_bucket(60), "60-69")
        self.assertEqual(confidence_bucket(70), "70-79")
        self.assertEqual(confidence_bucket(80), "80-89")
        self.assertEqual(confidence_bucket(90), "90-100")

    def test_summarize_returns(self):
        df = pd.DataFrame(
            {
                "ret": [1.0, -0.5, 0.5, 0.0],
                "mfe": [1.4, 0.2, 0.8, 0.3],
                "mae": [-0.2, -0.9, -0.1, -0.3],
            }
        )

        stats = summarize_returns(
            df,
            "ret",
            mfe_column="mfe",
            mae_column="mae",
        )

        self.assertEqual(stats["n"], 4)
        self.assertAlmostEqual(stats["mean"], 0.25)
        self.assertAlmostEqual(stats["win_rate"], 50.0)
        self.assertAlmostEqual(stats["loss_rate"], 25.0)
        self.assertAlmostEqual(stats["flat_rate"], 25.0)
        self.assertAlmostEqual(stats["avg_mfe"], 0.675)
        self.assertAlmostEqual(stats["avg_mae"], -0.375)

    def test_report_contains_efficiency_and_ai_sections(self):
        decisions = pd.DataFrame(
            [
                {
                    "symbol": "XAUUSD",
                    "timeframe": "M5",
                    "filter_qualifies": True,
                    "openai_called": True,
                    "action": "BUY",
                },
                {
                    "symbol": "XAUUSD",
                    "timeframe": "M5",
                    "filter_qualifies": False,
                    "openai_called": False,
                    "action": "",
                },
            ]
        )

        outcomes = pd.DataFrame(
            [
                {
                    "symbol": "XAUUSD",
                    "timeframe": "M5",
                    "filter_qualifies": True,
                    "filter_direction": "BUY",
                    "openai_called": True,
                    "action": "BUY",
                    "confidence": 82,
                    "market_regime": "BULL_TREND",
                    "filter_return_5_pct": 0.1,
                    "filter_return_10_pct": 0.2,
                    "filter_return_20_pct": 0.3,
                    "filter_mfe_20_pct": 0.5,
                    "filter_mae_20_pct": -0.1,
                    "openai_return_5_pct": 0.1,
                    "openai_return_10_pct": 0.2,
                    "openai_return_20_pct": 0.3,
                    "openai_mfe_20_pct": 0.5,
                    "openai_mae_20_pct": -0.1,
                }
            ]
        )

        report = build_research_report(
            decisions,
            outcomes,
            symbol="XAUUSD",
            timeframe="M5",
        )

        self.assertIn("Pipeline / API Call Efficiency", report)
        self.assertIn("OpenAI Directional Performance", report)
        self.assertIn("OpenAI Confidence Buckets", report)
        self.assertIn("50.0%", report)


if __name__ == "__main__":
    unittest.main()
