"""Offline integration tests. Synthetic approvals NEVER stand for broker approval."""
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import cash_risk
import hybrid_replay as replay
from hybrid_v31_adapter import market_identity
from replay_common import canonical, digest, read_json
from replay_records import records_envelope, verify_records, recorded_risk
from replay_snapshot import create_snapshot, save_snapshot
from replay_holdout import reserve_holdout
from test_replay_snapshot import snapshot


def specimen(direction='BUY'):
    """Explicit TEST-only evidence fixture, packaged before evaluation."""
    snap=snapshot(direction=direction)
    settings=replay.ReplaySettings(commission_per_lot=0.,fixed_fee=0.)
    sources=replay.source_versions()
    plan=replay.make_plan(snap,settings)
    for index in plan.indices(plan.segments[0]):
        observation,payload,_=replay.feature_at(snap,plan,plan.segments[0],index)
        local=replay.weighted.evaluate(payload)
        if local.action==direction:
            break
    else:
        raise AssertionError('Fixture must exercise unchanged V3.1 directional path')
    binding=replay.binding_for(snap,settings,sources,observation,payload)
    cash_plan=cash_risk.make_plan(direction=='BUY',observation.bid,observation.ask,5.,.01,
                                .1,.2,.01,.01,100.,100.,100.,0.,0.)
    if cash_plan is None:
        raise AssertionError('Synthetic plan fixture invalid')
    raw_plan=asdict(cash_plan)
    record=dict(decision_bar_raw=observation.closed,signal_id='1'*32,binding=binding,
        origin_sha256=digest({'fixture':'synthetic M3 test; never an actual broker receipt'}),
        ai_result=dict(status='SUCCESS',signal=dict(action=direction,confidence=90,
            market_regime='UNCLEAR',reason='SYNTHETIC_TEST confirmation fixture')),
        risk=dict(status='APPROVED_RECORDED',kind='EXACT_CASH_PLAN_RECEIPT',signal_id='1'*32,
            binding=copy.deepcopy(binding),direction=direction,fee_assumptions=settings.fees,
            plan_version=cash_risk.VERSION,cash_plan=raw_plan,plan_sha256=digest(raw_plan)))
    return snap,settings,sources,record


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap,cls.settings,cls.sources,cls.record=specimen()

    def run_case(self,record=None,mode='RECORDED',**kwargs):
        records=records_envelope([record or self.record],evidence_kind='SYNTHETIC_TEST')
        return replay.run_replay(self.snap,settings=self.settings,sources=self.sources,
            records=records,ai_mode=mode,allow_synthetic=True,phase='DEVELOPMENT',**kwargs)

    def event(self,result):
        return next(e for e in result['events'] if e['bar_raw']==self.record['decision_bar_raw'])

    def test_recorded_equivalence_both_directions(self):
        for direction in ('BUY','SELL'):
            snap,settings,sources,record=specimen(direction)
            result=replay.run_replay(snap,settings=settings,sources=sources,ai_mode='RECORDED',
                records=records_envelope([record],evidence_kind='SYNTHETIC_TEST'),
                phase='DEVELOPMENT',allow_synthetic=True)
            event=next(e for e in result['events'] if e['bar_raw']==record['decision_bar_raw'])
            self.assertEqual(event['local_action'],direction)
            self.assertEqual(event['legacy_ai_candidate'],direction)
            self.assertEqual(event['final_action'],direction)
            self.assertEqual(event['risk_status'],'APPROVED_RECORDED')
            self.assertFalse(event['intentional_safety_delta'])
            self.assertEqual(event['evidence']['mode'],'PREVIEW')

    def test_none_mode_does_not_reuse_recorded_ai(self):
        event=self.event(self.run_case(mode='NONE'))
        self.assertEqual(event['risk_status'],'APPROVED_RECORDED')
        self.assertEqual(event['final_action'],'WAIT')
        self.assertEqual(event['legacy_reason'],'AI_UNAVAILABLE')

    def test_unavailable_risk_wait_and_intentional_delta(self):
        record=copy.deepcopy(self.record); record.pop('risk')
        event=self.event(self.run_case(record))
        self.assertEqual(event['risk_status'],'UNAVAILABLE')
        self.assertEqual(event['final_action'],'WAIT')
        self.assertEqual(event['legacy_ai_candidate'],'BUY')
        self.assertTrue(event['intentional_safety_delta'])

    def test_recorded_ai_bar_mismatch_does_not_replay_signal(self):
        record=copy.deepcopy(self.record); record['decision_bar_raw']+=300
        result=self.run_case(record)
        for event in result['events']:
            if event['bar_raw'] in (self.record['decision_bar_raw'],record['decision_bar_raw']):
                self.assertEqual(event['final_action'],'WAIT')
                self.assertIsNone(event['signal_id'])
                self.assertNotEqual(event['recorded_binding'],'MATCHED')

    def test_recorded_source_settings_versions_feature_mismatches(self):
        from replay_records import match_record
        for key in ('source_identity_hash','settings_sha256','versions_sha256','feature_sha256','bar_raw'):
            record=copy.deepcopy(self.record); record['binding'][key]='wrong'
            matched,reason=match_record(record,self.record['binding'])
            self.assertFalse(matched)
            self.assertEqual(reason,'RECORDED_BINDING_MISMATCH')
        record=copy.deepcopy(self.record); record['binding']['settings_sha256']='0'*64
        self.assertEqual(self.event(self.run_case(record))['final_action'],'WAIT')

    def test_recorded_cash_plan_hash_mismatch_wait(self):
        record=copy.deepcopy(self.record); record['risk']['cash_plan']['volume']*=2
        event=self.event(self.run_case(record))
        self.assertEqual(event['risk_status'],'UNAVAILABLE')
        self.assertEqual(event['risk_reason'],'CASH_PLAN_HASH_MISMATCH')
        self.assertEqual(event['final_action'],'WAIT')

    def test_risk_receipt_binding_fee_version_and_signal_mismatch(self):
        market=market_identity(replay.feature_at(self.snap,replay.make_plan(self.snap,self.settings),
            replay.make_plan(self.snap,self.settings).segments[0],
            (self.record['decision_bar_raw']-self.snap.bars[0].time)//300)[0])
        for key,value in (('signal_id','2'*32),('binding',{}),('direction','SELL'),
                          ('fee_assumptions',{'commission_per_lot':1.,'fixed_fee':0.}),('plan_version','wrong')):
            record=copy.deepcopy(self.record); record['risk'][key]=value
            with self.subTest(key=key):
                risk=recorded_risk(record,self.record['binding'],market,'BUY',fee_assumptions=self.settings.fees)
                self.assertEqual(risk.status,'UNAVAILABLE')
                self.assertFalse(risk.decision.allowed)

    def test_rejected_receipt_remains_rejected(self):
        record=copy.deepcopy(self.record); record['risk']['status']='REJECTED'
        event=self.event(self.run_case(record))
        self.assertEqual(event['risk_status'],'REJECTED')
        self.assertEqual(event['final_action'],'WAIT')

    def test_record_archive_integrity_duplicate_bar_and_synthetic_opt_in(self):
        envelope=records_envelope([self.record],evidence_kind='SYNTHETIC_TEST')
        with self.assertRaises(ValueError):
            verify_records(envelope)
        envelope['body']['records'][0]=copy.deepcopy(self.record)
        envelope['body']['records'][0]['signal_id']='2'*32
        with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):
            verify_records(envelope,allow_synthetic=True)
        duplicate=records_envelope([self.record,self.record],evidence_kind='SYNTHETIC_TEST')
        with self.assertRaisesRegex(ValueError,'DUPLICATE'):
            verify_records(duplicate,allow_synthetic=True)

    def test_synthetic_evidence_cannot_be_mixed_with_market_snapshot(self):
        with self.assertRaisesRegex(ValueError,'REQUIRE_SYNTHETIC_SNAPSHOT'):
            replay.run_replay(snapshot(prior_use='DECLARED_UNSEEN'),settings=self.settings,sources=self.sources,
                records=records_envelope([self.record],evidence_kind='SYNTHETIC_TEST'),
                allow_synthetic=True,phase='DEVELOPMENT')

    def test_future_prices_do_not_affect_features_or_decision(self):
        plan=replay.make_plan(self.snap,self.settings); segment=plan.segments[0]
        index=next(iter(plan.indices(segment)))
        old_observation,old_payload,_=replay.feature_at(self.snap,plan,segment,index)
        rows=self.snap.to_dict()['bars']
        for row in rows[index+1:]:
            for field in ('open','high','low','close'):
                row[field]+=1000
            row['spread']+=100
        m=self.snap.manifest
        other=create_snapshot(rows,source_identity_hash=m['source_identity_hash'],point=m['point'],
            settings=m['settings'],created_at=m['created_at'],closed_before_raw=m['closed_before_raw'],
            generator_hash=m['generator_sha256'],prior_use=m['prior_use'])
        observation,payload,_=replay.feature_at(other,plan,segment,index)
        self.assertEqual(observation,old_observation)
        self.assertEqual(canonical(payload),canonical(old_payload))
        self.assertEqual(replay.weighted.evaluate(payload),replay.weighted.evaluate(old_payload))

    def test_same_input_byte_identical_report_and_evidence(self):
        first=self.run_case(); second=self.run_case()
        self.assertEqual(canonical(first),canonical(second))
        self.assertEqual(replay.report_text(first),replay.report_text(second))

    def test_none_no_receipts_is_all_wait_and_split_phases(self):
        result=replay.run_replay(self.snap,sources=self.sources)
        self.assertEqual(set(result['summaries']),{'DEVELOPMENT','VALIDATION'})
        self.assertTrue(all(e['final_action']=='WAIT' and e['risk_status']=='UNAVAILABLE' for e in result['events']))
        dev=self.run_case()
        self.assertEqual(set(dev['summaries']),{'DEVELOPMENT'})
        self.assertFalse(any(e['split']=='HOLDOUT' for e in result['events']))

    def test_final_requires_reservation_and_evaluates_only_holdout(self):
        with self.assertRaisesRegex(ValueError,'RESERVATION_REQUIRED'):
            replay.run_replay(self.snap,sources=self.sources,phase='FINAL')
        settings=replay.ReplaySettings(); plan=replay.make_plan(self.snap,settings)
        with tempfile.TemporaryDirectory() as root:
            permit=reserve_holdout(self.snap,plan,replay.policy_hash(self.snap,settings),
                replay.versions_hash(self.sources),root,declaration='synthetic only',exposure_ranges=[],allow_synthetic=True)
            result=replay.run_replay(self.snap,sources=self.sources,phase='FINAL',holdout_permit=permit)
            self.assertEqual(set(result['summaries']),{'HOLDOUT'})
            self.assertTrue(all(e['split']=='HOLDOUT' for e in result['events']))

    def test_replay_cli_new_output_and_no_overwrite(self):
        from contextlib import redirect_stdout
        from io import StringIO
        with tempfile.TemporaryDirectory() as root, redirect_stdout(StringIO()):
            root=Path(root); source=root/'snapshot.json'; out=root/'run'
            save_snapshot(source,self.snap); original=source.read_bytes()
            args=['--snapshot',str(source),'--output',str(out),'--phase','DEVELOPMENT']
            self.assertEqual(replay.main(args),0)
            before={p.name:p.read_bytes() for p in out.iterdir()}
            manifest=read_json(out/'run_manifest.json')
            self.assertEqual(manifest['evidence_sha256'],hashlib.sha256(before['evidence.json']).hexdigest())
            with self.assertRaises(FileExistsError):
                replay.main(args)
            self.assertEqual(before,{p.name:p.read_bytes() for p in out.iterdir()})
            self.assertEqual(source.read_bytes(),original)

    def test_no_sdk_dependency_and_no_network_sqlite_or_broker_primitives(self):
        # -S removes site packages: neither OpenAI nor MT5 nor pandas is needed.
        root=Path(replay.__file__).resolve().parent
        code="""
import builtins,sys
sys.path[:0]=sys.argv[1:]
original=builtins.__import__
def blocked(name,*a,**k):
    if name.split('.')[0] in ('openai','MetaTrader5'):
        raise AssertionError('SDK import: '+name)
    return original(name,*a,**k)
builtins.__import__=blocked
import hybrid_replay
from test_replay_snapshot import snapshot
import socket,sqlite3,cash_risk,live_ai_core
from unittest.mock import patch
with patch.object(socket,'socket',side_effect=AssertionError('socket')), patch.object(sqlite3,'connect',side_effect=AssertionError('database')), patch.object(cash_risk,'broker_plan',side_effect=AssertionError('broker')), patch.object(live_ai_core,'request_analysis',side_effect=AssertionError('AI')):
    result=hybrid_replay.run_replay(snapshot(),sources=hybrid_replay.source_versions(),phase='DEVELOPMENT')
    assert all(e['final_action']=='WAIT' for e in result['events'])
"""
        completed=subprocess.run([sys.executable,'-B','-S','-c',code,str(root),str(root/'tests')],
            capture_output=True,text=True,timeout=40)
        self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
