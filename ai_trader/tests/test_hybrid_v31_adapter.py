"""Offline V3.1 equivalence: actual unchanged legacy process_event is the oracle."""
import copy
from dataclasses import replace
import itertools
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import cash_demo
import cash_ensemble_v31 as legacy
import cash_risk
import ensemble_weighted_v31 as weighted
import hybrid_contract as c
import hybrid_v31_adapter as adapter
import live_ai_core as core

FORMING = 1_800_000_000
EVENT_M, EVENT_W = 100., 1_790_000_000.
SID = '1' * 32
SOURCES = (c.SourceVersion('synthetic-fixture', 'v1', 'b' * 64),)


def fixture(direction='BUY', spread=.2):
    rows = []
    sign = 1 if direction == 'BUY' else -1
    for i in range(30):
        close = 4400. + sign * i * 1.1
        rows.append(dict(bar_id_raw=FORMING - (30 - i) * 300,
                         open=close-sign*.7, high=close+1, low=close-1, close=close,
                         tick_volume=100+i*2, ema20=close-sign*2, ema50=close-sign*5,
                         atr14=5., rsi14=62. if sign == 1 else 38.))
    bid = rows[-1]['close']
    before = core.Observation(FORMING, FORMING-300, FORMING*1000+1000, bid, bid+spread, .01, 'a'*64)
    payload = dict(symbol='XAUUSD', timeframe='M5', latest_closed_bar_raw=before.closed,
                   candles=rows, current_quote=dict(bid=bid, ask=bid+spread, point=.01,
                   tick_time_msc_raw=before.tick_msc, spread_price=(bid+spread)-bid))
    after = replace(before, tick_msc=before.tick_msc+2000)
    return before, after, payload


def ai_result(action='BUY', confidence=80):
    return dict(status='SUCCESS', signal=dict(action=action, confidence=confidence,
                market_regime='UNCLEAR', reason='synthetic independent confirmation'))


def local_decision(direction, regime):
    votes = tuple(weighted.Vote(name, direction, weight, 1., 'synthetic')
                  for name, weight in weighted.WEIGHTS.items())
    return weighted.aggregate_votes(votes + (weighted.Vote('execution_quality', 'WAIT', 0., 0., 'OK'),), regime)


def plan_for(before, direction):
    return cash_risk.make_plan(direction == 'BUY', before.bid, before.ask, 5., .01,
                              .1, .2, .01, .01, 100., 100., 100., 0., 0.)


def approved(before, direction):
    return adapter.risk_from_cash_plan(plan_for(before, direction), adapter.market_identity(before),
                                      direction, allowed=True, reason_code='PLAN_APPROVED')


def fresh(before, after, **changes):
    values = dict(atr=5., event_mono=EVENT_M, event_wall=EVENT_W,
                  end_mono=EVENT_M+2, end_wall=EVENT_W+2, receiver_ready=True)
    values.update(changes)
    return adapter.freshness_from_v31(before, after, **values)


class MemoryLedger:
    """Only the legacy process_event interface; no SQLite/file operations."""
    def reserve(self, bar, decision, payload):
        return SID

    def api_count(self):
        return 0

    def mark_api(self, bar):
        pass

    def finish(self, bar, result, status='SUCCESS'):
        self.result = copy.deepcopy(result)


def legacy_result(local, before, after, payload, ai):
    ledger, state = MemoryLedger(), Mock()
    state.receiver_ready.return_value = True
    implementation = NS(nondirectional_gate=core.nondirectional_gate,
                        request_analysis=Mock(return_value=copy.deepcopy(ai)),
                        validate_signal=core.validate_signal, MAX_SPREAD_ATR=core.MAX_SPREAD_ATR)
    account = NS(login=123)
    with patch.object(legacy.base, 'require_usd_demo', return_value=(after, account)), \
         patch.object(legacy.time, 'monotonic', return_value=EVENT_M+2), \
         patch.object(legacy.time, 'time', return_value=EVENT_W+2):
        result = legacy.process_event(ledger, state, None, implementation,
                                      NS(config=NS(model='synthetic')), account,
                                      before, payload, local, EVENT_M, EVENT_W)
    packet = state.publish.call_args.args[0] if state.publish.called else None
    return result, packet, implementation.request_analysis.call_count


def packet(final, before):
    return adapter.preview_packet(final, sid=SID, issued=int(EVENT_W+2), expiry=int(EVENT_W+32),
                                  login=123, atr=5., bid=before.bid, ask=before.ask)


