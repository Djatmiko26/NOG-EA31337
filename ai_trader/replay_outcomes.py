"""Reusable hypothetical TP/SL labels using BID bars and explicit ASK approximation."""
from dataclasses import asdict, dataclass
from replay_common import finite, integer


@dataclass(frozen=True)
class Outcome:
    status: str
    r: float | None
    resolution_bars: int | None
    evaluated_bars: int

    def to_dict(self):
        return asdict(self)


def evaluate_outcome(action, risk, future_bars, *, point, decision_bar_raw, horizon=None):
    if action not in ('BUY', 'SELL', 'WAIT'):
        raise ValueError('INVALID_ACTION')
    if action == 'WAIT':
        return Outcome('WAIT', None, None, 0)
    if not risk.allowed or risk.direction != action:
        raise ValueError('DIRECTION_REQUIRES_MATCHED_RISK')
    if finite(point) == 0:
        raise ValueError('INVALID_POINT')
    if horizon is not None:
        integer(horizon, 1)
    bars = tuple(future_bars if horizon is None else future_bars[:horizon])
    previous = decision_bar_raw
    for bar in bars:
        if bar.time <= previous:
            raise ValueError('OUTCOME_MUST_BE_STRICTLY_FUTURE')
        previous = bar.time
    for index, bar in enumerate(bars, 1):
        # SELL cover uses ASK approximated by the full-bar spread; never presented
        # as exact ticks. Future spread belongs ONLY to the outcome evaluator.
        offset = bar.spread*point if action == 'SELL' else 0.
        high, low = bar.high+offset, bar.low+offset
        tp = high >= risk.tp if action == 'BUY' else low <= risk.tp
        sl = low <= risk.sl if action == 'BUY' else high >= risk.sl
        if tp and sl:
            return Outcome('AMBIGUOUS', None, index, index)
        if tp:
            return Outcome('TP_FIRST', risk.planned_reward_usd/risk.planned_risk_usd, index, index)
        if sl:
            return Outcome('SL_FIRST', -1., index, index)
    return Outcome('UNRESOLVED', None, None, len(bars))


def metrics(events):
    statuses = ('WAIT', 'TP_FIRST', 'SL_FIRST', 'AMBIGUOUS', 'UNRESOLVED')
    counts = {key: sum(e['outcome']['status'] == key for e in events) for key in statuses}
    resolved = [e for e in events if e['outcome']['status'] in ('TP_FIRST', 'SL_FIRST')]
    values = [e['outcome']['r'] for e in resolved]
    current = maximum = 0
    for e in events:
        status = e['outcome']['status']
        if status == 'SL_FIRST':
            current += 1; maximum = max(maximum, current)
        elif status != 'WAIT':
            current = 0  # ambiguous/unresolved interrupt a known streak
    average = sum(values)/len(values) if values else None
    return dict(events=len(events), local_wait=sum(e['local_action'] == 'WAIT' for e in events),
        ai_wait_block=sum(e['ai_gate'] not in ('CONFIRMED', 'LOCAL_WAIT') for e in events),
        risk_veto=sum(e['risk_status'] != 'APPROVED_RECORDED' for e in events),
        final_buy=sum(e['final_action'] == 'BUY' for e in events),
        final_sell=sum(e['final_action'] == 'SELL' for e in events), **counts,
        directional=len(events)-counts['WAIT'], resolved_directional=len(resolved),
        win_rate=counts['TP_FIRST']/len(resolved) if resolved else None,
        expectancy_r=average, average_r=average, max_losing_streak=maximum,
        average_resolution_bars=sum(e['outcome']['resolution_bars'] for e in resolved)/len(resolved) if resolved else None)


def breakdown(events, field):
    keys = sorted({str(e[field]) for e in events})
    return {key: metrics([e for e in events if str(e[field]) == key]) for key in keys}
