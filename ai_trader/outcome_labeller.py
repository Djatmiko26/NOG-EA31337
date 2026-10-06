from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DECISIONS_FILE = DATA_DIR / "decisions.csv"
OUTCOMES_FILE = DATA_DIR / "outcomes.csv"

HORIZONS = (5, 10, 20)
MAX_HORIZON = max(HORIZONS)

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
}

TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 5 * 60,
    "M15": 15 * 60,
    "M30": 30 * 60,
    "H1": 60 * 60,
    "H4": 4 * 60 * 60,
}


OUTCOME_FIELDS = [
    "labelled_at_utc",
    "candle_epoch_raw",
    "candle_time_server",
    "candle_time_utc",
    "symbol",
    "timeframe",
    "entry_close",
    "entry_atr14",
    "filter_qualifies",
    "filter_direction",
    "filter_bull_score",
    "filter_bear_score",
    "openai_called",
    "action",
    "confidence",
    "market_regime",
    "close_5",
    "close_10",
    "close_20",
    "market_return_5_pct",
    "market_return_10_pct",
    "market_return_20_pct",
    "filter_return_5_pct",
    "filter_return_10_pct",
    "filter_return_20_pct",
    "openai_return_5_pct",
    "openai_return_10_pct",
    "openai_return_20_pct",
    "future_high_20",
    "future_low_20",
    "market_mfe_long_20_pct",
    "market_mae_long_20_pct",
    "filter_mfe_20_pct",
    "filter_mae_20_pct",
    "openai_mfe_20_pct",
    "openai_mae_20_pct",
]


def log(message: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{now}] {message}", flush=True)


def clean_str(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def clean_float(value: Any) -> float | None:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    return float(value)


def clean_int(value: Any) -> int | None:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    return int(float(value))


def clean_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or pd.isna(value):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def pct_change(entry: float, exit_price: float) -> float:
    if entry <= 0:
        raise ValueError("entry price harus > 0")
    return ((exit_price - entry) / entry) * 100.0


def directional_return_pct(
    entry: float,
    exit_price: float,
    direction: str,
) -> float | None:
    direction = direction.strip().upper()
    raw = pct_change(entry, exit_price)

    if direction == "BUY":
        return raw
    if direction == "SELL":
        return -raw
    return None


def directional_excursions_pct(
    entry: float,
    future_bars: pd.DataFrame,
    direction: str,
) -> tuple[float | None, float | None]:
    direction = direction.strip().upper()
    if direction not in {"BUY", "SELL"}:
        return None, None

    if future_bars.empty:
        return None, None

    highest = float(future_bars["high"].max())
    lowest = float(future_bars["low"].min())

    if direction == "BUY":
        favorable = pct_change(entry, highest)
        adverse = pct_change(entry, lowest)
    else:
        favorable = -pct_change(entry, lowest)
        adverse = -pct_change(entry, highest)

    # By definition MFE cannot be negative and MAE cannot be positive.
    return max(0.0, favorable), min(0.0, adverse)


def load_settings() -> tuple[str, str, str, int]:
    load_dotenv(BASE_DIR / ".env")

    mt5_path = os.getenv(
        "MT5_PATH",
        r"C:\Program Files\MetaTrader 5\terminal64.exe",
    ).strip()
    symbol = os.getenv("SYMBOL", "XAUUSD").strip()
    timeframe_name = os.getenv("TIMEFRAME", "M5").strip().upper()

    if timeframe_name not in TIMEFRAME_MAP:
        raise RuntimeError(
            f"TIMEFRAME {timeframe_name!r} belum didukung. "
            f"Pilihan: {', '.join(TIMEFRAME_MAP)}"
        )

    return mt5_path, symbol, timeframe_name, TIMEFRAME_MAP[timeframe_name]


def connect_mt5(mt5_path: str, symbol: str) -> None:
    if not mt5.initialize(mt5_path):
        raise RuntimeError(f"MT5 initialize gagal: {mt5.last_error()}")

    terminal = mt5.terminal_info()
    if terminal is None or not terminal.connected:
        mt5.shutdown()
        raise RuntimeError(f"Terminal MT5 tidak terkoneksi: {mt5.last_error()}")

    if not mt5.symbol_select(symbol, True):
        mt5.shutdown()
        raise RuntimeError(f"Symbol {symbol} tidak ditemukan: {mt5.last_error()}")


def latest_closed_epoch(symbol: str, timeframe: int) -> int:
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, 1)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Gagal membaca candle tertutup terbaru: {mt5.last_error()}")
    return int(rates[0]["time"])


