"""USD 10/10 planning arithmetic. No network, credentials or order functions.

Prices and slopes must come from the broker. The SL limit includes known
round-trip fees and a declared stress assumption, not a guaranteed maximum loss.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math

LOSS_USD = 10.0
PROFIT_USD = 10.0
MAX_LOT = 0.10
STRESS_POINTS = 20
VERSION = 'xau-cash-10-10-v1'

@dataclass(frozen=True)
class CashPlan:
    volume: float
    entry: float
    sl: float
    tp: float
    stressed_loss: float
    stressed_profit: float
    fee: float
    technical_sl: float


def positive(*values: float) -> bool:
    return all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in values)


def make_plan(buy: bool, bid: float, ask: float, atr: float, tick: float,
              stop_distance: float, stress: float, minimum: float, step: float,
              maximum: float, loss_per_price_lot: float, gain_per_price_lot: float,
              commission: float, fixed_fee: float) -> CashPlan:
    if type(buy) is not bool or not positive(bid, ask, atr, tick, minimum, step,
                                            maximum, loss_per_price_lot, gain_per_price_lot):
        raise ValueError('INVALID_POSITIVE_INPUT')
    if any(type(v) not in (int,float) or not math.isfinite(v) or v < 0
           for v in (stop_distance, stress, commission, fixed_fee)) or ask < bid:
        raise ValueError('INVALID_COST_OR_QUOTE')
    cap = min(maximum, MAX_LOT)
    if cap < minimum or fixed_fee >= LOSS_USD:
        raise ValueError('MIN_LOT_OR_FIXED_FEE_EXCEEDS_LIMIT')
    entry = ask if buy else bid
    technical = (math.floor(min(entry-1.5*atr, bid-stop_distance-tick)/tick)*tick if buy
                 else math.ceil(max(entry+1.5*atr, ask+stop_distance+tick)/tick)*tick)
    if technical <= 0:
        raise ValueError('INVALID_TECHNICAL_STOP')
    per_lot = loss_per_price_lot * (abs(entry-technical)+2*stress) + commission
    raw = min(cap, (LOSS_USD-fixed_fee)/per_lot)
    if raw < minimum:
        raise ValueError('MIN_LOT_EXCEEDS_CASH_LIMIT')
    volume = minimum + math.floor((raw-minimum)/step+1e-10)*step
    if volume > raw+1e-12:
        volume -= step
    if volume < minimum-1e-12:
        raise ValueError('MIN_LOT_EXCEEDS_CASH_LIMIT')
    fee = volume*commission+fixed_fee
    distance = (LOSS_USD-fee)/(loss_per_price_lot*volume)-2*stress
    # Round stop TOWARD entry to keep the planned loss at or below the ceiling.
    sl = (math.ceil((entry-distance)/tick)*tick if buy
          else math.floor((entry+distance)/tick)*tick)
    target_distance = (PROFIT_USD+fee)/(gain_per_price_lot*volume)+2*stress
    tp = (math.ceil((entry+target_distance)/tick)*tick if buy
          else math.floor((entry-target_distance)/tick)*tick)
    if not positive(volume, sl, tp):
        raise ValueError('INVALID_CASH_GEOMETRY')
    eps = tick*1e-6
    if (buy and (sl > technical+eps or sl >= bid-stop_distance or tp <= max(ask,bid+stop_distance))
        or not buy and (sl < technical-eps or sl <= ask+stop_distance or tp >= min(bid,ask-stop_distance))):
        raise ValueError('STOP_GRID_OR_BROKER_DISTANCE')
    loss = volume*loss_per_price_lot*(abs(entry-sl)+2*stress)+fee
    profit = volume*gain_per_price_lot*(abs(tp-entry)-2*stress)-fee
    if (loss > LOSS_USD+1e-8 or loss <= 0 or profit < PROFIT_USD-1e-8
        or profit > PROFIT_USD+gain_per_price_lot*volume*tick+1e-8):
        raise ValueError('FINAL_CASH_CHECK')
    return CashPlan(volume, entry, sl, tp, loss, profit, fee, technical)


def broker_plan(mt5, symbol: str, bid: float, ask: float, atr: float,
                buy: bool, commission: float, fixed_fee: float) -> CashPlan:
    account, info = mt5.account_info(), mt5.symbol_info(symbol)
    if account is None or account.trade_mode != getattr(mt5, 'ACCOUNT_TRADE_MODE_DEMO', 0):
        raise ValueError('DEMO_ACCOUNT_REQUIRED')
    if account.currency != 'USD' or symbol != 'XAUUSD' or info is None or info.currency_profit != 'USD':
        raise ValueError('XAUUSD_AND_USD_ACCOUNT_REQUIRED')
    if not positive(account.equity, account.balance) or min(account.equity, account.balance) < 100:
        raise ValueError('DEMO_EQUITY_BALANCE_MUST_BE_AT_LEAST_100_USD')
    size, minimum = float(info.trade_tick_size), float(info.volume_min)
    if not positive(size, minimum, float(info.point), float(info.volume_step)):
        raise ValueError('INVALID_BROKER_SPECIFICATION')
    typ = mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL
    entry, sign = (ask, 1) if buy else (bid, -1)
    loss_tick = mt5.order_calc_profit(typ, symbol, minimum, entry, entry-sign*size)
    gain_tick = mt5.order_calc_profit(typ, symbol, minimum, entry, entry+sign*size)
    if not positive(-loss_tick if loss_tick is not None else 0,
                    gain_tick if gain_tick is not None else 0):
        raise ValueError('BROKER_TICK_CALC_FAILED')
    maximum = float(info.volume_max)
    limit = float(getattr(info, 'volume_limit', 0))
    if limit > 0:
        maximum = min(maximum, limit)
    stress = STRESS_POINTS*float(info.point)
    plan = make_plan(buy, bid, ask, atr, size, float(info.trade_stops_level)*float(info.point),
                     stress, minimum, float(info.volume_step), maximum,
                     -loss_tick/(minimum*size), gain_tick/(minimum*size), commission, fixed_fee)
    # Recheck the full broker calculation; do not trust an extrapolated tick value.
    loss = mt5.order_calc_profit(typ, symbol, plan.volume, entry+sign*stress, plan.sl-sign*stress)
    profit = mt5.order_calc_profit(typ, symbol, plan.volume, entry+sign*stress, plan.tp-sign*stress)
    if (loss is None or profit is None or not math.isfinite(loss) or not math.isfinite(profit)
        or loss >= 0 or abs((-loss+plan.fee)-plan.stressed_loss) > 1e-5
        or abs((profit-plan.fee)-plan.stressed_profit) > 1e-5
        or -loss+plan.fee > LOSS_USD+1e-8 or profit-plan.fee < PROFIT_USD-1e-8):
        raise ValueError('BROKER_FINAL_CASH_MISMATCH')
    return plan
