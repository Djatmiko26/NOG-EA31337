# XAUUSD Cash Demo — batas rugi rencana USD 10, sasaran laba USD 10

Versi eksperimen terpisah dari Guarded Demo 0,25%. Default PREVIEW; tidak
mengaktifkan order di terminal. Sasaran ini BERBEDA dari pengaturan lama dan
belum membuktikan profitabilitas. USD 10 adalah 10% dari modal USD 100.

## Mulai dengan pemeriksaan saja

Hentikan bridge lama, buka MT5 demo, biarkan Algo Trading OFF. Dari PowerShell:

```powershell
cd C:\NOG-EA31337-OpenAI\ai_trader
git pull --ff-only origin openai-trader-v1
& C:\venv\Scripts\python.exe .\cash_demo.py --check
```

Mode ini tetap XAUUSD M5; pemeriksaan EURUSD sebelumnya tidak diwarisi. Hanya akun
DEMO dengan mata uang USD dan profit-currency XAUUSD USD yang didukung. Akun cent
USC bukan USD dan ditolak, bukan dikonversi diam-diam. Balance/equity minimal USD
100; saldo demo besar tidak memperbesar sasaran dolar. Margin memakai spesifikasi
akun demo tersebut, bukan bukti bahwa akun riil USD 100 cukup untuk margin.

`--check` membaca 120 candle tertutup dan indikator via adapter live yang sudah
ada, memakai OrderCalcProfit untuk mengecek dua rencana hipotetis BUY/SELL. Ini
BUKAN dua sinyal. Tidak memanggil OpenAI, tidak membuat ledger dan tidak mengirim
order. Tanpa data komisi, hasil adalah ilustrasi harga dengan asumsi biaya nol,
dilabeli `COMMISSION_UNKNOWN`, bukan izin trading. Jangan menebak komisi nol.

Jika biaya broker sudah diketahui, pemeriksaan offline-order dapat menerima
`--commission-per-lot <USD round trip per 1 lot>` dan
`--fixed-fee <USD round trip per posisi>`. Kedua opsi harus diisi bersamaan.
Nilai ini tidak mengubah input EA atau file .env. Biaya fixed mencakup biaya
minimum/fixed yang sudah diketahui; tidak boleh dihitung dua kali dengan per-lot.

## Apa yang diubah dalam aturan risiko

- Batas estimasi kerugian USD 10, target estimasi keuntungan USD 10, bukan 0,25%
  dan bukan TP dua kali SL. Tidak ada target profit harian yang dijanjikan.
- Lot otomatis dihitung dengan volume_min/step/max/limit broker; dibulatkan turun.
  Batas pilot 0,10 lot adalah CEILING, bukan lot minimum atau volume yang dipaksa.
- Jarak minimum teknis SL tetap 1,5 ATR dari candle tertutup, dengan syarat stop
  broker. Lot dipilih terlebih dulu agar jarak tersebut dapat ditampung dalam USD
  10. Jika minimum lot tidak muat: TIDAK TRADE, bukan SL dipersempit agar muat.
- Setelah lot dipilih, SL dapat diperlebar untuk mendekati batas dolar, tetapi
  tidak dipersempit di dalam jarak teknis. Ini perubahan strategi exit yang harus
  dievaluasi sebagai eksperimen baru, bukan hasil optimasi.
- Estimasi kerugian = kerugian harga pada entry dan exit yang masing-masing
  diperburuk 20 points + komisi round-trip per-lot + biaya fixed round-trip.
- Target profit menggunakan asumsi stress dan biaya yang sama, lalu mencari TP
  dengan estimasi setidaknya USD 10; grid tick dapat membuatnya sedikit lebih besar.
  Stop dibulatkan ke arah entry, TP menjauhi entry. Ini bukan R:R geometris persis
  1:1 karena biaya, stress, grid, dan loss aktual rencana bisa sedikit di bawah 10.
- Slippage 20 points per sisi adalah ASUMSI, bukan batas slippage broker. Cadangan
  20% versi lama tidak diwarisi; kedua asumsi jangan dicampur.
- Slope harga dari OrderCalcProfit pada tick/minimum lot digunakan untuk estimasi,
  lalu harga SL/TP/volume akhir diverifikasi ulang melalui OrderCalcProfit lengkap.
  Hasil nonlinier/berbeda ditolak; tidak mengandalkan tick value perkiraan semata.
