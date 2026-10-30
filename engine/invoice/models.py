"""Tabel modul invoice. Semua tabel baru: tabel foto POD tidak diubah (dibuat otomatis oleh init_db)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from engine.db import Base, JSONType, new_id, utcnow


class ApiKeySecret(Base):
    """`api_secret` untuk tanda tangan HMAC, terenkripsi dengan PQE_SECRET_KEY (engine/secretbox.py)."""

    __tablename__ = "api_key_secrets"
    key_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    secret_enc: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InvoiceJob(Base):
    __tablename__ = "invoice_jobs"
    __table_args__ = (
        Index("ix_invoice_job_claim", "status", "run_after"),
        Index("ix_invoice_job_tenant", "tenant_code", "created_at"),
        Index("ix_invoice_job_callback", "callback_status", "callback_next_at"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_code: Mapped[str] = mapped_column(String(64))
    key_id: Mapped[str] = mapped_column(String(32))  # API key pembuat job (secret callback)
    document_type: Mapped[str] = mapped_column(String(50))
    template_hint: Mapped[str | None] = mapped_column(String(32))
    filename: Mapped[str | None] = mapped_column(String(200))
    media_type: Mapped[str] = mapped_column(String(50))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10), default="queued")  # queued | running | succeeded | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    layer: Mapped[str | None] = mapped_column(String(20))
    result: Mapped[dict | None] = mapped_column(JSONType)
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    callback_url: Mapped[str | None] = mapped_column(String(1000))
    callback_status: Mapped[str | None] = mapped_column(String(10))  # pending | done | dead
    callback_attempts: Mapped[int] = mapped_column(Integer, default=0)
    callback_next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    callback_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InvoiceFile(Base):
    """File inbox. Dipisah dari invoice_jobs agar polling status tidak membaca blob; dihapus setelah retensi."""

    __tablename__ = "invoice_files"
    job_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class InvoiceTemplate(Base):
    __tablename__ = "invoice_templates"
    __table_args__ = (UniqueConstraint("tenant_code", "document_type", "issuer_npwp", name="uq_invoice_template"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_code: Mapped[str] = mapped_column(String(64))
    document_type: Mapped[str] = mapped_column(String(50))
    issuer_npwp: Mapped[str] = mapped_column(String(16))
    rules: Mapped[dict] = mapped_column(JSONType, default=dict)  # {field: aturan} lihat templates.py
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class InvoiceCorrection(Base):
    """Riwayat koreksi pengguna. Unique per (tenant, idempotency_key): setiap key diterapkan paling banyak sekali."""

    __tablename__ = "invoice_corrections"
    __table_args__ = (UniqueConstraint("tenant_code", "idempotency_key", name="uq_invoice_correction_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_code: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    template_id: Mapped[int | None] = mapped_column(Integer, index=True)
    job_id: Mapped[str] = mapped_column(String(32), index=True)
    field: Mapped[str] = mapped_column(String(50))
    extracted: Mapped[dict | list | str | int | None] = mapped_column(JSONType, nullable=True)
    corrected: Mapped[dict | list | str | int | None] = mapped_column(JSONType, nullable=True)
    learned: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InvoiceIdempotency(Base):
    """Respons POST /v1/templates per header Idempotency-Key (request diulang → respons yang sama)."""

    __tablename__ = "invoice_idempotency"
    __table_args__ = (UniqueConstraint("tenant_code", "key", name="uq_invoice_idempotency"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_code: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(128))
    body_sha256: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class InvoiceNonce(Base):
    """Tanda tangan POST /v1/jobs yang sudah diterima, sampai jendela waktunya lewat. Request yang sama persis
    (replay atau retry jaringan) mengembalikan job yang sama, bukan job baru."""

    __tablename__ = "invoice_nonces"
    signature_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_code: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
