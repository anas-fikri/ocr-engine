# Troubleshooting

Cari gejala di tabel, lalu ikuti kolom **Solusi**. Jika belum ketemu, cek bagian [Langkah diagnosis umum](#langkah-diagnosis-umum) di bawah.

Semua error API berbentuk:

```json
{"error": {"code": "KODE_ERROR", "message": "penjelasan"}}
```

Daftar isi:

- [Autentikasi & akses API](#autentikasi--akses-api)
- [Foto tidak muncul / tidak diproses](#foto-tidak-muncul--tidak-diproses)
- [Hasil analisis terasa salah](#hasil-analisis-terasa-salah)
- [Foto ulang, status pengiriman & koreksi opsional](#foto-ulang-status-pengiriman--koreksi-opsional)
- [Gambar, webhook, dan integrasi](#gambar-webhook-dan-integrasi)
- [Pengaturan SILO: test koneksi gagal](#pengaturan-silo-test-koneksi-gagal)
- [Infrastruktur & deployment](#infrastruktur--deployment)
- [Backup NAS](#backup-nas)
- [Waktu & retensi](#waktu--retensi)
- [Langkah diagnosis umum](#langkah-diagnosis-umum)

---

## Autentikasi & akses API

| Gejala | Penyebab | Solusi |
|---|---|---|
| `401 UNAUTHORIZED` "API key wajib (header X-API-Key)" | Header tidak dikirim | Kirim `X-API-Key: pqe_...` atau `Authorization: Bearer pqe_...` |
| `401 UNAUTHORIZED` "API key tidak valid atau sudah dicabut" | Salah ketik, spasi/baris baru ikut tersalin, atau key dicabut | Salin ulang tanpa spasi. Cek di Web UI → API key (kolom prefix dan status). Buat key baru jika dicabut |
| Web UI kembali ke halaman login "Sesi berakhir" | Key dicabut atau DB engine diganti (key lama tidak ada) | Login ulang dengan key yang valid |
| Web UI: "Role app/customer tidak bisa memakai Web UI" | Key role `app`/`customer` dipakai untuk login | Pakai key role `admin`, `ops`, atau `viewer` |
| `403 FORBIDDEN` "role ... tidak boleh mengakses endpoint ini" | Role key tidak cukup. Contoh: `app` memanggil `/v1/admin/*`, `customer` memanggil `/v1/photos`, `viewer` menekan tombol review | Pakai key dengan role yang sesuai (lihat [api-reference.md](api-reference.md)) |
| `403 FORBIDDEN` "API key tidak punya akses ke tenant X" | Key terikat ke tenant lain | Pakai key milik tenant tersebut, atau hapus parameter `tenant` (key `app` otomatis memakai tenant-nya) |
| `400 TENANT_REQUIRED` | Key admin/ops (tidak terikat tenant) memanggil `POST /v1/analyze` tanpa `?tenant=` | Tambahkan `?tenant=<code>`, atau pakai key role `app` yang terikat tenant |
| `404 TENANT_NOT_FOUND` | Kode tenant salah / tenant belum dibuat | Cek Web UI → Tenant. Kode tenant huruf kecil, contoh `courier-app` |
| `409 TENANT_DISABLED` | Tenant dinonaktifkan | Admin: Web UI → Tenant → Ubah → **Aktifkan** |
| `401` di `/v1/events/s3` "token event tidak valid" | Token bucket notification SILO tidak sama dengan `PQE_S3_EVENT_TOKEN` | Samakan `auth_token` di config notify webhook SILO dengan `PQE_S3_EVENT_TOKEN` |

## Foto tidak muncul / tidak diproses

| Gejala | Penyebab | Solusi |
|---|---|---|
| `404 OBJECT_NOT_FOUND` saat `POST /v1/analyze` (JSON `object_key`) | Key/bucket salah, atau upload ke SILO belum selesai saat engine dipanggil | Panggil engine **setelah** upload sukses. Cek key persis (case-sensitive, tanpa `/` di depan). Bucket default `AWS_BUCKET`; kirim `bucket` jika berbeda |
| Foto berstatus error `OBJECT_NOT_FOUND` | Objek dihapus/dipindah sebelum worker memprosesnya | Pastikan aplikasi tidak menghapus/mengganti nama file setelah upload |
| Foto baru di bucket tidak pernah muncul di engine | Tidak ada source tenant yang cocok: `bucket`/`prefix` salah, ekstensi tidak ada di `suffixes`, atau tenant nonaktif | Cek config tenant: `bucket` dan `prefix` harus cocok dengan awal key. Ekstensi default `.jpg .jpeg .png .heic .heif .webp`. Lihat Web UI → Sistem → Source ingest (kolom error & foto baru) |
| Foto muncul terlambat (beberapa detik sampai menit) | Bucket notification SILO belum aktif, jadi engine memakai polling `poll_interval_sec` | Normal untuk polling. Untuk real-time aktifkan event: lokal `./scripts/silo-enable-events.sh`, produksi lihat [operations.md](operations.md) |
| Event SILO terkirim tapi `/v1/events/s3` menjawab `ignored` | Bucket event tidak sama dengan `bucket` source (atau `AWS_BUCKET` jika `bucket` kosong), prefix tidak cocok, ekstensi tidak ada di `suffixes`, atau event bukan `s3:ObjectCreated:*` | Samakan `bucket`/`prefix` source dengan lokasi upload. Cek `suffixes` |
| Foto lama (lebih dari beberapa hari) tidak ikut diproses | `date_prefix_format` aktif, sehingga polling hanya melihat `lookback_days` hari terakhir (reconcile: minimal 7 hari) | Untuk backfill: naikkan `lookback_days` sementara lalu `pqe reconcile --tenant <code>`, atau pakai source `db_poll`, atau `pqe migrate-legacy` untuk foto di server aplikasi |
| Polling lambat / beban ke SILO tinggi | Tanpa `date_prefix_format`, seluruh prefix di-list setiap polling | Pakai key bertanggal + `date_prefix_format: "%Y/%m/%d/"`, atau naikkan `poll_interval_sec`, atau aktifkan bucket notification |
| Foto tercatat tapi status tetap `pending` | Tidak ada proses worker yang berjalan (misal hanya `pqe api`), atau worker kewalahan | Jalankan `pqe worker` (atau `pqe run`). Cek Web UI → Sistem: "Antre" yang terus naik = tambah `PQE_WORKER_CONCURRENCY` atau replika worker |
| Source `db_poll` error di Web UI → Sistem | Env `dsn_env` belum diisi, driver DB tidak ada, query tidak memakai `:checkpoint`, atau checkpoint bukan kolom datetime | Isi env DSN di engine. MySQL sudah didukung (`pymysql`); SQL Server butuh `pyodbc` + ODBC driver di image. Pakai `checkpoint_column` bertipe datetime |
| Resi/kurir/jenis foto kosong di hasil | Mapping tidak menemukan nilai | Cek nama metadata objek (`x-amz-meta-awb` → `meta:awb`), regex key, dan urutan aturan di `mapping` ([configuration.md §2.3](configuration.md#23-mapping-membaca-resi-kurir-jenis-foto)). Test regex dengan contoh key asli |
| Profile yang dipakai bukan yang diharapkan | `photo_type` kosong / tidak sama persis dengan nama profile (case-sensitive) → dipakai `default_profile` | Samakan nilai `photo_type` dengan nama profile, atau tambah aturan `default:<profile>` |

## Hasil analisis terasa salah

| Gejala | Penyebab | Solusi |
|---|---|---|
| Banyak foto bagus kena `BLURRY` / terlalu ketat | Threshold bawaan belum dikalibrasi dengan foto POD asli. Foto dengan latar bokeh/polos luas atau objek kecil juga bisa skor rendah | Kalibrasi: [configuration.md §6](configuration.md#6-kalibrasi-threshold). Turunkan `thresholds.sharpness` atau `params.sharpness.low/high` di profile terkait. Selama kalibrasi pakai mode `shadow` |
| Foto buram tetap `PASS` | Threshold terlalu longgar untuk jenis foto itu | Naikkan `thresholds.sharpness` untuk profile tersebut (contoh `pod_document`) |
| `EXIF_MISSING` di hampir semua foto | **Normal.** Banyak aplikasi/plugin kamera, kompresi, upload web, dan WhatsApp menghapus EXIF | Tidak perlu tindakan, ini hanya info dan tidak memengaruhi verdict. Untuk validasi waktu/lokasi, minta aplikasi mengirim `captured_at`, `lat`, `lng` |
| `DUPLICATE_SIMILAR` pada foto yang sebenarnya berbeda | Pemandangan sangat mirip (pintu/gudang yang sama, paket sejenis, sudut sama) menghasilkan pHash mirip | Ops cukup **Setujui** setelah membandingkan. Jika sering, turunkan `params.duplicate.max_distance` (contoh 4) untuk profile itu |
| `DUPLICATE_EXACT` | File identik byte-per-byte dipakai untuk resi lain | Hampir pasti foto dipakai ulang. Investigasi, lalu **Tandai kecurangan** bila terbukti |
| `OLD_PHOTO` muncul padahal foto baru | Jam HP kurir salah, atau foto diproses ulang lama setelah diterima | Cek jam HP. Waktu "diterima" diambil saat engine pertama mencatat foto |
| Terlalu banyak foto WARN karena indikasi keaslian | Bawaan `thresholds.authenticity.warn: 75`: satu temuan (≥ 30 poin) sudah WARN | Turunkan `thresholds.authenticity.warn` (contoh 65 atau 60) jika ingin lebih longgar |
| `OCR_UNAVAILABLE` | Tesseract tidak terpasang: image di-build dengan `--build-arg WITH_OCR=false`, atau engine dijalankan di luar Docker tanpa Tesseract | Pakai image standar (OCR aktif), atau install `tesseract-ocr tesseract-ocr-ind tesseract-ocr-eng` + `pip install pytesseract`. Cek: `python -c "from engine.analyzers.ocr import ocr_available; print(ocr_available())"` |
| `NO_TEXT_DETECTED` pada foto resi yang jelas | Resi terlalu kecil di frame, miring jauh, atau OCR memang tidak aktif untuk profile itu | Pastikan `photo_type` = `pod_document`. Minta kurir memotret resi lebih dekat. Sesuaikan `thresholds.ocr` jika perlu |
| `COMPRESSION_TARGET_NOT_MET` | Target ukuran tidak bisa dicapai tanpa melanggar pengaman SSIM (keterbacaan diutamakan) | Normal untuk foto sangat detail. Jika ukuran lebih penting, turunkan `compression.guard.min_ssim` atau `min_quality`, atau naikkan `target_max_bytes` |
| Threshold sudah diubah tapi foto lama tidak berubah | Perubahan config hanya berlaku untuk analisis berikutnya | Klik **Analisis ulang** di detail foto, atau `POST /v1/photos/<id>/reprocess` |
| Perubahan config belum terasa | Cache config ≤ 5 detik | Tunggu beberapa detik. Pastikan **Simpan** sukses dan versi config naik |

## Foto ulang, status pengiriman & koreksi opsional

| Gejala | Penyebab | Solusi |
|---|---|---|
| Foto gelap tidak diperbaiki (`enhancement.attempted: false`) | Sesuai desain: engine tidak memanipulasi gambar, koreksi pencahayaan nonaktif secara bawaan | Kurir foto ulang di tempat terang. Jika admin memang ingin koreksi untuk profile tertentu: `enhance.enabled: true` ([configuration.md](configuration.md#perbaikan-pencahayaan-otomatis-enhance)) |
| `allow_status_update: false` | Foto `REJECT` di mode `enforce`, atau ops memilih "Minta foto ulang"/"Tandai kecurangan" | Benar: aplikasi pengiriman jangan update status, tampilkan `courier_message`, minta foto ulang. Ops bisa **Setujui** jika foto sebenarnya layak |
| Status pengiriman tertahan padahal engine mati | Aplikasi tidak fail-open (menganggap tidak ada jawaban = ditolak) | Hanya `allow_status_update: false` yang menahan status. Error/timeout/`202` = simpan foto ke SILO sendiri (kode lama) dan update status seperti biasa. Pakai `checkFile`/`checkObject` di [clients/](../clients/README.md) yang sudah fail-open |
| `stored` bernilai `null` | Foto ditolak (`allow_status_update: false`), atau engine tidak menjawab dalam batas waktu (`decided: false` di client) | Ditolak: benar, foto tidak disimpan sebagai foto pengiriman; kirim balik `courier_message` ke kurir. Engine tidak menjawab: aplikasi menyimpan foto ke SILO sendiri (kode lama) lalu update status; engine memeriksa foto itu belakangan lewat bucket notification/polling |
| Link `stored.url` tidak bisa dibuka langsung di browser | Bucket SILO private; `url` adalah lokasi permanen tanpa tanda tangan | Simpan `url` (atau `bucket` + `key`) di database aplikasi, lalu buat presigned URL saat menampilkan, atau ambil lewat engine (`GET /v1/photos/{id}/file`) / evidence |
| `retake_required: true` tapi `allow_status_update: true` | Mode `shadow`/`warn`: kualitas dinilai `REJECT`, tapi kurir belum ditahan | Sesuai desain. Kurir hanya ditahan di mode `enforce`. Lihat [integration-guide.md §7](integration-guide.md#7-menampilkan-hasil-ke-kurir) |
| `courier_message` `null` padahal foto `REJECT` | Mode `shadow` (`action: log`): kurir tidak perlu melihat apa pun | Sesuai desain. Naikkan mode ke `warn`/`enforce` setelah kalibrasi |
| `enhancement.reason: no_improvement` / `verification_failed` / `nothing_to_fix` | Hanya jika koreksi opsional diaktifkan: kurva tidak membuat verdict membaik, verifikasi eksak menolak hasil, atau foto tidak butuh kurva | Foto dinilai apa adanya. Jika `REJECT`, kurir diminta foto ulang |
| Ops ragu apakah foto "diedit" | — | Bandingkan dengan **Buka original** di detail foto. Secara bawaan versi kompres hanya diperkecil/dikompres; `sha256` dan kode verifikasi merujuk ke original. Penjelasan untuk pelanggan/auditor: [integration-guide.md §7.1](integration-guide.md#71-optimasi-tanpa-manipulasi) |
| Foto `WARN` tidak muncul di evidence | Tenant memakai `review_required: true` | Ops **Setujui** foto tersebut, atau ubah tenant ke `review_required: false` (bawaan) |
| Foto `REJECT` muncul di evidence | Ops pernah **Setujui** foto itu | Sesuai desain. **Reset** atau **Minta foto ulang** untuk mengeluarkannya |

## Gambar, webhook, dan integrasi

| Gejala | Penyebab | Solusi |
|---|---|---|
| Thumbnail/foto tidak tampil di Web UI (gambar rusak) | Presigned URL memakai host internal yang tidak bisa dibuka browser (contoh `http://silo:9000`) | Web UI otomatis mencoba lagi lewat engine (`GET /v1/photos/{id}/file`), jadi biasanya tetap tampil. Agar link **Buka original** juga jalan, isi `AWS_PUBLIC_ENDPOINT` dengan host yang bisa dibuka browser (compose lokal: `http://localhost:9000`; dari HP: `http://<IP-PC>:9000`), lalu restart engine |
| Link foto "Request has expired" / `AccessDenied` | Presigned URL kedaluwarsa (UI 15 menit, pelanggan 7 hari) | Muat ulang halaman/detail untuk link baru. Ubah `ENGINE_PRESIGN_TTL_UI` jika perlu |
| Detail foto: "Tidak ada pratinjau" | Tenant `store_mode: none` (tanpa versi kompres) | Pakai link **Buka original**, atau ubah ke `store_mode: processed` |
| `POST /v1/analyze?sync=true` menjawab `202` dengan `poll_url` | Analisis belum selesai dalam `timeout_ms` (default 3000). Ini **fail-open** yang disengaja | Lanjutkan proses bisnis. Ambil hasil dari `GET /v1/photos/<id>` (poll), atau pakai webhook. Naikkan `timeout_ms` (maks 30000) jika memang perlu menunggu |
| `413 FILE_TOO_LARGE` | File lebih besar dari `PQE_MAX_UPLOAD_MB` (default 25 MB) | Naikkan env tersebut, atau kirim via referensi objek (upload ke SILO dulu) |
| `422 NO_INPUT` / `EMPTY_FILE` / `INVALID_BODY` | Body salah: tidak ada `file` (multipart) maupun JSON `object_key`, file kosong, atau JSON tidak valid | Lihat contoh di [integration-guide.md](integration-guide.md). Untuk JSON wajib header `Content-Type: application/json` |
| Webhook tidak diterima aplikasi | Tenant tidak punya `sinks`, event tidak ada di `events`, URL tidak bisa dijangkau dari server engine, atau tidak ada proses worker (webhook dikirim oleh worker) | Cek `sinks` di config tenant. Dari server engine, tes `curl -X POST <url>`. Pastikan `pqe worker`/`pqe run` berjalan. Lihat error terakhir di Web UI → Sistem → "Webhook gagal permanen" (atau `GET /v1/admin/webhooks?status=pending`). Pantau metrik `pqe_pending_webhooks` dan `pqe_webhooks_total{result="error"}` |
| Webhook ditolak aplikasi karena tanda tangan salah | Secret beda, verifikasi memakai body yang sudah di-parse/di-format ulang, atau jam server selisih > 5 menit | Pakai body **mentah**. Pastikan env `secret_env` berisi nilai yang sama di engine dan aplikasi. Sinkronkan jam (NTP). Pakai fungsi `verifyWebhook` di `clients/` |
| Webhook berhenti dikirim setelah aplikasi sempat down lama | Setelah 8 percobaan (jeda sampai 1 jam), pengiriman berstatus `dead` | Web UI → Sistem → "Webhook gagal permanen" → **Kirim ulang semua** (admin), atau `POST /v1/admin/webhooks/retry-dead`. Daftar: `GET /v1/admin/webhooks?status=dead` |
| Client TS/PHP selalu `unavailable: true` | Engine tidak bisa dijangkau dari aplikasi (DNS, firewall, URL salah) atau timeout terlalu kecil | Tes `curl <PQE_URL>/healthz` dari server aplikasi. Naikkan `timeoutMs`. Proses bisnis tetap jalan (fail-open) |
| Test client gagal "sudah di-patch" | Runtime Node/PHP belum memuat security release 21 Okt 2026 | Upgrade ke Node 22.23.4 / 24.22.1 / 26.12.1 atau PHP 8.2.35 / 8.3.36 / 8.4.27 / 8.5.12 (lihat [clients/README.md](../clients/README.md)) |

## Uji Coba Kamera (HP)

| Gejala | Penyebab | Solusi |
|---|---|---|
| HP tidak bisa membuka `http://<IP-PC>:8000/ui/coba.html` | HP beda jaringan, IP salah, atau firewall PC memblokir port 8000 | Sambungkan HP ke Wi-Fi yang sama, cek IP dengan `ip addr`/`ipconfig`, izinkan port 8000 (dan 8443 untuk HTTPS) di firewall |
| Tombol **Kamera langsung** tidak aktif | Browser hanya mengizinkan kamera di halaman HTTPS atau localhost | Pakai **📷 Ambil foto** (kamera bawaan HP, bisa lewat HTTP), atau jalankan `docker-compose.https.yml` dan buka `https://<IP-PC>:8443/ui/coba.html` ([getting-started.md §4b](getting-started.md#4b-uji-coba-dari-hp-tanpa-membangun-aplikasi)) |
| Peringatan "koneksi tidak aman / sertifikat tidak valid" di HTTPS uji coba | Sertifikat dibuat sendiri oleh Caddy (CA lokal) | Wajar untuk uji coba: pilih **Advanced → Proceed**. Untuk produksi pakai ingress dengan sertifikat resmi |
| "Kamera tidak bisa dibuka: Permission denied" | Izin kamera untuk situs ditolak | Buka pengaturan situs di browser HP, izinkan Kamera, muat ulang halaman |
| "Lokasi tidak tersedia" | Lokasi juga butuh HTTPS dan izin | Abaikan (dilanjutkan tanpa GPS) atau pakai HTTPS |
| Mode **Simulasi aplikasi kurir** tidak bisa dipilih | Role `viewer`, atau tenant belum dipilih | Pakai key `admin`/`ops`/`app` dan pilih tenant (bawaan `uji-coba`) |
| Tenant `uji-coba` tidak ada di daftar | Database dibuat sebelum file `config/tenants/uji-coba.yaml` ada dan import otomatis belum jalan | Restart engine (tenant baru di folder config di-import saat start) atau `pqe import-tenants` |
| Hasil "Engine belum selesai dalam 3 dtk" | Analisis lebih lama dari batas sync (foto besar, OCR, PC sibuk) | Ini perilaku fail-open yang benar; hasil menyusul di halaman yang sama. Jika sering, tambah `PQE_WORKER_CONCURRENCY` atau CPU |
| Foto dari **Kamera langsung** selalu "Metadata kamera tidak tersedia" | Foto dari browser tidak membawa EXIF | Wajar dan hanya informasi (tidak memengaruhi verdict). Pakai **📷 Ambil foto** untuk menguji EXIF |
| Foto uji ditandai `DUPLICATE_EXACT` / `DUPLICATE_SIMILAR` | File yang sama (atau versi gelap/terang dari foto yang sama) sudah pernah dikirim | Benar secara logika: pengiriman berbeda tidak boleh memakai foto yang sama. Ambil foto baru |

## Pengaturan SILO: test koneksi gagal

Pesan di bawah muncul di hasil **Test koneksi** (Web UI → Pengaturan SILO) atau di kolom error job/`/readyz`.

| Pesan / gejala | Penyebab | Solusi |
|---|---|---|
| `EndpointConnectionError: Could not connect to the endpoint URL: "http://127.0.0.1:9000/..."` | Endpoint `127.0.0.1`/`localhost` (misal dari JSON `mc` yang dibuat di laptop). Di dalam Docker/Kubernetes alamat itu adalah container engine sendiri | Ganti Endpoint dengan alamat SILO yang bisa dijangkau dari **server/container engine**: compose lokal `http://silo:9000`, SILO kantor `https://s3.pcpexpress.com`. Engine yang jalan langsung di PC (tanpa Docker) baru boleh memakai `127.0.0.1` |
| `EndpointConnectionError` / `ConnectTimeoutError` ke host lain | Host salah, port salah, firewall, atau DNS | Dari server engine: `curl -I <endpoint>/minio/health/live`. Cek `http` vs `https` dan port |
| `SSLError` / `CERTIFICATE_VERIFY_FAILED` | Endpoint `https` dengan sertifikat self-signed/CA internal | Pakai sertifikat valid di SILO, atau tambahkan CA internal ke image engine |
| `SignatureDoesNotMatch` | **Secret key salah** (salah salin, spasi ikut), atau signature tidak cocok | Salin ulang secret key, simpan lagi. Pastikan Signature `s3v4` |
| `InvalidAccessKeyId` | Access key salah / user service account dihapus | Cek user di SILO (`mcli admin user info <alias> <user>`) |
| `AccessDenied` di cek "Baca bucket aplikasi" | Policy service account tidak punya `s3:ListBucket`/`s3:GetObject` di bucket aplikasi | Tambahkan bucket aplikasi ke policy (contoh policy ditampilkan di halaman Pengaturan SILO dan di [operations.md §4.2](operations.md#42-service-account-engine-least-privilege)) |
| `AccessDenied` di cek "Tulis bucket hasil kompres" | Policy tidak punya `s3:PutObject`/`s3:DeleteObject` di bucket engine | Tambahkan izin tulis untuk bucket processed/thumb/upload |
| `NoSuchBucket` / `404` di salah satu cek | Nama bucket salah atau bucket belum dibuat | Perbaiki nama bucket di form, atau minta tim infra membuat bucket (lokal: `ENGINE_CREATE_BUCKETS=true`) |
| Error alamat/`NoSuchBucket` aneh padahal bucket ada | Gaya alamat salah: SILO/MinIO butuh `path` | Set **Gaya alamat** = `path` (JSON `mc`: `"path": "on"`) atau `auto` |
| `409 SECRET_KEY_MISSING` saat **Simpan** | `PQE_SECRET_KEY` belum diisi di server, jadi secret key tidak bisa disimpan terenkripsi | Isi `PQE_SECRET_KEY` (`openssl rand -hex 32`) di `.env`/K8s Secret lalu restart, atau isi `AWS_SECRET_ACCESS_KEY` lewat env. Field selain secret key tetap bisa disimpan |
| `422 INVALID_SETTINGS` | Endpoint tanpa `http://`/`https://`, gaya alamat bukan `auto/path/virtual`, atau signature bukan `s3v4/s3` | Perbaiki nilai sesuai pesan |
| Test berhasil tapi job masih gagal | Belum lewat 10 detik sejak simpan, atau job sudah dead sebelum diperbaiki | Tunggu ≤ 10 detik, lalu Web UI → Sistem → **Ulangi semua** |
| Secret key "hilang" setelah redeploy (job `SignatureDoesNotMatch`/`AccessDenied`) | `PQE_SECRET_KEY` berubah, secret lama tidak bisa dibuka sehingga engine memakai `AWS_SECRET_ACCESS_KEY` env | Kembalikan `PQE_SECRET_KEY` lama, atau isi ulang secret key di Pengaturan SILO |
| Foto tampil rusak di browser setelah endpoint diganti | Endpoint internal tidak bisa dibuka browser | Isi **Endpoint publik** dengan host yang bisa dibuka browser/pelanggan |

## Infrastruktur & deployment

| Gejala | Penyebab | Solusi |
|---|---|---|
| `no such service: #` atau `zsh: unknown file attribute` saat paste perintah di Mac | zsh tidak menganggap `#` sebagai komentar saat di-paste, dan membaca `( ... )` sebagai pola file | Paste perintah tanpa komentar/kurung, atau jalankan `setopt interactivecomments` dulu |
| `pqe demo-upload` gagal: "Ditolak: PQE_ENV=production. demo-upload menulis 12 foto sintetis ke bucket ..." | Pagar pengaman: di server (`PQE_ENV=production`, bawaan) perintah demo ditolak agar foto sintetis tidak masuk bucket produksi | Benar dan disengaja. Untuk uji lokal pakai `docker-compose.yml` (sudah `PQE_ENV=development`). Untuk smoke test di staging: `pqe demo-upload --bucket <bucket-staging> --force`. Jangan ke bucket produksi |
| `ForeignBucketWrite: engine tidak boleh menulis/menghapus di bucket '...' (bukan bucket engine)` | Pagar pengaman: engine hanya boleh menulis/menghapus di `ENGINE_BUCKET_PROCESSED`, `ENGINE_BUCKET_THUMB`, `ENGINE_BUCKET_UPLOAD`, dan `upload_bucket` di config tenant. Bucket aplikasi selalu read-only bagi engine, apa pun hak akses key-nya | Disengaja. Jika memang perlu memindahkan foto lama ke bucket aplikasi, pakai `pqe migrate-legacy --bucket ...` (tujuan dipilih eksplisit). Jika engine harus menyimpan upload ke bucket lain, isi `upload_bucket` di config tenant |
| `Cannot connect to the Docker daemon` | Docker Desktop belum berjalan | Buka Docker Desktop, tunggu sampai statusnya "running", ulangi perintah |
| Folder `ocr-engine/ocr-engine` (clone dobel) | `git clone` dijalankan lagi dari dalam folder hasil clone | `cd ..` ke folder induk, hapus folder dalam yang dobel, lanjut di folder `ocr-engine` pertama |
| Job **gagal permanen (dead)** di Web UI → Sistem | Error sementara berulang sampai `PQE_JOB_MAX_ATTEMPTS` (default 5), biasanya SILO/DB tidak bisa diakses | Baca kolom error terakhir, perbaiki penyebabnya (kredensial, jaringan, bucket), lalu **Ulangi** / **Ulangi semua** (admin). Via API: `POST /v1/admin/jobs/retry-dead` |
| Error `PROCESSING_FAILED` di foto | Foto yang job-nya dead | Sama seperti di atas. Setelah diulang dan sukses, status foto kembali `done` |
| `GET /readyz` → `503`, `storage: error` | Engine tidak bisa mengakses SILO: endpoint salah, kredensial salah, atau bucket `ENGINE_BUCKET_PROCESSED` belum ada | Cek nilai efektif di Web UI → **Pengaturan SILO** (nilai di sana menimpa `AWS_ENDPOINT`, `AWS_ACCESS_KEY_ID/SECRET`, dst.) lalu **Test koneksi**. Minta tim infra membuat bucket engine, atau `ENGINE_CREATE_BUCKETS=true` (lokal). Lihat [Pengaturan SILO: test koneksi gagal](#pengaturan-silo-test-koneksi-gagal) |
| `GET /readyz` → `database: error` | `DATABASE_URL` salah / Postgres mati | Cek koneksi. Format: `postgresql+psycopg://user:pass@host:5432/pqe` |
| `sqlite3.OperationalError: database is locked` | SQLite dipakai beberapa proses sekaligus (misal `pqe api` + `pqe worker` terpisah, atau beberapa replika) | SQLite hanya untuk uji **satu proses** (`pqe run`). Untuk lainnya wajib PostgreSQL |
| `docker compose up` / build gagal: `429 Too Many Requests` dari Docker Hub | Batas unduhan Docker Hub (IP bersama/kantor) | `docker login`, atau tarik dari mirror lalu beri tag lokal: `docker pull mirror.gcr.io/library/python:3.12-slim && docker tag mirror.gcr.io/library/python:3.12-slim python:3.12-slim` (sama untuk `postgres:16-alpine` dan `pgsty/silo:<tag>`) |
| Build gagal di `pip install`: `CERTIFICATE_VERIFY_FAILED` / `self-signed certificate in certificate chain` | Proxy/firewall kantor memakai CA sendiri | Buat Dockerfile turunan **lokal** (jangan commit CA): tambahkan `COPY corp-ca.crt /tmp/ca.crt` dan `ENV PIP_CERT=/tmp/ca.crt` sebelum baris `RUN pip install -r requirements.lock`, lalu `docker build -f Dockerfile.local .`. Alternatif: pakai PyPI mirror internal |
| Container engine restart terus | Biasanya `DATABASE_URL` tidak bisa dijangkau saat start atau env wajib salah | `docker compose logs engine` / `kubectl logs`. Perbaiki env, lalu restart |
| SILO lokal: bucket notification tidak jalan | Aturan event belum ditambahkan ke bucket | Jalankan `./scripts/silo-enable-events.sh` setelah bucket ada. Polling tetap jalan sebagai cadangan |
| Antrean menumpuk (`pqe_queue_lag_seconds` tinggi) | Worker kurang, CPU penuh, atau SILO lambat | Tambah `PQE_WORKER_CONCURRENCY` / replika worker (K8s: HPA). Cek error di log worker |

## Backup NAS

Halaman Web UI → **Backup NAS** menampilkan error terakhir; tombol **Test NAS** memeriksa setiap langkah. Detail mount dan
restore: [operations.md bagian 6](operations.md#6-postgresql--backup).

| Gejala | Penyebab | Solusi |
|---|---|---|
| "folder /backup tidak ada di dalam container" | Tujuan folder tetapi tidak ada folder yang dipasang ke `/backup` (compose lama, atau pod K8s tanpa volume) | Pakai compose terbaru (memasang `NAS_MOUNT` ke `/backup`), isi `NAS_MOUNT` di `.env`, `docker compose up -d`. K8s: tambahkan volume PVC/NFS di `/backup` pada pod worker dan API |
| "folder /backup tidak bisa ditulis engine (user uid 10001)" | Folder NAS milik user lain (contoh root atau user NAS) | Mount SMB dengan `uid=10001,gid=10001`, atau `sudo chown -R 10001 <folder NAS_MOUNT>`. Untuk NFS, izinkan uid 10001 menulis di export |
| Test NAS ✅ tetapi file tidak muncul di NAS | `NAS_MOUNT` masih bawaan `./backup` (folder lokal di server), bukan share NAS | Mount share NAS di server, arahkan `NAS_MOUNT` ke sana, `docker compose up -d`. File lama di `./backup` bisa disalin manual ke NAS |
| "Ruang kosong NAS" ❌ (< 1 GB) | NAS hampir penuh | Kosongkan/perbesar share. Backup tidak pernah menghapus foto di NAS, jadi kapasitas harus direncanakan (± ukuran bucket `photos-upload` + `photos-processed`) |
| "pg_dump tidak ada di image engine" | Image engine lama (sebelum `postgresql-client` ditambahkan) | Build ulang image (`docker compose up -d --build`) |
| "pg_dump gagal: ... server version mismatch" | Server PostgreSQL lebih baru dari pg_dump di image (pg_dump 17 mendukung server sampai 17) | Pakai Postgres 14–17, atau build image dengan `postgresql-client` yang lebih baru |
| NAS S3: `EndpointConnectionError`, `404`/`NoSuchBucket`, `SignatureDoesNotMatch`, `AccessDenied` | Endpoint tidak terjangkau dari container, bucket belum dibuat di NAS, secret salah, atau akun NAS tanpa izin tulis | Cek endpoint dari server (`curl <endpoint>`), buat bucket di NAS, isi ulang secret key (kosong = tidak diubah), beri akun izin tulis ke bucket. Jangan pakai `127.0.0.1` (itu container engine) |
| `409 SECRET_KEY_MISSING` saat menyimpan tujuan NAS S3 | `PQE_SECRET_KEY` belum diisi di server | Isi `PQE_SECRET_KEY` lalu restart. Tujuan folder tidak butuh secret |
| "Foto belum tersalin" terus bertambah | Jadwal mati, tidak ada proses worker (backup dijalankan worker; `pqe api` saja tidak menjalankan backup), atau setiap putaran gagal | Aktifkan jadwal, pastikan `pqe run`/`pqe worker` berjalan, perbaiki error terakhir, lalu **Backup foto sekarang**. Pantau `pqe_backup_pending` dan `pqe_backup_errors_total` |
| Tombol "Backup ... sekarang" tidak ada efek | Tidak ada worker yang berjalan, atau worker lain sedang menjalankan backup (lease maks. 15 menit) | Pastikan worker berjalan. Status "sedang berjalan" akan selesai sendiri; permintaan tetap menunggu di `state.requested` |
| Foto yang sudah dihapus dari SILO (retensi) masih ada di NAS | Disengaja: backup hanya menyalin, penghapusan di SILO tidak diterapkan ke NAS | Hapus manual di NAS sesuai kebijakan retensi legal jika diperlukan |

## Waktu & retensi

| Gejala | Penyebab | Solusi |
|---|---|---|
| Waktu di Web UI berbeda dengan waktu di database/log | DB dan log menyimpan **UTC**. Web UI menampilkan zona waktu komputer pengguna. PDF evidence memakai `timezone` tenant | Normal. Saat membandingkan, konversi ke zona yang sama |
| `OLD_PHOTO` / `CAPTURE_TIME_MISMATCH` meleset beberapa jam | Waktu EXIF / `captured_at` tanpa offset dianggap `timezone` tenant, padahal HP/aplikasi di zona lain | Kirim `captured_at` dengan offset (`2026-10-22T08:13:20+07:00`) atau epoch. Sesuaikan `timezone` tenant untuk wilayah WITA/WIT jika satu tenant per wilayah |
| Prefix tanggal (`date_prefix_format`) meleset sehari di sekitar tengah malam | Aplikasi membuat key memakai UTC, sedangkan engine memakai `timezone` tenant | Samakan zona waktu pembuatan key di aplikasi dengan `timezone` tenant. `lookback_days` ≥ 1 sudah menutup sebagian besar kasus |
| Hasil kompres/thumbnail hilang setelah 3 tahun | Retensi (`retention_days`) bekerja sesuai desain | Sesuai kebijakan. Naikkan `retention_days` jika kebijakan berubah (hanya berlaku untuk foto baru) |
| Foto original tidak terhapus setelah masa retensi | Engine **tidak pernah** menghapus foto original milik aplikasi | Atur lifecycle di bucket SILO, contoh `mcli ilm rule add --expire-days 1095 <alias>/<bucket>` |
| Foto lama terhapus lebih cepat/lambat dari yang diharapkan | `retention_days` tenant berbeda dengan kebutuhan | Ubah `retention_days`: `expires_at` semua foto tenant otomatis dihitung ulang dari waktu tercatat |

---

## Langkah diagnosis umum

1. **Kesehatan:** `curl <engine>/healthz` (proses hidup) dan `curl <engine>/readyz` (DB + SILO).
2. **Web UI → Sistem:** antrean, job dead, webhook dead, error per source.
3. **Log:**
   - compose: `docker compose logs -f engine`
   - K8s: `kubectl -n photo-quality logs deploy/pqe-worker` (juga `pqe-api`, `pqe-ingestor`)
   - Detail lebih banyak: set `PQE_LOG_LEVEL=DEBUG` lalu restart.
4. **Metrik** (`/metrics` di port API; untuk `pqe worker`/`pqe ingestor` di port `PQE_METRICS_PORT`, default 9100): `pqe_queue_depth`, `pqe_queue_lag_seconds`, `pqe_dead_jobs`, `pqe_pending_webhooks`, `pqe_source_errors_total`, `pqe_photos_failed_total`.
5. **Uji satu foto tanpa server:** `pqe analyze foto.jpg --profile pod_recipient` menunjukkan skor dan alasan persis seperti yang dihitung worker (tanpa cek duplikat).
6. **Cek config:** `pqe validate-tenant config/tenants/*.yaml`, atau Web UI → Tenant → Validasi → "Profile hasil gabungan".
7. **Audit log** (Web UI → Sistem, admin): siapa mengubah config/key/review/Pengaturan SILO terakhir kali.
