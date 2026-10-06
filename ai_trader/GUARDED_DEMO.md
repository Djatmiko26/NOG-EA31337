# Guarded Demo V1 — pengelolaan risiko dan jalur order DEMO

Implementasi baru, bukan strategi terbukti profitable dan bukan versi produksi.
Kode penelitian/DRYRUN dan SEMUA ledger lama tidak diubah. Tidak ada klaim profit
harian atau bahwa risiko aktual pasti tidak melebihi risiko rencana.

## Mulai sekarang tanpa biaya API atau order

Hentikan server lama. Biarkan MT5 demo tetap terbuka. Di PowerShell:

```powershell
cd C:\NOG-EA31337-OpenAI\ai_trader
git pull --ff-only origin openai-trader-v1
& C:\venv\Scripts\python.exe .\guarded_demo.py --check
```

`--check` menggunakan koneksi baca MT5 dan OrderCalcProfit, bukan OrderSend.
Menampilkan mata uang akun, minimum lot, ATR, SL contoh BUY/SELL serta perkiraan
kerugian minimum lot. Dua arah adalah pemeriksaan spesifikasi, BUKAN dua sinyal.
Tidak memanggil OpenAI atau menulis ledger. Simpan output untuk diperiksa.

Default modal acuan **100 dalam MATA UANG AKUN**, dibatasi lagi oleh equity dan
balance. Pada akun USD: anggaran rencana 0,25%, yaitu USD 0,25, walaupun saldo demo
USD 100.000. Jika mata uang akun bukan USD, angka 100 bukan ekuivalen USD 100.
`MIN_LOT_EXCEEDS_BUDGET` berarti tidak ada lot yang memenuhi batas tersebut.
Jangan menaikkan lot atau mengurangi SL semata-mata agar order dapat dibuka.

## Instalasi tanpa mencari folder Data Folder

```powershell
& C:\venv\Scripts\python.exe .\guarded_demo.py --install
```

Installer memastikan terminal DEMO lalu menyalin HANYA dua file baru ke
`terminal_info().data_path / MQL5 / Experts`:

- `NOG_GuardedDemo.mq5`
- `NOG_RiskMath.mqh`

File tujuan yang berbeda dicadangkan dengan akhiran `.backup.<timestamp>`.
Installer juga membuat token HTTP lokal pada `MQL5/Files/NOG_GuardedDemo/bridge.token`;
token lama dipakai kembali, tidak dicetak atau disimpan ke GitHub. Token ini BUKAN
OpenAI API key. Installer tidak mengubah .env, memasang EA ke chart, mengompilasi,
atau menyalakan Algo Trading. Sesudah copy ia menjalankan pemeriksaan risiko.

Buka file .mq5 dari lokasi instalasi yang dicetak, kompilasi F7 di MetaEditor.
Jika ada error, berhenti. Source EA belum dikompilasi/dijalankan di lingkungan
asisten. Uji C++ di bawah hanya mengompilasi fungsi matematika bersama, bukan EA.

Lepaskan EA DRYRUN lama dari chart dan pasang SATU `NOG_GuardedDemo` pada demo
XAUUSD M5. Tetap gunakan URL WebRequest `http://127.0.0.1:8765`, chart biasa,
bukan Strategy Tester. Default **InpEnableDemoOrders=false**, Algo Trading OFF.
`RISK_SELF_TEST_PASS` adalah self-test matematika yang dijalankan EA ketika dipasang.

## Aturan pengelolaan risiko

- Hanya akun DEMO; identitas akun/server diperiksa lagi sebelum permintaan order.
- Risiko rencana = 0,25% × min(equity, balance, InpCapitalCap).
- Modal acuan default 100 dalam mata uang akun; tidak otomatis memakai saldo demo besar.
- SL 1,5 × ATR dari candle tertutup, disesuaikan tick size dan minimum stop broker.
- TP 2 × jarak risiko harga. Parameter ini eksperimen engineering, bukan hasil optimasi.
- OrderCalcProfit memakai spesifikasi broker/mata uang akun. Entry dan stop diberi
  stress 20 points per sisi, lalu biaya komisi ditambahkan. Ini asumsi, bukan batas fill.
- Lot dibulatkan TURUN dalam step broker. Bila di bawah minimum: TIDAK TRADE.
- Maksimum 80% anggaran risiko dialokasikan pada estimasi loss+komisi, menyisakan
  20% buffer. Gap/slippage/swap/biaya lain tetap dapat melampaui buffer dan batas rencana.
