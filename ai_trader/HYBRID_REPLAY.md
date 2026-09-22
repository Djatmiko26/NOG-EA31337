# Milestone 3: reproducible offline replay

Research only. Default AI mode is NONE and final evidence stays PREVIEW. There is no transport, broker connection, order, live-runner integration, parameter tuning, model change or historical-ledger write in this pipeline. All existing strategy, contract, risk and prompt files remain unchanged.

## Architecture

Local closed-bar JSON -> replay_snapshot -> replay_split -> hybrid_replay -> unchanged V3.1 evaluator -> unchanged hybrid adapter/reducer -> replay_outcomes -> immutable evidence/report. replay_records supplies matched recorded AI and risk evidence. replay_holdout reserves final evaluation before running it. replay_common supplies canonical serialization, strict JSON and exclusive file creation.

The replay core takes an immutable Snapshot, frozen ReplaySettings, frozen SourceVersion tuple and supplied evidence. It does not read a database or fetch prices. File reads, source hashing, reservations and output creation are CLI boundaries. Historical scripts are audited, not invoked. Full artifact inventory and overlap ranges are in REPLAY_DATA_INVENTORY.md and REPLAY_DATA_INVENTORY.json.

## Snapshot v1

Envelope: `{"manifest": {...}, "bars": [...]}`. Each bar has exactly `time, open, high, low, close, spread, tick_volume`. Prices are BID OHLC; spread is an integer point count. Positive finite OHLC, ordered unique raw times and `time + 300 <= closed_before_raw` are mandatory. Gaps are permitted for closed-market periods; horizons count bars, not elapsed minutes. Closedness is relative to the supplied source cutoff, not independently attested terminal state.

Manifest fields:

| Field | Meaning |
| --- | --- |
| schema_version / generator_version | closed-m5-snapshot-v1 |
| snapshot_id | SHA256 of complete manifest before adding this ID |
| symbol / timeframe | XAUUSD / M5 |
| source_identity_hash | Existing anonymized source identity; no raw login/server |
| first_bar_raw / last_bar_raw / bar_count | Bounds and count of stored bars |
| point / price_basis / time_basis | Point size, BID, raw source epochs not independently verified UTC |
| content_sha256 | Canonical OHLC/spread/volume/time array hash |
| generator_sha256 / created_at | Generator source hash and caller-supplied timezone-aware creation time |
| settings / settings_sha256 | Detached immutable settings object and hash |
| closed_before_raw | Exclusive forming-bar boundary supplied by source provenance |
| prior_use | UNKNOWN, EXPLORED, DECLARED_UNSEEN, or SYNTHETIC_TEST |

Canonical bytes are UTF-8 sorted compact JSON, no NaN/Infinity, with one final LF. Hashes cover those bytes. JSON layout/CRLF changes alone do not change the canonical content hash. Duplicate JSON keys are rejected. Verification recomputes the entire manifest, so changed bars/settings/counts fail. Exclusive `xb` creation refuses every existing target, including an empty file. Output under ai_trader/data or .git is forbidden. No existing artifact is repaired.

Local-only CLI examples (replace placeholders with real provenance; NEW paths must not exist):

```powershell
python -B ai_trader/replay_snapshot.py --create LOCAL_CLOSED_BARS.json --output NEW_SNAPSHOT.json --source-hash SOURCE_SHA256 --point 0.01 --settings LOCAL_SETTINGS.json --created-at 2026-09-22T00:00:00+00:00 --closed-before-raw RAW_FORMING_BAR --prior-use EXPLORED
python -B ai_trader/replay_snapshot.py --verify NEW_SNAPSHOT.json
python -B ai_trader/replay_snapshot.py --describe NEW_SNAPSHOT.json
python -B ai_trader/hybrid_replay.py --snapshot NEW_SNAPSHOT.json --output NEW_DEV_RUN --phase DEVELOPMENT
```

