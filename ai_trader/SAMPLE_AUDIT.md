# Audit per sampel, tanpa API baru

`sample_audit.py` membaca `data/openai_blind.sqlite3` yang sudah diisi oleh
uji blind. Tidak ada permintaan OpenAI, koneksi MT5, pembacaan `.env`,
perubahan model/prompt, pengambilan sampel baru, atau eksekusi order.
Script hanya memakai pustaka standar Python. Kode monitor/pilot lama tidak diubah.

## Jalankan di PowerShell, dari ai_trader

```powershell
git pull --ff-only origin openai-trader-v1
python -m unittest discover -s tests -p "test_sample_audit.py" -v
python sample_audit.py
Get-Content -LiteralPath .\data\openai_sample_audit.md
```

Jalankan audit hanya setelah tes berakhir `OK`. Tidak perlu menjalankan ulang
`--prepare`/`--run` atau menyalin `.env`. Monitor live boleh tetap berhenti agar
konsumsi API-nya terpisah tidak berjalan. MT5 tidak harus terbuka untuk audit ini.

Database sumber dibuka dengan SQLite URI `mode=ro`, `query_only=ON`, dan satu
transaksi baca. Database yang hilang tidak dibuat ulang. Data sumber, baseline,
dan perubahan payload diperiksa terhadap fingerprint dan struktur snapshot.
Ini pemeriksaan konsistensi lokal, bukan bukti keaslian kriptografis hasil broker.

Output baru di direktori data yang sudah diabaikan oleh Git:

```text
data/openai_sample_audit.md
data/openai_sample_audit.csv
```

Kedua database pilot/blind dan laporan lama tidak ditimpa. Menjalankan ulang
auditor hanya memperbarui dua output audit tersebut, bukan menambah API attempt.
Snapshot blind sudah menyertakan sumber dan hasil pembanding; file database pilot
asli tidak dibuka oleh script ini. Tetap simpan kedua database sebagai arsip.

## Isi audit

- Return kotor Local, AI lama, AI blind, dan selisih setiap sampel pada 5/10/20 bar.
- Rerata, median, jumlah endpoint positif/negatif/nol, minimum dan maksimum.
- Pemisahan kelompok ACTIVE (BUY/SELL) dan WAIT, dengan penyebut N eksplisit.
- Sensitivitas rerata delta jika masing-masing sampel dikeluarkan bergantian.
- Jumlah respons belum dicoba/gagal/tidak pasti dan fingerprint data sumber.

Seluruh kolom statistik berpasangan memakai subset respons blind SUCCESS yang
sama. WAIT bernilai nol hanya untuk skenario tidak mengambil posisi. ERROR,
REFUSED, INCOMPLETE, INVALID, STARTED dan NOT_ATTEMPTED adalah data hilang,
bukan WAIT, bukan return nol, dan bukan transaksi menang. ACTIVE juga tidak
berarti program telah membuka order.

Audit memeriksa konsistensi label arah dengan angka return pasar, dan aritmetika
harga acuan/close berikutnya. Toleransi interval memperhitungkan harga tersimpan
5 desimal dan return 6 desimal; angka tersimpan tidak ditulis ulang. Pemeriksaan
ini TIDAK mengambil ulang histori atau memvalidasi timezone, kelengkapan bar,
atau apakah harga masuk bisa dieksekusi setelah jawaban AI.

Jika muncul `AUDIT STOP`, simpan pesannya. Jangan menghapus database atau
mengulang API untuk mencoba memperbaiki audit. Tidak ada hasil sampel pribadi
yang disertakan dalam kode atau tes di GitHub.

## Penafsiran

Semua angka adalah perubahan harga kotor dari harga close sinyal, bukan return
saldo akun. Spread, komisi, slippage, pembiayaan, latensi dan biaya API belum
masuk. Tidak ada SL/TP, ukuran posisi, equity curve atau drawdown. Banyaknya
endpoint positif bukan win rate transaksi sesungguhnya.

Sensitivitas tanpa satu sampel adalah deskripsi ketergantungan rata-rata pada
sampel, BUKAN interval kepercayaan atau izin membuang sampel agar hasil membaik.
Tidak ada optimasi parameter atau rekomendasi strategi yang dipilih auditor.
Periode ini telah diperiksa; hasilnya tetap eksplorasi, bukan holdout baru.
Perbedaan dua kondisi tidak membuktikan penyebabnya; variasi model dan
kontaminasi pelatihan historis belum terpisah dari pengaruh petunjuk input.

## Bukti pengujian

Pada 16 September 2026, 29 tes khusus auditor lulus pada Python 3.13 di lingkungan
pengembangan asisten. Tes memakai harga sintetis dan database SQLite sementara,
bukan data akun pengguna. Termasuk tes CLI dengan `python -S` (tanpa site-packages),
preservasi byte database, pengecualian hasil gagal, tanda SELL, WAIT=0, pembulatan,
fingerprint, dan larangan menimpa input/output lama.

Tes ini bukan pengujian ulang seluruh suite Windows/MT5 atau audit atas database
pengguna yang masih berada di PC. Jalankan tes dan auditor di PC untuk hasil asli.

Referensi implementasi Python (bukan bukti hasil trading):

- https://docs.python.org/3/library/sqlite3.html#how-to-work-with-sqlite-uris
- https://docs.python.org/3/library/statistics.html
