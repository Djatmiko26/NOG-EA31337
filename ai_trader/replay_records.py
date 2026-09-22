"""Match supplied recorded evidence; never generate AI signals or broker approval."""
from dataclasses import asdict, dataclass
import re
import cash_risk
from hybrid_contract import RiskDecision
from hybrid_v31_adapter import risk_from_cash_plan
from replay_common import canonical, digest, finite, sha256

VERSION = 'bound-replay-records-v1'


def records_envelope(records, *, evidence_kind):
    # Packaging existing records is not a signal/approval generator.
    if evidence_kind not in ('RECORDED_EVIDENCE', 'SYNTHETIC_TEST'):
        raise ValueError('INVALID_EVIDENCE_KIND')
    body = dict(version=VERSION, evidence_kind=evidence_kind, records=records)
    return {'body': body, 'sha256': digest(body)}


def verify_records(document, *, allow_synthetic=False):
    if type(document) is not dict or set(document) != {'body', 'sha256'}:
        raise ValueError('INVALID_RECORDS_ENVELOPE')
    body = document['body']
    if type(body) is not dict:
        raise ValueError('INVALID_RECORDS_BODY')
    if document['sha256'] != digest(body) or body.get('version') != VERSION:
        raise ValueError('RECORDS_HASH_MISMATCH')
    kind = body.get('evidence_kind')
    if kind not in ('RECORDED_EVIDENCE', 'SYNTHETIC_TEST') or (kind == 'SYNTHETIC_TEST' and not allow_synthetic):
        raise ValueError('SYNTHETIC_EVIDENCE_NOT_AUTHORIZED')
    records = body.get('records')
    if type(records) is not list:
        raise ValueError('RECORD_LIST_REQUIRED')
    seen, ids = set(), set()
    for record in records:
        if type(record) is not dict:
            raise ValueError('INVALID_RECORD')
        bar = record.get('decision_bar_raw')
        sid = record.get('signal_id')
        if type(bar) is not int or bar <= 0 or bar in seen:
            raise ValueError('DUPLICATE_OR_INVALID_RECORD_BAR')
        if type(sid) is not str or re.fullmatch('[a-f0-9]{32}', sid) is None or sid in ids:
            raise ValueError('DUPLICATE_OR_INVALID_SIGNAL_ID')
        sha256(record.get('origin_sha256'))
        seen.add(bar); ids.add(sid)
    return {r['decision_bar_raw']: r for r in records}


def match_record(record, expected):
    if record is None:
        return False, 'RECORD_UNAVAILABLE'
    if record.get('decision_bar_raw') != expected['bar_raw']:
        return False, 'RECORDED_BAR_MISMATCH'
    if record.get('binding') != expected:
        return False, 'RECORDED_BINDING_MISMATCH'
    return True, 'MATCHED'


@dataclass(frozen=True)
class ReplayRisk:
    status: str  # APPROVED_RECORDED / REJECTED / UNAVAILABLE
    reason_code: str
    decision: RiskDecision


def recorded_risk(record, expected, market, direction, *, fee_assumptions):
    def unavailable(reason):
        return ReplayRisk('UNAVAILABLE', reason, RiskDecision(market, direction, False, reason))
    matched, reason = match_record(record, expected)
    if not matched:
        return unavailable(reason)
    saved = record.get('risk')
    if not isinstance(saved, dict):
        return unavailable('RISK_RECORD_UNAVAILABLE')
    if (saved.get('signal_id') != record['signal_id'] or saved.get('binding') != expected
            or saved.get('direction') != direction or saved.get('fee_assumptions') != fee_assumptions
            or saved.get('plan_version') != cash_risk.VERSION):
        return unavailable('RISK_RECORD_MISMATCH')
    if saved.get('status') == 'REJECTED':
        return ReplayRisk('REJECTED', 'RECORDED_RISK_REJECTED',
                          RiskDecision(market, direction, False, 'RECORDED_RISK_REJECTED'))
    if saved.get('status') != 'APPROVED_RECORDED' or saved.get('kind') != 'EXACT_CASH_PLAN_RECEIPT':
        return unavailable('EXACT_APPROVAL_UNAVAILABLE')
    if (fee_assumptions.get('commission_per_lot') is None or fee_assumptions.get('fixed_fee') is None):
        return unavailable('FEE_ASSUMPTIONS_UNAVAILABLE')
    raw_plan = saved.get('cash_plan')
    if saved.get('plan_sha256') != digest(raw_plan):
        return unavailable('CASH_PLAN_HASH_MISMATCH')
    try:
        plan = cash_risk.CashPlan(**raw_plan)
        for name, value in asdict(plan).items():
            finite(value)
            if name != 'fee' and value == 0:
                raise ValueError('NONPOSITIVE_PLAN_FIELD')
        if not (plan.sl < plan.entry < plan.tp if direction == 'BUY' else plan.tp < plan.entry < plan.sl):
            raise ValueError('INVALID_PLAN_ENTRY_ORIENTATION')
        fee = fee_assumptions['commission_per_lot']*plan.volume+fee_assumptions['fixed_fee']
        if abs(plan.fee-fee) > 1e-8:
            return unavailable('RECORDED_FEE_MISMATCH')
        result = risk_from_cash_plan(plan, market, direction, allowed=True, reason_code='APPROVED_RECORDED')
    except (ValueError, TypeError, KeyError):
        return unavailable('INVALID_RECORDED_CASH_PLAN')
    if not result.allowed:
        return ReplayRisk('REJECTED', result.reason_code, result)
    return ReplayRisk('APPROVED_RECORDED', 'APPROVED_RECORDED', result)
