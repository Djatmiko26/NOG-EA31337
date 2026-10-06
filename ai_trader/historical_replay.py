from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

import market_monitor as monitor
from outcome_labeller import MAX_HORIZON, OUTCOME_FIELDS, compute_outcome
from research_report import build_research_report
from strategy_filter import FilterDecision, evaluate_setup


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
REPLAY_DECISIONS_FILE = DATA_DIR / "replay_decisions.csv"
REPLAY_OUTCOMES_FILE = DATA_DIR / "replay_outcomes.csv"
REPLAY_REPORT_FILE = DATA_DIR / "replay_report.md"


@dataclass(frozen=True)
class ReplayConfig:
    bars: int
    warmup_bars: int
    openai_enabled: bool
    max_ai_calls: int


def log(message: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{now}] {message}", flush=True)


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} harus true/false, yes/no, on/off, atau 1/0.")


def load_replay_config(base_config: monitor.Config) -> ReplayConfig:
    load_dotenv(BASE_DIR / ".env")

    bars = max(300, int(os.getenv("REPLAY_BARS", "5000")))
    minimum_warmup = max(
        60,
        base_config.candles_to_ai,
        base_config.filter_config.lookback + 2,
    )
    warmup_bars = max(
        minimum_warmup,
        int(os.getenv("REPLAY_WARMUP_BARS", "200")),
    )
    openai_enabled = env_bool("REPLAY_OPENAI", False)
    max_ai_calls = max(0, int(os.getenv("REPLAY_MAX_AI_CALLS", "20")))

    if openai_enabled and max_ai_calls <= 0:
        raise RuntimeError(
            "REPLAY_OPENAI=true tetapi REPLAY_MAX_AI_CALLS <= 0. "
            "Tetapkan batas biaya yang eksplisit."
        )

    if bars <= warmup_bars + MAX_HORIZON:
        raise RuntimeError(
            "REPLAY_BARS terlalu kecil. Harus lebih besar dari "
            "REPLAY_WARMUP_BARS + 20 future bars."
        )

    return ReplayConfig(
        bars=bars,
        warmup_bars=warmup_bars,
        openai_enabled=openai_enabled,
        max_ai_calls=max_ai_calls,
    )


def replay_decision_indices(
    total_bars: int,
    warmup_bars: int,
    max_horizon: int = MAX_HORIZON,
) -> list[int]:
    """Return indices that have both enough past context and future labels.

    Index i may only use rows <= i to make a decision. Rows i+1..i+max_horizon
    are reserved strictly for outcome labelling.
    """
    if total_bars <= warmup_bars + max_horizon:
        return []
    return list(range(warmup_bars, total_bars - max_horizon))


