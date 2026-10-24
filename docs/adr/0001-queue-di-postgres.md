# ADR 0001: Antrean job di PostgreSQL, bukan Redis

- **Status:** Diterima (22 Okt 2026)
- **Menggantikan:** rencana "Queue: Redis (+ RQ/Celery/Arq)" di [sdlc-plan.md](../sdlc-plan.md) 3.10

## Konteks

Rencana awal memakai Redis sebagai antrean. Saat implementasi, kebutuhan nyatanya:

- Volume sekitar 1.000 foto/hari (bahkan 10× lipat pun hanya beberapa job per detik).
- Pencatatan foto dan pembuatan job harus **atomik**: foto tidak boleh tercatat tanpa job, atau sebaliknya.
- Jaminan "tidak ada foto yang terlewat" dan idempotensi (polling, event, dan reconciliation boleh tumpang tindih).
- Tim ops ingin komponen sesedikit mungkin di PC uji coba maupun K8s.

## Keputusan

Antrean disimpan di tabel `jobs` PostgreSQL (`engine/jobs.py`):

- Claim job dengan `SELECT ... FOR UPDATE SKIP LOCKED` sehingga banyak worker aman berjalan bersamaan.
- Foto + job dibuat dalam satu transaksi. Unique constraint `bucket + object_key` membuat enqueue idempotent.
- Retry dengan backoff eksponensial, status `dead` setelah `PQE_JOB_MAX_ATTEMPTS`. Job macet dikembalikan otomatis.
- Outbox webhook (`webhook_deliveries`) memakai pola yang sama.

## Konsekuensi

- (+) Satu komponen lebih sedikit (tidak ada Redis untuk dipasang, di-backup, dan dipantau).
- (+) Atomik & mudah diaudit: status job bisa di-query dengan SQL dan terlihat di Web UI.
- (+) Backup antrean ikut backup database.
- (−) Polling DB tiap `PQE_WORKER_POLL_INTERVAL_SEC` (default 1 detik) per thread worker. Pada volume saat ini bebannya tidak berarti.
- (−) Jika volume naik ratusan kali lipat, perlu dievaluasi ulang (misal `LISTEN/NOTIFY` atau broker khusus). Abstraksi `claim_job / finish_job / fail_job` memudahkan penggantian.
- SQLite hanya untuk uji lokal satu proses (`SKIP LOCKED` tidak berlaku di SQLite).

## Alternatif yang ditolak

- **Redis + RQ/Celery:** komponen tambahan, tidak atomik dengan pencatatan foto (butuh pola outbox juga), risiko job hilang jika Redis tanpa persistence.
- **RabbitMQ/Kafka:** berlebihan untuk volume ini dan menambah beban operasional.
