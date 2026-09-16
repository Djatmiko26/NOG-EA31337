import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from openai_holdout_validation import prepare, spaced_candidates, split_holdout_indices
from pilot_support import (
    INSTRUCTIONS, SCHEMA, PilotStore, apply_outcome_signal, build_paired_report,
    execute_plan, paired_metrics, request_analysis, validate_signal,
)


def signal(action="BUY"):
    return {"action": action, "confidence": 70, "market_regime": "RANGE", "reason": "Uji."}


def plan(count=3):
    samples = []
    for i in range(count):
        outcome = {"entry_close": 100., "future_high_20": 110., "future_low_20": 90.}
        for h in (5, 10, 20):
            outcome[f"filter_return_{h}_pct"] = 1.
            outcome[f"market_return_{h}_pct"] = 1.
        samples.append({"sample_id": str(i), "payload": {"closed": [1., 2., 3.]},
                        "row": {"filter_direction": "BUY"}, "outcome": outcome})
    return {"spec": {"model": "test-model", "max_output_tokens": 2048,
                     "reasoning_effort": "low", "instructions": INSTRUCTIONS, "schema": SCHEMA},
            "samples": samples}


def response(text=None, status="completed", output=None):
    return SimpleNamespace(status=status, output=output or [],
                           output_text=json.dumps(signal()) if text is None else text,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=50),
                           id="test-response", model="test-model")


class PilotSupportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "pilot.sqlite3"
        self.store = PilotStore(self.path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.store.close)

    def test_hard_cap_rejects_more_than_ten(self):
        with self.assertRaises(ValueError):
            self.store.save_plan(plan(11))
        self.store.save_plan(plan(10))
        for i in range(10):
            self.assertTrue(self.store.claim(i))
        with self.assertRaises(ValueError):
            self.store.claim(10)

    def test_snapshot_is_immutable(self):
        saved = plan()
        self.store.save_plan(saved)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_plan(plan(2))
        self.assertEqual(prepare(self.store), saved)  # no MT5/API import on reuse

    def test_snapshot_integrity_check(self):
        self.store.save_plan(plan())
        self.store.db.execute("UPDATE plan SET fingerprint='modified'")
        self.store.db.commit()
        with self.assertRaises(ValueError):
            self.store.plan()

    def test_claim_visible_in_second_connection(self):
        self.store.save_plan(plan())
        self.assertTrue(self.store.claim(0))
        other = PilotStore(self.path)
        try:
            self.assertFalse(other.claim(0))
            self.assertEqual(other.attempts()[0]["status"], "STARTED")
        finally:
            other.close()

    def test_finish_requires_reservation(self):
        self.store.save_plan(plan())
        with self.assertRaises(ValueError):
            self.store.finish(0, {"status": "SUCCESS", "signal": signal()})

    def test_successful_run_is_not_repeated(self):
        self.store.save_plan(plan())
        client = Mock()
        client.responses.create.return_value = response()
        execute_plan(self.store, client, lambda _: None)
        execute_plan(self.store, client, lambda _: None)
        self.assertEqual(client.responses.create.call_count, 3)
        self.assertEqual(len(self.store.attempts()), 3)

    def test_error_is_saved_and_stops_run(self):
        self.store.save_plan(plan())
        client = Mock()
        client.responses.create.side_effect = RuntimeError("sensitive credential do not print")
        execute_plan(self.store, client, lambda _: None)
        self.assertEqual(client.responses.create.call_count, 1)
        saved = self.store.attempts()
        self.assertEqual(saved[0]["status"], "ERROR")
        self.assertNotIn("sensitive credential", json.dumps(saved))

    def test_uncertain_attempt_not_retried(self):
        self.store.save_plan(plan(2))
        client = Mock()
        client.responses.create.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            execute_plan(self.store, client, lambda _: None)
        self.assertEqual(self.store.attempts()[0]["status"], "STARTED")
        client.responses.create.side_effect = None
        client.responses.create.return_value = response()
        execute_plan(self.store, client, lambda _: None)
        self.assertEqual(client.responses.create.call_count, 2)  # second sample only
        self.assertEqual(self.store.attempts()[0]["status"], "STARTED")

    def test_future_outcomes_never_sent_to_api(self):
        saved = plan(1)
        saved["samples"][0]["outcome"]["private_future"] = "NEVER_SEND_FUTURE"
        self.store.save_plan(saved)
        client = Mock()
        client.responses.create.return_value = response()
        execute_plan(self.store, client, lambda _: None)
        kwargs = client.responses.create.call_args.kwargs
        self.assertEqual(json.loads(kwargs["input"]), saved["samples"][0]["payload"])
        self.assertNotIn("NEVER_SEND_FUTURE", str(kwargs))
        self.assertEqual(kwargs["max_output_tokens"], 2048)
        self.assertFalse(kwargs["store"])

    def test_incomplete_not_treated_as_wait(self):
        client = Mock()
        client.responses.create.return_value = response(status="incomplete")
        result = request_analysis(client, plan()["spec"], {})
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertNotIn("signal", result)
        self.assertEqual(result["output_tokens"], 50)

    def test_refusal_not_treated_as_wait(self):
        client = Mock()
        client.responses.create.return_value = response(output=[
            SimpleNamespace(content=[SimpleNamespace(type="refusal")])])
        result = request_analysis(client, plan()["spec"], {})
        self.assertEqual(result["status"], "REFUSED")

    def test_invalid_json_not_treated_as_wait(self):
        client = Mock()
        client.responses.create.return_value = response(text="not JSON")
        self.assertEqual(request_analysis(client, plan()["spec"], {})["status"], "INVALID")

    def test_confidence_bool_and_extra_fields_rejected(self):
        value = signal()
        value["confidence"] = True
        with self.assertRaises(ValueError):
            validate_signal(value)
        value = signal()
        value["order_lot"] = 1
        with self.assertRaises(ValueError):
            validate_signal(value)

    def test_pairing_includes_wait_zero_on_same_denominator(self):
        saved = plan()
        for sample, ret in zip(saved["samples"], [-2., 3., -1.]):
            sample["outcome"]["filter_return_20_pct"] = ret
            sample["outcome"]["market_return_20_pct"] = ret
        attempts = {i: {"status": "SUCCESS", "signal": signal(action)}
                    for i, action in enumerate(["WAIT", "BUY", "SELL"])}
        metrics = paired_metrics(saved, attempts, 20)
        self.assertEqual(metrics["n"], 3)
        self.assertEqual(metrics["local"], 0.)
        self.assertAlmostEqual(metrics["ai_wait_zero"], 4 / 3)
        self.assertAlmostEqual(metrics["agree_only"], 1.)
        self.assertAlmostEqual(metrics["paired_delta"], 4 / 3)

    def test_errors_excluded_not_converted_to_zero(self):
        attempts = {0: {"status": "ERROR"}, 1: {"status": "STARTED"},
                    2: {"status": "SUCCESS", "signal": signal()}}
        metrics = paired_metrics(plan(), attempts, 20)
        self.assertEqual(metrics["n"], 1)
        report = build_paired_report(plan(), attempts)
        self.assertIn("failed/uncertain: 2", report)
        self.assertIn("EXPLORATORY", report)

    def test_no_success_yields_no_performance_number(self):
        self.assertIsNone(paired_metrics(plan(), {}, 20)["local"])
        self.assertIn("| 20 | 0 | - |", build_paired_report(plan(), {}))

    def test_sell_outcome_direction_and_excursions(self):
        outcome = plan()["samples"][0]["outcome"]
        result = apply_outcome_signal(outcome, signal("SELL"))
        self.assertEqual(result["openai_return_20_pct"], -1.)
        self.assertEqual(result["openai_mfe_20_pct"], 10.)
        self.assertEqual(result["openai_mae_20_pct"], -10.)
        self.assertEqual(apply_outcome_signal(outcome, signal("WAIT"))["openai_return_20_pct"], "")

    def test_candidate_windows_do_not_overlap(self):
        selected = spaced_candidates(list(range(100)))
        self.assertEqual(selected, [0, 20, 40, 60, 80])
        self.assertTrue(all(b - a >= 20 for a, b in zip(selected, selected[1:])))

    def test_development_label_end_precedes_validation(self):
        dev, val = split_holdout_indices(list(range(100)), .7, purge_bars=20)
        self.assertEqual(val[0], 70)
        self.assertTrue(all(i + 20 < min(val) for i in dev))

    def test_single_split_has_no_fake_validation(self):
        self.assertEqual(split_holdout_indices([5], .7), ([5], []))


if __name__ == "__main__":
    unittest.main()
