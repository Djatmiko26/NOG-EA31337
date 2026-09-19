"""Bounded local-filter information ablation on the EXISTING frozen pilot.

--prepare (default) / --report: offline, no MT5 or OpenAI calls.
--run: at most ten NEW paid attempts in a separate, persistent database.
Only the payload's local_filter block changes. This is exploratory, not a holdout.
"""
from __future__ import annotations

import argparse
import copy
import csv
import io
import json
import math
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from pilot_support import (
    HARD_MAX_ATTEMPTS, HORIZONS, INSTRUCTIONS, PROMPT_VERSION, SCHEMA,
    PilotStore, digest, execute_plan, utc_now, validate_signal,
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SOURCE_FILE = DATA_DIR / "openai_pilot.sqlite3"
DB_FILE = DATA_DIR / "openai_blind.sqlite3"
REPORT_FILE = DATA_DIR / "openai_blind_report.md"
ROWS_FILE = DATA_DIR / "openai_blind_comparison.csv"
DESIGN = "remove_local_filter_only_v1"
PAYLOAD_FIELDS = {
    "symbol", "timeframe", "latest_closed_candle_utc",
    "detected_server_utc_offset_hours", "local_filter", "candles",
}
CANDLE_FIELDS = {
    "time", "open", "high", "low", "close", "tick_volume", "spread",
    "ema20", "ema50", "rsi14", "atr14",
}
SPEC_FIELDS = {"model", "instructions", "schema", "max_output_tokens",
               "reasoning_effort", "prompt_version"}


def log(message: str) -> None:
    print(f"[{utc_now()}] {message}", flush=True)


def finite_number(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Expected a finite numeric field, not text or a boolean.")
    return float(value)


def candle_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Expected a timestamp string.")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("Payload timestamps must have a timezone.")
    return result


def blind_payload(payload: dict) -> dict:
    """Strict shape validation; remove ONLY local_filter, preserving all else."""
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_FIELDS:
        raise ValueError("Unexpected payload fields; stop rather than silently change the experiment.")
    if not isinstance(payload["local_filter"], dict):
        raise ValueError("Missing original local_filter block.")
    symbol = payload["symbol"]
    if (not isinstance(symbol, str) or not 1 <= len(symbol) <= 64
            or any(not (c.isalnum() or c in '._#-') for c in symbol)):
        raise ValueError("Unexpected symbol format.")
    if payload["timeframe"] not in {"M1", "M5", "M15", "M30", "H1", "H4"}:
        raise ValueError("Unsupported timeframe.")
    if abs(finite_number(payload["detected_server_utc_offset_hours"])) > 14:
        raise ValueError("Unexpected offset; no automatic correction is made.")
    candles = payload["candles"]
    if not isinstance(candles, list) or not candles:
        raise ValueError("Missing closed candles.")
    times = []
    for candle in candles:
        if not isinstance(candle, dict) or set(candle) != CANDLE_FIELDS:
            raise ValueError("Unexpected candle fields; direction/outcomes cannot be sent.")
        times.append(candle_time(candle["time"]))
        for field in CANDLE_FIELDS - {"time"}:
            finite_number(candle[field])
        if any(candle[f] <= 0 for f in ("open", "high", "low", "close", "ema20", "ema50")):
            raise ValueError("Invalid price/EMA.")
        if (candle["high"] < max(candle["open"], candle["close"], candle["low"])
                or candle["low"] > min(candle["open"], candle["close"], candle["high"])):
            raise ValueError("Inconsistent OHLC.")
        if any(candle[f] < 0 for f in ("atr14", "spread", "tick_volume")):
            raise ValueError("Negative ATR/spread/volume.")
        if not 0 <= candle["rsi14"] <= 100:
            raise ValueError("Invalid RSI.")
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("Candle timestamps must be strictly increasing.")
    if candle_time(payload["latest_closed_candle_utc"]) != times[-1]:
        raise ValueError("Latest closed candle does not match payload end.")
    result = copy.deepcopy(payload)
    del result["local_filter"]
    return result


def read_source(path: Path) -> tuple[dict, dict[int, dict]]:
    """SQLite read-only URI: never initializes or modifies the old database."""
    if not path.is_file():
        raise ValueError("Original data/openai_pilot.sqlite3 is missing; do not recreate it to retry.")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")  # one consistent read snapshot
        saved = db.execute("SELECT body, fingerprint FROM plan WHERE id=1").fetchone()
        if saved is None:
            raise ValueError("Original pilot has no frozen sample plan.")
        source = json.loads(saved[0])
        if digest(source) != saved[1]:
            raise ValueError("Original pilot fingerprint mismatch; stop.")
        rows = db.execute("SELECT sample_index, status, started_at_utc, result FROM attempts")
        attempts = {i: {**(json.loads(body) if body else {}), "status": status,
                        "started_at_utc": started} for i, status, started, body in rows}
        return source, attempts
    finally:
        db.close()


def validate_source(source: dict, attempts: dict[int, dict]) -> None:
    if source.get("version") != 2:
        raise ValueError("Expected the original bounded pilot version 2.")
    samples = source.get("samples", [])
    if not 1 <= len(samples) <= HARD_MAX_ATTEMPTS:
        raise ValueError("Source must have 1..10 samples; no additional sampling is allowed.")
    if set(attempts) != set(range(len(samples))):
        raise ValueError("Complete the original pilot first; no calls made here.")
    if len({s["sample_id"] for s in samples}) != len(samples):
        raise ValueError("Duplicate source samples.")
    spec = source["spec"]
    if (set(spec) != SPEC_FIELDS or spec["instructions"] != INSTRUCTIONS
            or spec["schema"] != SCHEMA or spec["prompt_version"] != PROMPT_VERSION):
        raise ValueError("Source prompt/schema is incompatible with this ablation.")
    if not isinstance(spec["model"], str) or not spec["model"].strip():
        raise ValueError("Missing original model; no fallback is permitted.")
    if (type(spec["max_output_tokens"]) is not int
            or not 256 <= spec["max_output_tokens"] <= 4096
            or spec["reasoning_effort"] not in {"none", "low", "medium", "high"}):
        raise ValueError("Invalid inherited request limits.")
    for i, sample in enumerate(samples):
        result = attempts[i]
        if result.get("status") != "SUCCESS":
            raise ValueError("All original samples need valid SUCCESS responses before this test.")
        validate_signal(result["signal"])
        blind_payload(sample["payload"])
        direction = sample["row"]["filter_direction"]
        if direction not in {"BUY", "SELL"}:
            raise ValueError("Source sample is not a directional local candidate.")
        for horizon in HORIZONS:
            raw = finite_number(sample["outcome"][f"market_return_{horizon}_pct"])
            local = finite_number(sample["outcome"][f"filter_return_{horizon}_pct"])
            if not math.isclose(local, raw if direction == "BUY" else -raw, abs_tol=1e-6):
                raise ValueError("Inconsistent original directional return.")


def make_plan(source: dict, attempts: dict[int, dict]) -> dict:
    validate_source(source, attempts)
    samples = copy.deepcopy(source["samples"])
    for sample in samples:
        sample["payload"] = blind_payload(sample["payload"])
    baseline = {str(i): copy.deepcopy(value) for i, value in attempts.items()}
    return {
        "version": DESIGN, "created_at_utc": utc_now(),
        "source_fingerprint": digest(source), "baseline_fingerprint": digest(baseline),
        "source_plan": copy.deepcopy(source), "baseline_attempts": baseline,
        "spec": copy.deepcopy(source["spec"]), "samples": samples,
    }


def validate_plan(plan: dict) -> None:
    if plan.get("version") != DESIGN:
        raise ValueError("Not a blind-pilot database.")
    source, baseline = plan["source_plan"], plan["baseline_attempts"]
    if digest(source) != plan["source_fingerprint"] or digest(baseline) != plan["baseline_fingerprint"]:
        raise ValueError("Source/baseline fingerprint mismatch.")
    attempts = {int(i): value for i, value in baseline.items()}
    expected = make_plan(source, attempts)
    if plan["spec"] != expected["spec"] or plan["samples"] != expected["samples"]:
        raise ValueError("Only local_filter removal is allowed; model, candles and outcomes must be unchanged.")


def prepare(store: PilotStore, source_path: Path = SOURCE_FILE) -> dict:
    if Path(store.db.execute("PRAGMA database_list").fetchone()[2]).resolve() == source_path.resolve():
        raise ValueError("Source and destination databases must be different.")
    existing = store.plan()
    if existing is not None:
        validate_plan(existing)
        log("Existing blind plan reused | OPENAI_CALLS=0 | original pilot unchanged")
        return existing
    source, attempts = read_source(source_path)
    plan = make_plan(source, attempts)
    store.save_plan(plan)
    log(f"Blind prepared | samples={len(plan['samples'])} | model={plan['spec']['model']} | "
        "only_change=remove_local_filter | OPENAI_CALLS=0")
    return plan


def execute_blind(store: PilotStore, client: Any, progress=log) -> None:
    plan = store.plan()
    if plan is None:
        raise ValueError("Run --prepare first; no automatic sampling is allowed.")
    validate_plan(plan)
    execute_plan(store, client, progress)  # reserves BEFORE HTTP; never auto-retries


def directional_return(raw: float, action: str) -> float:
    if action == "WAIT":
        return 0.0
    if action not in {"BUY", "SELL"}:
        raise ValueError("Unexpected direction.")
    return raw if action == "BUY" else -raw


def comparison_rows(plan: dict, attempts: dict[int, dict]) -> list[dict]:
    validate_plan(plan)
    if set(attempts) - set(range(len(plan["samples"]))):
        raise ValueError("Attempt outside the saved sample plan.")
    rows = []
    for i, sample in enumerate(plan["samples"]):
        old = plan["baseline_attempts"][str(i)]
        new = attempts.get(i, {})
        status = new.get("status", "NOT_ATTEMPTED")
        valid = status == "SUCCESS"
        signal = validate_signal(new["signal"]) if valid else None
        local, old_action = sample["row"]["filter_direction"], old["signal"]["action"]
        row = {
            "sample_id": sample["sample_id"],
            "candle_time_server": sample["row"].get("candle_time_server", ""),
            "local_direction": local, "original_action": old_action,
            "blind_status": status, "blind_action": signal["action"] if valid else "",
            "original_confidence": old["signal"]["confidence"],
            "blind_confidence": signal["confidence"] if valid else "",
            "blind_reason": signal["reason"] if valid else "",
            "changed_from_original": signal["action"] != old_action if valid else "",
            "model_requested": plan["spec"]["model"],
            "original_model_returned": old.get("model_returned", ""),
            "blind_model_returned": new.get("model_returned", ""),
            "original_response_id": old.get("response_id", ""),
            "blind_response_id": new.get("response_id", ""),
            "input_tokens": new.get("input_tokens", ""),
            "output_tokens": new.get("output_tokens", ""),
        }
        for h in HORIZONS:
            raw = float(sample["outcome"][f"market_return_{h}_pct"])
            row[f"local_{h}_pct"] = float(sample["outcome"][f"filter_return_{h}_pct"])
            row[f"original_{h}_pct"] = directional_return(raw, old_action)
            row[f"blind_{h}_pct"] = directional_return(raw, signal["action"]) if valid else ""
            row[f"agree_only_{h}_pct"] = (row[f"local_{h}_pct"]
                if valid and signal["action"] == local else 0.0 if valid else "")
        rows.append(row)
    return rows


def matched_metrics(rows: list[dict], horizon: int) -> dict:
    if horizon not in HORIZONS:
        raise ValueError("Unsupported horizon.")
    matched = [r for r in rows if r["blind_status"] == "SUCCESS"]
    result = {"n": len(matched)}
    for group in ("local", "original", "blind", "agree_only"):
        result[group] = mean(r[f"{group}_{horizon}_pct"] for r in matched) if matched else None
    result["delta"] = (mean(r[f"blind_{horizon}_pct"] - r[f"original_{horizon}_pct"]
                            for r in matched) if matched else None)
    return result


def build_report(plan: dict, attempts: dict[int, dict]) -> str:
    rows = comparison_rows(plan, attempts)
    matched = [r for r in rows if r["blind_status"] == "SUCCESS"]
    def fmt(value):
        return "-" if value is None else f"{value:.6f}"
    lines = ["# OpenAI blind-payload comparison", "", f"Generated: {utc_now()}",
        f"Model requested (unchanged): `{plan['spec']['model']}`",
        f"Source fingerprint: `{plan['source_fingerprint']}`", "",
        "**EXPLORATORY: previously inspected samples, not a fresh holdout. No orders.**",
        "Blind means ONLY that explicit local_filter hints were removed; candles/indicators remain.",
        "Samples, instructions, requested model, reasoning, schema and token cap are unchanged.",
        "The original results are reused offline, not requested or charged again.", "",
        f"Frozen samples: {len(rows)}; new attempts reserved: {len(attempts)}; valid blind responses: {len(matched)}.",
        f"Unattempted: {len(rows)-len(attempts)}; failed/uncertain: {len(attempts)-len(matched)}.",
        "Errors/refusals/incomplete/STARTED are missing results, NOT WAIT and NOT zero-return trades.",
        "All gross-average columns use the same valid-blind-response sample subset.", "",
        "| Bars | Paired N | Local % | Original AI % | Blind AI (WAIT=0) % | Blind agree-only % | Blind minus original (pp) |",
        "| --- | --- | --- | --- | --- | --- | --- |"]
    for h in HORIZONS:
        m = matched_metrics(rows, h)
        lines.append(f"| {h} | {m['n']} | {fmt(m['local'])} | {fmt(m['original'])} | "
                     f"{fmt(m['blind'])} | {fmt(m['agree_only'])} | {fmt(m['delta'])} |")
    lines += ["", "WAIT=0 is a gross no-position scenario, not a winning trade or fee refund.",
              "Blind agree-only follows the local direction only if the blind response agrees.",
              "", "## Per-sample directions", "",
              "| # | Server time (inherited) | Local | Original AI | Blind AI | Status | Changed? |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for i, row in enumerate(rows, 1):
        stamp = str(row["candle_time_server"]).replace("|", " ").replace("\n", " ")
        changed = str(row["changed_from_original"]) if row["blind_status"] == "SUCCESS" else "-"
        lines.append(f"| {i} | {stamp} | {row['local_direction']} | {row['original_action']} | "
                     f"{row['blind_action'] or '-'} | {row['blind_status']} | {changed} |")
    changed = sum(r["changed_from_original"] for r in matched)
    agrees = sum(r["blind_action"] == r["local_direction"] for r in matched)
    lines += ["", f"Changed from original: {changed}/{len(matched)}; blind agrees with local: {agrees}/{len(matched)}."]
    for action in ("BUY", "SELL", "WAIT"):
        lines.append(f"Blind {action}: {sum(r['blind_action'] == action for r in matched)}")
    waits = [r["local_20_pct"] for r in matched if r["blind_action"] == "WAIT"]
    lines += [f"Local 20-bar gross return on blind WAIT samples: N={len(waits)}, "
              f"mean={fmt(mean(waits) if waits else None)}%.", "", "## Returned model check"]
    mismatch = [r for r in matched if r["original_model_returned"] != r["blind_model_returned"]]
    unknown = [r for r in matched if not r["original_model_returned"] or not r["blind_model_returned"]]
    lines += [f"Different returned model IDs: {len(mismatch)}; missing model IDs: {len(unknown)}.",
              "Returned model IDs do not prove identical backend weights. A model alias may change.",
              "", "## Observed NEW token usage"]
    for field in ("input_tokens", "output_tokens"):
        known = [v[field] for v in attempts.values() if type(v.get(field)) is int]
        lines.append(f"{field}: {sum(known)}; observed on {len(known)}/{len(attempts)} new attempts.")
    lines += ["Unknown usage is not zero cost. Request/token bounds are NOT a dollar budget.",
        "", "## Interpretation limits",
        "Differences may reflect payload removal, ordinary model variability or backend changes.",
        "One response per condition is NOT a causal test proving anchoring; unchanged results do not prove its absence.",
        "The same selected samples remain biased toward the local filter; this is not independent validation.",
        "No instruction forces disagreement or WAIT. Different actions are not automatically better.",
        "Gross signal-close returns exclude spread, commissions, slippage, financing, latency and API cost.",
        "No executable entry prices, TP/SL, lot sizing, account equity or drawdown are simulated.",
        "Confidence is uncalibrated; historical training contamination cannot be ruled out.",
        "Source timestamps/offsets and outcome labels are reused, not independently verified.",
        "A subset of successful requests may be biased when failures are nonrandom.",
        "Do not tune repeatedly on these ten samples; reserve genuinely unseen/forward data.", ""]
    return "\n".join(lines)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     suffix=".tmp", delete=False) as handle:
        tmp = Path(handle.name)
        handle.write(text)
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def export(store: PilotStore) -> None:
    plan = store.plan()
    if plan is None:
        raise ValueError("Run --prepare first.")
    attempts = store.attempts()
    rows = comparison_rows(plan, attempts)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    atomic_write(ROWS_FILE, stream.getvalue())
    report = build_report(plan, attempts)
    atomic_write(REPORT_FILE, report)
    print("\n" + report)
    log(f"Saved report: {REPORT_FILE}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true", help="Offline sample copy (default).")
    mode.add_argument("--run", action="store_true", help="Explicit NEW paid API experiment.")
    mode.add_argument("--report", action="store_true", help="Offline comparison report only.")
    args = parser.parse_args()
    if args.run or args.report:
        if not DB_FILE.is_file():
            parser.error("Run --prepare first. No database was created and no API call was made.")
    store = PilotStore(DB_FILE)
    try:
        if not args.run and not args.report:
            prepare(store)
        elif args.run:
            plan = store.plan()
            if plan is None:
                raise ValueError("Run --prepare first.")
            validate_plan(plan)
            remaining = len(plan["samples"]) - len(store.attempts())
            log(f"Frozen BLIND pilot | model={plan['spec']['model']} | unattempted={remaining} | "
                "retries=0 | NEW_PAID_ATTEMPTS | NO_ORDER_EXECUTION")
            if remaining:
                from dotenv import load_dotenv
                from openai import OpenAI
                load_dotenv(BASE_DIR / ".env")
                key = os.getenv("OPENAI_API_KEY", "").strip()
                if not key:
                    raise ValueError("OPENAI_API_KEY missing; never share your .env.")
                with OpenAI(api_key=key, base_url="https://api.openai.com/v1",
                            max_retries=0, timeout=45.0) as client:
                    execute_blind(store, client, progress=log)
        export(store)
    except KeyboardInterrupt:
        log("Stopped; STARTED attempts remain uncertain and will NOT be retried.")
        if store.plan() is not None:
            export(store)
    finally:
        store.close()


if __name__ == "__main__":
    main()
