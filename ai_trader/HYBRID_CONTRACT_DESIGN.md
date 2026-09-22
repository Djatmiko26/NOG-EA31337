# Hybrid decision contract — Milestone 2 design

Design completed before implementation, 2026-09-22, branch `openai-trader-v1`.
Baseline: 354/354 isolated Python tests; data manifest SHA-256
`88f86ea46af478f61d8fa3bb92d77c9242dfb59c39e47f2372a6c55382fbd418`.

## Duplication audit and integration boundary

| Concern | Existing implementation | Decision |
| --- | --- | --- |
| Freshness/source/bar | V3 and V3.1 `_fresh_publish_recheck` are identical; cash_demo.analyse has similar checks plus account binding; live_ai_core/MT5Feed validate observations | New pure V3.1 adapter consumes observations, explicit clocks and receiver result; preserve 40s/3s/5s and spread checks. Do not migrate old runners. |
| AI validation | V2 calls cash_demo.analyse; V3/V3.1 duplicate agreement handling; all use live_ai_core.validate_signal | Reuse validator; reducer owns typed agreement outcome. Prompt/model/schema untouched. |
| Receiver readiness | Same readiness gates around analysis/publication, shared BridgeState and cash handler | An explicit boolean input; no socket, poll, callback or implicit readiness in reducer. |
| Final direction | V3 fixed confidence, V3.1 threshold from weighted decision; dictionaries mix candidate and publishability | One typed FinalDecision; stage conflict or veto always WAIT. |
| Risk handoff | cash_risk contains broker math; V3.1 runner does not receive a risk plan before packet generation; EA calculates/rechecks risk | Map an explicitly approved/rejected CashPlan into RiskDecision. Missing/unapproved plan cannot authorize a direction. Keep broker math and EA unchanged. |
| Packet | V2/V3/V3.1 reuse cash_demo.packet; same PREVIEW protocol | Adapter produces bytes using the existing encoder, never publishes or opens a socket. |
| Evidence | Repeated encode/asdict helpers, frozen per-version ledger schemas | New immutable evidence envelope with version/hash list and detached JSON; no DB constructor/migration/write. |
| Strategy/regime | V3 counts votes; V3.1 weights votes and detects regime; similar feature extraction is not identical policy | Call V3.1 evaluator unchanged. Minority votes are evidence, not stage-level direction conflicts. |

## Before and after

Before: V3.1 evaluator -> independent AI -> legacy direction -> freshness gate ->
PREVIEW packet -> EA risk plan/recheck. A withheld packet can still leave a legacy
`final_action=BUY/SELL` in the result dictionary.

After (additive, offline V3.1 adapter): unchanged evaluator -> typed evidence +
already obtained independent AI result + explicit risk approval + freshness ->
pure reducer -> ONE FinalDecision -> optional PREVIEW bytes -> existing EA recheck
when a separately authorized future caller actually transports the packet.

The old cash_ensemble_v31 CLI/ledger path remains byte-for-byte unchanged. The new
entry point is `hybrid_v31_adapter.evaluate_v31`; it is not automatically enabled
in `run_one`. Wiring a mandatory pre-publication risk plan into that old runner
would change its behavior and frozen source hash. No fake risk approval is used
to conceal this boundary. V1/V2/V3/V3.1 and their ledgers are preserved.

Equivalence means unchanged evaluator output, unchanged valid-market AI decision,
and identical PREVIEW packet bytes for matching authorized inputs. It does not
claim that a legacy pre-publication direction is already a post-risk final action:
stale/risk-veto inputs deliberately become WAIT in the new contract, with no
packet. This reconciles hard-veto semantics with preserving legacy execution.

## Contract

Frozen dataclasses: MarketIdentity; StrategyVote/StrategyEvidence;
RegimeEvidence; EnsembleEvidence; AIConfirmation; RiskDecision; Freshness;
SourceVersion; DecisionEvidence; FinalDecision. Nested evidence uses tuples and
frozen dataclasses. JSON is detached, versioned, deterministic and rejects NaN.

RiskDecision contains allowed, reason_code, planned_risk_usd,
planned_reward_usd, lot, sl, tp, plus market/direction binding and plan provenance.
The contract defines types and invariants, not broker math or risk limits.
No AI confidence or research suggested-RR parameter is accepted by the risk adapter.

Reducer priority: risk veto -> source/bar/staleness/receiver -> execution veto ->
regime block -> local WAIT -> stage direction conflict -> risk direction conflict
-> missing/failed/mismatched AI -> AI WAIT/disagreement/low confidence -> confirmed.
Optional AI absence is allowed only when the caller explicitly disables required
confirmation; the V3.1 adapter always requires it.

## Validation plan

