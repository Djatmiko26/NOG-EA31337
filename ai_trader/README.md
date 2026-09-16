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

Later, after 20 future CLOSED bars exist:

decisions.csv + MT5 history
             |
             v
      outcome_labeller.py
             |
             v
         outcomes.csv
  return 5/10/20 bars + MFE/MAE
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

## Run monitor

Keep MT5 open and logged into the demo account:

```powershell
python market_monitor.py
```

Stop safely:

```text
Ctrl+C
```

## Run V4 outcome labelling

The outcome labeller does not call OpenAI and does not place orders. It reads `decisions.csv`, waits until at least 20 future **closed** bars exist, retrieves those bars from MT5, and appends research labels to `outcomes.csv`.

Run:

```powershell
python outcome_labeller.py
```

A recent decision may correctly produce:

```text
new_labels=0 | pending_for_20_bars=1
```

That simply means 20 future closed candles do not exist yet. Re-run the labeller later.

## Runtime files

```text
data/signals.csv
data/decisions.csv
data/outcomes.csv
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

This is the source journal for research.

### `signals.csv`

Records only candles that passed the local filter and were sent to OpenAI.

### `outcomes.csv`

V4 records only decisions that have at least 20 future closed bars available. For each matured decision it stores:

- entry close and ATR14
- local filter direction and scores
- OpenAI action/confidence/regime when available
- future close after 5, 10, and 20 bars
- raw market return after 5, 10, and 20 bars
- direction-adjusted local-filter return
- direction-adjusted OpenAI return
- highest/lowest price during the next 20 bars
- MFE/MAE for the long market direction
- direction-adjusted MFE/MAE for the local filter
- direction-adjusted MFE/MAE for OpenAI BUY/SELL signals

`WAIT` and locally skipped decisions deliberately have blank direction-adjusted metrics where no BUY/SELL direction exists.

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

On the current development terminal, V3 detected `+3.0h`. Continue observing this across trading sessions before adding London/New York session rules.

V4 uses the original raw MT5 candle epoch as the history key so outcome matching is independent of display timezone.

## Outcome definitions

For a decision at entry close `P0`, the 5-bar outcome uses the close of the **fifth future closed bar**, not the current/forming bar. The same rule applies to 10 and 20 bars.

Raw market return is long-oriented:

```text
(exit - entry) / entry * 100
```

Direction-adjusted return:

- BUY: same as raw market return
- SELL: sign is inverted
- WAIT/NONE: blank

MFE (maximum favorable excursion) is never below zero. MAE (maximum adverse excursion) is never above zero. Both are measured over the next 20 closed bars.

V4 intentionally does **not** simulate SL/TP yet because SL/TP rules have not been validated. ATR-based SL/TP research will be added only as an explicit, configurable experiment rather than silently assuming arbitrary distances.

## Tests

Run all deterministic tests from the `ai_trader` directory:

```powershell
python -m unittest discover -s tests -v
```

Current tests cover:

- bullish filter candidate
- excessive spread skip
- low-volatility skip
- BUY/SELL directional return sign
- BUY/SELL MFE/MAE direction
- correct 5th/10th/20th future-bar selection

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

### V4 - Outcome labelling / research ✅ initial implementation

- mature decisions only after 20 future closed bars
- future close after 5/10/20 bars
- raw market returns
- direction-adjusted local-filter returns
- direction-adjusted OpenAI returns
- 20-bar MFE/MAE
- duplicate-label protection
- unit tests for outcome maths

Still pending in V4:

- aggregate performance report by local score
- performance by OpenAI action
- performance by confidence bucket
- performance by market regime
- API-call reduction report
- optional explicit ATR-based SL/TP simulation
- larger historical/replay dataset

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