The local import convenience accepts a bar list or a legacy envelope's `body.bars`; it hashes the imported content anew. It does not authenticate the legacy envelope or establish compatibility with its old source. Verify legacy provenance separately before making any claim about it.

## Time splits and use rules

Default horizon=20, purge=20, context=120. Remove two purge gaps, then allocate remaining bars 60% DEVELOPMENT, 20% VALIDATION and the remainder HOLDOUT. Each segment needs at least context+horizon bars. No shuffle. Each decision uses 120 closed bars entirely inside its own segment; future labels also remain inside that segment. The next segment cannot reuse the previous segment's outcomes as feature history. Purge below horizon, overlapping segments and out-of-bounds windows fail.

For an 800-bar synthetic snapshot: DEVELOPMENT [0,456), gap [456,476), VALIDATION [476,628), gap [628,648), HOLDOUT [648,800). Eligible events are 317, 13 and 13 respectively. Indices are zero-based with exclusive ends.

- DEVELOPMENT phase exposes development decisions only. Tuning is permitted only here.
- SELECTION (default) exposes development+validation decisions. Select a configuration here, then freeze its settings/source hashes.
- FINAL exposes holdout decisions only and requires an exact reservation matching snapshot, settings and versions. It cannot run on UNKNOWN/EXPLORED data. DECLARED_UNSEEN additionally must not overlap the audited exposure inventory. A declaration is a provenance claim, not mathematical proof of no prior use.

FINAL CLI requires a persistent `--holdout-registry NEW_OR_EXISTING_REGISTRY_DIRECTORY` and `--holdout-declaration "provenance and frozen selection"`. Reserve-before-run is fail-closed: errors also consume the reservation. Existing overlapping reservations reject repeated evaluation, including changing settings or using another snapshot/source identity for the same market interval. Keep this registry across runs. Do not delete it, move to a fresh registry or retune after FINAL. Final reports may be read again; "read once" means one final evaluation, not prohibiting file-integrity verification or report viewing.

The pure evaluator accepts a prevalidated permit; it does not perform registry I/O. The CLI is the supported workflow boundary for one-use enforcement. Registry provenance is single-operator workflow protection, not adversarial authentication or a distributed concurrent scheduler. No optimizer exists, and the software cannot prevent a person from manually analyzing already-visible data outside this workflow.

SYNTHETIC_TEST requires explicit `--allow-synthetic` for final reservation or recorded synthetic evidence, and never establishes a clean market holdout. There is currently no certified clean historical holdout in the audited data. `execution_oos_50000.json` overlaps baseline and is NOT a clean holdout.

## AI and risk evidence

NONE never uses an AI result, even when a records file is supplied for risk receipts. AI-required paths consequently WAIT. RECORDED requires an existing evidence file; missing or mismatched records are unavailable. No generated AI result or current network confirmation is substituted.

Records envelope: `{"body":{"version":"bound-replay-records-v1","evidence_kind":"RECORDED_EVIDENCE|SYNTHETIC_TEST","records":[...]},"sha256":"canonical body hash"}`.

Each record contains unique `decision_bar_raw`, unique 32-hex `signal_id`, `origin_sha256`, `binding`, optional `ai_result` in the existing V3.1 result schema, and optional `risk`. Keep the actual original evidence identified by origin_sha256. The hash is a provenance reference; this loader does not authenticate signatures or auto-recover a missing original. Legacy records are not assumed compatible or automatically imported.

Exact binding fields: source_identity_hash, symbol, timeframe, bar_raw, settings_sha256, versions_sha256, feature_sha256. The settings hash includes snapshot settings, replay settings, unchanged strategy version, quote convention and context. Feature hash covers the actual closed-bar payload. Source versions cover every replay module plus the unchanged strategy/core/risk/contract/adapter dependencies. Future prices are excluded from the feature binding, while the run manifest separately identifies the entire snapshot.

