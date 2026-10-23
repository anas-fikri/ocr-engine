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

> **Semua nilai default di atas hanya untuk lokal.** Jika compose ini dipasang di mesin yang bisa diakses orang lain, ganti `PQE_BOOTSTRAP_ADMIN_KEY`, `SILO_ROOT_PASSWORD`, `POSTGRES_PASSWORD`, `PQE_S3_EVENT_TOKEN`, dan `PQE_SECRET_KEY` di `.env` **sebelum** `up` pertama kali.
>
> Panduan integrasi untuk programmer aplikasi tersedia di http://localhost:8000/ui/guide.html (tanpa login).
>
> **Uji coba dari HP:** `http://<IP-PC>:8000/ui/coba.html`. Untuk kamera langsung di browser (butuh HTTPS) tambahkan
> `docker-compose.https.yml`: `PQE_HTTPS_HOST=<IP-PC> docker compose -f docker-compose.yml -f docker-compose.https.yml up -d`,
> lalu buka `https://<IP-PC>:8443/ui/coba.html` (sertifikat CA lokal Caddy, khusus uji coba). Foto simulasi masuk tenant
> `uji-coba` (retensi 30 hari). Di produksi, nonaktifkan tenant `uji-coba` lewat Web UI jika tidak dipakai.

Compose lokal memakai `PQE_ENV=development` agar `pqe demo-upload` boleh dijalankan, dan memasang folder `./backup`
(`NAS_MOUNT`) ke `/backup` untuk mencoba halaman **Backup NAS**.

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
#              PQE_BOOTSTRAP_ADMIN_KEY (acak panjang), PQE_SECRET_KEY (acak panjang), PUBLIC_BASE_URL
docker compose -f docker-compose.remote-silo.yml up -d --build
```

Alternatif tanpa mengedit `.env` untuk koneksi SILO: isi `PQE_BOOTSTRAP_ADMIN_KEY` dan `PQE_SECRET_KEY` saja, jalankan
compose, lalu buka Web UI → **Pengaturan SILO** (admin), tempel JSON `mc` atau isi endpoint/access key/secret key/bucket,
klik **Test koneksi**, lalu **Simpan**. Nilai di halaman itu menimpa env dan berlaku tanpa restart
(detail: [configuration.md §1.6](configuration.md#16-pengaturan-silo-lewat-web-ui)). Endpoint harus alamat yang bisa
dijangkau **dari container engine** (contoh `https://s3.pcpexpress.com`), **bukan** `127.0.0.1`.

Catatan:

- Seluruh `.env` dimuat ke container engine (`env_file: .env`). `DATABASE_URL` ditimpa compose agar mengarah ke Postgres di compose.
- `ENGINE_CREATE_BUCKETS` di `.env.example` bernilai `false`: bucket engine harus sudah dibuat tim infra (bagian 4).
- Engine hanya **membaca** `AWS_BUCKET` dan **menulis** ke `ENGINE_BUCKET_PROCESSED`, `ENGINE_BUCKET_THUMB`, `ENGINE_BUCKET_UPLOAD`. Ini dijaga di kode (pagar pengaman, lihat bagian 11): walau key yang dipakai punya akses penuh, engine menolak menulis/menghapus di bucket aplikasi, dan `PQE_ENV=production` (bawaan `.env.example`) menolak `pqe demo-upload`. Dengan integrasi "scan dulu, baru simpan", foto pengiriman yang diterima disimpan engine di `ENGINE_BUCKET_PROCESSED` (link `stored`) dan foto asli diarsipkan di `ENGINE_BUCKET_UPLOAD`; aplikasi tidak perlu memberi engine akses tulis ke bucket aplikasi.
- Backup NAS: mount share NAS di server, isi `NAS_MOUNT` di `.env` (dipasang ke `/backup`), lalu atur di Web UI → **Backup NAS** (bagian 6.2–6.3).
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

Isi `AWS_ACCESS_KEY_ID=pqe-engine` dan `AWS_SECRET_ACCESS_KEY=<secret>` di `.env` / K8s Secret, **atau** masukkan lewat Web UI → **Pengaturan SILO** (secret disimpan terenkripsi dengan `PQE_SECRET_KEY`). Jika foto aplikasi lain ada di bucket lain, tambahkan bucket itu ke dua statement pertama. Halaman Pengaturan SILO juga menampilkan contoh policy ini sesuai nama bucket yang diisi.

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
  --from-literal=PQE_S3_EVENT_TOKEN='***' \
  --from-literal=PQE_SECRET_KEY="$(openssl rand -hex 32)"

