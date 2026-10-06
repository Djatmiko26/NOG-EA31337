"""Pure final-decision reducer. No fallback direction, I/O or broker math."""
from hybrid_contract import DecisionEvidence, FinalDecision


def reduce_final_decision(evidence: DecisionEvidence) -> FinalDecision:
    if type(evidence) is not DecisionEvidence:
        raise ValueError('DECISION_EVIDENCE_REQUIRED')
    e = evidence
    def wait(reason):
        return FinalDecision('WAIT', reason, e)

    # Approval from AI or strategy can never override the risk manager.
    if not e.risk.allowed:
        return wait('RISK_VETO')
    if e.risk.market != e.market:
        return wait('RISK_IDENTITY_MISMATCH')
    if not e.freshness.source_unchanged:
        return wait('SOURCE_CHANGED')
    if not e.freshness.bar_unchanged:
        return wait('BAR_CHANGED')
    if not e.freshness.market_fresh:
        return wait('STALE_MARKET')
    if not e.freshness.receiver_ready:
        return wait('RECEIVER_NOT_READY')
    if e.ensemble.execution_veto or any(v.action == 'VETO' for v in e.strategy.votes):
        return wait('EXECUTION_VETO')
    if not e.regime.entry_allowed or e.regime.regime == 'RANGE':
        return wait('REGIME_BLOCK')
    if e.strategy.action == 'WAIT' or e.ensemble.action == 'WAIT':
        return wait('LOCAL_WAIT')
    if e.strategy.action != e.ensemble.action:
        return wait('ENSEMBLE_CONFLICT')
    action = e.ensemble.action
    regime_direction = {'BULL_TREND': 'BUY', 'BEAR_TREND': 'SELL'}.get(e.regime.regime, 'WAIT')
    if any(d not in ('WAIT', action) for d in (e.regime.direction, regime_direction)):
        return wait('REGIME_BLOCK')
    if e.risk.direction != action:
        return wait('RISK_DIRECTION_CONFLICT')
    if e.ai is None:
        if e.ai_required:
            return wait('AI_MISSING')
    else:
        if e.ai.market != e.market:
            return wait('AI_IDENTITY_MISMATCH')
        if e.ai.status != 'SUCCESS':
            return wait('AI_' + e.ai.status)
        if e.ai.action == 'WAIT':
            return wait('AI_WAIT')
        if e.ai.action != action:
            return wait('AI_DISAGREE')
        if e.ai.confidence < e.ensemble.ai_min_confidence:
            return wait('AI_LOW_CONFIDENCE')
    return FinalDecision(action, 'CONFIRMED_' + action, e)
