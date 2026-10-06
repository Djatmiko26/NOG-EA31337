# Diagnosis hasil baseline, tanpa eksperimen API baru

`execution_diagnostics.py` membaca snapshot dan CSV yang SUDAH dibuat oleh
`execution_baseline.py`. Ini laporan tambahan, bukan strategi baru, pemilihan
parameter, pengukuran untung akun, atau bukti kelayakan trading.

## Jalankan dari ai_trader

```powershell
git pull --ff-only origin openai-trader-v1
python -m unittest discover -s tests -p "test_execution_diagnostics.py" -v
python execution_diagnostics.py
Get-Content -LiteralPath .\data\execution_diagnostics_report.md
```

Lanjutkan setelah tes `OK`. Tidak perlu menjalankan ulang `--prepare`, mengambil
histori baru, mengganti `.env` atau membuka MT5. Tidak ada mode panggilan API.
Monitor live boleh tetap berhenti agar konsumsi API terpisahnya tidak berjalan.

## Input dipertahankan

```text
data/execution_baseline_snapshot.json
data/execution_baseline_trades.csv
```

Keduanya dibaca sekali dalam mode baca. Diagnostik memeriksa hash snapshot,
identitas kode engine/filter, harga dan epoch pada setiap baris CSV, proxy
spread dari bar sebelum fill, aritmetika BUY/SELL, jadwal i+2/i+22, purge,
ketiadaan posisi bertumpuk, dan tiga skenario dengan arah/jadwal sama.
Satu posisi pada 0x/1x/2x dihitung sebagai SATU posisi, bukan tiga observasi.
Baris kosong tanpa posisi memberikan N=0 dan mean kosong, bukan keuntungan nol.

Hasil disimpan hanya ke:

```text
data/execution_diagnostics_report.md
data/execution_diagnostics_positions.csv
```

Snapshot, trades.csv, baseline report, database pilot/blind, kode engine/filter,
.env dan laporan sebelumnya tidak diubah. Python standar saja; tidak mengimpor
pandas, OpenAI atau MetaTrader5. File kode engine/filter hanya dibaca untuk hash
teks yang dinormalisasi CRLF/LF agar konsisten dengan baseline Windows.

Jika `DIAGNOSTICS STOP`, simpan pesannya. Jangan hapus snapshot, mengubah CSV,
reset Git paksa, atau mengulang API untuk mengatasi kegagalan pemeriksaan.

## Isi laporan

1. Rekonsiliasi N, mean, median dan drag spread pada 0x, 1x, 2x.
2. Pada 1x: ALL/BUY/SELL, jumlah +/negatif/nol, mean/median, rerata kelompok
   positif dan negatif, serta kontribusi masing-masing arah ke mean segmen.
3. Pada 1x: kelompok berdasarkan transisi raw timestamp tidak kontinu ketika
   pending dan/atau holding, tanpa membuang posisi apa pun.
4. Minimum/maksimum dan sensitivitas mean jika satu posisi dikeluarkan
   bergantian secara matematis; tidak menghapus baris input.
5. Lima hasil terendah dan tertinggi per segmen sebagai rincian deskriptif.

Kontribusi mean kelompok = jumlah return kelompok dibagi N seluruh posisi pada
segmen yang sama. Kontribusi BUY + SELL merekonsiliasi mean keseluruhan segmen.
Jumlah return untuk penghitungan itu bukan akumulasi return akun.

## Gap bukan filter trading

Anomali berarti selisih raw epoch antarbar bukan durasi nominal timeframe.
NONE tidak menjamin data lengkap. Ini bukan verifikasi zona waktu, DST, hari
libur, kualitas broker, atau penyebab gap. Pending dihitung dari signal-open
sampai entry-open; holding dari entry-open sampai exit-open. Raw duration bukan
janji durasi pasar yang telah dikonfirmasi secara independen.

Keberadaan gap sesudah entry baru diketahui SETELAH kejadian. Statistik kelompok
tersebut tidak boleh langsung dijadikan aturan pemilihan sinyal menggunakan
informasi masa depan. Tidak ada posisi yang ditolak atau diubah jadwalnya.

## Batas kesimpulan

Tidak menghitung ulang indikator/filter atau membuktikan kelengkapan daftar
kandidat/posisi. Verifikasi harga/CSV dan hash adalah konsistensi lokal, bukan
tanda tangan broker, sertifikasi histori, atau pembuktian seluruh pipeline.

Analisis 1x tetap memakai spread PROXY. Tidak mencakup komisi, slippage,
pembiayaan, biaya API, fill sesungguhnya, SL/TP, lot, risk 0.25%, margin, saldo,
equity curve atau drawdown akun. Label positif adalah endpoint, bukan win rate
transaksi netto. Sensitivitas tanpa satu posisi bukan interval kepercayaan atau
izin menghapus hasil buruk agar mean menjadi positif.

Tidak menetapkan BUY-only, SELL-only, pembalikan sinyal, horizon baru atau model
lebih mahal dari laporan ini. Semua data ini sudah diperiksa; hubungan kelompok
dan hasil bukan bukti sebab-akibat. Gunakan diagnosis untuk merumuskan hipotesis,
lalu bekukan desain sebelum evaluasi pada periode benar-benar baru/forward.

## Bukti tes

35 tes khusus lulus pada Python 3.13.5 di lingkungan asisten pada 16 September
2026. Data seluruhnya sintetis dalam bentuk snapshot/CSV baseline, dengan identitas
source fixture dan direktori sementara. Tidak memakai data akun pengguna, tidak
menjalankan MT5 dan tidak memanggil API. Tes mencakup CLI `python -S`, preservasi
byte input, CRLF, SELL, spread satu kali, triplet skenario, overlap, purge,
raw gap boundaries, penolakan CSV rusak/nonfinite, dan N=0.

Tes fokus ini bukan pengujian seluruh suite Windows/MT5 dan bukan reproduksi
hasil baseline pada histori nyata. Jalankan tes serta diagnosis di PC Anda.

Referensi implementasi statistik (bukan bukti performa trading):
https://docs.python.org/3/library/statistics.html
