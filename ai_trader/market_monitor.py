from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

from strategy_filter import FilterConfig, FilterDecision, evaluate_setup


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
SIGNALS_FILE = DATA_DIR / "signals.csv"
DECISIONS_FILE = DATA_DIR / "decisions.csv"
LOG_FILE = LOG_DIR / "market_monitor.log"

# MT5 brokers can expose bar timestamps aligned to broker/server wall-clock
# rather than real UTC. We keep the original epoch for candle identity and
# derive normalized UTC for research/session analysis.
SERVER_UTC_OFFSET_SECONDS = 0


TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
}


@dataclass(frozen=True)
class Config:
    mt5_path: str
    symbol: str
    timeframe_name: str
    timeframe: int
    poll_seconds: int
    candles_to_load: int
    candles_to_ai: int
    openai_model: str
    filter_enabled: bool
    filter_config: FilterConfig


def log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] {message}"
    print(line, flush=True)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False

    raise RuntimeError(
        f"{name} harus berupa true/false, yes/no, on/off, atau 1/0."
    )


def load_config() -> Config:
    load_dotenv(BASE_DIR / ".env")

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY tidak ditemukan. Copy .env.example menjadi .env "
            "lalu isi API key Anda."
        )

    timeframe_name = os.getenv("TIMEFRAME", "M5").strip().upper()
    if timeframe_name not in TIMEFRAME_MAP:
        raise RuntimeError(
            f"TIMEFRAME {timeframe_name!r} belum didukung. "
            f"Pilihan: {', '.join(TIMEFRAME_MAP)}"
        )

    mt5_path = os.getenv(
        "MT5_PATH",
        r"C:\Program Files\MetaTrader 5\terminal64.exe",
    ).strip()

    symbol = os.getenv("SYMBOL", "XAUUSD").strip()
    poll_seconds = max(1, int(os.getenv("POLL_SECONDS", "5")))
    candles_to_load = max(60, int(os.getenv("CANDLES_TO_LOAD", "120")))
    candles_to_ai = max(10, int(os.getenv("CANDLES_TO_AI", "30")))

    if candles_to_ai > candles_to_load:
        raise RuntimeError("CANDLES_TO_AI tidak boleh melebihi CANDLES_TO_LOAD.")

    filter_config = FilterConfig(
        lookback=max(5, int(os.getenv("FILTER_LOOKBACK", "12"))),
        min_score=max(1, int(os.getenv("FILTER_MIN_SCORE", "4"))),
        min_atr_ratio=max(
            0.0,
            float(os.getenv("FILTER_MIN_ATR_RATIO", "0.00035")),
        ),
        max_spread_atr_ratio=max(
            0.0,
            float(os.getenv("FILTER_MAX_SPREAD_ATR_RATIO", "0.12")),
        ),
        breakout_buffer_atr=max(
            0.0,
            float(os.getenv("FILTER_BREAKOUT_BUFFER_ATR", "0.25")),
        ),
        min_ema_gap_atr=max(
            0.0,
            float(os.getenv("FILTER_MIN_EMA_GAP_ATR", "0.10")),
        ),
        rsi_bull=float(os.getenv("FILTER_RSI_BULL", "52")),
        rsi_bear=float(os.getenv("FILTER_RSI_BEAR", "48")),
    )

    if filter_config.min_score > 6:
        raise RuntimeError("FILTER_MIN_SCORE maksimum 6.")
    if filter_config.rsi_bear >= filter_config.rsi_bull:
        raise RuntimeError("FILTER_RSI_BEAR harus lebih kecil dari FILTER_RSI_BULL.")

    return Config(
        mt5_path=mt5_path,
        symbol=symbol,
        timeframe_name=timeframe_name,
        timeframe=TIMEFRAME_MAP[timeframe_name],
        poll_seconds=poll_seconds,
        candles_to_load=candles_to_load,
        candles_to_ai=candles_to_ai,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna").strip(),
        filter_enabled=env_bool("FILTER_ENABLED", True),
        filter_config=filter_config,
    )