class EquivalenceTests(unittest.TestCase):
    def test_180_legacy_action_and_packet_cases(self):
        cases = 0
        for direction, regime, ai_action, score in itertools.product(
                ('BUY', 'SELL'), c.REGIMES, c.ACTIONS, (0, 69, 70, 74, 75, 100)):
            before, after, payload = fixture(direction)
            local = local_decision(direction, regime)
            ai = ai_result(ai_action, score)
            with self.subTest(direction=direction, regime=regime, ai=ai_action, score=score):
                old, old_packet, calls = legacy_result(local, before, after, payload, ai)
                final = adapter.adapt_v31(local, adapter.market_identity(before), ai_result=ai,
                                         risk=approved(before, direction), freshness=fresh(before, after), sources=SOURCES)
                self.assertEqual(final.action, old['final_action'])
                self.assertEqual(packet(final, before), old_packet)
                self.assertEqual(calls, 0 if local.action == 'WAIT' else 1)
                self.assertEqual(final.evidence.ensemble.ai_min_confidence, local.ai_min_confidence)
            cases += 1
        self.assertEqual(cases, 180)

    def test_evaluator_entrypoint_preserves_inputs_votes_and_scores(self):
        for direction, spread in itertools.product(('BUY', 'SELL'), (.2, 1.)):
            before, after, payload = fixture(direction, spread)
            original = copy.deepcopy(payload)
            local = weighted.evaluate(payload)
            old, old_packet, _ = legacy_result(local, before, after, payload, ai_result(direction))
            final = adapter.evaluate_v31(payload, before, ai_result=ai_result(direction),
                                        risk=approved(before, direction), freshness=fresh(before, after), sources=SOURCES)
            self.assertEqual(payload, original)
            self.assertEqual(final.action, old['final_action'])
            self.assertEqual(packet(final, before), old_packet)
            e = final.evidence.ensemble
            self.assertEqual((e.buy_score, e.sell_score, e.margin, e.core_count, e.suggested_rr),
                             (local.buy_score, local.sell_score, local.margin, local.core_count, local.suggested_rr))
            self.assertEqual(tuple((v.name, v.action, v.weight, v.strength, v.reason) for v in final.evidence.strategy.votes),
                             tuple((v.name, v.signal, v.weight, v.strength, v.reason) for v in local.votes))

    def test_minor_opposing_votes_are_not_new_unanimity_rule(self):
        before, after, payload = fixture('SELL')
        votes = tuple(weighted.Vote(name, 'SELL' if name in weighted.CORE else 'BUY', weight, 1., 'synthetic')
                      for name, weight in weighted.WEIGHTS.items())
        local = weighted.aggregate_votes(votes + (weighted.Vote('execution_quality', 'WAIT', 0., 0., 'OK'),), 'BEAR_TREND')
        old, old_packet, _ = legacy_result(local, before, after, payload, ai_result('SELL'))
        final = adapter.adapt_v31(local, adapter.market_identity(before), ai_result=ai_result('SELL'),
                                 risk=approved(before, 'SELL'), freshness=fresh(before, after), sources=SOURCES)
        self.assertEqual(final.action, 'SELL')
        self.assertEqual(final.action, old['final_action'])
        self.assertEqual(packet(final, before), old_packet)

    def test_publication_freshness_boundary_parity(self):
        before, after, payload = fixture()
        cases = [
            (after, 2., 2., True), (after, 0., 0., True), (after, -1., -1., True),
            (after, 40., 40., True), (after, 40.001, 40.001, True),
            (after, 2., 5., True), (after, 2., 5.001, True),
            (before, 5., 5., True), (before, 5.001, 5.001, True),
            (replace(after, tick_msc=before.tick_msc-1), 2., 2., True),
            (replace(after, source_id='c'*64), 2., 2., True),
            (replace(after, point=.1), 2., 2., True),
            (replace(after, forming=FORMING+300, closed=FORMING, tick_msc=(FORMING+300)*1000+1000), 2., 2., True),
            (replace(after, ask=before.bid+1.), 2., 2., True), (after, 2., 2., False),
        ]
        for obs, elapsed, wall_elapsed, ready in cases:
            with self.subTest(obs=obs, elapsed=elapsed, wall_elapsed=wall_elapsed, ready=ready):
                state = Mock(); state.receiver_ready.return_value = ready
                with patch.object(legacy.base, 'require_usd_demo', return_value=(obs, NS(login=123))), \
                     patch.object(legacy.time, 'monotonic', return_value=EVENT_M+elapsed), \
                     patch.object(legacy.time, 'time', return_value=EVENT_W+wall_elapsed):
                    old = legacy._fresh_publish_recheck(None, state, core, before, payload, EVENT_M, EVENT_W)
                new = fresh(before, obs, end_mono=EVENT_M+elapsed, end_wall=EVENT_W+wall_elapsed, receiver_ready=ready)
                self.assertEqual(new.ready, old is not None)

    def test_stale_legacy_candidate_is_not_mislabelled_as_final(self):
        before, after, payload = fixture()
        changed = replace(after, source_id='c'*64)
        local = local_decision('BUY', 'BULL_TREND')
        old, old_packet, _ = legacy_result(local, before, changed, payload, ai_result())
        final = adapter.adapt_v31(local, adapter.market_identity(before), ai_result=ai_result(),
                                 risk=approved(before, 'BUY'), freshness=fresh(before, changed), sources=SOURCES)
        self.assertEqual(old['final_action'], 'BUY')  # legacy pre-publication field
        self.assertIsNone(old_packet)
        self.assertEqual((final.action, final.reason_code), ('WAIT', 'SOURCE_CHANGED'))
        self.assertIsNone(packet(final, before))


