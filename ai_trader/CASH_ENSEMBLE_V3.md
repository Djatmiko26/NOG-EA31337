# Cash Ensemble V3 — 10 logika, 1 keputusan, 1 posisi

V3 adalah jalur riset terpisah dari V1/V2. Ledger lama tidak dihapus atau di-reset.

## Prinsip utama

Satu candle XAUUSD M5 dinilai oleh 10 logika lokal. Logika-logika tersebut tidak
membuka order. Mereka hanya memberikan BUY, SELL, WAIT, atau VETO.

Sepuluh logika:

1. EMA trend alignment
2. EMA slope
3. 12-bar breakout
4. trend pullback ke EMA20
5. RSI momentum
6. candle impulse relatif ATR
7. market structure
8. tick-volume impulse
9. ATR expansion + directional close
10. execution quality veto (spread/ATR dan volatilitas minimum)

Aggregator membutuhkan:
- minimal 6 vote searah
- margin BUY-vs-SELL minimal 3
- minimal 2 core logic searah dari EMA trend, EMA slope, breakout, market structure
- tidak ada execution-quality VETO

Jika syarat gagal, hasil lokal WAIT dan OpenAI tidak dipanggil.

Jika konsensus lokal BUY/SELL, OpenAI menganalisis payload market yang sama secara
independen. Arah ensemble tidak dimasukkan ke prompt/payload OpenAI.

Final BUY/SELL hanya bila:
- AI memilih arah yang sama
- confidence AI >= 65
- quote/bar/source/receiver masih fresh
- semua guard CashDemo tetap lolos

Jika AI WAIT, berlawanan, atau confidence di bawah ambang, final WAIT.

## RR fleksibel

V3 merekam suggested RR berdasarkan kekuatan konsensus:
- support 6 -> 1.00R
- support 7 -> 1.25R
- support 8 -> 1.50R
- support 9 -> 2.00R

Suggested RR masih research-only. EA preview tetap memakai cash-plan yang sudah
divalidasi. cash_rr_lab_v3.py menguji 10 target paralel tanpa membuka 10 order:

0.50R, 0.75R, 1.00R, 1.25R, 1.50R, 1.75R, 2.00R, 2.50R, 3.00R, 4.00R.

Satu sinyal tetap satu exact EA receipt. Risk-Reward Lab hanya menguji perjalanan
harga hipotetis terhadap banyak target.

## Safety

- DEMO USD, XAUUSD M5
- PREVIEW ONLY
- EA: InpEnableDemoOrders=false
- EA: InpAcknowledgeHighRisk=false
- Algo Trading OFF
- tidak ada broker order function di V3
- local WAIT/VETO = 0 API calls
- maksimal 1 event baru per sekali --run
- API candidate cap = 12
- event cap = 40
- ledger V3 = data/cash_ensemble_v3.sqlite3
- source/model/threshold dibekukan setelah ledger dimulai

## Test sebelum start

~~~powershell
cd C:\NOG-EA31337-OpenAI\ai_trader

& C:\venv\Scripts\python.exe -m py_compile .\ensemble_logic.py .\cash_ensemble_v3.py .\cash_rr_lab_v3.py

& C:\venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_ensemble_logic.py" -v

& C:\venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_cash_ensemble_v3.py" -v
~~~

Tidak ada perintah di atas yang memanggil OpenAI atau mengirim order.

## Status

~~~powershell
& C:\venv\Scripts\python.exe .\cash_ensemble_v3.py --status
~~~

Sebelum event pertama:

~~~text
V3_LEDGER_NOT_STARTED | EVENTS=0/40 | API=0/12 | API_CALLS=0
~~~

## Satu evaluasi candle

~~~powershell
& C:\venv\Scripts\python.exe .\cash_ensemble_v3.py --run
~~~

Program menunggu receiver EA dan candle M5 baru.

Contoh local WAIT:

~~~text
V3_ENSEMBLE | action=WAIT | ...
V3_RESULT | ... reason=LOCAL_WAIT ... api=0/12
~~~

Tidak ada OpenAI call.

Contoh kandidat yang dikonfirmasi:

~~~text
V3_ENSEMBLE | action=SELL | BUY=0 | SELL=7 | ...
V3_RESULT | ... local=SELL | final=SELL | reason=CONFIRMED | delivery=READY_FOR_EA
~~~

EA tetap harus menunjukkan:

~~~text
NOG_CASH | PLAN | SELL | ...
NOG_CASH | PREVIEW_PLAN_OK | NO_ORDER
~~~

## Evaluasi RR

Setelah ada final BUY/SELL exact receipt:

~~~powershell
& C:\venv\Scripts\python.exe .\cash_rr_lab_v3.py --commission-per-lot 0 --fixed-fee 0
~~~

Lab menampilkan outcome semua 10 RR dan menandai RR yang disarankan ensemble.

## Interpretasi

V3 tidak menganggap jumlah vote atau confidence sebagai probabilitas profit.
WAIT bukan loss. Satu TP_FIRST atau SL_FIRST bukan bukti edge. Tujuan V3 adalah
mengumpulkan evidence yang bisa dibandingkan: vote pattern, AI agreement,
confidence, regime, exact EA plan, RR outcome, dan akhirnya expectancy pada
sampel yang lebih besar.
