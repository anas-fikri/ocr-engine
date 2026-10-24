# Referensi Konfigurasi

Semua yang bisa diatur di Photo Quality Engine ada di tiga tempat:

1. **Environment variable**: koneksi (SILO, database), keamanan, dan perilaku worker. Berlaku untuk seluruh engine, diubah lewat `.env` / K8s ConfigMap+Secret, dan butuh restart.
2. **Koneksi SILO dan Backup NAS (Web UI, admin)**: koneksi baca ke SILO (endpoint, access/secret key, bucket aplikasi) yang menimpa environment variable SILO, serta tujuan dan jadwal backup NAS. Berlaku **tanpa restart**. Lihat [1.6](#16-koneksi-silo-lewat-web-ui) dan [1.7](#17-backup-nas-lewat-web-ui).
3. **Config tenant (YAML)**: aturan per aplikasi, yaitu sumber foto, mapping metadata, profile per jenis foto, threshold, koreksi pencahayaan opsional, kompresi, review, webhook, dan retensi. Diubah lewat Web UI atau API **tanpa restart**.

Daftar isi:

- [1. Environment variable](#1-environment-variable)
  - [1.6 Koneksi SILO lewat Web UI](#16-koneksi-silo-lewat-web-ui)
  - [1.7 Backup NAS lewat Web UI](#17-backup-nas-lewat-web-ui)
- [2. Config tenant (YAML)](#2-config-tenant-yaml)
  - [2.1 Field tingkat atas](#21-field-tingkat-atas)
  - [2.2 sources: dari mana foto diambil](#22-sources-dari-mana-foto-diambil)
  - [2.3 mapping: membaca resi, kurir, jenis foto](#23-mapping-membaca-resi-kurir-jenis-foto)
  - [2.4 sinks: webhook ke aplikasi](#24-sinks-webhook-ke-aplikasi)
  - [2.5 profiles: aturan per jenis foto](#25-profiles-aturan-per-jenis-foto)
- [3. Cara skor dihitung](#3-cara-skor-dihitung)
- [4. Kompresi & thumbnail](#4-kompresi--thumbnail)
- [5. Mode, verdict, dan action](#5-mode-verdict-dan-action)
- [6. Kalibrasi threshold](#6-kalibrasi-threshold)
- [7. Bagaimana perubahan config berlaku](#7-bagaimana-perubahan-config-berlaku)

---

## 1. Environment variable

Engine membaca environment variable, plus file `.env` di folder kerja kalau ada. Template lengkap ada di [`.env.example`](../.env.example). Nilai rahasia (key, password) **tidak boleh** di-commit: simpan di `.env` lokal atau K8s Secret.

### 1.1 SILO / S3

Nama variabel sengaja disamakan dengan aplikasi Laravel yang sudah ada. Semua variabel bertanda ⚙ bisa ditimpa
admin lewat Web UI → **Koneksi SILO** tanpa restart (lihat [1.6](#16-koneksi-silo-lewat-web-ui)).

Engine **hanya membaca** SILO dan tidak pernah menyimpan foto ([ADR 0006](adr/0006-engine-hanya-memproses-gambar.md));
penyimpanan dikerjakan aplikasi. Jadi yang wajib diisi hanya endpoint, key (izin baca), dan bucket aplikasi.

| Variabel | Default | Arti | Kapan diubah |
|---|---|---|---|
| `AWS_ENDPOINT` ⚙ | `https://s3.pcpexpress.com` | Endpoint S3 yang dipakai engine (dari dalam server/container) | Lokal pakai `http://silo:9000` (compose) atau `http://localhost:9000` |
| `AWS_PUBLIC_ENDPOINT` ⚙ | kosong (= `AWS_ENDPOINT`) | Host yang dipakai **browser/pelanggan** untuk membuka foto. Presigned URL ditandatangani dengan host ini | Jika host internal berbeda dengan host yang bisa dibuka browser, contoh compose: internal `http://silo:9000`, publik `http://localhost:9000` |
| `AWS_DEFAULT_REGION` ⚙ | `us-east-1` | Wajib ada untuk SDK S3. Tidak berpengaruh di SILO | Hampir tidak pernah |
| `AWS_USE_PATH_STYLE_ENDPOINT` | `true` | `true` = URL `endpoint/bucket/key` (wajib untuk SILO/MinIO) | Hanya jika pindah ke S3 yang memakai virtual-host |
| `AWS_ADDRESSING_STYLE` ⚙ | kosong | `auto` / `path` / `virtual`. Jika diisi, **menimpa** `AWS_USE_PATH_STYLE_ENDPOINT`. Sama dengan `path` di konfigurasi `mc` (`on` = `path`, `off` = `virtual`) | Biasanya dibiarkan kosong |
| `AWS_SIGNATURE_VERSION` ⚙ | `s3v4` | Versi tanda tangan request: `s3v4` (disarankan) atau `s3` (v2, server lama). Sama dengan `api` di konfigurasi `mc` | Hanya untuk server S3 lama yang belum mendukung v4 |
| `AWS_ACCESS_KEY_ID` ⚙ | kosong | Access key **service account engine** | Selalu diisi. Jangan pakai akun admin/root SILO |
| `AWS_SECRET_ACCESS_KEY` ⚙ | kosong | Secret key pasangannya | Selalu diisi |
| `AWS_BUCKET` ⚙ | `ekas-pcptransport` | Bucket foto milik aplikasi (engine hanya **membaca**: polling, `object_key`, dan foto yang tidak punya arsip). Default untuk `object_key` dari API/source yang tidak menyebut bucket | Sesuaikan dengan bucket aplikasi |
| `AWS_URL` | — | **Tidak dibaca engine.** Ada di `.env.example` hanya agar sama dengan config aplikasi Laravel | — |
| `ENGINE_BUCKET_ARCHIVE` ⚙ | `ocr` | Bucket **arsip engine** (baca + tulis + hapus). Setiap foto upload diarsipkan di `<tenant>/<yyyy>/<mm>/<dd>/<id>.<ext>`: foto diterima = sama dengan `output_image`, foto ditolak = foto asli (review & kalibrasi). Dihapus worker setelah `archive_retention_days` tenant (bawaan 520 hari). Satu-satunya bucket yang ditulis engine ([ADR 0007](adr/0007-arsip-engine-di-bucket-ocr.md)) | Biarkan `ocr`; tim infra membuat bucket ini. Tenant bisa memakai bucket lain lewat `archive_bucket` |
| `ENGINE_BUCKET_PROCESSED` / `ENGINE_BUCKET_THUMB` / `ENGINE_BUCKET_UPLOAD` | `photos-processed` / `photos-thumb` / `photos-upload` | **Data versi lama saja** (sebelum arsip `ocr`). Hanya dibaca untuk membuka/backup foto lama | Biarkan bawaan. Instalasi baru tidak perlu membuat bucket ini |
| `ENGINE_CREATE_BUCKETS` | `false` | `true` = saat start, engine membuat `AWS_BUCKET`, `ENGINE_BUCKET_ARCHIVE` (dan bucket versi lama) jika belum ada | `true` hanya untuk SILO lokal/uji coba (`docker-compose.yml`). Di produksi selalu `false` |
| `ENGINE_PRESIGN_TTL_UI` | `900` | Masa berlaku link foto (detik) untuk Web UI dan API `/v1/photos` | Perpanjang jika ops sering membuka tab lama |
| `ENGINE_PRESIGN_TTL_CUSTOMER` | `604800` (7 hari) | Masa berlaku link foto di `/v1/evidence/...` (untuk pelanggan) | Maksimal 7 hari (batas presigned S3 v4) |

### 1.2 Database

| Variabel | Default | Arti | Kapan diubah |
|---|---|---|---|
| `DATABASE_URL` | `sqlite:///./pqe.db` | Koneksi database engine | Produksi/compose **wajib PostgreSQL**: `postgresql+psycopg://user:pass@host:5432/pqe`. SQLite hanya untuk uji satu proses (lihat [troubleshooting](troubleshooting.md)) |

Skema tabel dibuat otomatis saat start (`pqe init` / `pqe run` / `pqe api` / `pqe worker`).

### 1.3 API & keamanan

| Variabel | Default | Arti | Kapan diubah |
|---|---|---|---|
| `PUBLIC_BASE_URL` | `http://localhost:8000` | URL publik engine. Dipakai untuk link verifikasi (`/v1/verify/<sha256>`) di PDF evidence | Isi dengan domain produksi, contoh `https://photo-quality.pcpexpress.com` |
| `PQE_BOOTSTRAP_ADMIN_KEY` | kosong | Jika diisi, API key admin dengan nilai ini dibuat otomatis saat start (sekali saja, idempotent) | Isi nilai acak panjang di produksi. Setelah membuat admin key lain lewat Web UI, nilai ini boleh dirotasi |
| `PQE_S3_EVENT_TOKEN` | kosong | Token yang wajib dikirim SILO di header `Authorization` saat bucket notification ke `/v1/events/s3`. Format `Bearer <token>` maupun `<token>` diterima | **Selalu isi di produksi.** Jika kosong, endpoint event menerima request tanpa token |
| `PQE_DEFAULT_LANG` | `id` | Bahasa pesan API jika request tidak mengirim `Accept-Language` atau `?lang=` | Jarang |
| `PQE_MAX_UPLOAD_MB` | `25` | Batas ukuran file di `POST /v1/analyze` (multipart). Lebih besar → `413 FILE_TOO_LARGE` | Jika ada kamera yang menghasilkan file sangat besar |
| `PQE_SYNC_TIMEOUT_MS` | `3000` | Batas tunggu default `POST /v1/analyze` dengan file (selalu dianalisis langsung) dan `?sync=true` untuk `object_key`. Lewat batas → respons `202`, `allow_status_update: true`, `output_image: null` (fail-open; aplikasi menyimpan foto asli), hasil tetap tercatat | Turunkan jika aplikasi butuh respons lebih cepat |
| `PQE_SECRET_KEY` | kosong | Kunci untuk mengenkripsi rahasia yang disimpan di database lewat Web UI (secret key SILO di halaman **Koneksi SILO**). Isi dengan nilai acak panjang, contoh `openssl rand -hex 32` | **Selalu isi di produksi** dan simpan di K8s Secret. Jika kosong, secret key SILO tidak bisa disimpan lewat Web UI (`409 SECRET_KEY_MISSING`). Jika **diganti**, secret yang sudah tersimpan tidak bisa dibaca lagi dan harus diisi ulang di Koneksi SILO |
| `PQE_CORS_ORIGINS` | kosong | Origin web (dipisah koma) yang boleh memanggil API langsung dari browser. Kosong = CORS mati | Isi hanya jika aplikasi web memanggil engine dari browser |
| `PQE_ENV` | `production` | `production` = pagar pengaman perintah: `pqe demo-upload` ditolak kecuali dengan `--force`. `development` = perintah demo diizinkan. Apa pun nilainya, engine **tidak pernah** menulis/menghapus di bucket aplikasi (read-only walau key punya akses penuh); engine hanya menulis ke bucket arsipnya (`ENGINE_BUCKET_ARCHIVE`) | `development` hanya untuk lokal (`docker-compose.yml` sudah mengaturnya). Biarkan `production` di server |

### 1.4 Worker & ingestor

| Variabel | Default | Arti | Kapan diubah |
|---|---|---|---|
| `PQE_TENANTS_DIR` | `config/tenants` | Folder YAML tenant untuk **seed awal** (lihat [bagian 7](#7-bagaimana-perubahan-config-berlaku)). Di image Docker: `/app/config/tenants` | Jika folder config dipindah |
| `PQE_WORKER_CONCURRENCY` | `2` | Jumlah thread analisis per proses worker | Naikkan jika antrean menumpuk dan CPU masih longgar |
| `PQE_WORKER_POLL_INTERVAL_SEC` | `1` | Jeda worker mengecek antrean saat kosong | Jarang |
| `PQE_JOB_MAX_ATTEMPTS` | `5` | Maksimal percobaan untuk error sementara (SILO/DB timeout). Jeda retry: 10 s, 20 s, 40 s, ... (maks 1 jam). Setelah habis → job **dead** | Jarang |
| `PQE_JOB_STALE_AFTER_SEC` | `600` | Job berstatus `running` lebih lama dari ini dianggap macet (worker mati) dan dikembalikan ke antrean | Naikkan jika ada foto yang memang butuh > 10 menit (tidak wajar) |
| `PQE_RECONCILE_INTERVAL_SEC` | `86400` | Seberapa sering setiap source di-*reconcile* (list ulang dengan lookback minimal 7 hari). Juga interval polling untuk source `s3_event` | Turunkan jika bucket notification belum stabil |
| `PQE_DUPLICATE_LOOKBACK_DAYS` | `90` | Rentang hari foto lama yang dibandingkan saat cek duplikat (per tenant) | Naikkan untuk deteksi reuse foto yang lebih lama (memori worker bertambah) |
| `PQE_METRICS_PORT` | `9100` | Port endpoint `/metrics` Prometheus untuk proses **tanpa API** (`pqe worker`, `pqe ingestor`). Proses `pqe api`/`pqe run` menyajikan `/metrics` di port API | Jika port 9100 bentrok |
| `PQE_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` | `DEBUG` saat investigasi |

### 1.5 Khusus docker-compose

Variabel ini hanya dibaca `docker-compose.yml`, tidak oleh engine:

| Variabel | Default | Arti |
|---|---|---|
| `SILO_ROOT_USER` / `SILO_ROOT_PASSWORD` | `siloadmin` / `siloadmin-local-only` | Akun root SILO lokal. Di compose lokal juga dipakai sebagai `AWS_ACCESS_KEY_ID/SECRET` engine |
| `POSTGRES_PASSWORD` | `pqe-local-only` | Password Postgres lokal |
| `NAS_MOUNT` | `./backup` | Folder di PC/server tempat share NAS di-mount (SMB/NFS). Dipasang ke `/backup` di container engine, dipakai tujuan **folder** di halaman Backup NAS. Harus bisa ditulis uid 10001 ([operations.md 6.3](operations.md#63-mount-nas-untuk-tujuan-folder)) |

Compose lokal juga mengisi `PQE_SECRET_KEY` dengan nilai contoh `local-secret-key-change-me` agar halaman Pengaturan
SILO langsung bisa dicoba, dan `PQE_ENV=development` agar `pqe demo-upload` bisa dijalankan. **Jangan** pakai nilai
itu di server.

Khusus `docker-compose.prod.yml` (server production, lihat [deploy-production.md](deploy-production.md)), diisi otomatis
oleh `scripts/prod-setup.sh`:

| Variabel | Default | Arti |
|---|---|---|
| `PQE_DOMAIN` | - (wajib) | Nama domain HTTPS engine, misal `photo-quality.pcpexpress.com` |
| `PQE_TLS` | `internal` | `internal` = sertifikat dari CA internal Caddy (intranet; pasang root CA di perangkat). Isi alamat email untuk sertifikat Let's Encrypt otomatis (domain publik, port 80/443 terbuka dari internet) |
| `POSTGRES_PASSWORD` | - (wajib) | Password Postgres production, acak. Harus diisi **sebelum** start pertama |
| `NAS_MOUNT` | `./backup` | Folder share NAS di server, dipasang ke `/backup` |
| `PQE_BUNDLED_POSTGRES` | `1` | `0` = container `postgres` tidak dijalankan; database di host server (diisi `scripts/host-postgres-setup.sh`) |
| `PQE_DATABASE_URL` | - | `DATABASE_URL` engine jika database bukan container bawaan, misal `postgresql+psycopg://pqe:...@host.docker.internal:5432/pqe` |

<a id="16-pengaturan-silo-lewat-web-ui"></a>

### 1.6 Koneksi SILO lewat Web UI

Admin bisa mengatur koneksi SILO dari Web UI → **Koneksi SILO** (dulu bernama "Pengaturan SILO"; atau API `GET/PUT /v1/admin/settings/storage`,
lihat [api-reference.md](api-reference.md#pengaturan-silo)), tanpa mengedit `.env` dan tanpa restart.

**Urutan prioritas per field:** nilai yang disimpan di halaman ini (database) > environment variable > bawaan.
Field yang tidak diisi di halaman ini tetap memakai env. Setiap field menampilkan asalnya ("dari halaman ini",
"dari env server", "bawaan"), dan field dari database punya link **kembalikan ke env**.

| Field di halaman / API | Menimpa env |
|---|---|
| Endpoint (`endpoint`) | `AWS_ENDPOINT` |
| Endpoint publik (`public_endpoint`) | `AWS_PUBLIC_ENDPOINT` |
| Region (`region`) | `AWS_DEFAULT_REGION` |
| Access key (`access_key`) | `AWS_ACCESS_KEY_ID` |
| Secret key (`secret_key`) | `AWS_SECRET_ACCESS_KEY` |
| Gaya alamat (`addressing_style`) | `AWS_ADDRESSING_STYLE` |
| Signature (`signature_version`) | `AWS_SIGNATURE_VERSION` |
| Bucket foto aplikasi (`app_bucket`) | `AWS_BUCKET` |
| Bucket arsip engine (`archive_bucket`) | `ENGINE_BUCKET_ARCHIVE` |
| Bucket versi lama (`processed_bucket`, `thumb_bucket`, `upload_bucket`, hanya lewat API, tidak tampil di form) | `ENGINE_BUCKET_PROCESSED` / `ENGINE_BUCKET_THUMB` / `ENGINE_BUCKET_UPLOAD` |

**Impor dari konfigurasi `mc` / MinIO client.** Tempel JSON seperti di bawah ke kotak "Impor dari konfigurasi mc",
klik **Isi form dari JSON**, lalu **Test koneksi** dan **Simpan**:

```json
{"url": "https://s3.pcpexpress.com", "accessKey": "<access key>", "secretKey": "<secret key>", "api": "s3v4", "path": "auto"}
```

| Kunci JSON `mc` | Menjadi | Konversi |
|---|---|---|
| `url` | Endpoint | Garis miring di akhir dibuang |
| `accessKey` / `secretKey` | Access key / Secret key | Apa adanya |
| `api` | Signature | `s3v2` / `s3` → `s3`; selain itu → `s3v4` |
| `path` | Gaya alamat | `on` → `path`; `off` / `dns` → `virtual`; selain itu (`auto`) → `auto` |

> **Awas `127.0.0.1` / `localhost`.** Endpoint dipakai oleh **server engine**, bukan browser Anda. Di dalam Docker
> atau Kubernetes, `127.0.0.1` berarti container engine itu sendiri, sehingga koneksi gagal (`EndpointConnectionError`).
> Ganti dengan nama service (compose lokal: `http://silo:9000`) atau host/IP server SILO (`https://s3.pcpexpress.com`).
> Halaman ini memberi peringatan jika JSON yang ditempel memakai `127.0.0.1`/`localhost`. Jika browser juga perlu
> host lain untuk membuka foto, isi **Endpoint publik**.

**Test koneksi** memakai nilai di form (sebelum disimpan; field kosong memakai nilai efektif saat ini) dan menjalankan
tiga cek: (1) "Baca bucket aplikasi" (list), (2) "Baca isi foto" (membaca metadata satu foto contoh), (3) "Tulis bucket
arsip 'ocr'" (menulis lalu menghapus file uji kecil). Bucket aplikasi tidak pernah ditulis. Setiap cek menampilkan ✅/❌ beserta pesan error asli dari SILO (lihat
[troubleshooting](troubleshooting.md#koneksi-silo-test-koneksi-gagal)).

Aturan penyimpanan:

- Field yang dikosongkan di form **tidak diubah**. Untuk kembali ke env, pakai link **kembalikan ke env**
  (API: `{"clear": ["endpoint"]}`).
- **Secret key disimpan terenkripsi** dengan `PQE_SECRET_KEY` dan tidak pernah ditampilkan lagi (hanya status
  "tersimpan"). Access key ditampilkan tersamar. Tanpa `PQE_SECRET_KEY`, field lain tetap bisa disimpan tetapi secret
  key tidak (`409 SECRET_KEY_MISSING`); isi `PQE_SECRET_KEY` lalu restart, atau isi `AWS_SECRET_ACCESS_KEY` lewat env.
- Perubahan berlaku di **semua proses** (API, worker, ingestor) **paling lambat 10 detik**, tanpa restart.
- Setiap simpan menaikkan versi dan tercatat di audit log (`settings.storage.update`, berisi nama field yang diubah,
  **tanpa** nilai rahasia).
- Tetap pakai **service account khusus engine** dengan izin baca saja (`s3:GetObject`, `s3:ListBucket`; halaman ini
  menampilkan contoh policy-nya), bukan akun admin/root SILO. Lihat [operations.md](operations.md#42-service-account-engine-least-privilege).
  Sebagai lapisan tambahan, engine sendiri menolak menulis/menghapus di bucket aplikasi (lihat `PQE_ENV` di 1.3).

### 1.7 Backup NAS lewat Web UI

Menu **Backup NAS** (role `admin`) mengatur salinan terjadwal database dan foto (yang disimpan aplikasi di SILO) ke NAS. Nilainya tersimpan di database
(bukan env) dan dibaca worker setiap putaran (±10 detik), jadi berlaku tanpa restart.

| Field | Bawaan | Arti |
|---|---|---|
| `enabled` | `false` | Jadwal aktif. Jika mati, backup tetap bisa dijalankan manual |
| `target` | `folder` | `folder` (NAS di-mount ke container) atau `s3` (NAS S3-compatible) |
| `folder_path` | `/backup` | Path absolut di dalam container (= `NAS_MOUNT` di compose) |
| `s3_endpoint`, `s3_bucket`, `s3_prefix`, `s3_region`, `s3_access_key`, `s3_secret_key`, `s3_addressing_style` | `us-east-1`, `path` | Tujuan NAS S3. Secret disimpan terenkripsi dengan `PQE_SECRET_KEY` (tanpa itu: `409 SECRET_KEY_MISSING`) |
| `include_database` | `true` | Dump database engine |
| `include_photos` | `true` | Satu salinan per foto: arsip engine (bucket `ocr`) bila ada, selain itu foto di bucket aplikasi (foto polling / `object_key`). Foto tanpa arsip dan tanpa lokasi aplikasi tidak ikut |
| `include_upload_originals` / `include_processed` / `include_thumbs` | `true` / `true` / `false` | **Data versi lama**: salinan di bucket `photos-upload`/`photos-processed`/`photos-thumb` dari versi sebelum arsip `ocr`. Di Web UI ada di bagian "Data versi lama" |
| `photo_interval_minutes` | `15` | Jeda backup foto (1–1440) |
| `db_interval_hours` | `24` | Jeda dump database (1–720) |
| `db_keep` | `14` | Jumlah dump database terakhir yang disimpan di NAS (1–365) |

Setiap simpan tercatat di audit log (`settings.backup.update`), permintaan manual sebagai `backup.run_requested`.
Detail cara kerja, mount NAS, dan restore: [operations.md bagian 6](operations.md#6-postgresql--backup).

---

## 2. Config tenant (YAML)

**Tenant** = satu aplikasi yang terhubung ke engine (contoh: aplikasi kurir). Setiap tenant punya satu dokumen YAML. Contoh lengkap: [`config/tenants/courier-app.yaml`](../config/tenants/courier-app.yaml).
Tenant [`uji-coba`](../config/tenants/uji-coba.yaml) dipakai halaman Uji Coba Kamera (`/ui/coba.html`): tanpa sumber
bucket, mode `enforce` untuk semua jenis foto (lewat `profiles.default`), retensi 30 hari.

Field yang tidak dikenal akan **ditolak** saat validasi, supaya salah ketik langsung ketahuan.

### 2.1 Field tingkat atas

| Field | Wajib | Default | Arti |
|---|---|---|---|
| `code` | ya | — | ID tenant. Huruf kecil, angka, `-`; 2-63 karakter; diawali huruf/angka. Contoh `courier-app`. **Jangan diganti** setelah dipakai (dipakai di data foto dan nama file varian) |
| `name` | ya | — | Nama tampilan di Web UI |
| `default_lang` | | `id` | `id` / `en`. Bahasa pesan di **payload webhook** tenant ini. (Respons API mengikuti `Accept-Language` / `?lang=` / `PQE_DEFAULT_LANG`, bukan field ini) |
| `timezone` | | `Asia/Jakarta` | Zona waktu untuk: waktu EXIF dan `captured_at` tanpa offset, tanggal pada `date_prefix_format`, checkpoint `db_poll`, dan tampilan waktu di PDF evidence |
| `retention_days` | | `1095` (3 tahun) | Lihat [Retensi](#retensi) |
| `archive_bucket` | | kosong | Bucket arsip khusus tenant ini. Kosong = `ENGINE_BUCKET_ARCHIVE` (bawaan `ocr`), dipisah per tenant lewat prefix `<code>/`. Bucket ini harus bisa ditulis key engine |
| `archive_retention_days` | | `520` | Masa simpan file arsip engine (1–3650 hari, dihitung dari waktu diterima). Setelah itu worker menghapus file arsipnya (audit `archive.purge`); catatan analisis tetap sampai `retention_days`. Lihat [Retensi](#retensi) |
| `store_mode`, `upload_bucket` | | — | **Tidak dipakai lagi** ([ADR 0006](adr/0006-engine-hanya-memproses-gambar.md); arsip sekarang lewat `archive_bucket`). Masih diterima agar config lama tetap valid |
| `default_profile` | | `default` | Profile yang dipakai jika `photo_type` foto kosong atau tidak dikenal |
| `review_required` | | `false` | Apakah foto `WARN` harus di-*approve* ops dulu sebelum masuk evidence pelanggan. `false` = `PASS` dan `WARN` langsung masuk evidence, review ops **opsional** (antrean Review tetap bisa dipakai untuk memantau). `true` = hanya `PASS` atau foto yang di-*approve* ops. `REJECT` tidak pernah masuk tanpa approve. Lihat [bagian 5](#5-mode-verdict-dan-action) |
| `sources` | | `[]` | Dari mana foto diambil. Kosong = tenant hanya menerima foto lewat API |
| `mapping` | | `{}` | Aturan membaca metadata. Lihat [2.3](#23-mapping-membaca-resi-kurir-jenis-foto) |
| `sinks` | | `[]` | Webhook ke aplikasi. Lihat [2.4](#24-sinks-webhook-ke-aplikasi) |
| `profiles` | | `{}` | Override profile. Lihat [2.5](#25-profiles-aturan-per-jenis-foto) |

#### Retensi

- Saat foto pertama kali tercatat, engine mengisi `expires_at = waktu tercatat + retention_days`. Saat `retention_days` diubah (Web UI / API / `pqe import-tenants --force`), `expires_at` **semua foto tenant itu dihitung ulang** dari waktu tercatatnya.
- **Arsip engine** (bucket `ocr`) punya masa simpan sendiri: `archive_retention_days` (bawaan 520 hari). Setelah lewat, worker menghapus file arsipnya dan mengosongkan `archive` di catatan foto; hasil analisis tetap ada. Dicatat di audit log (`archive.purge`) dan metrik `pqe_archive_total{result="purged"}`.
- Setelah lewat `expires_at`, worker menghapus **milik engine saja**: baris foto (metadata hasil analisis), job, riwayat review terkait, dan objek di bucket engine versi lama jika ada. Penghapusan dicatat di audit log (`retention.purge`). Catatan pengiriman webhook yang sudah selesai/dead dihapus otomatis setelah 30 hari.
- **Foto di bucket aplikasi tidak pernah dihapus engine.** Atur lifecycle di SILO, contoh 3 tahun:
  ```bash
  mcli ilm rule add --expire-days 1095 <alias>/ekas-pcptransport
  ```

### 2.2 sources: dari mana foto diambil

Satu tenant boleh punya beberapa source. Semua jalur berujung ke antrean yang **idempotent** (foto yang sama tidak diproses dua kali), jadi polling, event, dan reconcile boleh tumpang tindih.
Semua source hanya **membaca** SILO/database aplikasi; foto dari source tidak diarsipkan ulang ke bucket `ocr`.

> Jika aplikasi sudah memanggil `POST /v1/analyze` (`checkFile`) untuk foto di prefix yang sama, kirim `object_key` di
> request itu supaya catatan dari polling digabung ke catatan upload. Tanpa `object_key`, satu foto bisa tercatat dua
> kali (upload + polling); matikan source polling untuk prefix itu setelah integrasi berjalan.

Field umum:

| Field | Default | Dipakai oleh | Arti |
|---|---|---|---|
| `type` | — | semua | `s3_poll`, `s3_event`, atau `db_poll` |
| `bucket` | `AWS_BUCKET` | s3_poll, s3_event, db_poll | Bucket yang dipantau. **Selalu tulis eksplisit** (lihat catatan event di bawah) |
| `prefix` | `""` | s3_poll, s3_event | Hanya key yang diawali prefix ini. Contoh `courier-app/` |
| `date_prefix_format` | kosong | s3_poll, s3_event | Format `strftime` bagian tanggal di key, contoh `"%Y/%m/%d/"`. Jika diisi, engine hanya me-list `prefix + tanggal` untuk `lookback_days` hari terakhir. **Sangat disarankan** untuk bucket besar |
| `lookback_days` | `2` | semua | s3: berapa hari ke belakang prefix tanggal di-list. db_poll: rentang awal saat checkpoint belum ada |
| `poll_interval_sec` | `15` | s3_poll, db_poll | Interval polling |
| `suffixes` | `.jpg .jpeg .png .heic .heif .webp` | s3_poll, s3_event | Ekstensi file yang diproses (tidak case-sensitive). File lain diabaikan |
| `dsn_env` | — | db_poll | **Nama** environment variable yang berisi DSN database aplikasi (bukan DSN-nya) |
| `query` | — | db_poll | SQL yang wajib memakai parameter `:checkpoint` |
| `url_column` | `photo_url` | db_poll | Kolom berisi link foto atau object key |
| `checkpoint_column` | `created_at` | db_poll | Kolom yang dipakai sebagai checkpoint (harus **datetime**, lihat catatan) |

#### `s3_poll`

Engine me-list bucket setiap `poll_interval_sec`. Objek yang belum dikenal (atau ETag-nya berubah karena ditimpa) masuk antrean.

```yaml
sources:
  - type: s3_poll
    bucket: ekas-pcptransport
    prefix: "courier-app/"
    date_prefix_format: "%Y/%m/%d/"   # key: courier-app/2026/10/22/AWB123.jpg
    lookback_days: 2
    poll_interval_sec: 15
```

- Tanpa `date_prefix_format`, **seluruh prefix di-list setiap polling**. Ini oke untuk ribuan objek, tapi berat untuk jutaan.
- Dengan `date_prefix_format`, foto yang lebih tua dari `lookback_days` tidak dilirik polling biasa. Reconcile (saat start dan setiap `PQE_RECONCILE_INTERVAL_SEC`) memakai lookback `max(lookback_days, 7)`. Untuk backfill lebih lama, naikkan `lookback_days` sementara lalu jalankan `pqe reconcile --tenant <code>`.

#### `s3_event`

Jalur utama = bucket notification SILO ke `POST /v1/events/s3` (real-time). Polling untuk source ini hanya berjalan sebagai jaring pengaman setiap `PQE_RECONCILE_INTERVAL_SEC` (default sehari sekali).

```yaml
sources:
  - type: s3_event
    bucket: ekas-pcptransport      # event dicocokkan dengan bucket ini (kosong = AWS_BUCKET)
    prefix: "courier-app/"
```

- Event dicocokkan ke tenant berdasarkan `bucket` + `prefix` (prefix terpanjang menang). Event juga diterima untuk source `s3_poll` dengan bucket/prefix yang cocok, jadi `s3_poll` + event bisa dipakai bersamaan.
- Jika `bucket` dikosongkan, event **tidak akan pernah cocok**. Selalu tulis nama bucket.
- Cara mengaktifkan notification di SILO lokal: `./scripts/silo-enable-events.sh`.

#### `db_poll`

Membaca tabel link foto di DB aplikasi (read-only, sebaiknya replica). Berguna untuk foto lama, atau jika metadata (resi, kurir) hanya ada di DB.

Aturan query:

- Wajib memakai parameter `:checkpoint` (nilai checkpoint terakhir). Saat pertama jalan, nilainya `sekarang - lookback_days` dalam `timezone` tenant, tanpa zona waktu.
- Kolom `url_column` berisi **link penuh** (`https://s3.pcpexpress.com/<bucket>/<key>`, host harus sama dengan `AWS_ENDPOINT` atau `AWS_PUBLIC_ENDPOINT`, path-style maupun virtual-host) **atau** object key saja (dibaca dari `bucket` source / `AWS_BUCKET`).
- Kolom lain yang namanya sama dengan field metadata (`awb`, `courier_id`, `photo_type`, `customer_id`, `branch`, `captured_at`, `lat`, `lng`, `source`, `external_id`) otomatis dipakai dan **mengalahkan** aturan mapping. Pakai alias SQL.
- Checkpoint kolom **waktu**: pakai `>=` (bukan `>`). Baris yang terbaca ulang aman karena antrean idempotent, sedangkan `>` bisa melewatkan baris dengan timestamp yang sama.
- Checkpoint kolom **ID angka** (auto-increment): pakai `>` dan `ORDER BY id`. Bisa dikombinasikan dengan `LIMIT` untuk membatasi beban per polling.

Contoh MySQL / MariaDB (driver `pymysql` sudah ada di image):

```yaml
sources:
  - type: db_poll
    dsn_env: COURIER_DB_DSN          # env: mysql+pymysql://readonly:***@db-replica:3306/courier
    poll_interval_sec: 30
    lookback_days: 3
    url_column: photo_url
    checkpoint_column: created_at
    query: >
      SELECT p.photo_url,
             p.created_at,
             d.awb_number    AS awb,
             d.courier_code  AS courier_id,
             p.category      AS photo_type,
             d.customer_code AS customer_id,
             d.branch_code   AS branch
      FROM delivery_photos p
      JOIN deliveries d ON d.id = p.delivery_id
      WHERE p.created_at >= :checkpoint
      ORDER BY p.created_at
      LIMIT 2000
```

Contoh SQL Server:

```yaml
sources:
  - type: db_poll
    dsn_env: COURIER_MSSQL_DSN       # env: mssql+pyodbc://readonly:***@sql-host/courier?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
    query: >
      SELECT TOP 2000 p.PhotoUrl AS photo_url,
             p.CreatedAt          AS created_at,
             d.AwbNo              AS awb,
             d.CourierCode        AS courier_id
      FROM dbo.DeliveryPhotos p
      JOIN dbo.Deliveries d ON d.Id = p.DeliveryId
      WHERE p.CreatedAt >= :checkpoint
      ORDER BY p.CreatedAt
```

> SQL Server butuh driver yang **belum ada** di image standar: paket Python `pyodbc` plus Microsoft ODBC Driver 18 (`msodbcsql18`) di OS. Tambahkan keduanya di Dockerfile turunan sebelum memakai `mssql+pyodbc`.

> **Catatan checkpoint:** engine menyimpan nilai terbesar `checkpoint_column` (waktu sebagai teks ISO-8601, ID sebagai angka) di tabel `source_state`, dan membandingkan ID angka secara numerik. Kolom waktu maupun ID auto-increment sama-sama didukung. Contoh dengan ID:
>
> ```yaml
>     query: "SELECT id, photo_url, awb FROM delivery_photos WHERE id > :checkpoint ORDER BY id LIMIT 500"
>     checkpoint_column: id
> ```
>
> Pertama kali jalan tanpa checkpoint, `:checkpoint` berisi waktu `sekarang - lookback_days`. Untuk kolom ID, isi checkpoint awal dengan membuat baris di `source_state` (id `<tenant>:<index>:db_poll`, checkpoint `0` atau ID terakhir yang ingin dilewati).

### 2.3 mapping: membaca resi, kurir, jenis foto

Mapping menentukan bagaimana engine mendapatkan metadata setiap foto. Field yang dikenali:

| Field | Arti |
|---|---|
| `awb` | Nomor resi (dipakai untuk pencarian, evidence, cek duplikat lintas resi) |
| `photo_type` | Jenis foto = **nama profile** (contoh `pod_recipient`, `pod_document`) |
| `courier_id` | ID kurir |
| `customer_id` | ID pelanggan (isolasi evidence untuk API key role `customer`) |
| `branch` | Kode cabang |
| `captured_at` | Waktu foto diambil (ISO-8601 atau epoch detik/milidetik). Tanpa offset → dianggap `timezone` tenant |
| `lat`, `lng` | Lokasi pengiriman (dibandingkan dengan GPS EXIF) |
| `source` | `camera` / `gallery` |
| `external_id` | ID foto di aplikasi Anda (untuk korelasi) |

**Urutan prioritas:**

1. **Field eksplisit dari pemanggil selalu menang**, yaitu field di body `POST /v1/analyze` atau kolom hasil query `db_poll` dengan nama field yang sama.
2. Jika tidak ada, aturan di `mapping.<field>` dicoba dari kiri ke kanan. Nilai pertama yang tidak kosong dipakai.
3. Jika tenant tidak menulis aturan untuk sebuah field, dipakai aturan bawaan `["meta:<nama-pakai-strip>", "meta:<nama_pakai_underscore>"]`. Contoh untuk `courier_id`: `meta:courier-id`, lalu `meta:courier_id`.

**Sintaks aturan:**

| Aturan | Arti | Contoh |
|---|---|---|
| `meta:<nama>` | Metadata objek S3 `x-amz-meta-<nama>` (tidak case-sensitive, nilai otomatis di-URL-decode) | `meta:awb` |
| `field:<nama>` | Field dari body API / kolom DB dengan nama **lain** | `field:resi` |
| `key:regex:<pola>` | Regex pada object key. Pakai named group sesuai nama field, atau group pertama, atau seluruh match | `key:regex:(?P<awb>[A-Z0-9]{8,20})_` |
| `key:segment:<n>` | Potongan ke-n dari key dipisah `/`. Mulai 0, negatif dari belakang | `key:segment:1`, `key:segment:-2` |
| `key:basename` | Nama file tanpa ekstensi | `key:basename` |
| `default:<nilai>` | Nilai tetap | `default:pod_recipient` |

Contoh untuk key `courier-app/pod_document/2026/10/22/AWB123456_K045_1729580000.jpg`:

```yaml
mapping:
  awb:         ["meta:awb", "key:regex:(?P<awb>AWB\\d+)_"]          # -> AWB123456
  photo_type:  ["meta:photo-type", "key:segment:1", "default:pod_recipient"]  # -> pod_document
  courier_id:  ["meta:courier-id", "key:regex:_(?P<courier_id>K\\d+)_"]       # -> K045
  external_id: ["key:basename"]                                     # -> AWB123456_K045_1729580000
  branch:      ["meta:branch", "default:JKT01"]
```

Tips:

- Di YAML, backslash dalam string berkutip ganda harus ditulis dua kali (`"\\d+"`). Dalam kutip tunggal cukup satu (`'\d+'`).
- Regex yang tidak valid, `key:segment:` tanpa angka, atau jenis aturan yang salah ketik **ditolak saat menyimpan** config.
- Metadata objek adalah sumber paling akurat. Minta aplikasi mengirim `x-amz-meta-awb`, `x-amz-meta-photo-type`, dst. saat upload (lihat [integration-guide.md](integration-guide.md)).

### 2.4 sinks: webhook ke aplikasi

| Field | Default | Arti |
|---|---|---|
| `type` | `webhook` | Satu-satunya jenis saat ini |
| `url` | — | Endpoint aplikasi Anda. Harus bisa dijangkau **dari server engine** |
| `secret_env` | — | **Nama** environment variable yang berisi secret HMAC (disarankan) |
| `secret` | — | Secret ditulis langsung di config (hanya untuk lokal/uji; tersimpan di DB & riwayat config) |
| `events` | `[analysis.completed]` | `analysis.completed` (analisis selesai/gagal permanen) dan/atau `review.updated` (keputusan ops) |
| `timeout_sec` | `5.0` | Batas waktu satu pengiriman |

```yaml
sinks:
  - type: webhook
    url: https://courier-api.internal/hooks/photo-quality
    secret_env: COURIER_WEBHOOK_SECRET
    events: [analysis.completed, review.updated]
```

Perilaku pengiriman:

- Dikirim oleh **proses worker** (`pqe worker` atau `pqe run`), bukan `pqe api`.
- Respons HTTP 2xx = sukses. Selain itu di-retry dengan jeda `15 × 2^percobaan` detik (maks 1 jam), sampai 8 percobaan, lalu status `dead`. Webhook dead terlihat di Web UI → Sistem → "Webhook gagal permanen" dan bisa dikirim ulang (admin), atau lewat `GET /v1/admin/webhooks?status=dead` dan `POST /v1/admin/webhooks/retry-dead`.
- Header: `X-PQE-Event`, `X-PQE-Delivery`, `X-PQE-Timestamp`, dan jika secret ada `X-PQE-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<body>")>`. Contoh verifikasi ada di [integration-guide.md](integration-guide.md) dan di `clients/`.
- Body: `{"event": "...", "photo": {...}}`. Objek `photo` sama dengan respons `GET /v1/photos` tanpa `urls`, dengan pesan dalam bahasa `default_lang` tenant.

### 2.5 profiles: aturan per jenis foto

Profile = kumpulan aturan pemeriksaan untuk satu jenis foto. Engine punya profile bawaan ([`engine/defaults/profiles.yaml`](../engine/defaults/profiles.yaml)): `default`, `pod_recipient`, `pod_document`, `package`, `goods`, `location`. Tenant cukup menulis **bagian yang ingin diubah**.

#### Memilih profile untuk sebuah foto

`photo_type` hasil mapping dipakai sebagai nama profile jika namanya ada di profile bawaan atau di `profiles` tenant (case-sensitive). Jika tidak ada, dipakai `default_profile` tenant.

#### Urutan penggabungan (deep merge, yang belakang menimpa yang depan)

Untuk profile bernama `X`:

1. `default` bawaan engine
2. `profiles.default` tenant (dilewati jika `X` adalah `default`)
3. Jika `profiles.X.extends: P`: profile bawaan `P`, lalu `profiles.P` tenant (`extends` juga boleh dipakai di `profiles.default`)
4. Profile bawaan bernama `X` (jika ada)
5. `profiles.X` tenant

Contoh:

```yaml
default_profile: pod_recipient
profiles:
  default:                  # berlaku untuk SEMUA profile tenant ini
    mode: warn
  pod_document:
    mode: enforce
    thresholds:
      ocr: { reject: 50, warn: 70 }
  pod_cod:                  # profile baru khusus COD, mewarisi pod_document
    extends: pod_document
    thresholds:
      sharpness: { reject: 35, warn: 60 }
```

> Karena profile bawaan bernama sama dipasang setelah `profiles.default` tenant (langkah 4), nilai yang memang diatur profile bawaan (contoh threshold `sharpness` di `pod_document`) tidak tertimpa oleh `profiles.default` tenant. Untuk mengubahnya, tulis di `profiles.pod_document`.

Cek hasil akhir: Web UI → Tenant → Ubah → **Validasi** → "Profile hasil gabungan", atau `GET /v1/admin/tenants/<code>` (`resolved_profiles`).

#### Field profile

| Field | Default (`default` bawaan) | Arti |
|---|---|---|
| `mode` | `shadow` | `shadow` / `warn` / `enforce`. Lihat [bagian 5](#5-mode-verdict-dan-action) |
| `checks` | `[sharpness, resolution, exposure, exif, duplicate, ocr]` | Pemeriksaan yang dijalankan. Pilihan: `sharpness`, `resolution`, `exposure`, `exif`, `duplicate`, `ocr`. `ocr` (teks terbaca) aktif di semua profile bawaan; hapus dari `checks` tenant hanya untuk jenis foto yang memang tanpa teks |
| `thresholds` | lihat tabel di bawah | Batas skor per pemeriksaan |
| `params` | lihat [bagian 3](#3-cara-skor-dihitung) | Parameter perhitungan |
| `enhance` | `{enabled: false}` | Koreksi pencahayaan opsional tanpa AI. Bawaan nonaktif: engine tidak memanipulasi gambar. Lihat [di bawah](#perbaikan-pencahayaan-otomatis-enhance) |
| `compression` | lihat [bagian 4](#4-kompresi--thumbnail) | Kompresi |
| `thumbnail` | `{long_side_px: 320, quality: 70}` | Thumbnail |

`checks` menimpa daftar sepenuhnya (bukan digabung). Jika mengubahnya, tulis lengkap.

#### Threshold

Semua skor bernilai **0-100, makin tinggi makin baik**.

- skor `< reject` → **REJECT**
- skor `< warn` → **WARN**
- `reject: null` = tidak pernah REJECT untuk pemeriksaan itu

Verdict foto = yang terburuk dari semua pemeriksaan. Threshold untuk pemeriksaan yang tidak ada di `checks` diabaikan.

Nilai bawaan:

| Profile | sharpness | resolution | exposure | ocr | authenticity |
|---|---|---|---|---|---|
| `default` | 25 / 50 | 30 / 55 | 15 / 45 | 40 / 65 | – / 75 |
| `pod_recipient` | 25 / 50 | (default) | (default) | (default) | (default) |
| `pod_document` | 30 / 55 | 35 / 60 | (default) | (default) | (default) |
| `package`, `goods` | 20 / 45 | (default) | (default) | (default) | (default) |
| `location` | 10 / 25 | 20 / 40 | (default) | (default) | (default) |

`ocr` aktif di **semua** profile: target utama engine adalah teks di foto (resi, label, dokumen) harus terbaca;
target berikutnya paket/foto terlihat jelas.

**`text_first`** (profile, bawaan `true`): jika teks terbaca (skor `ocr` ≥ warn), masalah kejelasan foto lain
(`sharpness`, `resolution`, `exposure`: buram ringan, pecah, gelap, terlalu terang, pudar) paling berat **WARN**: kurir
diberi tahu, status pengiriman tetap boleh diupdate. Alasan tersebut ditandai `detail.softened: "text_readable"`. Teks
tidak terbaca tetap REJECT. `text_first: false` = aturan lama (setiap pemeriksaan bisa REJECT sendiri).

**Latar putih dokumen/layar:** pemeriksaan `exposure` mengenali latar kertas (banyak putih + goresan teks gelap;
metrik `paper_background`). Silau dihitung hanya dari area putih yang jauh dari teks, dan "terlalu terang" dari
kepekatan teks tergelap. Jika teks terbaca jelas (skor `ocr` ≥ 80), `GLARE`/`TOO_BRIGHT` pada latar kertas hanya info.

(format `reject / warn`)

**authenticity** (keaslian) defaultnya hanya bisa WARN (`reject: null`). Temuan keaslian adalah indikasi untuk ditinjau manusia, bukan bukti.

<a id="perbaikan-pencahayaan-otomatis-enhance"></a>

#### Koreksi pencahayaan opsional (enhance)

**Bawaan: nonaktif.** Engine fokus memproses gambar tanpa manipulasi: hanya optimasi (perkecil proporsional bila
perlu, orientasi EXIF) dan kompresi dinamis (lihat [bagian 4](#4-kompresi--thumbnail)). Foto gelap/terang/pudar
dinilai apa adanya dan, jika `REJECT`, kurir diminta foto ulang.

Admin bisa mengizinkan koreksi pencahayaan untuk profile tertentu:

```yaml
profiles:
  package:
    enhance:
      enabled: true     # bawaan false. true = coba koreksi kurva global sebelum verdict final
```

Jika diaktifkan dan ada alasan `TOO_DARK`, `TOO_BRIGHT`, `LOW_CONTRAST`, atau `GLARE` (severity `warn`/`reject`),
engine mencoba mengoreksi pencahayaan **sebelum** verdict final diputuskan:

1. Susun **satu** kurva pencahayaan (LUT 256 nilai) dari statistik foto: *levels* (rentang persentil 0,5-99,5
   direntangkan ke 0-255 jika rentangnya < 200) lalu *gamma* (jika median kecerahan < 90 atau > 170, digeser ke ±120;
   gamma dibatasi 0,3-3,0). Kurva dipaksa monoton naik.
2. Terapkan kurva yang sama ke **seluruh piksel dan ketiga kanal warna**. Tidak ada penajaman, resize, crop, rotasi,
   AI, atau label/watermark.
3. **Verifikasi eksak:** setiap nilai masukan pada setiap kanal harus menghasilkan tepat satu nilai keluaran dan
   urutannya tidak terbalik. Gagal satu saja = hasil dibuang (`reason: verification_failed`).
4. Nilai ulang **hanya** `exposure` dan `ocr` pada hasil perbaikan. Skor `sharpness`, `resolution`, `authenticity`
   tetap dari foto asli.
5. Hasil dipakai hanya jika verdict **membaik** (`REJECT` → `WARN`/`PASS`, atau `WARN` → `PASS`); jika tidak,
   `reason: no_improvement`. Jika dipakai, `output_image` dibuat dari hasil perbaikan (JPEG
   quality 95 sebelum kompresi, selalu file baru meskipun kecil).

Karena kurva memberi nilai yang sama untuk nilai piksel yang sama di mana pun posisinya, perbaikan ini **tidak bisa
menambah atau mengubah huruf/angka**. Foto original dan `sha256` tidak pernah berubah. Detail hasil tersimpan di
field `enhancement` foto (lihat [integration-guide.md §7.1](integration-guide.md#71-optimasi-tanpa-manipulasi)).
Tanpa `enhance.enabled: true`, `enhancement` selalu `{"attempted": false, "applied": false}`.

Foto yang `REJECT` (termasuk yang tetap `REJECT` setelah koreksi, jika diaktifkan) mendapat `retake_required: true`.

---

## 3. Cara skor dihitung

Semua pemeriksaan berbasis aturan (OpenCV), tanpa model AI, jadi setiap skor bisa dijelaskan. Rumus `linear(x, a, b)` artinya: `x = a` → 0, `x = b` → 100, linear di antaranya, dipotong ke 0-100.

### sharpness (kode alasan `BLURRY`)

1. Foto diubah ke grayscale dan diperkecil sehingga sisi terpanjang = `analysis_long_side_px` (1024). Dengan begitu foto 12 MP dan 2 MP dinilai di skala yang sama.
2. Blur ringan 3×3 (meredam noise sensor), lalu *variance of Laplacian* dihitung per tile 4×4 (16 tile).
3. Kontras rendah dikompensasi (penguat 1-4×), supaya foto gelap/pudar tidak dianggap buram. Masalah cahaya ditangani `exposure`.
4. Metrik = rata-rata **25% tile paling tajam**. Foto dengan objek tajam tapi latar polos/bokeh tetap lolos.
5. Skor = `linear(log10(metrik+1), log10(low+1), log10(high+1))`.

| Param | Default | Arti |
|---|---|---|
| `params.sharpness.analysis_long_side_px` | 1024 | Skala normalisasi |
| `params.sharpness.low` | 4 | Metrik ≤ ini → skor 0 |
| `params.sharpness.high` | 120 | Metrik ≥ ini → skor 100 |

Sebagai gambaran (foto 2400 px): foto tajam biasanya metrik 50-250+, blur ringan (Gaussian σ2) ~30, blur jelas (σ4) ~10, sangat buram (σ8) ~2-3.

### resolution (kode `LOW_RESOLUTION`, `PIXELATED`, `HEAVY_COMPRESSION`)

Skor = **sub-skor terendah**, dan kode alasan mengikuti sub-skor terendah itu.

| Sub-skor | Rumus | Arti |
|---|---|---|
| `LOW_RESOLUTION` | `linear(sisi pendek px, 0, ideal_short_side_px)` | Sisi pendek setelah rotasi EXIF. Default ideal 1080: 540 px → 50 |
| `PIXELATED` | `min(linear(blockiness, 3.5, 2.2), 40 + 0.6 × linear(mosaic, 22, 12))` | *blockiness* = blocking 8×8 JPEG (~1.0-1.5 normal, >3 kotak-kotak jelas). *mosaic* = foto kecil yang diperbesar. Sinyal mosaic **dibatasi minimal 40**, jadi sendirian paling jauh menghasilkan WARN, tidak pernah REJECT (foto berarea polos yang dikompres WebP/HEIC bisa mirip) |
| `HEAVY_COMPRESSION` | `linear(estimasi quality JPEG, 10, 60)` | Hanya untuk JPEG, dari tabel kuantisasi. Quality ≥ 60 → 100 |

Dengan threshold bawaan (30 / 55): sisi pendek < 324 px → REJECT, < 594 px → WARN; quality JPEG < 25 → REJECT, < 37.5 → WARN.

| Param | Default |
|---|---|
| `params.resolution.ideal_short_side_px` | 1080 |

### exposure (kode `TOO_DARK`, `TOO_BRIGHT`, `GLARE`, `LOW_CONTRAST`)

Dihitung pada grayscale 512 px. Skor = sub-skor terendah.

| Sub-skor | Rumus | Arti |
|---|---|---|
| `TOO_DARK` | `linear(rata-rata kecerahan, 20, 75)` | 0-255 |
| `TOO_BRIGHT` | `linear(rata-rata kecerahan, 245, 205)` | |
| `GLARE` | `linear(porsi piksel ≥ 250, 0.45, 0.12)` | Pantulan/cahaya putih menutupi foto |
| `LOW_CONTRAST` | `linear(simpangan baku, 6, 30)` | Foto pudar |

Tidak ada param yang bisa diubah. Atur lewat threshold `exposure`.

### ocr (kode `NO_TEXT_DETECTED`, `TEXT_UNREADABLE`; info `OCR_UNAVAILABLE`)

Target engine: **teks di foto harus terbaca**. Aktif di semua profile bawaan, butuh Tesseract (image Docker standar
sudah menyertakan bahasa `ind` + `eng`).

0. Luruskan foto bila miring (sudut dominan dari garis lurus: baris teks, tepi kartu/resi; Hough). Hanya untuk
   dibaca, foto tidak diubah. Kasus nyata: kartu/resi yang dipegang miring 10-35° baru terbaca setelah diluruskan.
1. Cari area yang tampak seperti teks (OpenCV: gradien + morfologi), ambil maksimal 4 area terbesar.
2. Baca tiap area dengan Tesseract pada resolusi asli (area kecil diperbesar, area tegak dicoba diputar 90°).
3. Jika hasil area belum meyakinkan (< 3 kata atau confidence < 70), seluruh foto ikut dibaca; dipakai yang terbaik.
4. Jika aplikasi mengirim `awb` dan nomor itu terbaca di foto (huruf/angka saja, spasi diabaikan), skor minimal 90
   (`metrics.ocr.awb_found: true`).

- Skor = rata-rata confidence Tesseract per kata (berbobot panjang kata; kata dengan < 2 karakter alfanumerik diabaikan).
- Kurang dari 3 kata terbaca (dan resi tidak ditemukan) → skor 0, kode `NO_TEXT_DETECTED`; selain itu skor di bawah
  threshold → `TEXT_UNREADABLE`.
- Area dengan banyak baris (> 8, misal struk panjang atau halaman laporan) dibaca per kelompok 4 baris: awal, tengah,
  akhir (dipotong di sela baris). Keterbacaan diukur dari sampel itu.
- Batas waktu baca 1,2 detik per foto. Waktu habis tanpa hasil (server sibuk / foto sangat buram) → info
  `OCR_TIMEOUT`, **tanpa skor** (tidak menolak foto hanya karena waktu habis; foto buram tetap ditolak oleh `sharpness`).
- Metrik: `score`, `confidence`, `words`, `awb_found`, `method` (`regions`/`full`), `regions` (koordinat relatif area teks).
- Tesseract tidak ada → info `OCR_UNAVAILABLE`, tanpa skor, tidak memengaruhi verdict (preflight memberi peringatan).

### authenticity (dari `exif` + `duplicate`)

Skor keaslian = `100 - total poin risiko` (minimal 0). Hanya dihitung jika `exif` atau `duplicate` ada di `checks`. Di API ditampilkan juga `authenticity_risk = 100 - skor`.

| Kode | Poin | Kapan muncul | Param |
|---|---|---|---|
| `EDITING_SOFTWARE` | 40 | Tag EXIF `Software` berisi Photoshop, Lightroom, GIMP, Snapseed, PicsArt, Canva, Facetune, Meitu, Pixlr, Affinity, VSCO, PhotoDirector, AirBrush | — |
| `OLD_PHOTO` | 30 | Waktu EXIF lebih tua dari waktu foto diterima engine lebih dari N jam | `params.exif.max_age_hours` (48) |
| `CAPTURE_TIME_MISMATCH` | 30 | Waktu EXIF berbeda dengan `captured_at` dari aplikasi lebih dari N jam | `params.exif.captured_at_tolerance_hours` (6) |
| `GPS_MISMATCH` | 30 | GPS EXIF berjarak lebih dari N km dari `lat/lng` dari aplikasi | `params.exif.gps_tolerance_km` (2) |
| `DUPLICATE_EXACT` | 70 | File identik (SHA-256 sama) sudah dipakai foto lain dengan resi **berbeda** | — |
| `DUPLICATE_SIMILAR` | 50 | Foto secara visual sangat mirip (jarak Hamming pHash 64-bit ≤ N) dengan foto resi berbeda | `params.duplicate.max_distance` (6) |
| `EXIF_MISSING` | 0 (info) | Tidak ada make, model, maupun waktu di EXIF. **Normal**: banyak aplikasi/platform menghapus EXIF | — |

Catatan:

- Perbandingan duplikat hanya dalam tenant yang sama, untuk foto `PQE_DUPLICATE_LOOKBACK_DAYS` hari terakhir. Foto lain dengan resi **sama** tidak dihitung (wajar ada beberapa foto per pengiriman).
- Waktu EXIF memakai `DateTimeOriginal` (atau `DateTime`) dengan `OffsetTimeOriginal` jika ada. Tanpa offset, dianggap `timezone` tenant.
- Dengan threshold bawaan `warn: 75`, **satu temuan saja (≥ 30 poin) sudah memicu WARN** untuk ditinjau ops. Jika terlalu sensitif, turunkan `thresholds.authenticity.warn` (contoh 65 → temuan 30 poin saja tidak lagi WARN, tetapi 40 poin ke atas tetap WARN).

---

## 4. Kompresi & thumbnail

Ini satu-satunya pemrosesan gambar bawaan engine: **optimasi tanpa manipulasi**. Foto hanya diperkecil proporsional
(bila perlu) dan di-encode ulang; tidak ada filter, penajaman, crop, atau koreksi warna/cahaya. Hasilnya dikembalikan
ke aplikasi sebagai `output_image` di respons `POST /v1/analyze` (upload file); aplikasi yang menyimpannya ke SILO.

| Field `compression` | Default `default` | `pod_document` | Arti |
|---|---|---|---|
| `enabled` | `true` | | `false` = tidak pernah kompres |
| `format` | `jpeg` | | `jpeg` (progressive, optimize, 4:2:0) atau `webp` |
| `target_max_bytes` | 1 000 000 | | Target ukuran hasil |
| `skip_if_below_bytes` | 1 000 000 | | File ≤ ini (dan memenuhi syarat di bawah) tidak dikompres |
| `max_long_side_px` | 2000 | 2400 | Sisi terpanjang maksimal hasil |
| `min_long_side_px` | 1024 | 1400 | Engine tidak memperkecil di bawah ini demi ukuran |
| `min_quality` | 55 | 70 | Quality terendah yang boleh dipakai |
| `max_quality` | 90 | | Quality tertinggi yang dicoba |
| `guard.min_ssim` | 0.90 | 0.94 | Kemiripan minimal hasil vs original (SSIM, 1.0 = identik) |
| `text_guard_max_bytes` | 1 500 000 | | Batas ukuran saat penjaga keterbacaan teks menaikkan kualitas |

**Algoritma:**

1. **Lewati (passthrough)** jika `enabled: false`, **atau** ukuran ≤ `skip_if_below_bytes` **dan** format JPEG/PNG/WebP **dan** sisi terpanjang ≤ 1.5 × `max_long_side_px`. `output_image` = file asli apa adanya (`optimized: false`). HEIC/HEIF selalu dikonversi ke JPEG karena tidak semua browser bisa membukanya.
2. Perkecil ke `max_long_side_px` (jika lebih besar).
3. Untuk skala 100%, 85%, 72%, 60%, 50% (tidak di bawah `min_long_side_px`): cari **quality tertinggi** antara `min_quality`-`max_quality` yang ukurannya ≤ target (binary search).
4. Hitung SSIM hasil terhadap original (grayscale, 1024 px). Jika ≥ `guard.min_ssim`, hasil diterima.
5. Jika target **dan** guard tidak bisa dipenuhi bersamaan, **keterbacaan menang**: dipilih kandidat terkecil yang lolos guard (meski di atas target), atau `max_quality` di `max_long_side_px`. Hasilnya ditandai info `COMPRESSION_TARGET_NOT_MET`.
6. Jika hasil malah lebih besar dari original (dan formatnya bisa dibuka browser), original yang dipakai.
7. **Penjaga keterbacaan teks:** jika teks foto asli terbaca (skor `ocr` ≥ warn), hasil kompres dibaca ulang di area
   teks yang sama. Skor tidak boleh turun lebih dari 8 poin, tidak boleh di bawah warn, dan resi yang terbaca harus tetap
   terbaca. Jika gagal: quality dinaikkan, lalu resolusi (×1,4, lalu resolusi asli), lalu foto asli, tetapi **tidak
   pernah melewati `text_guard_max_bytes`** (bawaan 1 500 000 byte) supaya storage terkendali. Tidak ada yang lolos →
   dipakai kandidat paling terbaca dalam batas itu. Hasilnya di `compression.text_guard`
   (`before`, `after`, `awb_found`, `raised`, `ok`, `max_bytes`, `steps`).

**Penajam teks** (`enhance.sharpen_text`, bawaan `true`, tanpa AI): jika teks sulit dibaca (skor `ocr` < warn), engine
mencoba unsharp mask klasik (`hasil = foto + 1,0 × (foto − blur σ1,2)`), lalu membaca ulang. Dipakai **hanya** jika skor
keterbacaan naik ≥ 5 poin dan verdict tidak memburuk; skor ketajaman tidak dinilai ulang (tidak boleh naik palsu).
Filter ini hanya mempertegas tepi yang sudah ada, tidak bisa menambah/mengubah huruf. Hanya untuk versi yang disimpan;
foto asli dan hash-nya tidak berubah. Dicatat di `enhancement.sharpen_text` (`before`, `after`, `applied`). Dilewati
jika analisis sudah > 1,6 detik (kurir tetap dijawab < 3 detik).

**Thumbnail:** sisi terpanjang `thumbnail.long_side_px` (320), JPEG `thumbnail.quality` (70). Selalu JPEG. Dibuat engine
saat diminta Web UI (`GET /v1/photos/{id}/file?variant=thumbnail`, dari arsip engine atau bucket aplikasi), tidak
disimpan.

**Yang disimpan:** foto yang **diterima** (`allow_status_update: true`) dikembalikan di `output_image` (base64; hasil
kompresi, atau file asli jika tidak perlu dikompres); aplikasi menyimpannya ke bucket-nya sendiri dan boleh memberi tahu
lokasinya (`object_key` atau `POST /v1/photos/{id}/stored`). Foto yang ditolak: `output_image: null`. Engine
mengarsipkan salinannya di bucket arsip (`ocr`) setelah respons: foto diterima = isi `output_image`, foto ditolak =
foto asli. Untuk foto yang dibaca dari SILO (polling, `object_key`), engine hanya menganalisis; tidak ada salinan yang
disimpan.

---

## 5. Mode, verdict, dan action

| Verdict | Arti |
|---|---|
| `PASS` | Semua skor di atas batas `warn` |
| `WARN` | Ada skor di bawah `warn` (tapi tidak di bawah `reject`), atau ada indikasi keaslian |
| `REJECT` | Ada skor di bawah `reject`, atau file tidak bisa dibuka (`UNREADABLE_FILE`) |

`action` adalah **saran untuk aplikasi**, sesuai `mode` profile. Engine sendiri tidak pernah menahan upload atau mengubah status pengiriman (status milik aplikasi pengiriman); engine hanya memberi keputusan `allow_status_update`.

| mode | PASS | WARN | REJECT |
|---|---|---|---|
| `shadow` | `log` | `log` | `log` |
| `warn` | `log` | `notify` | `notify` |
| `enforce` | `log` | `notify` | `reject` |

- `log` = cukup dicatat, jangan ganggu kurir. `allow_status_update: true`, `courier_message: null`.
- `notify` = tampilkan `courier_message` ke kurir, tapi foto tetap diterima. `allow_status_update: true`.
- `reject` = foto ditolak: tampilkan `courier_message`, minta kurir foto ulang. `allow_status_update: false`, artinya
  aplikasi pengiriman **tidak boleh** mengupdate status pengiriman sampai foto ulang diterima. Status pengiriman milik
  aplikasi pengiriman; engine hanya memberi keputusan.

Alur yang disarankan: semua tenant mulai di `shadow` (1-2 minggu, kalibrasi), lalu `warn`, lalu `enforce` setelah disetujui ops & finance. Mode bisa berbeda per profile (contoh `pod_document: enforce`, `location: warn`).

Verdict dihitung dari foto apa adanya (atau setelah koreksi pencahayaan jika profile mengaktifkan
[enhance](#perbaikan-pencahayaan-otomatis-enhance)). Selain `action`, setiap foto punya field turunan:

| Field | Aturan |
|---|---|
| `allow_status_update` | `false` jika `action = reject` atau ops memilih "minta foto ulang"/"tandai kecurangan"; `true` jika ops meng-*approve*, selama `pending`, dan saat gagal karena sistem (fail-open). File rusak (`UNREADABLE_FILE`) diperlakukan seperti `REJECT`. Hanya bisa `false` di mode `enforce` |
| `courier_message` | Kalimat siap tampil untuk kurir (ID/EN) jika `action` `notify`/`reject`: `<message>. <suggestion>` dari alasan pertama, diawali `Wajib foto ulang.` jika `retake_required`. Selain itu `null` |
| `retake_required` | `true` jika verdict `REJECT` (kualitas tidak memenuhi syarat), atau ops memilih "minta foto ulang". `false` jika ops meng-*approve*. Disertai `retake_message` (ID/EN). Tidak bergantung pada `mode` |
| `evidence_eligible` | `review_required: false` (bawaan): `PASS` dan `WARN` = `true`, `REJECT` = `false` (kecuali di-*approve* ops). `review_required: true`: hanya `PASS` atau yang di-*approve* ops. Foto "minta foto ulang"/"kecurangan" atau yang gagal diproses selalu `false` |

---

<a id="kalibrasi-threshold"></a>

## 6. Kalibrasi threshold

> **Penting:** threshold bawaan dikalibrasi dari sampel **sintetis** (`pqe synth-samples`) dan foto publik, **bukan** foto POD asli PCP Express. Wajib dikalibrasi ulang dengan foto POD asli selama mode `shadow` sebelum naik ke `warn`/`enforce`.

### Langkah

1. **Kumpulkan sampel berlabel.** Minimal ±50 foto per kelompok, misal folder `bagus/`, `buram/`, `pecah/`, `gelap/`, plus foto yang dulu ditolak finance/pelanggan. Ambil dari antrean Review (ops sudah melabeli dengan approve/retake) atau langsung dari bucket.

2. **Jalankan analisis lokal** (tanpa server, tanpa DB):
   ```bash
   pqe analyze sampel/bagus/*.jpg  --profile pod_recipient --json > bagus.json
   pqe analyze sampel/buram/*.jpg  --profile pod_recipient --json > buram.json
   # pakai profile dari config tenant (termasuk override):
   pqe analyze sampel/resi/*.jpg --tenant-file config/tenants/courier-app.yaml --profile pod_document --json > resi.json
   # simpan hasil kompres untuk dicek mata:
   pqe analyze sampel/bagus/*.jpg --profile pod_document --out-dir hasil-kompres/
   ```
   Lewat Docker: `docker compose exec engine pqe analyze ...` (mount folder sampel ke container).

3. **Lihat sebaran skor**, misalnya dengan `jq`:
   ```bash
   jq -r '.[] | [.file, .verdict, .scores.sharpness, .scores.resolution, .scores.exposure] | @tsv' bagus.json buram.json
   ```
   Pilih threshold yang memisahkan kelompok "bagus" dan "buruk". Lebih aman sedikit longgar daripada menolak foto bagus.

4. **Coba di Web UI → Uji foto** untuk kasus-kasus tertentu. Tidak ada yang disimpan dan cek duplikat tidak dijalankan.

5. **Simpan threshold baru** di config tenant (Web UI → Tenant → Ubah → Validasi → Simpan). Contoh:
   ```yaml
   profiles:
     pod_recipient:
       thresholds:
         sharpness: { reject: 20, warn: 40 }
       params:
         sharpness: { low: 3, high: 100 }
   ```

6. **Analisis ulang foto lama** jika perlu: tombol **Analisis ulang** di detail foto, atau `POST /v1/photos/<id>/reprocess` (role admin/ops). Status review yang sudah ada tidak direset.

7. Pantau dashboard: persentase WARN/REJECT dan alasan terbanyak. Ulangi kalibrasi bulanan.

---

## 7. Bagaimana perubahan config berlaku

- **Database adalah sumber kebenaran.** File YAML di `PQE_TENANTS_DIR` hanya **seed**: saat start, tenant yang *belum ada* di DB di-import. Tenant yang sudah ada **tidak ditimpa**.
- Mengubah config tenant yang sudah ada:
  - **Web UI → Tenant → Ubah → Validasi → Simpan** (disarankan), atau
  - `PUT /v1/admin/tenants` dengan body YAML (`Content-Type: application/x-yaml`), atau
  - edit file YAML lalu `pqe import-tenants --force` (menimpa dari file).
- Setiap simpan yang mengubah isi menaikkan **versi** (`v1`, `v2`, ...). Riwayat lengkap tersimpan (Web UI → Riwayat versi, atau `GET /v1/admin/tenants/<code>/history`) dan tercatat di audit log.
- Engine membaca ulang config dari DB **paling lambat 5 detik**, tanpa restart, di semua proses (API, worker, ingestor). (Koneksi SILO: paling lambat 10 detik.)
- Config yang tidak valid **tidak bisa disimpan**. Pesan errornya menyebut field yang salah.
- Perubahan berlaku untuk foto yang dianalisis **setelahnya**. Setiap foto menyimpan `config_version` (versi config tenant saat dianalisis), jadi hasil lama tetap bisa dijelaskan. Gunakan **Analisis ulang** untuk menerapkan aturan baru ke foto lama.
- Validasi file tanpa menyimpan: `pqe validate-tenant config/tenants/*.yaml`.
- Menonaktifkan tenant (Web UI / `POST /v1/admin/tenants/<code>/disable`) menghentikan polling dan event untuk tenant itu dan menolak `POST /v1/analyze` (`409 TENANT_DISABLED`). Data lama tetap ada.
