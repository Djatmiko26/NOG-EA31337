"""Reproducible offline V3.1 vs hybrid research; never connects to a broker or AI."""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path

import ensemble_weighted_v31 as weighted
import live_ai_core as core
from hybrid_contract import Freshness, SourceVersion
from hybrid_v31_adapter import adapt_v31, ai_from_v31, market_identity
from replay_common import canonical, digest, finite, output_path, read_json, write_new
from replay_snapshot import Snapshot, load_snapshot, verify_snapshot
from replay_split import split_chronological, validate_plan
from replay_records import match_record, recorded_risk, verify_records
from replay_outcomes import breakdown, evaluate_outcome, metrics
from replay_holdout import reserve_holdout, validate_permit

VERSION = 'offline-hybrid-replay-v1'
PHASES = {'DEVELOPMENT': ('DEVELOPMENT',), 'SELECTION': ('DEVELOPMENT', 'VALIDATION'), 'FINAL': ('HOLDOUT',)}
SOURCE_FILES = ('hybrid_replay.py', 'replay_common.py', 'replay_snapshot.py', 'replay_split.py',
                'replay_records.py', 'replay_outcomes.py', 'replay_holdout.py',
                'hybrid_contract.py', 'hybrid_decision.py', 'hybrid_v31_adapter.py',
                'ensemble_weighted_v31.py', 'live_ai_core.py', 'cash_risk.py', 'cash_demo.py')


@dataclass(frozen=True)
class ReplaySettings:
    horizon: int = 20
    purge: int = 20
    development_fraction: float = .6
    validation_fraction: float = .2
    commission_per_lot: float | None = None
    fixed_fee: float | None = None

    def __post_init__(self):
        for value in (self.commission_per_lot, self.fixed_fee):
            if value is not None:
                finite(value)
        if (self.commission_per_lot is None) != (self.fixed_fee is None):
            raise ValueError('BOTH_FEE_ASSUMPTIONS_REQUIRED')

    @property
    def fees(self):
        return dict(commission_per_lot=self.commission_per_lot, fixed_fee=self.fixed_fee)


def source_versions():
    # I/O boundary used by CLI/tests; run_replay itself receives frozen hashes.
    root = Path(__file__).resolve().parent
    return tuple(SourceVersion(name, VERSION, hashlib.sha256((root/name).read_bytes()).hexdigest())
                 for name in SOURCE_FILES)


def make_plan(snapshot, settings):
    return split_chronological(len(snapshot.bars), context_bars=core.CONTEXT_BARS,
        horizon=settings.horizon, purge=settings.purge, development_fraction=settings.development_fraction,
        validation_fraction=settings.validation_fraction)


def policy_hash(snapshot, settings):
    return digest({'snapshot_settings_sha256': snapshot.manifest['settings_sha256'],
                   'replay_settings': asdict(settings), 'strategy_version': weighted.VERSION,
                   'quote_convention': 'CLOSED_BID_CLOSE_PLUS_BAR_SPREAD', 'context_bars': core.CONTEXT_BARS})


def versions_hash(sources):
    if type(sources) is not tuple or not sources:
        raise ValueError('FROZEN_SOURCE_VERSIONS_REQUIRED')
    return digest([asdict(s) for s in sources])


def feature_at(snapshot, plan, segment, index):
    feature_window, future_window = plan.windows(segment, index)
    start, stop = feature_window
    history = snapshot.bars[start:stop]
    last = history[-1]
    m = snapshot.manifest
    # No future open/spread/tick enters features. This is a research quote proxy,
    # not a claim that an observed live tick or broker approval exists.
    observation = core.Observation(last.time+300, last.time, (last.time+300)*1000+1000,
        last.close, last.close+last.spread*m['point'], m['point'], m['source_identity_hash'])
    payload = core.build_payload([asdict(b) for b in history], m['symbol'], observation)
    return observation, payload, future_window


def binding_for(snapshot, settings, sources, observation, payload):
    m = snapshot.manifest
    return dict(source_identity_hash=m['source_identity_hash'], symbol=m['symbol'], timeframe=m['timeframe'],
                bar_raw=observation.closed, settings_sha256=policy_hash(snapshot, settings),
                versions_sha256=versions_hash(sources), feature_sha256=digest(payload))


def legacy_confirmation(local, ai):
    if local.action == 'WAIT':
        return 'WAIT', 'LOCAL_WAIT'
    if ai is None:
        return 'WAIT', 'AI_UNAVAILABLE'
    if ai.status != 'SUCCESS':
        return 'WAIT', 'AI_'+ai.status
    if ai.action == 'WAIT':
        return 'WAIT', 'AI_WAIT'
    if ai.action != local.action:
        return 'WAIT', 'AI_DISAGREE'
    if ai.confidence < local.ai_min_confidence:
        return 'WAIT', 'AI_LOW_CONFIDENCE'
    return local.action, 'CONFIRMED'


