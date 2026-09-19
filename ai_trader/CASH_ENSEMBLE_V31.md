# Cash Ensemble V3.1 — Weighted, Regime-Aware, Preview Only

V3.1 dibuat sebagai jalur baru. Ledger V3 dan V2 tidak diubah.

## Arsitektur

XAUUSD M5 -> regime detector -> 10 logic votes -> weighted aggregator ->
independent OpenAI confirmation -> PREVIEW packet -> exact EA receipt ->
RR lab.

## Bobot logika

- market_structure: 2.00
- ema_trend: 2.00
- breakout: 1.50
- ema_slope: 1.50
- pullback: 1.00
- rsi_momentum: 0.75
- candle_impulse: 0.75
- volume_impulse: 0.50
- atr_expansion: 0.50
- execution_quality: hard VETO, no directional weight

Total directional weight = 10.50.

## Regime

- BULL_TREND
- BEAR_TREND
- RANGE
- VOLATILE
- UNCLEAR

RANGE suppresses directional entry.
VOLATILE and UNCLEAR require stronger weighted score, larger margin, more core
support, and AI confidence >= 75.
Trend regimes require AI confidence >= 70.

## Directional gate

Base requirements:
- weighted score >= 6.0
- weighted margin >= 3.0
- at least 2 core logics agree
- no execution-quality veto
- direction must agree with bull/bear trend regime when regime is directional

VOLATILE / UNCLEAR:
- weighted score >= 7.0
- weighted margin >= 4.0
- at least 3 core logics agree

## Adaptive RR suggestion

Research-only:
- volatile/unclear -> 1.00R
- score >= 6.5 and margin >= 4.0 -> 1.25R
- score >= 7.5 and margin >= 5.0 -> 1.50R
- score >= 8.5 and margin >= 6.0 -> 2.00R

EA still remains PREVIEW and records its exact 1R cash plan. The V3.1 RR lab
evaluates the alternative ratios historically; it does not open multiple orders.

## AI role

OpenAI never receives the local direction or weighted score in its market payload.
It independently analyzes the same market snapshot.

Final BUY/SELL requires:
- local weighted direction passes
- AI chooses same direction
- AI confidence meets regime threshold
- market/quote/source/receiver remain fresh

Local WAIT/VETO consumes zero API calls.

## Safety

- USD DEMO only
- XAUUSD M5
- PREVIEW only
- InpEnableDemoOrders=false
- InpAcknowledgeHighRisk=false
- Algo Trading OFF
- no broker order primitives in V3.1
- one new event maximum per --run
- event cap 40
- API candidate cap 12
- ledger: data/cash_ensemble_v31.sqlite3
- frozen source/model/logic settings after ledger starts

## Validation

~~~powershell
cd C:\NOG-EA31337-OpenAI\ai_trader

& C:\venv\Scripts\python.exe -m py_compile .\ensemble_weighted_v31.py .\cash_ensemble_v31.py .\cash_rr_lab_v31.py

& C:\venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_ensemble_weighted_v31.py" -v

& C:\venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_cash_ensemble_v31.py" -v
~~~

## Status

~~~powershell
& C:\venv\Scripts\python.exe .\cash_ensemble_v31.py --status
~~~

Initial expected output:

~~~text
V31_LEDGER_NOT_STARTED | EVENTS=0/40 | API=0/12 | API_CALLS=0
~~~

## One event

~~~powershell
& C:\venv\Scripts\python.exe .\cash_ensemble_v31.py --run
~~~

## RR evaluation after confirmed BUY/SELL

~~~powershell
& C:\venv\Scripts\python.exe .\cash_rr_lab_v31.py --commission-per-lot 0 --fixed-fee 0
~~~
