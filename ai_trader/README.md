# NOG OpenAI Trader

Experimental read-only market monitor that connects MetaTrader 5 to the OpenAI API.

## Current status

Working pipeline:

```text
MetaTrader 5
    |
    | XAUUSD closed M5 candles
    v
Python
    |
    | EMA20 / EMA50 / RSI14 / ATR14
    v
OpenAI API
    |
    | Structured output
    v
BUY / SELL / WAIT
    |
    v
data/signals.csv + logs/market_monitor.log
```

**No order execution code is present in this version.**

## Safety rules

- Never commit `.env`.
- Never commit an OpenAI API key.
- Do not enable live trading while this branch is in the research/monitoring phase.
- OpenAI output is analysis, not a direct order instruction.
- Risk management and order permission will remain local deterministic rules.
- The planned risk-per-trade baseline is 0.25%, but it is not active in this monitor.

## Requirements

- Windows
- MetaTrader 5 terminal
- Python 3.14+ (current development machine uses Python 3.14.6)
- OpenAI API key

Install dependencies:

```powershell
pip install -r requirements.txt
```

## Setup

Create `.env` from the template:

```powershell
Copy-Item .env.example .env
notepad .env
```

Set your own API key:

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

## Run

Keep MetaTrader 5 open and logged into the demo account, then run:

```powershell
python market_monitor.py
```

Stop safely with:

```text
Ctrl+C
```

The program creates runtime files locally:

```text
data/signals.csv
logs/market_monitor.log
```

These runtime files are ignored by Git.

## What `signals.csv` records

Each processed closed candle stores:

- candle timestamp
- symbol and timeframe
- close
- EMA20
- EMA50
- RSI14
- ATR14
- spread
- OpenAI action (`BUY`, `SELL`, `WAIT`)
- confidence (classification confidence, **not** probability of profit)
- market regime
- reason
- model name

## Development roadmap

### V1 - Connectivity ✅

- MT5 -> Python
- Python -> OpenAI
- XAUUSD M5 candle retrieval
- structured `BUY / SELL / WAIT`

### V2 - Continuous monitor ✅ initial implementation

- process only newly closed candles
- call OpenAI once per new candle
- CSV signal journal
- text runtime log
- restart/resume protection

### V3 - Local setup filter

Before calling OpenAI, evaluate deterministic conditions such as:

- spread
- ATR
- trend alignment
- breakout proximity
- session

Only qualifying setups should consume an OpenAI API call.

### V4 - Outcome labelling / research

Measure what happened after each historical signal:

- MFE / MAE
- return after N bars
- hypothetical SL/TP result
- performance by action
- performance by confidence bucket
- performance by market regime

### V5 - Risk engine

Deterministic local controls:

- risk per trade: 0.25%
- maximum daily loss
- maximum open positions
- spread ceiling
- signal expiry
- SL distance validation
- broker-aware lot sizing

### V6 - Demo execution

Only after research metrics justify it:

- EA/API bridge
- demo orders only
- kill switch
- idempotent order handling
- full execution journal

### V7 - Walk-forward validation

Validate on unseen periods before considering a small live account.

## Branch

Development branch:

```text
openai-trader-v1
```

It was created from `small-account-v1` so the existing small-account work remains preserved.
