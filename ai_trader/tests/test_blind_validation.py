"""Offline regression tests. Synthetic candles, mock API, temporary SQLite only."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import openai_blind_validation as blind
from pilot_support import INSTRUCTIONS, PROMPT_VERSION, SCHEMA, PilotStore, encode


def signal(action="BUY"):
    return {"action": action, "confidence": 72, "market_regime": "RANGE", "reason": "Synthetic test."}


def result(action="BUY", status="SUCCESS"):
    return {"status": status, "signal": signal(action), "model_returned": "test-model",
            "response_id": "test-response", "input_tokens": 100, "output_tokens": 20}


def fixture(count=3):
    spec = {"model": "test-model", "instructions": INSTRUCTIONS,
            "prompt_version": PROMPT_VERSION, "schema": copy.deepcopy(SCHEMA),
            "max_output_tokens": 2048, "reasoning_effort": "low"}
    samples, attempts = [], {}
    for i in range(count):
        direction = "BUY" if i % 2 == 0 else "SELL"
        raw = [2., -3., -1.][i % 3]
        payload = {
            "symbol": "XAUUSD", "timeframe": "M5",
            "latest_closed_candle_utc": "2026-09-09T00:05:00Z",
            "detected_server_utc_offset_hours": 3.0,
            "local_filter": {"candidate_direction": direction, "bull_score": 5,
                             "bear_score": 1, "reason": "PRIVATE_FILTER_HINT", "metrics": {}},
            "candles": [{"time": time, "open": 100., "high": 102., "low": 98., "close": 101.,
                         "tick_volume": 800, "spread": 10, "ema20": 100., "ema50": 99.,
                         "rsi14": 55., "atr14": 4.} for time in
                        ("2026-09-09T00:00:00Z", "2026-09-09T00:05:00Z")],
        }
        outcome = {"entry_close": 101., "future_high_20": 104., "future_low_20": 97.,
                   "private_future_test": "NEVER_SEND_FUTURE"}
        for h in (5, 10, 20):
            outcome[f"market_return_{h}_pct"] = raw
            outcome[f"filter_return_{h}_pct"] = raw if direction == "BUY" else -raw
        samples.append({"sample_id": str(i), "payload": payload, "outcome": outcome,
                        "row": {"filter_direction": direction, "candle_epoch_raw": 1_780_000_000 + i * 6000,
                                "candle_time_server": "2026-09-09T03:05:00"}})
        attempts[i] = result(direction)
    return {"version": 2, "spec": spec, "samples": samples}, attempts


def api_response(action="BUY", status="completed", text=None, refusal=False):
    return SimpleNamespace(status=status,
        output=[SimpleNamespace(content=[SimpleNamespace(type="refusal")])] if refusal else [],
        output_text=json.dumps(signal(action)) if text is None else text,
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        id="mock-response", model="test-model")


class BlindValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.store = PilotStore(self.path / "blind.sqlite3")
        self.addCleanup(self.store.close)

    def save_source(self, count=3):
        source, attempts = fixture(count)
        path = self.path / "original.sqlite3"
        store = PilotStore(path)
        try:
            store.save_plan(source)
            for i, value in attempts.items():
                store.claim(i)
                store.finish(i, value)
        finally:
            store.close()
        return path

    def save_blind(self, count=3):
        source, attempts = fixture(count)
        plan = blind.make_plan(source, attempts)
        self.store.save_plan(plan)
        return plan

    def test_removes_only_local_filter_without_mutating_source(self):
        source, attempts = fixture()
        before = encode(source)
        plan = blind.make_plan(source, attempts)
        self.assertEqual(encode(source), before)
        self.assertEqual(plan["spec"], source["spec"])
        for old, new in zip(source["samples"], plan["samples"]):
            self.assertNotIn("local_filter", new["payload"])
            expected = {k: v for k, v in old["payload"].items() if k != "local_filter"}
            self.assertEqual(encode(expected), encode(new["payload"]))
            self.assertEqual(old["row"], new["row"])
            self.assertEqual(old["outcome"], new["outcome"])
            self.assertEqual(old["sample_id"], new["sample_id"])

    def test_unknown_top_level_field_rejected(self):
        payload = fixture()[0]["samples"][0]["payload"]
        payload["candidate_direction"] = "BUY"
        with self.assertRaises(ValueError):
            blind.blind_payload(payload)

    def test_unknown_candle_field_rejected(self):
        payload = fixture()[0]["samples"][0]["payload"]
        payload["candles"][0]["future_return"] = 1.
        with self.assertRaises(ValueError):
            blind.blind_payload(payload)

    def test_nested_direction_in_numeric_field_rejected(self):
        payload = fixture()[0]["samples"][0]["payload"]
        payload["candles"][0]["close"] = {"direction": "BUY"}
        with self.assertRaises(ValueError):
            blind.blind_payload(payload)

    def test_nonfinite_or_boolean_prices_rejected(self):
        for bad in (float("nan"), float("inf"), True, "BUY"):
            with self.subTest(bad=bad):
                payload = fixture()[0]["samples"][0]["payload"]
                payload["candles"][0]["close"] = bad
                with self.assertRaises(ValueError):
                    blind.blind_payload(payload)

    def test_latest_boundary_and_unsorted_candles_rejected(self):
        payload = fixture()[0]["samples"][0]["payload"]
        payload["latest_closed_candle_utc"] = "2026-09-09T00:00:00Z"
        with self.assertRaises(ValueError):
            blind.blind_payload(payload)
        payload = fixture()[0]["samples"][0]["payload"]
        payload["candles"].reverse()
        with self.assertRaises(ValueError):
            blind.blind_payload(payload)

    def test_future_free_payload_sent_not_saved_outcomes_or_old_answers(self):
        plan = self.save_blind(1)
        client = Mock()
        client.responses.create.return_value = api_response()
        blind.execute_blind(self.store, client, lambda _: None)
        call = client.responses.create.call_args.kwargs
        self.assertEqual(json.loads(call["input"]), plan["samples"][0]["payload"])
        for forbidden in ("PRIVATE_FILTER_HINT", "NEVER_SEND_FUTURE", "baseline_attempts",
                          "candidate_direction", "bull_score", "bear_score"):
            self.assertNotIn(forbidden, str(call))
        self.assertEqual(call["instructions"], plan["source_plan"]["spec"]["instructions"])
        self.assertEqual(call["reasoning"], {"effort": "low"})
        self.assertEqual(call["max_output_tokens"], 2048)
        self.assertFalse(call["store"])

    def test_requires_complete_successful_source(self):
        for status in ("ERROR", "STARTED", "REFUSED", "INCOMPLETE", "INVALID"):
            source, attempts = fixture()
            attempts[1]["status"] = status
            with self.assertRaises(ValueError):
                blind.make_plan(source, attempts)
        source, attempts = fixture()
        del attempts[1]
        with self.assertRaises(ValueError):
            blind.make_plan(source, attempts)

    def test_ten_sample_cap_and_duplicate_rejection(self):
        source, attempts = fixture(11)
        with self.assertRaises(ValueError):
            blind.make_plan(source, attempts)
        source, attempts = fixture()
        source["samples"][1]["sample_id"] = source["samples"][0]["sample_id"]
        with self.assertRaises(ValueError):
            blind.make_plan(source, attempts)

    def test_inherited_token_cap_and_prompt_validated(self):
        for name, value in (("max_output_tokens", 9000), ("max_output_tokens", True),
                            ("instructions", "new prompt"), ("schema", {})):
            source, attempts = fixture()
            source["spec"][name] = value
            with self.assertRaises(ValueError):
                blind.make_plan(source, attempts)

    def test_model_or_candles_cannot_change_after_preparation(self):
        original = self.save_blind()
        for edit in ("model", "candles", "outcome"):
            plan = copy.deepcopy(original)
            if edit == "model":
                plan["spec"]["model"] = "different-model"
            elif edit == "candles":
                plan["samples"][0]["payload"]["candles"][0]["close"] = 100.
            else:
                plan["samples"][0]["outcome"]["market_return_20_pct"] = 999.
            with self.assertRaises(ValueError):
                blind.validate_plan(plan)

    def test_source_read_is_byte_preserving_and_readonly(self):
        path = self.save_source()
        before = path.read_bytes()
        original_connect = sqlite3.connect
        with patch.object(blind.sqlite3, "connect", wraps=original_connect) as connect:
            source, attempts = blind.read_source(path)
            self.assertIn("mode=ro", connect.call_args.args[0])
            self.assertTrue(connect.call_args.kwargs["uri"])
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(len(attempts), len(source["samples"]))

    def test_missing_source_not_created(self):
        missing = self.path / "missing.sqlite3"
        with self.assertRaises(ValueError):
            blind.read_source(missing)
        self.assertFalse(missing.exists())

    def test_corrupt_source_fingerprint_rejected(self):
        path = self.save_source()
        db = sqlite3.connect(path)
        db.execute("UPDATE plan SET fingerprint='wrong'")
        db.commit()
        db.close()
        with self.assertRaises(ValueError):
            blind.read_source(path)

    def test_prepare_reuses_plan_even_without_original_file(self):
        path = self.save_source()
        saved = blind.prepare(self.store, path)
        path.unlink()
        self.assertEqual(blind.prepare(self.store, path), saved)

    def test_prepare_does_not_replace_old_database(self):
        path = self.save_source()
        before = path.read_bytes()
        blind.prepare(self.store, path)
        self.assertEqual(path.read_bytes(), before)

    def test_source_and_destination_cannot_be_same(self):
        with self.assertRaises(ValueError):
            blind.prepare(self.store, self.path / "blind.sqlite3")

    def test_reservation_committed_before_mock_request(self):
        self.save_blind(1)
        other = PilotStore(self.path / "blind.sqlite3")
        self.addCleanup(other.close)
        client = Mock()
        def check(**_):
            self.assertEqual(other.attempts()[0]["status"], "STARTED")
            self.assertFalse(other.claim(0))
            return api_response()
        client.responses.create.side_effect = check
        blind.execute_blind(self.store, client, lambda _: None)
        self.assertEqual(self.store.attempts()[0]["status"], "SUCCESS")

    def test_completed_run_makes_zero_new_calls(self):
        self.save_blind(10)
        client = Mock()
        client.responses.create.return_value = api_response()
        blind.execute_blind(self.store, client, lambda _: None)
        self.assertEqual(client.responses.create.call_count, 10)
        blind.execute_blind(self.store, client, lambda _: None)
        self.assertEqual(client.responses.create.call_count, 10)

    def test_error_stops_and_resume_skips_failed_sample(self):
        self.save_blind(2)
        client = Mock()
        client.responses.create.side_effect = RuntimeError("PRIVATE_KEY_NEVER_PRINT")
        blind.execute_blind(self.store, client, lambda _: None)
        self.assertEqual(client.responses.create.call_count, 1)
        self.assertEqual(self.store.attempts()[0]["status"], "ERROR")
        self.assertNotIn("PRIVATE_KEY_NEVER_PRINT", encode(self.store.attempts()))
        client.responses.create.side_effect = None
        client.responses.create.return_value = api_response()
        blind.execute_blind(self.store, client, lambda _: None)
        self.assertEqual(client.responses.create.call_count, 2)
        self.assertEqual(self.store.attempts()[0]["status"], "ERROR")

    def test_keyboard_interrupt_does_not_retry_uncertain_attempt(self):
        self.save_blind(2)
        client = Mock()
        client.responses.create.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            blind.execute_blind(self.store, client, lambda _: None)
        self.assertEqual(self.store.attempts()[0]["status"], "STARTED")
        client.responses.create.side_effect = None
        client.responses.create.return_value = api_response()
        blind.execute_blind(self.store, client, lambda _: None)
        self.assertEqual(client.responses.create.call_count, 2)
        self.assertEqual(self.store.attempts()[0]["status"], "STARTED")

    def test_refused_incomplete_and_invalid_are_not_wait(self):
        variants = [(api_response(refusal=True), "REFUSED"),
                    (api_response(status="incomplete"), "INCOMPLETE"),
                    (api_response(text="not JSON"), "INVALID")]
        plan = self.save_blind(3)
        client = Mock()
        for response, status in variants:
            client.responses.create.return_value = response
            blind.execute_blind(self.store, client, lambda _: None)
        self.assertEqual([v["status"] for v in self.store.attempts().values()],
                         ["REFUSED", "INCOMPLETE", "INVALID"])
        self.assertEqual(blind.matched_metrics(blind.comparison_rows(plan, self.store.attempts()), 20)["n"], 0)

    def test_same_denominator_with_wait_and_opposite_direction(self):
        plan = self.save_blind()
        new = {0: result("WAIT"), 1: result("BUY"), 2: result("SELL")}
        metrics = blind.matched_metrics(blind.comparison_rows(plan, new), 20)
        self.assertEqual(metrics["n"], 3)
        self.assertAlmostEqual(metrics["local"], 4 / 3)
        self.assertAlmostEqual(metrics["original"], 4 / 3)
        self.assertAlmostEqual(metrics["blind"], -2 / 3)
        self.assertAlmostEqual(metrics["delta"], -2.)
        self.assertEqual(metrics["agree_only"], 0.)

    def test_failure_excludes_original_and_local_from_matched_denominator(self):
        plan = self.save_blind()
        new = {0: result("WAIT"), 1: {"status": "ERROR"}, 2: result("SELL")}
        rows = blind.comparison_rows(plan, new)
        m = blind.matched_metrics(rows, 20)
        self.assertEqual(m["n"], 2)
        self.assertEqual(m["local"], .5)
        self.assertEqual(m["original"], .5)
        self.assertEqual(m["blind"], .5)
        self.assertEqual(rows[1]["blind_20_pct"], "")
        report = blind.build_report(plan, new)
        self.assertIn("failed/uncertain: 1", report)
        self.assertIn("Blind WAIT: 1", report)

    def test_no_responses_report_has_no_fake_zero_returns(self):
        plan = self.save_blind()
        self.assertIn("| 20 | 0 | - | - | - |", blind.build_report(plan, {}))

    def test_model_mismatch_and_unknown_usage_are_reported(self):
        plan = self.save_blind(1)
        new = {0: result()}
        new[0]["model_returned"] = "changed-backend-id"
        new[0].pop("input_tokens")
        report = blind.build_report(plan, new)
        self.assertIn("Different returned model IDs: 1", report)
        self.assertIn("input_tokens: 0; observed on 0/1", report)
        self.assertIn("Unknown usage is not zero cost", report)

    def test_report_explicitly_limits_causal_and_profit_claims(self):
        plan = self.save_blind()
        report = blind.build_report(plan, {0: result(), 1: result("SELL"), 2: result()})
        self.assertIn("EXPLORATORY", report)
        self.assertIn("NOT a causal test proving anchoring", report)
        self.assertIn("Changed from original: 0/3", report)
        self.assertIn("blind agrees with local: 3/3", report)
        self.assertIn("latency and API cost", report)

    def test_csv_report_export_does_not_touch_source_files(self):
        self.save_blind()
        with patch.object(blind, "REPORT_FILE", self.path / "report.md"), \
             patch.object(blind, "ROWS_FILE", self.path / "comparison.csv"), \
             patch("builtins.print"):
            blind.export(self.store)
        self.assertTrue((self.path / "comparison.csv").is_file())
        self.assertIn("blind_status", (self.path / "comparison.csv").read_text())
        self.assertTrue((self.path / "report.md").is_file())

    def test_inconsistent_outcome_rejected_before_any_paid_call(self):
        source, attempts = fixture()
        source["samples"][0]["outcome"]["filter_return_20_pct"] = 999.
        with self.assertRaises(ValueError):
            blind.make_plan(source, attempts)

    def test_offline_prepare_and_report_do_not_import_mt5_or_openai(self):
        import sys
        path = self.save_source(1)
        before = set(sys.modules)
        plan = blind.prepare(self.store, path)
        blind.build_report(plan, {})
        added = set(sys.modules) - before
        self.assertFalse(any(x == "openai" or x.startswith("openai.") or x == "MetaTrader5" for x in added))


if __name__ == "__main__":
    unittest.main()
