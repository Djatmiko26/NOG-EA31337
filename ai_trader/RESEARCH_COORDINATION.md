# NOG Autonomous Research Coordination

This branch and draft PR are the coordination channel between the NOG Trading AI dot and ChatGPT Work.

## Safety boundary

Research/backtest/replay only. Never:
- trade a real-money account;
- enable live/Algo Trading automatically;
- bypass a risk veto;
- increase risk, lot size, grid depth, or martingale exposure autonomously;
- open HOLDOUT without explicit human approval;
- delete/reset ledgers, journals, or historical evidence.

## Message protocol

### Dot -> reviewer

Post a PR comment using:

```
REVIEW_REQUIRED
EXPERIMENT_ID: <id>
STATUS: COMPLETE

HYPOTHESIS:
<one frozen hypothesis>

BASELINE:
<baseline>

CHANGES:
<what changed>

TESTS:
<test result>

DEVELOPMENT:
<metrics>

VALIDATION:
<metrics>

RISK:
<drawdown, exposure, tail-risk, hard-stops>

DECISION_PROPOSED:
PROMISING | NOT_PROMISING | INCONCLUSIVE

ARTIFACTS:
<commit/report paths>

REQUEST:
REVIEW
```

Then stop and wait for `REVIEW_RESULT`.

### Reviewer -> Dot

The reviewer posts:

```
REVIEW_RESULT: APPROVED | REJECTED | HUMAN_APPROVAL_REQUIRED

EXPERIMENT_ID: <id>

ASSESSMENT:
<reason>

NEXT_EXPERIMENT:
<exactly one next hypothesis, or NONE>

CONSTRAINTS:
<frozen constraints>
```

`REVIEW_RESULT` applies to the experiment that was just reviewed.

The dot may continue automatically when:
- `REVIEW_RESULT: APPROVED` and `NEXT_EXPERIMENT` is not `NONE`; or
- `REVIEW_RESULT: REJECTED` and the reviewer supplies one distinct, safe `NEXT_EXPERIMENT` that does not retune the rejected version.

The dot must stop when:
- `REVIEW_RESULT: HUMAN_APPROVAL_REQUIRED`; or
- `NEXT_EXPERIMENT: NONE`.

## Autonomous next-experiment rule

After rejecting an experiment, the reviewer may authorize exactly one new research-only hypothesis if it is justified by the evidence and stays inside the safety boundary.

The next hypothesis must:
- be materially distinct from the rejected experiment;
- not be a parameter sweep or rescue-tuning of the failed version;
- keep HOLDOUT closed;
- keep existing risk limits unchanged;
- avoid broker/demo/live order execution;
- avoid martingale, lot multiplier, or unlimited grid;
- preserve failed evidence and frozen conclusions;
- use DEVELOPMENT/VALIDATION only when those datasets are already exploratory;
- state one measurable acceptance/rejection criterion before execution.

Preferred research directions after a rejected strategy variant include:
- failure-mechanism diagnostics;
- regime or session dependence;
- entry-quality attribution;
- cost/spread sensitivity;
- execution-assumption sensitivity;
- baseline simplification or ablation.

Do not authorize a new experiment merely to keep the loop running.

If no evidence-supported, materially distinct, safe hypothesis exists, set:
`NEXT_EXPERIMENT: NONE`.

## Mandatory human gates

Stop and require the user before:
- any real-money action;
- first broker/demo order if not already explicitly authorized;
- any risk-limit increase;
- opening HOLDOUT;
- martingale or lot multiplier;
- deleting/modifying historical evidence;
- major architecture changes;
- credentials, payment, or external authorization.

## Git discipline

- Use experiment branches/commits for code changes.
- Do not push directly to protected/mainline branches.
- Do not merge this coordination PR.
- Preserve failed experiments and their evidence.