The `risk` object repeats signal_id/binding/direction, fee_assumptions (commission_per_lot and fixed_fee), plan_version, status and kind. Approval additionally requires kind EXACT_CASH_PLAN_RECEIPT, exact cash_plan fields and canonical plan_sha256. No approval is calculated from hypothetical broker state. Missing fees, bad hash, invalid plan geometry, or mismatched signal/source/bar/direction/settings/version/fees makes risk unavailable. Existing hard caps are checked by the unchanged adapter.

| Offline status | Meaning and final behavior |
| --- | --- |
| APPROVED_RECORDED | Matched supplied exact receipt; other reducer vetoes still apply |
| REJECTED | Recorded rejection or existing adapter hard cap veto; WAIT |
| UNAVAILABLE | Missing or mismatched exact evidence/calculation; WAIT |

The wrapper stores status alongside the unchanged RiskDecision. Passing through recorded approval is not new broker authorization. Synthetic approvals exist only in explicitly labelled tests; mixing them with a market snapshot is rejected. Offline freshness flags describe the verified simulation window, not a real receiver. Evidence is tagged RESEARCH_ONLY_NO_TRANSPORT and no preview packet is produced.

## Outcomes, comparison and metrics

Decision quote proxy uses the latest closed BID close plus its spread; it never uses next-bar open/spread. Outcome prices use strictly future bars. BUY exits use BID; SELL exits use ASK approximated by each future bar's spread. TP/SL touched on the same bar is AMBIGUOUS, never an assumed win. Neither hit within the horizon is UNRESOLVED. WAIT is a separate status with no R, win or loss. Exact tick snapshots are not supported in v1.

TP R is recorded stressed_reward/stressed_risk; SL R=-1. These are receipt-based hypothetical net-risk labels, not reconstructed realized cash P&L. Win rate, expectancy R and average R use TP/SL resolved events only; expectancy and average R are equal under this empirical definition. All WAIT/AMBIGUOUS/UNRESOLVED counts remain visible. Ambiguous/unresolved interrupt a known losing streak; WAIT is skipped. Average resolution bars uses resolved TP/SL only.

Report stage counts can overlap: local_wait, AI wait/block (excluding local WAIT), risk veto/unavailable, final BUY/SELL/WAIT. Breakdowns cover final direction, regime, recorded confidence bucket and suggested RR. Confidence is not a calibrated probability. evidence.json retains local V3.1 action, legacy AI candidate, hybrid final action, reasons and frozen evidence. A previously directional candidate becoming WAIT is marked an intentional safety delta. Existing Milestone 2 oracle tests still verify the legacy live decision function independently; no live execution is run here.

Outputs in a NEW directory are evidence.json, report.md, run_manifest.json. The manifest hashes report and evidence plus snapshot/config/source identifiers. No current clock/UUID enters report generation: same supplied inputs yield identical bytes. This is an event study, not a portfolio simulator; hypothetical outcomes may overlap in time. There is no automatic profitability verdict.

## Verification and remaining work

Regression modules: test_replay_snapshot.py (snapshot/CLI/Windows handles/splits), test_replay_outcomes.py (price sides/denominators/holdout), test_hybrid_replay.py (unchanged evaluator/record binding/determinism/offline integration). All generated fixtures and output files use TemporaryDirectory. The isolated full suite protects all old data, blocks OpenAI/MT5/order calls and external network, and permits local loopback tests. A separate subprocess runs replay with site packages disabled and SDK imports/socket/SQLite/broker/AI calls blocked.

Historical hashes and known mismatches remain documented in TEST_AND_DATA_PROVENANCE.md; no compatibility conclusion is drawn. MQL5 full compile is not verified. g++ is unavailable, so C++ harnesses are not run. No broker order, fresh OpenAI call, live forward run, or real clean-holdout performance result was obtained.

Recommended Milestone 4: establish an independently retained source/evidence capture format and reserve genuinely unseen demo-market data; validate exact quote/fee/receipt provenance offline before adding any demo execution integration. Freeze selection policy first, retain the registry, and keep all execution wiring subject to a separate approval. Strategy tuning and live trading are outside this milestone.
