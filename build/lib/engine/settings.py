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

    engine_bucket_processed: str = Field("photos-processed", alias="ENGINE_BUCKET_PROCESSED")
    engine_bucket_thumb: str = Field("photos-thumb", alias="ENGINE_BUCKET_THUMB")
    engine_bucket_upload: str = Field("photos-upload", alias="ENGINE_BUCKET_UPLOAD")
    engine_presign_ttl_ui: int = Field(900, alias="ENGINE_PRESIGN_TTL_UI")
    engine_presign_ttl_customer: int = Field(604800, alias="ENGINE_PRESIGN_TTL_CUSTOMER")
    engine_create_buckets: bool = Field(False, alias="ENGINE_CREATE_BUCKETS")

    # --- Database ---
    database_url: str = Field("sqlite:///./pqe.db", alias="DATABASE_URL")

    # --- API & keamanan ---
    public_base_url: str = Field("http://localhost:8000", alias="PUBLIC_BASE_URL")
    bootstrap_admin_key: str | None = Field(None, alias="PQE_BOOTSTRAP_ADMIN_KEY")
    s3_event_token: str | None = Field(None, alias="PQE_S3_EVENT_TOKEN")
    default_lang: str = Field("id", alias="PQE_DEFAULT_LANG")
    max_upload_mb: int = Field(25, alias="PQE_MAX_UPLOAD_MB")
    sync_timeout_ms: int = Field(3000, alias="PQE_SYNC_TIMEOUT_MS")

    # --- Worker & ingestor ---
    tenants_dir: Path = Field(Path("config/tenants"), alias="PQE_TENANTS_DIR")
    worker_poll_interval_sec: float = Field(1.0, alias="PQE_WORKER_POLL_INTERVAL_SEC")
    worker_concurrency: int = Field(2, alias="PQE_WORKER_CONCURRENCY")
    job_max_attempts: int = Field(5, alias="PQE_JOB_MAX_ATTEMPTS")
    job_stale_after_sec: int = Field(600, alias="PQE_JOB_STALE_AFTER_SEC")
    reconcile_interval_sec: int = Field(86400, alias="PQE_RECONCILE_INTERVAL_SEC")
    duplicate_lookback_days: int = Field(90, alias="PQE_DUPLICATE_LOOKBACK_DAYS")

    log_level: str = Field("INFO", alias="PQE_LOG_LEVEL")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
