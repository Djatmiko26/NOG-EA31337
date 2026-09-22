"""Table-driven hybrid contract/reducer safety tests; synthetic immutable inputs."""
import ast
from dataclasses import FrozenInstanceError, replace
import itertools
import json
from pathlib import Path
import unittest

import hybrid_contract as c
from hybrid_decision import reduce_final_decision


def evidence(action='BUY'):
    market = c.MarketIdentity('a' * 64, 1_800_000_000 - 300, 1_800_000_000, .01)
    risk = c.RiskDecision(market, action, True, 'PLAN_APPROVED', 10., 10., .01,
                          4200. if action == 'BUY' else 4400.,
                          4400. if action == 'BUY' else 4200., 'synthetic')
    return c.DecisionEvidence(
        market, c.StrategyEvidence('synthetic', action, 'LOCAL_DIRECTION'),
        c.RegimeEvidence('BULL_TREND' if action == 'BUY' else 'BEAR_TREND', True, action, 'REGIME_EVALUATED'),
        c.EnsembleEvidence(action, 8. if action == 'BUY' else 0., 8. if action == 'SELL' else 0.,
                           8., 4, 70, 2., False, 'synthetic'),
        c.AIConfirmation(market, action, 80, 'SUCCESS', 'UNCLEAR', 'independent synthetic'),
        risk, c.Freshness(True, True, True, True),
        (c.SourceVersion('synthetic', 'v1', 'b' * 64),),
    )


class ReducerTests(unittest.TestCase):
    def test_decision_table(self):
        buy, sell = evidence(), evidence('SELL')
        rows = [
            (buy, 'BUY', 'CONFIRMED_BUY'),
            (sell, 'SELL', 'CONFIRMED_SELL'),
            (replace(buy, ai=replace(buy.ai, action='SELL')), 'WAIT', 'AI_DISAGREE'),
            (replace(sell, ai=replace(sell.ai, action='WAIT')), 'WAIT', 'AI_WAIT'),
            (replace(buy, risk=replace(buy.risk, allowed=False)), 'WAIT', 'RISK_VETO'),
            (replace(buy, freshness=replace(buy.freshness, market_fresh=False)), 'WAIT', 'STALE_MARKET'),
            (replace(buy, freshness=replace(buy.freshness, source_unchanged=False)), 'WAIT', 'SOURCE_CHANGED'),
            (replace(buy, freshness=replace(buy.freshness, bar_unchanged=False)), 'WAIT', 'BAR_CHANGED'),
            (replace(buy, freshness=replace(buy.freshness, receiver_ready=False)), 'WAIT', 'RECEIVER_NOT_READY'),
            (replace(buy, ai=replace(buy.ai, confidence=69)), 'WAIT', 'AI_LOW_CONFIDENCE'),
            (replace(buy, regime=replace(buy.regime, regime='RANGE')), 'WAIT', 'REGIME_BLOCK'),
            (replace(buy, regime=replace(buy.regime, entry_allowed=False)), 'WAIT', 'REGIME_BLOCK'),
            (replace(buy, ensemble=replace(buy.ensemble, execution_veto=True)), 'WAIT', 'EXECUTION_VETO'),
            (replace(buy, ai=None), 'WAIT', 'AI_MISSING'),
            (replace(buy, strategy=replace(buy.strategy, action='WAIT')), 'WAIT', 'LOCAL_WAIT'),
            (replace(buy, strategy=replace(buy.strategy, action='SELL')), 'WAIT', 'ENSEMBLE_CONFLICT'),
            (replace(buy, risk=sell.risk), 'WAIT', 'RISK_DIRECTION_CONFLICT'),
        ]
        for item, action, reason in rows:
            with self.subTest(reason=reason):
                final = reduce_final_decision(item)
                self.assertEqual((final.action, final.reason_code), (action, reason))
                self.assertIs(final.evidence.risk, item.risk)
                self.assertEqual(final.mode, 'PREVIEW')

    def test_risk_veto_never_directional(self):
        base = evidence()
        for local, aggregate, ai, conf in itertools.product(c.ACTIONS, c.ACTIONS, c.ACTIONS, (0, 69, 70, 100)):
            item = replace(base, strategy=replace(base.strategy, action=local),
                           ensemble=replace(base.ensemble, action=aggregate),
                           ai=replace(base.ai, action=ai, confidence=conf), risk=replace(base.risk, allowed=False))
            final = reduce_final_decision(item)
            self.assertEqual((final.action, final.reason_code), ('WAIT', 'RISK_VETO'))

    def test_each_freshness_failure_never_directional(self):
        for direction, flag, conf in itertools.product(('BUY', 'SELL'),
                ('market_fresh', 'source_unchanged', 'bar_unchanged', 'receiver_ready'), (0, 70, 100)):
            base = evidence(direction)
            item = replace(base, freshness=replace(base.freshness, **{flag: False}),
                           ai=replace(base.ai, confidence=conf))
            self.assertEqual(reduce_final_decision(item).action, 'WAIT')

    def test_conflicting_stage_directions_never_directional(self):
        for local, aggregate, ai in itertools.product(('BUY', 'SELL'), repeat=3):
            base = evidence(local)
            item = replace(base, ensemble=replace(base.ensemble, action=aggregate), ai=replace(base.ai, action=ai))
            final = reduce_final_decision(item)
            self.assertIn(final.action, c.ACTIONS)
            if len({local, aggregate, ai}) > 1:
                self.assertEqual(final.action, 'WAIT')

    def test_risk_and_ai_market_binding(self):
        base = evidence()
        other = replace(base.market, source_id='c' * 64)
        for name in ('risk', 'ai'):
            item = replace(base, **{name: replace(getattr(base, name), market=other)})
            self.assertEqual(reduce_final_decision(item).action, 'WAIT')

    def test_failed_ai_never_becomes_fallback(self):
        base = evidence()
        for status in ('ERROR', 'INVALID', 'REFUSED', 'INCOMPLETE'):
            item = replace(base, ai=replace(base.ai, status=status, action='BUY', confidence=100))
            self.assertEqual(reduce_final_decision(item).reason_code, 'AI_' + status)

    def test_optional_ai_absence_must_be_explicit(self):
        base = evidence()
        self.assertEqual(reduce_final_decision(replace(base, ai=None)).action, 'WAIT')
        self.assertEqual(reduce_final_decision(replace(base, ai=None, ai_required=False)).action, 'BUY')
        # Optional confirmation never means ignoring a supplied disagreement.
        self.assertEqual(reduce_final_decision(replace(base, ai_required=False,
                         ai=replace(base.ai, action='SELL'))).action, 'WAIT')

    def test_confidence_threshold_boundaries(self):
        for threshold in (70, 75):
            base = evidence()
            for score in (threshold - 1, threshold, threshold + 1):
                item = replace(base, ensemble=replace(base.ensemble, ai_min_confidence=threshold),
                               ai=replace(base.ai, confidence=score))
                self.assertEqual(reduce_final_decision(item).action, 'BUY' if score >= threshold else 'WAIT')

    def test_execution_vote_veto_cannot_be_hidden_by_aggregate(self):
        base = evidence()
        vote = c.StrategyVote('execution_quality', 'VETO', 0., 1., 'synthetic')
        item = replace(base, strategy=replace(base.strategy, votes=(vote,)))
        self.assertEqual(reduce_final_decision(item).reason_code, 'EXECUTION_VETO')


