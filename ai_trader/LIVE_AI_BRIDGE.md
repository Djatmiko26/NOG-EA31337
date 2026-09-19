# Live AI Bridge V1 — analisis candle live, DRYRUN saja

Jalur baru: MT5 demo -> 120 candle tertutup -> indikator -> 30 candle + quote
terkini -> OpenAI -> HTTP lokal -> `NOG_LiveAI_DRYRUN` di chart M5.
Tidak ada fungsi pembukaan, penutupan atau perubahan order di tiga file baru.
Risk engine 0,25%, lot, SL/TP, daily-loss limit dan eksekusi belum diimplementasikan.
Kode EA lama di repository terpisah dan tidak boleh dianggap read-only.

## Langkah pengguna (Windows)

1. Ketik `QUIT` di konsol `demo>` milik `ea_bridge_demo.py`. Tunggu kembali ke
   prompt PowerShell. Hentikan `market_monitor.py` juga agar API-nya tidak berjalan
   terpisah. Tidak perlu menghapus program, `.env`, snapshot atau database lama.
2. Di PowerShell:

```powershell
cd C:\NOG-EA31337-OpenAI\ai_trader
git pull --ff-only origin openai-trader-v1
& C:\venv\Scripts\python.exe .\live_ai_bridge.py --check
```

`C:\venv` adalah lingkungan Python yang dipakai pada setup proyek sebelumnya.
Bila virtualenv Anda berbeda, gunakan python.exe dari lingkungan yang berisi
MetaTrader5, openai dan python-dotenv. `--check` menghubungkan MT5 demo dan membaca
120 candle; tidak memanggil OpenAI, tidak membuat ledger, dan bukan tes izin API.
Jangan ganti `.env` yang sudah bekerja. Model dibaca dari `OPENAI_MODEL`, dengan
default model proyek yang sudah digunakan, `gpt-5.6-luna`. Tidak ada fallback model.
`SYMBOL`/`MT5_PATH` diwarisi dari `.env`; hanya M5 didukung versi ini.

3. Salin **file baru** `mt5\NOG_LiveAI_DRYRUN.mq5` ke Data Folder terminal aktif:
   MT5 > File > Open Data Folder > MQL5 > Experts. Buka salinan di MetaEditor dan
   Compile F7. Jangan lanjut bila ada error. Lepaskan `NOG_SignalReceiver_DRYRUN`
   yang lama dari chart; pasang `NOG_LiveAI_DRYRUN` pada satu chart XAUUSD M5.
   Akun harus demo; **Algo Trading tetap OFF**. Tetap izinkan WebRequest untuk:

```text
http://127.0.0.1:8765
```

Gunakan chart biasa, bukan Strategy Tester. `API_UNAVAILABLE` sebelum Python
baru berjalan wajar. Receiver baru tidak menerima format TEST_ONLY yang lama.

4. **Mode ini memakai kredit OpenAI API**, maksimal TIGA percobaan tambahan pada
   database live yang baru, bukan tiga per restart:

```powershell
& C:\venv\Scripts\python.exe .\live_ai_bridge.py --run
```

Program menunggu receiver baru mem-poll API dan kemudian **candle M5 berikutnya
selesai**. Tidak menganalisis candle lama saat startup, tidak backfill sinyal yang
terlewat, dan tidak menerima BUY manual. Dalam pasar aktif normal, candle M5
berikutnya dapat memerlukan sekitar lima menit; saat pasar tutup tidak ada sinyal.
Heartbeat tercetak setiap 30 detik. Jika spread/ATR melewati batas, panggilan dilewati.

Contoh penanda, BUKAN hasil yang dijanjikan:

```text
CHECK_OK | DEMO | ... | OPENAI_CALLS=0
LIVE AI BRIDGE READY | ... | attempts=0/3
WAIT_EA_RECEIVER ...
WAIT_NEW_CLOSED_CANDLE
NEW_CLOSED_CANDLE | bar_raw=...
OPENAI_RESULT | status=SUCCESS | action=WAIT | delivery=READY_FOR_DRYRUN
```

Di Experts MT5: `PARSER_SELF_TEST_PASS`, `NO_SIGNAL`, lalu
`ACCEPTED_AI_SIGNAL BUY/SELL/WAIT` dan mungkin `DUPLICATE_IGNORED`.
`READY_FOR_DRYRUN` artinya Python menyediakan respons; hanya log Experts yang
mengonfirmasi penerimaan di EA. ACCEPTED bukan order/risk approval atau bukti profit.
Sinyal berlaku paling lama 30 detik dan tidak diperpanjang oleh polling.
Setelah habis masa berlaku, chart kembali `NO_SIGNAL`; log penerimaan tetap ada.

Setelah tiga attempt, tidak ada request tambahan. Proses tetap menyediakan API
kosong setelah sinyal terakhir habis masa berlaku, sampai Ctrl+C. Restart tidak
mengembalikan jatah; pada ledger penuh program keluar tanpa membuat client OpenAI.

```powershell
& C:\venv\Scripts\python.exe .\live_ai_bridge.py --status
```

`--status` hanya membaca ledger lokal. Tidak ada API, MT5 atau pembacaan `.env`.

## Pembatasan biaya, error dan file