def run_replay(snapshot: Snapshot, *, settings=ReplaySettings(), sources,
               ai_mode='NONE', records=None, phase='SELECTION', holdout_permit=None,
               allow_synthetic=False):
    """Pure evaluation. CLI must reserve FINAL holdout before calling this function.

No receipt fabrication, live freshness/receiver claim, order transport or writes.
Freshness flags describe the offline snapshot window; evidence explicitly says
RESEARCH_ONLY_NO_TRANSPORT. They are not authorization for live reuse.
"""
    snapshot = verify_snapshot(snapshot.to_dict())
    if ai_mode not in ('NONE', 'RECORDED') or phase not in PHASES:
        raise ValueError('INVALID_REPLAY_MODE_OR_PHASE')
    plan = validate_plan(make_plan(snapshot, settings))
    policy, versions = policy_hash(snapshot, settings), versions_hash(sources)
    if phase == 'FINAL':
        validate_permit(holdout_permit, snapshot, plan, policy, versions)
    index_records = verify_records(records, allow_synthetic=allow_synthetic) if records is not None else {}
    if records is not None and records['body']['evidence_kind'] == 'SYNTHETIC_TEST' and snapshot.manifest['prior_use'] != 'SYNTHETIC_TEST':
        raise ValueError('SYNTHETIC_RECORDS_REQUIRE_SYNTHETIC_SNAPSHOT')
    if ai_mode == 'RECORDED' and records is None:
        raise ValueError('RECORDED_MODE_REQUIRES_EVIDENCE_FILE')
    events = []
    for segment in plan.segments:
        if segment.label not in PHASES[phase]:
            continue
        for index in plan.indices(segment):
            observation, payload, future_window = feature_at(snapshot, plan, segment, index)
            local = weighted.evaluate(payload)
            market = market_identity(observation)
            expected = binding_for(snapshot, settings, sources, observation, payload)
            recorded = index_records.get(observation.closed)
            matched, binding_reason = match_record(recorded, expected)
            ai_result = recorded.get('ai_result') if ai_mode == 'RECORDED' and matched else None
            ai = ai_from_v31(ai_result, market)
            risk = recorded_risk(recorded, expected, market, local.action, fee_assumptions=settings.fees)
            final = adapt_v31(local, market, ai_result=ai_result, risk=risk.decision,
                              freshness=Freshness(True, True, True, True), sources=sources)
            legacy_action, legacy_reason = legacy_confirmation(local, ai)
            outcome = evaluate_outcome(final.action, risk.decision, snapshot.bars[slice(*future_window)],
                point=snapshot.manifest['point'], decision_bar_raw=observation.closed, horizon=settings.horizon)
            confidence = ai.confidence if ai is not None and ai.status == 'SUCCESS' else None
            bucket = 'UNAVAILABLE' if confidence is None else ('90-100' if confidence >= 90 else
                f'{confidence//10*10:02d}-{confidence//10*10+9:02d}')
            events.append(dict(split=segment.label, index=index, bar_raw=observation.closed,
                feature_window=[index-core.CONTEXT_BARS+1, index+1], future_window=list(future_window),
                feature_sha256=digest(payload), local_action=local.action, local_reason=local.reason,
                regime=local.regime, suggested_rr=local.suggested_rr, confidence_bucket=bucket,
                legacy_ai_candidate=legacy_action, legacy_reason=legacy_reason, ai_gate=legacy_reason,
                recorded_binding=binding_reason, risk_status=risk.status, risk_reason=risk.reason_code,
                signal_id=recorded['signal_id'] if matched else None,
                final_action=final.action, reason_code=final.reason_code,
                intentional_safety_delta=legacy_action != 'WAIT' and final.action == 'WAIT',
                outcome=outcome.to_dict(), evidence=final.to_dict()))
    summaries = {}
    for label in PHASES[phase]:
        group = [e for e in events if e['split'] == label]
        summaries[label] = dict(metrics=metrics(group), by_direction=breakdown(group, 'final_action'),
            by_regime=breakdown(group, 'regime'), by_confidence=breakdown(group, 'confidence_bucket'),
            by_suggested_rr=breakdown(group, 'suggested_rr'),
            reason_differences=dict(sorted(Counter(e['legacy_reason']+' -> '+e['reason_code'] for e in group).items())))
    return dict(version=VERSION, use='RESEARCH_ONLY_NO_TRANSPORT', snapshot_id=snapshot.manifest['snapshot_id'],
        snapshot_content_sha256=snapshot.manifest['content_sha256'], prior_use=snapshot.manifest['prior_use'],
        ai_mode=ai_mode, phase=phase, settings=asdict(settings), settings_sha256=policy,
        versions_sha256=versions, sources=[asdict(s) for s in sources], split_plan=plan.to_dict(),
        records_sha256=records['sha256'] if records is not None else None,
        evidence_kind=records['body']['evidence_kind'] if records is not None else 'NO_RECORDED_EVIDENCE',
        summaries=summaries, events=events)


