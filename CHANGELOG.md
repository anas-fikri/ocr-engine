# Catatan versi

Format mengikuti [Keep a Changelog](https://keepachangelog.com/id-ID/1.1.0/); nomor versi mengikuti
[Semantic Versioning](https://semver.org/lang/id/). Versi yang sedang berjalan tampil di Web UI (sidebar & login),
`GET /healthz`, `GET /v1/me`, header `X-PQE-Version` di setiap respons, dan `pqe --version`, lengkap dengan **kode
build** (commit git, contoh `v0.2.0 · 118e3a2`, header `X-PQE-Build`) jika di-deploy dengan `scripts/deploy.sh`.
Tanpa `scripts/deploy.sh` (misalnya `docker compose up --build` langsung), kode build tertulis `build ?` di Web UI.

Aturan naik versi: setiap perubahan yang dipasang ke server menaikkan nomor versi dan menambah catatan di sini.
Perbaikan saja → angka terakhir (0.2.0 → 0.2.1); fitur atau perubahan penilaian → angka tengah (0.2.x → 0.3.0).
Kode build berubah di setiap deploy, nomor versi berubah di setiap rilis.

Rilis dibuat dengan tag git `vX.Y.Z`; server production memakai tag, bukan cabang
([deploy-production.md](docs/deploy-production.md#11-operasional)).

## [0.4.0] - 25 Okt 2026 (UAT)

### Engine
- Arsip foto **ditolak** tidak lagi menyimpan file kamera asli yang besar: file asli dipakai bila ≤ 1,5 MB, selain itu
  disimpan sebagai salinan review JPEG kualitas tinggi (quality 82-92, s.d. 3000 px, EXIF tetap, tanpa koreksi)
  maks. 1,5 MB. Foto kamera ditolak 3-7 MB → ±1,3 MB; buram/gelapnya tetap terlihat (skor sama). Dikerjakan di
  belakang layar, kurir tidak menunggu. Atur per tenant dengan `archive_rejected_max_bytes` (`0` = file asli).

### Web UI
- Detail foto, baris **Arsip engine**: ukuran asli → ukuran salinan review, quality, dan resolusi.

## [0.3.0] - 24 Okt 2026 (UAT)

### Engine
- Jenis foto baru: `invoice` (invoice/struk/kwitansi, teks wajib terbaca, resolusi simpan lebih tinggi), `accident`
  (bukti kecelakaan/kerusakan/insiden) dan `other`. Untuk `accident`/`other` teks **tidak wajib**
  (`text_required: false`): keterbacaan tetap diukur, tetapi foto tanpa teks tidak ditolak.
- `REFERENCE_MISMATCH`: barcode di foto berisi nomor sejenis tetapi lain dari `awb`/`reference` yang dikirim aplikasi
  (kemungkinan foto paket/dokumen lain) → WARN untuk ditinjau. Field baru `reference` untuk nomor selain resi
  (nomor invoice, nomor laporan).
- `FAR_FROM_DESTINATION`: foto diambil lebih dari 1 km (`params.exif.dest_radius_km`) dari alamat tujuan
  (`dest_lat`/`dest_lng`); posisi dari GPS foto atau HP.
- **Laporan kalibrasi** (Web UI Kalibrasi, `pqe calibration`, `GET /v1/admin/calibration`): verdict vs keputusan
  reviewer, salah tolak/salah terima per jenis foto & alasan, saran angka batas, dan saran naik mode
  `shadow → warn → enforce` dengan syarat yang jelas.
- **Ringkasan harian & alert** ke Slack/Google Chat/Teams/Mattermost atau endpoint JSON (Web UI Laporan & alert,
  `pqe digest`): ringkasan 24 jam setiap pagi, laporan kalibrasi setiap Senin, alert lonjakan tolakan per cabang/kurir
  dan foto gagal diproses. URL webhook disimpan terenkripsi dan tidak pernah ditulis ke log.

### Web UI
- Halaman **Kalibrasi** dan **Laporan & alert**; Dashboard menampilkan "Perlu dicek (24 jam terakhir)".
- Uji Coba Kamera menampilkan hasil pra-cek di HP sebelum foto dikirim.

### Client
- **Pra-cek di HP** (`clients/typescript/precheck.js`): buram/gelap/terlalu terang/terlalu kecil diperiksa di
  browser/WebView sebelum dikirim, rumus ketajaman sama dengan engine, fail-open.
- `PhotoFields` TS/PHP: `reference`, `dest_lat`, `dest_lng`.

## [0.2.0] - 24 Okt 2026 (UAT)

### Engine
- Nomor resi dibaca dari **barcode/QR** label (zxing-cpp, bukan AI) sebelum OCR: label terbaca dalam puluhan ms
  (skor 95), tanpa menunggu Tesseract; hasil kompres dijaga agar barcode tetap terbaca.
- Lebih cepat: Tesseract sebagai library (model dimuat sekali, bukan per panggilan), area teks dibaca paralel, kompresi
  & thumbnail dikerjakan bersamaan dengan pemeriksaan. Foto kamera 12 MP ±1,8 → ±0,8 detik; foto buram ±1,3 → ±0,4 detik.
- Latar kertas/layar putih: cukup skor `ocr` ≥ 70 (sebelumnya 80) agar putih tidak dinilai silau. Screenshot dan
  struk yang jelas sering berskor 74-80 dan sebelumnya masih kena `GLARE`.

### Web UI
- Detail foto menampilkan nomor resi yang terbaca dari barcode.
- Label versi menampilkan `build ?` bila engine tidak di-deploy dengan `scripts/deploy.sh`.

## [0.1.0] - 24 Okt 2026 (UAT)

Rilis pertama untuk live uji coba dan UAT.

### Engine
- Penilaian kualitas foto: ketajaman, resolusi/pecah, pencahayaan, kontras, EXIF, foto identik/mirip dipakai ulang, OCR resi.
- Verdict `PASS`/`WARN`/`REJECT` dengan alasan + saran ID/EN, mode `shadow`/`warn`/`enforce` per tenant & profile.
- Keputusan siap pakai: `allow_status_update`, `courier_message`, `retake_required`. Status pengiriman tetap milik aplikasi.
- `POST /v1/analyze` mengembalikan foto hasil optimasi (`output_image`, base64, < 1 MB) untuk disimpan aplikasi ke
  bucket-nya sendiri ([ADR 0006](docs/adr/0006-engine-hanya-memproses-gambar.md)).
- Arsip engine di bucket `ocr`, per aplikasi, masa simpan 520 hari ([ADR 0007](docs/adr/0007-arsip-engine-di-bucket-ocr.md)):
  foto diterima = foto hasil, foto ditolak = foto asli untuk review & kalibrasi. Ditulis di belakang layar; bucket
  aplikasi tetap hanya dibaca.
- **Teks harus terbaca** di semua jenis foto: foto miring diluruskan dulu, OCR per area teks, pencocokan nomor resi,
  foto tidak terbaca ditolak (`NO_TEXT_DETECTED`/`TEXT_UNREADABLE`); waktu habis tidak menghukum (`OCR_TIMEOUT`).
- `text_first`: teks terbaca → gelap/terang/pudar/pecah/buram ringan cukup WARN (target berikutnya: paket jelas).
- Dokumen/struk/layar berlatar putih tidak lagi dinilai silau: putih dianggap kertas bila teks jelas terbaca.
- Halaman penuh teks dibaca per kelompok baris (awal/tengah/akhir) supaya tidak kehabisan waktu.
- Penajam teks tanpa AI (unsharp mask), dipakai hanya jika keterbacaan teks terbukti naik.
- Optimasi tanpa manipulasi: hanya diperkecil proporsional + kompresi dinamis dengan pengaman SSIM dan penjaga
  keterbacaan teks (hasil kompres dibaca ulang; kualitas/resolusi dinaikkan bila teks memburuk, maks. 1,5 MB).
- Lokasi SILO foto di aplikasi bisa dilaporkan (`object_key` atau `POST /v1/photos/{id}/stored`), opsional.
- Evidence PDF per resi / per invoice dengan kode verifikasi keaslian.
- Backup NAS terjadwal (folder SMB/NFS atau S3-compatible) untuk database dan arsip foto.
- `pqe preflight` dan `GET /v1/admin/preflight` untuk cek kesiapan production.
- Fail-open: engine lambat/mati tidak pernah menahan kurir.

### Web UI
- Dashboard, Review, Cari foto, Evidence, Uji foto, Tenant, API key, Koneksi SILO, Backup NAS, Sistem.
- Navigasi samping, versi terlihat, setiap halaman langsung fokus ke field utamanya, filter cukup tekan Enter, nyaman di HP.
- Uji Coba Kamera (`/ui/coba.html`) dan Panduan Integrasi (`/ui/guide.html`) tanpa build step.

### Integrasi & deploy
- Client TypeScript dan PHP (`checkFile`, `checkObject`, `reportStored`), tidak pernah throw, fail-open.
- `docker-compose.prod.yml` (engine + PostgreSQL + HTTPS Caddy), `scripts/prod-setup.sh`, opsi PostgreSQL di host
  (`scripts/host-postgres-setup.sh`), manifest Kubernetes, metrik Prometheus.
