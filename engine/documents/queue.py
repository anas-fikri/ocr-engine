"""Antrean job dokumen (tabel document_jobs, pola sama dengan ADR 0001: SELECT ... FOR UPDATE SKIP LOCKED)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from engine.db import as_aware, utcnow
from engine.documents.models import DocumentJob
from engine.jobs import WORKER_ID
from engine.settings import get_settings

MAX_ATTEMPTS = 3
# error pembacaan dokumen yang akan sama lagi jika diulang (bom, terenkripsi, terlalu besar): tidak di-retry
PERMANENT = {"UNSUPPORTED_MEDIA_TYPE", "UNREADABLE_FILE", "PDF_ENCRYPTED", "TOO_MANY_PAGES", "PAGE_TOO_LARGE",
             "IMAGE_TOO_LARGE", "RESOURCE_LIMIT", "PROCESSING_TIMEOUT", "FILE_EXPIRED", "OCR_UNAVAILABLE",
             "UNSUPPORTED_DOCUMENT_TYPE", "READ_ERROR"}


def pending_count(s: Session, tenant: str) -> int:
    return s.scalar(select(func.count()).select_from(DocumentJob)
                    .where(DocumentJob.tenant_code == tenant, DocumentJob.status.in_(("queued", "running")))) or 0


def claim(s: Session) -> DocumentJob | None:
    """Ambil job berikutnya secara adil antar-namespace: namespace yang paling lama tidak dilayani dulu, lalu job
    tertua di namespace itu. Satu namespace yang mengantre ratusan dokumen tidak membuat namespace lain menunggu."""
    now = utcnow()
    ready = (DocumentJob.status == "queued", DocumentJob.run_after <= now)
    tenants = s.scalars(select(DocumentJob.tenant_code).where(*ready).distinct().limit(100)).all()
    served = dict(s.execute(select(DocumentJob.tenant_code, func.max(DocumentJob.started_at))
                            .where(DocumentJob.tenant_code.in_(tenants)).group_by(DocumentJob.tenant_code)).all())
    order = sorted(tenants, key=lambda t: (served.get(t) is not None, as_aware(served.get(t)) or now))
    job = None
    for t in order + [None]:  # None = cadangan global (semua kandidat sedang dikunci worker lain)
        q = select(DocumentJob).where(*ready)
        if t is not None:
            q = q.where(DocumentJob.tenant_code == t)
        job = s.scalar(q.order_by(DocumentJob.created_at).limit(1).with_for_update(skip_locked=True))
        if job is not None:
            break
    if job is None:
        return None
    job.status, job.locked_at, job.locked_by = "running", now, WORKER_ID
    job.attempts += 1
    job.started_at = job.started_at or now
    return job


def _finished(job: DocumentJob) -> None:
    job.finished_at = utcnow()
    job.locked_at = None
    if job.callback_url:
        job.callback_status, job.callback_next_at = "pending", utcnow()


def succeed(job: DocumentJob, result: dict, layer: str, duration_ms: int) -> None:
    job.status, job.result, job.layer, job.duration_ms = "succeeded", result, layer, duration_ms
    job.error_code = job.error_message = None
    _finished(job)


def fail(job: DocumentJob, code: str, message: str) -> bool:
    """Error permanen atau percobaan habis → failed (return True). Selain itu antre ulang dengan jeda."""
    job.error_code, job.error_message = code, message[:2000]
    if code in PERMANENT or job.attempts >= MAX_ATTEMPTS:
        job.status = "failed"
        _finished(job)
        return True
    job.status, job.locked_at = "queued", None
    job.run_after = utcnow() + timedelta(seconds=30 * 2 ** (job.attempts - 1))
    return False


def requeue_stale(s: Session) -> int:
    cutoff = utcnow() - timedelta(seconds=get_settings().docs_sandbox_timeout_sec + 300)
    r = s.execute(update(DocumentJob).where(DocumentJob.status == "running", DocumentJob.locked_at < cutoff)
                  .values(status="queued", locked_at=None, locked_by=None))
    return r.rowcount or 0


def queue_depth(s: Session) -> int:
    return s.scalar(select(func.count()).select_from(DocumentJob).where(DocumentJob.status == "queued")) or 0