kubectl -n photo-quality create configmap pqe-tenants --from-file=config/tenants/ \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -k deploy/k8s
kubectl -n photo-quality get pods
```

Catatan:

- `PQE_SECRET_KEY` harus **sama** di semua pod (API, worker, ingestor) dan **tidak boleh hilang**: secret key SILO yang disimpan lewat Web UI dienkripsi dengan kunci ini. Simpan salinannya di password manager tim. Jika berubah, secret di Pengaturan SILO harus diisi ulang (engine kembali memakai `AWS_SECRET_ACCESS_KEY` dari env sampai diisi ulang).
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

### 6.2 Backup NAS otomatis (Web UI → Backup NAS)

Engine menyalin sendiri foto dan database ke NAS secara terjadwal. Pengaturannya di Web UI menu **Backup NAS** (role
`admin`), tersimpan di database (secret NAS S3 terenkripsi dengan `PQE_SECRET_KEY`), dan dijalankan oleh worker (`pqe run`
atau `pqe worker`): loop pemeliharaan worker memeriksa jadwal dan permintaan manual setiap ±10 detik. Jika ada beberapa
worker, hanya satu yang menjalankan backup dalam satu waktu (lease 15 menit di tabel `settings`).

**Tujuan (pilih satu):**

| Jenis | Kapan dipakai | Yang diisi |
|---|---|---|
| **Folder** (bawaan) | Share NAS (SMB/NFS) di-mount di server, lalu dipasang ke container | `folder_path` di dalam container, bawaan `/backup` (= `NAS_MOUNT` di server, lihat 6.3) |
| **NAS S3-compatible** | NAS punya layanan S3 (Synology, QNAP, MinIO di NAS) | endpoint, bucket, prefix (opsional), region, access key, secret key, gaya alamat (`path` disarankan) |

**Yang dibackup (bawaan):**

| Pilihan | Bawaan | Keterangan |
|---|:-:|---|
| Foto asli dari kurir (`include_upload_originals`) | ✅ | Foto asli di bucket engine (`photos-upload`, alur "scan dulu"). Bukti keaslian (hash, EXIF) |
| Foto pengiriman yang diterima (`include_processed`) | ✅ | Versi di `photos-processed` yang link-nya disimpan aplikasi (`stored.url`) |
| Database engine (`include_database`) | ✅ | `pg_dump --format=custom` (PostgreSQL) atau salinan konsisten SQLite |
| Foto asli di bucket aplikasi (`include_app_originals`) | ❌ | Aktifkan jika bucket aplikasi belum dibackup tim infra (lihat 6.5) |
| Thumbnail (`include_thumbs`) | ❌ | Bisa dibuat ulang |

**Jadwal (bawaan):** foto setiap **15 menit** (`photo_interval_minutes`), database setiap **24 jam** (`db_interval_hours`),
simpan **14** dump terakhir (`db_keep`). Tombol **Backup foto sekarang** / **Backup database sekarang** meminta worker
menjalankannya di putaran berikutnya (±10 detik), juga saat jadwal dimatikan.

**Tata letak di NAS:**

```
photos/<bucket>/<key>              contoh photos/photos-processed/courier-app/2026/10/23/<id>.jpg
database/pqe-<YYYYmmdd-HHMMSS>.dump   (PostgreSQL)  atau  database/pqe-<...>.sqlite (SQLite)
```

**Sifat backup:**

- **Hanya menyalin.** Foto di NAS tidak pernah dihapus engine. Penghapusan di SILO (retensi 3 tahun, lifecycle) **tidak**
  ikut diterapkan ke NAS. Satu-satunya yang dihapus adalah dump database lama di luar `db_keep`.
- **Inkremental.** Foto yang sudah tersalin dicatat di tabel `backup_items` dan tidak disalin lagi. File yang sudah ada
  di NAS dengan ukuran sama dilewati. Hanya foto berstatus selesai (`done`/`error`) yang disalin; foto yang masih antre
  menyusul di putaran berikutnya.
- Satu putaran foto dibatasi ±50 detik agar worker tetap responsif; sisanya dilanjutkan putaran berikutnya.
- Jika NAS tidak bisa dijangkau, putaran berhenti, error tercatat (Web UI dan metrik), dan foto tetap dihitung
  "belum tersalin" sampai berhasil.
- Image engine sudah memuat `postgresql-client` (pg_dump 17, kompatibel dengan PostgreSQL 14–17).

Tombol **Test NAS** memeriksa: folder/bucket tujuan ada, tulis-baca-hapus file uji, ruang kosong (> 1 GB, tujuan folder),
akses baca bucket `photos-upload` dan `photos-processed`, dan kesiapan dump database. API: `GET/PUT
/v1/admin/settings/backup`, `POST /v1/admin/settings/backup/test`, `POST /v1/admin/backup/run`
([api-reference.md](api-reference.md#backup-nas)).

### 6.3 Mount NAS untuk tujuan folder

`docker-compose.yml` dan `docker-compose.remote-silo.yml` memasang folder `NAS_MOUNT` di server (bawaan `./backup`) ke
`/backup` di container. Mount share NAS di server, lalu arahkan `NAS_MOUNT` ke sana:

**Linux (SMB/CIFS):**

```bash
sudo mkdir -p /mnt/nas
sudo mount -t cifs //192.168.1.20/pqe-backup /mnt/nas -o username=pqe-backup,uid=10001,gid=10001,file_mode=0660,dir_mode=0770
```

Lalu di `.env`: `NAS_MOUNT=/mnt/nas`, dan `docker compose up -d`. Supaya permanen, tambahkan baris yang sama di `/etc/fstab`
(simpan password di file credentials, bukan di fstab). Untuk NFS: `sudo mount -t nfs 192.168.1.20:/volume1/pqe-backup /mnt/nas`.

**macOS (Docker Desktop):** buka share di Finder (**Go → Connect to Server** → `smb://192.168.1.20/pqe-backup`). Share
muncul di `/Volumes/pqe-backup`; isi `NAS_MOUNT=/Volumes/pqe-backup` lalu `docker compose up -d`.

