# Baseline lokal: asumsi entry dan spread

Ini tahap penelitian setelah audit sepuluh sampel, BUKAN EA trading atau persetujuan
strategi. `execution_baseline.py` menguji filter lokal saja pada histori yang lebih
besar. Tidak ada OpenAI call, API key, `.env`, perubahan prompt atau pembukaan order.
File monitor, filter dan kedua pilot lama tidak diubah.

## Jalankan

Dari folder `C:\NOG-EA31337-OpenAI\ai_trader`:

```powershell
git pull --ff-only origin openai-trader-v1
python -m unittest discover -s tests -p "test_execution_baseline.py" -v
python execution_baseline.py --prepare
python execution_baseline.py --report
Get-Content -LiteralPath .\data\execution_baseline_report.md
```

Lanjutkan setelah tes `OK`. Hanya `--prepare` yang pertama membutuhkan MT5 terbuka
dan terkoneksi (gunakan demo). Ia meminta 5.000 candle XAUUSD M5 mulai posisi 1,
mengabaikan candle berjalan pada posisi 0. Tidak perlu mengaktifkan AutoTrading.
Histori parsial tidak diam-diam dipakai: jumlah kurang dari yang diminta menghentikan
capture. Muat history chart/periksa pengaturan jumlah bar MT5 bila itu terjadi.

`--report` memakai snapshot lokal, bukan histori yang bergerak. Tidak membutuhkan
terminal, API key atau koneksi jaringan. Tanpa argumen, script hanya menampilkan
bantuan dan tidak menghubungi terminal.

Defaults path terminal dan symbol sama dengan konfigurasi pengembangan sebelumnya.
Bila terminal berbeda, opsi hanya untuk capture awal:

```powershell
python execution_baseline.py --prepare --mt5-path "C:\Program Files\MetaTrader 5\terminal64.exe" --symbol XAUUSD --timeframe M5 --bars 5000
```

Program ini sengaja tidak membaca `.env`; pengaturan filter di sana tidak diwarisi.
Mengulang `--prepare` memakai snapshot yang sudah ada, bukan mengganti pasar atau
parameter. Jangan menghapusnya untuk mencari periode yang menghasilkan angka bagus.

## Aturan eksperimen tetap (bukan parameter terbukti optimal)

- FilterConfig bawaan, kecuali `min_score=5` (gerbang eksperimen sebelumnya).
- EMA/RSI/ATR meniru EWM finite-window monitor lama: konteks 120 bar, warmup 200.
  Konvensi RSI flat=100 lama dipertahankan; ini bukan klaim seeding sama dengan
  indikator bawaan MT5. Perubahan indikator harus menjadi eksperimen lain.
- Sinyal dibuat setelah candle `i` tertutup. Entry memakai OPEN `i+2`, sesudah satu
  candle penuh ditunda. Exit memakai OPEN `i+22`, 20 bar setelah entry.
- Delay satu bar adalah asumsi skenario, bukan pengukuran latensi API dan bukan
  jaminan fill. Pada M5 kontinu itu penundaan sekitar 5 menit, bukan beberapa detik.
- Hanya satu posisi hipotetis. Sinyal baru ketika entry pending/posisi aktif dilewati;
  kandidat berikut dipertimbangkan setelah bar exit tertutup. Tidak ada pyramiding.
- Split 70/30 pada indeks sinyal yang memiliki entry/exit lengkap; 22 sinyal di
  ujung EARLY dipurge sehingga exit EARLY mendahului bar sinyal pertama LATE.
  Kedua segmen mulai tanpa posisi. LATE bukan otomatis holdout baru: periode ini
  dapat tumpang tindih dengan eksperimen yang sudah dilihat.
- Tak ada pemilihan score, horizon atau pemenang berdasarkan hasil laporan.

Ini adalah baseline baru. Jadwal masuk/keluar dan datasetnya berbeda dari sepuluh
sampel AI; jangan menyebut selisih terhadap pilot sebagai kontribusi kausal AI.

## Model spread: PROXY, bukan tick historis

MT5 dapat membangun chart dari Bid atau Last. Capture menolak mode selain BID
karena Last OHLC tidak boleh dianggap sebagai Bid.

Pada entry OPEN bar `e`, gunakan spread **bar tertutup e-1**. Pada exit OPEN bar
`x`, gunakan spread **bar tertutup x-1**. Tidak menggunakan ringkasan spread dari
seluruh bar e/x yang belum selesai pada waktu fill. Nilai spread sebelumnya tetap
hanya perkiraan; bukan quote Ask/Bid yang benar-benar tersedia pada detik fill.

