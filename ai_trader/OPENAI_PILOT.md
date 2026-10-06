# Bounded OpenAI historical pilot

This supersedes the earlier instructions to call `openai_holdout_validation.py`
without arguments. The filename is retained, but **this is an exploratory pilot,
not an untouched statistical holdout**. The same historical period was already
inspected while comparing local-filter scores 4, 5 and 6.

No order execution is present in these pilot files. Other legacy EA code in the
repository is separate; this is not a statement that the entire repository is
read-only. Keep the live monitor stopped during this experiment to avoid its
separate API consumption.

## Run from Windows PowerShell, inside ai_trader

```powershell
python -m unittest discover -s tests -v
python openai_holdout_validation.py --prepare
python openai_holdout_validation.py --run
```

`--prepare` (also the no-argument default) reads MT5, applies the local filter,
spaces candidate signals at least 20 bars apart, selects at most ten across the
later historical segment and saves their exact inputs and outcome references.
It does not call OpenAI. Indicator windows are recomputed to match the live
monitor's configured finite context. Development labels crossing the validation
boundary are purged, although this does NOT undo the prior inspection of the
validation period. The raw bar IDs are retained; inherited UTC offset estimates
are not independently verified by this pilot.

`--run` explicitly calls the **paid API** on the saved inputs. It does not reload
MT5 history, change the sample, or select a different model. The model and request
settings are frozen at preparation; `.env` changes afterward do not alter this
experiment. The API key itself is read at run time, and is never saved in the
snapshot, logs or repository. The official API endpoint is used.

```powershell
python openai_holdout_validation.py --report
notepad data\openai_pilot_report.md
```

`--report` regenerates the report locally without MT5 or API requests.

## Defaults and scope of cost controls

Existing `.env` files need not be replaced. Optional settings, used only while
preparing a NEW snapshot:

```text
AI_VALIDATION_BARS=5000
AI_VALIDATION_SCORE=5
AI_VALIDATION_DEV_FRACTION=0.70
AI_VALIDATION_MAX_CALLS=10
AI_VALIDATION_MAX_OUTPUT_TOKENS=2048
AI_VALIDATION_REASONING_EFFORT=low
```

The existing `OPENAI_MODEL` is retained. As verified on 2026-09-16, the official
GPT-5.6 Luna model page lists the model and the `low` reasoning option. This is
not an assertion about access permissions for every API account.

The pilot refuses call caps outside 1..10. Each attempt is committed to SQLite
**before** its HTTP request. SDK automatic retries are disabled with
`max_retries=0`; the client timeout is 45 seconds. Each request has an output-token
bound. These are request/token controls, **not a dollar spending cap**. Input
tokens, output tokens and the selected model determine usage charges. Timeouts
may leave a charge without a usable reply; missing usage is not zero usage.

## Resume and failures

Runtime files are local and covered by the repository's existing `data/` ignore:

```text
data/openai_pilot.sqlite3
data/openai_pilot_decisions.csv
data/openai_pilot_outcomes.csv
data/openai_pilot_report.md
```

These names do not overwrite the old `openai_holdout_*` reports.

Successful results are saved after each response. A refusal, incomplete answer,
malformed JSON or API exception is recorded separately and stops that invocation.
A crash after reservation leaves `STARTED`, meaning **uncertain**, not unattempted.
A later `--run` skips every reserved attempt, including failures/uncertain ones,
and can continue only the remaining unattempted samples. A completed rerun makes
zero new requests. A second `--prepare` reuses the same snapshot.

Do not delete the database to retry: that removes the local accounting and
anti-repeat protection. Counts cover this database/pilot only, not other programs,
API projects, independently prepared databases or historical attempts from the old
script. For 401/403/404/429, inspect the reported status and account/model/billing
configuration rather than repeatedly restarting. Full exception text and keys are
not printed. There is no silent model fallback or automatic quota purchase.

## Comparing on the same samples

All paired columns use the exact same samples with successful, validated replies:

- Local: the local direction's gross endpoint price change.
- AI (WAIT=0): the AI direction's price change, or zero gross return for no position.
- Agree-only: the local direction only when AI agrees; otherwise no position.

API failures are excluded with their missing count explicitly reported. They are
not fake `WAIT` signals or zero-return trades. The report includes the local
20-bar return on samples rejected with AI `WAIT`, and observed input/output tokens.
The CSV records the requested model, returned model and response ID.

Gross endpoint returns from the signal candle close are **not executable net P/L**.
They exclude spread, fees, slippage, financing, API costs and decision latency.
There is no TP/SL, lot sizing or account-equity simulation. No claim of independent
observations, calibrated confidence, or profitable trading is made. Ten samples
are useful for pipeline debugging only. Success-only comparisons can be biased
if failures are nonrandom. Historical payloads exclude future bars, but this does
not certify absence of pretraining contamination. Reserve genuinely unseen data
and forward testing for later evaluation; do not tune repeatedly on this sample.

## Test evidence

The focused offline suite for this change ran 23 tests: 20 new pilot tests plus
the three existing split tests. All passed with mocked API responses and temporary
SQLite files. No paid request or real MT5 connection was made in those tests.
The complete existing Windows/MT5 suite must also be run on the development PC.

## Official references

- https://developers.openai.com/api/docs/models/gpt-5.6-luna
- https://developers.openai.com/api/reference/python/#retries
- https://developers.openai.com/api/docs/guides/structured-outputs

These document model/API behavior, not evidence of trading profitability.