**Izin tulis:** engine berjalan sebagai user **uid 10001** di container. Folder harus bisa ditulis uid itu: `uid=10001` pada
opsi mount SMB seperti di atas, atau `sudo chown -R 10001 /mnt/nas` (NFS/folder lokal). Tanpa itu, **Test NAS** menampilkan
pesan "tidak bisa ditulis engine (user uid 10001)".

**Kubernetes:** pasang PVC atau volume NFS di `/backup` pada pod **worker** (yang menjalankan backup) dan pod **API**
(tombol Test NAS dijalankan di pod API). Contoh potongan spec pod:

```yaml
volumes:
  - name: nas
    nfs: {server: 192.168.1.20, path: /volume1/pqe-backup}
containers:
  - name: worker
    volumeMounts:
      - {name: nas, mountPath: /backup}
```

Pod berjalan sebagai uid 10001, jadi export NFS harus mengizinkan uid itu menulis.

### 6.4 Restore

**Foto** (salin balik ke SILO dengan struktur yang sama):

```bash
mcli alias set pcp https://s3.pcpexpress.com ADMIN_KEY ADMIN_SECRET
mcli mirror /mnt/nas/photos/photos-processed pcp/photos-processed
mcli mirror /mnt/nas/photos/photos-upload    pcp/photos-upload
mcli cp /mnt/nas/photos/photos-processed/courier-app/2026/10/23/<id>.jpg pcp/photos-processed/courier-app/2026/10/23/<id>.jpg
```

Untuk tujuan NAS S3, ganti sumber dengan alias NAS (`mcli alias set nas http://192.168.1.20:9000 ...`, lalu
`mcli mirror nas/pqe-backup/photos/photos-processed pcp/photos-processed`; tambahkan prefix jika diisi).

**Database (PostgreSQL):**

```bash
pg_restore --clean --if-exists --no-owner -h postgres-host -U pqe -d pqe /mnt/nas/database/pqe-20261023-020000.dump
```

**Database (SQLite, hanya uji lokal):** hentikan engine, salin `database/pqe-<...>.sqlite` ke lokasi `DATABASE_URL`, start lagi.

Uji restore **bulanan** ke database terpisah:

```bash
createdb -h staging-host -U postgres pqe_restore_test
pg_restore --no-owner -h staging-host -U postgres -d pqe_restore_test /mnt/nas/database/pqe-YYYYmmdd-HHMMSS.dump
psql -h staging-host -U postgres -d pqe_restore_test -c "select count(*), max(received_at) from photos;"
```

### 6.5 Tambahan di sisi infra

