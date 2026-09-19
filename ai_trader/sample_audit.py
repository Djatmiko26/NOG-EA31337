"""Audit saved blind-pilot samples offline. No MT5, API, .env, or order access."""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import math
import os
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
HORIZONS = (5, 10, 20)
POLICIES = ("local", "original", "blind", "agree_only")
STATUSES = {"STARTED", "SUCCESS", "ERROR", "REFUSED", "INVALID", "INCOMPLETE"}
DESIGN = "remove_local_filter_only_v1"


def fingerprint(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def finite(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Label harus angka finite; teks, boolean, NaN/Inf ditolak.")
    return float(value)


def signal_action(result: dict) -> str:
    signal = result.get("signal", {})
    action = signal.get("action")
    score = signal.get("confidence")
    if action not in {"BUY", "SELL", "WAIT"}:
        raise ValueError("Respons SUCCESS tidak mempunyai action yang valid.")
    if type(score) is not int or not 0 <= score <= 100:
        raise ValueError("Confidence tersimpan tidak valid.")
    return action


def read_snapshot(path: Path) -> tuple[dict, dict[int, dict]]:
    """mode=ro cannot create a missing DB; BEGIN makes reads consistent."""
    if not path.is_file():
        raise ValueError("Database blind belum ditemukan. Tidak membuat database atau memanggil API.")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        saved = db.execute("SELECT body, fingerprint FROM plan WHERE id=1").fetchone()
        if saved is None:
            raise ValueError("Database tidak mempunyai rencana sampel tersimpan.")
        plan = json.loads(saved[0])
        if fingerprint(plan) != saved[1]:
            raise ValueError("Fingerprint database berbeda. Audit dihentikan.")
        attempts = {}
        for index, status, body in db.execute(
            "SELECT sample_index, status, result FROM attempts ORDER BY sample_index"
        ):
            if status not in STATUSES:
                raise ValueError("Status percobaan tidak dikenal.")
            result = json.loads(body) if body else {}
            if not isinstance(result, dict):
                raise ValueError("Format hasil percobaan tidak valid.")
            if status == "STARTED":
                if body is not None:
                    raise ValueError("STARTED tidak boleh memiliki hasil akhir.")
            elif not body or result.get("status") != status:
                raise ValueError("Status percobaan dan hasil JSON berbeda.")
            attempts[index] = {**result, "status": status}
        return plan, attempts
    finally:
        db.close()


def validate_plan(plan: dict, attempts: dict[int, dict]) -> None:
    if plan.get("version") != DESIGN:
        raise ValueError("Bukan database eksperimen blind yang didukung.")
    source, baseline = plan["source_plan"], plan["baseline_attempts"]
    if (fingerprint(source) != plan["source_fingerprint"]
            or fingerprint(baseline) != plan["baseline_fingerprint"]):
        raise ValueError("Fingerprint sumber atau hasil pembanding berbeda.")
    samples = source["samples"]
    if source.get("version") != 2 or not 1 <= len(samples) <= 10:
        raise ValueError("Versi atau jumlah sampel sumber tidak didukung.")
    ids = [s["sample_id"] for s in samples]
    if any(not isinstance(s, str) or not s for s in ids) or len(set(ids)) != len(ids):
        raise ValueError("ID sampel kosong atau duplikat.")
    if set(baseline) != {str(i) for i in range(len(samples))}:
        raise ValueError("Hasil pembanding tidak lengkap.")
    if set(attempts) - set(range(len(samples))):
        raise ValueError("Percobaan berada di luar sampel tersimpan.")
    expected = copy.deepcopy(samples)
    for sample in expected:
        removed = sample["payload"].pop("local_filter", None)
        if not isinstance(removed, dict):
            raise ValueError("Payload sumber tidak mempunyai blok local_filter.")
    if plan["spec"] != source["spec"] or plan["samples"] != expected:
        raise ValueError("Data/settings berubah selain penghapusan local_filter.")
    for i in range(len(samples)):
        if baseline[str(i)].get("status") != "SUCCESS":
            raise ValueError("Pembanding asli belum memiliki respons valid lengkap.")
        signal_action(baseline[str(i)])
        result = attempts.get(i)
        if result:
            if result.get("status") not in STATUSES:
                raise ValueError("Status percobaan tidak dikenal.")
            if result["status"] == "SUCCESS":
                signal_action(result)


def directional(raw: float, action: str) -> float:
    if action == "WAIT":
        return 0.0
    if action not in {"BUY", "SELL"}:
        raise ValueError("Arah lokal harus BUY/SELL.")
    return raw if action == "BUY" else -raw


def check_label(outcome: dict, row: dict, h: int) -> float:
    """Check stored-label arithmetic only, NOT broker history or execution prices.

    Stored entry/exit prices are rounded to 5 decimals and returns to 6.
    Interval bounds allow that rounding without silently rewriting any label.
    """
    entry, exit_price = finite(outcome["entry_close"]), finite(outcome[f"close_{h}"])
    raw, local = finite(outcome[f"market_return_{h}_pct"]), finite(outcome[f"filter_return_{h}_pct"])
    if entry <= 0 or exit_price <= 0 or finite(row["close"]) != entry:
        raise ValueError("Harga acuan label dan baris keputusan tidak konsisten.")
    direction = row["filter_direction"]
    if direction not in {"BUY", "SELL"}:
        raise ValueError("Arah lokal tidak valid.")
    if not math.isclose(local, directional(raw, direction), rel_tol=0, abs_tol=1.01e-6):
        raise ValueError("Tanda return lokal berbeda dari arah/label pasar.")
    half_unit = 0.000005
    if entry <= half_unit or exit_price <= half_unit:
        raise ValueError("Presisi harga tersimpan terlalu rendah untuk audit aritmetika.")
    lower = ((exit_price - half_unit) / (entry + half_unit) - 1) * 100
    upper = ((exit_price + half_unit) / (entry - half_unit) - 1) * 100
    if not lower - 0.00000051 <= raw <= upper + 0.00000051:
        raise ValueError("Return tersimpan tidak cocok dengan harga label, termasuk toleransi pembulatan.")
    return raw


def audit_rows(plan: dict, attempts: dict[int, dict]) -> list[dict]:
    validate_plan(plan, attempts)
    rows = []
    for i, sample in enumerate(plan["samples"]):
        old = plan["baseline_attempts"][str(i)]
        new = attempts.get(i, {})
        status = new.get("status", "NOT_ATTEMPTED")
        action = signal_action(new) if status == "SUCCESS" else ""
        row = {
            "sample_no": i + 1, "sample_id": sample["sample_id"],
            "candle_time_server": sample["row"].get("candle_time_server", ""),
            "local_direction": sample["row"]["filter_direction"],
            "original_action": signal_action(old), "blind_action": action,
            "blind_status": status,
            "group": "MISSING" if not action else "WAIT" if action == "WAIT" else "ACTIVE",
        }
        for h in HORIZONS:
            raw = check_label(sample["outcome"], sample["row"], h)
            row[f"local_{h}_pct"] = finite(sample["outcome"][f"filter_return_{h}_pct"])
            row[f"original_{h}_pct"] = directional(raw, row["original_action"])
            row[f"blind_{h}_pct"] = directional(raw, action) if action else None
            row[f"agree_only_{h}_pct"] = (
                row[f"local_{h}_pct"] if action == row["local_direction"] else 0.0
            ) if action else None
            row[f"delta_{h}_pp"] = (
                row[f"blind_{h}_pct"] - row[f"original_{h}_pct"]
            ) if action else None
        rows.append(row)
    return rows


def describe(values: list[float]) -> dict:
    values = [finite(v) for v in values]
    n = len(values)
    return {"n": n, "mean": mean(values) if n else None,
            "median": median(values) if n else None,
            "positive": sum(v > 0 for v in values), "negative": sum(v < 0 for v in values),
            "flat": sum(v == 0 for v in values),
            "minimum": min(values) if n else None, "maximum": max(values) if n else None}


def leave_one_out(values: list[float]) -> tuple[float | None, float | None]:
    """Descriptive sensitivity, NOT a confidence interval or sample-selection rule."""
    values = [finite(v) for v in values]
    if len(values) < 2:
        return None, None
    alternatives = [mean(values[:i] + values[i + 1:]) for i in range(len(values))]
    return min(alternatives), max(alternatives)


def cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if type(value) is float:
        return f"{value:.6f}"
    return str(value).replace("|", "/").replace("\n", " ").replace("\r", " ").replace("`", "'")


def table(headers: list, rows: list[list]) -> list[str]:
    return ["| " + " | ".join(map(cell, headers)) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
            *["| " + " | ".join(map(cell, row)) + " |" for row in rows]]


def build_report(plan: dict, attempts: dict[int, dict], rows: list[dict]) -> str:
    matched = [r for r in rows if r["blind_status"] == "SUCCESS"]
    active = [r for r in matched if r["group"] == "ACTIVE"]
    waits = [r for r in matched if r["group"] == "WAIT"]
    lines = ["# Audit per sampel - pilot OpenAI (OFFLINE)", "",
             f"Dibuat: {datetime.now(timezone.utc).isoformat()}",
             f"Model tersimpan: {cell(plan['spec']['model'])}",
             f"Fingerprint sumber: {plan['source_fingerprint']}",
             f"Fingerprint hasil tersimpan: {fingerprint({str(i): v for i, v in attempts.items()})}", "",
             "**Eksplorasi pada sampel yang sudah diperiksa; bukan holdout baru atau bukti profit.**",
             "API_CALLS=0 | MT5_CONNECTIONS=0 | DATABASE_READ_ONLY | NO_ORDER_EXECUTION", "",
             f"Sampel: {len(rows)}; pasangan valid: {len(matched)}; ACTIVE: {len(active)}; WAIT: {len(waits)}.",
             f"Belum dicoba: {sum(r['blind_status'] == 'NOT_ATTEMPTED' for r in rows)}; "
             f"gagal/tidak pasti: {sum(r['blind_status'] not in {'SUCCESS', 'NOT_ATTEMPTED'} for r in rows)}.",
             "Semua statistik berpasangan memakai subset SUCCESS yang sama. MISSING bukan WAIT.",
             "ACTIVE berarti klasifikasi BUY/SELL, bukan order nyata. WAIT=0 bukan kemenangan.",
             "Pemeriksaan aritmetika harga/label lolos dengan toleransi pembulatan; histori broker tidak diverifikasi ulang.", "",
             "## 1. Hasil tiap sampel", ""]
    for h in HORIZONS:
        lines += [f"### Horizon {h} candle (persen perubahan harga kotor)", ""]
        lines += table(["#", "Waktu server", "Local", "AI lama", "AI blind", "Status",
                        "Local %", "AI lama %", "Blind %", "Delta pp"], [
            [r["sample_no"], r["candle_time_server"], r["local_direction"], r["original_action"],
             r["blind_action"], r["blind_status"], r[f"local_{h}_pct"], r[f"original_{h}_pct"],
             r[f"blind_{h}_pct"], r[f"delta_{h}_pp"]] for r in rows])
        lines.append("")
    lines += ["## 2. Rerata, median, dan jumlah tanda return", "",
              "Positif/negatif/nol adalah tanda endpoint, BUKAN win rate transaksi setelah biaya.",
              "Untuk BLIND dan AGREE_ONLY, nol dapat berasal dari tidak mengambil posisi.", ""]
    summary = []
    for h in HORIZONS:
        for policy in POLICIES:
            s = describe([r[f"{policy}_{h}_pct"] for r in matched])
            summary.append([h, policy, s["n"], s["mean"], s["median"], s["positive"],
                            s["negative"], s["flat"], s["minimum"], s["maximum"]])
    lines += table(["Bars", "Policy", "N", "Mean %", "Median %", "+", "-", "0", "Min %", "Max %"], summary)
    lines += ["", "## 3. Kandidat aktif dibanding kandidat WAIT", "",
              "Local pada WAIT adalah hasil arah lokal yang dilewati, bukan hasil order AI.",
              "N kelompok di bawah berbeda; jangan menyamakan rerata ACTIVE dengan rerata seluruh sampel.", ""]
    groups = []
    for h in HORIZONS:
        for name, subset in (("ACTIVE", active), ("WAIT", waits)):
            s = describe([r[f"local_{h}_pct"] for r in subset])
            b = describe([r[f"blind_{h}_pct"] for r in subset])
            groups.append([h, name, s["n"], s["mean"], s["median"], s["positive"], s["negative"],
                           s["flat"], b["mean"]])
    lines += table(["Bars", "Kelompok", "N", "Local mean %", "Local median %", "Local +", "Local -", "Local 0", "Blind mean %"], groups)
    lines += ["", "## 4. Sensitivitas jika satu sampel dikeluarkan bergantian", "",
              "Diterapkan ke delta Blind - Original pada SEMUA pasangan valid, termasuk WAIT.",
              "Rentang ini BUKAN interval kepercayaan. Tidak ada baris yang dihapus dari data.", ""]
    sensitivity = []
    for h in HORIZONS:
        deltas = [r[f"delta_{h}_pp"] for r in matched]
        low, high = leave_one_out(deltas)
        sensitivity.append([h, len(deltas), mean(deltas) if deltas else None,
                            max(0, len(deltas) - 1), low, high])
    lines += table(["Bars", "N penuh", "Mean delta pp", "N tanpa 1", "Minimum mean pp", "Maximum mean pp"], sensitivity)
    counts = Counter(r["blind_status"] for r in rows)
    lines += ["", "## 5. Batas audit", "",
              "Status: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
              "Audit menghitung dari snapshot lokal, bukan mengambil ulang candle atau jawaban AI.",
              "Return dari harga close sinyal tidak menjamin harga tersebut bisa dieksekusi setelah respons API.",
              "Spread, komisi, slippage, pembiayaan, latensi, dan biaya API belum dikurangi.",
              "Tidak ada simulasi SL/TP, lot, saldo, equity curve, atau drawdown.",
              "Median/min/max dan sensitivitas adalah deskriptif; tidak memilih threshold/prompt/strategi terbaik.",
              "Timestamp dan offset diwarisi, bukan dikonfirmasi; ketiadaan kontaminasi pelatihan tidak dibuktikan.",
              "Satu respons per kondisi tidak memisahkan efek petunjuk dari variasi model atau perubahan backend.",
              "Hasil subset sukses dapat bias jika kegagalan tidak acak; data gagal tidak dijadikan nol.",
              "Gunakan periode baru/forward untuk evaluasi berikutnya, bukan menyetel ulang pada sepuluh sampel ini.", ""]
    return "\n".join(lines)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(text)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def csv_cell(value: Any) -> Any:
    # Avoid interpreting text cells as spreadsheet formulas; numbers remain numbers.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def run_audit(database: Path, output_dir: Path) -> tuple[Path, Path]:
    report_path = output_dir / "openai_sample_audit.md"
    csv_path = output_dir / "openai_sample_audit.csv"
    if database.resolve() in {report_path.resolve(), csv_path.resolve()}:
        raise ValueError("Output tidak boleh menimpa database input.")
    plan, attempts = read_snapshot(database)
    rows = audit_rows(plan, attempts)
    report = build_report(plan, attempts, rows)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows({k: csv_cell(v) for k, v in r.items()} for r in rows)
    atomic_write(csv_path, stream.getvalue())
    atomic_write(report_path, report)
    print(report)
    print(f"Saved audit: {report_path}")
    return report_path, csv_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=BASE_DIR / "data" / "openai_blind.sqlite3")
    parser.add_argument("--output-dir", type=Path, default=BASE_DIR / "data")
    args = parser.parse_args(argv)
    try:
        run_audit(args.database, args.output_dir)
        return 0
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, OSError, sqlite3.Error) as exc:
        # Validation errors are authored here; avoid dumping rows, credentials or raw JSON.
        message = str(exc) if type(exc) is ValueError else type(exc).__name__
        print(f"AUDIT STOP: {message}. Data lama tidak diubah; jangan jalankan API ulang.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
