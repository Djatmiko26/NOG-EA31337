from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

import market_monitor as monitor
from historical_replay import (
    load_history,
    replay_decision_indices,
    select_evenly_spaced_indices,
    ReplayConfig,
)
from outcome_labeller import MAX_HORIZON, OUTCOME_FIELDS, compute_outcome
from research_report import build_research_report
from strategy_filter import evaluate_setup


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DECISIONS_FILE = DATA_DIR / "openai_holdout_decisions.csv"
OUTCOMES_FILE = DATA_DIR / "openai_holdout_outcomes.csv"
REPORT_FILE = DATA_DIR / "openai_holdout_report.md"


def log(message: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{now}] {message}", flush=True)


def load_settings() -> tuple[int, int, float, int]:
    load_dotenv(BASE_DIR / ".env")
    bars = max(1000, int(os.getenv("AI_VALIDATION_BARS", "5000")))
    score = min(6, max(1, int(os.getenv("AI_VALIDATION_SCORE", "5"))))
    dev_fraction = float(os.getenv("AI_VALIDATION_DEV_FRACTION", "0.70"))
    max_calls = max(1, int(os.getenv("AI_VALIDATION_MAX_CALLS", "20")))

    if not 0.50 <= dev_fraction <= 0.90:
        raise RuntimeError("AI_VALIDATION_DEV_FRACTION harus antara 0.50 dan 0.90")

    return bars, score, dev_fraction, max_calls


def split_holdout_indices(indices: list[int], dev_fraction: float) -> tuple[list[int], list[int]]:
    if not indices:
        return [], []
    cut = int(len(indices) * dev_fraction)
    cut = min(max(cut, 1), len(indices) - 1)
    return indices[:cut], indices[cut:]


def build_decision_row(base_config: monitor.Config, latest: pd.Series, decision) -> dict:
    return {
        "logged_at_utc": datetime.now(timezone.utc).isoformat(),
        "candle_epoch_raw": int(latest["server_epoch_raw"]),
        "candle_time_server": latest["server_time"].isoformat(),
        "candle_time_utc": latest["time"].isoformat(),
        "symbol": base_config.symbol,
        "timeframe": base_config.timeframe_name,
        "close": round(float(latest["close"]), 5),
        "ema20": round(float(latest["ema20"]), 5),
        "ema50": round(float(latest["ema50"]), 5),
        "rsi14": round(float(latest["rsi14"]), 5),
        "atr14": round(float(latest["atr14"]), 5),
        "spread": int(latest["spread"]),
        "filter_enabled": True,
        "filter_qualifies": decision.qualifies,
        "filter_direction": decision.direction,
        "filter_bull_score": decision.bull_score,
        "filter_bear_score": decision.bear_score,
        "filter_reason": decision.reason,
        "openai_called": False,
        "action": "",
        "confidence": "",
        "market_regime": "",
        "reason": decision.reason,
        "model": "",
    }


def main() -> None:
    bars, score, dev_fraction, max_calls = load_settings()
    base_config = monitor.load_config()
    filter_config = replace(base_config.filter_config, min_score=score)

    replay_config = ReplayConfig(
        bars=bars,
        warmup_bars=max(200, base_config.candles_to_ai, filter_config.lookback + 2),
        openai_enabled=False,
        max_ai_calls=0,
    )

    log(
        "OpenAI holdout validation starting | "
        f"symbol={base_config.symbol} | timeframe={base_config.timeframe_name} | "
        f"bars={bars} | score={score} | dev_fraction={dev_fraction:.2f} | "
        f"max_ai_calls={max_calls} | ORDER_EXECUTION=DISABLED"
    )

    monitor.connect_mt5(base_config)
    try:
        info = monitor.mt5.symbol_info(base_config.symbol)
        if info is None or info.point <= 0:
            raise RuntimeError("Gagal membaca point symbol dari MT5")
        point = float(info.point)

        df = load_history(base_config, replay_config)
        indices = replay_decision_indices(len(df), replay_config.warmup_bars, MAX_HORIZON)
        _, validation_indices = split_holdout_indices(indices, dev_fraction)

        max_context = max(base_config.candles_to_ai, filter_config.lookback + 2, 60)
        candidates: list[int] = []
        local_decisions: dict[int, object] = {}

        for i in validation_indices:
            start = max(0, i - max_context + 1)
            context = df.iloc[start : i + 1].copy()
            decision = evaluate_setup(context, point=point, config=filter_config)
            local_decisions[i] = decision
            if decision.qualifies:
                candidates.append(i)

        selected = select_evenly_spaced_indices(candidates, max_calls)
        log(
            "Holdout candidates | "
            f"validation_decisions={len(validation_indices)} | "
            f"qualified={len(candidates)} | selected_for_openai={len(selected)}"
        )

        client = OpenAI()
        decision_rows: list[dict] = []
        outcome_rows: list[dict] = []

        for n, i in enumerate(selected, start=1):
            decision = local_decisions[i]
            start = max(0, i - max_context + 1)
            context = df.iloc[start : i + 1].copy()
            latest = df.iloc[i]
            row = build_decision_row(base_config, latest, decision)

            payload = monitor.build_market_payload(base_config, context, decision)
            signal = monitor.ask_openai(client, base_config, payload)

            row["openai_called"] = True
            row["action"] = signal["action"]
            row["confidence"] = int(signal["confidence"])
            row["market_regime"] = signal["market_regime"]
            row["reason"] = signal["reason"]
            row["model"] = base_config.openai_model
            decision_rows.append(row)

            future = df.iloc[i + 1 : i + 1 + MAX_HORIZON].copy()
            outcome_rows.append(compute_outcome(pd.Series(row), future))

            log(
                f"OpenAI holdout {n}/{len(selected)} | "
                f"server_time={latest['server_time'].isoformat()} | "
                f"local={decision.direction} | ai={signal['action']} | "
                f"confidence={signal['confidence']} | regime={signal['market_regime']}"
            )

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        decisions_df = pd.DataFrame(decision_rows)
        outcomes_df = pd.DataFrame(outcome_rows)

        decisions_df.to_csv(DECISIONS_FILE, index=False)
        if outcomes_df.empty:
            pd.DataFrame(columns=OUTCOME_FIELDS).to_csv(OUTCOMES_FILE, index=False)
        else:
            outcomes_df = outcomes_df.reindex(columns=OUTCOME_FIELDS)
            outcomes_df.to_csv(OUTCOMES_FILE, index=False)

        report = build_research_report(
            decisions_df,
            outcomes_df,
            symbol=base_config.symbol,
            timeframe=base_config.timeframe_name,
        )
        REPORT_FILE.write_text(report, encoding="utf-8")

        log(f"Saved: {DECISIONS_FILE}")
        log(f"Saved: {OUTCOMES_FILE}")
        log(f"Saved: {REPORT_FILE}")
        print("\n" + report)
    finally:
        monitor.mt5.shutdown()


if __name__ == "__main__":
    main()