- **Point-in-time recovery database:** dump harian berarti kehilangan maksimal 24 jam data analisis. Untuk lebih rapat,
  aktifkan WAL archiving di server Postgres (`archive_mode=on`, `archive_command` ke NAS) atau fitur PITR layanan Postgres.
- **Foto asli di bucket aplikasi:** jika tim infra sudah mem-backup bucket aplikasi, biarkan `include_app_originals` mati.
  Alternatif di sisi infra (sesuai [sdlc-plan.md](sdlc-plan.md) 4.11):

  ```bash
  mcli alias set nas https://nas-silo.pcpexpress.local NAS_USER NAS_PASSWORD
  mcli mirror --watch pcp/ekas-pcptransport nas/ekas-pcptransport
  ```

  Jalankan sebagai service (systemd / Deployment terpisah).
- **Versioning & object lock** di SILO (bagian 4.5) tetap disarankan sebagai perlindungan dari penghapusan tidak sengaja
  dan ransomware. NAS sebaiknya di lokasi berbeda dari server SILO.

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
| `pqe_backup_pending` | gauge | - | Foto yang belum tersalin ke NAS (diperbarui setiap putaran backup di worker) |
| `pqe_backup_files_total` | counter | `kind` (`original`/`processed`/`thumbnail`/`database`) | File yang disalin ke NAS |
| `pqe_backup_errors_total` | counter | `kind` (`photos`/`database`) | Kegagalan backup ke NAS |

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

      - alert: PqeBackupNasGagal
        expr: increase(pqe_backup_errors_total[1h]) > 0
        labels: {severity: warning}
        annotations:
          summary: "Backup NAS gagal ({{ $labels.kind }})"
          description: "Web UI -> Backup NAS menampilkan pesan error terakhir. Cek mount NAS / koneksi NAS S3."

      - alert: PqeBackupNasTertinggal
        expr: pqe_backup_pending > 500 and deriv(pqe_backup_pending[2h]) > 0
        for: 2h
        labels: {severity: warning}
        annotations:
          summary: "Foto belum tersalin ke NAS terus bertambah"
          description: "Backup tidak berjalan (jadwal mati, worker mati, NAS penuh/tidak terjangkau)."

      - alert: PqeTidakReady
        expr: probe_success{job="pqe-readyz"} == 0
        for: 5m
        labels: {severity: critical}
        annotations:
          summary: "/readyz gagal: engine tidak bisa mengakses DB atau SILO"
```

Alert terakhir mengasumsikan blackbox exporter dengan job `pqe-readyz` yang memeriksa `https://<host>/readyz`. Di Kubernetes, readiness probe `pqe-api` sudah memakai `/readyz`.

Pantau juga kapasitas SILO/NAS (alert > 80%; ruang kosong tujuan folder juga terlihat di Web UI → Backup NAS) dan,
jika dipakai, status `mcli mirror` di sisi infra. `pqe_backup_pending` dan `pqe_backup_*_total` muncul di endpoint
metrik proses worker (`pqe run` di :8000, `pqe worker` di :9100).

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
| Rotasi kredensial SILO | Rotasi berkala / kebocoran | `mcli admin user add pcp pqe-engine-2 SECRET_BARU` + `mcli admin policy attach pcp pqe-engine --user pqe-engine-2`. Lalu **salah satu**: (a) Web UI → **Pengaturan SILO** → isi access key & secret key baru → **Test koneksi** → **Simpan** (tanpa restart, berlaku ≤ 10 detik), atau (b) update secret K8s/`.env` dan restart semua pod. Verifikasi `/readyz`, lalu `mcli admin user remove pcp pqe-engine`. Catatan: nilai di Pengaturan SILO menimpa env, jadi jika pernah disimpan di sana, rotasi juga harus lewat sana (atau **kembalikan ke env**) |
| Semua job gagal dengan `SignatureDoesNotMatch` / `AccessDenied` / `EndpointConnectionError` setelah ada yang mengubah Pengaturan SILO | Nilai salah tersimpan di Pengaturan SILO | Web UI → **Pengaturan SILO** → perbaiki atau klik **kembalikan ke env** pada field yang salah → **Test koneksi**. Siapa yang mengubah: audit log `settings.storage.update`. Setelah pulih: **Ulangi semua** job dead |
| `PQE_SECRET_KEY` hilang / berubah | Secret K8s dibuat ulang tanpa nilai lama | Secret key SILO yang tersimpan tidak bisa dibuka; engine memakai `AWS_SECRET_ACCESS_KEY` env (jika ada). Isi ulang secret key di **Pengaturan SILO**, atau kembalikan nilai `PQE_SECRET_KEY` lama |
| Backup NAS gagal / "Foto belum tersalin" terus naik | NAS tidak ter-mount, penuh, kredensial NAS S3 salah, jadwal mati, worker mati | Web UI → **Backup NAS** → baca error terakhir → **Test NAS**. Perbaiki mount/izin (6.3) atau kredensial, lalu **Backup foto sekarang**. Foto yang belum tersalin tidak hilang; disalin begitu NAS kembali |
| Perlu memulihkan foto / database | SILO rusak, objek terhapus, DB rusak | Restore dari NAS (6.4). Setelah DB dipulihkan, jalankan `pqe reconcile` agar foto yang masuk setelah dump terakhir tercatat lagi |
| Config tenant rusak / salah | Edit YAML keliru | Web UI -> Tenant -> Riwayat versi, salin versi lama, simpan. Config yang gagal di-parse dilewati (tenant lain tetap jalan) dan tercatat di log |

