"""Offline checks: synthetic baseline-format records only; no API/broker access."""
import ast
import contextlib
import copy
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import execution_diagnostics as d


def make_body():
    bars = [{"time": 1700000000 + 300 * i, "open": 100., "high": 101., "low": 99.,
             "close": 100., "spread": 20, "tick_volume": 50} for i in range(500)]
    return {"version": d.VERSION, "rules": dict(d.RULES), "filter": dict(d.FILTER),
            "chart_mode": "BID", "timeframe": "M5", "symbol": "TEST", "point": .01,
            "bars": bars, "pandas": "fixture-only", "implementation": {}}


def make_rows(body, choices=None):
    choices = choices or [("EARLY", 200, "BUY"), ("EARLY", 224, "SELL"),
                          ("LATE", 394, "BUY"), ("LATE", 416, "SELL")]
    rows = []
    for seg, i, side in choices:
        e, x = i + 2, i + 22
        bars = body["bars"]
        bid0, bid1 = bars[e]["open"], bars[x]["open"]
        for mult in (0, 1, 2):
            es, xs = bars[e - 1]["spread"], bars[x - 1]["spread"]
            entry = bid0 + mult * es * body["point"] if side == "BUY" else bid0
            exit_price = bid1 if side == "BUY" else bid1 + mult * xs * body["point"]
            sign = 1 if side == "BUY" else -1
            gross = sign * (bid1 - bid0) / bid0 * 100
            adjusted = sign * (exit_price - entry) / bid0 * 100
            rows.append({"segment": seg, "direction": side, "spread_multiplier": mult,
                         "signal_index": i, "entry_index": e, "exit_index": x,
                         "signal_epoch_raw": bars[i]["time"], "entry_epoch_raw": bars[e]["time"],
                         "exit_epoch_raw": bars[x]["time"], "signal_close": bars[i]["close"],
                         "entry_bid_open": bid0, "exit_bid_open": bid1,
                         "entry_spread_lagged_points": es, "exit_spread_lagged_points": xs,
                         "entry_quote_proxy": entry, "exit_quote_proxy": exit_price,
                         "gross_bid_return_pct": gross, "spread_proxy_return_pct": adjusted,
                         "spread_drag_pp": gross - adjusted})
    return rows


class ExecutionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.snapshot, self.csv = self.data / d.SNAPSHOT.name, self.data / d.TRADES.name
        self.report, self.positions = self.data / d.REPORT.name, self.data / d.POSITIONS.name
        self.body = make_body()
        for name in ("execution_baseline.py", "strategy_filter.py"):
            text = '# Synthetic source identity fixture; never imported.\n'
            (self.root / name).write_text(text, encoding="utf-8")
            self.body["implementation"][name] = hashlib.sha256(text.encode()).hexdigest()
        self.rows = make_rows(self.body)
        self.save()

    def save(self, rows=None):
        self.snapshot.write_text(json.dumps({"body": self.body, "sha256": d.digest(self.body)}), encoding="utf-8")
        with self.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=sorted(d.TRADE_FIELDS))
            writer.writeheader()
            writer.writerows(self.rows if rows is None else rows)

    def load(self):
        return d.read_inputs(self.snapshot, self.csv, self.root)

    def run_report(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return d.run(self.snapshot, self.csv, self.report, self.positions, self.root)

    def gap(self, at, extra=3600):
        for row in self.body["bars"][at:]:
            row["time"] += extra

    def test_valid_round_trip_and_hashes(self):
        body, rows, hashes = self.load()
        self.assertEqual(body, self.body)
        self.assertEqual(len(rows), 12)
        self.assertEqual(hashes["snapshot"], d.digest(self.body))
        self.assertEqual(hashes["trades_file"], hashlib.sha256(self.csv.read_bytes()).hexdigest())

    def test_triples_are_not_three_trades(self):
        body, rows, _ = self.load()
        positions = d.enrich_positions(body, rows)
        self.assertEqual(len(positions), 4)
        self.assertTrue(all(r["spread_multiplier"] == 1 for r in positions))

    def test_flat_bid_long_and_short_lose_one_spread(self):
        _, rows, _ = self.load()
        for row in rows:
            self.assertAlmostEqual(row["spread_proxy_return_pct"], -.2 * row["spread_multiplier"])

    def test_sell_price_drop_has_positive_gross(self):
        self.body["bars"][246].update(open=99., high=100., low=98., close=99.)
        self.rows = make_rows(self.body)
        self.save()
        _, rows, _ = self.load()
        sell = next(r for r in rows if r["signal_index"] == 224 and r["spread_multiplier"] == 0)
        self.assertAlmostEqual(sell["spread_proxy_return_pct"], 1.)

    def test_corrupt_snapshot_hash_rejected(self):
        envelope = json.loads(self.snapshot.read_text())
        envelope["sha256"] = "bad"
        self.snapshot.write_text(json.dumps(envelope))
        with self.assertRaises(ValueError): self.load()

    def test_implementation_change_rejected(self):
        (self.root / "strategy_filter.py").write_text("changed\n")
        with self.assertRaises(ValueError): self.load()

    def test_crlf_source_identity_normalized(self):
        path = self.root / "strategy_filter.py"
        path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
        self.load()

    def test_changed_rules_rejected(self):
        self.body["rules"]["hold_bars"] = 5
        self.save()
        with self.assertRaises(ValueError): self.load()

    def test_invalid_bid_mode_rejected(self):
        self.body["chart_mode"] = "LAST"
        self.save()
        with self.assertRaises(ValueError): self.load()

    def test_nonfinite_csv_rejected(self):
        self.rows[0]["spread_proxy_return_pct"] = float('nan')
        self.save()
        with self.assertRaises(ValueError): self.load()

    def test_boolean_bar_value_rejected(self):
        self.body["bars"][0]["open"] = True
        self.save()
        with self.assertRaises(ValueError): self.load()

    def test_out_of_order_bars_rejected(self):
        self.body["bars"][1]["time"] = self.body["bars"][0]["time"]
        self.save()
        with self.assertRaises(ValueError): self.load()

    def test_bad_ohlc_rejected(self):
        self.body["bars"][1]["high"] = 99.
        self.save()
        with self.assertRaises(ValueError): self.load()

    def test_tampered_return_rejected(self):
        self.rows[0]["spread_proxy_return_pct"] += .1
        self.save()
        with self.assertRaises(ValueError): self.load()

    def test_stale_csv_epochs_rejected(self):
        self.rows[0]["entry_epoch_raw"] += 300
        self.save()
        with self.assertRaises(ValueError): self.load()

    def test_wrong_lagged_spread_rejected(self):
        self.rows[0]["entry_spread_lagged_points"] = 99
        self.save()
        with self.assertRaises(ValueError): self.load()

    def test_fill_bar_spread_is_not_used(self):
        # e=202 is not the spread proxy bar e-1=201.
        self.body["bars"][202]["spread"] = 999
        self.save()
        self.load()

    def test_missing_scenario_rejected(self):
        self.save(self.rows[:-1])
        with self.assertRaises(ValueError): self.load()

    def test_duplicate_scenario_rejected(self):
        self.save(self.rows + [self.rows[0]])
        with self.assertRaises(ValueError): self.load()

    def test_different_direction_across_scenarios_rejected(self):
        altered = make_rows(self.body, [("EARLY", 200, "SELL")])
        self.rows[0] = altered[0]  # numerically valid zero-spread case, inconsistent side
        self.save()
        with self.assertRaises(ValueError): self.load()

    def test_overlap_rejected(self):
        self.rows += make_rows(self.body, [("EARLY", 201, "BUY")])
        self.save()
        with self.assertRaises(ValueError): self.load()

    def test_cross_boundary_trade_rejected(self):
        self.rows = make_rows(self.body, [("EARLY", 373, "BUY")])
        self.save()
        with self.assertRaises(ValueError): self.load()

    def test_gap_entry_boundary_is_pending_only(self):
        self.gap(202)
        row = d.enrich_positions(self.body, make_rows(self.body))[0]
        self.assertEqual(row["raw_gap_group"], "PENDING_ONLY")
        self.assertEqual(row["holding_elapsed_raw_seconds"], 6000)

    def test_gap_exit_boundary_is_holding(self):
        self.gap(222)
        row = d.enrich_positions(self.body, make_rows(self.body))[0]
        self.assertEqual(row["raw_gap_group"], "HOLDING_ONLY")
        self.assertEqual(row["holding_elapsed_raw_seconds"], 9600)

    def test_gap_after_exit_is_not_counted(self):
        self.gap(223)
        self.assertEqual(d.enrich_positions(self.body, make_rows(self.body))[0]["raw_gap_group"], "NONE")

    def test_both_gap_phases_do_not_remove_position(self):
        self.gap(201)
        self.gap(210)
        enriched = d.enrich_positions(self.body, make_rows(self.body))
        self.assertEqual(len(enriched), 4)
        self.assertEqual(enriched[0]["raw_gap_group"], "PENDING_AND_HOLDING")

    def test_statistics_signs_mean_and_leave_one_out(self):
        result = d.statistics([-3., 1., 2., 0.])
        self.assertEqual(result["mean"], 0.)
        self.assertEqual(result["median"], .5)
        self.assertEqual((result["positive"], result["negative"], result["zero"]), (2, 1, 1))
        self.assertEqual(result["mean_positive"], 1.5)
        self.assertEqual(result["mean_negative"], -3.)
        self.assertEqual(result["loo_min"], -2/3)
        self.assertEqual(result["loo_max"], 1.)

    def test_empty_and_single_statistics_are_not_fake_zero_results(self):
        self.assertIsNone(d.statistics([])["mean"])
        self.assertIsNone(d.statistics([1.])["loo_min"])

    def test_empty_original_csv_header_supported(self):
        with self.csv.open("w", newline="") as handle:
            csv.writer(handle).writerow(sorted(d.EMPTY_FIELDS))
        text = self.run_report()
        self.assertIn('Posisi simulasi unik: 0', text)
        self.assertIn('| EARLY | 1 | 0 | - | - | - |', text)

    def test_output_preserves_all_input_bytes_and_rerun(self):
        (self.root / '.env').write_text('SECRET_DO_NOT_READ')
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        text = self.run_report()
        self.run_report()
        for path, data in before.items(): self.assertEqual(path.read_bytes(), data)
        self.assertNotIn('SECRET_DO_NOT_READ', text)
        self.assertIn('OPENAI_CALLS=0', text)
        self.assertIn('| LATE | ALL | 2 | -0.200000 |', text)
        self.assertIn('Label ini diketahui SETELAH kejadian', text)

    def test_source_or_old_report_cannot_be_output(self):
        with self.assertRaises(ValueError):
            d.run(self.snapshot, self.csv, self.snapshot, self.positions, self.root)
        with self.assertRaises(ValueError):
            d.run(self.snapshot, self.csv, self.data / 'execution_baseline_report.md', self.positions, self.root)

    def test_hardlinked_output_rejected(self):
        os.link(self.snapshot, self.report)
        with self.assertRaises(ValueError): self.run_report()

    def test_missing_input_is_not_created(self):
        self.snapshot.unlink()
        with self.assertRaises(FileNotFoundError): self.run_report()
        self.assertFalse(self.snapshot.exists())
        self.assertFalse(self.report.exists())

    def test_standard_library_cli_no_site_packages(self):
        target = self.root / 'execution_diagnostics.py'
        target.write_text(Path(d.__file__).read_text(), encoding='utf-8')
        result = subprocess.run([sys.executable, '-S', str(target)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('Saved diagnosis:', result.stdout)
        self.assertTrue(self.positions.exists())

    def test_imports_have_no_api_mt5_pandas_or_env_loader(self):
        tree = ast.parse(Path(d.__file__).read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): imports.extend(a.name for a in node.names)
            if isinstance(node, ast.ImportFrom): imports.append(node.module)
        self.assertFalse(set(imports) & {'openai', 'MetaTrader5', 'pandas', 'dotenv', 'execution_baseline', 'strategy_filter', 'socket', 'requests'})


if __name__ == '__main__':
    unittest.main()