Untuk point simbol `p` dan pengali skenario `k`:

```text
BUY:  entry = bid_open[e] + k * spread[e-1] * p
      exit  = bid_open[x]
SELL: entry = bid_open[e]
      exit  = bid_open[x] + k * spread[x-1] * p
```

P&L harga BUY = exit-entry; SELL = entry-exit. Persen semuanya dinormalisasi dengan
bid_open[e], bukan modal, margin atau equity. Spread tidak dibebankan dua kali pada
harga datar: contoh Bid 100, spread .20, long/short masing-masing berkurang .20 unit.

Tiga skenario memakai jadwal dan arah posisi yang SAMA:

| k | Arti |
| --- | --- |
| 0 | Pembanding tanpa spread; bukan kondisi trading normal yang diasumsikan nyata. |
| 1 | Satu kali proxy spread dari bar sebelumnya. |
| 2 | Stress dua kali proxy; bukan batas biaya terburuk atau hasil kalibrasi broker. |

Komisi, slippage, swap/pembiayaan dan biaya API BELUM dimodelkan. Tidak ada API yang
benar-benar dipanggil program ini. Hasil setelah proxy spread bukan return bersih
trading. Biaya nyata dapat lebih tinggi. Spread=0 di histori bukan bukti biaya nol.
Tidak ada SL/TP, lot, risk 0.25%, margin, saldo, equity curve atau drawdown akun.

## Data dan preservasi

```text
data/execution_baseline_snapshot.json
data/execution_baseline_trades.csv
data/execution_baseline_report.md
```

Hanya nama baru ini dipakai; database pilot/blind serta hasil audit lama tidak dibuka
atau ditimpa. Direktori data diabaikan oleh `.gitignore` repo. Jangan commit snapshot
atau kredensial. Snapshot dibuat eksklusif, bukan overwrite; pemeriksaan hash gagal
menutup proses jika file tidak lengkap/rusak. Jangan reset paksa untuk mengatasinya.

Snapshot menyimpan OHLC/spread mentah, point/mode chart, aturan, hash implementasi,
versi Python/pandas/MT5, dan waktu capture. Tidak menyimpan login, password atau key.
Hash adalah pemeriksaan konsistensi, bukan tanda tangan broker. Kode engine/filter
berbeda dari saat capture ditolak; perbedaan versi pandas diberi peringatan.

Timestamp memakai RAW_MT5 tanpa offset otomatis. Identitas bar dipertahankan, tetapi
zona waktu, perubahan DST, keaslian dan kelengkapan historis belum disertifikasi.
Gap tidak diisi bar sintetis atau dihapus diam-diam; jumlah transisi nonkontinu
tercatat. 20 bar dapat melintasi penutupan pasar/hari libur sehingga durasi nyata
lebih panjang. Biaya pembiayaan periode itu belum diperhitungkan.

## Bukti tes

32 tes khusus lulus pada Python 3.13.5/pandas 2.2.3 di lingkungan asisten. Menggunakan
harga sintetis dan adapter MT5 tiruan; TIDAK memakai terminal nyata atau histori akun
pengguna. Salinan `strategy_filter.py` untuk tes cocok dengan blob repo
`d068da4390a5fdd426ad21a8a4757012ae1f26fe`.

Cakupan: capture start_pos=1, mode Last ditolak, history parsial gagal, shutdown saat
error, snapshot tidak ditimpa, hash/kode berubah ditolak, input tidak finite, urutan
bar, gap, BUY ask/Bid dan SELL Bid/ask, spread tidak dua kali, jeda dan holding,
purge boundary, posisi tidak overlap, future data tidak masuk keputusan, lagged
spread bukan ringkasan bar fill, skenario sejadwal, N=0, offline report dan tidak ada
import OpenAI/panggilan order. Suite Windows/MT5 lengkap belum dijalankan di sini.

Tes memastikan perilaku kode tersebut, bukan keberhasilan strategi. Jangan menyetel
berulang pada periode ini. Simpan sebagai pengujian engineering, tetapkan desain lalu
gunakan data baru/forward dan quote/tick dengan biaya terukur untuk evaluasi berikutnya.

## Referensi resmi (perilaku platform, bukan bukti profit)

- https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrompos_py
- https://www.mql5.com/en/docs/python_metatrader5/mt5symbolinfo_py
- https://www.mql5.com/en/book/automation/symbols/symbols_chart_mode