- Komisi round-trip per 1.0 lot harus diisi dalam MATA UANG AKUN. Default -1 = belum
  diketahui, memblokir order. Isi 0 hanya setelah memastikan demo benar-benar tanpa
  komisi; bukan karena ingin melewati pengaman. Komisi minimum per order, swap, biaya
  fixed dan biaya API belum dimodelkan. Uji pada akun demo khusus tanpa aktivitas lain.
- Maksimum satu posisi ATAU pending order pada seluruh akun; tidak hanya magic robot.
- Maksimum dua pengajuan entry per hari UTC dan **dua pengajuan total pilot**.
  Pengajuan yang gagal/tidak pasti juga menghabiskan jatah. Restart tidak meresetnya.
- Lock penambahan posisi pada penurunan equity/balance 1% modal acuan harian, termasuk
  anggaran risiko posisi baru. Baseline mulai pada observasi pertama hari UTC, BUKAN
  rekonstruksi equity tepat tengah malam. Hari baru tidak reset selama posisi masih ada.
- Perubahan balance/credit terdeteksi pada history setelah anchor memicu halt. Daily
  guard ini bukan sistem pembukuan lengkap untuk akun dengan transfer/EA/manual lainnya.
- Spread/ATR maksimum 0,12; drift midpoint sejak input AI maksimum 0,25 ATR.
- Quote harus bergerak, raw bar ID cocok, sinyal belum expired (maksimum 30 detik).
- Margin terhitung tidak boleh lebih dari 50% free margin. Market order + SL/TP + FOK
  harus didukung broker. Mode yang tidak didukung ditolak, tidak fallback tanpa stop.
- OrderCheck diikuti pemeriksaan ulang lalu SATU OrderSend dengan SL/TP terpasang.
  Tidak ada martingale, averaging, pembalikan otomatis, retry atau pengisian sisa lot.
- Nilai true OrderSend saja bukan bukti fill: hasil, deal, posisi, SL/TP dan risiko
  harga fill diperiksa. Tidak pasti/partial/missing protection -> HALT + log/peringatan.
  Posisi yang terbentuk harus diperiksa pengguna; tidak ada emergency-close otomatis.

`InpPauseNewEntries` memblokir entry baru; tidak menutup posisi yang sudah ada.
Ctrl+C pada Python juga hanya menghentikan sinyal baru. SL/TP yang sudah diterima
broker tidak dihapus oleh program ini. Penutupan sesi/pasar/gap tetap membawa risiko.

## Batas protokol dan izin

Receiver baru menolak format TEST_ONLY dan AI_DRYRUN lama. Protokol baru:

```text
NOG_GUARDED_V1;<PREVIEW|DEMO_SEND>;<id>;<symbol>;M5;<BUY|SELL|WAIT>;<issued>;<expires>;<bar_raw>;<login>;<atr>;<reference_bid>;<reference_ask>
```

Token + account + server + symbol diperiksa oleh API lokal; info akun tidak dikirim
ke OpenAI. Tidak ada endpoint publik, listen hanya 127.0.0.1. Token tidak melindungi
terhadap proses jahat yang sudah dapat membaca file pengguna/terminal. Tidak untuk LAN,
internet, multi-VPS atau akun bersama. Respons tidak ditandatangani; bukan transport produksi.

Arah tetap berasal dari OpenAI live. Tidak ada local_direction hint dan tidak ada
BUY manual. WAIT berarti tidak ada entry. Confidence tidak diperlakukan sebagai
probabilitas profit dan tidak dipakai untuk memperbesar lot.

Dua kunci harus aktif sebelum order demo mungkin terkirim:

1. Python dijalankan secara eksplisit dengan `--run --demo-orders`.
2. EA `InpEnableDemoOrders=true`, serta izin Algo Trading MT5/EA/akun aktif.

Tanpa kedua kunci: preview/tolak, tidak ada OrderSend. Default tetap preview.
Jika `InpRoundTripCommissionPerLot=-1`, risk plan ditolak sampai fee diverifikasi.
Biaya/modal dapat dikoreksi SEBELUM pengajuan order pertama. Setelah pengajuan
pertama, konfigurasi state dibekukan; perubahan ditolak dan tidak menghapus budget.

## Mode API berbayar — jangan mulai sebelum check/compile/risk input ditinjau

Mode preview (EA tidak mengirim order):

```powershell
& C:\venv\Scripts\python.exe .\guarded_demo.py --run
```

Mode khusus pengajuan order DEMO, setelah pengaman diperiksa dan EA diaktifkan:

```powershell
& C:\venv\Scripts\python.exe .\guarded_demo.py --run --demo-orders
```

