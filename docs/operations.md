# Panduan Operasional & Deployment

Dokumen ini untuk tim infra/ops yang menjalankan Photo Quality Engine: dari PC uji coba sampai Kubernetes produksi, termasuk setup SILO, backup, monitoring, runbook, dan checklist keamanan.

Untuk integrasi aplikasi lihat [integration-guide.md](integration-guide.md). Untuk pengembangan engine lihat [development.md](development.md).

## Daftar Isi

1. [Mode menjalankan engine](#1-mode-menjalankan-engine)
2. [Docker compose lokal (full stack)](#2-docker-compose-lokal-full-stack)
3. [Docker compose dengan SILO kantor](#3-docker-compose-dengan-silo-kantor)
4. [Setup SILO produksi (untuk tim infra)](#4-setup-silo-produksi-untuk-tim-infra)
5. [Kubernetes](#5-kubernetes)
6. [PostgreSQL & backup](#6-postgresql--backup)
7. [Monitoring](#7-monitoring)
8. [Kapasitas & scaling](#8-kapasitas--scaling)
9. [Runbook](#9-runbook)
10. [Upgrade & rollback](#10-upgrade--rollback)
11. [Checklist keamanan](#11-checklist-keamanan)

---

## 1. Mode menjalankan engine

Engine terdiri dari tiga peran. Semuanya ada di satu image/paket yang sama, dipilih lewat perintah `pqe`.

| Perintah | Isi | Kapan dipakai |
|---|---|---|
| `pqe run` | API + Web UI + worker + ingestor dalam **satu proses** | PC, uji coba, volume kecil. Default `CMD` di `Dockerfile` |
| `pqe api` | API + Web UI (`/ui`, `/docs`, `/metrics`, `/healthz`, `/readyz`) | Produksi, bisa banyak replika |
| `pqe worker` | Analisis foto, kirim webhook, pemeliharaan (job macet, retensi, gauge metrik) | Produksi, di-scale sesuai antrean |
| `pqe ingestor` | Polling SILO / DB aplikasi + reconciliation | Produksi, **cukup 1 replika** |

Opsi yang tersedia:

```bash
pqe run --host 0.0.0.0 --port 8000 --concurrency 2   # --concurrency = jumlah thread analisis
pqe api --host 0.0.0.0 --port 8000
pqe worker --concurrency 4
pqe ingestor
```

Setiap peran menjalankan `bootstrap` saat start: membuat tabel database (jika belum ada), membuat kunci admin dari `PQE_BOOTSTRAP_ADMIN_KEY` (jika belum ada), meng-import tenant dari `PQE_TENANTS_DIR` (hanya tenant yang **belum** ada di DB), dan membuat bucket engine jika `ENGINE_CREATE_BUCKETS=true`.

Perintah operasional lain:

| Perintah | Fungsi |
|---|---|
| `pqe init` | Siapkan database & import tenant, lalu keluar |
| `pqe create-key --name NAMA --role admin\|ops\|viewer\|app\|customer [--tenant KODE] [--customer-id ID]` | Buat API key (nilai hanya tampil sekali) |
| `pqe import-tenants [--dir FOLDER] [--force]` | Import YAML tenant. Tanpa `--force` tenant yang sudah ada **tidak** ditimpa |
| `pqe validate-tenant FILE...` | Validasi YAML tenant tanpa menyimpan |
| `pqe reconcile [--tenant KODE]` | Jalankan semua source sekali dengan lookback panjang (minimal 7 hari) |
| `pqe process` | Proses semua job yang antre lalu berhenti |
| `pqe analyze FILE... [--profile P] [--tenant-file F] [--lang id\|en] [--json] [--out-dir D]` | Analisis file lokal tanpa server/DB |
| `pqe synth-samples FOLDER` | Buat foto contoh sintetis |
| `pqe demo-upload [--bucket B] [--prefix demo/]` | Upload foto contoh ke bucket untuk mencoba alur end-to-end |
| `pqe migrate-legacy FOLDER --bucket B [--prefix P] [--dry-run] [--sleep-ms N]` | Upload foto lama ke SILO dengan verifikasi checksum (MD5 = ETag single-part) |

Semua konfigurasi lewat environment variable, lihat [configuration.md](configuration.md) dan `.env.example`.

---

## 2. Docker compose lokal (full stack)

`docker-compose.yml` menjalankan **SILO lokal + PostgreSQL 16 + engine**. Cocok untuk PC dan uji coba.

```bash
cp .env.example .env          # opsional; tanpa .env pun nilai default jalan
docker compose up -d --build
docker compose ps             # tunggu engine "healthy"
```

| Layanan | Alamat | Kredensial default |
|---|---|---|
| Web UI | http://localhost:8000/ui | API key = `PQE_BOOTSTRAP_ADMIN_KEY` (default `pqe_local_admin_change_me`) |
| Swagger API | http://localhost:8000/docs | sama |
| SILO S3 API | http://localhost:9000 | `SILO_ROOT_USER` / `SILO_ROOT_PASSWORD` (default `siloadmin` / `siloadmin-local-only`) |
| SILO console | http://localhost:9001 | sama |
| PostgreSQL | hanya di jaringan compose | user `pqe`, password `POSTGRES_PASSWORD` (default `pqe-local-only`) |

> **Semua nilai default di atas hanya untuk lokal.** Jika compose ini dipasang di mesin yang bisa diakses orang lain, ganti `PQE_BOOTSTRAP_ADMIN_KEY`, `SILO_ROOT_PASSWORD`, `POSTGRES_PASSWORD`, dan `PQE_S3_EVENT_TOKEN` di `.env` **sebelum** `up` pertama kali.

Image SILO yang dipakai: `docker.io/pgsty/silo:RELEASE.2026-10-18T00-00-00Z` (fork komunitas MinIO dari Pigsty, kompatibel S3). Engine otomatis membuat bucket `ekas-pcptransport`, `photos-processed`, `photos-thumb`, `photos-upload` (`ENGINE_CREATE_BUCKETS=true`). Folder `config/tenants` di-mount read-only ke container.

### Mencoba alur end-to-end

```bash
docker compose exec engine pqe demo-upload       # upload 12 foto contoh ke bucket aplikasi
# buka http://localhost:8000/ui -> Review
```

### Mengaktifkan notifikasi real-time (bucket notification)

Tanpa langkah ini engine tetap jalan memakai **polling** (`s3_poll`, default tiap 15 detik). Untuk real-time:

```bash
./scripts/silo-enable-events.sh                  # default bucket: AWS_BUCKET atau ekas-pcptransport
./scripts/silo-enable-events.sh nama-bucket-lain
```

Skrip ini memakai target webhook yang sudah didefinisikan di environment container SILO (`MINIO_NOTIFY_WEBHOOK_*_PQE`, endpoint `http://engine:8000/v1/events/s3`, token `PQE_S3_EVENT_TOKEN`, antrean di `/data/.pqe-events` sehingga event tidak hilang saat engine mati), lalu menjalankan `mcli event add local/<bucket> arn:minio:sqs::PQE:webhook --event put`. Polling tetap berjalan sebagai jaring pengaman. Duplikat aman karena pencatatan foto idempotent.

### Menghentikan / reset

```bash
docker compose down            # data tetap (volume pg-data & silo-data)
docker compose down -v         # HAPUS semua data lokal (database + foto). Jangan pernah di server bersama!
```

---

## 3. Docker compose dengan SILO kantor

`docker-compose.remote-silo.yml` menjalankan **engine + PostgreSQL lokal**, foto dibaca dari SILO kantor (`s3.pcpexpress.com`).

```bash
cp .env.example .env
# isi minimal: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (service account engine, lihat bagian 4),
#              PQE_BOOTSTRAP_ADMIN_KEY (acak panjang), PUBLIC_BASE_URL
docker compose -f docker-compose.remote-silo.yml up -d --build
```

Catatan:

- Seluruh `.env` dimuat ke container engine (`env_file: .env`). `DATABASE_URL` ditimpa compose agar mengarah ke Postgres di compose.
- `ENGINE_CREATE_BUCKETS` di `.env.example` bernilai `false`: bucket engine harus sudah dibuat tim infra (bagian 4).
- Engine hanya **membaca** `AWS_BUCKET` dan **menulis** ke `ENGINE_BUCKET_PROCESSED`, `ENGINE_BUCKET_THUMB`, `ENGINE_BUCKET_UPLOAD`.
- Sesuaikan `config/tenants/courier-app.yaml` (bucket, prefix, mapping) dengan struktur key foto di SILO kantor sebelum start. Setelah tenant tersimpan di DB, ubah lewat Web UI (menu Tenant) atau `pqe import-tenants --force`.

---

## 4. Setup SILO produksi (untuk tim infra)

Semua perintah memakai `mcli` (klien MinIO yang dibundel SILO; di container SILO tersedia sebagai `mcli`). Sintaks mengikuti klien MinIO; cocokkan dengan dokumentasi SILO (https://silo.pgsty.com/docs/) untuk versi yang terpasang.

```bash
mcli alias set pcp https://s3.pcpexpress.com ADMIN_USER ADMIN_PASSWORD
```

### 4.1 Bucket engine

```bash
mcli mb pcp/photos-processed
mcli mb pcp/photos-thumb
mcli mb pcp/photos-upload
```

Bucket aplikasi (`ekas-pcptransport`) sudah ada dan tetap dimiliki aplikasi. Pastikan semua bucket **private** (tanpa anonymous policy): `mcli anonymous get pcp/ekas-pcptransport` harus `private`/`none`.

### 4.2 Service account engine (least privilege)

Jangan pernah memakai akun admin/root SILO untuk engine. Simpan policy berikut sebagai `pqe-engine-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BacaBucketAplikasi",
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": ["arn:aws:s3:::ekas-pcptransport/*"]
    },
    {
      "Sid": "ListBucketAplikasi",
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": ["arn:aws:s3:::ekas-pcptransport"]
    },
    {
      "Sid": "KelolaObjekBucketEngine",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": [
        "arn:aws:s3:::photos-processed/*",
        "arn:aws:s3:::photos-thumb/*",
        "arn:aws:s3:::photos-upload/*"
      ]
    },
    {
      "Sid": "ListBucketEngine",
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": [
        "arn:aws:s3:::photos-processed",
        "arn:aws:s3:::photos-thumb",
        "arn:aws:s3:::photos-upload"
      ]
    }
  ]
}
```

```bash
mcli admin user add pcp pqe-engine 'SECRET_ACAK_PANJANG'
mcli admin policy create pcp pqe-engine pqe-engine-policy.json
mcli admin policy attach pcp pqe-engine --user pqe-engine
```

Isi `AWS_ACCESS_KEY_ID=pqe-engine` dan `AWS_SECRET_ACCESS_KEY=<secret>` di `.env` / K8s Secret. Jika foto aplikasi lain ada di bucket lain, tambahkan bucket itu ke dua statement pertama.

Engine tidak membuat bucket di produksi (`ENGINE_CREATE_BUCKETS=false`), jadi policy di atas tidak butuh `s3:CreateBucket`. `/readyz` memeriksa akses ke `photos-processed`.

### 4.3 Bucket notification ke engine (real-time)

```bash
mcli admin config set pcp notify_webhook:pqe \
  endpoint="https://photo-quality.pcpexpress.com/v1/events/s3" \
  auth_token="ISI_SAMA_DENGAN_PQE_S3_EVENT_TOKEN" \
  queue_dir="/data/.pqe-events" \
  queue_limit="100000"
mcli admin service restart pcp

mcli event add pcp/ekas-pcptransport arn:minio:sqs::pqe:webhook --event put
mcli event list pcp/ekas-pcptransport          # verifikasi
```

- Identifier di ARN (`pqe`) **harus sama** dengan nama config (`notify_webhook:pqe`). Di docker-compose lokal identifier-nya `PQE` (dari env `MINIO_NOTIFY_WEBHOOK_*_PQE`).
- `queue_dir` membuat event diantre di disk SILO saat engine tidak bisa dihubungi, lalu dikirim ulang.
- Engine menerima token dalam bentuk `Authorization: Bearer <token>` maupun `Authorization: <token>`. Jika `PQE_S3_EVENT_TOKEN` kosong, endpoint `/v1/events/s3` menerima tanpa token. **Selalu isi di produksi.**
- Di config tenant, source boleh tetap `s3_poll` (polling + event berjalan bersama) atau diganti `s3_event` (event sebagai jalur utama, polling hanya saat reconciliation tiap `PQE_RECONCILE_INTERVAL_SEC`, default 24 jam).

### 4.4 Retensi (lifecycle)

Engine menghapus data miliknya sendiri yang melewati `retention_days` tenant (default 1095 hari = 3 tahun): baris DB foto, objek processed dan thumbnail. **Foto original milik aplikasi tidak dihapus engine.** Atur lifecycle di SILO:

```bash
mcli ilm rule add pcp/ekas-pcptransport --expire-days 1095
mcli ilm rule add pcp/photos-processed  --expire-days 1095
mcli ilm rule add pcp/photos-thumb      --expire-days 1095
mcli ilm rule add pcp/photos-upload     --expire-days 1095
mcli ilm rule ls pcp/ekas-pcptransport
```

Durasi final dikonfirmasi legal/finance (lihat [sdlc-plan.md](sdlc-plan.md) Q5). Uji dulu di bucket staging.

### 4.5 Versioning & object lock (disarankan)

Foto original adalah bukti penagihan. Lindungi dari penghapusan tidak sengaja / ransomware:

- Aktifkan versioning: `mcli version enable pcp/ekas-pcptransport`, lalu tambahkan aturan pembersihan versi lama (`mcli ilm rule add pcp/ekas-pcptransport --noncurrent-expire-days 30`).
- Object lock hanya bisa diaktifkan saat bucket **dibuat** (`mcli mb --with-lock ...`). Untuk bucket yang sudah ada, rencanakan migrasi ke bucket baru dengan lock. Mode yang disarankan: GOVERNANCE dengan retensi sesuai kebijakan.

---

## 5. Kubernetes

Manifest ada di `deploy/k8s/` (kustomize):

| File | Isi |
|---|---|
| `namespace.yaml` | Namespace `photo-quality` |
| `configmap.yaml` | `pqe-config`: endpoint SILO, bucket, `PUBLIC_BASE_URL`, `PQE_TENANTS_DIR=/app/config/tenants`, dst. |
| `secret.example.yaml` | Contoh `pqe-secrets` (**jangan** di-apply / commit dengan nilai asli) |
| `api.yaml` | Deployment `pqe-api` (2 replika, readiness `/readyz`, liveness `/healthz`) + Service `pqe-api` |
| `worker.yaml` | Deployment `pqe-worker` (2 replika) + HPA CPU 70%, 2-8 replika |
| `ingestor.yaml` | Deployment `pqe-ingestor` (1 replika) |
| `ingress.yaml` | Ingress nginx `photo-quality.pcpexpress.com`, TLS `pqe-tls`, body maks 30 MB |

### 5.1 Build & push image

```bash
docker build -t registry.pcpexpress.com/photo-quality-engine:0.1.0 .
docker push registry.pcpexpress.com/photo-quality-engine:0.1.0
# tanpa OCR (image lebih kecil): docker build --build-arg WITH_OCR=false ...
```

Sesuaikan `images.newName` / `newTag` di `kustomization.yaml`. Pipeline GitLab (`.gitlab-ci.yml`) membangun dan push `$CI_REGISTRY_IMAGE:<sha>` dan `:latest` dari default branch. Di produksi pakai tag versi / sha, bukan `latest`.

### 5.2 Deploy

Dari root repository:

```bash
kubectl apply -f deploy/k8s/namespace.yaml

kubectl -n photo-quality create secret generic pqe-secrets \
  --from-literal=DATABASE_URL='postgresql+psycopg://pqe:***@postgres-host:5432/pqe' \
  --from-literal=AWS_ACCESS_KEY_ID='pqe-engine' \
  --from-literal=AWS_SECRET_ACCESS_KEY='***' \
  --from-literal=PQE_BOOTSTRAP_ADMIN_KEY='pqe_***' \
  --from-literal=PQE_S3_EVENT_TOKEN='***'

kubectl -n photo-quality create configmap pqe-tenants --from-file=config/tenants/ \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -k deploy/k8s
kubectl -n photo-quality get pods
```

Catatan:

- ConfigMap `pqe-tenants` hanya **seed**. Tenant yang sudah ada di DB tidak ditimpa saat pod restart. Ubah tenant lewat Web UI atau `kubectl exec deploy/pqe-api -- pqe import-tenants --force`.
- **Ingestor cukup 1 replika.** Lebih dari satu tetap aman (pencatatan idempotent dengan unique constraint `bucket + object_key`), tapi menggandakan request list ke SILO.
- HPA worker berbasis CPU. Analisis foto CPU-bound, jadi antrean menumpuk = CPU tinggi = replika bertambah.
- Ingress harus TLS. Tambahkan IP allowlist jika endpoint `/v1/events/s3` dan `/ui` hanya untuk jaringan internal.
- Webhook SILO (bagian 4.3) diarahkan ke Service internal (`http://pqe-api.photo-quality.svc/v1/events/s3`) jika SILO berada di cluster/jaringan yang sama, atau ke URL ingress.

---

## 6. PostgreSQL & backup

### 6.1 Kebutuhan

- PostgreSQL **14+** (diuji dengan 16). Antrean job memakai `SELECT ... FOR UPDATE SKIP LOCKED`, hasil analisis memakai `JSONB`.
- Format `DATABASE_URL`: `postgresql+psycopg://user:password@host:5432/pqe`.
- Skema dibuat otomatis saat start (`create_all`). Saat ini belum ada migrasi otomatis untuk perubahan kolom; lihat [development.md](development.md#mengubah-skema-database).
- Ukuran kecil: estimasi < 5 GB untuk 3 tahun pada 1.000 foto/hari ([sdlc-plan.md](sdlc-plan.md) 3.5).
- SQLite (`sqlite:///./pqe.db`) **hanya** untuk uji lokal satu proses.

### 6.2 Backup database

```bash
# harian (cron / CronJob), simpan ke NAS
pg_dump -Fc -h postgres-host -U pqe pqe > /nas/backup/pqe/pqe-$(date +%F).dump
```

Untuk restore ke titik waktu tertentu, aktifkan WAL archiving di server Postgres (`archive_mode=on`, `archive_command` ke NAS) atau pakai fitur PITR dari layanan Postgres yang dipakai.

Uji restore **bulanan**:

```bash
createdb -h staging-host -U postgres pqe_restore_test
pg_restore -h staging-host -U postgres -d pqe_restore_test /nas/backup/pqe/pqe-YYYY-MM-DD.dump
psql -h staging-host -U postgres -d pqe_restore_test -c "select count(*), max(received_at) from photos;"
```

### 6.3 Backup foto SILO ke NAS

Sesuai [sdlc-plan.md](sdlc-plan.md) 4.11:

```bash
mcli alias set nas https://nas-silo.pcpexpress.local NAS_USER NAS_PASSWORD   # SILO/S3 di NAS
mcli mirror --watch pcp/ekas-pcptransport nas/ekas-pcptransport              # wajib (original)
mcli mirror --watch pcp/photos-processed  nas/photos-processed               # opsional (bisa dibuat ulang)
```

Jalankan `mcli mirror --watch` sebagai service (systemd / Deployment terpisah). Original **wajib** di-backup; processed & thumbnail opsional karena bisa dibuat ulang dengan analisis ulang. NAS sebaiknya di lokasi berbeda dari server SILO. Uji restore sampel acak tiap bulan.

---

## 7. Monitoring

### 7.1 Endpoint

| Endpoint | Fungsi |
|---|---|
| `GET /healthz` | Liveness: proses API hidup (`{"status":"ok","version":...}`) |
| `GET /readyz` | Readiness: cek DB (`SELECT 1`) dan akses SILO (bucket `ENGINE_BUCKET_PROCESSED`). `503` + `"degraded"` jika salah satu gagal |
| `GET /metrics` | Metrik Prometheus |

### 7.2 Daftar metrik

| Metrik | Tipe | Label | Arti |
|---|---|---|---|
| `pqe_photos_ingested_total` | counter | `tenant`, `source` | Foto baru tercatat (`s3_poll`, `s3_event`, `db_poll`) |
| `pqe_photos_analyzed_total` | counter | `tenant`, `verdict` | Foto selesai dianalisis per verdict |
| `pqe_photos_failed_total` | counter | `tenant`, `code` | Foto gagal (`UNREADABLE_FILE`, `OBJECT_NOT_FOUND`, `PROCESSING_FAILED`, `TENANT_NOT_FOUND`) |
| `pqe_jobs_dead_total` | counter | - | Job yang menyerah setelah semua retry |
| `pqe_analysis_seconds` | histogram | - | Durasi proses satu foto (analisis + kompres + simpan) |
| `pqe_webhooks_total` | counter | `result` (`ok`/`error`) | Percobaan kirim webhook |
| `pqe_source_errors_total` | counter | `tenant`, `type` | Error saat membaca source (SILO list / DB aplikasi) |
| `pqe_queue_depth` | gauge | - | Job menunggu di antrean |
| `pqe_queue_lag_seconds` | gauge | - | Umur job tertua yang masih antre |
| `pqe_dead_jobs` | gauge | - | Jumlah job dead (perlu ditinjau) |
| `pqe_pending_webhooks` | gauge | - | Webhook menunggu dikirim |

**Penting, metrik bersifat per proses:**

- Gauge antrean (`pqe_queue_*`, `pqe_dead_jobs`, `pqe_pending_webhooks`) dihitung langsung dari database setiap kali
  `/metrics` di proses API di-scrape (juga diperbarui loop pemeliharaan worker setiap ±30 detik). Nilainya sama di
  semua pod API.
- Counter analisis/webhook bertambah di proses yang mengerjakannya (worker, atau API untuk `sync=true`). Counter ingest
  bertambah di ingestor (atau API untuk event SILO).
- Dengan `pqe run` semuanya satu proses, jadi `/metrics` di port 8000 sudah lengkap.
- Dengan mode terpisah, `pqe worker` dan `pqe ingestor` membuka endpoint metrik sendiri di port `PQE_METRICS_PORT`
  (default **9100**, path `/metrics`). Scrape ketiganya: API `:8000/metrics`, worker & ingestor `:9100/metrics`.
  Endpoint 9100 ini juga dipakai `HEALTHCHECK` Docker untuk container worker/ingestor.

Contoh anotasi scrape di Kubernetes (tambahkan di template pod worker/ingestor):

```yaml
metadata:
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/port: "9100"
    prometheus.io/path: /metrics
```

### 7.3 Contoh alert Prometheus

```yaml
groups:
  - name: photo-quality-engine
    rules:
      - alert: PqeQueueLagTinggi
        expr: pqe_queue_lag_seconds > 300
        for: 5m
        labels: {severity: warning}
        annotations:
          summary: "Antrean analisis foto tertinggal > 5 menit"
          description: "Cek worker (log, CPU) dan akses SILO/DB. Runbook: docs/operations.md#9-runbook"

      - alert: PqeDeadJobs
        expr: pqe_dead_jobs > 0
        for: 10m
        labels: {severity: warning}
        annotations:
          summary: "Ada job analisis yang gagal permanen"
          description: "Tinjau di Web UI -> Sistem, perbaiki penyebab, lalu 'Ulangi semua'."

      - alert: PqeWorkerErrorRateTinggi
        expr: |
          sum(rate(pqe_photos_failed_total{code="PROCESSING_FAILED"}[15m]))
            / clamp_min(sum(rate(pqe_photos_analyzed_total[15m])), 0.001) > 0.02
        for: 15m
        labels: {severity: warning}
        annotations:
          summary: "> 2% foto gagal diproses dalam 15 menit"

      - alert: PqeWebhookMenumpuk
        expr: pqe_pending_webhooks > 100 and deriv(pqe_pending_webhooks[30m]) > 0
        for: 30m
        labels: {severity: warning}
        annotations:
          summary: "Webhook ke aplikasi menumpuk dan terus bertambah"
          description: "Endpoint webhook aplikasi kemungkinan down / menolak. Cek webhook_deliveries.last_error."

      - alert: PqeSourceError
        expr: increase(pqe_source_errors_total[15m]) > 3
        labels: {severity: warning}
        annotations:
          summary: "Ingestor gagal membaca source {{ $labels.tenant }} ({{ $labels.type }})"
          description: "Lihat Web UI -> Sistem -> Source ingest (kolom Error)."

      - alert: PqeTidakReady
        expr: probe_success{job="pqe-readyz"} == 0
        for: 5m
        labels: {severity: critical}
        annotations:
          summary: "/readyz gagal: engine tidak bisa mengakses DB atau SILO"
```

Alert terakhir mengasumsikan blackbox exporter dengan job `pqe-readyz` yang memeriksa `https://<host>/readyz`. Di Kubernetes, readiness probe `pqe-api` sudah memakai `/readyz`.

Pantau juga kapasitas SILO/NAS (alert > 80%) dan status `mcli mirror` di sisi infra.

---

## 8. Kapasitas & scaling

- Satu foto 12 MP (≈ 4 MB) butuh sekitar **1-3 detik CPU** (decode, analisis, kompresi, thumbnail). Dengan OCR (profile `pod_document`) bisa sekitar 3,5 detik. Foto kecil (< 1 MB) jauh lebih cepat karena tidak dikompres.
- **1.000 foto/hari itu sangat kecil**: rata-rata < 1 foto/menit. Satu instance `pqe run` dengan `--concurrency 2` sudah cukup lega.
- Kapasitas kasar per worker: `concurrency × 3600 / 2` foto/jam. Contoh: 2 thread ≈ 3.600 foto/jam.
- Naikkan `PQE_WORKER_CONCURRENCY` sampai sekitar jumlah core CPU pod. Di atas itu tambah replika worker (HPA).
- Memori: siapkan sekitar 300-500 MB per thread analisis untuk foto besar. Limit manifest worker 2 Gi untuk default 2 thread.
- API ringan, kecuali `POST /v1/analyze` dengan upload file dan `sync=true` yang memproses foto di proses API (pool 4 thread).
- Ingestor: polling `s3_poll` me-list seluruh prefix setiap `poll_interval_sec`. Untuk bucket besar, aktifkan `date_prefix_format` (hanya prefix tanggal terbaru yang di-list) atau pakai bucket notification.

---

## 9. Runbook

| Gejala | Kemungkinan penyebab | Langkah |
|---|---|---|
| Antrean menumpuk (`pqe_queue_lag_seconds` naik, Web UI Sistem "Antre" besar) | Worker mati / kurang, SILO lambat, DB lambat | 1) `kubectl -n photo-quality get pods`, cek log worker. 2) Cek `/readyz`. 3) Tambah replika / concurrency. 4) Cek job dead. Operasional kurir **tidak terdampak** (engine di luar jalur kurir) |
| SILO tidak bisa diakses | Jaringan / SILO down / kredensial dicabut | Engine retry otomatis (backoff 10 detik ×2, maks 1 jam, hingga `PQE_JOB_MAX_ATTEMPTS`=5). Setelah pulih: Web UI -> Sistem -> "Ulangi semua" untuk job dead, lalu `pqe reconcile` untuk mengejar foto yang terlewat |
| Database down | Postgres mati / penuh | API `/readyz` 503, worker & ingestor log error lalu mencoba lagi. Setelah DB pulih, job "running" yang tertinggal dikembalikan otomatis setelah `PQE_JOB_STALE_AFTER_SEC` (600 detik). Ingestor mengejar lewat reconciliation saat start |
| Lonjakan REJECT tiba-tiba | Versi aplikasi baru mengompres foto terlalu agresif, kamera bermasalah, atau threshold terlalu ketat | Dashboard: lihat "Alasan terbanyak" dan per kurir/cabang. Jika satu aplikasi/versi, laporkan ke tim aplikasi. Jika threshold, ubah profile (Web UI -> Tenant) atau turunkan mode ke `shadow`/`warn` sementara. Analisis ulang foto tertentu dengan tombol "Analisis ulang" |
| Webhook gagal (`pqe_pending_webhooks` naik) | Endpoint aplikasi down / menolak / secret salah | Lihat `last_error` di tabel `webhook_deliveries`. Retry otomatis hingga 8 kali (backoff 15 detik ×2, maks 1 jam), setelah itu status `dead`. Perbaiki endpoint, lalu kirim ulang lewat Web UI → **Sistem** → **Webhook gagal permanen** → **Kirim ulang semua**, atau `POST /v1/admin/webhooks/retry-dead` |
| Foto tidak muncul di engine | Prefix/bucket source salah, ekstensi tidak didukung, event tidak aktif | Web UI -> Sistem -> Source ingest (error, foto baru). Cek `sources[].prefix`/`suffixes` di config tenant. Jalankan `pqe reconcile --tenant KODE` |
| API key bocor / karyawan keluar | - | Web UI -> API key -> Cabut (atau `DELETE /v1/admin/api-keys/{id}`). Buat key baru, bagikan lewat kanal aman |
| Rotasi kunci bootstrap admin | Nilai `PQE_BOOTSTRAP_ADMIN_KEY` diketahui orang lain | Buat admin key baru (`pqe create-key --name admin-baru --role admin`), ganti `PQE_BOOTSTRAP_ADMIN_KEY` di secret, restart, lalu **cabut** key lama `bootstrap-admin` di Web UI. Bootstrap hanya *menambah* key, tidak pernah menghapus key lama |
| Rotasi kredensial SILO | Rotasi berkala / kebocoran | `mcli admin user add pcp pqe-engine-2 SECRET_BARU` + `mcli admin policy attach pcp pqe-engine --user pqe-engine-2`, update secret K8s/`.env`, restart semua pod, verifikasi `/readyz`, lalu `mcli admin user remove pcp pqe-engine` |
| Config tenant rusak / salah | Edit YAML keliru | Web UI -> Tenant -> Riwayat versi, salin versi lama, simpan. Config yang gagal di-parse dilewati (tenant lain tetap jalan) dan tercatat di log |

---

## 10. Upgrade & rollback

1. Baca catatan rilis / diff, terutama perubahan `engine/db.py` (skema) dan `requirements.lock`.
2. Pastikan CI hijau (lint, test engine, test client di runtime yang sudah di-patch, build image).
3. Backup database (`pg_dump`) sebelum upgrade produksi.
4. Deploy ke staging (bucket staging), jalankan smoke test: `/readyz`, `pqe demo-upload` ke bucket staging, cek Web UI.
5. Produksi: ganti tag image di `kustomization.yaml` lalu `kubectl apply -k deploy/k8s`. Rolling update tidak berdampak ke kurir.
6. Pantau 24 jam: antrean, error rate, distribusi verdict.

**Rollback:** `kubectl -n photo-quality rollout undo deploy/pqe-api deploy/pqe-worker deploy/pqe-ingestor`, atau kembalikan tag image. Karena skema saat ini hanya *menambah* tabel (`create_all`), versi lama tetap bisa jalan di DB yang sama. Jika suatu rilis mengubah skema secara tidak kompatibel, restore dari backup langkah 3.

Docker compose: `git pull && docker compose up -d --build`. Rollback: checkout versi sebelumnya lalu `up -d --build`.

---

## 11. Checklist keamanan

- [ ] `PQE_BOOTSTRAP_ADMIN_KEY` diganti nilai acak panjang (bukan `pqe_local_admin_change_me`). Setelah admin personal dibuat, pertimbangkan merotasi/mencabut key bootstrap.
- [ ] `PQE_S3_EVENT_TOKEN` diisi (endpoint event menerima tanpa token jika kosong).
- [ ] Engine memakai **service account khusus** dengan policy minimum (bagian 4.2), bukan admin/root SILO.
- [ ] Semua bucket **private**. Foto hanya diakses lewat presigned URL: `ENGINE_PRESIGN_TTL_UI` (default 900 detik) dan `ENGINE_PRESIGN_TTL_CUSTOMER` (default 604800 = 7 hari, maksimum yang diizinkan S3 SigV4).
- [ ] Engine diakses lewat **TLS** (ingress / reverse proxy). `AWS_PUBLIC_ENDPOINT` memakai https.
- [ ] Secret (`.env`, K8s Secret) **tidak** di-commit. `.gitignore` sudah mengecualikan `.env` dan `.env.*` kecuali `.env.example`.
- [ ] Kredensial yang pernah tersebar di chat/tiket/dokumen dianggap bocor dan dirotasi.
- [ ] API key per aplikasi (role `app` terikat tenant) dan per pelanggan (role `customer` terikat `customer_id`). Jangan berbagi key admin.
- [ ] Web UI / endpoint admin dibatasi ke jaringan internal jika memungkinkan.
- [ ] `PQE_CORS_ORIGINS` hanya berisi origin web yang memang memanggil API dari browser (kosong = CORS mati). Jangan isi `*`.
- [ ] Retensi & penghapusan sesuai kebijakan legal (UU PDP: foto memuat data pribadi penerima).
- [ ] Runtime client yang memanggil engine memakai versi yang sudah di-patch dari security release **21 Oktober 2026** (detail: [clients/README.md](../clients/README.md)):
  - Node.js **22.23.4 / 24.22.1 / 26.12.1** atau lebih baru di line yang sama (Node 20 & 25 tidak di-patch, jangan dipakai)
  - PHP **8.2.35 / 8.3.36 / 8.4.27 / 8.5.12** atau lebih baru di branch yang sama (PHP 8.1 ke bawah tidak didukung; 8.2 EOL 31 Des 2026)
- [ ] Image engine dibangun ulang berkala untuk mendapat patch OS (`python:3.12-slim`) dan dependency (`requirements.lock`).
