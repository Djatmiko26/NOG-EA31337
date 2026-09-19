# Dynamic EMA Pullback — Research Module

Implementasi ini mengikuti dokumen strategi XAUUSD M5 yang diberikan pengguna.

## Aturan yang diimplementasikan

Trend:
- BUY: EMA50 > EMA200
- SELL: EMA50 < EMA200

Pullback Candle [2]:
- BUY: close Candle [2] < EMA50 atau EMA200
- SELL: close Candle [2] > EMA50 atau EMA200

Trigger Candle [1]:
- BUY: bullish
- SELL: bearish
- minimum body default 60 pips
- opposite wick maximum 20% dari total high-low

Plan:
- BUY SL = Low Candle [1] - SL buffer
- SELL SL = High Candle [1] + SL buffer
- TP = risk distance x RRR, default 1.5R

Management model:
- BEP trigger default 15 pips
- trailing default 20 pips setelah BEP aktif
- fee buffer explicit, default 0 karena spesifikasi tidak memberi angka

Position limit:
- parameter source default max active positions = 3
- fungsi can_open() memodelkan batas tersebut
- tidak diaktifkan pada live/pilot execution saat ini

## Interpretasi pip

Dokumen menyatakan 60 pips = 6.0 points/price units. Modul memakai:
- pip_price = 0.1
- 60 pips -> 6.0
- 10 pips -> 1.0
- 15 pips -> 1.5
- 20 pips -> 2.0

Ini dibuat sebagai input, bukan asumsi broker permanen. Jika definisi pip broker berbeda,
ubah --pip-price pada checker.

## File

- dynamic_ema_pullback.py — pure logic
- dynamic_ema_pullback_check.py — MT5 read-only checker
- tests/test_dynamic_ema_pullback.py — synthetic specification tests

Tidak ada OrderSend, OpenAI, atau ledger write di modul/checker.

## Test

~~~powershell
cd C:\NOG-EA31337-OpenAI\ai_trader
git pull --ff-only origin openai-trader-v1

& C:\venv\Scripts\python.exe -m py_compile .\dynamic_ema_pullback.py .\dynamic_ema_pullback_check.py

& C:\venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_dynamic_ema_pullback.py" -v
~~~

## Check kondisi market live tanpa order

~~~powershell
& C:\venv\Scripts\python.exe .\dynamic_ema_pullback_check.py
~~~

Contoh output directional:

~~~text
DYN_EMA | XAUUSD M5 | action=SELL | ...
TRIGGER | body=... | opposite_wick_ratio=...
PLAN | SELL | entry_reference=... | SL=... | TP=... | RR=1.50
REASON | EMA50<EMA200 + Candle[2] pullback + bearish trigger
DYN_EMA_DONE | SPEC_RESEARCH_ONLY | NO_API | NO_ORDER
~~~

Jika syarat belum lengkap:

~~~text
DYN_EMA | XAUUSD M5 | action=WAIT | ...
REASON | SELL conditions failed: ...
~~~

## Tahap berikut

Modul ini belum diberi bobot di V3.1 karena ledger V3.1 membekukan source/settings.
Setelah hasil standalone diperiksa, modul ini dapat menjadi kandidat logic tambahan
pada versi ensemble berikutnya dengan ledger baru.