def select_evenly_spaced_indices(values: list[int], max_items: int) -> list[int]:
    """Deterministically sample candidates across the whole replay period."""
    if max_items <= 0 or not values:
        return []
    if len(values) <= max_items:
        return list(values)
    if max_items == 1:
        return [values[len(values) // 2]]

    last = len(values) - 1
    selected_positions = [round(i * last / (max_items - 1)) for i in range(max_items)]

    result: list[int] = []
    seen: set[int] = set()
    for position in selected_positions:
        value = values[position]
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def load_history(
    base_config: monitor.Config,
    replay_config: ReplayConfig,
) -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(
        base_config.symbol,
        base_config.timeframe,
        1,  # only CLOSED bars
        replay_config.bars,
    )
    if rates is None:
        raise RuntimeError(f"Gagal mengambil replay history: {mt5.last_error()}")

    df = pd.DataFrame(rates)
    if len(df) <= replay_config.warmup_bars + MAX_HORIZON:
        raise RuntimeError(
            f"History yang tersedia hanya {len(df)} bars; tidak cukup untuk replay."
        )

    df["server_epoch_raw"] = df["time"].astype("int64")
    df["server_time"] = pd.to_datetime(df["server_epoch_raw"], unit="s")
    df["time"] = pd.to_datetime(
        df["server_epoch_raw"] - monitor.SERVER_UTC_OFFSET_SECONDS,
        unit="s",
        utc=True,
    )
    df = df.sort_values("server_epoch_raw").reset_index(drop=True)
    return monitor.add_indicators(df)


def build_decision_row(
    base_config: monitor.Config,
    latest: pd.Series,
    filter_decision: FilterDecision,
) -> dict[str, Any]:
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
        "filter_enabled": base_config.filter_enabled,
        "filter_qualifies": filter_decision.qualifies,
        "filter_direction": filter_decision.direction,
        "filter_bull_score": filter_decision.bull_score,
        "filter_bear_score": filter_decision.bear_score,
        "filter_reason": filter_decision.reason,
        "openai_called": False,
        "action": "",
        "confidence": "",
        "market_regime": "",
        "reason": filter_decision.reason,
        "model": "",
    }


def apply_signal_to_row(
    row: dict[str, Any],
    signal: dict[str, Any],
    model: str,
) -> None:
    row["openai_called"] = True
    row["action"] = signal["action"]
    row["confidence"] = int(signal["confidence"])
    row["market_regime"] = signal["market_regime"]
    row["reason"] = signal["reason"]
    row["model"] = model


def run_replay(
    base_config: monitor.Config,
    replay_config: ReplayConfig,
    df: pd.DataFrame,
    *,
    point: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    indices = replay_decision_indices(
        len(df),
        replay_config.warmup_bars,
        MAX_HORIZON,
    )
    if not indices:
        raise RuntimeError("Tidak ada replay decision index yang valid.")

    max_context = max(
        base_config.candles_to_ai,
        base_config.filter_config.lookback + 2,
        60,
    )

    records: list[dict[str, Any]] = []
    filter_by_index: dict[int, FilterDecision] = {}
    row_position_by_index: dict[int, int] = {}
    qualified_indices: list[int] = []

    for count, i in enumerate(indices, start=1):
        start = max(0, i - max_context + 1)
        context = df.iloc[start : i + 1].copy()
        filter_decision = evaluate_setup(
            context,
            point=point,
            config=base_config.filter_config,
        )

        row_position_by_index[i] = len(records)
        filter_by_index[i] = filter_decision
        records.append(
            build_decision_row(
                base_config,
                df.iloc[i],
                filter_decision,
            )
        )

        if filter_decision.qualifies:
            qualified_indices.append(i)

        if count % 1000 == 0:
            log(
                f"Replay local progress | processed={count}/{len(indices)} | "
                f"qualified={len(qualified_indices)}"
            )

    selected_for_ai: list[int] = []
    if replay_config.openai_enabled and qualified_indices:
        selected_for_ai = select_evenly_spaced_indices(
            qualified_indices,
            replay_config.max_ai_calls,
        )
        client = OpenAI()

        log(
            "Historical OpenAI sampling enabled | "
            f"qualified={len(qualified_indices)} | selected={len(selected_for_ai)} | "
            f"hard_cap={replay_config.max_ai_calls}"
        )

        for call_number, i in enumerate(selected_for_ai, start=1):
            start = max(0, i - max_context + 1)
            context = df.iloc[start : i + 1].copy()
            filter_decision = filter_by_index[i]
            row = records[row_position_by_index[i]]

            try:
                payload = monitor.build_market_payload(
                    base_config,
                    context,
                    filter_decision,
                )
                signal = monitor.ask_openai(client, base_config, payload)
                apply_signal_to_row(row, signal, base_config.openai_model)
                log(
                    f"Replay OpenAI {call_number}/{len(selected_for_ai)} | "
                    f"time={row['candle_time_utc']} | candidate={row['filter_direction']} | "
                    f"action={row['action']} | confidence={row['confidence']}"
                )
            except Exception as exc:
                # Preserve the batch instead of losing thousands of local decisions.
                row["openai_called"] = True
                row["model"] = base_config.openai_model
                row["reason"] = f"REPLAY_OPENAI_ERROR: {exc}"
                log(
                    f"Replay OpenAI error {call_number}/{len(selected_for_ai)} | "
                    f"time={row['candle_time_utc']} | error={exc}"
                )

    decisions = pd.DataFrame(records, columns=monitor.DECISION_FIELDS)

    outcomes: list[dict[str, Any]] = []
    for i in indices:
        row = records[row_position_by_index[i]]
        future = df.iloc[i + 1 : i + 1 + MAX_HORIZON].copy()
        outcome = compute_outcome(pd.Series(row), future)
        outcomes.append(outcome)

    outcome_df = pd.DataFrame(outcomes, columns=OUTCOME_FIELDS)
    return decisions, outcome_df


def write_outputs(
    base_config: monitor.Config,
    decisions: pd.DataFrame,
    outcomes: pd.DataFrame,
) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    decisions.to_csv(REPLAY_DECISIONS_FILE, index=False)
    outcomes.to_csv(REPLAY_OUTCOMES_FILE, index=False)

    report = build_research_report(
        decisions,
        outcomes,
        symbol=base_config.symbol,
        timeframe=base_config.timeframe_name,
    )
    REPLAY_REPORT_FILE.write_text(report + "\n", encoding="utf-8")


def main() -> None:
    base_config = monitor.load_config()
    replay_config = load_replay_config(base_config)

    log(
        "Historical replay starting | "
        f"symbol={base_config.symbol} | timeframe={base_config.timeframe_name} | "
        f"bars={replay_config.bars} | warmup={replay_config.warmup_bars} | "
        f"openai={replay_config.openai_enabled} | "
        f"max_ai_calls={replay_config.max_ai_calls if replay_config.openai_enabled else 0} | "
        "ORDER_EXECUTION=DISABLED"
    )

    monitor.connect_mt5(base_config)

    try:
        symbol_info = mt5.symbol_info(base_config.symbol)
        if symbol_info is None or symbol_info.point <= 0:
            raise RuntimeError(
                f"Gagal membaca point {base_config.symbol}: {mt5.last_error()}"
            )

        df = load_history(base_config, replay_config)
        log(
            f"History loaded | closed_bars={len(df)} | "
            f"first_server={df.iloc[0]['server_time'].isoformat()} | "
            f"last_server={df.iloc[-1]['server_time'].isoformat()}"
        )

        decisions, outcomes = run_replay(
            base_config,
            replay_config,
            df,
            point=float(symbol_info.point),
        )
        write_outputs(base_config, decisions, outcomes)

        qualified = decisions["filter_qualifies"].astype(bool)
        called = decisions["openai_called"].astype(bool)

        log(
            "Historical replay complete | "
            f"decisions={len(decisions)} | "
            f"qualified={int(qualified.sum())} | "
            f"openai_calls={int(called.sum())} | "
            f"mature_outcomes={len(outcomes)}"
        )
        log(f"Saved: {REPLAY_DECISIONS_FILE}")
        log(f"Saved: {REPLAY_OUTCOMES_FILE}")
        log(f"Saved: {REPLAY_REPORT_FILE}")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