File baru: `data/live_ai_bridge.sqlite3`. Payload dan respons disimpan lokal;
nomor akun, password dan API key tidak dimasukkan ke payload atau ledger. Identitas
source disimpan sebagai hash untuk menolak pergantian source tanpa sengaja.
Model/schema/instruksi/source/hash kode dibekukan ketika ledger dibuat.
Semua database pilot, baseline, blind, audit dan file lama tidak diubah.
Direktori data diabaikan oleh .gitignore; jangan unggah `.env` atau ledger.

Setiap attempt direservasi dalam transaksi SQLite sebelum request. Batas tiga
mencakup request gagal/tidak pasti. `max_retries=0`, timeout SDK 25 detik, output
maksimal 2.048 token per request. SDK timeout tidak menjamin seluruh request selesai
tepat dalam 25 detik; jawaban terlalu terlambat tidak diteruskan. Batas request/token
ini BUKAN batas dolar. Timeout bisa dikenai biaya tanpa respons yang bisa dipakai.

Refusal, incomplete, JSON salah dan API error tidak diubah menjadi WAIT. Program
berhenti; ledger dengan ERROR/STARTED/tidak sukses memblokir run berikutnya sampai
masalah ditinjau. Jangan hapus ledger untuk mencoba lagi atau menambah batas. Proteksi
berlaku pada ledger ini saja, bukan kopi proyek, program lain atau billing total.
Jangan jalankan dua server atau dua monitor. Konflik port menghentikan program;
program tidak otomatis mematikan proses lain. Tidak ada pembelian quota otomatis.

## Pemilihan data dan freshness

Hanya angka candle tertutup (posisi 1..120) masuk indikator; 30 terakhir dikirim.
Quote Bid/Ask live dikirim pada field terpisah, bukan disamarkan sebagai OHLC tertutup.
EWM finite-window meniru konvensi lama, termasuk RSI flat=100. Bukan klaim seeding
identik indikator bawaan MT5. Tidak menggunakan indikator dari candle berjalan.

Gerbang lokal hanya memeriksa data/quote, ATR positif dan current spread/ATR <=0,12.
Tidak menghitung score arah atau mengirim `local_filter`, candidate_direction,
score bull/bear, alasan strategi maupun data akun. Angka 0,12 merupakan asumsi
pengujian engineering, bukan parameter yang telah terbukti menguntungkan.

Startup hanya mengamati. Calon request membutuhkan transisi raw M5 berturutan,
tick advancing yang diamati proses, interval polling pendek dan clock stabil.
Jeda/data putus/pergantian akun/lompatan bar menyebabkan resync tanpa panggilan.
Tidak mengurangi offset UTC dari raw timestamp, tidak memakai session filter.
Ini belum sertifikasi kesegaran feed broker: kedua sisi harus melihat raw bar ID
sama; data historis, timezone dan kemungkinan data broker salah belum diverifikasi.

Selama request, HTTP server berjalan pada thread terpisah tanpa mengakses MT5.
Sinyal sebelumnya dibersihkan. Setelah respons, source/closed bar/tick/spread,
kehadiran receiver dan waktu diperiksa lagi. Jawaban lewat 45 detik dari deteksi
close, bar/source berubah atau pemeriksaan gagal tidak diteruskan. Server juga
membuang sinyal ketika monitor macet, tick berhenti teramati maju, clock berubah,
bar berganti atau TTL habis. Tidak ada fallback sinyal lokal/manual.

EA memakai OnTimer 2 detik dan WebRequest timeout 1 detik. Ia memeriksa demo,
M5/symbol, protocol, ID, TTL, clock dan kesamaan `iTime(...,1)` dengan raw ID
candle Python sebelum menampilkan penerimaan. Tidak memeriksa lot, SL/TP atau
menilai apakah perdagangan aman. Memory duplikat 256 ID per attachment, bukan
idempotency order persisten dan bukan perlindungan antar-chart/restart.

## Keamanan transport dan batas penelitian

Port hanya `127.0.0.1:8765`, tanpa endpoint publik. Header receiver adalah pemeriksaan
kompatibilitas, BUKAN autentikasi. Proses lokal lain dapat menirunya. Transport ini
bukan gateway produksi dan tidak boleh diekspos ke LAN/internet.
API key hanya dibaca Python; endpoint OpenAI resmi dengan TLS. EA tidak menerima key.

Membaca candle live dan mendapatkan JSON valid tidak membuktikan hasil trading.
Tidak ada backtest profit, data validation baru yang disertifikasi, calibrated
confidence, ukuran posisi, saldo atau drawdown pada pengujian sambungan ini.

## Bukti pengujian

44 tes terfokus lulus pada Python 3.13.5 di lingkungan asisten: input sintetis,
OpenAI/MT5 tiruan, SQLite sementara dan HTTP loopback. Termasuk simulasi loop dengan
batas tiga request, restart tanpa replay, candle tertutup, rollover/gap/clock,
penolakan hasil terlambat/bar berubah, dan status error bukan WAIT.
Tidak ada panggilan berbayar, koneksi terminal nyata atau compiler MQL5 di pengujian.
EA baru harus dikompilasi F7 dan diuji pada MT5 pengguna. Seluruh suite lama tidak
dijalankan ulang; kode penelitian lama dipertahankan tanpa perubahan.

```powershell
python -m unittest discover -s tests -p "test_live_ai_bridge.py" -v
```

Referensi resmi (perilaku platform, bukan bukti profit):
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://developers.openai.com/api/reference/python/
- https://developers.openai.com/api/docs/models/gpt-5.6-luna
- https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrompos_py
- https://www.mql5.com/en/docs/series/itime
- https://www.mql5.com/en/docs/network/webrequest
