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

## [0.5.0] - 30 Okt 2026

### Modul baru: OCR dokumen generik ([ADR 0008](docs/adr/0008-modul-ocr-invoice.md), [ADR 0009](docs/adr/0009-modul-dokumen-generik-multi-client.md))
- Engine yang sama sekarang juga membaca **dokumen** untuk aplikasi client mana pun (FR-OCR-001..013). Terpisah dari
  pemeriksaan foto POD: router, tabel (`document_*`, `api_key_secrets`), antrean, dan thread worker sendiri. Fungsi
  foto tidak berubah. Desain, abuse case, acceptance criteria: [docs/document-ocr-design.md](docs/document-ocr-design.md).
- Endpoint `POST /v1/jobs` (PDF/JPEG/PNG, async), `GET /v1/jobs/{id}` (teks, blok ber-koordinat, field + confidence,
  layer), `POST /v1/templates` (koreksi → template per penerbit, idempoten), `GET /v1/templates/{type}/{issuer_id}`.
- **Multi-client:** satu aplikasi = satu namespace (tenant) dengan API key + `api_secret` HMAC, job, file, template,
  dan host callback sendiri; isolasi antar-client dites. Onboarding: `pqe create-client`. Rotasi: `pqe set-api-secret`.
- **Jenis dokumen = plugin** (`detect/extract/validate`, `field_kinds`, penerbit, pengayaan); plugin pertama
  `invoice` (invoice vendor Indonesia). Plugin baru lewat entry point `pqe.document_extractors` /
  `PQE_DOCUMENT_PLUGINS` tanpa mengubah core.
- Layer 1 teks PDF digital (PDFium), layer 2 QR e-Faktur (plugin invoice, **mati secara bawaan**,
  `PQE_INVOICE_EFAKTUR_QR`), layer 3 scan/foto (deskew, ratakan bayangan, kontras, binarize, Tesseract ind+eng),
  layer 4 template per penerbit dari koreksi pengguna. Tanpa AI generatif / model ML.
- Dokumen dibaca di proses anak dengan batas memori, CPU, waktu, env tanpa rahasia, prioritas CPU rendah. Tipe file
  dari magic bytes. Diverifikasi independen (2 putaran) sebelum rilis.
- Callback ber-HMAC opsional (host per client), retensi file inbox 30 hari / hasil 365 hari, `pqe document-worker`.
- `/healthz` menampilkan versi tesserocr (Tesseract) & PDFium dan jenis dokumen terdaftar; `pqe preflight` memeriksa
  modul dokumen. Metrik baru `pqe_document_*`.
- Client contoh tanpa dependency: `clients/php/DocumentOcrClient.php`, `clients/typescript/document-client.ts`.
- Dependency baru: `pypdfium2` (BSD-3/Apache-2.0). Config tenant: field opsional `callback_hosts`.

## [0.4.1] - 25 Okt 2026 (UAT)

### Perbaikan
- Web UI: label versi kosong jika langsung masuk dengan API key yang tersimpan di browser (tanpa form login).
  Sekarang versi selalu dimuat saat halaman dibuka.
- Web UI dikirim dengan `Cache-Control: no-cache`: setelah deploy, browser langsung memakai `app.js`/`style.css`
  terbaru (dicek ulang lewat ETag), tidak lagi tampilan lama dari cache.

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
