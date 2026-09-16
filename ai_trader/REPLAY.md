# Historical Replay / Backtest Research

`historical_replay.py` accelerates research by replaying already-closed MT5 candles.

It does **not** place orders.

## No-lookahead rule

For a decision at historical bar `i`:

- indicators and filter input may use only bars `<= i`;
- OpenAI payload, when enabled, contains only bars `<= i`;
- bars `i+1 ... i+20` are reserved only for outcome labelling;
- the currently forming live candle is never included.

This separation is covered by unit tests for replay index boundaries.

## Safe first run: local filter only

Keep these values in `.env`:

```text
REPLAY_BARS=5000
REPLAY_WARMUP_BARS=200
REPLAY_OPENAI=false
REPLAY_MAX_AI_CALLS=20
```

Run:

```powershell
python historical_replay.py
```

This creates:

```text
data/replay_decisions.csv
data/replay_outcomes.csv
data/replay_report.md
```

With `REPLAY_OPENAI=false`, the replay costs **zero OpenAI API calls**. It measures the deterministic local filter across historical bars and immediately labels 5/10/20-bar outcomes.

## Optional historical OpenAI sample

Do not enable this until the local-only replay has been inspected.

To test OpenAI on a capped sample of qualifying historical setups:

```text
REPLAY_OPENAI=true
REPLAY_MAX_AI_CALLS=20
```

The engine first finds every qualifying setup, then selects up to the hard cap distributed across the replay period. This avoids blindly calling the API on every historical candidate.

`REPLAY_MAX_AI_CALLS` is a hard maximum for valid selected candidates in one replay run. API failures are recorded in the replay decision output instead of discarding the entire local replay dataset.

## Interpreting the report

The replay report uses the same research statistics as the live monitor:

- local-filter qualification rate;
- API call / skip rate;
- directional return after 5, 10, and 20 bars;
- 20-bar MFE and MAE;
- OpenAI BUY/SELL performance when historical AI sampling is enabled;
- confidence buckets;
- market regime groups;
- Local Filter vs OpenAI agreement.

Historical performance does not prove future profitability. Replay results should later be split into development and unseen validation periods before any demo execution logic is enabled.

## Recommended workflow

1. `REPLAY_OPENAI=false`, 5,000 bars.
2. Inspect `replay_report.md` and local-filter sample size.
3. Change filter thresholds only if there is a defensible research reason.
4. Repeat on different history windows rather than optimizing one window endlessly.
5. Enable a small capped OpenAI sample only after local filtering is understood.
6. Build walk-forward / out-of-sample validation before demo execution.