class RiskAndAdapterTests(unittest.TestCase):
    def test_plan_mapping_exact_and_independent_of_ai_rr(self):
        for direction in ('BUY', 'SELL'):
            before, after, _ = fixture(direction)
            plan = plan_for(before, direction)
            risk = approved(before, direction)
            original = copy.deepcopy(plan)
            expected = (plan.stressed_loss, plan.stressed_profit, plan.volume, plan.sl, plan.tp)
            self.assertEqual((risk.planned_risk_usd, risk.planned_reward_usd, risk.lot, risk.sl, risk.tp), expected)
            packets = []
            for conf, rr in itertools.product((75, 80, 100), (1., 1.25, 1.5, 2., 20.)):
                local = replace(local_decision(direction, 'UNCLEAR'), suggested_rr=rr)
                final = adapter.adapt_v31(local, adapter.market_identity(before), ai_result=ai_result(direction, conf),
                                         risk=risk, freshness=fresh(before, after), sources=SOURCES)
                self.assertIs(final.evidence.risk, risk)
                self.assertEqual(final.action, direction)
                packets.append(packet(final, before))
            self.assertTrue(all(value == packets[0] for value in packets))
            self.assertEqual(plan, original)

    def test_missing_rejected_or_over_limit_plan_vetoes(self):
        before, after, _ = fixture()
        market = adapter.market_identity(before)
        for plan, allowed in ((None, True), (plan_for(before, 'BUY'), False),
                              (replace(plan_for(before, 'BUY'), stressed_loss=11.), True),
                              (replace(plan_for(before, 'BUY'), volume=.11), True)):
            risk = adapter.risk_from_cash_plan(plan, market, 'BUY', allowed=allowed, reason_code='PLAN_REJECTED')
            final = adapter.adapt_v31(local_decision('BUY', 'BULL_TREND'), market, ai_result=ai_result(),
                                     risk=risk, freshness=fresh(before, after), sources=SOURCES)
            self.assertFalse(risk.allowed)
            self.assertEqual(final.reason_code, 'RISK_VETO')
            self.assertIsNone(packet(final, before))

    def test_missing_malformed_or_failed_ai_is_wait(self):
        before, after, _ = fixture()
        for result in (None, {}, {'status': 'ERROR'}, {'status': 'REFUSED'}, {'status': 'SUCCESS'},
                       {'status': 'SUCCESS', 'signal': {'action': 'BUY'}}, 'BUY'):
            final = adapter.adapt_v31(local_decision('BUY', 'BULL_TREND'), adapter.market_identity(before),
                                     ai_result=result, risk=approved(before, 'BUY'), freshness=fresh(before, after), sources=SOURCES)
            self.assertEqual(final.action, 'WAIT')
            self.assertIsNone(packet(final, before))

    def test_snapshot_mismatch_is_not_directional(self):
        before, after, payload = fixture()
        for field, value in (('symbol', 'OTHER'), ('timeframe', 'M1'), ('latest_closed_bar_raw', before.closed-300)):
            changed = copy.deepcopy(payload); changed[field] = value
            final = adapter.evaluate_v31(changed, before, ai_result=ai_result(), risk=approved(before, 'BUY'),
                                        freshness=fresh(before, after), sources=SOURCES)
            self.assertEqual(final.action, 'WAIT')
            self.assertIsNone(packet(final, before))

    def test_preview_cannot_override_reducer_or_mode(self):
        before, after, _ = fixture()
        final = adapter.adapt_v31(local_decision('BUY', 'BULL_TREND'), adapter.market_identity(before),
                                 ai_result=ai_result(), risk=approved(before, 'BUY'), freshness=fresh(before, after), sources=SOURCES)
        self.assertIn(b';PREVIEW;', packet(final, before))
        with self.assertRaises(ValueError):packet(replace(final, action='SELL'), before)
        with self.assertRaises(TypeError):adapter.preview_packet(final, mode='DEMO_SEND')

    def test_preview_reuses_encoder_and_does_not_call_broker_math(self):
        before, after, payload = fixture()
        risk = approved(before, 'BUY')
        with patch.object(cash_risk, 'broker_plan', side_effect=AssertionError('broker math called')), \
             patch.object(core, 'request_analysis', side_effect=AssertionError('AI requested')), \
             patch.object(cash_demo, 'packet', wraps=cash_demo.packet) as encoder:
            final = adapter.evaluate_v31(payload, before, ai_result=ai_result(), risk=risk,
                                        freshness=fresh(before, after), sources=SOURCES)
            packet(final, before)
            encoder.assert_called_once()
            self.assertEqual(encoder.call_args.args[0], 'PREVIEW')


if __name__ == '__main__':
    unittest.main()
