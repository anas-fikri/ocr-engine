"""Metrik Prometheus (endpoint /metrics). Lihat docs/operations.md untuk alert yang disarankan."""

from prometheus_client import Counter, Gauge, Histogram

PHOTOS_INGESTED = Counter("pqe_photos_ingested_total", "Foto baru tercatat", ["tenant", "source"])
PHOTOS_ANALYZED = Counter("pqe_photos_analyzed_total", "Foto selesai dianalisis", ["tenant", "verdict"])
PHOTOS_FAILED = Counter("pqe_photos_failed_total", "Foto gagal diproses", ["tenant", "code"])
ARCHIVE = Counter("pqe_archive_total", "Penyimpanan ke arsip engine (bucket ocr)", ["result"])  # ok|retry|failed|purged
JOBS_DEAD = Counter("pqe_jobs_dead_total", "Job yang menyerah setelah semua retry")
ANALYSIS_SECONDS = Histogram(
    "pqe_analysis_seconds", "Durasi proses satu foto", buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32)
)
WEBHOOKS_SENT = Counter("pqe_webhooks_total", "Pengiriman webhook", ["result"])
SOURCE_ERRORS = Counter("pqe_source_errors_total", "Error saat membaca source", ["tenant", "type"])
QUEUE_DEPTH = Gauge("pqe_queue_depth", "Job menunggu di antrean")
QUEUE_LAG = Gauge("pqe_queue_lag_seconds", "Umur job tertua di antrean")
DEAD_JOBS = Gauge("pqe_dead_jobs", "Jumlah job dead (perlu ditinjau)")
PENDING_WEBHOOKS = Gauge("pqe_pending_webhooks", "Webhook menunggu dikirim")
BACKUP_PENDING = Gauge("pqe_backup_pending", "Foto yang belum tersalin ke NAS")
BACKUP_FILES = Counter("pqe_backup_files_total", "File yang disalin ke NAS", ["kind"])
BACKUP_ERRORS = Counter("pqe_backup_errors_total", "Kegagalan backup ke NAS", ["kind"])
DOCS_JOBS = Counter("pqe_document_jobs_total", "Job dokumen selesai", ["tenant", "type", "status", "layer"])
DOCS_SECONDS = Histogram(
    "pqe_document_seconds", "Durasi proses satu dokumen", buckets=(1, 2, 4, 8, 16, 32, 64, 128, 256)
)
DOCS_QUEUE_DEPTH = Gauge("pqe_document_queue_depth", "Job dokumen menunggu di antrean")
DOCS_AUTH_FAILURES = Counter("pqe_document_auth_failures_total", "Request modul dokumen ditolak autentikasi", ["code"])
DOCS_CALLBACKS = Counter("pqe_document_callbacks_total", "Pengiriman callback job dokumen", ["result"])
