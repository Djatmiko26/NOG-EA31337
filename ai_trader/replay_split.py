"""Chronological, isolated history/outcome windows; no random split or I/O."""
from dataclasses import asdict, dataclass
from replay_common import finite, integer

LABELS = ('DEVELOPMENT', 'VALIDATION', 'HOLDOUT')


@dataclass(frozen=True)
class Segment:
    label: str
    start: int
    stop: int  # exclusive


@dataclass(frozen=True)
class SplitPlan:
    segments: tuple[Segment, ...]
    context_bars: int
    horizon: int
    purge: int
    bar_count: int

    def to_dict(self):
        return asdict(self)

    def indices(self, segment):
        if segment not in self.segments:
            raise ValueError('UNKNOWN_SPLIT')
        return range(segment.start + self.context_bars - 1, segment.stop - self.horizon)

    def windows(self, segment, decision):
        if decision not in self.indices(segment):
            raise ValueError('FUTURE_OR_HISTORY_LEAKAGE')
        return (decision-self.context_bars+1, decision+1), (decision+1, decision+self.horizon+1)


def split_chronological(bar_count, *, context_bars=120, horizon=20, purge=None,
                        development_fraction=.6, validation_fraction=.2):
    integer(bar_count, 1); integer(context_bars, 1); integer(horizon, 1)
    purge = horizon if purge is None else integer(purge)
    if purge < horizon:
        raise ValueError('PURGE_MUST_COVER_OUTCOME_HORIZON')
    finite(development_fraction); finite(validation_fraction)
    if min(development_fraction, validation_fraction) <= 0 or development_fraction+validation_fraction >= 1:
        raise ValueError('INVALID_SPLIT_FRACTIONS')
    usable = bar_count - 2*purge
    sizes = [int(usable*development_fraction), int(usable*validation_fraction)]
    sizes.append(usable-sum(sizes))
    if min(sizes) < context_bars+horizon:
        raise ValueError('INSUFFICIENT_HISTORY_FOR_THREE_SPLITS')
    segments, start = [], 0
    for label, size in zip(LABELS, sizes):
        segments.append(Segment(label, start, start+size))
        start += size+purge
    return SplitPlan(tuple(segments), context_bars, horizon, purge, bar_count)


def validate_plan(plan):
    integer(plan.context_bars, 1); integer(plan.horizon, 1); integer(plan.purge)
    if plan.purge < plan.horizon or tuple(s.label for s in plan.segments) != LABELS:
        raise ValueError('INVALID_SPLIT_PLAN')
    previous = None
    for segment in plan.segments:
        integer(segment.start); integer(segment.stop, 1)
        if segment.stop > plan.bar_count or segment.stop-segment.start < plan.context_bars+plan.horizon:
            raise ValueError('INVALID_SEGMENT_BOUNDS')
        if previous is not None and segment.start-previous.stop < plan.purge:
            raise ValueError('SPLIT_OVERLAP_OR_PURGE_LEAKAGE')
        previous = segment
    if plan.segments[0].start != 0 or plan.segments[-1].stop != plan.bar_count:
        raise ValueError('INCOMPLETE_SPLIT_PLAN')
    return plan
