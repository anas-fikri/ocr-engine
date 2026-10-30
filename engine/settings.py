"""Konfigurasi engine dari environment variable (lihat .env.example dan docs/configuration.md)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- SILO / S3 (nama variabel sama dengan aplikasi Laravel yang sudah ada) ---
    aws_endpoint: str = Field("https://s3.pcpexpress.com", alias="AWS_ENDPOINT")
    aws_public_endpoint: str | None = Field(None, alias="AWS_PUBLIC_ENDPOINT")
    aws_default_region: str = Field("us-east-1", alias="AWS_DEFAULT_REGION")
    aws_use_path_style_endpoint: bool = Field(True, alias="AWS_USE_PATH_STYLE_ENDPOINT")
    aws_access_key_id: str = Field("", alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str = Field("", alias="AWS_SECRET_ACCESS_KEY")
    aws_bucket: str = Field("ekas-pcptransport", alias="AWS_BUCKET")
    # auto | path | virtual. Kosong = ikut AWS_USE_PATH_STYLE_ENDPOINT (true -> path, false -> auto)
    aws_addressing_style: str | None = Field(None, alias="AWS_ADDRESSING_STYLE")
    aws_signature_version: str = Field("s3v4", alias="AWS_SIGNATURE_VERSION")

    # arsip engine (ADR 0007): salinan foto yang dinilai engine, per tenant: <tenant>/<yyyy>/<mm>/<dd>/<id>.<ext>
    engine_bucket_archive: str = Field("ocr", alias="ENGINE_BUCKET_ARCHIVE")
    engine_bucket_processed: str = Field("photos-processed", alias="ENGINE_BUCKET_PROCESSED")
    engine_bucket_thumb: str = Field("photos-thumb", alias="ENGINE_BUCKET_THUMB")
    engine_bucket_upload: str = Field("photos-upload", alias="ENGINE_BUCKET_UPLOAD")
    engine_presign_ttl_ui: int = Field(900, alias="ENGINE_PRESIGN_TTL_UI")
    engine_presign_ttl_customer: int = Field(604800, alias="ENGINE_PRESIGN_TTL_CUSTOMER")
    engine_create_buckets: bool = Field(False, alias="ENGINE_CREATE_BUCKETS")

    # production = pagar pengaman aktif (perintah demo ditolak). Lokal/compose: development.
    pqe_env: str = Field("production", alias="PQE_ENV")

    # --- Database ---
    database_url: str = Field("sqlite:///./pqe.db", alias="DATABASE_URL")

    # --- API & keamanan ---
    public_base_url: str = Field("http://localhost:8000", alias="PUBLIC_BASE_URL")
    # kunci enkripsi untuk rahasia yang disimpan lewat Web UI (contoh secret key SILO). String acak panjang.
    secret_key: str | None = Field(None, alias="PQE_SECRET_KEY")
    bootstrap_admin_key: str | None = Field(None, alias="PQE_BOOTSTRAP_ADMIN_KEY")
    s3_event_token: str | None = Field(None, alias="PQE_S3_EVENT_TOKEN")
    default_lang: str = Field("id", alias="PQE_DEFAULT_LANG")
    max_upload_mb: int = Field(25, alias="PQE_MAX_UPLOAD_MB")
    sync_timeout_ms: int = Field(3000, alias="PQE_SYNC_TIMEOUT_MS")
    # daftar origin dipisah koma, contoh "https://portal.pcpexpress.com". Kosong = CORS mati.
    cors_origins: str = Field("", alias="PQE_CORS_ORIGINS")

    # --- Worker & ingestor ---
    tenants_dir: Path = Field(Path("config/tenants"), alias="PQE_TENANTS_DIR")
    worker_poll_interval_sec: float = Field(1.0, alias="PQE_WORKER_POLL_INTERVAL_SEC")
    worker_concurrency: int = Field(2, alias="PQE_WORKER_CONCURRENCY")
    job_max_attempts: int = Field(5, alias="PQE_JOB_MAX_ATTEMPTS")
    job_stale_after_sec: int = Field(600, alias="PQE_JOB_STALE_AFTER_SEC")
    reconcile_interval_sec: int = Field(86400, alias="PQE_RECONCILE_INTERVAL_SEC")
    duplicate_lookback_days: int = Field(90, alias="PQE_DUPLICATE_LOOKBACK_DAYS")

    # port metrik Prometheus untuk proses `pqe worker` / `pqe ingestor` (API memakai /metrics di port API)
    metrics_port: int = Field(9100, alias="PQE_METRICS_PORT")
    log_level: str = Field("INFO", alias="PQE_LOG_LEVEL")

    # --- Modul dokumen (ADR 0008/0009, docs/document-ocr-design.md) ---
    docs_enabled: bool = Field(True, alias="PQE_DOCS_ENABLED")
    docs_worker_concurrency: int = Field(1, alias="PQE_DOCS_WORKER_CONCURRENCY")
    docs_signature_window_sec: int = Field(300, alias="PQE_DOCS_SIGNATURE_WINDOW_SEC")
    docs_max_upload_mb: int = Field(20, alias="PQE_DOCS_MAX_UPLOAD_MB")
    docs_max_pages: int = Field(20, alias="PQE_DOCS_MAX_PAGES")
    docs_render_dpi: int = Field(300, alias="PQE_DOCS_RENDER_DPI")
    docs_max_image_pixels: int = Field(50_000_000, alias="PQE_DOCS_MAX_IMAGE_PIXELS")
    docs_sandbox_memory_mb: int = Field(1536, alias="PQE_DOCS_SANDBOX_MEMORY_MB")
    docs_sandbox_timeout_sec: int = Field(180, alias="PQE_DOCS_SANDBOX_TIMEOUT_SEC")
    docs_ocr_page_timeout_sec: int = Field(30, alias="PQE_DOCS_OCR_PAGE_TIMEOUT_SEC")
    docs_max_pending_per_tenant: int = Field(200, alias="PQE_DOCS_MAX_PENDING_PER_TENANT")
    docs_inbox_retention_days: int = Field(30, alias="PQE_DOCS_INBOX_RETENTION_DAYS")
    docs_result_retention_days: int = Field(365, alias="PQE_DOCS_RESULT_RETENTION_DAYS")
    # off (bawaan, izin TBD-15 belum dikonfirmasi) | decode (QR dibaca lokal) | lookup (+ panggil validasi DJP)
    # plugin invoice: QR e-Faktur
    invoice_efaktur_qr: str = Field("off", alias="PQE_INVOICE_EFAKTUR_QR")
    invoice_efaktur_hosts: str = Field("svc.efaktur.pajak.go.id,efaktur.pajak.go.id", alias="PQE_INVOICE_EFAKTUR_HOSTS")
    # host tujuan callback_url yang diizinkan (dipisah koma). Kosong = callback ditolak (cegah SSRF)
    docs_callback_hosts: str = Field("", alias="PQE_DOCS_CALLBACK_HOSTS")
    # plugin jenis dokumen tambahan: "paket.modul:Kelas,..." (selain entry point pqe.document_extractors)
    document_plugins: str = Field("", alias="PQE_DOCUMENT_PLUGINS")


def addressing_style(s: Settings) -> str:
    if s.aws_addressing_style in ("auto", "path", "virtual"):
        return s.aws_addressing_style
    return "path" if s.aws_use_path_style_endpoint else "auto"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
