"""File inbox dokumen invoice (ADR 0008: di PostgreSQL, tabel invoice_files).

Satu-satunya tempat yang tahu di mana file disimpan; jika kelak dipindah ke bucket SILO `ocr` (prefix `invoice/`),
cukup ganti fungsi di sini. Nama file dari klien tidak pernah dipakai sebagai path.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from engine.db import session_scope, utcnow
from engine.invoice.models import InvoiceFile


def put(s: Session, job_id: str, content: bytes) -> None:
    s.add(InvoiceFile(job_id=job_id, content=content))


def get(job_id: str) -> bytes | None:
    with session_scope() as s:
        return s.scalar(select(InvoiceFile.content).where(InvoiceFile.job_id == job_id))


def purge_older_than(days: int, batch: int = 500) -> int:
    """FR-OCR-010: hapus file inbox yang lebih tua dari retensi (hasil job tetap ada)."""
    cutoff = utcnow() - timedelta(days=days)
    with session_scope() as s:
        ids = s.scalars(select(InvoiceFile.job_id).where(InvoiceFile.created_at < cutoff).limit(batch)).all()
        if ids:
            s.execute(delete(InvoiceFile).where(InvoiceFile.job_id.in_(ids)))
        return len(ids)


def safe_filename(name: str | None) -> str | None:
    """Hanya untuk dicatat: basename, tanpa karakter kontrol, ≤ 200 karakter."""
    if not name:
        return None
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(c for c in base if c.isprintable())[:200].strip()
    return base or None
