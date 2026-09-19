"""Synthetic safety tests for Cash Ensemble V3.1. No network/OpenAI/orders."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cash_ensemble_v31 as v31
import ensemble_weighted_v31 as e


def decision(action="WAIT",regime="BEAR_TREND"):
    votes=tuple(
        e.Vote(
            name,
            "WAIT",
            0.0 if name=="execution_quality" else e.WEIGHTS[name],
            0.0,
            "test",
        )
        for name in [
            "market_structure","ema_trend","breakout","ema_slope","pullback",
            "rsi_momentum","candle_impulse","volume_impulse","atr_expansion",
            "execution_quality",
        ]
    )
    return e.WeightedDecision(
        action,regime,0.0,0.0,0.0,0.0,0,0,0,10,0,0.0,0.0,
        70 if regime not in {"VOLATILE","UNCLEAR"} else 75,"test",votes
    )


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"v31.sqlite3"
        self.ledger=v31.Ledger(self.path,{"test":1})
        self.addCleanup(self.ledger.close)

    def test_local_wait_uses_no_api(self):
        d=decision("WAIT")
        sid=self.ledger.reserve(100,d,{"p":1})
        self.assertIsNotNone(sid)
        self.ledger.finish(100,{"status":"SUCCESS","reason":"LOCAL_WAIT",
                                "final_action":"WAIT","delivery":"NOT_PUBLISHED"})
        self.assertEqual(self.ledger.api_count(),0)
        self.assertEqual(self.ledger.event_count(),1)

    def test_api_mark_is_persistent(self):
        d=decision("SELL")
        self.assertIsNotNone(self.ledger.reserve(101,d,{}))
        self.ledger.mark_api(101)
        self.assertEqual(self.ledger.api_count(),1)
        self.assertTrue(self.ledger.blocked())

    def test_duplicate_bar_rejected(self):
        d=decision()
        self.ledger.reserve(102,d,{})
        self.ledger.finish(102,{"status":"SUCCESS"})
        self.assertIsNone(self.ledger.reserve(102,d,{}))

    def test_status_read_only(self):
        d=decision()
        self.ledger.reserve(103,d,{})
        self.ledger.finish(103,{"status":"SUCCESS","reason":"LOCAL_WAIT",
                                "final_action":"WAIT","delivery":"NOT_PUBLISHED"})
        self.ledger.close()
        before=self.path.read_bytes()
        with patch.object(v31,"DB",self.path),contextlib.redirect_stdout(io.StringIO()) as out:
            v31.status()
        self.assertEqual(before,self.path.read_bytes())
        self.assertIn("API=0/",out.getvalue())

    def test_frozen_spec(self):
        self.ledger.close()
        reopened=v31.Ledger(self.path,{"test":1})
        reopened.close()
        with self.assertRaisesRegex(ValueError,"V31_FROZEN_SETTINGS_CHANGED"):
            bad=v31.Ledger(self.path,{"test":2})
            bad.close()


class SourceSafetyTests(unittest.TestCase):
    def test_preview_only_no_order_primitives(self):
        text=Path(v31.__file__).read_text(encoding="utf-8")
        self.assertNotIn("OrderSend(",text)
        self.assertNotIn("order_send(",text)
        self.assertNotIn("--demo-orders",text)
        self.assertNotIn("TRADE_ACTION_DEAL",text)
        self.assertIn('"PREVIEW"',text)

    def test_ai_is_independent_and_threshold_is_dynamic(self):
        text=Path(v31.__file__).read_text(encoding="utf-8")
        self.assertIn("core.request_analysis(client,feed.config.model,payload)",text)
        self.assertNotIn('payload["ensemble"',text)
        self.assertIn("decision.ai_min_confidence",text)

    def test_one_event_per_run(self):
        text=Path(v31.__file__).read_text(encoding="utf-8")
        self.assertIn("initial=ledger.event_count()",text)
        self.assertIn("while ledger.event_count()==initial:",text)
        self.assertIn("V31_DELIVERY_WINDOW",text)


if __name__=="__main__":
    unittest.main()
