"""Local-filter execution assumptions lab. No OpenAI, credentials or order functions.

--prepare: freeze closed BID bars from the user's MT5 (once).
--report: replay the saved snapshot offline with three spread-proxy scenarios.
No mode is selected by default. This is research, not broker execution or net P/L.
"""
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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Callable

import pandas as pd
import strategy_filter
from strategy_filter import FilterConfig, evaluate_setup

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SNAPSHOT = DATA_DIR / "execution_baseline_snapshot.json"
REPORT = DATA_DIR / "execution_baseline_report.md"
TRADES = DATA_DIR / "execution_baseline_trades.csv"
VERSION = "local-open-price-spread-proxy-v1"
TIMEFRAMES = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}
FIELDS = ("time", "open", "high", "low", "close", "spread", "tick_volume")
SPREAD_SCENARIOS = (0, 1, 2)  # diagnostics, NOT estimates of actual broker charges


@dataclass(frozen=True)
class Rules:
    context_bars: int = 120
    warmup_bars: int = 200
    hold_bars: int = 20
    delay_bars: int = 1  # signal i -> entry open i+2, NOT the signal close
    development_fraction: float = 0.70


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def implementation() -> dict:
    # Text reads normalize CRLF/LF so a Windows Git checkout has the same hash.
    return {path.name: hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            for path in (Path(__file__), Path(strategy_filter.__file__))}


def validate_bars(records: list[dict]) -> pd.DataFrame:
    if not records:
        raise ValueError("Tidak ada candle pada snapshot.")
    previous = None
    for row in records:
        if set(row) != set(FIELDS):
            raise ValueError("Kolom candle berbeda; jangan mengubah snapshot.")
        for field in FIELDS:
            value = row[field]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("Candle mengandung nilai nonfinite/non-numerik.")
        if type(row["time"]) is not int or row["time"] <= 0:
            raise ValueError("Epoch candle harus integer positif.")
        if previous is not None and row["time"] <= previous:
            raise ValueError("Epoch harus unik dan meningkat; tidak diurutkan/dihapus otomatis.")
        previous = row["time"]
        if min(row[f] for f in ("open", "high", "low", "close")) <= 0:
            raise ValueError("Harga candle harus positif.")
        if (row["high"] < max(row["open"], row["close"], row["low"])
                or row["low"] > min(row["open"], row["close"], row["high"])):
            raise ValueError("OHLC tidak konsisten.")
        if any(row[f] < 0 or row[f] != int(row[f]) for f in ("spread", "tick_volume")):
            raise ValueError("Spread/volume harus integer nonnegatif.")
    return pd.DataFrame(records)


