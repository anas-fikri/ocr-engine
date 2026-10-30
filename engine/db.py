"""Model database (SQLAlchemy 2.0). PostgreSQL di produksi, SQLite untuk uji lokal/test."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from engine.settings import get_settings

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    config_yaml: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TenantConfigHistory(Base):
    __tablename__ = "tenant_config_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_code: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    config_yaml: Mapped[str] = mapped_column(Text)
    changed_by: Mapped[str | None] = mapped_column(String(200))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(16))  # admin | ops | viewer | app | customer
    tenant_code: Mapped[str | None] = mapped_column(String(64))
    customer_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Photo(Base):
    __tablename__ = "photos"
    __table_args__ = (
        UniqueConstraint("bucket", "object_key", name="uq_photo_object"),
        Index("ix_photo_tenant_received", "tenant_code", "received_at"),
        Index("ix_photo_review_queue", "tenant_code", "verdict", "review_status"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_code: Mapped[str] = mapped_column(String(64), index=True)
    bucket: Mapped[str] = mapped_column(String(255))
    object_key: Mapped[str] = mapped_column(String(1024))
    etag: Mapped[str | None] = mapped_column(String(100))
    ingest_source: Mapped[str] = mapped_column(String(20))  # s3_poll | s3_event | db_poll | api | upload
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending|done|error

    # metadata hasil mapping
    awb: Mapped[str | None] = mapped_column(String(100), index=True)
    courier_id: Mapped[str | None] = mapped_column(String(100), index=True)
    photo_type: Mapped[str | None] = mapped_column(String(50))
    customer_id: Mapped[str | None] = mapped_column(String(100), index=True)
    branch: Mapped[str | None] = mapped_column(String(100), index=True)
    external_id: Mapped[str | None] = mapped_column(String(200), index=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    capture_source: Mapped[str | None] = mapped_column(String(20))  # camera | gallery
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)

    # hasil analisis
    profile: Mapped[str | None] = mapped_column(String(50))
    config_version: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    phash: Mapped[str | None] = mapped_column(String(16))
    verdict: Mapped[str | None] = mapped_column(String(10), index=True)
    action: Mapped[str | None] = mapped_column(String(10))
    scores: Mapped[dict] = mapped_column(JSONType, default=dict)
    reasons: Mapped[list] = mapped_column(JSONType, default=list)
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)
    exif: Mapped[dict] = mapped_column(JSONType, default=dict)
    compression: Mapped[dict] = mapped_column(JSONType, default=dict)
    image_info: Mapped[dict] = mapped_column(JSONType, default=dict)
    processed_bucket: Mapped[str | None] = mapped_column(String(255))
    processed_key: Mapped[str | None] = mapped_column(String(1024))
    thumb_bucket: Mapped[str | None] = mapped_column(String(255))
    thumb_key: Mapped[str | None] = mapped_column(String(1024))
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    # review ops
    review_status: Mapped[str] = mapped_column(String(20), default="none")  # none|approved|retake_requested|fraud
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)

    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_job_claim", "status", "run_after"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    photo_id: Mapped[str] = mapped_column(String(32), ForeignKey("photos.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(10), default="queued")  # queued | running | done | dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    photo_id: Mapped[str] = mapped_column(String(32), ForeignKey("photos.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_delivery_claim", "status", "next_attempt_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_code: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    event: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSONType)
    sink_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | done | dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceState(Base):
    __tablename__ = "source_state"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)  # "<tenant>:<index>:<type>"
    checkpoint: Mapped[str | None] = mapped_column(String(500))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconcile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_found: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class Setting(Base):
    """Pengaturan yang bisa diubah lewat Web UI (contoh koneksi SILO). Menimpa nilai env."""

    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BackupItem(Base):
    """Foto yang file-nya sudah tersalin ke NAS (lihat engine/backup.py)."""

    __tablename__ = "backup_items"
    photo_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    files: Mapped[int] = mapped_column(Integer, default=0)
    backed_up_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str | None] = mapped_column(String(300))
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = get_settings().database_url
        kwargs: dict = {"pool_pre_ping": True, "future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(conn, _):  # pragma: no cover - sqlite only
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Dipakai test: buang koneksi lama agar DATABASE_URL baru terbaca."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def init_db() -> None:
    import engine.invoice.models  # noqa: F401  tabel modul invoice (ADR 0008)

    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    s = _SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def is_postgres() -> bool:
    return get_engine().dialect.name == "postgresql"


def as_aware(dt: datetime | None) -> datetime | None:
    """SQLite mengembalikan datetime naive; anggap UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