- Spread masuk melalui Ask entry/Bid exit BUY dan Bid entry/Ask exit SELL, bukan
  ditambahkan berulang. Gate current spread/ATR <=0,12 dan drift <=0,25 ATR tetap.
- Komisi kedua input EA default -1 (belum diketahui); EA tidak polling API sampai
  kedua biaya diketahui. Isi 0 hanya setelah benar-benar diverifikasi di broker.
- FOK + market + SL + TP harus didukung; tidak fallback ke transaksi tanpa stop.
  OrderCalcMargin dan OrderCheck harus lolos, lalu harga/bar/account dicek ulang.
- Satu posisi atau pending order di SELURUH akun memblokir entry berikutnya.
  Tidak martingale, averaging, pyramiding, pembalikan otomatis atau order ulang.

Sasaran dolar TIDAK menjamin realisasi kerugian/keuntungan tepat USD 10. Gap,
slippage di luar asumsi, eksekusi berbeda, biaya salah, swap/pembiayaan dan biaya
API belum dikendalikan penuh. Tidak ada software stop yang menjamin harga fill.
Tidak ada backtest profit baru dalam perubahan ini.

## Batas harian dan pilot — tidak lagi 1% versi lama

Batas entry baru berdasarkan penurunan balance/equity USD 10 dari anchor harian
(termasuk risiko rencana entry baru), dan target kenaikan balance harian USD 10.
Anchor dimulai pada observasi pertama hari UTC, bukan rekonstruksi tengah malam.
Perubahan funding memicu review. Gunakan akun demo khusus, tanpa EA/manual lain.
Hari baru tidak reset selama posisi/pending masih ada.

**Pilot baru ini hanya mengizinkan SATU pengajuan entry total dan maksimal satu
per hari.** Permintaan ditolak/tidak pasti tetap menghabiskan jatah. Restart tidak
meresetnya. Setelah satu pengajuan, EA tidak membuka lagi. Batas ini menghentikan
ENTRY BARU; tidak menjamin drawdown USD 10 dan tidak otomatis menutup posisi.
Jangan hapus journal/ledger untuk mengulang jatah.

## Instalasi

Setelah hasil --check ditinjau:

```powershell
& C:\venv\Scripts\python.exe .\cash_demo.py --install
```

Installer hanya menyalin `NOG_CashDemo.mq5` dan `NOG_CashMath.mqh` ke Data Folder
MT5 aktif, mencadangkan file tujuan berbeda dan membuat/memakai ulang token lokal
`MQL5/Files/NOG_CashDemo/bridge.token`. Token bukan OpenAI key dan tidak dicetak.
EA lama, seluruh ledger riset/live, .env, dan konfigurasi terminal tidak diubah.
Installer tidak kompilasi, memasang chart atau mengaktifkan Algo Trading.

Kompilasi salinan yang terpasang dengan F7 di MetaEditor. EA lengkap belum
dikompilasi di lingkungan asisten; jangan lanjut jika ada error. Lepaskan EA lama,
pasang satu `NOG_CashDemo` di chart biasa XAUUSD M5 akun DEMO USD. Bukan Tester.
Izinkan WebRequest hanya `http://127.0.0.1:8765` seperti sebelumnya.

Default `InpEnableDemoOrders=false`, `InpAcknowledgeHighRisk=false`,
`InpPauseNewEntries=false`, kedua input komisi `-1`. Log `CASH_SELF_TEST_PASS`
menguji matematika saat EA dipasang; belum membuktikan order/fill bekerja.

## Menyambungkan OpenAI live — terpisah dari pemeriksaan awal

Arah tetap berasal dari OpenAI atas candle tertutup live; bukan BUY manual.
Prompt/indikator lama digunakan kembali, termasuk legacy RSI flat=100. Tidak
mengirim local_direction hint, rincian akun, lot, atau rahasia ke OpenAI.

```powershell
# Menggunakan kredit API, tetap PREVIEW:
& C:\venv\Scripts\python.exe .\cash_demo.py --run
```

Program menunggu EA yang sesuai dan candle M5 BARU selesai; tidak backfill atau
menerbitkan ulang hasil lama. READY_FOR_EA bukan bukti fill/penerimaan. Periksa
Experts untuk `AI_WAIT`, `RISK_REJECT`, atau `PREVIEW_PLAN_OK`.

