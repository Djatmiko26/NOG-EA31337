"""Legacy filename; this is an EXPLORATORY historical pilot, not a fresh holdout.

--prepare (default): freeze at most ten setups, no OpenAI calls.
--run: use the frozen sample; reserve each attempt before HTTP; never auto-retry.
--report: rebuild local reports without MT5 or OpenAI access.
"""
from __future__ import annotations

import argparse
import csv
import os
import tempfile
from pathlib import Path

from pilot_support import (
    HARD_MAX_ATTEMPTS, HORIZONS, INSTRUCTIONS, PROMPT_VERSION, SCHEMA,
    PilotStore, apply_outcome_signal, build_paired_report, digest, execute_plan,
    utc_now,
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_FILE = DATA_DIR / "openai_pilot.sqlite3"
DECISIONS_FILE = DATA_DIR / "openai_pilot_decisions.csv"
OUTCOMES_FILE = DATA_DIR / "openai_pilot_outcomes.csv"
REPORT_FILE = DATA_DIR / "openai_pilot_report.md"


def log(message: str) -> None:
    print(f"[{utc_now()}] {message}", flush=True)


def split_holdout_indices(indices: list[int], dev_fraction: float,
                          purge_bars: int = 0) -> tuple[list[int], list[int]]:
    if not 0 < dev_fraction < 1 or purge_bars < 0:
        raise ValueError("Invalid split fraction or purge length.")
    if len(indices) < 2:
        return list(indices), []
    cut = min(max(int(len(indices) * dev_fraction), 1), len(indices) - 1)
    dev, val = indices[:cut], indices[cut:]
    if purge_bars:
        dev = [i for i in dev if i + purge_bars < val[0]]
    return dev, val


def spaced_candidates(indices: list[int], gap: int = 20) -> list[int]:
    if gap < 1:
        raise ValueError("Gap must be positive.")
    selected: list[int] = []
    for i in sorted(set(indices)):
        if not selected or i - selected[-1] >= gap:
            selected.append(i)
    return selected


def load_settings() -> tuple[int, int, float, int]:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    bars = int(os.getenv("AI_VALIDATION_BARS", "5000"))
    score = int(os.getenv("AI_VALIDATION_SCORE", "5"))
    fraction = float(os.getenv("AI_VALIDATION_DEV_FRACTION", "0.70"))
    calls = int(os.getenv("AI_VALIDATION_MAX_CALLS", "10"))
    if bars < 1000 or not 1 <= score <= 6 or not .50 <= fraction <= .90:
        raise ValueError("Invalid AI_VALIDATION_BARS/SCORE/DEV_FRACTION.")
    if not 1 <= calls <= HARD_MAX_ATTEMPTS:
        raise ValueError("AI_VALIDATION_MAX_CALLS must be 1..10 for this pilot.")
    return bars, score, fraction, calls


def prepare(store: PilotStore) -> dict:
    existing = store.plan()
    if existing is not None:
        log("Existing frozen sample reused. No resampling and no API calls.")
        return existing

    # Imports are lazy: offline tests and --report/--run do not require MT5.
    from dataclasses import asdict, replace
    import pandas as pd
    import market_monitor as monitor
    from historical_replay import (
        ReplayConfig, build_decision_row, load_history,
        replay_decision_indices, select_evenly_spaced_indices,
    )
    from outcome_labeller import MAX_HORIZON, compute_outcome
    from strategy_filter import evaluate_setup

    bars, score, fraction, calls = load_settings()
    base = monitor.load_config()
    filter_config = replace(base.filter_config, min_score=score)
    base = replace(base, filter_config=filter_config, filter_enabled=True)
    tokens = int(os.getenv("AI_VALIDATION_MAX_OUTPUT_TOKENS", "2048"))
    effort = os.getenv("AI_VALIDATION_REASONING_EFFORT", "low").strip()
    if not 256 <= tokens <= 4096 or effort not in {"none", "low", "medium", "high"}:
        raise ValueError("Invalid output-token bound or reasoning effort.")
    context_size = max(base.candles_to_load, base.candles_to_ai,
                       filter_config.lookback + 2, 60)
    replay = ReplayConfig(bars=bars, warmup_bars=max(200, context_size),
                          openai_enabled=False, max_ai_calls=0)
    monitor.connect_mt5(base)
    try:
        info = monitor.mt5.symbol_info(base.symbol)
        if info is None or info.point <= 0:
            raise RuntimeError("MT5 symbol point unavailable.")
        history = load_history(base, replay)
        indices = replay_decision_indices(len(history), replay.warmup_bars, MAX_HORIZON)
        dev, validation = split_holdout_indices(indices, fraction, MAX_HORIZON)
        candidates, decisions = [], {}

        def context_at(i):
            # Match the live monitor's finite indicator window; no future rows.
            raw_context = history.iloc[max(0, i - context_size + 1):i + 1].copy()
            return monitor.add_indicators(raw_context)

        for i in validation:
            decision = evaluate_setup(context_at(i), point=float(info.point), config=filter_config)
            if decision.qualifies:
                candidates.append(i)
                decisions[i] = decision
        eligible = spaced_candidates(candidates, MAX_HORIZON)
        selected = select_evenly_spaced_indices(eligible, calls)
        if not selected:
            raise RuntimeError("No qualifying samples; no API call made.")
        samples = []
        for i in selected:
            context = context_at(i)
            row = build_decision_row(base, context.iloc[-1], decisions[i])
            payload = monitor.build_market_payload(base, context, decisions[i])
            # Outcomes are kept separately in the local snapshot, never in input.
            outcome = compute_outcome(pd.Series(row), history.iloc[i + 1:i + 1 + MAX_HORIZON])
            samples.append({"sample_id": digest({"epoch": row["candle_epoch_raw"],
                                                 "payload": payload}),
                            "row": row, "payload": payload, "outcome": outcome})
        plan = {
            "version": 2, "created_at_utc": utc_now(),
            "design": "exploratory_period_previously_inspected",
            "symbol": base.symbol, "timeframe": base.timeframe_name,
            "history_first_raw": int(history.iloc[0]["server_epoch_raw"]),
            "history_last_raw": int(history.iloc[-1]["server_epoch_raw"]),
            "validation_decisions": len(validation), "development_after_purge": len(dev),
            "qualified": len(candidates), "min_spacing_bars": MAX_HORIZON,
            "indicator_context_bars": context_size, "filter_config": asdict(filter_config),
            "spec": {"model": base.openai_model, "max_output_tokens": tokens,
                     "reasoning_effort": effort, "prompt_version": PROMPT_VERSION,
                     "instructions": INSTRUCTIONS, "schema": SCHEMA},
            "samples": samples,
        }
        store.save_plan(plan)
        log(f"Prepared | samples={len(samples)} | model={base.openai_model} | "
            f"max_attempts={len(samples)} | max_output_tokens={tokens} | OPENAI_CALLS=0")
        return plan
    finally:
        monitor.mt5.shutdown()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     suffix=".tmp", delete=False) as handle:
        tmp = Path(handle.name)
        handle.write(text)
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def csv_text(rows: list[dict]) -> str:
    import io
    stream = io.StringIO(newline="")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def export(store: PilotStore) -> None:
    plan = store.plan()
    if plan is None:
        raise ValueError("No saved sample. Run --prepare first.")
    attempts = store.attempts()
    rows, outcomes = [], []
    for i, sample in enumerate(plan["samples"]):
        attempt = attempts.get(i, {})
        signal = attempt.get("signal") if attempt.get("status") == "SUCCESS" else None
        row = dict(sample["row"])
        row.update({"sample_id": sample["sample_id"],
                    "attempt_status": attempt.get("status", "NOT_ATTEMPTED"),
                    "openai_called": i in attempts, "model": plan["spec"]["model"],
                    "model_returned": attempt.get("model_returned", ""),
                    "response_id": attempt.get("response_id", ""),
                    "input_tokens": attempt.get("input_tokens", ""),
                    "output_tokens": attempt.get("output_tokens", ""),
                    "error_type": attempt.get("error_type", ""),
                    "http_status": attempt.get("http_status", "")})
        if signal:
            row.update(signal)
        rows.append(row)
        result = apply_outcome_signal(sample["outcome"], signal)
        result.update({"sample_id": sample["sample_id"], "attempt_status": row["attempt_status"],
                       "openai_called": i in attempts})
        outcomes.append(result)
    atomic_text(DECISIONS_FILE, csv_text(rows))
    atomic_text(OUTCOMES_FILE, csv_text(outcomes))
    report = build_paired_report(plan, attempts)
    atomic_text(REPORT_FILE, report)
    print("\n" + report)
    log(f"Saved report: {REPORT_FILE}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true", help="Freeze samples; no OpenAI calls (default).")
    mode.add_argument("--run", action="store_true", help="Paid API pilot on the saved samples.")
    mode.add_argument("--report", action="store_true", help="Local report only; no MT5 or API calls.")
    args = parser.parse_args()
    store = PilotStore(DB_FILE)
    try:
        if not args.run and not args.report:
            prepare(store)
        elif args.run:
            plan = store.plan()
            if plan is None:
                raise ValueError("Run --prepare first; --run will not sample automatically.")
            remaining = len(plan["samples"]) - len(store.attempts())
            log(f"Frozen pilot | model={plan['spec']['model']} | "
                f"unattempted={remaining} | retries=0 | NO_ORDER_EXECUTION")
            if remaining:
                from dotenv import load_dotenv
                from openai import OpenAI
                load_dotenv(BASE_DIR / ".env")
                key = os.getenv("OPENAI_API_KEY", "").strip()
                if not key:
                    raise ValueError("OPENAI_API_KEY missing; keep it only in local .env.")
                # Official endpoint; no silent fallback or alternative model.
                with OpenAI(api_key=key, base_url="https://api.openai.com/v1",
                            max_retries=0, timeout=45.0) as client:
                    execute_plan(store, client, progress=log)
        export(store)
    except KeyboardInterrupt:
        log("Stopped. Uncertain STARTED attempts will not be retried automatically.")
        if store.plan() is not None:
            export(store)
    finally:
        store.close()


if __name__ == "__main__":
    main()
