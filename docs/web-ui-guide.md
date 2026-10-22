# Panduan Web UI

Web UI dipakai tim **ops/SPV**, **admin engine**, dan **viewer** (finance, manajemen) untuk memantau kualitas foto, meninjau foto bermasalah, dan mengatur engine.

- Alamat: `https://<host-engine>/ui` (lokal: http://localhost:8000/ui)
- Tidak perlu instalasi. Web UI disajikan langsung oleh engine.

Daftar isi:

- [Masuk](#masuk)
- [Dashboard](#dashboard)
- [Review (antrean ops)](#review-antrean-ops)
- [Cari foto](#cari-foto)
- [Detail foto](#detail-foto)
- [Panduan keputusan ops](#panduan-keputusan-ops)
- [Uji foto](#uji-foto)
- [Tenant (admin)](#tenant-admin)
- [API key (admin)](#api-key-admin)
- [Sistem](#sistem)
- [Tips](#tips)

---

## Masuk

Login memakai **API key**, bukan username/password.

| Role | Bisa apa di Web UI |
|---|---|
| `admin` | Semua menu, termasuk Tenant, API key, ulangi job gagal, audit log |
| `ops` | Dashboard, Review, Cari foto, Uji foto, Sistem; **bisa** memberi keputusan review dan analisis ulang |
| `viewer` | Dashboard, Review, Cari foto, Uji foto, Sistem; **hanya lihat**, tanpa tombol keputusan |
| `app`, `customer` | **Tidak bisa** masuk Web UI (khusus integrasi aplikasi / pelanggan) |

Cara mendapatkan key:

- Minta admin membuatkan di menu **API key**, atau
- Admin server menjalankan: `pqe create-key --name "nama-anda" --role ops`
- Key admin pertama: nilai `PQE_BOOTSTRAP_ADMIN_KEY` (lihat [configuration.md](configuration.md)).

Key disimpan di browser (localStorage) sampai Anda klik **Keluar** atau key dicabut. Jangan login di komputer bersama tanpa keluar setelahnya.

---

## Dashboard

Ringkasan kualitas foto.

| Elemen | Arti |
|---|---|
| Filter **Tenant** | Semua tenant atau satu aplikasi |
| Filter **Periode** | 1, 7, 14, 30, atau 90 hari terakhir (berdasarkan waktu foto diterima engine) |
| **Foto diterima** | Jumlah foto dalam periode |
| **Layak (PASS)** / **Kurang ideal (WARN)** / **Tidak layak (REJECT)** | Persentase dan jumlah |
| **Menunggu review ops** | Foto WARN/REJECT/error yang belum diberi keputusan. Klik angkanya untuk ke Review |
| **Tren harian** | Batang bertumpuk per hari: PASS (hijau), WARN (oranye), REJECT (merah), masih diproses (abu-abu) |
| **Alasan terbanyak** | Kode alasan paling sering (WARN/REJECT). Klik kodenya untuk membuka **Cari foto** dengan filter alasan itu |
| **Kurir / Cabang dengan foto bermasalah terbanyak** | Top 10 jumlah foto WARN/REJECT. Dasar pembinaan kurir/cabang |

---

## Review (antrean ops)

Daftar foto yang **perlu keputusan manusia**: verdict WARN atau REJECT, atau gagal diproses (ERROR), yang **belum** direview.

| Filter | Arti |
|---|---|
| Tenant, Resi, Kurir, Cabang | Pencarian persis (bukan sebagian) |
| Verdict | PASS / WARN / REJECT |
| Kode alasan | Contoh `BLURRY`, `PIXELATED`, `DUPLICATE_EXACT` (daftar lengkap: `GET /v1/meta/reason-codes`) |

Klik **Terapkan** setelah mengubah filter. Daftar berisi 50 foto per halaman, terbaru di atas (tombol **Sebelumnya / Berikutnya**). Klik baris untuk membuka [detail foto](#detail-foto). Setelah keputusan diambil, foto hilang dari antrean.

Kolom tabel: thumbnail, resi (+ ID eksternal), tenant/jenis foto, kurir/cabang, verdict, alasan utama, status review, waktu diterima.

> Saat filter **Kode alasan** dipakai, jumlah di bawah tabel tetap menghitung semua foto sesuai filter lain, dan satu halaman bisa berisi kurang dari 50 baris. Ini wajar.

---

## Cari foto

Sama dengan Review, tetapi mencari di **semua** foto (termasuk PASS dan yang sudah direview), dengan filter tambahan:

| Filter | Arti |
|---|---|
| Status review | belum direview / disetujui / minta foto ulang / kecurangan |
| Dari / Sampai | Rentang tanggal foto diterima engine |

Dipakai untuk: mencari foto sebuah resi saat ada sengketa, mengecek riwayat kurir tertentu, atau melihat semua foto dengan alasan tertentu.

---

## Detail foto

Panel kanan yang terbuka saat baris foto diklik.

**Bagian atas:** nomor resi, verdict (atau `ERROR` jika file gagal diproses), status review, tenant, profile yang dipakai, dan **aksi mode** (`log` / `notify` / `reject`, yaitu saran ke aplikasi sesuai mode profile; lihat [configuration.md §5](configuration.md#5-mode-verdict-dan-action)).

**Gambar:** versi kompres (klik untuk ukuran penuh), plus link **Buka original** dan **Buka versi kompres**. Link berlaku 15 menit (`ENGINE_PRESIGN_TTL_UI`). Jika kedaluwarsa, tutup lalu buka lagi detailnya.

**Skor:** batang 0-100 per pemeriksaan. Warna hanya panduan visual (hijau ≥ 55, oranye ≥ 30, merah < 30). Verdict tetap ditentukan threshold profile.

**Catatan:** setiap alasan beserta pesan, kode, skor, dan saran untuk kurir:

- merah = alasan REJECT
- oranye = alasan WARN
- biru = informasi saja (contoh `EXIF_MISSING`, tidak memengaruhi verdict)

**Keputusan ops** (hanya admin & ops):

| Tombol | Status review | Efek |
|---|---|---|
| **Setujui (layak tagih)** | `approved` | Foto WARN/REJECT tetap boleh dipakai sebagai bukti penagihan. Masuk evidence pelanggan |
| **Minta foto ulang** | `retake_requested` | Foto **tidak** dipakai di evidence, meski verdict-nya PASS. Tindak lanjuti ke kurir/cabang di luar engine; foto pengganti yang diupload akan dianalisis sebagai foto baru |
| **Tandai kecurangan** | `fraud` | Foto **tidak** dipakai di evidence. Tandai untuk eskalasi ke SPV |
| **Reset** | `none` | Batalkan keputusan; foto kembali ke antrean review (jika WARN/REJECT/error) |
| **Analisis ulang** | (tidak berubah) | Foto dianalisis ulang memakai config tenant **terbaru** (misal setelah threshold diubah). Status sementara "pending" sampai worker selesai |

Kolom **Catatan (opsional)** ikut disimpan bersama keputusan. Setiap keputusan tercatat di **Riwayat review** (siapa, kapan, apa) dan di audit log, lalu dikirim sebagai webhook `review.updated` jika aplikasi berlangganan.

**Bagian bawah:** data pengiriman (kurir, cabang, pelanggan, waktu foto, lokasi dengan link Google Maps, sumber ingest, lokasi objek di SILO), info gambar & kompresi (format, dimensi, ukuran sebelum → sesudah, quality, SSIM, SHA-256), EXIF, riwayat review, dan metrik mentah (untuk kalibrasi).

---

## Panduan keputusan ops

Pertanyaan kuncinya: **apakah foto ini cukup sebagai bukti untuk ditagihkan dan dipertahankan jika pelanggan komplain?**

| Situasi | Keputusan |
|---|---|
| WARN/REJECT, tapi penerima/paket/resi **masih jelas terlihat** (engine terlalu ketat, misal latar bokeh atau cahaya agak gelap) | **Setujui**. Catat di kolom catatan jika polanya sering, supaya admin bisa mengkalibrasi threshold |
| Foto buram/pecah/gelap sampai **resi atau penerima tidak bisa dikenali** | **Minta foto ulang** (atau minta bukti lain ke cabang) |
| File rusak (`UNREADABLE_FILE`) | **Minta foto ulang** |
| `DUPLICATE_EXACT`: foto identik dipakai untuk resi lain | Buka kedua foto (cari resi lain di **Cari foto**). Jika memang foto yang sama dipakai ulang → **Tandai kecurangan** |
| `DUPLICATE_SIMILAR` | Bandingkan dulu. Lokasi yang sama (pintu kantor/gudang yang sama, paket sejenis) bisa mirip tanpa curang. Jika jelas foto yang sama dengan sedikit perubahan → **Tandai kecurangan**; jika berbeda → **Setujui** |
| `EDITING_SOFTWARE`, `OLD_PHOTO`, `GPS_MISMATCH`, `CAPTURE_TIME_MISMATCH` | Indikasi, bukan bukti. Cek konteks (jam pengiriman, lokasi). Ragu → konfirmasi ke kurir/SPV sebelum **Tandai kecurangan** |
| Hanya `EXIF_MISSING` (info) | Bukan masalah, tidak perlu tindakan |
| Salah klik keputusan | **Reset**, lalu pilih ulang |
| Threshold baru saja diubah admin | **Analisis ulang** foto yang ingin dinilai ulang |

### Pengaruh ke evidence pelanggan

Evidence (`/v1/evidence/...`, termasuk PDF) **hanya** menampilkan foto yang:

- sudah selesai dianalisis (`status = done`), **dan**
- verdict `PASS` **atau** disetujui ops (`approved`), **dan**
- **tidak** berstatus `retake_requested` atau `fraud`.

Artinya: PASS otomatis masuk evidence; WARN/REJECT baru masuk setelah **Setujui**; foto yang diminta ulang atau ditandai curang tidak pernah tampil ke pelanggan. Skor internal dan risiko keaslian tidak pernah ditampilkan ke pelanggan.

---

## Uji foto

Menganalisis foto **tanpa menyimpan apa pun**. Dipakai untuk mencoba threshold, onboarding aplikasi baru, atau melatih tim. Butuh role `admin` atau `ops`.

1. Pilih **Tenant** untuk memakai profile tenant itu, atau "(profile bawaan)".
2. Pilih **Jenis foto / profile**. Tanpa tenant, daftar berisi profile bawaan engine (`default`, `pod_recipient`, `pod_document`, `package`, `goods`, `location`). Setelah tenant dipilih, daftar berganti ke semua profile tenant tersebut (termasuk profile buatan tenant).
3. Pilih file, lalu klik **Analisis**.

Hasilnya: verdict, profile, mode, aksi, skor, catatan beserta saran, hasil kompresi (ukuran sebelum → sesudah, dimensi, quality, SSIM), waktu proses, dan JSON lengkap.

Catatan: cek duplikat tidak dijalankan di sini (tidak dibandingkan dengan foto lain), dan tidak ada data yang tersimpan maupun webhook yang terkirim.

---

## Tenant (admin)

Satu tenant = satu aplikasi yang terhubung. Referensi semua field: [configuration.md](configuration.md).

| Elemen | Fungsi |
|---|---|
| Tabel tenant | Kode, nama, versi config, jenis source, mode tiap profile, status aktif |
| **Ubah** | Membuka editor YAML config tenant |
| **Tenant baru** | Editor berisi template awal |
| **Validasi** | Cek config tanpa menyimpan, lalu tampilkan daftar profile + "Profile hasil gabungan" (nilai akhir setelah digabung dengan bawaan) |
| **Simpan** | Validasi lalu simpan. Versi naik jika isi berubah. Berlaku di semua proses dalam ≤ 5 detik, tanpa restart |
| **Nonaktifkan / Aktifkan** | Nonaktif = polling & event berhenti untuk tenant ini dan `POST /v1/analyze` ditolak. Data lama tetap ada |
| **Riwayat versi** | Semua versi config: siapa, kapan, dan isi lengkapnya. Untuk rollback, salin isi versi lama ke editor lalu **Simpan** |

Config yang salah tidak akan tersimpan; pesan error menyebut field yang bermasalah.

---

## API key (admin)

| Field | Arti |
|---|---|
| Nama / pemakai | Label, contoh `aplikasi-kurir-prod`, `ops-budi` |
| Role | `app`, `ops`, `viewer`, `customer`, `admin` |
| Tenant | **Wajib** untuk role `app` (key hanya bisa mengakses tenant itu). Opsional untuk role lain (membatasi ke satu tenant) |
| Customer ID | **Wajib** untuk role `customer` (pelanggan hanya bisa melihat evidence untuk `customer_id` miliknya) |

- Klik **Buat**. Nilai key tampil **sekali saja** di kotak kuning; salin dan berikan lewat kanal aman. Engine hanya menyimpan hash-nya.
- Tabel menampilkan prefix key, role, tenant, customer, tanggal dibuat, dan terakhir dipakai (diperbarui paling sering sekali per menit).
- **Cabut** langsung menonaktifkan key; aplikasi yang memakainya akan mendapat `401`. Buat key baru sebelum mencabut key yang masih dipakai produksi.

Praktik baik: satu key per aplikasi per lingkungan (dev/staging/prod) dan satu key per orang ops, supaya audit log jelas dan pencabutan tidak mengganggu pihak lain.

---

## Sistem

| Elemen | Arti / tindakan |
|---|---|
| **Antre / Diproses / Selesai / Gagal permanen (dead)** | Status antrean analisis. "Antre" yang terus naik = worker kurang atau mati |
| **Job gagal permanen** | Job yang gagal setelah semua percobaan (biasanya SILO/DB tidak bisa diakses). Tampil tenant, objek, jumlah percobaan, error terakhir |
| **Ulangi** / **Ulangi semua** (admin) | Kembalikan job dead ke antrean (percobaan direset). Perbaiki penyebabnya dulu |
| **Webhook gagal permanen** | Pengiriman webhook ke aplikasi yang gagal setelah 8 percobaan: tenant, event, URL, jumlah percobaan, error terakhir |
| **Kirim ulang semua** (admin) | Antrekan ulang semua webhook gagal. Pastikan endpoint aplikasi sudah bisa menerima |
| **Source ingest** | Setiap source (`tenant:index:type`): kapan terakhir jalan, jumlah foto baru, reconcile terakhir, error terakhir. Kolom error yang terisi = source bermasalah (lihat [troubleshooting.md](troubleshooting.md)) |
| **Audit log** (admin) | 50 aktivitas terakhir: perubahan config, API key, review, retry job/webhook, retensi |
| Link **/metrics** dan **/docs** | Metrik Prometheus dan dokumentasi API interaktif (Swagger) |

---

## Tips

- Bahasa pesan alasan mengikuti bahasa browser (Indonesia/Inggris).
- Waktu ditampilkan dalam zona waktu komputer Anda.
- Mulai hari dengan Dashboard → klik **Menunggu review ops** → kerjakan antrean dari atas.
- Jika satu kurir/cabang mendominasi daftar masalah, cek dulu pola alasannya. `BLURRY` berulang biasanya soal cara memotret; `PIXELATED`/`HEAVY_COMPRESSION` berulang biasanya setting kompresi di aplikasi atau HP tertentu.
- Lonjakan REJECT mendadak pada satu aplikasi sering berarti ada rilis aplikasi baru yang mengubah setting kamera/kompresi. Laporkan ke tim aplikasi.
- Jangan menyetujui foto hanya untuk mengosongkan antrean. Evidence ke pelanggan bergantung pada keputusan ini.
