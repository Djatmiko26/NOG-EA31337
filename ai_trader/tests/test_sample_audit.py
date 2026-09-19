"""Offline only: synthetic prices, no market data, temporary SQLite databases."""
import ast
import contextlib
import copy
import csv
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sample_audit as audit


def response(action):
    return {"status": "SUCCESS", "model_returned": "unit-test-model",
            "signal": {"action": action, "confidence": 70,
                       "market_regime": "RANGE", "reason": "Synthetic test only."}}


def fixture():
    samples = []
    for i, (local, close) in enumerate((("BUY", 102.), ("SELL", 99.), ("BUY", 97.))):
        raw = close - 100.
        outcome = {"entry_close": 100.}
        for h in audit.HORIZONS:
            outcome.update({f"close_{h}": close, f"market_return_{h}_pct": raw,
                            f"filter_return_{h}_pct": raw if local == "BUY" else -raw})
        payload = {"symbol": "TEST", "timeframe": "M5",
                   "local_filter": {"candidate_direction": local},
                   "candles": [{"close": 100.}]}
        samples.append({"sample_id": str(i), "payload": payload,
                        "row": {"filter_direction": local, "close": 100.,
                                "candle_time_server": f"2024-01-02T0{i}:00:00"},
                        "outcome": outcome})
    source = {"version": 2, "spec": {"model": "unit-test-model"}, "samples": samples}
    baseline = {str(i): response(s["row"]["filter_direction"]) for i, s in enumerate(samples)}
    blind = copy.deepcopy(samples)
    for s in blind:
        del s["payload"]["local_filter"]
    plan = {"version": audit.DESIGN, "source_plan": source,
            "source_fingerprint": audit.fingerprint(source),
            "baseline_attempts": baseline, "baseline_fingerprint": audit.fingerprint(baseline),
            "spec": copy.deepcopy(source["spec"]), "samples": blind}
    attempts = {0: response("WAIT"), 1: response("SELL"), 2: response("SELL")}
    return plan, attempts


@contextlib.contextmanager
def test_db(path):
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


def write_database(path, plan, attempts):
    with test_db(path) as db:
        db.executescript("""
            CREATE TABLE plan(id INTEGER PRIMARY KEY, body TEXT, fingerprint TEXT);
            CREATE TABLE attempts(sample_index INTEGER PRIMARY KEY, status TEXT,
                                  started_at_utc TEXT, result TEXT);
        """)
        db.execute("INSERT INTO plan VALUES(1, ?, ?)", (json.dumps(plan), audit.fingerprint(plan)))
        for i, value in attempts.items():
            body = None if value["status"] == "STARTED" else json.dumps(value)
            db.execute("INSERT INTO attempts VALUES(?, ?, 'synthetic', ?)", (i, value["status"], body))


class AuditMathTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.attempts = fixture()

    def test_directional_rows_and_agreement(self):
        rows = audit.audit_rows(self.plan, self.attempts)
        self.assertEqual([r["blind_20_pct"] for r in rows], [0., 1., 3.])
        self.assertEqual([r["agree_only_20_pct"] for r in rows], [0., 1., 0.])
        self.assertEqual([r["delta_20_pp"] for r in rows], [-2., 0., 6.])
        self.assertEqual([r["group"] for r in rows], ["WAIT", "ACTIVE", "ACTIVE"])

    def test_mean_median_and_sign_counts(self):
        result = audit.describe([-2., 0., 1., 9.])
        self.assertEqual(result["n"], 4)
        self.assertEqual(result["mean"], 2.)
        self.assertEqual(result["median"], .5)
        self.assertEqual((result["positive"], result["negative"], result["flat"]), (2, 1, 1))

    def test_empty_stats_are_missing_not_zero_return(self):
        result = audit.describe([])
        self.assertEqual(result["n"], 0)
        self.assertIsNone(result["mean"])
        self.assertIsNone(result["median"])

    def test_wait_is_zero_but_error_uncertain_and_unattempted_are_missing(self):
        for status in ("ERROR", "REFUSED", "INVALID", "INCOMPLETE", "STARTED", None):
            attempts = copy.deepcopy(self.attempts)
            if status is None:
                del attempts[1]
            else:
                attempts[1] = {"status": status}
            rows = audit.audit_rows(self.plan, attempts)
            self.assertEqual(rows[0]["blind_20_pct"], 0.)
            self.assertIsNone(rows[1]["blind_20_pct"])
            self.assertIsNone(rows[1]["delta_20_pp"])
            self.assertEqual(rows[1]["group"], "MISSING")
            report = audit.build_report(self.plan, attempts, rows)
            self.assertIn("pasangan valid: 2", report)
            self.assertIn("| 20 | local | 2 | -0.500000 |", report)

    def test_no_success_report_has_no_performance_numbers(self):
        rows = audit.audit_rows(self.plan, {})
        report = audit.build_report(self.plan, {}, rows)
        self.assertIn("| 20 | blind | 0 | - | - |", report)
        self.assertIn("Belum dicoba: 3", report)

    def test_active_wait_groups_use_explicit_denominators(self):
        rows = audit.audit_rows(self.plan, self.attempts)
        report = audit.build_report(self.plan, self.attempts, rows)
        self.assertIn("ACTIVE: 2; WAIT: 1", report)
        self.assertIn("| 20 | WAIT | 1 | 2.000000 |", report)
        self.assertIn("| 20 | ACTIVE | 2 | -1.000000 |", report)

    def test_leave_one_out_is_descriptive_and_keeps_input(self):
        values = [-2., 1., 9.]
        self.assertEqual(audit.leave_one_out(values), (-.5, 5.))
        self.assertEqual(values, [-2., 1., 9.])
        self.assertEqual(audit.leave_one_out([3.]), (None, None))
        self.assertEqual(audit.leave_one_out([]), (None, None))

    def test_nonfinite_and_bool_labels_rejected(self):
        for value in (True, "1", float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audit.describe([value])

    def test_wrong_directional_label_rejected(self):
        s = copy.deepcopy(self.plan["samples"][1])
        s["outcome"]["filter_return_20_pct"] = -1.
        with self.assertRaises(ValueError):
            audit.check_label(s["outcome"], s["row"], 20)

    def test_wrong_close_label_rejected(self):
        s = copy.deepcopy(self.plan["samples"][0])
        s["outcome"]["close_20"] = 150.
        with self.assertRaises(ValueError):
            audit.check_label(s["outcome"], s["row"], 20)

    def test_entry_mismatch_rejected(self):
        s = copy.deepcopy(self.plan["samples"][0])
        s["row"]["close"] = 99.
        with self.assertRaises(ValueError):
            audit.check_label(s["outcome"], s["row"], 20)

    def test_stored_price_and_return_rounding_allowed(self):
        s = copy.deepcopy(self.plan["samples"][0])
        entry, exit_price = 1.08345, 1.0845649
        raw = round((exit_price - entry) / entry * 100, 6)
        s["row"]["close"] = entry
        s["outcome"].update(entry_close=entry, close_20=round(exit_price, 5),
                            market_return_20_pct=raw, filter_return_20_pct=raw)
        self.assertEqual(audit.check_label(s["outcome"], s["row"], 20), raw)

    def test_baseline_or_source_fingerprint_mismatch_rejected(self):
        for key in ("source_fingerprint", "baseline_fingerprint"):
            plan = copy.deepcopy(self.plan)
            plan[key] = "bad"
            with self.assertRaises(ValueError):
                audit.audit_rows(plan, self.attempts)

    def test_changing_sample_outcome_or_model_rejected(self):
        for mutate in (lambda p: p["samples"][0]["outcome"].update(close_20=200.),
                       lambda p: p["spec"].update(model="another-test-model")):
            plan = copy.deepcopy(self.plan)
            mutate(plan)
            with self.assertRaises(ValueError):
                audit.audit_rows(plan, self.attempts)

    def test_unknown_action_and_confidence_bool_rejected(self):
        for field, value in (("action", "HOLD"), ("confidence", True)):
            attempts = copy.deepcopy(self.attempts)
            attempts[0]["signal"][field] = value
            with self.assertRaises(ValueError):
                audit.audit_rows(self.plan, attempts)

    def test_attempt_outside_sample_rejected(self):
        self.attempts[15] = response("BUY")
        with self.assertRaises(ValueError):
            audit.audit_rows(self.plan, self.attempts)

    def test_duplicate_sample_ids_rejected_even_after_rehash(self):
        self.plan["source_plan"]["samples"][1]["sample_id"] = "0"
        self.plan["source_fingerprint"] = audit.fingerprint(self.plan["source_plan"])
        with self.assertRaises(ValueError):
            audit.audit_rows(self.plan, self.attempts)

    def test_input_objects_unchanged(self):
        before = audit.fingerprint({"plan": self.plan, "attempts": self.attempts})
        rows = audit.audit_rows(self.plan, self.attempts)
        audit.build_report(self.plan, self.attempts, rows)
        self.assertEqual(before, audit.fingerprint({"plan": self.plan, "attempts": self.attempts}))

    def test_escape_text_not_numeric_returns(self):
        self.assertEqual(audit.csv_cell("=SUM(1,2)"), "'=SUM(1,2)")
        self.assertEqual(audit.csv_cell(-1.25), -1.25)
        self.assertEqual(audit.cell("a|b\nc`d"), "a/b c'd")


class AuditStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db = self.base / "blind.sqlite3"
        self.plan, self.attempts = fixture()
        write_database(self.db, self.plan, self.attempts)

    def test_missing_database_does_not_create_file(self):
        path = self.base / "absent.sqlite3"
        with self.assertRaises(ValueError):
            audit.read_snapshot(path)
        self.assertFalse(path.exists())

    def test_sqlite_opened_readonly_and_db_bytes_unchanged(self):
        before = self.db.read_bytes()
        real_connect = sqlite3.connect
        calls = []
        def connect(database_uri, **kwargs):
            calls.append((database_uri, kwargs))
            return real_connect(database_uri, **kwargs)
        with patch.object(audit.sqlite3, "connect", side_effect=connect):
            plan, attempts = audit.read_snapshot(self.db)
        self.assertTrue(calls[0][0].endswith("?mode=ro"))
        self.assertTrue(calls[0][1]["uri"])
        self.assertEqual(plan, self.plan)
        self.assertEqual(attempts, self.attempts)
        self.assertEqual(before, self.db.read_bytes())

    def test_bad_stored_fingerprint_rejected(self):
        with test_db(self.db) as db:
            db.execute("UPDATE plan SET fingerprint='bad'")
        with self.assertRaises(ValueError):
            audit.read_snapshot(self.db)

    def test_status_json_disagreement_rejected(self):
        with test_db(self.db) as db:
            db.execute("UPDATE attempts SET status='ERROR' WHERE sample_index=0")
        with self.assertRaises(ValueError):
            audit.read_snapshot(self.db)

    def test_started_body_null_preserved_as_missing(self):
        with test_db(self.db) as db:
            db.execute("UPDATE attempts SET status='STARTED', result=NULL WHERE sample_index=0")
        plan, attempts = audit.read_snapshot(self.db)
        self.assertIsNone(audit.audit_rows(plan, attempts)[0]["blind_20_pct"])

    def test_export_never_changes_db_or_existing_reports(self):
        before = self.db.read_bytes()
        old = self.base / "openai_blind_report.md"
        old.write_text("KEEP THIS", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            report, csv_file = audit.run_audit(self.db, self.base)
        self.assertEqual(before, self.db.read_bytes())
        self.assertEqual(old.read_text(), "KEEP THIS")
        self.assertIn("API_CALLS=0", report.read_text())
        with csv_file.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["blind_20_pct"], "0.0")
        self.assertNotIn("reason", rows[0])

    def test_repeated_audit_adds_no_attempts(self):
        before = self.db.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            audit.run_audit(self.db, self.base)
            audit.run_audit(self.db, self.base)
        self.assertEqual(before, self.db.read_bytes())

    def test_invalid_input_stops_before_writing_outputs(self):
        with test_db(self.db) as db:
            db.execute("UPDATE plan SET fingerprint='bad'")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = audit.main(["--database", str(self.db), "--output-dir", str(self.base)])
        self.assertEqual(rc, 1)
        self.assertFalse((self.base / "openai_sample_audit.md").exists())

    def test_standard_library_only_cli_without_site_packages(self):
        proc = subprocess.run([sys.executable, "-S", str(Path(audit.__file__)),
                               "--database", str(self.db), "--output-dir", str(self.base)],
                              capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("DATABASE_READ_ONLY", proc.stdout)

    def test_imports_exclude_market_network_and_secret_readers(self):
        tree = ast.parse(Path(audit.__file__).read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(n.name.split(".")[0] for n in node.names)
            if isinstance(node, ast.ImportFrom):
                imports.add(node.module.split(".")[0])
        self.assertFalse(imports & {"openai", "MetaTrader5", "dotenv", "requests", "socket", "urllib", "pandas"})


if __name__ == "__main__":
    unittest.main()