def legacy_indicators(context: pd.DataFrame) -> pd.DataFrame:
    """Match monitor blob 6b2b10e9 finite-window EWM, including flat-RSI convention.

    This is not an assertion of identical seeding to MT5's built-in indicators.
    Keeping it explicit avoids silently retuning features during execution research.
    """
    result = context.copy()
    result["ema20"] = result["close"].ewm(span=20, adjust=False).mean()
    result["ema50"] = result["close"].ewm(span=50, adjust=False).mean()
    delta = result["close"].diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    result["rsi14"] = (100 - (100 / (1 + rs))).fillna(100.0)
    previous = result["close"].shift(1)
    tr = pd.concat([result["high"] - result["low"],
                    (result["high"] - previous).abs(),
                    (result["low"] - previous).abs()], axis=1).max(axis=1)
    result["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return result


def split_indices(total: int, rules: Rules) -> tuple[dict[str, list[int]], int, int]:
    if (min(rules.context_bars, rules.warmup_bars, rules.hold_bars) < 1
            or rules.delay_bars < 0 or rules.warmup_bars < rules.context_bars
            or not 0 < rules.development_fraction < 1):
        raise ValueError("Aturan indeks tidak valid.")
    # Exit is an OPEN at i+1+delay+hold; it must be within saved CLOSED bars.
    tail = 1 + rules.delay_bars + rules.hold_bars
    indices = list(range(rules.warmup_bars, total - tail))
    if len(indices) < 2:
        raise ValueError("Candle tidak cukup untuk dua segmen dan exit.")
    cut = min(max(int(len(indices) * rules.development_fraction), 1), len(indices) - 1)
    boundary = indices[cut]
    development = [i for i in indices[:cut] if i + tail < boundary]
    if not development:
        raise ValueError("Development habis setelah purge; butuh histori lebih panjang.")
    return {"EARLY": development, "LATE": indices[cut:]}, boundary, cut - len(development)


def candidates(df: pd.DataFrame, indices: list[int], point: float,
               rules: Rules, config: FilterConfig, evaluator: Callable = evaluate_setup) -> dict[int, str]:
    result = {}
    for i in indices:
        # No price, spread or indicator from i+1 onward enters the decision.
        context = legacy_indicators(df.iloc[i - rules.context_bars + 1:i + 1].copy())
        decision = evaluator(context, point=point, config=config)
        if decision.qualifies:
            if decision.direction not in {"BUY", "SELL"}:
                raise ValueError("Filter lolos tanpa arah BUY/SELL.")
            result[i] = decision.direction
    return result


def schedule(directions: dict[int, str], rules: Rules) -> list[tuple[int, int, int, str]]:
    result, available_signal = [], -1
    for i, direction in sorted(directions.items()):
        if i < available_signal:
            continue  # no new signal while entry is pending or a hypothetical position is open
        entry = i + 1 + rules.delay_bars
        exit_index = entry + rules.hold_bars
        result.append((i, entry, exit_index, direction))
        available_signal = exit_index  # re-evaluate only after the exit bar has CLOSED
    return result


def quote_proxy(entry_bid: float, exit_bid: float, entry_spread: float,
                exit_spread: float, point: float, direction: str, multiplier: float) -> dict:
    numbers = (entry_bid, exit_bid, entry_spread, exit_spread, point, multiplier)
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in numbers):
        raise ValueError("Input harga/biaya harus finite.")
    if min(entry_bid, exit_bid, point) <= 0 or min(entry_spread, exit_spread, multiplier) < 0:
        raise ValueError("Input harga/biaya di luar batas.")
    if direction not in {"BUY", "SELL"}:
        raise ValueError("Arah tidak valid.")
    # BID bars: long enters at synthetic ask, closes bid; short enters bid, covers ask.
    # Only PRECEDING CLOSED bar spread is used, never the fill bar's full-bar summary.
    entry = entry_bid + multiplier * entry_spread * point if direction == "BUY" else entry_bid
    exit_price = exit_bid if direction == "BUY" else exit_bid + multiplier * exit_spread * point
    sign = 1 if direction == "BUY" else -1
    gross = sign * (exit_bid - entry_bid) / entry_bid * 100
    adjusted = sign * (exit_price - entry) / entry_bid * 100
    return {"entry_quote_proxy": entry, "exit_quote_proxy": exit_price,
            "gross_bid_return_pct": gross, "spread_proxy_return_pct": adjusted,
            "spread_drag_pp": gross - adjusted}