def detect_server_utc_offset_seconds(config: Config) -> int:
    """Estimate broker/server clock offset relative to real UTC."""
    tick = mt5.symbol_info_tick(config.symbol)
    if tick is None or int(tick.time) <= 0:
        log("WARNING: tick time unavailable; assuming MT5 server offset=0h.")
        return 0

    delta = int(tick.time) - int(time.time())

    # Broker server offsets are normally hour/half-hour aligned. Rounding
    # prevents network/processing seconds from polluting the estimate.
    half_hour = 30 * 60
    rounded = int(round(delta / half_hour) * half_hour)

    if abs(rounded) > 14 * 60 * 60:
        log(
            "WARNING: detected MT5 clock offset outside +/-14h; "
            "assuming offset=0h."
        )
        return 0

    return rounded


def connect_mt5(config: Config) -> None:
    if not mt5.initialize(config.mt5_path):
        raise RuntimeError(f"MT5 initialize gagal: {mt5.last_error()}")

    terminal = mt5.terminal_info()
    if terminal is None or not terminal.connected:
        mt5.shutdown()
        raise RuntimeError(f"Terminal MT5 tidak terkoneksi: {mt5.last_error()}")

    if not mt5.symbol_select(config.symbol, True):
        mt5.shutdown()
        raise RuntimeError(
            f"Symbol {config.symbol} tidak ditemukan. "
            "Periksa nama symbol di Market Watch."
        )

    symbol_info = mt5.symbol_info(config.symbol)
    if symbol_info is None or symbol_info.point <= 0:
        mt5.shutdown()
        raise RuntimeError(
            f"Gagal membaca specification symbol {config.symbol}: {mt5.last_error()}"
        )

    global SERVER_UTC_OFFSET_SECONDS
    SERVER_UTC_OFFSET_SECONDS = detect_server_utc_offset_seconds(config)

    # Monitor ini sengaja READ-ONLY. trade_allowed tidak dibutuhkan.
    log(
        f"MT5 connected | symbol={config.symbol} | "
        f"timeframe={config.timeframe_name} | "
        f"point={symbol_info.point} | "
        f"detected_server_utc_offset={SERVER_UTC_OFFSET_SECONDS / 3600:+.1f}h | "
        "trade_execution=DISABLED_BY_DESIGN"
    )


def get_latest_closed_candle_epoch_raw(config: Config) -> int:
    rates = mt5.copy_rates_from_pos(
        config.symbol,
        config.timeframe,
        1,  # 0 = candle berjalan; 1 = candle terakhir yang sudah tutup
        1,
    )
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Gagal membaca candle terakhir: {mt5.last_error()}")

    return int(rates[0]["time"])


