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

The dot may continue automatically only after `REVIEW_RESULT: APPROVED`.

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
