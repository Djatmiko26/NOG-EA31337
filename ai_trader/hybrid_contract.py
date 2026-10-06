"""Immutable hybrid research contracts. No broker math, I/O or SDK dependencies."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
import re
from typing import Literal

Action = Literal['BUY', 'SELL', 'WAIT']
Regime = Literal['BULL_TREND', 'BEAR_TREND', 'RANGE', 'VOLATILE', 'UNCLEAR']
ACTIONS = ('BUY', 'SELL', 'WAIT')
REGIMES = ('BULL_TREND', 'BEAR_TREND', 'RANGE', 'VOLATILE', 'UNCLEAR')


def choice(value, options):
    if type(value) is not str or value not in options:
        raise ValueError('INVALID_ENUM')


def number(value, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError('INVALID_NUMBER')


def boolean(value):
    if type(value) is not bool:
        raise ValueError('INVALID_BOOLEAN')


def code(value):
    if type(value) is not str or re.fullmatch(r'[A-Z][A-Z0-9_]*', value) is None:
        raise ValueError('INVALID_REASON_CODE')


def text(value):
    if type(value) is not str or not value:
        raise ValueError('INVALID_TEXT')


def confidence(value):
    if type(value) is not int or not 0 <= value <= 100:
        raise ValueError('INVALID_CONFIDENCE')


@dataclass(frozen=True)
class MarketIdentity:
    source_id: str
    closed_bar: int
    forming_bar: int
    point: float
    symbol: str = 'XAUUSD'
    timeframe: str = 'M5'

    def __post_init__(self):
        if type(self.source_id) is not str or re.fullmatch('[a-f0-9]{64}', self.source_id) is None:
            raise ValueError('INVALID_SOURCE_ID')
        if any(type(v) is not int or v <= 0 for v in (self.closed_bar, self.forming_bar)):
            raise ValueError('INVALID_BAR')
        if self.forming_bar - self.closed_bar != 300:
            raise ValueError('INVALID_M5_BAR_PAIR')
        number(self.point)
        if self.point == 0:
            raise ValueError('INVALID_POINT')
        choice(self.symbol, ('XAUUSD',))
        choice(self.timeframe, ('M5',))


@dataclass(frozen=True)
class SourceVersion:
    name: str
    version: str
    sha256: str

    def __post_init__(self):
        text(self.name)
        text(self.version)
        if type(self.sha256) is not str or re.fullmatch('[a-f0-9]{64}', self.sha256) is None:
            raise ValueError('INVALID_SOURCE_HASH')


@dataclass(frozen=True)
class StrategyVote:
    name: str
    action: Literal['BUY', 'SELL', 'WAIT', 'VETO']
    weight: float
    strength: float
    reason: str

    def __post_init__(self):
        text(self.name)
        choice(self.action, (*ACTIONS, 'VETO'))
        number(self.weight)
        number(self.strength)
        if self.strength > 1:
            raise ValueError('INVALID_STRENGTH')
        text(self.reason)


@dataclass(frozen=True)
class StrategyEvidence:
    name: str
    action: Action  # selected setup, not a unanimity requirement for module votes
    reason_code: str
    votes: tuple[StrategyVote, ...] = ()

    def __post_init__(self):
        text(self.name)
        choice(self.action, ACTIONS)
        code(self.reason_code)
        if type(self.votes) is not tuple or any(type(v) is not StrategyVote for v in self.votes):
            raise ValueError('IMMUTABLE_VOTES_REQUIRED')
        if len({v.name for v in self.votes}) != len(self.votes):
            raise ValueError('DUPLICATE_VOTE')


@dataclass(frozen=True)
class RegimeEvidence:
    regime: Regime
    entry_allowed: bool
    direction: Action  # WAIT means neutral, not an inferred direction
    reason_code: str

    def __post_init__(self):
        choice(self.regime, REGIMES)
        boolean(self.entry_allowed)
        choice(self.direction, ACTIONS)
        code(self.reason_code)


@dataclass(frozen=True)
class EnsembleEvidence:
    action: Action
    buy_score: float
    sell_score: float
    margin: float
    core_count: int
    ai_min_confidence: int
    suggested_rr: float
    execution_veto: bool
    reason: str

    def __post_init__(self):
        choice(self.action, ACTIONS)
        for value in (self.buy_score, self.sell_score, self.margin, self.suggested_rr):
            number(value)
        if type(self.core_count) is not int or self.core_count < 0:
            raise ValueError('INVALID_CORE_COUNT')
        confidence(self.ai_min_confidence)
        boolean(self.execution_veto)
        text(self.reason)


@dataclass(frozen=True)
class AIConfirmation:
    market: MarketIdentity
    action: Action
    confidence: int
    status: str
    regime: Regime
    reason: str

    def __post_init__(self):
        if type(self.market) is not MarketIdentity:
            raise ValueError('MARKET_IDENTITY_REQUIRED')
        choice(self.action, ACTIONS)
        confidence(self.confidence)
        choice(self.status, ('SUCCESS', 'ERROR', 'REFUSED', 'INVALID', 'INCOMPLETE'))
        choice(self.regime, REGIMES)
        text(self.reason)


@dataclass(frozen=True)
class RiskDecision:
    market: MarketIdentity
    direction: Action
    allowed: bool
    reason_code: str
    planned_risk_usd: float | None = None
    planned_reward_usd: float | None = None
    lot: float | None = None
    sl: float | None = None
    tp: float | None = None
    plan_source: str = 'UNASSESSED'

    def __post_init__(self):
        if type(self.market) is not MarketIdentity:
            raise ValueError('MARKET_IDENTITY_REQUIRED')
        choice(self.direction, ACTIONS)
        boolean(self.allowed)
        code(self.reason_code)
        text(self.plan_source)
        amounts = (self.planned_risk_usd, self.planned_reward_usd, self.lot, self.sl, self.tp)
        for value in amounts:
            if value is not None:
                number(value)
        if self.allowed and (self.direction == 'WAIT' or any(v is None or v <= 0 for v in amounts)):
            raise ValueError('APPROVED_PLAN_REQUIRED')
        if self.allowed and ((self.direction == 'BUY' and self.sl >= self.tp)
                             or (self.direction == 'SELL' and self.sl <= self.tp)):
            raise ValueError('PLAN_DIRECTION_CONFLICT')


@dataclass(frozen=True)
class Freshness:
    market_fresh: bool = False
    source_unchanged: bool = False
    bar_unchanged: bool = False
    receiver_ready: bool = False

    def __post_init__(self):
        for value in (self.market_fresh, self.source_unchanged, self.bar_unchanged, self.receiver_ready):
            boolean(value)

    @property
    def ready(self):
        return self.market_fresh and self.source_unchanged and self.bar_unchanged and self.receiver_ready


@dataclass(frozen=True)
class DecisionEvidence:
    market: MarketIdentity
    strategy: StrategyEvidence
    regime: RegimeEvidence
    ensemble: EnsembleEvidence
    ai: AIConfirmation | None
    risk: RiskDecision
    freshness: Freshness
    sources: tuple[SourceVersion, ...]
    ai_required: bool = True

    def __post_init__(self):
        for value, cls in ((self.market, MarketIdentity), (self.strategy, StrategyEvidence),
                           (self.regime, RegimeEvidence), (self.ensemble, EnsembleEvidence),
                           (self.risk, RiskDecision), (self.freshness, Freshness)):
            if type(value) is not cls:
                raise ValueError('INVALID_EVIDENCE_TYPE')
        if self.ai is not None and type(self.ai) is not AIConfirmation:
            raise ValueError('INVALID_AI_TYPE')
        boolean(self.ai_required)
        if type(self.sources) is not tuple or not self.sources or any(type(s) is not SourceVersion for s in self.sources):
            raise ValueError('IMMUTABLE_SOURCE_VERSIONS_REQUIRED')
        if len({s.name for s in self.sources}) != len(self.sources):
            raise ValueError('DUPLICATE_SOURCE')


@dataclass(frozen=True)
class FinalDecision:
    action: Action
    reason_code: str
    evidence: DecisionEvidence
    schema_version: str = field(default='hybrid-decision-v1', init=False)
    mode: str = field(default='PREVIEW', init=False)

    def __post_init__(self):
        choice(self.action, ACTIONS)
        code(self.reason_code)
        if type(self.evidence) is not DecisionEvidence:
            raise ValueError('INVALID_EVIDENCE_TYPE')
        if self.action != 'WAIT' and (not self.evidence.risk.allowed or not self.evidence.freshness.ready):
            raise ValueError('DIRECTION_REQUIRES_RISK_AND_FRESHNESS')

    def to_dict(self):
        # Detached primitives only; mutating the result cannot alter evidence.
        return json.loads(self.to_json())

    def to_json(self):
        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False, allow_nan=False)
