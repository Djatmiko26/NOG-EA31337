"""Read saved execution-baseline files only; no MT5, API, .env or order access."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import platform
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SNAPSHOT = DATA_DIR / "execution_baseline_snapshot.json"
TRADES = DATA_DIR / "execution_baseline_trades.csv"
REPORT = DATA_DIR / "execution_diagnostics_report.md"
POSITIONS = DATA_DIR / "execution_diagnostics_positions.csv"
VERSION = "local-open-price-spread-proxy-v1"
RULES = {"context_bars": 120, "warmup_bars": 200, "hold_bars": 20,
         "delay_bars": 1, "development_fraction": 0.70}
FILTER = {"lookback": 12, "min_score": 5, "min_atr_ratio": .00035,
          "max_spread_atr_ratio": .12, "breakout_buffer_atr": .25,
          "min_ema_gap_atr": .10, "rsi_bull": 52., "rsi_bear": 48.}
TIMEFRAMES = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}
BAR_FIELDS = {"time", "open", "high", "low", "close", "spread", "tick_volume"}
INT_FIELDS = {"spread_multiplier", "signal_index", "entry_index", "exit_index",
              "signal_epoch_raw", "entry_epoch_raw", "exit_epoch_raw",
              "entry_spread_lagged_points", "exit_spread_lagged_points"}
FLOAT_FIELDS = {"signal_close", "entry_bid_open", "exit_bid_open", "entry_quote_proxy",
                "exit_quote_proxy", "gross_bid_return_pct", "spread_proxy_return_pct", "spread_drag_pp"}
TRADE_FIELDS = INT_FIELDS | FLOAT_FIELDS | {"segment", "direction"}
EMPTY_FIELDS = {"segment", "spread_multiplier", "direction", "signal_epoch_raw", "spread_proxy_return_pct"}


def digest(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def number(value) -> float:
    if isinstance(value, bool):
        raise ValueError("Boolean bukan angka harga/return.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Angka kosong atau nonfinite tidak diizinkan.")
    return result


def same(actual, expected) -> None:
    if not math.isclose(number(actual), number(expected), rel_tol=1e-10, abs_tol=1e-9):
        raise ValueError("CSV tidak konsisten dengan harga/spread snapshot; jangan resampling.")


def read_inputs(snapshot: Path, trades: Path, code_dir: Path = BASE_DIR) -> tuple[dict, list[dict], dict]:
    raw_snapshot, raw_trades = snapshot.read_bytes(), trades.read_bytes()
    envelope = json.loads(raw_snapshot.decode("utf-8-sig"))
    body = envelope["body"]
    if envelope["sha256"] != digest(body) or body.get("version") != VERSION:
        raise ValueError("Hash/versi snapshot berbeda.")
    if body["rules"] != RULES or body["filter"] != FILTER:
        raise ValueError("Aturan bukan baseline tetap; tidak diubah oleh diagnostik.")
    if body["chart_mode"] != "BID" or body["timeframe"] not in TIMEFRAMES:
        raise ValueError("Mode chart/timeframe tidak didukung.")
    point = number(body["point"])
    if point <= 0:
        raise ValueError("Point harus positif.")
    current_code = {name: hashlib.sha256((code_dir / name).read_text(encoding="utf-8").encode("utf-8")).hexdigest()
                    for name in ("execution_baseline.py", "strategy_filter.py")}
    if body["implementation"] != current_code:
        raise ValueError("Kode baseline/filter berbeda dari snapshot; jangan hapus snapshot.")
    bars = body["bars"]
    previous = None
    for bar in bars:
        if set(bar) != BAR_FIELDS:
            raise ValueError("Kolom candle berubah.")
        if any(type(bar[f]) not in (int, float) for f in BAR_FIELDS):
            raise ValueError("Tipe data candle tidak valid.")
        for f in BAR_FIELDS:
            number(bar[f])
        t = bar["time"]
        if type(t) is not int or t <= 0 or (previous is not None and t <= previous):
            raise ValueError("Epoch candle harus unik, positif, dan meningkat.")
        previous = t
        if min(bar[f] for f in ("open", "high", "low", "close")) <= 0:
            raise ValueError("Harga tidak positif.")
        if bar["high"] < max(bar["open"], bar["close"], bar["low"]) or bar["low"] > min(bar["open"], bar["close"], bar["high"]):
            raise ValueError("OHLC tidak konsisten.")
        if any(bar[f] < 0 or bar[f] != int(bar[f]) for f in ("spread", "tick_volume")):
            raise ValueError("Spread/volume tidak valid.")
    count = len(bars) - 22 - 200
    if count < 2:
        raise ValueError("Histori terlalu pendek.")
    cut = min(max(int(count * .70), 1), count - 1)
    boundary = 200 + cut
    if boundary - 22 <= 200:
        raise ValueError("Segmen awal habis setelah purge.")
    reader = csv.DictReader(io.StringIO(raw_trades.decode("utf-8-sig"), newline=""))
    fields = reader.fieldnames or []
    if len(set(fields)) != len(fields):
        raise ValueError("Kolom CSV duplikat.")
    source_rows = list(reader)
    if set(fields) != TRADE_FIELDS and not (not source_rows and set(fields) == EMPTY_FIELDS):
        raise ValueError("Schema CSV baseline berbeda.")
    rows, grouped = [], {}
    for raw in source_rows:
        if set(raw) != TRADE_FIELDS or any(v is None for v in raw.values()):
            raise ValueError("Baris CSV tidak lengkap/kelebihan kolom.")
        row = {f: int(raw[f]) for f in INT_FIELDS}
        row.update({f: number(raw[f]) for f in FLOAT_FIELDS})
        row.update({f: raw[f] for f in ("segment", "direction")})
        i, e, x = (row[f] for f in ("signal_index", "entry_index", "exit_index"))
        seg, mult, side = row["segment"], row["spread_multiplier"], row["direction"]
        if seg not in ("EARLY", "LATE") or side not in ("BUY", "SELL") or mult not in (0, 1, 2):
            raise ValueError("Segment/arah/skenario tidak valid.")
        valid_index = 200 <= i < boundary - 22 if seg == "EARLY" else boundary <= i < len(bars) - 22
        if not valid_index or e != i + 2 or x != e + 20:
            raise ValueError("Jadwal melanggar delay, holding, atau purge boundary.")
        for prefix, index in (("signal", i), ("entry", e), ("exit", x)):
            if row[f"{prefix}_epoch_raw"] != bars[index]["time"]:
                raise ValueError("Epoch CSV tidak cocok dengan snapshot.")
        entry_bid, exit_bid = bars[e]["open"], bars[x]["open"]
        es, xs = bars[e - 1]["spread"], bars[x - 1]["spread"]
        for key, value in {"signal_close": bars[i]["close"], "entry_bid_open": entry_bid,
                           "exit_bid_open": exit_bid, "entry_spread_lagged_points": es,
                           "exit_spread_lagged_points": xs}.items():
            same(row[key], value)
        entry = entry_bid + mult * es * point if side == "BUY" else entry_bid
        exit_price = exit_bid if side == "BUY" else exit_bid + mult * xs * point
        sign = 1 if side == "BUY" else -1
        gross = sign * (exit_bid - entry_bid) / entry_bid * 100
        proxy = sign * (exit_price - entry) / entry_bid * 100
        for key, value in {"entry_quote_proxy": entry, "exit_quote_proxy": exit_price,
                           "gross_bid_return_pct": gross, "spread_proxy_return_pct": proxy,
                           "spread_drag_pp": gross - proxy}.items():
            same(row[key], value)
        key = (seg, i)
        peers = grouped.setdefault(key, {})
        if mult in peers:
            raise ValueError("Posisi/skenario CSV duplikat.")
        peers[mult] = row
        rows.append(row)
    for peers in grouped.values():
        if set(peers) != {0, 1, 2} or len({r["direction"] for r in peers.values()}) != 1:
            raise ValueError("Tiga skenario harus punya jadwal dan arah yang sama.")
    for seg in ("EARLY", "LATE"):
        scheduled = sorted((r for r in rows if r["segment"] == seg and r["spread_multiplier"] == 1), key=lambda r: r["signal_index"])
        if any(b["signal_index"] < a["exit_index"] for a, b in zip(scheduled, scheduled[1:])):
            raise ValueError("Posisi bertumpuk; bukan jadwal baseline.")
    fingerprints = {"snapshot": envelope["sha256"], "snapshot_file": hashlib.sha256(raw_snapshot).hexdigest(),
                    "trades_file": hashlib.sha256(raw_trades).hexdigest()}
    return body, rows, fingerprints


def enrich_positions(body: dict, rows: list[dict]) -> list[dict]:
    bars, seconds = body["bars"], TIMEFRAMES[body["timeframe"]]
    anomalies = {i for i in range(1, len(bars)) if bars[i]["time"] - bars[i - 1]["time"] != seconds}
    result = []
    for row in sorted(rows, key=lambda r: r["signal_index"]):
        if row["spread_multiplier"] != 1:
            continue  # three scenarios are ONE hypothetical position, not three trades
        i, e, x = row["signal_index"], row["entry_index"], row["exit_index"]
        pending = sum(j in anomalies for j in range(i + 1, e + 1))
        held = sum(j in anomalies for j in range(e + 1, x + 1))
        group = "PENDING_AND_HOLDING" if pending and held else "PENDING_ONLY" if pending else "HOLDING_ONLY" if held else "NONE"
        result.append({**row, "pending_raw_anomalies": pending, "holding_raw_anomalies": held,
                       "raw_gap_group": group,
                       "holding_elapsed_raw_seconds": bars[x]["time"] - bars[e]["time"],
                       "holding_nominal_seconds": 20 * seconds})
    return result


def statistics(values: list[float]) -> dict:
    if any(not math.isfinite(v) for v in values):
        raise ValueError("Return nonfinite.")
    positive, negative = [v for v in values if v > 0], [v for v in values if v < 0]
    loo = [(math.fsum(values) - v) / (len(values) - 1) for v in values] if len(values) > 1 else []
    return {"n": len(values), "mean": mean(values) if values else None,
            "median": median(values) if values else None, "positive": len(positive),
            "negative": len(negative), "zero": sum(v == 0 for v in values),
            "mean_positive": mean(positive) if positive else None,
            "mean_negative": mean(negative) if negative else None,
            "minimum": min(values) if values else None, "maximum": max(values) if values else None,
            "loo_min": min(loo) if loo else None, "loo_max": max(loo) if loo else None}


def fmt(value) -> str:
    return "-" if value is None else f"{value:.6f}"


def table(headers: list[str], rows: list[list]) -> list[str]:
    def line(items):
        return "| " + " | ".join(str(v).replace("|", "/").replace("\n", " ").replace("\r", " ") for v in items) + " |"
    return [line(headers), line(["---"] * len(headers)), *(line(r) for r in rows)]


def render(body: dict, rows: list[dict], positions: list[dict], fingerprints: dict) -> str:
    lines = ["# Diagnosis baseline tersimpan (offline)", "",
             f"Dibuat: {datetime.now(timezone.utc).isoformat()}",
             f"Snapshot SHA256: {fingerprints['snapshot']}",
             f"CSV SHA256: {fingerprints['trades_file']}",
             f"Python diagnostik: {platform.python_version()}", "",
             "NO_ORDER_EXECUTION | OPENAI_CALLS=0 | MT5_CONNECTIONS=0 | INPUT_READ_ONLY",
             "Aturan, snapshot, arah dan jadwal tetap; tidak ada pemilihan strategi/parameter.",
             f"Posisi simulasi unik: {len(positions)}; baris tiga skenario: {len(rows)}.",
             "Semua persen adalah return harga, BUKAN saldo/margin akun atau hasil NET.", "",
             "## 1. Rekonsiliasi skenario", ""]
    summary = []
    for segment in ("EARLY", "LATE"):
        for mult in (0, 1, 2):
            subset = [r for r in rows if r["segment"] == segment and r["spread_multiplier"] == mult]
            stat = statistics([r["spread_proxy_return_pct"] for r in subset])
            summary.append([segment, mult, stat["n"], fmt(stat["mean"]), fmt(stat["median"]),
                            fmt(mean(r["spread_drag_pp"] for r in subset) if subset else None)])
    lines += table(["Segment", "Spread x", "N", "Mean %", "Median %", "Drag pp"], summary)
    lines += ["", "## 2. Arah BUY/SELL pada skenario 1x", "",
              "Avg negatif tetap bertanda negatif. Jumlah + bukan win rate eksekusi nyata.",
              "Kontribusi = jumlah return kelompok / N SEMUA posisi segmen; bukan return akun.", ""]
    side_rows = []
    for segment in ("EARLY", "LATE"):
        segment_rows = [r for r in positions if r["segment"] == segment]
        for side in ("ALL", "BUY", "SELL"):
            values = [r["spread_proxy_return_pct"] for r in segment_rows if side == "ALL" or r["direction"] == side]
            stat = statistics(values)
            contribution = math.fsum(values) / len(segment_rows) if segment_rows else None
            side_rows.append([segment, side, stat["n"], fmt(stat["mean"]), fmt(stat["median"]),
                              stat["positive"], stat["negative"], stat["zero"],
                              fmt(stat["mean_positive"]), fmt(stat["mean_negative"]), fmt(contribution)])
    lines += table(["Segment", "Arah", "N", "Mean %", "Median %", "+", "-", "0", "Avg positif %", "Avg negatif %", "Kontribusi pp"], side_rows)
    lines += ["", "## 3. Transisi raw timestamp tidak kontinu (1x)", "",
              "NONE tidak menjamin histori lengkap. Anomali berarti selisih antarbar bukan interval timeframe.",
              "PENDING: signal-open sampai entry-open; HOLDING: entry-open sampai exit-open.",
              "Label ini diketahui SETELAH kejadian: bukan filter yang tersedia saat sinyal.",
              "Tidak ada posisi yang dibuang, dijadwal ulang, atau diubah menjadi WAIT.", ""]
    gap_rows = []
    for segment in ("EARLY", "LATE"):
        segment_rows = [r for r in positions if r["segment"] == segment]
        for group in ("NONE", "PENDING_ONLY", "HOLDING_ONLY", "PENDING_AND_HOLDING"):
            values = [r["spread_proxy_return_pct"] for r in segment_rows if r["raw_gap_group"] == group]
            stat = statistics(values)
            gap_rows.append([segment, group, stat["n"], fmt(stat["mean"]), fmt(stat["median"]),
                             fmt(math.fsum(values) / len(segment_rows) if segment_rows else None)])
    lines += table(["Segment", "Anomali", "N", "Mean %", "Median %", "Kontribusi pp"], gap_rows)
    lines += ["", "## 4. Rentang dan sensitivitas satu posisi (1x)", "",
              "Tanpa-satu hanya deskripsi; BUKAN interval kepercayaan atau izin menghapus loss.", ""]
    sensitivity = []
    for segment in ("EARLY", "LATE"):
        values = [r["spread_proxy_return_pct"] for r in positions if r["segment"] == segment]
        stat = statistics(values)
        sensitivity.append([segment, stat["n"], fmt(stat["mean"]), fmt(stat["minimum"]), fmt(stat["maximum"]),
                            fmt(stat["loo_min"]), fmt(stat["loo_max"])])
    lines += table(["Segment", "N", "Mean %", "Min %", "Max %", "Tanpa-1 min mean %", "Tanpa-1 max mean %"], sensitivity)
    lines += ["", "## 5. Lima hasil terendah dan tertinggi per segmen (1x)", "",
              "Daftar deskriptif; tidak menambah/menghapus posisi dari statistik.", ""]
    extremes = []
    for segment in ("EARLY", "LATE"):
        subset = sorted([r for r in positions if r["segment"] == segment], key=lambda r: (r["spread_proxy_return_pct"], r["signal_index"]))
        for label, selected in (("LOW", subset[:5]), ("HIGH", list(reversed(subset[-5:])))):
            for row in selected:
                extremes.append([segment, label, row["signal_index"], row["signal_epoch_raw"], row["direction"],
                                 fmt(row["spread_proxy_return_pct"]), row["raw_gap_group"]])
    lines += table(["Segment", "Kelompok", "Signal index", "Raw epoch", "Arah", "Return %", "Anomali"], extremes)
    lines += ["", "## Batas diagnosis", "",
              "Harga/epoch/spread/aritmetika CSV, triplet skenario, dan aturan jadwal diperiksa terhadap snapshot.",
              "Filter/indikator tidak dihitung ulang; kelengkapan daftar kandidat/posisi tidak dibuktikan oleh audit ini.",
              "Hash memeriksa konsistensi lokal, bukan tanda tangan broker atau bukti keaslian hasil.",
              "Anomali raw clock dapat berasal dari sesi libur, histori hilang atau pergantian jam; sebabnya belum diketahui.",
              "Tidak menormalisasi timezone, mengisi gap, atau membuang posisi yang melewati gap.",
              "Data/grouping sudah dilihat; bukan holdout baru. Perbedaan grup bukan bukti sebab-akibat.",
              "Spread tetap PROXY; komisi, slippage, pembiayaan, API cost dan fill nyata tidak dimodelkan.",
              "Tidak ada SL/TP, lot, risk 0.25%, ekuitas, margin atau drawdown akun.",
              "Tidak memilih BUY-only/SELL-only atau membalik sinyal dari angka ini.",
              "Bekukan rancangan berikutnya sebelum memakai periode benar-benar baru/forward.", ""]
    return "\n".join(lines)


def distinct_paths(paths: list[Path]) -> None:
    for i, a in enumerate(paths):
        for b in paths[i + 1:]:
            if a.resolve() == b.resolve() or (a.exists() and b.exists() and os.path.samefile(a, b)):
                raise ValueError("Input/output harus berbeda, termasuk symlink/hardlink.")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        tmp = Path(handle.name)
        handle.write(text)
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def run(snapshot=SNAPSHOT, trades=TRADES, report=REPORT, positions_file=POSITIONS, code_dir=BASE_DIR) -> str:
    protected = [snapshot, trades, snapshot.parent / "execution_baseline_report.md",
                 snapshot.parent / "openai_pilot.sqlite3", snapshot.parent / "openai_blind.sqlite3",
                 code_dir / "execution_baseline.py", code_dir / "strategy_filter.py", code_dir / ".env"]
    distinct_paths([*protected, report, positions_file])
    body, rows, fingerprints = read_inputs(snapshot, trades, code_dir)
    positions = enrich_positions(body, rows)
    text = render(body, rows, positions, fingerprints)
    stream = io.StringIO(newline="")
    fields = sorted(positions[0]) if positions else ["segment", "signal_index", "direction", "spread_proxy_return_pct", "raw_gap_group"]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(positions)
    atomic_write(positions_file, stream.getvalue())
    atomic_write(report, text)
    print(text)
    print(f"Saved diagnosis: {report}")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()  # no parameter switches, capture or paid mode
    try:
        run()
        return 0
    except (ValueError, OSError, KeyError, TypeError, OverflowError, csv.Error) as exc:
        print(f"DIAGNOSTICS STOP | {type(exc).__name__}: {exc}")
        print("Simpan pesan. Jangan hapus snapshot atau mengulang API.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