Table-driven safety cases and exhaustive small-domain invariants; immutable and
strict JSON validation; mapping equality for all CashPlan amounts; actual legacy
process_event as an offline oracle with in-memory ledger and mocked AI/feed;
freshness boundary parity; unchanged legacy source hashes; no API/network/order or
SQLite writes. Run full isolated suite and AST/py_compile, compare data manifests
across the entire milestone, then git diff --check. No commit or push.

## Reducer decision table

The first applicable gate wins. The original risk reason remains in evidence
when the final reason is the general `RISK_VETO`.

| Input condition | Final action | Reason |
| --- | --- | --- |
| Risk rejected or missing plan | WAIT | RISK_VETO |
| Risk belongs to another market/bar | WAIT | RISK_IDENTITY_MISMATCH |
| Source/point changed | WAIT | SOURCE_CHANGED |
| Closed/forming bar changed | WAIT | BAR_CHANGED |
| Market stale | WAIT | STALE_MARKET |
| Receiver not ready | WAIT | RECEIVER_NOT_READY |
| Execution veto in aggregate or any module | WAIT | EXECUTION_VETO |
| RANGE, blocked regime, or regime direction conflict | WAIT | REGIME_BLOCK |
| Local setup or ensemble WAIT | WAIT | LOCAL_WAIT |
| Selected strategy and ensemble disagree | WAIT | ENSEMBLE_CONFLICT |
| Risk plan direction disagrees | WAIT | RISK_DIRECTION_CONFLICT |
| Required confirmation absent | WAIT | AI_MISSING |
| AI identity mismatch or failed response | WAIT | AI_IDENTITY_MISMATCH / AI_ERROR / AI_INVALID / AI_REFUSED / AI_INCOMPLETE |
| AI WAIT | WAIT | AI_WAIT |
| AI direction differs | WAIT | AI_DISAGREE |
| AI confidence below unchanged V3.1 threshold | WAIT | AI_LOW_CONFIDENCE |
| All gates pass for BUY / SELL | BUY / SELL | CONFIRMED_BUY / CONFIRMED_SELL |

## Verification completed

- Before: 354/354 Python tests passed.
- After: 380/380 passed, 0 failures/errors/skips; 26 new test methods.
- 65 Python files passed AST and py_compile (bytecode output in a temporary folder).
- 180 direction/regime/AI/confidence cases matched the unchanged legacy
  process_event final direction AND exact PREVIEW bytes, using a memory-only ledger.
- 15 post-analysis freshness boundary cases matched the legacy publication gate.
- Real evaluator fixtures matched votes, weights, strengths, scores, margin,
  core support, suggested RR and packet output; minority opposing votes retained.
- Explicit counterexample: legacy stale source can retain a BUY candidate while
  withholding its packet; the new typed final is WAIT and also has no packet.
- Risk mapping retained all plan values exactly. Confidence and research RR
  variations did not alter risk amounts, lot, SL/TP or packet bytes.
- Table cases, small-domain invariants, malformed/missing AI handling, evidence
  immutability, finite numbers and detached serialization passed.
- Every legacy tracked file remained unchanged, including all nine audited
  production modules, model/prompt/schema, risk limits and the test runner.
- Across the whole milestone: 36 data files, 40,693,340 bytes; all names, sizes and
  per-file SHA-256 values identical. Before and after canonical manifest SHA-256:
  `88f86ea46af478f61d8fa3bb92d77c9242dfb59c39e47f2372a6c55382fbd418`.
- Evidence directories from this run: `nog-test-audit-9u1os95d` (baseline) and
  `nog-test-audit-h4x5yxn8` (after), under the OS temporary directory.
- git diff --check passed; the six new untracked files were also checked using
  git diff --no-index --check. No files were staged, committed or pushed.

## Remaining boundaries and recommended Milestone 3

The adapter is offline and additive, not a replacement live runner. Risk approval,
fee/account checks, source hash capture and the association of an AI response with
its original market snapshot remain caller responsibilities. No new durable
journal, API budget manager, transport service, EA receipt ingestion or broker
verification was introduced. The adapter does not certify a caller-created plan
as a broker-approved plan. Existing EA risk rechecks remain mandatory.

MQL5 full compile, C++ harnesses (g++ unavailable), Linux execution and any broker
execution are not verified here. The existing 50k OOS snapshot overlapping the
baseline remains unsuitable as a clean holdout.

Recommended Milestone 3: immutable offline replay of the new contract with frozen
source/config/data manifests, purged development/validation boundaries, genuinely
unseen periods, and separately supplied deterministic risk evidence. Record typed
final evidence in a NEW research journal only after its schema is agreed; preserve
all prior ledgers. Explicit live-runner/EA integration is a later reviewed boundary,
not an automatic consequence of passing these offline contract tests.
