# Test and data provenance

Recorded 2026-09-22 for Milestone 1 on `openai-trader-v1`.
This inventory is historical evidence, not a compatibility certification.
No ledger or journal was migrated, reset, or rewritten for this document.

## Existing SQLite databases

All paths below are relative to `ai_trader/data/`. Counts are from the read-only audit.
`SUCCESS` describes a recorded attempt/event, not an executed order or profitability.

| Database | Classification | Recorded state |
| --- | --- | --- |
| cash_demo.sqlite3 | Legacy cash V1 pilot | 4 SUCCESS attempts |
| cash_pilot_v2.sqlite3 | Cash V2 preview pilot | 4 SUCCESS attempts |
| cash_ensemble_v3.sqlite3 | V3 ensemble research pilot | 1 SUCCESS event |
| cash_ensemble_v31.sqlite3 | V3.1 weighted ensemble research pilot | Metadata exists; 0 events |
| live_ai_bridge.sqlite3 | Legacy dry-run pilot | 1 ERROR attempt, HTTP 429 |
| live_ai_bridge_after_429.sqlite3 | Separate legacy retry pilot | 1 SUCCESS attempt |
| openai_pilot.sqlite3 | Exploratory historical research pilot | 10 SUCCESS attempts |
| openai_blind.sqlite3 | Blind comparison on existing research samples | 10 SUCCESS attempts |

The audit returned `integrity_check=ok` for all eight databases. SQLite structural
integrity does not establish semantic validity or compatibility with current code.
CSV journals, replay outputs and snapshots under `data/` remain historical evidence.

## Known source-hash mismatches

Comparing stored implementation hashes to current source bytes found:

- cash_demo.sqlite3: cash_demo.py, cash_risk.py, live_ai_bridge.py, live_ai_core.py.
- live_ai_bridge.sqlite3: live_ai_bridge.py, live_ai_core.py.
- live_ai_bridge_after_429.sqlite3: live_ai_bridge.py, live_ai_core.py.

No mismatch was found in the stored implementation maps for cash V2, V3 and V3.1
at the audit snapshot. This does NOT establish compatibility of any old ledger
with current code, configuration, model, terminal, receiver binary or broker state.
No hash was replaced to bypass a frozen-settings check.

## Research-period provenance

`execution_baseline_snapshot.json` contains 5,000 bars, raw epochs
1787233500 through 1789566600.
`execution_oos_50000.json` contains 50,000 bars, raw epochs
1767010500 through 1789771800: it overlaps the baseline and is NOT a clean holdout.
`execution_oos_older_50000.json` contains 50,000 earlier bars, raw epochs
1744603500 through 1767010200. Temporal separation alone does not certify an
unseen holdout or rule out prior use during strategy development.
The historical OpenAI pilot is exploratory, not a new independent holdout.

## Journals and verification limits

Source references separate terminal journals under `NOG_CashDemo`,
`NOG_GuardedDemo`, and Common Files `NOG_EMAPullback_Trial`.
Their actual terminal contents were not inspected or modified by this milestone.

Full MQL5 compilation with MetaEditor has NOT been verified. Existing `.ex5`
files are not proof that current source compiles or matches those binaries.
`g++` was unavailable during the audit; `tests/check_cash_math.py` could not start
its compiler. Cash/risk/EMA C++ harnesses have not been validated in this environment.
No broker execution or new strategy-performance validation was performed.