def load_closed_candles(config: Config) -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(
        config.symbol,
        config.timeframe,
        1,
        config.candles_to_load,
    )

    if rates is None or len(rates) < 60:
        raise RuntimeError(
            f"Data candle tidak cukup ({0 if rates is None else len(rates)} bar). "
            f"MT5 error: {mt5.last_error()}"
        )

    df = pd.DataFrame(rates)
    df["server_epoch_raw"] = df["time"].astype("int64")

    # server_time preserves the wall-clock value exposed by MT5.
    df["server_time"] = pd.to_datetime(df["server_epoch_raw"], unit="s")

    # time is normalized UTC for research and future session filters.
    df["time"] = pd.to_datetime(
        df["server_epoch_raw"] - SERVER_UTC_OFFSET_SECONDS,
        unit="s",
        utc=True,
    )

    df = df.sort_values("server_epoch_raw").reset_index(drop=True)
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()

    result["ema20"] = result["close"].ewm(span=20, adjust=False).mean()
    result["ema50"] = result["close"].ewm(span=50, adjust=False).mean()

    delta = result["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    result["rsi14"] = 100 - (100 / (1 + rs))
    result["rsi14"] = result["rsi14"].fillna(100.0)

    previous_close = result["close"].shift(1)
    tr = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - previous_close).abs(),
            (result["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    result["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return result


def build_market_payload(
    config: Config,
    df: pd.DataFrame,
    filter_decision: FilterDecision,
) -> dict[str, Any]:
    ai_df = df.tail(config.candles_to_ai).copy()
    ai_df["time"] = ai_df["time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    columns = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
        "spread",
        "ema20",
        "ema50",
        "rsi14",
        "atr14",
    ]

    ai_df = ai_df[columns].round(5)

    return {
        "symbol": config.symbol,
        "timeframe": config.timeframe_name,
        "latest_closed_candle_utc": ai_df.iloc[-1]["time"],
        "detected_server_utc_offset_hours": SERVER_UTC_OFFSET_SECONDS / 3600,
        "local_filter": {
            "candidate_direction": filter_decision.direction,
            "bull_score": filter_decision.bull_score,
            "bear_score": filter_decision.bear_score,
            "reason": filter_decision.reason,
            "metrics": filter_decision.metrics,
        },
        "candles": ai_df.to_dict(orient="records"),
    }


def ask_openai(
    client: OpenAI,
    config: Config,
    market_data: dict[str, Any],
) -> dict[str, Any]:
    response = client.responses.create(
        model=config.openai_model,
        instructions=(
            "You are a market-analysis component inside an experimental "
            "trading research system. Analyze ONLY the supplied OHLC market "
            "data and indicators. The final row is the latest CLOSED candle. "
            "The local_filter block is a deterministic pre-filter and is only "
            "context; independently verify the setup from the supplied data. "
            "Do not assume knowledge of prices after that candle. Do not use "
            "outside market information. Do not invent news or economic events. "
            "BUY means the supplied data currently shows a bullish setup. "
            "SELL means the supplied data currently shows a bearish setup. "
            "WAIT means there is no sufficiently clear setup. Prefer WAIT when "
            "evidence conflicts or is weak. Confidence describes confidence in "
            "the classification, NOT probability of profit."
        ),
        input=json.dumps(market_data, ensure_ascii=False),
        text={
            "format": {
                "type": "json_schema",
                "name": "market_signal",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["BUY", "SELL", "WAIT"],
                        },
                        "confidence": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                        },
                        "market_regime": {
                            "type": "string",
                            "enum": [
                                "BULL_TREND",
                                "BEAR_TREND",
                                "RANGE",
                                "VOLATILE",
                                "UNCLEAR",
                            ],
                        },
                        "reason": {"type": "string"},
                    },
                    "required": [
                        "action",
                        "confidence",
                        "market_regime",
                        "reason",
                    ],
                    "additionalProperties": False,
                },
            }
        },
    )

    signal = json.loads(response.output_text)

    if signal["action"] not in {"BUY", "SELL", "WAIT"}:
        raise RuntimeError(f"Action OpenAI tidak valid: {signal['action']}")

    return signal


# Keep the V2 signals.csv schema backward compatible. V3 filter metadata is
# recorded in decisions.csv.
SIGNAL_FIELDS = [
    "logged_at_utc",
    "candle_time_utc",
    "symbol",
    "timeframe",
    "close",
    "ema20",
    "ema50",
    "rsi14",
    "atr14",
    "spread",
    "action",
    "confidence",
    "market_regime",
    "reason",
    "model",
]


DECISION_FIELDS = [
    "logged_at_utc",
    "candle_epoch_raw",
    "candle_time_server",
    "candle_time_utc",
    "symbol",
    "timeframe",
    "close",
    "ema20",
    "ema50",
    "rsi14",
    "atr14",
    "spread",
    "filter_enabled",
    "filter_qualifies",
    "filter_direction",
    "filter_bull_score",
    "filter_bear_score",
    "filter_reason",
    "openai_called",
    "action",
    "confidence",
    "market_regime",
    "reason",
    "model",
]


