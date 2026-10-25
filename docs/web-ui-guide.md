# Panduan Web UI

Web UI dipakai tim **ops/SPV**, **admin engine**, dan **viewer** (finance, manajemen) untuk memantau kualitas foto, meninjau foto bermasalah, dan mengatur engine.

- Alamat: `https://<host-engine>/ui` (lokal: http://localhost:8000/ui)
- Tidak perlu instalasi. Web UI disajikan langsung oleh engine.
- **Panduan integrasi untuk programmer aplikasi** ada di `https://<host-engine>/ui/guide.html` (tanpa login). Link-nya
  ada di menu (**Panduan Integrasi ↗**) dan di halaman login, jadi programmer bisa dikirimi link itu saja. Halaman itu
  juga memuat diagram alur integrasi, alur penggunaan, keputusan di aplikasi kurir, dan proses di dalam engine.
- **Uji Coba Kamera** ada di `https://<host-engine>/ui/coba.html` (menu **Uji Coba Kamera ↗**): halaman yang dioptimalkan
  untuk HP untuk mencoba engine seperti aplikasi kurir, lihat [Uji Coba Kamera](#uji-coba-kamera-hp).
- Penyimpanan foto pengiriman dikerjakan aplikasi pengiriman ([ADR 0006](adr/0006-engine-hanya-memproses-gambar.md)).
  Web UI menampilkan foto dari **arsip engine** (bucket `ocr`, [ADR 0007](adr/0007-arsip-engine-di-bucket-ocr.md)):
  foto diterima = foto hasil, foto ditolak = salinan review kualitas tinggi (maks. 1,5 MB). Foto polling dibaca dari bucket aplikasi.

**Navigasi:** menu ada di sidebar kiri, dikelompokkan **Pantau** (Dashboard, Review, Cari foto, Evidence), **Uji** (Uji
foto, Uji Coba Kamera), **Pengaturan** (Tenant, API key, Koneksi SILO, Backup NAS; khusus admin), dan **Lainnya** (Sistem,
Panduan Integrasi). Di HP sidebar disembunyikan; buka dengan tombol **☰** di kiri atas. **Versi engine** tampil di
sidebar, pojok kanan atas, dan halaman login (sebutkan versi ini saat melapor masalah). Setiap halaman langsung menaruh
kursor di field utamanya (misal kotak **Resi** di Review/Cari foto), dan filter cukup diterapkan dengan **Enter**.

Daftar isi:

- [Masuk](#masuk)
- [Dashboard](#dashboard)
- [Review (antrean ops)](#review-antrean-ops)
- [Cari foto](#cari-foto)
- [Detail foto](#detail-foto)
- [Evidence (penagihan)](#evidence-penagihan)
- [Panduan keputusan ops](#panduan-keputusan-ops)
- [Uji foto](#uji-foto)
- [Uji Coba Kamera (HP)](#uji-coba-kamera-hp)
- [Tenant (admin)](#tenant-admin)
- [API key (admin)](#api-key-admin)
- [Koneksi SILO (admin)](#koneksi-silo-admin)
- [Backup NAS (admin)](#backup-nas-admin)
- [Kalibrasi](#kalibrasi)
- [Laporan & alert (admin)](#laporan--alert-admin)
- [Sistem](#sistem)
- [Tips](#tips)

---

## Masuk

Login memakai **API key**, bukan username/password.

| Role | Bisa apa di Web UI |
|---|---|
| `admin` | Semua menu, termasuk Tenant, API key, Koneksi SILO, Backup NAS, preflight, ulangi job gagal, audit log |
| `ops` | Dashboard, Review, Cari foto, Evidence, Uji foto, Sistem; **bisa** memberi keputusan review dan analisis ulang |
| `viewer` | Dashboard, Review, Cari foto, Evidence, Uji foto, Sistem; **hanya lihat**, tanpa tombol keputusan |
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
| **Menunggu review ops** | Foto WARN/REJECT/error yang belum diberi keputusan. Klik angkanya untuk ke Review. Dengan `review_required: false` (bawaan), angka ini bukan tunggakan: foto WARN sudah masuk evidence, review hanya untuk koreksi |
| **Tren harian** | Batang bertumpuk per hari: PASS (hijau), WARN (oranye), REJECT (merah), masih diproses (abu-abu) |
| **Alasan terbanyak** | Kode alasan paling sering (WARN/REJECT). Klik kodenya untuk membuka **Cari foto** dengan filter alasan itu |
| **Kurir / Cabang dengan foto bermasalah terbanyak** | Top 10 jumlah foto WARN/REJECT. Dasar pembinaan kurir/cabang |
| **Perlu dicek (24 jam terakhir)** | Muncul jika tingkat tolak sebuah cabang/kurir jauh di atas 7 hari sebelumnya (≥ 2× dan ≥ 15 poin persen, minimal 10 foto), atau banyak foto gagal diproses. Klik nama cabang/kurir untuk melihat fotonya |

---

## Review (antrean ops)

Daftar foto WARN atau REJECT, atau gagal diproses (ERROR), yang **belum** direview.

> **Review bersifat opsional.** Dengan setelan bawaan tenant (`review_required: false`), foto `PASS` dan `WARN`
> langsung masuk evidence pelanggan tanpa menunggu ops, dan foto `REJECT` sudah otomatis dibalas "wajib foto ulang"
> ke aplikasi. Jadi tidak ada foto yang "pending" karena antrean ini. Pakai antrean untuk memantau dan mengoreksi
> (misal **Setujui** foto REJECT yang sebenarnya masih jelas, atau **Tandai kecurangan**). Jika sebuah tenant ingin
> setiap foto WARN dicek manusia dulu, admin mengisi `review_required: true` di config tenant
> (lihat [configuration.md](configuration.md#21-field-tingkat-atas)).

| Filter | Arti |
|---|---|
| Tenant, Resi, Kurir, Cabang | Pencarian persis (bukan sebagian) |
| Verdict | PASS / WARN / REJECT |
| Kode alasan | Contoh `BLURRY`, `PIXELATED`, `DUPLICATE_EXACT` (daftar lengkap: `GET /v1/meta/reason-codes`) |

Klik **Terapkan** (atau tekan **Enter**) setelah mengubah filter; **Reset** mengosongkan semua filter. Di HP hanya kotak
**Resi** yang tampil; tombol **Filter lainnya** membuka filter lain. Daftar berisi 50 foto per halaman, terbaru di atas (tombol **Sebelumnya / Berikutnya**). Klik baris untuk membuka [detail foto](#detail-foto). Setelah keputusan diambil, foto hilang dari antrean.

Kolom tabel: thumbnail, resi (+ ID eksternal), tenant/jenis foto, kurir/cabang, verdict (dengan tulisan merah **foto ulang** jika `retake_required`), alasan utama, status review, waktu diterima. Di HP kolom tenant, kurir, review, dan waktu disembunyikan. Thumbnail dibuat engine saat dibutuhkan dari arsip engine (atau dari bucket aplikasi untuk foto polling); kotak abu-abu bertuliskan **tidak ada file** berarti tidak ada arsip (belum/gagal ditulis, atau lewat 520 hari) dan aplikasi tidak melaporkan lokasinya.

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

**Gambar:** foto dari arsip engine, atau dari bucket aplikasi jika tidak ada arsip (klik atau **Buka foto** untuk ukuran penuh), plus link **Evidence PDF resi ini**. Foto yang ditolak menampilkan foto asli dengan keterangan *"Foto asli yang ditolak (arsip engine, untuk review & kalibrasi). Tidak disimpan aplikasi."* Link berlaku 15 menit (`ENGINE_PRESIGN_TTL_UI`). Jika kedaluwarsa, tutup lalu buka lagi detailnya. Jika muncul kotak **Tidak ada file foto**, arsip belum tersimpan atau sudah lewat masa simpan dan aplikasi tidak melaporkan lokasinya; data analisis tetap ada, tetapi tidak ada gambar, foto tidak masuk evidence, dan **Analisis ulang** tidak bisa dipakai. Tekan **Esc** atau **×** untuk menutup panel.

**Kotak status tambahan:**

- **Wajib foto ulang** (merah): kualitas tidak memenuhi syarat (verdict REJECT, atau ops memilih "Minta foto ulang").
  Berisi pesan yang sama dengan yang diterima aplikasi kurir (`retake_message`). Di mode `enforce`, aplikasi
  pengiriman menahan update status pengiriman (`allow_status_update: false`) sampai foto ulang diterima.
- **Diperbaiki otomatis** (biru): hanya muncul jika admin mengaktifkan koreksi pencahayaan opsional di profile
  (`enhance.enabled: true`, bawaan nonaktif). Ditampilkan kurva yang dipakai (contoh `levels:12-140, gamma:0.62`) dan
  verdict + kode alasan **sebelum** koreksi.

Secara bawaan engine tidak memanipulasi isi gambar: foto yang dikembalikan ke aplikasi (dan disimpan aplikasi) hanya
diperkecil/dikompres dari file kurir.

**Skor:** batang 0-100 per pemeriksaan. Warna hanya panduan visual (hijau ≥ 55, oranye ≥ 30, merah < 30). Verdict tetap ditentukan threshold profile.

**Catatan:** setiap alasan beserta pesan, kode, skor, dan saran untuk kurir:

- merah = alasan REJECT
- oranye = alasan WARN
- biru = informasi saja (contoh `EXIF_MISSING`, tidak memengaruhi verdict)

**Keputusan ops** (hanya admin & ops):

| Tombol | Status review | Efek |
|---|---|---|
| **Setujui (layak tagih)** | `approved` | Foto WARN/REJECT tetap boleh dipakai sebagai bukti penagihan. Masuk evidence pelanggan, `retake_required` menjadi `false` |
| **Minta foto ulang** | `retake_requested` | Foto **tidak** dipakai di evidence, meski verdict-nya PASS, dan `retake_required` menjadi `true`. Tindak lanjuti ke kurir/cabang di luar engine; foto pengganti yang diupload akan dianalisis sebagai foto baru |
| **Tandai kecurangan** | `fraud` | Foto **tidak** dipakai di evidence. Tandai untuk eskalasi ke SPV |
| **Reset** | `none` | Batalkan keputusan; foto kembali ke antrean review (jika WARN/REJECT/error) |
| **Analisis ulang** | (tidak berubah) | Foto dianalisis ulang memakai config tenant **terbaru** (misal setelah threshold diubah). Status sementara "pending" sampai worker selesai |

Kolom **Catatan (opsional)** ikut disimpan bersama keputusan. Setiap keputusan tercatat di **Riwayat review** (siapa, kapan, apa) dan di audit log, lalu dikirim sebagai webhook `review.updated` jika aplikasi berlangganan.

**Bagian bawah:** data pengiriman (kurir, cabang, pelanggan, waktu foto, lokasi dengan link Google Maps, sumber ingest, **Arsip engine** (`ocr/<tenant>/...` atau "-") dan **Lokasi di aplikasi** (atau "tidak dilaporkan aplikasi")), info gambar & kompresi (format, dimensi, ukuran sebelum → sesudah, quality, SSIM, SHA-256 file original), EXIF, riwayat review, dan metrik mentah (untuk kalibrasi).

---

## Evidence (penagihan)

Menu **Evidence** (semua role; untuk finance cukup key `viewer`). Kursor langsung di kotak resi. Isi nomor resi (satu
per baris, maks. 500) lalu **Buat PDF**. Foto diambil dari arsip engine (atau bucket aplikasi untuk foto polling):

- Satu resi → PDF evidence resi itu. Bisa juga dari detail foto: link **Evidence PDF resi ini**.
- Banyak resi → satu PDF gabungan, misal semua resi dalam satu periode invoice.
- Isi PDF: hanya foto layak (lihat [Pengaruh ke evidence pelanggan](#pengaruh-ke-evidence-pelanggan)), tanpa skor
  internal, dengan kode verifikasi SHA-256 per foto. Resi tanpa foto layak tidak muncul; jika tidak ada satu pun, PDF
  berisi keterangan itu.
- Key yang terikat tenant hanya melihat foto tenant-nya.

---

## Panduan keputusan ops

Pertanyaan kuncinya: **apakah foto ini cukup sebagai bukti untuk ditagihkan dan dipertahankan jika pelanggan komplain?**

| Situasi | Keputusan |
|---|---|
| REJECT, tapi penerima/paket/resi **masih jelas terlihat** (engine terlalu ketat, misal latar bokeh) | **Setujui**. Catat di kolom catatan jika polanya sering, supaya admin bisa mengkalibrasi threshold |
| WARN yang masih jelas | Dengan `review_required: false` tidak perlu tindakan (sudah masuk evidence). Dengan `review_required: true`: **Setujui** |
| Foto buram/pecah/gelap sampai **resi atau penerima tidak bisa dikenali** | **Minta foto ulang** (atau minta bukti lain ke cabang) |
| File rusak (`UNREADABLE_FILE`) | **Minta foto ulang** |
| `DUPLICATE_EXACT`: foto identik dipakai untuk resi lain | Buka kedua foto (cari resi lain di **Cari foto**). Jika memang foto yang sama dipakai ulang → **Tandai kecurangan** |
| `DUPLICATE_SIMILAR` | Bandingkan dulu. Lokasi yang sama (pintu kantor/gudang yang sama, paket sejenis) bisa mirip tanpa curang. Jika jelas foto yang sama dengan sedikit perubahan → **Tandai kecurangan**; jika berbeda → **Setujui** |
| `EDITING_SOFTWARE`, `OLD_PHOTO`, `GPS_MISMATCH`, `CAPTURE_TIME_MISMATCH` | Indikasi, bukan bukti. Cek konteks (jam pengiriman, lokasi). Ragu → konfirmasi ke kurir/SPV sebelum **Tandai kecurangan** |
| Hanya `EXIF_MISSING` (info) | Bukan masalah, tidak perlu tindakan |
| Salah klik keputusan | **Reset**, lalu pilih ulang |
| Threshold baru saja diubah admin | **Analisis ulang** foto yang ingin dinilai ulang |

### Pengaruh ke evidence pelanggan

Evidence (`/v1/evidence/...`, termasuk PDF) **hanya** menampilkan foto yang sudah selesai dianalisis
(`status = done`) dan **tidak** berstatus `retake_requested` atau `fraud`, dengan aturan verdict sesuai setelan tenant:

| Verdict | `review_required: false` (bawaan) | `review_required: true` |
|---|---|---|
| `PASS` | Langsung masuk | Langsung masuk |
| `WARN` | **Langsung masuk** | Masuk setelah **Setujui** |
| `REJECT` | Masuk hanya setelah **Setujui** | Masuk hanya setelah **Setujui** |

Foto yang diminta ulang atau ditandai curang tidak pernah tampil ke pelanggan (dan `allow_status_update` menjadi
`false`). Evidence hanya berisi foto yang **disimpan aplikasi** di SILO (isi tidak dimanipulasi), sedangkan kode
verifikasi (SHA-256) merujuk ke file yang dikirim kurir ke engine.
Skor internal dan risiko keaslian tidak pernah ditampilkan ke pelanggan.

---

## Uji foto

Menganalisis foto **tanpa menyimpan apa pun**. Dipakai untuk mencoba threshold, onboarding aplikasi baru, atau melatih tim. Bisa dipakai role `admin`, `ops`, dan `viewer`. Untuk mencoba dari HP dengan kamera, pakai [Uji Coba Kamera](#uji-coba-kamera-hp).

1. Pilih **Tenant** untuk memakai profile tenant itu, atau "(profile bawaan)".
2. Pilih **Jenis foto / profile**. Tanpa tenant, daftar berisi profile bawaan engine (`default`, `pod_recipient`, `pod_document`, `package`, `goods`, `location`). Setelah tenant dipilih, daftar berganti ke semua profile tenant tersebut (termasuk profile buatan tenant).
3. Pilih file (kursor sudah di kotak file saat halaman dibuka). Analisis langsung berjalan setelah file dipilih; tombol
   **Analisis** mengulanginya, misal setelah mengganti profile.

Hasilnya: verdict, profile, mode, aksi, kotak **Wajib foto ulang** / **Diperbaiki otomatis** (yang kedua hanya jika profile mengaktifkan koreksi pencahayaan opsional), skor, catatan beserta saran, hasil kompresi (ukuran sebelum → sesudah, dimensi, quality, SSIM), waktu proses, dan JSON lengkap.

Catatan: cek duplikat tidak dijalankan di sini (tidak dibandingkan dengan foto lain), dan tidak ada data yang tersimpan maupun webhook yang terkirim.

---

## Uji Coba Kamera (HP)

Halaman terpisah (`/ui/coba.html`) untuk mencoba engine **tanpa membangun aplikasi mobile**. Halaman ini berperan
sebagai aplikasi kurir dan dioptimalkan untuk layar HP. Masuk dengan API key `admin`, `ops`, atau `app` (`viewer` hanya
bisa uji cepat). API key tersimpan di browser yang sama dengan Web UI, jadi cukup login sekali.

| Bagian | Isi |
|---|---|
| **1. Pengaturan uji** | **Simulasi aplikasi kurir** (alur sama dengan aplikasi asli: foto dikirim ke engine, diperiksa dan dioptimasi, engine mengembalikan keputusan + foto hasil; tunggu maks. 3 detik. Di aplikasi asli, aplikasi juga menyimpan foto hasil ke bucket-nya; engine mengarsipkan foto di bucket `ocr`) atau **Uji cepat** (tidak dicatat). Tenant (bawaan `uji-coba`), jenis foto, **Tampilkan sebagai mode** (`shadow`/`warn`/`enforce`, hanya mengubah tampilan), nomor resi (otomatis `UJI...`, berganti setelah foto diterima; tetap sama saat foto ulang), ID kurir, lokasi GPS (butuh HTTPS) |
| **2. Ambil foto** | **📷 Ambil foto**: kamera bawaan HP (seperti aplikasi kurir, EXIF ikut). **🎥 Kamera langsung**: viewfinder di browser dengan tombol jepret, balik kamera, dan lampu (jika HP mendukung). Butuh HTTPS atau localhost. **🖼 Galeri / file** |
| **3. Hasil** | **Yang dilihat kurir**: tiruan layar aplikasi dengan `courier_message` dan tombol (Foto ulang / Tetap kirim / Lanjut) sesuai `action` dan `retake_required`, baris **Status pengiriman: diupdate / TIDAK diupdate** (sesuai `allow_status_update`; foto ditolak di mode `enforce` hanya punya tombol Foto ulang) beserta keterangan penyimpanan ("aplikasi menyimpan foto hasil engine ke bucket-nya ... engine mengarsipkan salinannya di bucket ocr" / "engine tidak menjawab: aplikasi menyimpan foto asli" / "aplikasi TIDAK menyimpan foto ini ... engine mengarsipkan foto asli di bucket ocr untuk review" / "uji cepat: tidak disimpan"), plus waktu tunggu kurir. **Detail untuk tim**: verdict, boleh update status, **Foto untuk disimpan aplikasi** (ukuran, dioptimasi atau asli, atau "tidak ada (ditolak)"; hanya di mode simulasi), wajib foto ulang, aksi, waktu tunggu & proses engine, ukuran sebelum → sesudah, masuk evidence atau tidak, perbandingan foto asli vs hasil engine, skor, catatan, kompresi, EXIF, JSON respons. Di mode simulasi ada tombol **Buka di Web UI**; foto uji tersimpan di arsip engine (`ocr/uji-coba/...`), jadi tampil di Web UI dan evidence resi uji |
| **Riwayat sesi ini** | Thumbnail percobaan sebelumnya; klik untuk melihat hasilnya lagi (hanya di browser, hilang saat halaman ditutup) |

Cara membuka dari HP dan menyalakan HTTPS untuk kamera langsung: [getting-started.md §4b](getting-started.md#4b-uji-coba-dari-hp-tanpa-membangun-aplikasi).

Data simulasi masuk tenant `uji-coba` (retensi 30 hari, tanpa sumber bucket, tanpa webhook), jadi dashboard
aplikasi kurir tidak terpengaruh. Filter **Tenant** di Web UI untuk melihat atau menyembunyikannya.

---

## Tenant (admin)

Satu tenant = satu aplikasi yang terhubung. Referensi semua field: [configuration.md](configuration.md).

| Elemen | Fungsi |
|---|---|
| Tabel tenant | Kode, nama, versi config, jenis source, mode tiap profile, status aktif |
| **Ubah** | Membuka editor YAML config tenant |
| **Tenant baru** | Editor berisi template awal (tombol ini mendapat fokus saat halaman dibuka; editor YAML mendapat fokus setelah dibuka) |
| **Validasi** | Cek config tanpa menyimpan, lalu tampilkan daftar profile + "Profile hasil gabungan" (nilai akhir setelah digabung dengan bawaan) |
| **Simpan** | Validasi lalu simpan. Versi naik jika isi berubah. Berlaku di semua proses dalam ≤ 5 detik, tanpa restart |
| **Nonaktifkan / Aktifkan** | Nonaktif = polling & event berhenti untuk tenant ini dan `POST /v1/analyze` ditolak. Data lama tetap ada |
| **Riwayat versi** | Semua versi config: siapa, kapan, dan isi lengkapnya. Untuk rollback, salin isi versi lama ke editor lalu **Simpan** |

Config yang salah tidak akan tersimpan; pesan error menyebut field yang bermasalah.

---

## API key (admin)

| Field | Arti |
|---|---|
| Nama / pemakai | Label, contoh `aplikasi-kurir-prod`, `ops-budi`. Kursor langsung di sini; **Enter** membuat key |
| Role | `app`, `ops`, `viewer`, `customer`, `admin` |
| Tenant | **Wajib** untuk role `app` (key hanya bisa mengakses tenant itu). Opsional untuk role lain (membatasi ke satu tenant) |
| Customer ID | **Wajib** untuk role `customer` (pelanggan hanya bisa melihat evidence untuk `customer_id` miliknya) |

- Klik **Buat**. Nilai key tampil **sekali saja** di kotak kuning; salin dan berikan lewat kanal aman. Engine hanya menyimpan hash-nya.
- Tabel menampilkan prefix key, role, tenant, customer, tanggal dibuat, dan terakhir dipakai (diperbarui paling sering sekali per menit).
- **Cabut** langsung menonaktifkan key; aplikasi yang memakainya akan mendapat `401`. Buat key baru sebelum mencabut key yang masih dipakai produksi.

Praktik baik: satu key per aplikasi per lingkungan (dev/staging/prod) dan satu key per orang ops, supaya audit log jelas dan pencabutan tidak mengganggu pihak lain.

---

<a id="pengaturan-silo-admin"></a>

## Koneksi SILO (admin)

Mengatur koneksi engine ke SILO tanpa mengedit `.env` dan tanpa restart (dulu bernama "Pengaturan SILO"). Engine
**membaca** bucket aplikasi (polling, foto tanpa arsip) dan **menulis** hanya ke bucket arsipnya (bawaan `ocr`). Kursor langsung di field **Endpoint**. Referensi lengkap:
[configuration.md §1.6](configuration.md#16-koneksi-silo-lewat-web-ui).

**Kartu "Koneksi"** berisi field Endpoint, Endpoint publik, Region, Access key, Secret key, Gaya alamat, Signature,
Bucket foto aplikasi, dan **Bucket arsip engine** (bawaan `ocr`; arsip per aplikasi, masa simpan 520 hari). Di bawah tiap field tertulis asal nilainya saat
ini: *dari halaman ini*, *dari env server*, atau *bawaan*. Field yang berasal dari halaman ini punya link
**kembalikan ke env**.

Langkah memasang koneksi dari JSON `mc` (format yang biasa dipakai `mc alias` / `~/.mc/config.json`):

1. Tempel JSON ke kotak **Impor dari konfigurasi mc / MinIO**, contoh
   `{"url":"https://s3.pcpexpress.com","accessKey":"...","secretKey":"...","api":"s3v4","path":"auto"}`.
2. Klik **Isi form dari JSON**. Form terisi otomatis (belum disimpan). Jika `url` berisi `127.0.0.1` atau
   `localhost`, muncul peringatan: di dalam Docker/Kubernetes alamat itu menunjuk ke container engine sendiri, bukan
   SILO. Ganti dengan host/IP SILO (atau `http://silo:9000` di compose lokal).
3. Klik **Test koneksi**. Tiga cek dijalankan dengan nilai form: **Baca bucket aplikasi**, **Baca isi foto**, dan
   **Tulis bucket arsip 'ocr'** (menulis lalu menghapus file uji kecil). Bucket aplikasi tidak ditulis. Semua harus ✅. Jika ❌, baca pesan errornya
   (lihat [troubleshooting.md](troubleshooting.md#koneksi-silo-test-koneksi-gagal)).
4. Klik **Simpan**. Berlaku di semua proses dalam ≤ 10 detik.

Catatan:

- **Secret key** disimpan terenkripsi dan tidak pernah ditampilkan lagi (placeholder "•••••• (tersimpan)").
  Kosongkan field ini jika tidak ingin mengubahnya. Access key ditampilkan tersamar.
- Jika muncul kotak **"PQE_SECRET_KEY belum diisi di server"**, secret key tidak bisa disimpan lewat halaman ini
  sampai admin server mengisi `PQE_SECRET_KEY` (lihat [operations.md](operations.md)). Field lain tetap bisa disimpan.
- Kartu kanan menampilkan contoh **policy permission minimal** (`s3:GetObject` + `s3:ListBucket` di bucket aplikasi;
  baca/tulis/hapus di bucket arsip) sesuai nama bucket di form. Gunakan service account khusus engine, bukan akun admin/root SILO.
- Setiap simpan tercatat di audit log (`settings.storage.update`, tanpa nilai rahasia).
- Bucket aplikasi hanya dibaca; engine hanya menulis ke bucket arsip. Ini dijaga di kode engine: walau key yang diisi punya akses penuh, engine menolak
  menulis atau menghapus di bucket aplikasi.

---

## Backup NAS (admin)

Menu **Backup NAS** mengatur salinan terjadwal database engine dan arsip foto (bucket `ocr`; foto polling dari bucket aplikasi) ke NAS. Hanya role `admin`. Backup **hanya
menyalin**: file di NAS tidak pernah dihapus engine, kecuali dump database lama di luar jumlah simpan.

**Kartu atas:** status jadwal (Aktif/Mati), **Foto belum tersalin**, **Foto sudah tersalin**, dan **Ruang kosong NAS**
(tujuan folder). Foto tanpa arsip dan tanpa lokasi aplikasi tidak dihitung "belum tersalin". Jika backup terakhir gagal, muncul kotak merah berisi pesan error dan waktunya.

**Kartu Tujuan:**

| Field | Isi |
|---|---|
| Aktifkan backup terjadwal | Jadwal jalan otomatis. Jika mati, tombol backup manual tetap bisa dipakai |
| Jenis NAS (kursor di sini saat halaman dibuka) | **Folder (NAS di-mount lewat SMB/NFS)** atau **NAS S3-compatible (Synology/QNAP/MinIO)** |
| Folder di dalam container | Bawaan `/backup` = folder `NAS_MOUNT` di server. Share NAS di-mount di server ke folder itu |
| Endpoint, Bucket, Prefix, Region, Access key, Secret key, Gaya alamat | Untuk NAS S3. Secret disimpan terenkripsi dan tidak ditampilkan lagi; kosongkan jika tidak ingin mengubah |

- **Test NAS**: menguji nilai di form (belum disimpan) dan menampilkan ✅/❌ untuk: tujuan ada, tulis-baca-hapus file uji,
  ruang kosong (> 1 GB, tujuan folder), akses baca bucket arsip engine dan bucket foto aplikasi, dan kesiapan dump database.
- **Simpan**: berlaku di putaran worker berikutnya (±10 detik), tanpa restart.

**Kartu Yang dibackup:** **Database engine** (wajib) dan **Foto** (aktif secara bawaan; satu salinan per foto: arsip
engine bila ada, selain itu foto di bucket aplikasi untuk foto polling). Bagian lipat **Data versi lama** berisi pilihan
untuk salinan di bucket `photos-*` dari versi sebelum arsip `ocr` (foto asli upload, hasil kompres,
thumbnail); instalasi baru tidak punya data ini. **Jadwal**: foto setiap 15 menit, database
setiap 24 jam, simpan 14 dump terakhir.

**Status:** waktu backup foto dan database terakhir (jumlah foto/file, nama dan ukuran dump), total file disalin, dan
status permintaan. Tombol **Backup foto sekarang** / **Backup database sekarang** meminta worker menjalankannya dalam
±10 detik; halaman memuat ulang status otomatis setelah ±12 detik.

Cara mount NAS di server (Linux, macOS, Kubernetes), izin uid 10001, dan restore: [operations.md bagian 6](operations.md#6-postgresql--backup).
Masalah umum: [troubleshooting.md](troubleshooting.md#backup-nas).

---

## Kalibrasi

Membandingkan verdict engine dengan keputusan reviewer di Review, per tenant dan jenis foto.

| Elemen | Arti |
|---|---|
| **Saran** | Angka batas yang sebaiknya diubah (contoh "turunkan `sharpness.reject` dari 25 ke 16") dan saran naik mode (`shadow → warn → enforce`). Engine tidak mengubah config sendiri: salin angkanya ke config tenant (menu Tenant) |
| **Salah tolak** | Foto REJECT yang disetujui reviewer (layak tagih). Tinggi = engine terlalu ketat |
| **Salah terima** | Foto PASS yang diminta foto ulang / ditandai kecurangan. Tinggi = engine terlalu longgar |
| **Naik mode** | Apa yang masih kurang sebelum aman naik mode (jumlah foto, jumlah review, atau salah tolak masih tinggi) |
| **Alasan** | Per kode alasan: jumlah, berapa yang direview, berapa yang disetujui reviewer |

Saran baru muncul jika data review cukup. Selama UAT, review foto REJECT dan sebagian foto PASS secara acak.
Detail aturan: [configuration.md](configuration.md#6-kalibrasi-threshold).

---

## Laporan & alert (admin)

Ringkasan harian dan peringatan otomatis ke chat tim.

1. Buat **incoming webhook** di Slack / Google Chat / Microsoft Teams / Mattermost, salin URL-nya.
2. Tempel di **URL webhook** (disimpan terenkripsi, butuh `PQE_SECRET_KEY` di server), pilih jam ringkasan harian.
3. **Kirim tes** → pesan muncul di channel. Lalu centang **Aktifkan** dan **Simpan**.

Isi ringkasan harian: jumlah foto, PASS/WARN/REJECT/error, waktu proses, alasan terbanyak, cabang & kurir dengan
tolakan tertinggi, dan lonjakan. Setiap Senin ditambah laporan kalibrasi 7 hari. **Alert lonjakan** dicek tiap 15
menit dan dikirim paling sering sekali per cabang/kurir per 24 jam. Format **JSON lengkap** untuk sistem tim sendiri
(`{"event", "text", "data"}`).

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
| **Audit log** (admin) | 50 aktivitas terakhir: perubahan config, API key, review, retry job/webhook, Koneksi SILO, retensi |
| Link **/metrics** dan **/docs** | Metrik Prometheus dan dokumentasi API interaktif (Swagger) |
| **Kesiapan production (preflight)** (admin) | Tombol **Jalankan preflight**: memeriksa secret, database, koneksi SILO ("Baca bucket aplikasi", "Tulis bucket arsip 'ocr'", "Bucket aplikasi hanya dibaca"), mode tenant, backup NAS, worker, OCR. Tiap baris ✅ / ⚠️ / ❌ beserta cara memperbaiki. Hasil "SIAP production" = tidak ada ❌. Sama dengan perintah `pqe preflight` |

---

## Tips

- Pesan alasan di Web UI selalu berbahasa Indonesia (bahasa untuk kurir diatur aplikasi lewat `lang`).
- Web UI bisa dibuka dari HP: menu lewat tombol ☰, kartu angka tersusun dua kolom, filter dilipat (**Filter lainnya**), dan tabel lebar bisa digeser di dalam kartunya.
- Saat melapor masalah, sebutkan versi engine yang tampil di sidebar.
- Waktu ditampilkan dalam zona waktu komputer Anda.
- Mulai hari dengan Dashboard → klik **Menunggu review ops** → periksa antrean dari atas (opsional jika `review_required: false`, tetapi berguna untuk menangkap kecurangan dan kalibrasi).
- Jika satu kurir/cabang mendominasi daftar masalah, cek dulu pola alasannya. `BLURRY` berulang biasanya soal cara memotret; `PIXELATED`/`HEAVY_COMPRESSION` berulang biasanya setting kompresi di aplikasi atau HP tertentu.
- Lonjakan REJECT mendadak pada satu aplikasi sering berarti ada rilis aplikasi baru yang mengubah setting kamera/kompresi. Laporkan ke tim aplikasi.
- Jangan menyetujui foto hanya untuk mengosongkan antrean. Evidence ke pelanggan bergantung pada keputusan ini.
