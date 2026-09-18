# Cash Pilot V2 — XAUUSD M5 PREVIEW research

Pilot V2 melanjutkan bukti CashDemo V1 tanpa menghapus atau mengubah ledger V1.

## Tujuan

Mengumpulkan sinyal live XAUUSD M5 secara disiplin dan menilai hasil hipotetis
TP-vs-SL. V2 bukan mode order dan bukan bukti profitabilitas.

## Batas keselamatan

- akun: USD DEMO saja
- simbol/timeframe: XAUUSD M5
- cash_pilot_v2.py hanya menerbitkan packet PREVIEW
- maksimum 1 attempt OpenAI baru per sekali --run
- maksimum 12 attempt dalam ledger V2
- ledger: data/cash_pilot_v2.sqlite3
- V1 data/cash_demo.sqlite3 tidak disentuh
- jangan delete/edit ledger untuk mengulang attempt
- bila source/model/settings berubah setelah ledger V2 dimulai, buat versi pilot baru;
  jangan mencairkan frozen spec
- tidak ada mode akun riil

## Kondisi MT5 selama V2

Gunakan EA NOG_CashDemo di XAUUSD M5 dengan:

~~~text
InpEnableDemoOrders = false
InpAcknowledgeHighRisk = false
InpPauseNewEntries = false
InpRoundTripCommissionPerLot = 0
InpRoundTripFixedFee = 0
Algo Trading = OFF
~~~

Nilai fee 0/0 hanya untuk MetaQuotes-Demo yang sedang diuji. Jangan anggap broker
riil memiliki biaya yang sama.

## Test sebelum run pertama

~~~powershell
cd C:\NOG-EA31337-OpenAI\ai_trader
& C:\venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_cash_pilot_v2.py" -v
~~~

Test ini synthetic/local: tidak memanggil OpenAI dan tidak mengirim order.

## Status read-only

~~~powershell
& C:\venv\Scripts\python.exe .\cash_pilot_v2.py --status
~~~

Sebelum attempt pertama:

~~~text
V2_LEDGER_NOT_STARTED | ATTEMPTS=0/12 | API_CALLS=0
~~~

## Satu attempt

~~~powershell
& C:\venv\Scripts\python.exe .\cash_pilot_v2.py --run
~~~

Program menunggu EA yang cocok dan candle M5 baru. Ia tidak memanggil OpenAI
sebelum receiver cocok. Setelah satu attempt berhasil/selesai, proses berhenti;
jalankan kembali secara eksplisit jika memang ingin attempt berikutnya.

Contoh:

~~~text
V2_READY | PREVIEW_ONLY | attempts=0/12 | max_new_this_run=1
V2_RESULT | SUCCESS | action=WAIT | delivery=READY_FOR_EA | attempts=1/12
~~~

Untuk BUY/SELL, EA tetap harus menunjukkan plan preview dan tidak boleh mengirim
order:

~~~text
NOG_CASH | PLAN | BUY/SELL | lot=... | SL=... | TP=...
NOG_CASH | PREVIEW_PLAN_OK | NO_ORDER
~~~

## Audit outcome tanpa API

Setelah ada BUY/SELL dan sudah tersedia history sesudah sinyal:

~~~powershell
& C:\venv\Scripts\python.exe .\audit_cash_v2.py --commission-per-lot 0 --fixed-fee 0
~~~

Audit menggunakan quote/ATR yang tersimpan dan data M1/tick MT5 untuk menentukan
TP_FIRST, SL_FIRST, UNRESOLVED, atau AMBIGUOUS. Ini hipotetis karena preview
tidak menjamin fill pada entry yang disimpan.

## Cara membaca bukti

WAIT bukan loss dan bukan win. BUY/SELL yang memiliki first-hit outcome memberi
satu observasi historis untuk plan tersebut. Beberapa TP_FIRST tidak otomatis
membuktikan edge atau profit live; slippage, spread, fee, seleksi sampel, dan
ukuran sampel tetap harus dinilai.

V2 tidak boleh diubah menjadi order mode. Jika bukti V2 nantinya cukup untuk
menguji eksekusi lagi, buat pilot order versi terpisah dengan ledger dan cap baru
agar bukti PREVIEW tidak tercampur dengan eksperimen eksekusi.
