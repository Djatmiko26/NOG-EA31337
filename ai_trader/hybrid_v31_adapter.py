"""Offline V3.1 adapter: existing evaluator/validator/encoder, new typed boundary.

No clients, feed connections, sockets, ledger constructors or publication calls.
Callers supply already obtained AI evidence and an explicit risk-manager result.
Legacy run_one and its frozen ledgers are not rewired by this module.
"""
from __future__ import annotations

from dataclasses import replace

import cash_demo
import cash_risk
import ensemble_weighted_v31 as weighted
import live_ai_core as core
from hybrid_contract import (
    AIConfirmation, DecisionEvidence, EnsembleEvidence, FinalDecision, Freshness,
    MarketIdentity, RiskDecision, SourceVersion, StrategyEvidence, StrategyVote,
    RegimeEvidence,
)
from hybrid_decision import reduce_final_decision


def market_identity(observation: core.Observation) -> MarketIdentity:
    observation.validate()
    return MarketIdentity(observation.source_id, observation.closed,
                          observation.forming, observation.point)


def risk_from_cash_plan(plan: cash_risk.CashPlan | None, market: MarketIdentity,
                        direction: str, *, allowed: bool, reason_code: str) -> RiskDecision:
    """Copy a risk manager's result, never calculate/enlarge a plan from AI scores.

The caller must supply actual risk approval. Merely constructing a CashPlan is
not approval; this mapping does not verify broker/account/fee state. Existing
broker_plan and the EA retain those responsibilities. None is always a veto.
"""
    if type(allowed) is not bool:
        raise ValueError('RISK_APPROVAL_BOOLEAN_REQUIRED')
    if plan is None:
        return RiskDecision(market, direction, False, 'RISK_PLAN_MISSING',
                            plan_source=cash_risk.VERSION)
    if type(plan) is not cash_risk.CashPlan:
        raise ValueError('CASH_PLAN_REQUIRED')
    # Guard the copied result with existing ceilings, not a new sizing policy.
    if allowed and (plan.stressed_loss > cash_risk.LOSS_USD + 1e-8
                    or plan.stressed_profit < cash_risk.PROFIT_USD - 1e-8
                    or plan.volume > cash_risk.MAX_LOT + 1e-12):
        allowed, reason_code = False, 'CASH_PLAN_OUTSIDE_EXISTING_LIMITS'
    return RiskDecision(market, direction, allowed, reason_code,
                        plan.stressed_loss, plan.stressed_profit, plan.volume,
                        plan.sl, plan.tp, cash_risk.VERSION)


def ai_from_v31(result: dict | None, market: MarketIdentity) -> AIConfirmation | None:
    """Validate the existing result schema; no SDK call, model or prompt change."""
    if result is None:
        return None
    if not isinstance(result, dict):
        return AIConfirmation(market, 'WAIT', 0, 'INVALID', 'UNCLEAR', 'INVALID_AI_RESULT')
    status = result.get('status')
    if status != 'SUCCESS':
        if status not in ('ERROR', 'REFUSED', 'INVALID', 'INCOMPLETE'):
            status = 'INVALID'
        return AIConfirmation(market, 'WAIT', 0, status, 'UNCLEAR', 'AI_' + status)
    try:
        signal = core.validate_signal(result.get('signal'))
    except (ValueError, TypeError):
        return AIConfirmation(market, 'WAIT', 0, 'INVALID', 'UNCLEAR', 'INVALID_AI_SIGNAL')
    return AIConfirmation(market, signal['action'], signal['confidence'], 'SUCCESS',
                          signal['market_regime'], signal['reason'])


def freshness_from_v31(before: core.Observation, after: core.Observation, *,
                       atr: float, event_mono: float, event_wall: float,
                       end_mono: float, end_wall: float, receiver_ready: bool) -> Freshness:
    """Pure equivalent of V3.1's post-analysis publication recheck.

Receiver readiness is measured by the caller, not assumed. This does not replace
TransitionGate or the pre-API 8-second gate; no API is called by this adapter.
"""
    before.validate()
    after.validate()
    atr = core.number(atr)
    event_mono, event_wall, end_mono, end_wall = map(
        core.number, (event_mono, event_wall, end_mono, end_wall))
    elapsed = end_mono - event_mono
    fresh = (atr > 0 and 0 <= elapsed <= 40
             and abs((end_wall - event_wall) - elapsed) <= 3
             and after.tick_msc >= before.tick_msc
             and not (after.tick_msc == before.tick_msc and elapsed > 5)
             and (after.ask - after.bid) / atr <= core.MAX_SPREAD_ATR)
    return Freshness(fresh, (after.source_id, after.point) == (before.source_id, before.point),
                     (after.closed, after.forming) == (before.closed, before.forming), receiver_ready)