def load_decisions(symbol: str, timeframe_name: str) -> pd.DataFrame:
    if not DECISIONS_FILE.exists():
        raise RuntimeError(
            f"{DECISIONS_FILE} belum ada. Jalankan market_monitor.py terlebih dahulu."
        )

    decisions = pd.read_csv(DECISIONS_FILE)
    if decisions.empty:
        return decisions

    required = {
        "candle_epoch_raw",
        "symbol",
        "timeframe",
        "close",
        "atr14",
        "filter_qualifies",
        "filter_direction",
        "filter_bull_score",
        "filter_bear_score",
        "openai_called",
        "action",
        "confidence",
        "market_regime",
    }
    missing = required.difference(decisions.columns)
    if missing:
        raise RuntimeError(
            "decisions.csv belum memakai schema V3. Kolom hilang: "
            + ", ".join(sorted(missing))
        )

    subset = decisions[
        (decisions["symbol"] == symbol)
        & (decisions["timeframe"] == timeframe_name)
    ].copy()

    subset["candle_epoch_raw"] = subset["candle_epoch_raw"].astype("int64")
    subset = subset.drop_duplicates(subset=["candle_epoch_raw"], keep="last")
    return subset.sort_values("candle_epoch_raw").reset_index(drop=True)


def load_labelled_epochs(symbol: str, timeframe_name: str) -> set[int]:
    if not OUTCOMES_FILE.exists():
        return set()

    try:
        outcomes = pd.read_csv(OUTCOMES_FILE)
        if outcomes.empty:
            return set()
        subset = outcomes[
            (outcomes["symbol"] == symbol)
            & (outcomes["timeframe"] == timeframe_name)
        ]
        return {int(value) for value in subset["candle_epoch_raw"].tolist()}
    except Exception as exc:
        raise RuntimeError(f"Gagal membaca outcomes.csv: {exc}") from exc


def load_history_window(
    symbol: str,
    timeframe: int,
    timeframe_name: str,
    earliest_decision_epoch: int,
    latest_closed: int,
) -> pd.DataFrame:
    # Request from just after the earliest decision. The generous end padding
    # is useful around weekends; only CLOSED bars <= latest_closed are retained.
    start = datetime.fromtimestamp(earliest_decision_epoch + 1, tz=timezone.utc)
    padding_seconds = max(
        3 * 24 * 60 * 60,
        TIMEFRAME_SECONDS[timeframe_name] * MAX_HORIZON * 6,
    )
    end = datetime.fromtimestamp(latest_closed, tz=timezone.utc) + timedelta(
        seconds=padding_seconds
    )

    rates = mt5.copy_rates_range(symbol, timeframe, start, end)
    if rates is None:
        raise RuntimeError(f"Gagal mengambil history MT5: {mt5.last_error()}")

    history = pd.DataFrame(rates)
    if history.empty:
        return history

    history = history[history["time"] <= latest_closed].copy()
    history = history.sort_values("time").drop_duplicates(subset=["time"])
    return history.reset_index(drop=True)


