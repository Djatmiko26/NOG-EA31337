# NOG OpenAI Trader

Experimental read-only market research system connecting MetaTrader 5 to the OpenAI API.

## Current architecture

```text
LIVE RESEARCH

MT5 closed candle
      |
      v
EMA20 / EMA50 / RSI14 / ATR14
      |
      v
Deterministic local filter
      |
      +-- weak setup --> local journal, OpenAI skipped
      |
      +-- qualified --> OpenAI structured BUY / SELL / WAIT
                              |
                              v
                     decisions / signals journal
                              |
                    after 20 future closed bars
                              v
                     outcome_labeller.py
                              |
                              v
                     research_report.py

HISTORICAL RESEARCH

last N closed MT5 bars
      |
      v
historical_replay.py
      |
      +-- no-lookahead local-filter replay
      +-- immediate 5/10/20-bar outcome labels
      +-- optional capped OpenAI historical sample
      |
      v
replay_report.md
```

**No order execution code is present in this branch.**

## Safety rules

- Never commit `.env` or an OpenAI API key.
- OpenAI output is research analysis, not a direct order instruction.
- Risk management and order permission remain local deterministic rules.
- Planned baseline risk per trade is 0.25%, but execution is not implemented yet.
- Historical OpenAI replay is OFF by default and must have an explicit hard call cap when enabled.

## Requirements

- Windows
- MetaTrader 5 terminal, open and connected
- Python 3.14+
- OpenAI API key

Install:

```powershell
pip install -r requirements.txt
```

## Setup

Create `.env` locally if it does not already exist:

```powershell
Copy-Item .env.example .env
notepad .env
```

Minimum value:

```text
OPENAI_API_KEY=your_real_key_here
```

Current development defaults:

```text
OPENAI_MODEL=gpt-5.6-luna
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
SYMBOL=XAUUSD
TIMEFRAME=M5
POLL_SECONDS=5
CANDLES_TO_LOAD=120
CANDLES_TO_AI=30

FILTER_ENABLED=true
FILTER_LOOKBACK=12
FILTER_MIN_SCORE=4
FILTER_MIN_ATR_RATIO=0.00035
FILTER_MAX_SPREAD_ATR_RATIO=0.12
FILTER_BREAKOUT_BUFFER_ATR=0.25
FILTER_MIN_EMA_GAP_ATR=0.10
FILTER_RSI_BULL=52
FILTER_RSI_BEAR=48

REPLAY_BARS=5000
REPLAY_WARMUP_BARS=200
REPLAY_OPENAI=false
REPLAY_MAX_AI_CALLS=20
```

These are research defaults, not proven profitable parameters.

## Live monitor

Run:

```powershell
python market_monitor.py
```

It processes only newly CLOSED candles and creates local runtime files such as:

```text
data/signals.csv
data/decisions.csv
logs/market_monitor.log
```

The current development terminal has detected MetaQuotes demo server time at approximately `UTC+3`; the program stores the raw MT5 candle epoch, server wall-clock time, and normalized UTC separately.

## Outcome labelling

Run after live decisions have at least 20 future closed bars:

```powershell
python outcome_labeller.py
```

Output:

```text
data/outcomes.csv
```

For each matured decision it measures:

- close after 5, 10 and 20 future bars;
- raw market return;
- local-filter directional return;
- OpenAI directional return for BUY/SELL;
- 20-bar MFE and MAE.

No arbitrary SL/TP assumption is silently inserted.

## Research report

Run:

```powershell
python research_report.py
```

Output:

```text
data/research_report.md
```

It reports:

- local-filter qualification rate;
- OpenAI API call/skip rate;
- local directional performance;
- OpenAI BUY/SELL directional performance;
- confidence buckets;
- market regime groups;
- local-filter vs OpenAI agreement.

Small samples are explicitly flagged.

## Historical replay

Historical replay accelerates research without waiting for live candles.

Safe first run:

```text
REPLAY_OPENAI=false
REPLAY_BARS=5000
REPLAY_WARMUP_BARS=200
```

Then:

```powershell
python historical_replay.py
```

Outputs:

```text
data/replay_decisions.csv
data/replay_outcomes.csv
data/replay_report.md
```

### No-lookahead rule

For historical decision bar `i`:

- decision inputs use only bars `<= i`;
- when historical OpenAI is enabled, the API payload also contains only bars `<= i`;
- bars `i+1 ... i+20` are reserved strictly for outcome measurement.

### Optional capped historical OpenAI sample

Only after inspecting the local-only replay, optionally set:

```text
REPLAY_OPENAI=true
REPLAY_MAX_AI_CALLS=20
```

The replay first finds all local-filter candidates and then samples up to the hard cap across the whole historical replay period. It does not call OpenAI on every candidate.

More detail: `REPLAY.md`.

## Tests

Run from `ai_trader`:

```powershell
python -m unittest discover -s tests -v
```

Tests currently cover:

- local-filter bullish candidate;
- high-spread rejection;
- low-volatility rejection;
- BUY/SELL directional return sign;
- BUY/SELL MFE/MAE direction;
- correct 5th/10th/20th future-bar outcome selection;
- research-report statistics and grouping;
- historical replay warmup/future-bar boundaries;
- capped, deterministic historical OpenAI sampling.

## Development roadmap

### V1 - Connectivity ✅

- MT5 -> Python
- Python -> OpenAI
- XAUUSD M5 closed-candle retrieval
- structured BUY / SELL / WAIT

### V2 - Continuous monitor ✅

- new closed candle detection
- one OpenAI request per accepted live candle
- journals and restart/resume

### V3 - Local setup filter ✅ initial implementation

- spread/ATR gate
- volatility gate
- trend/momentum score
- breakout proximity
- OpenAI skip path
- server-time separation

Still to validate:

- broker clock offset through session/day changes;
- session filter only after time validation;
- filter thresholds from larger data.

### V4 - Outcome / research ✅ expanded

- 5/10/20-bar outcome labelling
- MFE/MAE
- aggregate research report
- confidence/regime/agreement analysis
- historical no-lookahead replay
- safe capped historical OpenAI sampling

Next research work:

- run larger local historical samples;
- compare multiple non-overlapping historical windows;
- add explicit train/development vs unseen validation periods;
- only then evaluate ATR SL/TP experiments.

### V5 - Risk engine

Planned deterministic controls:

- 0.25% risk per trade
- maximum daily loss
- maximum open positions
- spread ceiling
- signal expiry
- SL validation
- broker-aware lot sizing

### V6 - Demo execution

Only after research evidence is adequate:

- EA/API bridge
- demo orders only
- kill switch
- idempotent order handling
- execution journal

### V7 - Walk-forward validation

Validate on unseen periods before any small live account is considered.

## Branch

```text
openai-trader-v1
```

Created from `small-account-v1`, preserving the earlier small-account development.
