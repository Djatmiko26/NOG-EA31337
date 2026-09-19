from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class FilterConfig:
    lookback: int = 12
    min_score: int = 4
    min_atr_ratio: float = 0.00035
    max_spread_atr_ratio: float = 0.12
    breakout_buffer_atr: float = 0.25
    min_ema_gap_atr: float = 0.10
    rsi_bull: float = 52.0
    rsi_bear: float = 48.0


@dataclass(frozen=True)
class FilterDecision:
    qualifies: bool
    direction: str
    bull_score: int
    bear_score: int
    reason: str
    metrics: dict[str, Any]


def _score_rules(rules: dict[str, bool]) -> tuple[int, list[str]]:
    passed = [name for name, value in rules.items() if value]
    return len(passed), passed


def evaluate_setup(
    df: pd.DataFrame,
    *,
    point: float,
    config: FilterConfig,
) -> FilterDecision:
    """Deterministic pre-filter for deciding whether OpenAI should be called.

    This function does NOT create a trading order. It only identifies whether
    the latest closed candle looks sufficiently interesting for deeper analysis.
    """
    required = {
        "open",
        "high",
        "low",
        "close",
        "spread",
        "ema20",
        "ema50",
        "rsi14",
        "atr14",
    }
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Filter input missing columns: {sorted(missing)}")

    if len(df) < config.lookback + 2:
        raise ValueError(
            f"Filter needs at least {config.lookback + 2} rows; got {len(df)}"
        )

    latest = df.iloc[-1]
    previous_candle = df.iloc[-2]
    lookback = df.iloc[-(config.lookback + 1) : -1]

    close = float(latest["close"])
    atr = float(latest["atr14"])
    ema20 = float(latest["ema20"])
    ema50 = float(latest["ema50"])
    rsi = float(latest["rsi14"])
    spread_points = float(latest["spread"])

    if close <= 0 or atr <= 0 or point <= 0:
        return FilterDecision(
            qualifies=False,
            direction="NONE",
            bull_score=0,
            bear_score=0,
            reason="Invalid price/ATR/point value; setup skipped.",
            metrics={},
        )

    previous_high = float(lookback["high"].max())
    previous_low = float(lookback["low"].min())

    atr_ratio = atr / close
    spread_price = spread_points * point
    spread_atr_ratio = spread_price / atr
    ema_gap_atr = abs(ema20 - ema50) / atr
    breakout_buffer = config.breakout_buffer_atr * atr

    near_upper_breakout = close >= previous_high - breakout_buffer
    near_lower_breakout = close <= previous_low + breakout_buffer

    bull_rules = {
        "ema20_above_ema50": ema20 > ema50,
        "close_above_ema20": close > ema20,
        "rsi_bullish": rsi >= config.rsi_bull,
        "near_upper_breakout": near_upper_breakout,
        "positive_last_bar": close > float(previous_candle["close"]),
        "ema_separation": ema_gap_atr >= config.min_ema_gap_atr,
    }
    bear_rules = {
        "ema20_below_ema50": ema20 < ema50,
        "close_below_ema20": close < ema20,
        "rsi_bearish": rsi <= config.rsi_bear,
        "near_lower_breakout": near_lower_breakout,
        "negative_last_bar": close < float(previous_candle["close"]),
        "ema_separation": ema_gap_atr >= config.min_ema_gap_atr,
    }

    bull_score, bull_passed = _score_rules(bull_rules)
    bear_score, bear_passed = _score_rules(bear_rules)

    metrics = {
        "atr_ratio": round(atr_ratio, 8),
        "spread_atr_ratio": round(spread_atr_ratio, 5),
        "ema_gap_atr": round(ema_gap_atr, 5),
        "previous_high": round(previous_high, 5),
        "previous_low": round(previous_low, 5),
        "near_upper_breakout": near_upper_breakout,
        "near_lower_breakout": near_lower_breakout,
        "bull_passed": ",".join(bull_passed),
        "bear_passed": ",".join(bear_passed),
    }

    if atr_ratio < config.min_atr_ratio:
        return FilterDecision(
            qualifies=False,
            direction="NONE",
            bull_score=bull_score,
            bear_score=bear_score,
            reason=(
                "LOCAL_SKIP low volatility: "
                f"ATR/close={atr_ratio:.6f} < {config.min_atr_ratio:.6f}"
            ),
            metrics=metrics,
        )

    if spread_atr_ratio > config.max_spread_atr_ratio:
        return FilterDecision(
            qualifies=False,
            direction="NONE",
            bull_score=bull_score,
            bear_score=bear_score,
            reason=(
                "LOCAL_SKIP spread too large relative to ATR: "
                f"spread/ATR={spread_atr_ratio:.3f} > "
                f"{config.max_spread_atr_ratio:.3f}"
            ),
            metrics=metrics,
        )

    best_score = max(bull_score, bear_score)

    if best_score < config.min_score:
        return FilterDecision(
            qualifies=False,
            direction="NONE",
            bull_score=bull_score,
            bear_score=bear_score,
            reason=(
                "LOCAL_SKIP setup score below threshold: "
                f"bull={bull_score}, bear={bear_score}, "
                f"required={config.min_score}"
            ),
            metrics=metrics,
        )

    if bull_score == bear_score:
        return FilterDecision(
            qualifies=False,
            direction="NONE",
            bull_score=bull_score,
            bear_score=bear_score,
            reason=(
                "LOCAL_SKIP directional scores tied: "
                f"bull={bull_score}, bear={bear_score}"
            ),
            metrics=metrics,
        )

    direction = "BUY" if bull_score > bear_score else "SELL"
    winning_rules = bull_passed if direction == "BUY" else bear_passed

    return FilterDecision(
        qualifies=True,
        direction=direction,
        bull_score=bull_score,
        bear_score=bear_score,
        reason=(
            f"LOCAL_PASS candidate={direction} "
            f"bull={bull_score} bear={bear_score}; "
            f"rules={','.join(winning_rules)}"
        ),
        metrics=metrics,
    )
