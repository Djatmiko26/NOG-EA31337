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


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
SIGNALS_FILE = DATA_DIR / "signals.csv"
LOG_FILE = LOG_DIR / "market_monitor.log"


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


def log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] {message}"
    print(line, flush=True)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


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

    return Config(
        mt5_path=mt5_path,
        symbol=symbol,
        timeframe_name=timeframe_name,
        timeframe=TIMEFRAME_MAP[timeframe_name],
        poll_seconds=poll_seconds,
        candles_to_load=candles_to_load,
        candles_to_ai=candles_to_ai,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna").strip(),
    )


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

    # Monitor ini sengaja READ-ONLY. trade_allowed tidak dibutuhkan.
    log(
        f"MT5 connected | symbol={config.symbol} | "
        f"timeframe={config.timeframe_name} | trade_execution=DISABLED_BY_DESIGN"
    )


def get_latest_closed_candle_time(config: Config) -> int:
    rates = mt5.copy_rates_from_pos(
        config.symbol,
        config.timeframe,
        1,  # 0 = candle yang masih berjalan; 1 = candle terakhir yang sudah tutup
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
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.sort_values("time").reset_index(drop=True)
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


def build_market_payload(config: Config, df: pd.DataFrame) -> dict[str, Any]:
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
        "latest_closed_candle": ai_df.iloc[-1]["time"],
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


def get_last_logged_candle_epoch(config: Config) -> int | None:
    if not SIGNALS_FILE.exists():
        return None

    try:
        history = pd.read_csv(SIGNALS_FILE)
        if history.empty:
            return None

        subset = history[
            (history["symbol"] == config.symbol)
            & (history["timeframe"] == config.timeframe_name)
        ]
        if subset.empty:
            return None

        value = pd.to_datetime(
            subset.iloc[-1]["candle_time_utc"],
            utc=True,
        )
        return int(value.timestamp())
    except Exception as exc:
        log(f"WARNING: gagal membaca state dari signals.csv: {exc}")
        return None


def process_new_candle(
    client: OpenAI,
    config: Config,
) -> int:
    df = add_indicators(load_closed_candles(config))
    latest = df.iloc[-1]
    candle_epoch = int(latest["time"].timestamp())

    market_data = build_market_payload(config, df)

    log(
        "New closed candle | "
        f"time={latest['time'].isoformat()} | close={latest['close']:.5f} | "
        f"ema20={latest['ema20']:.5f} | ema50={latest['ema50']:.5f} | "
        f"rsi14={latest['rsi14']:.2f} | atr14={latest['atr14']:.5f}"
    )

    signal = ask_openai(client, config, market_data)
    append_signal(config, latest, signal)

    log(
        "OpenAI signal | "
        f"action={signal['action']} | confidence={signal['confidence']} | "
        f"regime={signal['market_regime']} | reason={signal['reason']}"
    )
    log(f"Saved: {SIGNALS_FILE}")

    return candle_epoch


def main() -> None:
    config = load_config()
    client = OpenAI()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    connect_mt5(config)

    last_processed = get_last_logged_candle_epoch(config)

    if last_processed is None:
        log("Belum ada signal log. Candle tertutup terbaru akan dianalisis sekali.")
    else:
        last_dt = datetime.fromtimestamp(last_processed, tz=timezone.utc)
        log(f"Resume state | last_logged_candle={last_dt.isoformat()}")

    log(
        f"Monitor started | poll={config.poll_seconds}s | "
        "NO ORDER EXECUTION CODE IS PRESENT"
    )

    try:
        while True:
            try:
                latest_closed = get_latest_closed_candle_time(config)

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
