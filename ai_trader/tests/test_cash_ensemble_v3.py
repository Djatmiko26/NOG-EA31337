"""Synthetic safety tests for Cash Ensemble V3. No network/OpenAI/orders."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

import cash_ensemble_v3 as v3
import ensemble_logic as e


def decision(action="WAIT"):
    names=["ema_trend","ema_slope","breakout","pullback","rsi_momentum",
           "candle_impulse","market_structure","volume_impulse",
           "atr_expansion","execution_quality"]
    votes=tuple(e.Vote(n,"WAIT","test",0.0) for n in names)
    return e.EnsembleDecision(action,0,0,10,0,0,0,0,0.0,0.0,"test",votes)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"v3.sqlite3"
        self.ledger=v3.Ledger(self.path,{"test":1})
        self.addCleanup(self.ledger.close)

    def test_local_wait_uses_no_api(self):
        d=decision("WAIT")
        sid=self.ledger.reserve(100,d,{"p":1})
        self.assertIsNotNone(sid)
        self.ledger.finish(100,{"status":"SUCCESS","reason":"LOCAL_WAIT",
                                "final_action":"WAIT","delivery":"NOT_PUBLISHED"})
        self.assertEqual(self.ledger.api_count(),0)
        self.assertEqual(self.ledger.event_count(),1)

    def test_api_reservation_is_persistent(self):
        d=decision("BUY")
        self.assertIsNotNone(self.ledger.reserve(101,d,{}))
        self.ledger.mark_api(101)
        self.assertEqual(self.ledger.api_count(),1)
        self.assertTrue(self.ledger.blocked())

    def test_duplicate_bar_rejected(self):
        d=decision()
        self.ledger.reserve(102,d,{})
        self.ledger.finish(102,{"status":"SUCCESS"})
        self.assertIsNone(self.ledger.reserve(102,d,{}))

    def test_frozen_spec(self):
        self.ledger.close()
        reopened=v3.Ledger(self.path,{"test":1})
        reopened.close()
        with self.assertRaisesRegex(ValueError,"V3_FROZEN_SETTINGS_CHANGED"):
            bad=v3.Ledger(self.path,{"test":2})
            bad.close()

    def test_status_read_only(self):
        d=decision()
        self.ledger.reserve(103,d,{})
        self.ledger.finish(103,{"status":"SUCCESS","reason":"LOCAL_WAIT",
                                "final_action":"WAIT","delivery":"NOT_PUBLISHED"})
        self.ledger.close()
        before=self.path.read_bytes()
        with patch.object(v3,"DB",self.path),contextlib.redirect_stdout(io.StringIO()) as out:
            v3.status()
        self.assertEqual(before,self.path.read_bytes())
        self.assertIn("API=0/",out.getvalue())


class SourceSafetyTests(unittest.TestCase):
    def test_preview_only_no_order_primitives(self):
        text=Path(v3.__file__).read_text(encoding="utf-8")
        self.assertNotIn("OrderSend(",text)
        self.assertNotIn("order_send(",text)
        self.assertNotIn("--demo-orders",text)
        self.assertNotIn("TRADE_ACTION_DEAL",text)
        self.assertIn('"PREVIEW"',text)
        self.assertIn("AI_MIN_CONFIDENCE",text)

    def test_independent_ai_payload_does_not_embed_ensemble(self):
        text=Path(v3.__file__).read_text(encoding="utf-8")
        call="core.request_analysis(client,feed.config.model,payload)"
        self.assertIn(call,text)
        self.assertNotIn('payload["ensemble"',text)

    def test_one_event_per_run_guard(self):
        text=Path(v3.__file__).read_text(encoding="utf-8")
        self.assertIn("initial=ledger.event_count()",text)
        self.assertIn("while ledger.event_count()==initial:",text)
        self.assertIn("V3_DELIVERY_WINDOW",text)


if __name__=="__main__":
    unittest.main()
