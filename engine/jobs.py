"""Antrean job berbasis database (SELECT ... FOR UPDATE SKIP LOCKED di PostgreSQL).

Alasan tidak memakai Redis: satu komponen lebih sedikit, transaksi foto+job atomik, dan
volume (ribuan foto/hari) jauh di bawah batas. Lihat docs/adr/0001-queue-di-postgres.md.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from engine.db import Job, Photo, new_id, session_scope, utcnow
from engine.settings import get_settings

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


@dataclass
class EnqueueResult:
    photo_id: str
    created: bool  # False = objek sudah pernah tercatat (idempotent)
    requeued: bool


def apply_mapped(p: Photo, meta: dict[str, Any], tz: str = "Asia/Jakarta") -> None:
    """Isi kolom photo dari hasil mapping metadata."""
    from engine.analyzers.exif import parse_meta_datetime

    p.awb = meta.get("awb")
    p.courier_id = meta.get("courier_id")
    p.photo_type = meta.get("photo_type")
    p.customer_id = meta.get("customer_id")
    p.branch = meta.get("branch")
    p.external_id = meta.get("external_id")
    p.capture_source = meta.get("source")
    p.captured_at = parse_meta_datetime(meta.get("captured_at"), tz)
    for attr in ("lat", "lng"):
        try:
            setattr(p, attr, float(meta[attr]) if meta.get(attr) not in (None, "") else None)
        except (TypeError, ValueError):
            setattr(p, attr, None)


def enqueue_object(
    tenant_code: str,
    bucket: str,
    key: str,
    *,
    ingest_source: str,
    fields: dict[str, Any] | None = None,
    mapped: dict[str, Any] | None = None,
    etag: str | None = None,
    retention_days: int | None = None,
    tz: str = "Asia/Jakarta",
    session: Session | None = None,
) -> EnqueueResult:
    """Catat foto + buat job. Aman dipanggil berulang untuk objek yang sama (idempotent).

    fields = field mentah dari pemanggil (API / baris DB), disimpan apa adanya di Photo.meta.
    mapped = hasil mapping awal (tanpa metadata S3) agar foto langsung bisa dicari per resi.
    Mapping final dilakukan worker setelah metadata objek S3 terbaca.
    """
    if session is None:
        with session_scope() as s:
            return enqueue_object(tenant_code, bucket, key, ingest_source=ingest_source, fields=fields,
                                  mapped=mapped, etag=etag, retention_days=retention_days, tz=tz, session=s)
    s = session
    existing = s.scalar(select(Photo).where(Photo.bucket == bucket, Photo.object_key == key))
    if existing is not None:
        # objek ditimpa (etag berubah) -> analisis ulang
        if etag and existing.etag and etag != existing.etag:
            existing.etag = etag
            existing.status = "pending"
            if fields:
                existing.meta = dict(fields)
            s.add(Job(photo_id=existing.id))
            return EnqueueResult(existing.id, False, True)
        if fields and not existing.meta:
            existing.meta = dict(fields)
        return EnqueueResult(existing.id, False, False)

    p = Photo(tenant_code=tenant_code, bucket=bucket, object_key=key, etag=etag, ingest_source=ingest_source,
              meta=dict(fields or {}))
    apply_mapped(p, mapped or fields or {}, tz)
    if retention_days:
        p.expires_at = utcnow() + timedelta(days=retention_days)
    try:
        with s.begin_nested():
            s.add(p)
            s.flush()
    except IntegrityError:  # ingestor lain mencatat objek yang sama bersamaan
        existing = s.scalar(select(Photo).where(Photo.bucket == bucket, Photo.object_key == key))
        return EnqueueResult(existing.id if existing else "", False, False)
    s.add(Job(photo_id=p.id))
    return EnqueueResult(p.id, True, False)


UNSTORED_BUCKET = ""  # foto upload: belum/tidak disimpan aplikasi ke SILO (lihat processor.bind_location)


def record_upload(tenant_code: str, *, fields: dict[str, Any], mapped: dict[str, Any] | None = None,
                  retention_days: int | None = None, tz: str = "Asia/Jakarta") -> str:
    """Catat foto yang dikirim langsung oleh aplikasi (tanpa job: dianalisis di request itu juga).

    Engine tidak menyimpan file-nya. Lokasi sementara `""/unstored/<id>` diganti bucket/key tempat aplikasi
    menyimpan foto (field `object_key` atau `POST /v1/photos/{id}/stored`).
    """
    with session_scope() as s:
        pid = new_id()
        p = Photo(id=pid, tenant_code=tenant_code, bucket=UNSTORED_BUCKET, object_key=f"unstored/{pid}",
                  ingest_source="upload", meta=dict(fields or {}))
        apply_mapped(p, mapped or fields or {}, tz)
        if retention_days:
            p.expires_at = utcnow() + timedelta(days=retention_days)
        s.add(p)
        return pid


def requeue_photo(photo_id: str, session: Session) -> None:
    p = session.get(Photo, photo_id)
    if p is None:
        raise KeyError(photo_id)
    p.status = "pending"
    session.add(Job(photo_id=photo_id))


def claim_job(session: Session) -> Job | None:
    now = utcnow()
    q = (
        select(Job)
        .where(Job.status == "queued", Job.run_after <= now)
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = session.scalar(q)
    if job is None:
        return None
    job.status = "running"
    job.locked_at = now
    job.locked_by = WORKER_ID
    job.attempts += 1
    return job


def claim_photo_job(session: Session, photo_id: str) -> Job | None:
    """Claim job milik foto tertentu (dipakai mode sinkron API)."""
    job = session.scalar(
        select(Job).where(Job.photo_id == photo_id, Job.status == "queued")
        .order_by(Job.id.desc()).limit(1).with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.status = "running"
    job.locked_at = utcnow()
    job.locked_by = WORKER_ID
    job.attempts += 1
    return job


def finish_job(session: Session, job: Job) -> None:
    job.status = "done"
    job.last_error = None


def fail_job(session: Session, job: Job, error: str) -> bool:
    """Retry dengan backoff eksponensial. Return True jika job menyerah (dead)."""
    max_attempts = get_settings().job_max_attempts
    job.last_error = error[:4000]
    if job.attempts >= max_attempts:
        job.status = "dead"
        return True
    job.status = "queued"
    job.run_after = utcnow() + timedelta(seconds=min(3600, 10 * 2 ** (job.attempts - 1)))
    job.locked_at = None
    return False


def requeue_stale(session: Session) -> int:
    """Job 'running' yang tertinggal (worker mati di tengah jalan) dikembalikan ke antrean."""
    cutoff = utcnow() - timedelta(seconds=get_settings().job_stale_after_sec)
    r = session.execute(
        update(Job).where(Job.status == "running", Job.locked_at < cutoff)
        .values(status="queued", locked_at=None, locked_by=None)
    )
    return r.rowcount or 0


def queue_stats(session: Session) -> dict[str, Any]:
    rows = session.execute(select(Job.status, func.count()).group_by(Job.status)).all()
    stats = {k: 0 for k in ("queued", "running", "done", "dead")}
    stats.update({status: n for status, n in rows})
    oldest = session.scalar(select(func.min(Job.created_at)).where(Job.status == "queued"))
    stats["oldest_queued_at"] = oldest.isoformat() if oldest else None
    return stats