def simulate(df: pd.DataFrame, point: float, rules: Rules, config: FilterConfig,
             evaluator: Callable = evaluate_setup, progress: Callable = print) -> tuple[list[dict], list[dict]]:
    parts, boundary, purged = split_indices(len(df), rules)
    all_trades, summaries = [], []
    for segment, indices in parts.items():
        choices = candidates(df, indices, point, rules, config, evaluator)
        selected = schedule(choices, rules)
        progress(f"Segment={segment} | eligible={len(indices)} | qualified={len(choices)} | simulated_positions={len(selected)}")
        for mult in SPREAD_SCENARIOS:
            current = []
            for i, e, x, direction in selected:
                costs = quote_proxy(float(df.iloc[e]["open"]), float(df.iloc[x]["open"]),
                                    float(df.iloc[e - 1]["spread"]), float(df.iloc[x - 1]["spread"]),
                                    point, direction, mult)
                row = {"segment": segment, "spread_multiplier": mult, "direction": direction,
                       "signal_index": i, "entry_index": e, "exit_index": x,
                       "signal_epoch_raw": int(df.iloc[i]["time"]),
                       "entry_epoch_raw": int(df.iloc[e]["time"]),
                       "exit_epoch_raw": int(df.iloc[x]["time"]),
                       "signal_close": float(df.iloc[i]["close"]),
                       "entry_bid_open": float(df.iloc[e]["open"]),
                       "exit_bid_open": float(df.iloc[x]["open"]),
                       "entry_spread_lagged_points": int(df.iloc[e - 1]["spread"]),
                       "exit_spread_lagged_points": int(df.iloc[x - 1]["spread"]), **costs}
                current.append(row)
            values = [r["spread_proxy_return_pct"] for r in current]
            summaries.append({"segment": segment, "spread_multiplier": mult,
                "eligible": len(indices), "qualified": len(choices), "n": len(values),
                "mean_pct": mean(values) if values else None,
                "median_pct": median(values) if values else None,
                "positive": sum(v > 0 for v in values), "negative": sum(v < 0 for v in values),
                "zero": sum(v == 0 for v in values),
                "avg_spread_drag_pp": mean(r["spread_drag_pp"] for r in current) if current else None,
                "boundary_index": boundary, "purged_decisions": purged})
            all_trades.extend(current)
    return summaries, all_trades


def load_snapshot(path: Path) -> dict:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    body = envelope["body"]
    if digest(body) != envelope["sha256"] or body.get("version") != VERSION:
        raise ValueError("Snapshot rusak/versi berbeda; jangan hapus atau ambil ulang otomatis.")
    if body["implementation"] != implementation():
        raise ValueError("Kode berubah sejak freeze; gunakan versi commit pembuat snapshot.")
    if body["rules"] != asdict(Rules()) or body["filter"] != asdict(FilterConfig(min_score=5)):
        raise ValueError("Aturan baseline berubah; tidak boleh tuning snapshot diam-diam.")
    if body["chart_mode"] != "BID" or body["timeframe"] not in TIMEFRAMES:
        raise ValueError("Mode harga/timeframe berbeda.")
    point = body["point"]
    if type(point) not in (int, float) or not math.isfinite(point) or point <= 0:
        raise ValueError("Point snapshot tidak valid.")
    validate_bars(body["bars"])
    split_indices(len(body["bars"]), Rules(**body["rules"]))
    return body


def prepare(path: Path, *, mt5_path: str, symbol: str = "XAUUSD", timeframe: str = "M5",
            count: int = 5000, mt5=None) -> dict:
    if path.exists():
        body = load_snapshot(path)
        print("Snapshot lama dipakai; tidak resampling, MT5_CONNECTIONS=0, OPENAI_CALLS=0.")
        return body
    if timeframe not in TIMEFRAMES or not 1000 <= count <= 50000:
        raise ValueError("Timeframe/jumlah bar tidak valid (1000..50000).")
    if not symbol or len(symbol) > 64 or any(not(c.isalnum() or c in '._#-') for c in symbol):
        raise ValueError("Nama symbol tidak valid.")
    if mt5 is None:
        import MetaTrader5 as mt5  # ONLY this mode accesses the terminal
    try:
        if not mt5.initialize(mt5_path):
            raise ValueError("MT5 initialize gagal; periksa terminal demo yang sudah login.")
        terminal = mt5.terminal_info()
        if terminal is None or not terminal.connected or not mt5.symbol_select(symbol, True):
            raise ValueError("Terminal/symbol belum siap.")
        info = mt5.symbol_info(symbol)
        if info is None or info.chart_mode != getattr(mt5, "SYMBOL_CHART_MODE_BID", 0):
            raise ValueError("Hanya chart BID didukung; tidak menganggap Last sebagai Bid.")
        point = float(info.point)
        if not math.isfinite(point) or point <= 0:
            raise ValueError("Point symbol tidak valid.")
        rates = mt5.copy_rates_from_pos(symbol, getattr(mt5, f"TIMEFRAME_{timeframe}"), 1, count)
        if rates is None or len(rates) != count:
            got = 0 if rates is None else len(rates)
            raise ValueError(f"Histori tidak lengkap: meminta {count}, menerima {got}. Tidak dibuat snapshot.")
        records = pd.DataFrame(rates)[list(FIELDS)].to_dict(orient="records")
        validate_bars(records)
        body = {"version": VERSION, "captured_at_utc": now(), "symbol": symbol,
                "timeframe": timeframe, "point": point, "chart_mode": "BID",
                "python": platform.python_version(), "pandas": pd.__version__,
                "mt5_package": getattr(mt5, "__version__", "unknown"),
                "time_basis": "RAW_MT5_NO_OFFSET_APPLIED", "rules": asdict(Rules()),
                "filter": asdict(FilterConfig(min_score=5)), "implementation": implementation(),
                "bars": records}
        split_indices(len(records), Rules())
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation: preserves earlier data, including on concurrent prepare.
        with path.open("x", encoding="utf-8") as stream:
            stream.write(encode({"body": body, "sha256": digest(body)}))
            stream.flush()
            os.fsync(stream.fileno())
        print(f"Frozen | closed_bars={len(records)} | point={point} | chart=BID | OPENAI_CALLS=0")
        return body
    finally:
        mt5.shutdown()


