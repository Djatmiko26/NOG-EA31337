# Research workflow

The OpenAI trader branch is still a read-only research system. No order execution is enabled.

## Recommended loop

Keep the market monitor running:

```powershell
python market_monitor.py
```

After decisions have at least 20 future closed candles, label their outcomes:

```powershell
python outcome_labeller.py
```

Then generate the research report:

```powershell
python research_report.py
```

The report is written locally to:

```text
data/research_report.md
```

All runtime research data under `data/` is ignored by Git.

## What the report measures

### Pipeline efficiency

- processed closed candles
- local-filter qualified candles
- OpenAI calls
- OpenAI calls skipped
- API call rate / skip rate
- OpenAI BUY / SELL / WAIT mix

### Local-filter directional performance

For 5, 10 and 20 future closed bars:

- sample count
- average directional return
- median directional return
- win rate
- loss rate
- 20-bar MFE
- 20-bar MAE

### OpenAI directional performance

Only OpenAI `BUY` and `SELL` classifications are treated as directional trades for performance statistics. `WAIT` is excluded from directional return calculations.

The same 5/10/20-bar statistics are computed for OpenAI directions.

### Local filter vs OpenAI agreement

When OpenAI returns BUY or SELL, the report checks whether it agrees with the deterministic local candidate direction and compares 20-bar outcomes for agreement vs disagreement groups.

### Confidence research

OpenAI classification confidence is grouped into:

```text
00-59
60-69
70-79
80-89
90-100
```

This is specifically intended to test whether confidence has any empirical relationship with realised returns. Confidence is **not** treated as probability of profit.

### Market regime research

Performance is grouped by the OpenAI market-regime classification:

- BULL_TREND
- BEAR_TREND
- RANGE
- VOLATILE
- UNCLEAR

### Direction research

BUY and SELL results are separated so directional bias can be detected.

## Sample-size rules

The report prints a strong warning below 30 matured decisions and a preliminary-data warning below 100.

Do not change filters, confidence thresholds, risk, SL, TP, or enable execution because of a handful of successful observations.

## Interpretation principles

- Compare mean and median; mean alone can be dominated by outliers.
- MFE/MAE are observations, not automatic SL/TP recommendations.
- Confidence buckets need sufficient samples before a threshold is justified.
- Regime groups need sufficient samples before regime-specific rules are justified.
- A filter should be kept, changed, or removed because of accumulated out-of-sample evidence, not because it sounds intuitively correct.
- Demo execution remains a later milestone after research and walk-forward validation.
