# Blind-payload comparison: one controlled input change

This is an exploratory information-ablation pilot on the **existing frozen samples**.
It is not an untouched holdout and does not establish a profitable strategy.
The old pilot and live-monitor code are not changed.

## What changes (and what does not)

`openai_blind_validation.py` reads the completed `data/openai_pilot.sqlite3`
using a read-only SQLite connection. All original samples must have valid
`SUCCESS` replies. It copies their frozen inputs and original responses into a
**separate** database. No new MT5 history is fetched; no sample is selected by
its outcome; nothing is resampled. The original results are reused, not requested again.

The **only deliberate request change** is removing the entire payload's
`local_filter` block: candidate direction, bullish/bearish scores, reason and metrics.
All candle values, indicators, timestamps, symbol, timeframe, instructions, model ID,
reasoning effort, JSON schema and output-token bound stay exactly as saved originally.
The existing instruction mentioning local_filter remains word-for-word to avoid a
second intervention in the prompt. It supplies no direction when that block is absent.
No new instruction forces disagreement or WAIT.

Here, "blind" means blind to the **explicit local-filter hint**, not blind to
indicators or to sample-selection effects. All these setups were chosen by the
same local filter. Raw bars and the inherited timezone estimate are not revalidated.
Unknown payload/candle fields, inconsistent directional labels, or changed prompt
settings stop preparation rather than silently alter the experiment.

## Commands (Windows PowerShell, from ai_trader)

Keep the live monitor stopped to avoid its separate API usage. No MT5 connection
is needed for any mode of this new script. Do not delete or replace either database.

```powershell
git pull --ff-only origin openai-trader-v1
python -m unittest discover -s tests -v
python openai_blind_validation.py --prepare
```

`--prepare` is also the no-argument default. It makes **zero API calls**, does
not read the API key, and does not modify the original database. A repeated prepare
reuses the exact saved blind plan, even if `.env` or the original database later changes.

For the new **paid API experiment**, explicitly run:

```powershell
python openai_blind_validation.py --run
```

This permits at most one new attempt per original sample (at most **10 new attempts**).
These are additional to the original pilot's charges. SDK retries are disabled
(`max_retries=0`), timeout is 45 seconds, output-token limit is inherited, and the
official API endpoint is used. No model fallback or automatic model upgrade occurs.
The API key is read from the local environment only for unattempted requests and
is never stored in the snapshot. `.env` changes cannot change the frozen model.

The request/token limits are **not a dollar spending cap**. Check API billing
for actual charges; timeouts can be charged without yielding usable output.

```powershell
python openai_blind_validation.py --report
Get-Content -LiteralPath .\data\openai_blind_report.md
```

`--report` uses only saved local records: no MT5, model, API key or network is needed.

## Resume, failures and files

Every new attempt is reserved durably **before** HTTP. Responses are saved after
each request. Errors, refusals, invalid JSON or incomplete responses stop that run;
a crash may leave STARTED (uncertain). A later run skips all reserved attempts,
including failed/uncertain ones, and can only process unattempted samples. Completed
reruns make zero new requests. Do not run multiple copies or delete databases to retry.
Protection applies to this database, not other scripts, other copies or old charges.

New local files (under the existing Git-ignored data directory):

```text
data/openai_blind.sqlite3
data/openai_blind_comparison.csv
data/openai_blind_report.md
```

The original `openai_pilot.sqlite3`, CSVs and report stay unchanged. Never commit
these databases or your `.env` to GitHub.

## Comparison and limitations

The report lists every sample's local direction, original AI action, blind AI action,
status, and whether its action changed. For each horizon it compares **the same
successful-blind-response subset** across Local, Original AI, Blind AI (WAIT=0),
and Blind agree-only. Failed results are excluded everywhere with counts shown;
they are not fictitious WAITs or zero returns. It also shows new observed tokens,
returned-model-ID differences and local returns on blind WAIT samples.

Different actions are not automatically better. Agreement is not trading accuracy.
Gross signal-close endpoint returns exclude execution latency, spread, commissions,
slippage, financing and API cost. They are not account returns. No orders, SL/TP,
lot sizes, equity curve or drawdown are simulated by these pilot files.

One response per condition cannot establish the cause of differences: model outputs
vary and a model alias/backend can change. Matching returned model IDs do not prove
identical weights. Unchanged results do not prove the absence of a hint effect,
and changed results do not prove anchoring. A repeated original-input control would
be needed to quantify ordinary response variability; it is **not run or charged here**.
Do not tune prompts repeatedly to improve these ten previously inspected samples.
Reserve genuinely unseen periods and forward data before approving a strategy.

## Test evidence and scope

On 2026-09-16, **30 focused offline tests passed** on Python 3.13.5, with synthetic
candles, temporary SQLite files and mocked API responses. The reused pilot_support.py
matched repository blob `c34e0b6632894a4bb7487f164910712e2b5fd65d` exactly.
No real MT5 terminal or paid API was used, and no profitability was tested.
The full existing Windows/MT5 suite still needs to run on the development PC.

Focused test command:

```powershell
python -m unittest discover -s tests -p "test_blind_validation.py" -v
```

Coverage includes single-field removal, unchanged model/settings/data, outcome/hint
exclusion from actual mocked request arguments, read-only source, cap, frozen reuse,
pre-request reservations, repeat/error/crash handling, WAIT accounting, common
sample denominators, model-ID warnings and offline reporting.

Official references (API behavior, **not** trading evidence):

- https://developers.openai.com/api/docs/guides/evaluation-best-practices
- https://developers.openai.com/api/docs/guides/prompt-engineering
- https://developers.openai.com/api/reference/python/