def render_report(body: dict, summaries: list[dict], trades: list[dict]) -> str:
    def fmt(value):
        return "-" if value is None else f"{value:.6f}"
    rules = Rules(**body["rules"])
    step = TIMEFRAMES[body["timeframe"]]
    records = body["bars"]
    gaps = sum(b["time"] - a["time"] != step for a, b in zip(records, records[1:]))
    lines = ["# Baseline lokal - asumsi entry dan spread (bukan P/L akun)", "",
        f"Dibuat: {now()}", f"Market: {body['symbol']} {body['timeframe']}; bars={len(records)}",
        f"Snapshot SHA256: {digest(body)}",
        f"Raw epoch range: {records[0]['time']} .. {records[-1]['time']}",
        "**EKSPLORASI, bukan holdout baru. NO_ORDER_EXECUTION | OPENAI_CALLS=0.**",
        "Hanya Local Filter score 5; bukan pengujian ulang AI lama/blind.",
        "", "## Aturan tetap",
        f"Konteks indikator={rules.context_bars}, warmup={rules.warmup_bars}, hold={rules.hold_bars} bar.",
        f"Signal i -> entry OPEN i+{1+rules.delay_bars} -> exit OPEN i+{1+rules.delay_bars+rules.hold_bars}.",
        "Delay satu bar penuh adalah ASUMSI, bukan hasil pengukuran latensi API.",
        "Hanya satu posisi simulasi; abaikan sinyal ketika pending/masih aktif.",
        "EARLY/LATE berurutan; label/exit EARLY tidak boleh memasuki batas sinyal LATE.",
        "Segmen ini mungkin sudah pernah diperiksa; bukan validation independen.",
        f"Purge di batas: {summaries[0]['purged_decisions']} sinyal; boundary index={summaries[0]['boundary_index']}.",
        "", "## Hasil skenario", "",
        "| Segment | Spread x | Eligible | Qualified | Posisi simulasi | Mean % | Median % | + | - | 0 | Spread drag pp |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for s in summaries:
        lines.append(f"| {s['segment']} | {s['spread_multiplier']} | {s['eligible']} | {s['qualified']} | "
            f"{s['n']} | {fmt(s['mean_pct'])} | {fmt(s['median_pct'])} | {s['positive']} | "
            f"{s['negative']} | {s['zero']} | {fmt(s['avg_spread_drag_pp'])} |")
    lines += ["", "0x = pembanding tanpa spread; 1x = proxy spread bar SEBELUM fill; 2x = stress dua kali proxy.",
        "BUY: ask entry sintetis lalu bid exit. SELL: bid entry lalu ask exit sintetis.",
        "Semua persen dibagi BID open saat entry (notional harga), bukan saldo/margin akun.",
        "Jadwal posisi sama di ketiga skenario; tidak dipilih ulang berdasarkan hasil/biaya.",
        "N posisi simulasi tidak sama dengan jumlah candle qualified. N=0 berarti tidak ada hasil, bukan 0% profit.",
        "", "## Batas dan kualitas data",
        f"Transisi raw timestamp tidak tepat {step}s: {gaps}; spread bar nol: {sum(r['spread']==0 for r in records)}.",
        "Gap tidak diisi candle palsu: dapat berasal dari sesi libur, data hilang, atau pergantian jam server.",
        "20 bar bisa lebih lama dari 100 menit pada M5. Pembiayaan saat gap belum dihitung.",
        "Tidak ada koreksi timezone otomatis; raw bar IDs tidak menyatakan waktu UTC terverifikasi.",
        "Spread bar sebelumnya hanyalah PROXY, bukan bid/ask historis pada detik fill; 2x bukan batas terburuk.",
        "Spread=0 di histori bukan bukti biaya nol. Spesifikasi symbol diambil saat capture, bukan arsip historis.",
        "Komisi, slippage, swap/pembiayaan, biaya API dan fill aktual belum dimodelkan; bukan hasil NET.",
        "Harga OPEN bukan janji harga yang bisa dieksekusi. Ini bukan backtest tick atau simulasi latensi detik.",
        "Tidak ada SL/TP, lot, risiko 0.25%, margin, saldo, equity curve atau drawdown akun.",
        "Seed EWM/RSI meniru kode monitor lama; belum disamakan dengan indikator native MT5.",
        "Hasil tidak sebanding langsung dengan 10-sampel: dataset, entry/exit dan jadwal posisi berbeda.",
        "Tidak memilih pemenang, membalik arah, atau menyetel parameter dari laporan ini.",
        "Bekukan desain lalu gunakan periode baru/forward untuk evaluasi strategi; snapshot ini tetap eksplorasi."]
    if pd.__version__ != body["pandas"]:
        lines.append(f"WARNING pandas berubah: capture={body['pandas']}, report={pd.__version__}; angka mungkin berbeda.")
    return "\n".join(lines) + "\n"


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     suffix=".tmp", delete=False) as stream:
        tmp = Path(stream.name)
        stream.write(text)
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def report(path: Path = SNAPSHOT, report_path: Path = REPORT, trades_path: Path = TRADES) -> str:
    if len({p.resolve() for p in (path, report_path, trades_path)}) != 3:
        raise ValueError("Input dan output harus berbeda.")
    body = load_snapshot(path)
    df = validate_bars(body["bars"])
    summaries, trades = simulate(df, body["point"], Rules(**body["rules"]), FilterConfig(**body["filter"]))
    stream = io.StringIO(newline="")
    # Header is present even if no positions exist.
    fields = list(trades[0]) if trades else ["segment", "spread_multiplier", "direction",
                                           "signal_epoch_raw", "spread_proxy_return_pct"]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(trades)
    text = render_report(body, summaries, trades)
    atomic_text(trades_path, stream.getvalue())
    atomic_text(report_path, text)
    print(text)
    print(f"Saved report: {report_path}")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--report", action="store_true")
    parser.add_argument("--mt5-path", default=r"C:\Program Files\MetaTrader 5\terminal64.exe")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--timeframe", choices=TIMEFRAMES, default="M5")
    parser.add_argument("--bars", type=int, default=5000)
    args = parser.parse_args()
    try:
        if args.prepare:
            prepare(SNAPSHOT, mt5_path=args.mt5_path, symbol=args.symbol,
                    timeframe=args.timeframe, count=args.bars)
        elif args.report:
            report()
        else:
            parser.print_help()  # no network on accidental run without mode
        return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError) as exc:
        print(f"BASELINE STOP | {type(exc).__name__}: {exc}")
        print("Jangan hapus snapshot/database pilot. Tidak ada order atau permintaan OpenAI.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