Jangan memulai order sebelum check, compile, biaya, dan preview ditinjau.
Mode order membutuhkan SEMUA: Python `--run --demo-orders`,
EA `InpEnableDemoOrders=true`, `InpAcknowledgeHighRisk=true`, serta izin
Algo Trading MT5/EA/akun, kondisi demo USD dan seluruh risk guards.
Tidak ada mode akun riil. Persetujuan risiko tidak menghapus risk guards.

**Kedua mode memakai satu ledger baru dengan total maksimum tiga permintaan API
TAMBAHAN gabungan**, bukan tiga per mode/restart/hari. Batas tidak memakai ulang
sisa quota pilot lama dan bukan limit dolar billing. Semua attempt direservasi
sebelum HTTP; kegagalan/STARTED memblokir run. SDK max_retries=0, timeout=25 detik,
output bound diwarisi 2048 token. Jangan delete ledger atau mengganti model untuk
mengulang. Setelah error periksa --status; source/model/code dibekukan.

```powershell
& C:\venv\Scripts\python.exe .\cash_demo.py --status
```

Status membaca ledger saja, tanpa .env/MT5/API. Model dari .env tetap dipakai tanpa
fallback; ketersediaan dan billing perlu diverifikasi akun API Anda, bukan dijamin.

## Penyimpanan dan proteksi

- Python: `data/cash_demo.sqlite3`, terpisah dari seluruh database sebelumnya.
- EA: journal append-only `MQL5/Files/NOG_CashDemo/state_<account>_<serverhash>.journal`.
- HTTP token, login/server/symbol dan protokol NOG_CASH_V1 diperiksa lokal. Ini
  bukan gateway publik/produksi; proses lokal dengan akses file dapat menyamar.
- Journal dibuka eksklusif; reservasi+halt ditulis dan flush sebelum OrderSend.
  Crash/error: tidak otomatis kirim ulang. FNV checksum mendeteksi korupsi tak
  sengaja, bukan tanda tangan keamanan. Jangan ubah/hapus journal.
- Return true OrderSend bukan bukti fill. Retcode, deal, volume, arah dan SL/TP
  diperiksa, kemudian estimasi risiko berdasarkan fill diperiksa ulang. Jika
  protection/margin/fee/fill tidak sesuai: HALT + peringatan dan periksa posisi.
  Tidak ada emergency-close otomatis atau upaya mengisi partial fill.
- SL/TP yang diterima broker tetap ada saat Python dihentikan. Ctrl+C, pause,
  tutup EA atau quota habis tidak menutup posisi yang telah terbentuk.
- Tidak melindungi dua terminal/VPS berbeda, EA/manual bersamaan, manipulasi file,
  gangguan broker, perubahan kode manual atau kehilangan histori. Satu terminal,
  satu EA, akun demo khusus. Batasan ini bukan klaim sistem sempurna.

## Bukti pengujian

35 unit test Python lulus (input sintetis, mock MT5/model, SQLite sementara dan
HTTP loopback). Header NOG_CashMath.mqh yang sama dikompilasi sebagai C++ lalu
10.000 kasus aritmetika dibandingkan dengan referensi Python: 3.992 rencana
lolos dan 6.008 ditolak, hasil kedua implementasi cocok. Termasuk biaya, arah,
volume/grid, min stop, target dolar, ledger persistensi, WAIT dan failure.

Ini TIDAK mengompilasi EA MQL5 lengkap atau menjalankan broker/order/paid API.
Seluruh suite historis tidak dijalankan ulang; source lama tidak diubah.

```powershell
& C:\venv\Scripts\python.exe -m unittest discover -s tests -p "test_cash_demo.py" -v
```

Referensi resmi (perilaku platform, bukan bukti profit):
- https://www.mql5.com/en/docs/trading/ordercalcprofit
- https://www.mql5.com/en/docs/trading/ordercheck
- https://www.mql5.com/en/docs/trading/ordersend
- https://www.mql5.com/en/docs/constants/environment_state/marketinfoconstants
- https://www.mql5.com/en/docs/files/fileopen
- https://www.mql5.com/en/docs/files/fileflush
- https://developers.openai.com/api/docs/guides/structured-outputs