def adapt_v31(local: weighted.WeightedDecision, market: MarketIdentity, *,
              ai_result: dict | None, risk: RiskDecision, freshness: Freshness,
              sources: tuple[SourceVersion, ...]) -> FinalDecision:
    """Map the authoritative V3.1 decision; do not rescore or require vote unanimity."""
    if type(local) is not weighted.WeightedDecision:
        raise ValueError('V31_WEIGHTED_DECISION_REQUIRED')
    votes = tuple(StrategyVote(v.name, v.signal, v.weight, v.strength, v.reason) for v in local.votes)
    strategy = StrategyEvidence(weighted.VERSION, local.action,
                                'LOCAL_WAIT' if local.action == 'WAIT' else 'LOCAL_DIRECTION', votes)
    direction = {'BULL_TREND': 'BUY', 'BEAR_TREND': 'SELL'}.get(local.regime, 'WAIT')
    regime = RegimeEvidence(local.regime, local.regime != 'RANGE', direction,
                           'REGIME_BLOCK' if local.regime == 'RANGE' else 'REGIME_EVALUATED')
    ensemble = EnsembleEvidence(local.action, local.buy_score, local.sell_score, local.margin,
                                local.core_count, local.ai_min_confidence, local.suggested_rr,
                                local.vetoes > 0, local.reason)
    evidence = DecisionEvidence(market, strategy, regime, ensemble, ai_from_v31(ai_result, market),
                                risk, freshness, sources, ai_required=True)
    return reduce_final_decision(evidence)


def evaluate_v31(payload: dict, before: core.Observation, *, ai_result: dict | None,
                 risk: RiskDecision, freshness: Freshness,
                 sources: tuple[SourceVersion, ...]) -> FinalDecision:
    """New offline entry point; same V3.1 evaluator, mandatory explicit risk input.

Payload must be the same market snapshot supplied independently to the AI.
No local direction, score, risk, or research RR is added to that payload.
"""
    market = market_identity(before)
    quote = payload.get('current_quote', {})
    candles = payload.get('candles', [])
    if (payload.get('symbol'), payload.get('timeframe'), quote.get('point')) != (
            market.symbol, market.timeframe, market.point):
        freshness = replace(freshness, source_unchanged=False)
    if (payload.get('latest_closed_bar_raw') != market.closed_bar
            or not candles or candles[-1].get('bar_id_raw') != market.closed_bar):
        freshness = replace(freshness, bar_unchanged=False)
    if (quote.get('bid'), quote.get('ask'), quote.get('tick_time_msc_raw')) != (
            before.bid, before.ask, before.tick_msc):
        freshness = replace(freshness, market_fresh=False)
    local = weighted.evaluate(payload)
    return adapt_v31(local, market, ai_result=ai_result, risk=risk,
                     freshness=freshness, sources=sources)


def preview_packet(decision: FinalDecision, *, sid: str, issued: int, expiry: int,
                   login: int, atr: float, bid: float, ask: float) -> bytes | None:
    """Encode one existing PREVIEW packet; no transport and no order mode option.

Cash plan values and suggested RR are deliberately not wire fields. The old EA
continues to compute/recheck its own exact plan; typed risk is not an EA override.
"""
    if type(decision) is not FinalDecision or decision != reduce_final_decision(decision.evidence):
        raise ValueError('REDUCED_FINAL_DECISION_REQUIRED')
    if not decision.evidence.ai_required:
        raise ValueError('V31_REQUIRES_AI_CONFIRMATION')
    if decision.reason_code not in ('CONFIRMED_BUY', 'CONFIRMED_SELL', 'AI_WAIT',
                                    'AI_DISAGREE', 'AI_LOW_CONFIDENCE'):
        return None
    return cash_demo.packet('PREVIEW', sid, decision.action, issued, expiry,
                            decision.evidence.market.closed_bar, login, atr, bid, ask)
