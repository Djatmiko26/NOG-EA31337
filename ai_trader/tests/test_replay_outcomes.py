from dataclasses import replace
from pathlib import Path
import copy
import tempfile
import unittest

from hybrid_contract import MarketIdentity, RiskDecision
from replay_common import digest
from replay_snapshot import Bar
from replay_outcomes import evaluate_outcome, metrics
from replay_holdout import reserve_holdout, validate_permit
import hybrid_replay as replay
from test_replay_snapshot import snapshot


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.market = MarketIdentity('a'*64,1800000000,1800000300,.01)
        self.buy = RiskDecision(self.market,'BUY',True,'SYNTHETIC',5.,10.,.01,90.,110.,'RECORDED')
        self.sell = RiskDecision(self.market,'SELL',True,'SYNTHETIC',5.,10.,.01,110.,90.,'RECORDED')

    def bar(self,high=105.,low=95.,spread=0,offset=300):
        return Bar(1800000000+offset,100.,high,low,100.,spread,100)

    def outcome(self,action,risk,bars,**kwargs):
        return evaluate_outcome(action,risk,bars,point=.01,decision_bar_raw=1800000000,**kwargs)

    def test_buy_bid_tp_and_sl_first(self):
        self.assertEqual(self.outcome('BUY',self.buy,[self.bar(high=111.,spread=1000)]).status,'TP_FIRST')
        self.assertEqual(self.outcome('BUY',self.buy,[self.bar(low=89.)]).r,-1.)
        self.assertEqual(self.outcome('BUY',self.buy,[self.bar(high=111.)]).r,2.)

    def test_sell_ask_proxy_changes_hit(self):
        self.assertEqual(self.outcome('SELL',self.sell,[self.bar(low=89.)]).status,'TP_FIRST')
        self.assertEqual(self.outcome('SELL',self.sell,[self.bar(low=89.,spread=200)]).status,'UNRESOLVED')
        self.assertEqual(self.outcome('SELL',self.sell,[self.bar(high=109.,spread=200)]).status,'SL_FIRST')

    def test_same_bar_ambiguous_is_never_assumed_win(self):
        for action,risk in (('BUY',self.buy),('SELL',self.sell)):
            result = self.outcome(action,risk,[self.bar(high=111.,low=89.)])
            self.assertEqual(result.status,'AMBIGUOUS')
            self.assertIsNone(result.r)

    def test_fixed_horizon_and_unresolved(self):
        bars=[self.bar(),self.bar(high=111.,offset=600)]
        self.assertEqual(self.outcome('BUY',self.buy,bars,horizon=1).status,'UNRESOLVED')
        self.assertEqual(self.outcome('BUY',self.buy,bars).resolution_bars,2)

    def test_future_order_and_unapproved_risk_rejected(self):
        with self.assertRaises(ValueError):
            self.outcome('BUY',self.buy,[self.bar(offset=0)])
        with self.assertRaises(ValueError):
            self.outcome('BUY',replace(self.buy,allowed=False),[self.bar()])

    def test_wait_separate_and_all_denominators_explicit(self):
        outcomes=[self.outcome('WAIT',replace(self.buy,allowed=False),[]),
            self.outcome('BUY',self.buy,[self.bar(high=111.)]),
            self.outcome('BUY',self.buy,[self.bar(low=89.)]),
            self.outcome('BUY',self.buy,[self.bar(high=111.,low=89.)]),
            self.outcome('BUY',self.buy,[self.bar()])]
        events=[dict(outcome=o.to_dict(),local_action='WAIT' if o.status=='WAIT' else 'BUY',
            ai_gate='LOCAL_WAIT' if o.status=='WAIT' else 'CONFIRMED',risk_status='APPROVED_RECORDED',
            final_action='WAIT' if o.status=='WAIT' else 'BUY') for o in outcomes]
        value=metrics(events)
        for name in ('WAIT','TP_FIRST','SL_FIRST','AMBIGUOUS','UNRESOLVED'):
            self.assertEqual(value[name],1)
        self.assertEqual(value['resolved_directional'],2)
        self.assertEqual(value['win_rate'],.5)
        self.assertEqual(value['average_r'],.5)
        self.assertEqual(value['ai_wait_block'],0)

    def test_all_wait_has_no_winrate_or_expectancy(self):
        value=metrics([dict(outcome=self.outcome('WAIT',self.buy,[]).to_dict(),local_action='WAIT',
            ai_gate='LOCAL_WAIT',risk_status='UNAVAILABLE',final_action='WAIT')])
        self.assertIsNone(value['win_rate'])
        self.assertIsNone(value['expectancy_r'])
        self.assertEqual(value['WAIT'],1)


class HoldoutTests(unittest.TestCase):
    def setUp(self):
        self.snapshot=snapshot()
        self.plan=replay.make_plan(self.snapshot,replay.ReplaySettings())
        self.settings_hash='c'*64
        self.versions_hash='d'*64

    def reserve(self,root,**kwargs):
        return reserve_holdout(kwargs.pop('snapshot',self.snapshot),self.plan,self.settings_hash,
            self.versions_hash,root,declaration='Synthetic test; no claim of unseen market data',
            exposure_ranges=kwargs.pop('exposure_ranges',[]),allow_synthetic=True,**kwargs)

    def test_reservation_cannot_be_reused_even_after_parameter_change(self):
        with tempfile.TemporaryDirectory() as root:
            permit=self.reserve(root)
            validate_permit(permit,self.snapshot,self.plan,self.settings_hash,self.versions_hash)
            before={p.name:p.read_bytes() for p in Path(root).iterdir()}
            self.settings_hash='e'*64
            with self.assertRaisesRegex(ValueError,'ALREADY_EVALUATED_OR_RESERVED'):
                self.reserve(root)
            self.assertEqual(before,{p.name:p.read_bytes() for p in Path(root).iterdir()})

    def test_overlapping_other_snapshot_cannot_renew_budget(self):
        with tempfile.TemporaryDirectory() as root:
            self.reserve(root)
            with self.assertRaisesRegex(ValueError,'ALREADY_EVALUATED_OR_RESERVED'):
                self.reserve(root,snapshot=snapshot(direction='SELL'))

    def test_prior_explored_cannot_be_called_clean_holdout(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError,'CLEAN_HOLDOUT_NOT_ESTABLISHED'):
                self.reserve(root,snapshot=snapshot(prior_use='EXPLORED'))

    def test_declared_unseen_rejected_on_inventory_overlap(self):
        exposed=dict(symbol='XAUUSD',timeframe='M5',first_bar_raw=1800000000,last_bar_raw=1900000000)
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError,'OVERLAPS_KNOWN_EXPLORATION'):
                self.reserve(root,snapshot=snapshot(prior_use='DECLARED_UNSEEN'),exposure_ranges=[exposed])

    def test_corrupted_registry_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            self.reserve(root)
            path=next(Path(root).glob('*.json'))
            data=path.read_text(); path.write_text(data.replace('RESERVED_FOR_FINAL_EVALUATION','changed'))
            with self.assertRaisesRegex(ValueError,'CORRUPTED'):
                self.reserve(root)

    def test_missing_or_mismatched_permit_rejected(self):
        for permit in (None,{},dict(settings_sha256='a'*64)):
            with self.assertRaisesRegex(ValueError,'MATCHED_HOLDOUT_RESERVATION_REQUIRED'):
                validate_permit(permit,self.snapshot,self.plan,self.settings_hash,self.versions_hash)