def append_signal(
    config: Config,
    latest: pd.Series,
    signal: dict[str, Any],
) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    exists = SIGNALS_FILE.exists()

    row = {
        "logged_at_utc": datetime.now(timezone.utc).isoformat(),
        "candle_time_utc": latest["time"].isoformat(),
        "symbol": config.symbol,
        "timeframe": config.timeframe_name,
        "close": round(float(latest["close"]), 5),
        "ema20": round(float(latest["ema20"]), 5),
        "ema50": round(float(latest["ema50"]), 5),
        "rsi14": round(float(latest["rsi14"]), 5),
        "atr14": round(float(latest["atr14"]), 5),
        "spread": int(latest["spread"]),
        "action": signal["action"],
        "confidence": int(signal["confidence"]),
        "market_regime": signal["market_regime"],
        "reason": signal["reason"],
        "model": config.openai_model,
    }

    with SIGNALS_FILE.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SIGNAL_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def append_decision(
    config: Config,
    latest: pd.Series,
    filter_decision: FilterDecision,
    *,
    openai_called: bool,
    signal: dict[str, Any] | None,
) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    exists = DECISIONS_FILE.exists()

    row = {
        "logged_at_utc": datetime.now(timezone.utc).isoformat(),
        "candle_epoch_raw": int(latest["server_epoch_raw"]),
        "candle_time_server": latest["server_time"].isoformat(),
        "candle_time_utc": latest["time"].isoformat(),
        "symbol": config.symbol,
        "timeframe": config.timeframe_name,
        "close": round(float(latest["close"]), 5),
        "ema20": round(float(latest["ema20"]), 5),
        "ema50": round(float(latest["ema50"]), 5),
        "rsi14": round(float(latest["rsi14"]), 5),
        "atr14": round(float(latest["atr14"]), 5),
        "spread": int(latest["spread"]),
        "filter_enabled": config.filter_enabled,
        "filter_qualifies": filter_decision.qualifies,
        "filter_direction": filter_decision.direction,
        "filter_bull_score": filter_decision.bull_score,
        "filter_bear_score": filter_decision.bear_score,
        "filter_reason": filter_decision.reason,
        "openai_called": openai_called,
        "action": "" if signal is None else signal["action"],
        "confidence": "" if signal is None else int(signal["confidence"]),
        "market_regime": "" if signal is None else signal["market_regime"],
        "reason": filter_decision.reason if signal is None else signal["reason"],
        "model": "" if signal is None else config.openai_model,
    }

    with DECISIONS_FILE.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=DECISION_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def get_last_processed_candle_epoch_raw(config: Config) -> int | None:
    if DECISIONS_FILE.exists():
        try:
            history = pd.read_csv(DECISIONS_FILE)
            subset = history[
                (history["symbol"] == config.symbol)
                & (history["timeframe"] == config.timeframe_name)
            ]
            if not subset.empty:
                return int(subset.iloc[-1]["candle_epoch_raw"])
        except Exception as exc:
            log(f"WARNING: gagal membaca state dari decisions.csv: {exc}")

    # Upgrade path from V2: its candle_time_utc column actually preserved the
    # raw MT5 wall-clock epoch on the development terminal. Parse it only as a
    # fallback until V3 writes decisions.csv.
    if SIGNALS_FILE.exists():
        try:
            history = pd.read_csv(SIGNALS_FILE)
            subset = history[
                (history["symbol"] == config.symbol)
                & (history["timeframe"] == config.timeframe_name)
            ]
            if not subset.empty:
                value = pd.to_datetime(
                    subset.iloc[-1]["candle_time_utc"],
                    utc=True,
                )
                return int(value.timestamp())
        except Exception as exc:
            log(f"WARNING: gagal membaca legacy state dari signals.csv: {exc}")

    return None