---

## 10. Upgrade & rollback

1. Baca catatan rilis / diff, terutama perubahan `engine/db.py` (skema) dan `requirements.lock`.
2. Pastikan CI hijau (lint, test engine, test client di runtime yang sudah di-patch, build image).
3. Backup database sebelum upgrade produksi: Web UI → **Backup NAS** → **Backup database sekarang** (atau `pg_dump` manual).
4. Deploy ke staging (bucket staging), jalankan smoke test: `/readyz`, `pqe demo-upload --bucket <bucket-staging> --force`
   (`--force` karena `PQE_ENV=production`; **jangan** ke bucket produksi), cek Web UI.
5. Produksi: ganti tag image di `kustomization.yaml` lalu `kubectl apply -k deploy/k8s`. Rolling update tidak berdampak ke kurir.
6. Pantau 24 jam: antrean, error rate, distribusi verdict.

**Rollback:** `kubectl -n photo-quality rollout undo deploy/pqe-api deploy/pqe-worker deploy/pqe-ingestor`, atau kembalikan tag image. Karena skema saat ini hanya *menambah* tabel (`create_all`), versi lama tetap bisa jalan di DB yang sama. Jika suatu rilis mengubah skema secara tidak kompatibel, restore dari backup langkah 3.

Docker compose: `git pull && docker compose up -d --build`. Rollback: checkout versi sebelumnya lalu `up -d --build`.

---

## 11. Checklist keamanan

- [ ] `PQE_BOOTSTRAP_ADMIN_KEY` diganti nilai acak panjang (bukan `pqe_local_admin_change_me`). Setelah admin personal dibuat, pertimbangkan merotasi/mencabut key bootstrap.
- [ ] `PQE_S3_EVENT_TOKEN` diisi (endpoint event menerima tanpa token jika kosong).
- [ ] `PQE_SECRET_KEY` diisi nilai acak panjang (`openssl rand -hex 32`, bukan `local-secret-key-change-me` dari compose lokal), sama di semua pod, dan salinannya disimpan aman. Kunci ini mengenkripsi secret key SILO yang disimpan lewat Web UI.
- [ ] Halaman **Pengaturan SILO** hanya bisa diakses role `admin`. Batasi jumlah admin key, dan cek audit log `settings.storage.update` secara berkala.
- [ ] Engine memakai **service account khusus** dengan policy minimum (bagian 4.2), bukan admin/root SILO.
- [ ] `PQE_ENV=production` (bawaan) di server. Pagar pengaman di engine: engine hanya bisa menulis/menghapus di bucket
      miliknya (`ENGINE_BUCKET_PROCESSED`, `ENGINE_BUCKET_THUMB`, `ENGINE_BUCKET_UPLOAD`, dan `upload_bucket` di config
      tenant). Bucket aplikasi tetap **read-only** walau key yang dipakai punya akses penuh, dan `pqe demo-upload` ditolak
      kecuali dengan `--force`. Pagar ini tambahan, bukan pengganti service account least privilege.
- [ ] **Backup NAS** aktif (Web UI → Backup NAS) untuk foto asli kurir, foto pengiriman, dan database, sudah di-**Test NAS**,
      dan restore sudah diuji (bagian 6). Akun NAS khusus backup, share NAS tidak bisa diakses umum.
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