def report_text(result):
    def value(v):
        return 'NA' if v is None else str(round(v, 6)) if isinstance(v, float) else str(v)
    lines = ['# Offline hybrid replay', '', '**RESEARCH ONLY — NO API, NO BROKER, NO TRANSPORT.**',
        '', 'Snapshot: '+result['snapshot_id'], 'Prior use: '+result['prior_use'],
        'AI mode: '+result['ai_mode']+'; phase: '+result['phase']+'; evidence: '+result['evidence_kind'],
        '', 'Features use 120 closed bars local to each split; quotes use closed BID close + bar spread.',
        'Outcomes are hypothetical barrier labels, not fill simulation. SELL uses ASK bar-spread approximation.',
        'WAIT, ambiguous and unresolved counts are retained. Win rate and both R averages use only TP/SL resolved events.',
        'No automatic profitability conclusion. Recorded fixture approvals are not broker approvals for this run.',
        '', '| Split | Events | Local WAIT | AI wait/block | Risk veto/unavailable | BUY | SELL | WAIT | TP | SL | Ambiguous | Unresolved | Win rate | Expectancy R | Average R | Max loss streak | Avg resolution bars |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    keys = ('events', 'local_wait', 'ai_wait_block', 'risk_veto', 'final_buy', 'final_sell', 'WAIT', 'TP_FIRST',
            'SL_FIRST', 'AMBIGUOUS', 'UNRESOLVED', 'win_rate', 'expectancy_r', 'average_r', 'max_losing_streak', 'average_resolution_bars')
    for split, summary in result['summaries'].items():
        lines.append('| '+split+' | '+' | '.join(value(summary['metrics'][key]) for key in keys)+' |')
    for split, summary in result['summaries'].items():
        lines += ['', '## '+split+' breakdowns', '']
        for name in ('by_direction', 'by_regime', 'by_confidence', 'by_suggested_rr', 'reason_differences'):
            lines += [name, '```json', canonical(summary[name]).decode().strip(), '```', '']
    lines += ['Intentional safety deltas: '+str(sum(e['intentional_safety_delta'] for e in result['events'])),
              'Event-level local/candidate/final actions, reason codes and immutable evidence are in evidence.json.',
              'This is an event study; overlapping hypothetical positions are not a portfolio/equity simulation.',
              'Ambiguous/unresolved events interrupt the known losing streak; WAIT does not count as a trade.']
    return ('\n'.join(lines)+'\n').encode('utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='NEW output directory; never overwritten')
    parser.add_argument('--phase', choices=tuple(PHASES), default='SELECTION')
    parser.add_argument('--ai-mode', choices=('NONE', 'RECORDED'), default='NONE')
    parser.add_argument('--records', type=Path)
    parser.add_argument('--horizon', type=int, default=20)
    parser.add_argument('--purge', type=int, default=20)
    parser.add_argument('--commission-per-lot', type=float)
    parser.add_argument('--fixed-fee', type=float)
    parser.add_argument('--allow-synthetic', action='store_true')
    parser.add_argument('--holdout-registry', type=Path)
    parser.add_argument('--holdout-declaration')
    parser.add_argument('--exposure-inventory', type=Path, default=Path(__file__).with_name('REPLAY_DATA_INVENTORY.json'))
    args = parser.parse_args(argv)
    snapshot = load_snapshot(args.snapshot)
    settings = ReplaySettings(horizon=args.horizon, purge=args.purge,
        commission_per_lot=args.commission_per_lot, fixed_fee=args.fixed_fee)
    sources = source_versions()
    records = read_json(args.records) if args.records else None
    if records is not None:
        verify_records(records, allow_synthetic=args.allow_synthetic)
    if args.ai_mode == 'RECORDED' and records is None:
        parser.error('RECORDED mode requires --records')
    destination = output_path(args.output)
    destination.mkdir(parents=True, exist_ok=False)
    permit = None
    if args.phase == 'FINAL':
        if args.holdout_registry is None or not args.holdout_declaration:
            parser.error('FINAL requires persistent --holdout-registry and --holdout-declaration')
        exposures = read_json(args.exposure_inventory)['known_exposure_ranges']
        permit = reserve_holdout(snapshot, make_plan(snapshot, settings), policy_hash(snapshot, settings),
            versions_hash(sources), args.holdout_registry, declaration=args.holdout_declaration,
            exposure_ranges=exposures, allow_synthetic=args.allow_synthetic)
    result = run_replay(snapshot, settings=settings, sources=sources, records=records,
        ai_mode=args.ai_mode, phase=args.phase, holdout_permit=permit, allow_synthetic=args.allow_synthetic)
    evidence, report = canonical(result), report_text(result)
    write_new(destination/'evidence.json', evidence)
    write_new(destination/'report.md', report)
    write_new(destination/'run_manifest.json', canonical(dict(snapshot_id=result['snapshot_id'],
        settings_sha256=result['settings_sha256'], versions_sha256=result['versions_sha256'],
        evidence_sha256=hashlib.sha256(evidence).hexdigest(), report_sha256=hashlib.sha256(report).hexdigest(),
        phase=args.phase, holdout_reservation=permit)))
    print('OFFLINE_REPLAY_COMPLETE', destination, 'events='+str(len(result['events'])))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