def get_filter_decision(
    config: Config,
    df: pd.DataFrame,
) -> FilterDecision:
    if not config.filter_enabled:
        return FilterDecision(
            qualifies=True,
            direction="NONE",
            bull_score=0,
            bear_score=0,
            reason="LOCAL_FILTER disabled; OpenAI call allowed for research.",
            metrics={},
        )

    symbol_info = mt5.symbol_info(config.symbol)
    if symbol_info is None or symbol_info.point <= 0:
        raise RuntimeError(
            f"Gagal membaca point untuk {config.symbol}: {mt5.last_error()}"
        )

    return evaluate_setup(
        df,
        point=float(symbol_info.point),
        config=config.filter_config,
    )


def process_new_candle(
    client: OpenAI,
    config: Config,
) -> int:
    df = add_indicators(load_closed_candles(config))
    latest = df.iloc[-1]
    candle_epoch_raw = int(latest["server_epoch_raw"])

    log(
        "New closed candle | "
        f"server_time={latest['server_time'].isoformat()} | "
        f"utc={latest['time'].isoformat()} | "
        f"close={latest['close']:.5f} | "
        f"ema20={latest['ema20']:.5f} | ema50={latest['ema50']:.5f} | "
        f"rsi14={latest['rsi14']:.2f} | atr14={latest['atr14']:.5f} | "
        f"spread={int(latest['spread'])}"
    )

    filter_decision = get_filter_decision(config, df)

    log(
        "Local filter | "
        f"pass={filter_decision.qualifies} | "
        f"candidate={filter_decision.direction} | "
        f"bull={filter_decision.bull_score} | "
        f"bear={filter_decision.bear_score} | "
        f"reason={filter_decision.reason}"
    )

    if not filter_decision.qualifies:
        append_decision(
            config,
            latest,
            filter_decision,
            openai_called=False,
            signal=None,
        )
        log(f"OpenAI skipped | Saved decision: {DECISIONS_FILE}")
        return candle_epoch_raw

    market_data = build_market_payload(config, df, filter_decision)
    signal = ask_openai(client, config, market_data)

    append_signal(config, latest, signal)
    append_decision(
        config,
        latest,
        filter_decision,
        openai_called=True,
        signal=signal,
    )

    log(
        "OpenAI signal | "
        f"action={signal['action']} | confidence={signal['confidence']} | "
        f"regime={signal['market_regime']} | reason={signal['reason']}"
    )
    log(f"Saved signal: {SIGNALS_FILE}")
    log(f"Saved decision: {DECISIONS_FILE}")

    return candle_epoch_raw


def main() -> None:
    config = load_config()
    client = OpenAI()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    connect_mt5(config)

    last_processed = get_last_processed_candle_epoch_raw(config)

    if last_processed is None:
        log("Belum ada state log. Candle tertutup terbaru akan diproses sekali.")
    else:
        log(f"Resume state | last_processed_raw_epoch={last_processed}")

    log(
        f"Local filter | enabled={config.filter_enabled} | "
        f"lookback={config.filter_config.lookback} | "
        f"min_score={config.filter_config.min_score} | "
        f"max_spread_atr={config.filter_config.max_spread_atr_ratio}"
    )

    log(
        f"Monitor started | poll={config.poll_seconds}s | "
        "NO ORDER EXECUTION CODE IS PRESENT"
    )

    try:
        while True:
            try:
                latest_closed = get_latest_closed_candle_epoch_raw(config)

                if latest_closed != last_processed:
                    last_processed = process_new_candle(client, config)

                time.sleep(config.poll_seconds)

            except KeyboardInterrupt:
                raise
            except Exception as exc:
                log(f"ERROR: {exc}")
                time.sleep(max(config.poll_seconds, 5))

    except KeyboardInterrupt:
        log("Monitor dihentikan oleh user (Ctrl+C).")
    finally:
        mt5.shutdown()
        log("MT5 connection closed.")


if __name__ == "__main__":
    main()