def compute_outcome(
    decision: pd.Series,
    future_bars: pd.DataFrame,
) -> dict[str, Any]:
    if len(future_bars) < MAX_HORIZON:
        raise ValueError(
            f"Butuh {MAX_HORIZON} future closed bars, hanya ada {len(future_bars)}."
        )

    future = future_bars.iloc[:MAX_HORIZON].copy()
    entry = float(decision["close"])
    filter_direction = clean_str(decision.get("filter_direction", "")).upper()
    action = clean_str(decision.get("action", "")).upper()

    closes = {
        horizon: float(future.iloc[horizon - 1]["close"])
        for horizon in HORIZONS
    }

    market_returns = {
        horizon: pct_change(entry, close)
        for horizon, close in closes.items()
    }
    filter_returns = {
        horizon: directional_return_pct(entry, close, filter_direction)
        for horizon, close in closes.items()
    }
    openai_returns = {
        horizon: directional_return_pct(entry, close, action)
        for horizon, close in closes.items()
    }

    market_mfe, market_mae = directional_excursions_pct(entry, future, "BUY")
    filter_mfe, filter_mae = directional_excursions_pct(
        entry,
        future,
        filter_direction,
    )
    openai_mfe, openai_mae = directional_excursions_pct(entry, future, action)

    result: dict[str, Any] = {
        "labelled_at_utc": datetime.now(timezone.utc).isoformat(),
        "candle_epoch_raw": int(decision["candle_epoch_raw"]),
        "candle_time_server": clean_str(decision.get("candle_time_server", "")),
        "candle_time_utc": clean_str(decision.get("candle_time_utc", "")),
        "symbol": clean_str(decision["symbol"]),
        "timeframe": clean_str(decision["timeframe"]),
        "entry_close": round(entry, 5),
        "entry_atr14": clean_float(decision.get("atr14")),
        "filter_qualifies": clean_bool(decision.get("filter_qualifies")),
        "filter_direction": filter_direction,
        "filter_bull_score": clean_int(decision.get("filter_bull_score")),
        "filter_bear_score": clean_int(decision.get("filter_bear_score")),
        "openai_called": clean_bool(decision.get("openai_called")),
        "action": action,
        "confidence": clean_int(decision.get("confidence")),
        "market_regime": clean_str(decision.get("market_regime", "")),
        "future_high_20": round(float(future["high"].max()), 5),
        "future_low_20": round(float(future["low"].min()), 5),
        "market_mfe_long_20_pct": round(float(market_mfe), 6),
        "market_mae_long_20_pct": round(float(market_mae), 6),
        "filter_mfe_20_pct": "" if filter_mfe is None else round(filter_mfe, 6),
        "filter_mae_20_pct": "" if filter_mae is None else round(filter_mae, 6),
        "openai_mfe_20_pct": "" if openai_mfe is None else round(openai_mfe, 6),
        "openai_mae_20_pct": "" if openai_mae is None else round(openai_mae, 6),
    }

    for horizon in HORIZONS:
        result[f"close_{horizon}"] = round(closes[horizon], 5)
        result[f"market_return_{horizon}_pct"] = round(
            market_returns[horizon],
            6,
        )

        filter_value = filter_returns[horizon]
        result[f"filter_return_{horizon}_pct"] = (
            "" if filter_value is None else round(filter_value, 6)
        )

        ai_value = openai_returns[horizon]
        result[f"openai_return_{horizon}_pct"] = (
            "" if ai_value is None else round(ai_value, 6)
        )

    return result


def validate_outcome_schema() -> None:
    if not OUTCOMES_FILE.exists() or OUTCOMES_FILE.stat().st_size == 0:
        return

    with OUTCOMES_FILE.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])

    if header != OUTCOME_FIELDS:
        raise RuntimeError(
            "Schema outcomes.csv berbeda dengan V4 saat ini. "
            "Pindahkan/hapus outcomes.csv lama sebelum melanjutkan."
        )


def append_outcome(row: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    validate_outcome_schema()
    exists = OUTCOMES_FILE.exists() and OUTCOMES_FILE.stat().st_size > 0

    with OUTCOMES_FILE.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTCOME_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in OUTCOME_FIELDS})


def main() -> None:
    mt5_path, symbol, timeframe_name, timeframe = load_settings()

    decisions = load_decisions(symbol, timeframe_name)
    if decisions.empty:
        log("Tidak ada decision untuk dilabeli.")
        return

    labelled = load_labelled_epochs(symbol, timeframe_name)
    pending = decisions[~decisions["candle_epoch_raw"].isin(labelled)].copy()

    if pending.empty:
        log("Semua decision yang tersedia sudah memiliki outcome label.")
        return

    connect_mt5(mt5_path, symbol)

    try:
        latest_closed = latest_closed_epoch(symbol, timeframe)
        earliest = int(pending.iloc[0]["candle_epoch_raw"])
        history = load_history_window(
            symbol,
            timeframe,
            timeframe_name,
            earliest,
            latest_closed,
        )

        if history.empty:
            log("Belum ada future candle history yang tersedia.")
            return

        appended = 0
        immature = 0

        for _, decision in pending.iterrows():
            epoch = int(decision["candle_epoch_raw"])
            future = history[history["time"] > epoch]

            if len(future) < MAX_HORIZON:
                immature += 1
                continue

            outcome = compute_outcome(decision, future)
            append_outcome(outcome)
            appended += 1

        log(
            "Outcome labelling complete | "
            f"new_labels={appended} | pending_for_20_bars={immature} | "
            f"already_labelled={len(labelled)} | file={OUTCOMES_FILE}"
        )

        if appended == 0 and immature > 0:
            log(
                "Normal: decision terbaru belum memiliki 20 candle tertutup "
                "sesudahnya. Jalankan outcome_labeller.py lagi nanti."
            )

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
