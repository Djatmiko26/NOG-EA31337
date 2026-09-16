# NOG OpenAI Trader

Experimental read-only market research system connecting MetaTrader 5 to the OpenAI API.

## Current pipeline

```text
MetaTrader 5
    |
    | CLOSED XAUUSD M5 candle
    v
Python indicators
EMA20 / EMA50 / RSI14 / ATR14
    |
    v
Deterministic local filter
spread / ATR / trend / momentum / breakout proximity
    |
    +---- setup weak ----------> decisions.csv (OpenAI skipped)
    |
    +---- setup qualifies
             |
             v
         OpenAI API
             |
             | structured output
             v
       BUY / SELL / WAIT
             |
             +--> signals.csv
             +--> decisions.csv
```

**No order execution code is present in this version.**

## Safety rules

- Never commit `.env`.
- Never commit an OpenAI API key.
- Do not enable live trading while this branch is in the research/monitoring phase.
- OpenAI output is analysis, not a direct order instruction.
- Risk management and order permission remain local deterministic rules.
- Planned baseline risk per trade is 0.25%, but no trade execution exists yet.

## Requirements

- Windows
- MetaTrader 5 terminal
- Python 3.14+
- OpenAI API key

Install:

```powershell
pip install -r requirements.txt
```

## Setup

Create `.env`:

```powershell
Copy-Item .env.example .env
notepad .env
```

Minimum required value:

```text
OPENAI_API_KEY=your_real_key_here
```

Default development configuration:

```text
OPENAI_MODEL=gpt-5.6-luna
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
SYMBOL=XAUUSD
TIMEFRAME=M5
POLL_SECONDS=5
CANDLES_TO_LOAD=120
CANDLES_TO_AI=30
```

V3 local-filter defaults:

```text
FILTER_ENABLED=true
FILTER_LOOKBACK=12
FILTER_MIN_SCORE=4
FILTER_MIN_ATR_RATIO=0.00035
FILTER_MAX_SPREAD_ATR_RATIO=0.12
FILTER_BREAKOUT_BUFFER_ATR=0.25
FILTER_MIN_EMA_GAP_ATR=0.10
FILTER_RSI_BULL=52
FILTER_RSI_BEAR=48
```

These are **research defaults**, not proven profitable parameters. They must be evaluated with collected data before any demo execution work.

## Run

Keep MT5 open and logged into the demo account:

```powershell
python market_monitor.py
```

Stop safely:

```text
Ctrl+C
```

## Runtime files

```text
data/signals.csv
data/decisions.csv
logs/market_monitor.log
```

All are ignored by Git.

### `decisions.csv`

Records **every processed closed candle**, including candles where OpenAI was skipped. It contains:

- raw MT5 candle epoch
- broker/server wall-clock time
- normalized UTC time
- indicator values
- spread
- local filter result
- bullish/bearish setup scores
- whether OpenAI was called
- OpenAI result when applicable

This file becomes the main dataset for later filter and outcome research.

### `signals.csv`

Records only candles that passed the local filter and were sent to OpenAI.

## Local filter V3

The filter is deliberately deterministic and inspectable. It checks:

- ATR relative to price
- spread relative to ATR
- EMA20/EMA50 alignment
- close relative to EMA20
- RSI direction
- last-bar momentum
- EMA separation relative to ATR
- proximity to the previous lookback high/low

Bullish and bearish conditions each receive a score. By default the strongest direction needs at least `4` of `6` conditions. A tied, weak, low-volatility, or expensive-spread setup is skipped locally.

The filter does **not** place orders and does **not** decide position size.

## MT5 server time handling

During V2 development, the MetaQuotes demo terminal exposed candle times roughly three hours ahead of real UTC. V3 therefore:

1. keeps the original MT5 epoch as candle identity;
2. records broker/server wall-clock time separately;
3. estimates the server-to-UTC offset from live tick time;
4. stores a normalized UTC timestamp.

This must be validated before adding London/New York session rules.

## Tests

The deterministic filter has basic unit tests:

```powershell
python -m unittest discover -s tests -v
```

## Development roadmap

### V1 - Connectivity ✅

- MT5 -> Python
- Python -> OpenAI
- XAUUSD M5 closed-candle retrieval
- structured `BUY / SELL / WAIT`

### V2 - Continuous monitor ✅

- process only newly closed candles
- one OpenAI call per processed candle
- CSV signal journal
- runtime logging
- restart/resume protection

### V3 - Local setup filter ✅ initial implementation

- spread/ATR gate
- volatility gate
- trend/momentum scoring
- breakout proximity
- OpenAI skip path
- every-candle decision journal
- broker/server vs UTC timestamp separation
- unit tests

Still pending in V3:

- validate detected broker clock offset over a full trading day
- add session filter only after time validation
- tune thresholds from data rather than intuition

### V4 - Outcome labelling / research

For every historical decision measure:

- return after N bars
- MFE / MAE
- hypothetical SL/TP result
- performance by local score
- performance by OpenAI action
- performance by confidence bucket
- performance by market regime
- API call reduction from the local filter

### V5 - Risk engine

Deterministic local controls:

- 0.25% risk per trade
- maximum daily loss
- maximum open positions
- spread ceiling
- signal expiry
- SL validation
- broker-aware lot sizing

### V6 - Demo execution

Only after research metrics justify it:

- EA/API bridge
- demo orders only
- kill switch
- idempotent order handling
- execution journal

### V7 - Walk-forward validation

Validate on unseen periods before considering a small live account.

## Branch

```text
openai-trader-v1
```

Created from `small-account-v1` so the existing small-account development remains preserved.
