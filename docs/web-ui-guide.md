# Panduan Web UI

Web UI dipakai tim **ops/SPV**, **admin engine**, dan **viewer** (finance, manajemen) untuk memantau kualitas foto, meninjau foto bermasalah, dan mengatur engine.

- Alamat: `https://<host-engine>/ui` (lokal: http://localhost:8000/ui)
- Tidak perlu instalasi. Web UI disajikan langsung oleh engine.
- **Panduan integrasi untuk programmer aplikasi** ada di `https://<host-engine>/ui/guide.html` (tanpa login). Link-nya
  ada di menu (**Panduan Integrasi ↗**) dan di halaman login, jadi programmer bisa dikirimi link itu saja. Halaman itu
  juga memuat diagram alur integrasi, alur penggunaan, keputusan di aplikasi kurir, dan proses di dalam engine.
- **Uji Coba Kamera** ada di `https://<host-engine>/ui/coba.html` (menu **Uji Coba Kamera ↗**): halaman yang dioptimalkan
  untuk HP untuk mencoba engine seperti aplikasi kurir, lihat [Uji Coba Kamera](#uji-coba-kamera-hp).

Daftar isi:

- [Masuk](#masuk)
- [Dashboard](#dashboard)
- [Review (antrean ops)](#review-antrean-ops)
- [Cari foto](#cari-foto)
- [Detail foto](#detail-foto)
- [Panduan keputusan ops](#panduan-keputusan-ops)
- [Uji foto](#uji-foto)
- [Uji Coba Kamera (HP)](#uji-coba-kamera-hp)
- [Tenant (admin)](#tenant-admin)
- [API key (admin)](#api-key-admin)
- [Pengaturan SILO (admin)](#pengaturan-silo-admin)
- [Sistem](#sistem)
- [Tips](#tips)

---

## Masuk

Login memakai **API key**, bukan username/password.

| Role | Bisa apa di Web UI |
|---|---|
| `admin` | Semua menu, termasuk Tenant, API key, Pengaturan SILO, ulangi job gagal, audit log |
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
| **Menunggu review ops** | Foto WARN/REJECT/error yang belum diberi keputusan. Klik angkanya untuk ke Review. Dengan `review_required: false` (bawaan), angka ini bukan tunggakan: foto WARN sudah masuk evidence, review hanya untuk koreksi |
| **Tren harian** | Batang bertumpuk per hari: PASS (hijau), WARN (oranye), REJECT (merah), masih diproses (abu-abu) |
| **Alasan terbanyak** | Kode alasan paling sering (WARN/REJECT). Klik kodenya untuk membuka **Cari foto** dengan filter alasan itu |
| **Kurir / Cabang dengan foto bermasalah terbanyak** | Top 10 jumlah foto WARN/REJECT. Dasar pembinaan kurir/cabang |

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

Klik **Terapkan** setelah mengubah filter. Daftar berisi 50 foto per halaman, terbaru di atas (tombol **Sebelumnya / Berikutnya**). Klik baris untuk membuka [detail foto](#detail-foto). Setelah keputusan diambil, foto hilang dari antrean.

Kolom tabel: thumbnail, resi (+ ID eksternal), tenant/jenis foto, kurir/cabang, verdict (dengan tulisan merah **foto ulang** jika `retake_required`), alasan utama, status review, waktu diterima.

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

**Kotak status tambahan:**

- **Wajib foto ulang** (merah): kualitas tidak bisa dimaksimalkan (verdict REJECT setelah percobaan perbaikan, atau
  ops memilih "Minta foto ulang"). Berisi pesan yang sama dengan yang diterima aplikasi kurir (`retake_message`).
- **Diperbaiki otomatis** (biru): pencahayaan foto diperbaiki dengan kurva global tanpa AI dan lolos verifikasi
  eksak (tidak ada teks/angka yang berubah). Ditampilkan kurva yang dipakai (contoh `levels:12-140, gamma:0.62`) dan
  verdict + kode alasan **sebelum** perbaikan. Gambar utama dan "versi kompres" adalah hasil perbaikan; **Buka
  original** selalu membuka file asli yang tidak diubah. Foto hasil perbaikan tidak diberi label/watermark apa pun.

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

**Bagian bawah:** data pengiriman (kurir, cabang, pelanggan, waktu foto, lokasi dengan link Google Maps, sumber ingest, lokasi objek di SILO), info gambar & kompresi (format, dimensi, ukuran sebelum → sesudah, quality, SSIM, SHA-256 file original), EXIF, riwayat review, dan metrik mentah (untuk kalibrasi).

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

Foto yang diminta ulang atau ditandai curang tidak pernah tampil ke pelanggan. Jika pencahayaan diperbaiki otomatis,
evidence memakai versi yang sudah diperbaiki, sedangkan kode verifikasi (SHA-256) tetap merujuk ke file original.
Skor internal dan risiko keaslian tidak pernah ditampilkan ke pelanggan.

---

## Uji foto

Menganalisis foto **tanpa menyimpan apa pun**. Dipakai untuk mencoba threshold, onboarding aplikasi baru, atau melatih tim. Bisa dipakai role `admin`, `ops`, dan `viewer`. Untuk mencoba dari HP dengan kamera, pakai [Uji Coba Kamera](#uji-coba-kamera-hp).

1. Pilih **Tenant** untuk memakai profile tenant itu, atau "(profile bawaan)".
2. Pilih **Jenis foto / profile**. Tanpa tenant, daftar berisi profile bawaan engine (`default`, `pod_recipient`, `pod_document`, `package`, `goods`, `location`). Setelah tenant dipilih, daftar berganti ke semua profile tenant tersebut (termasuk profile buatan tenant).
3. Pilih file, lalu klik **Analisis**.

Hasilnya: verdict, profile, mode, aksi, kotak **Wajib foto ulang** / **Diperbaiki otomatis** (jika berlaku, dengan verdict sebelum perbaikan), skor, catatan beserta saran, hasil kompresi (ukuran sebelum → sesudah, dimensi, quality, SSIM), waktu proses, dan JSON lengkap.

Catatan: cek duplikat tidak dijalankan di sini (tidak dibandingkan dengan foto lain), dan tidak ada data yang tersimpan maupun webhook yang terkirim.

---

## Uji Coba Kamera (HP)

Halaman terpisah (`/ui/coba.html`) untuk mencoba engine **tanpa membangun aplikasi mobile**. Halaman ini berperan
sebagai aplikasi kurir dan dioptimalkan untuk layar HP. Masuk dengan API key `admin`, `ops`, atau `app` (`viewer` hanya
bisa uji cepat). API key tersimpan di browser yang sama dengan Web UI, jadi cukup login sekali.

| Bagian | Isi |
|---|---|
| **1. Pengaturan uji** | **Simulasi aplikasi kurir** (foto disimpan, alur sama dengan aplikasi asli: upload → antrean → worker → SILO, tunggu maks. 3 detik) atau **Uji cepat** (tidak disimpan). Tenant (bawaan `uji-coba`), jenis foto, **Tampilkan sebagai mode** (`shadow`/`warn`/`enforce`, hanya mengubah tampilan), nomor resi (otomatis `UJI...`, berganti setelah foto diterima; tetap sama saat foto ulang), ID kurir, lokasi GPS (butuh HTTPS) |
| **2. Ambil foto** | **📷 Ambil foto**: kamera bawaan HP (seperti aplikasi kurir, EXIF ikut). **🎥 Kamera langsung**: viewfinder di browser dengan tombol jepret, balik kamera, dan lampu (jika HP mendukung). Butuh HTTPS atau localhost. **🖼 Galeri / file** |
| **3. Hasil** | **Yang dilihat kurir**: tiruan layar aplikasi dengan pesan dan tombol (Foto ulang / Tetap kirim / Lapor ke SPV / Lanjut) sesuai `action` dan `retake_required`, plus waktu tunggu kurir. **Detail untuk tim**: verdict, wajib foto ulang, aksi, waktu tunggu & proses engine, ukuran sebelum → sesudah, masuk evidence atau tidak, perbandingan foto asli vs hasil engine, perbaikan otomatis, skor, catatan, kompresi, EXIF, JSON respons. Di mode simulasi ada tombol **Buka di Web UI** dan **Evidence PDF resi ini** |
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

## Pengaturan SILO (admin)

Mengatur koneksi engine ke SILO tanpa mengedit `.env` dan tanpa restart. Referensi lengkap:
[configuration.md §1.6](configuration.md#16-pengaturan-silo-lewat-web-ui).

**Kartu "Koneksi"** berisi field Endpoint, Endpoint publik, Region, Access key, Secret key, Gaya alamat, Signature,
dan empat bucket (foto aplikasi, hasil kompres, thumbnail, upload). Di bawah tiap field tertulis asal nilainya saat
ini: *dari halaman ini*, *dari env server*, atau *bawaan*. Field yang berasal dari halaman ini punya link
**kembalikan ke env**.

Langkah memasang koneksi dari JSON `mc` (format yang biasa dipakai `mc alias` / `~/.mc/config.json`):

1. Tempel JSON ke kotak **Impor dari konfigurasi mc / MinIO**, contoh
   `{"url":"https://s3.pcpexpress.com","accessKey":"...","secretKey":"...","api":"s3v4","path":"auto"}`.
2. Klik **Isi form dari JSON**. Form terisi otomatis (belum disimpan). Jika `url` berisi `127.0.0.1` atau
   `localhost`, muncul peringatan: di dalam Docker/Kubernetes alamat itu menunjuk ke container engine sendiri, bukan
   SILO. Ganti dengan host/IP SILO (atau `http://silo:9000` di compose lokal).
3. Klik **Test koneksi**. Empat cek dijalankan dengan nilai form: baca bucket aplikasi, tulis+hapus objek uji di
   bucket hasil kompres, akses bucket thumbnail, akses bucket upload. Semua harus ✅. Jika ❌, baca pesan errornya
   (lihat [troubleshooting.md](troubleshooting.md#pengaturan-silo-test-koneksi-gagal)).
4. Klik **Simpan**. Berlaku di semua proses dalam ≤ 10 detik.

Catatan:

- **Secret key** disimpan terenkripsi dan tidak pernah ditampilkan lagi (placeholder "•••••• (tersimpan)").
  Kosongkan field ini jika tidak ingin mengubahnya. Access key ditampilkan tersamar.
- Jika muncul kotak **"PQE_SECRET_KEY belum diisi di server"**, secret key tidak bisa disimpan lewat halaman ini
  sampai admin server mengisi `PQE_SECRET_KEY` (lihat [operations.md](operations.md)). Field lain tetap bisa disimpan.
- Kartu kanan menampilkan contoh **policy permission minimal** untuk service account engine sesuai nama bucket di form.
  Gunakan service account khusus engine, bukan akun admin/root SILO.
- Setiap simpan tercatat di audit log (`settings.storage.update`, tanpa nilai rahasia).

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
| **Audit log** (admin) | 50 aktivitas terakhir: perubahan config, API key, review, retry job/webhook, Pengaturan SILO, retensi |
| Link **/metrics** dan **/docs** | Metrik Prometheus dan dokumentasi API interaktif (Swagger) |

---

## Tips

- Bahasa pesan alasan mengikuti bahasa browser (Indonesia/Inggris).
- Waktu ditampilkan dalam zona waktu komputer Anda.
- Mulai hari dengan Dashboard → klik **Menunggu review ops** → periksa antrean dari atas (opsional jika `review_required: false`, tetapi berguna untuk menangkap kecurangan dan kalibrasi).
- Jika satu kurir/cabang mendominasi daftar masalah, cek dulu pola alasannya. `BLURRY` berulang biasanya soal cara memotret; `PIXELATED`/`HEAVY_COMPRESSION` berulang biasanya setting kompresi di aplikasi atau HP tertentu.
- Lonjakan REJECT mendadak pada satu aplikasi sering berarti ada rilis aplikasi baru yang mengubah setting kamera/kompresi. Laporkan ke tim aplikasi.
- Jangan menyetujui foto hanya untuk mengosongkan antrean. Evidence ke pelanggan bergantung pada keputusan ini.
