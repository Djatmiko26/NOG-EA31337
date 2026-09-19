from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv

import market_monitor as monitor
from historical_replay import (
    ReplayConfig,
    load_history,
    load_replay_config,
    replay_decision_indices,
)
from outcome_labeller import MAX_HORIZON, compute_outcome
from strategy_filter import evaluate_setup


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
REPORT_FILE = DATA_DIR / "filter_validation.md"

SCORES = (4, 5, 6)
HORIZONS = (5, 10, 20)


def log(message: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{now}] {message}", flush=True)


def chronological_split(indices: list[int], development_fraction: float) -> tuple[list[int], list[int]]:
    """Split ordered replay decision indices without shuffling.

    Trading data is time ordered, so validation must occur strictly after the
    development segment. This helper intentionally never randomizes rows.
    """
    if not indices:
        return [], []
    if not 0.5 <= development_fraction <= 0.9:
        raise ValueError("development_fraction harus di antara 0.5 dan 0.9")

    split_at = int(len(indices) * development_fraction)
    split_at = max(1, min(split_at, len(indices) - 1))
    return indices[:split_at], indices[split_at:]


def fmt_pct(value: float | None, digits: int = 3) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:.{digits}f}%"


def summarize(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    series = pd.Series(values, dtype="float64")
    return {
        "n": len(series),
        "mean": float(series.mean()),
        "median": float(series.median()),
        "win_rate": float((series > 0).mean() * 100.0),
    }


def evaluate_score(
    *,
    df: pd.DataFrame,
    indices: list[int],
    base_config: monitor.Config,
    point: float,
    min_score: int,
) -> dict[str, Any]:
    filter_config = replace(base_config.filter_config, min_score=min_score)
    max_context = max(
        base_config.candles_to_ai,
        filter_config.lookback + 2,
        60,
    )

    returns: dict[int, list[float]] = {h: [] for h in HORIZONS}
    mfe_values: list[float] = []
    mae_values: list[float] = []
    qualified = 0
    buys = 0
    sells = 0

    for i in indices:
        start = max(0, i - max_context + 1)
        context = df.iloc[start : i + 1].copy()
        decision = evaluate_setup(context, point=point, config=filter_config)
        if not decision.qualifies:
            continue

        qualified += 1
        if decision.direction == "BUY":
            buys += 1
        elif decision.direction == "SELL":
            sells += 1

        latest = df.iloc[i]
        future = df.iloc[i + 1 : i + 1 + MAX_HORIZON].copy()

        decision_row = pd.Series(
            {
                "candle_epoch_raw": int(latest["server_epoch_raw"]),
                "candle_time_server": latest["server_time"].isoformat(),
                "candle_time_utc": latest["time"].isoformat(),
                "symbol": base_config.symbol,
                "timeframe": base_config.timeframe_name,
                "close": float(latest["close"]),
                "atr14": float(latest["atr14"]),
                "filter_qualifies": True,
                "filter_direction": decision.direction,
                "filter_bull_score": decision.bull_score,
                "filter_bear_score": decision.bear_score,
                "openai_called": False,
                "action": "",
                "confidence": "",
                "market_regime": "",
            }
        )

        outcome = compute_outcome(decision_row, future)
        for horizon in HORIZONS:
            value = outcome[f"filter_return_{horizon}_pct"]
            if value != "":
                returns[horizon].append(float(value))

        mfe = outcome["filter_mfe_20_pct"]
        mae = outcome["filter_mae_20_pct"]
        if mfe != "":
            mfe_values.append(float(mfe))
        if mae != "":
            mae_values.append(float(mae))

    result: dict[str, Any] = {
        "processed": len(indices),
        "qualified": qualified,
        "qualification_rate": (qualified / len(indices) * 100.0) if indices else 0.0,
        "buys": buys,
        "sells": sells,
        "avg_mfe_20": float(pd.Series(mfe_values).mean()) if mfe_values else None,
        "avg_mae_20": float(pd.Series(mae_values).mean()) if mae_values else None,
    }
    for horizon in HORIZONS:
        result[f"h{horizon}"] = summarize(returns[horizon])
    return result


def report_table(rows: list[list[Any]]) -> str:
    headers = [
        "Score",
        "Segment",
        "Processed",
        "Qualified",
        "Qual rate",
        "BUY",
        "SELL",
        "5-bar avg",
        "5-bar win",
        "10-bar avg",
        "10-bar win",
        "20-bar avg",
        "20-bar win",
        "20-bar MFE",
        "20-bar MAE",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join(lines)


def build_report(
    *,
    symbol: str,
    timeframe: str,
    development_fraction: float,
    results: dict[int, dict[str, dict[str, Any]]],
) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    rows: list[list[Any]] = []

    for score in SCORES:
        for segment in ("DEVELOPMENT", "VALIDATION"):
            data = results[score][segment]
            rows.append(
                [
                    score,
                    segment,
                    data["processed"],
                    data["qualified"],
                    fmt_pct(data["qualification_rate"], 1),
                    data["buys"],
                    data["sells"],
                    fmt_pct(data["h5"]["mean"]),
                    fmt_pct(data["h5"]["win_rate"], 1),
                    fmt_pct(data["h10"]["mean"]),
                    fmt_pct(data["h10"]["win_rate"], 1),
                    fmt_pct(data["h20"]["mean"]),
                    fmt_pct(data["h20"]["win_rate"], 1),
                    fmt_pct(data["avg_mfe_20"]),
                    fmt_pct(data["avg_mae_20"]),
                ]
            )

    return "\n".join(
        [
            "# Local Filter Chronological Validation",
            "",
            f"Generated: **{generated}**  ",
            f"Market: **{symbol} {timeframe}**  ",
            f"Chronological development fraction: **{development_fraction:.0%}**",
            "",
            "> Research only. No order execution and no OpenAI calls are made by this script.",
            "",
            "The earlier segment is development data; the later segment is validation data. Rows are never shuffled.",
            "",
            report_table(rows),
            "",
            "## Interpretation rules",
            "",
            "- Do not choose a score from development results alone.",
            "- Prefer settings whose behaviour remains reasonably consistent in the later validation segment.",
            "- Qualification rate matters because the local filter exists partly to reduce OpenAI calls.",
            "- A higher win rate with negative average return is not automatically useful.",
            "- This test does not include trading costs, slippage, SL/TP, position sizing, or live execution.",
            "",
        ]
    )


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    development_fraction = float(os.getenv("FILTER_VALIDATION_DEV_FRACTION", "0.70"))

    base_config = monitor.load_config()
    replay_config: ReplayConfig = load_replay_config(base_config)

    # This experiment is always local-only regardless of REPLAY_OPENAI.
    replay_config = replace(replay_config, openai_enabled=False, max_ai_calls=0)

    log(
        "Filter validation starting | "
        f"symbol={base_config.symbol} | timeframe={base_config.timeframe_name} | "
        f"bars={replay_config.bars} | scores={SCORES} | "
        f"dev_fraction={development_fraction:.2f} | OPENAI_CALLS=DISABLED"
    )

    monitor.connect_mt5(base_config)
    try:
        symbol_info = mt5.symbol_info(base_config.symbol)
        if symbol_info is None or symbol_info.point <= 0:
            raise RuntimeError("Gagal membaca symbol point.")

        df = load_history(base_config, replay_config)
        indices = replay_decision_indices(
            len(df), replay_config.warmup_bars, MAX_HORIZON
        )
        development_indices, validation_indices = chronological_split(
            indices, development_fraction
        )

        log(
            f"History split | total_decisions={len(indices)} | "
            f"development={len(development_indices)} | "
            f"validation={len(validation_indices)}"
        )

        results: dict[int, dict[str, dict[str, Any]]] = {}
        for score in SCORES:
            dev = evaluate_score(
                df=df,
                indices=development_indices,
                base_config=base_config,
                point=float(symbol_info.point),
                min_score=score,
            )
            val = evaluate_score(
                df=df,
                indices=validation_indices,
                base_config=base_config,
                point=float(symbol_info.point),
                min_score=score,
            )
            results[score] = {"DEVELOPMENT": dev, "VALIDATION": val}

            log(
                f"score={score} | dev_qual={dev['qualification_rate']:.1f}% | "
                f"dev_h20_avg={dev['h20']['mean']} | "
                f"val_qual={val['qualification_rate']:.1f}% | "
                f"val_h20_avg={val['h20']['mean']}"
            )

        report = build_report(
            symbol=base_config.symbol,
            timeframe=base_config.timeframe_name,
            development_fraction=development_fraction,
            results=results,
        )

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_FILE.write_text(report, encoding="utf-8")
        print("\n" + report)
        print(f"\nSaved report: {REPORT_FILE}")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