Masing-masing adalah fase baru dengan maksimal **3 permintaan API tambahan** kumulatif
pada ledger TERPISAH. Ini bukan pemulihan dua jatah retry lama. Memakai kedua mode
bisa menambah total maksimum enam permintaan; batasnya bukan budget dolar. Tidak ada
reset otomatis harian atau per restart. Jangan menghapus/menggandakan ledger untuk
menghindari batas. Mengaktifkan mode API belum memastikan risk engine akan mengizinkan
entry; suatu permintaan dapat berbayar meskipun hasilnya WAIT atau ditolak EA.

Feed, indikator finite 120/30 candle dan filter freshness dari versi live sebelumnya
digunakan ulang tanpa perubahan. Legacy RSI flat=100 dipertahankan dan belum disamakan
seeding-nya dengan indikator native MT5. Tidak ada klaim perbaikan edge strategi.

SDK max_retries=0, timeout=25 detik. Error 429 menyimpan error_code yang diizinkan
(billing/credit/rate limit) tanpa mencetak pesan mentah/API key. Failed/STARTED
memblokir run berikutnya; tidak disulap menjadi WAIT. Tidak ada auto-unblock atau
pembelian kredit. Periksa --status setelah error. Respons lewat 40 detik/bar berganti/
source berubah/spread melonjak tidak dipublikasikan. READY_FOR_EA belum membuktikan
penerimaan atau fill; periksa tab Experts/Trade MT5.

```powershell
& C:\venv\Scripts\python.exe .\guarded_demo.py --status
```

Status hanya membaca dua ledger baru, tidak memanggil API/MT5/.env.

## Persistensi dan lingkup jaminan

Python: `data/guarded_demo_preview.sqlite3` dan `data/guarded_demo_orders.sqlite3`.
Model, source, mode dan hash implementasi dibekukan; percobaan dicatat sebelum HTTP.
File lama tidak dibuka/ditimpa. Perubahan kode sesudah freeze perlu review, bukan reset.

EA: append-only `MQL5/Files/NOG_GuardedDemo/state_<account>_<serverhash>.journal`.
Satu handle tanpa FILE_SHARE menolak copy EA kedua di terminal yang sama. Last bar,
hitungan pengajuan, anchor daily dan halt ditulis/flush sebelum broker request.
Crash setelah reservation -> halt tetap; tidak ada pengiriman ulang otomatis.
Journal rusak/tidak cocok -> fail closed, bukan reset. EA memeriksa histori magic
jika journal hilang, tetapi keterbatasan histori broker tetap berlaku. Checksum FNV
untuk korupsi tidak sengaja, bukan tanda tangan keamanan.

Jaminan ini TIDAK mencakup dua terminal berbeda, VPS lain, penghapusan manual file,
EA lain atau order manual simultan. Gunakan demo/terminal khusus, satu receiver.
Daily lock/two-attempt cap menghentikan entry baru; bukan jaminan drawdown aktual.

## Pengujian sebelum commit

35 tes Python lulus pada Python 3.13.5: SQLite sementara, input/response sintetis,
mock feed/model dan HTTP loopback. Cakupan: reservasi persisten, budget, error!=WAIT,
input validation, account/token handshake, hasil terlambat, source/bar/tick/spread
berubah, preservasi file, installer dan pengecekan source jalur default preview.
Mereka TIDAK menjalankan terminal nyata atau whole-loop integrasi live.

Fungsi matematika `NOG_RiskMath.mqh` yang SAMA dikompilasi dengan g++ + shim nama
fungsi Math*. 19.830 assertion aritmetika lulus, termasuk grid 10.000 kasus lot.
Ini BUKAN kompilasi EA MQL5, dan bukan pengujian runtime state/file/order MQL5.
Source EA harus dikompilasi F7 dan diuji pada terminal demo pengguna sebelum arming.
Tidak ada klaim "sempurna", "lolos backtest profit", atau izin untuk akun riil.

```powershell
python -m unittest discover -s tests -p "test_guarded_demo.py" -v
```

Developer dengan g++ dapat menjalankan `tests/risk_math_harness.cpp`; tidak diperlukan
untuk pengguna Windows/MT5. Tidak ada file pasar/akun asli atau kredensial dimasukkan
ke test fixture, commit, atau artefak tes.

## Referensi resmi (perilaku platform, bukan bukti profit)

- https://www.mql5.com/en/docs/trading/ordercalcprofit
- https://www.mql5.com/en/docs/trading/ordercheck
- https://www.mql5.com/en/docs/trading/ordersend
- https://www.mql5.com/en/docs/constants/environment_state/marketinfoconstants
- https://www.mql5.com/en/docs/files/fileopen
- https://developers.openai.com/api/reference/python
- https://help.openai.com/en/articles/5955604
