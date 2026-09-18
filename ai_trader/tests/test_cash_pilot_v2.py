"""Synthetic tests for Cash Pilot V2. No network, OpenAI, or broker orders."""
import contextlib
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import cash_pilot_v2 as v2


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"v2.sqlite3"
        self.ledger=v2.Ledger(self.path,{"test":1})
        self.addCleanup(self.ledger.close)

    def test_preview_only_and_duplicate_bar(self):
        sid=self.ledger.reserve(100,"PREVIEW",{"x":1})
        self.assertRegex(sid,r"^[0-9a-f]{32}$")
        self.ledger.finish(100,{"status":"SUCCESS","signal":{"action":"WAIT"}})
        self.assertIsNone(self.ledger.reserve(100,"PREVIEW",{"x":2}))
        with self.assertRaisesRegex(ValueError,"INVALID_V2_RESERVATION"):
            self.ledger.reserve(101,"DEMO_SEND",{})

    def test_unresolved_attempt_blocks_future_attempts(self):
        self.assertIsNotNone(self.ledger.reserve(100,"PREVIEW",{}))
        self.assertTrue(self.ledger.blocked())
        self.assertIsNone(self.ledger.reserve(101,"PREVIEW",{}))

    def test_cap_is_persistent(self):
        for bar in range(100,100+v2.CAP):
            self.assertIsNotNone(self.ledger.reserve(bar,"PREVIEW",{}))
            self.ledger.finish(bar,{"status":"SUCCESS","signal":{"action":"WAIT"}})
        self.assertEqual(self.ledger.count(),v2.CAP)
        self.assertIsNone(self.ledger.reserve(999,"PREVIEW",{}))

    def test_frozen_spec(self):
        self.ledger.close()
        self.ledger=v2.Ledger(self.path,{"test":1})
        self.ledger.close()
        with self.assertRaisesRegex(ValueError,"V2_FROZEN_SETTINGS_CHANGED"):
            v2.Ledger(self.path,{"test":2})
        self.ledger=v2.Ledger(self.path,{"test":1})

    def test_status_is_read_only(self):
        self.ledger.reserve(100,"PREVIEW",{})
        self.ledger.finish(100,{"status":"SUCCESS","delivery":"READY_FOR_EA",
                                "signal":{"action":"BUY"}})
        self.ledger.close()
        before=self.path.read_bytes()
        with patch.object(v2,"DB",self.path),contextlib.redirect_stdout(io.StringIO()) as out:
            v2.status()
        self.assertEqual(before,self.path.read_bytes())
        self.assertIn("DIRECTIONAL=1",out.getvalue())
        self.ledger=v2.Ledger(self.path,{"test":1})


class SourceSafetyTests(unittest.TestCase):
    def test_no_order_primitives_or_demo_send_cli(self):
        text=(Path(v2.__file__).read_text(encoding="utf-8"))
        self.assertNotIn("OrderSend(",text)
        self.assertNotIn("order_send(",text)
        self.assertNotIn("TRADE_ACTION_DEAL",text)
        self.assertNotIn("--demo-orders",text)
        self.assertIn('"PREVIEW"',text)

    def test_run_loop_is_one_reserved_attempt_per_invocation(self):
        text=Path(v2.__file__).read_text(encoding="utf-8")
        self.assertIn("initial = ledger.count()",text)
        self.assertIn("while ledger.count() == initial:",text)
        self.assertIn("break",text)


if __name__=="__main__":
    unittest.main()
