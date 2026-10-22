"""Loop worker: proses job analisis, kirim webhook, pemeliharaan (job macet, retensi, metrik)."""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from engine import metrics
from engine.db import AuditLog, Job, Photo, WebhookDelivery, as_aware, session_scope, utcnow
from engine.jobs import queue_stats, requeue_stale
from engine.processor import run_one
from engine.settings import get_settings
from engine.storage import Storage, get_storage
from engine.webhooks import deliver_pending

log = logging.getLogger(__name__)


def update_gauges() -> dict:
    with session_scope() as s:
        st = queue_stats(s)
        pending_wh = s.scalar(select(func.count()).select_from(WebhookDelivery)
                              .where(WebhookDelivery.status == "pending")) or 0
    metrics.QUEUE_DEPTH.set(st["queued"])
    metrics.DEAD_JOBS.set(st["dead"])
    metrics.PENDING_WEBHOOKS.set(pending_wh)
    oldest = st["oldest_queued_at"]
    lag = (datetime.now(UTC) - as_aware(datetime.fromisoformat(oldest))).total_seconds() if oldest else 0
    metrics.QUEUE_LAG.set(max(0.0, lag))
    return st


def cleanup_webhook_history(days: int = 30) -> int:
    """Hapus catatan pengiriman webhook yang sudah selesai/dead lebih dari `days` hari."""
    with session_scope() as s:
        r = s.execute(delete(WebhookDelivery).where(
            WebhookDelivery.status.in_(("done", "dead")),
            WebhookDelivery.created_at < utcnow() - timedelta(days=days),
        ))
        return r.rowcount or 0


def retention_sweep(storage: Storage, batch: int = 200) -> int:
    """Hapus data engine (varian + baris DB) yang melewati masa retensi.

    Foto original milik aplikasi TIDAK dihapus engine: atur lifecycle di bucket SILO
    (lihat docs/operations.md).
    """
    with session_scope() as s:
        rows = s.execute(
            select(Photo.id, Photo.processed_bucket, Photo.processed_key, Photo.thumb_bucket, Photo.thumb_key)
            .where(Photo.expires_at.is_not(None), Photo.expires_at < utcnow()).limit(batch)
        ).all()
    if not rows:
        return 0
    for _, pb, pk, tb, tk in rows:
        for b, k in ((pb, pk), (tb, tk)):
            if b and k:
                try:
                    storage.delete(b, k)
                except Exception as e:  # objek mungkin sudah dihapus lifecycle
                    log.debug("hapus %s/%s gagal: %s", b, k, e)
    ids = [r[0] for r in rows]
    with session_scope() as s:
        s.execute(delete(Job).where(Job.photo_id.in_(ids)))
        s.execute(delete(Photo).where(Photo.id.in_(ids)))
        s.add(AuditLog(actor="system:retention", action="retention.purge", detail={"count": len(ids)}))
    return len(ids)


class Worker:
    def __init__(self, concurrency: int | None = None, storage: Storage | None = None) -> None:
        self.concurrency = concurrency or get_settings().worker_concurrency
        self.storage = storage
        self.stop_event = threading.Event()

    def _job_loop(self) -> None:
        idle = get_settings().worker_poll_interval_sec
        while not self.stop_event.is_set():
            try:
                busy = run_one(self.storage or get_storage())
            except Exception:
                log.exception("worker loop error")
                busy = False
            if not busy:
                self.stop_event.wait(idle)

    def _maintenance_loop(self) -> None:
        last_slow = 0.0
        while not self.stop_event.is_set():
            try:
                deliver_pending()
                now = time.monotonic()
                if now - last_slow >= 30:
                    with session_scope() as s:
                        n = requeue_stale(s)
                    if n:
                        log.warning("%d job macet dikembalikan ke antrean", n)
                    update_gauges()
                    retention_sweep(self.storage or get_storage())
                    cleanup_webhook_history()
                    last_slow = now
            except Exception:
                log.exception("maintenance error")
            self.stop_event.wait(2.0)

    def start(self) -> list[threading.Thread]:
        threads = [threading.Thread(target=self._job_loop, name=f"worker-{i}", daemon=True)
                   for i in range(self.concurrency)]
        threads.append(threading.Thread(target=self._maintenance_loop, name="maintenance", daemon=True))
        for t in threads:
            t.start()
        log.info("worker berjalan (%d thread analisis)", self.concurrency)
        return threads

    def run_until_empty(self, max_jobs: int = 10_000) -> int:
        """Proses semua job yang ada lalu berhenti (dipakai CLI & test)."""
        n = 0
        while n < max_jobs and run_one(self.storage or get_storage()):
            n += 1
        deliver_pending()
        return n