class ContractTests(unittest.TestCase):
    def test_immutable_nested_evidence(self):
        base = evidence()
        for obj, name, value in ((base, 'ai_required', False), (base.risk, 'lot', .1),
                                  (base.ensemble, 'suggested_rr', 10.), (base.market, 'source_id', 'c' * 64)):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, name, value)
        with self.assertRaises(ValueError):replace(base, sources=list(base.sources))
        with self.assertRaises(ValueError):replace(base.strategy, votes=[])

    def test_serialization_is_deterministic_and_detached(self):
        base = evidence()
        first = reduce_final_decision(base)
        document = first.to_dict()
        document['evidence']['risk']['lot'] = 9.
        self.assertEqual(first.evidence.risk.lot, .01)
        self.assertEqual(first.to_json(), reduce_final_decision(base).to_json())
        saved = json.loads(first.to_json())
        self.assertEqual(saved['evidence']['sources'][0]['sha256'], 'b' * 64)
        self.assertEqual(saved['evidence']['ai']['confidence'], 80)
        self.assertEqual(saved['evidence']['risk']['reason_code'], 'PLAN_APPROVED')
        self.assertEqual(saved['action'], 'BUY')

    def test_nonfinite_and_boolean_numbers_rejected(self):
        base = evidence()
        for name, value in itertools.product(('planned_risk_usd', 'planned_reward_usd', 'lot', 'sl', 'tp'),
                                             (float('nan'), float('inf'), -1., True)):
            for allowed in (False, True):
                with self.assertRaises(ValueError):replace(base.risk, allowed=allowed, **{name: value})
        for value in (True, 101, -1, 70.):
            with self.assertRaises(ValueError):replace(base.ai, confidence=value)

    def test_no_implicit_risk_or_freshness_approval(self):
        base = evidence()
        with self.assertRaises(ValueError):
            c.RiskDecision(base.market, 'BUY', True, 'UNASSESSED')
        self.assertFalse(c.Freshness().ready)
        with self.assertRaises(ValueError):replace(base.freshness, market_fresh=1)
        with self.assertRaises(ValueError):replace(base.risk, allowed=1)

    def test_final_action_domain_and_hard_veto_constructor(self):
        base = evidence()
        for bad in ('VETO', 'HOLD', '', None, 1):
            with self.assertRaises(ValueError):c.FinalDecision(bad, 'TEST', base)
        denied = replace(base, risk=replace(base.risk, allowed=False))
        with self.assertRaises(ValueError):c.FinalDecision('BUY', 'TEST', denied)
        with self.assertRaises(ValueError):c.FinalDecision('BUY', 'TEST', replace(base, freshness=c.Freshness()))

    def test_contract_and_reducer_imports_are_pure(self):
        root = Path(__file__).resolve().parents[1]
        allowed = {'__future__', 'dataclasses', 'json', 'math', 're', 'typing', 'hybrid_contract'}
        for name in ('hybrid_contract.py', 'hybrid_decision.py'):
            tree = ast.parse((root / name).read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertTrue(all(alias.name in allowed for alias in node.names))
                elif isinstance(node, ast.ImportFrom):
                    self.assertIn(node.module, allowed)
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, ('open', 'eval', 'exec', '__import__'))


if __name__ == '__main__':
    unittest.main()
