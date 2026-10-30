"""Thread worker modul dokumen: proses antrean document_jobs, kirim callback, retensi. Terpisah dari worker foto."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, select

from engine import metrics
from engine.db import AuditLog, session_scope, utcnow
from engine.documents import process, queue, signing, store
from engine.documents.models import DocumentIdempotency, DocumentJob, DocumentNonce
from engine.settings import get_settings

log = logging.getLogger(__name__)
CALLBACK_MAX_ATTEMPTS = 6


def callback_hosts(tenant: str | None = None) -> set[str]:
    """Host callback yang diizinkan: PQE_DOCS_CALLBACK_HOSTS (semua client) + `callback_hosts` di config tenant client
    itu (hanya untuk client itu: client lain tidak bisa mengarahkan callback ke host client ini)."""
    hosts = {h.strip().lower() for h in get_settings().docs_callback_hosts.split(",") if h.strip()}
    if tenant:
        from engine.tenants import TenantNotFound, registry

        try:
            hosts |= {h.strip().lower() for h in registry.get(tenant).config.callback_hosts if h.strip()}
        except TenantNotFound:
            pass
    return hosts


def callback_allowed(url: str, tenant: str | None = None) -> bool:
    """Cegah SSRF: hanya http(s) ke host yang diizinkan (callback_hosts), tanpa user:password. Entri `host` = port
    bawaan (80/443) saja; port lain harus ditulis eksplisit `host:port`."""
    try:
        u = urlsplit(url)
        port = u.port  # port tidak valid → ValueError
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or len(url) > 1000:
        return False
    host = u.hostname.lower()
    allowed = callback_hosts(tenant)
    return (host in allowed and port in (None, 80, 443)) or (port is not None and f"{host}:{port}" in allowed)


def deliver_callbacks(limit: int = 20, client: httpx.Client | None = None) -> int:
    with session_scope() as s:
        rows = s.scalars(select(DocumentJob).where(DocumentJob.callback_status == "pending",
                                                  DocumentJob.callback_next_at <= utcnow())
                         .order_by(DocumentJob.callback_next_at).limit(limit).with_for_update(skip_locked=True)).all()
        items = [(r.id, r.key_id, r.callback_url, r.status, r.document_type, r.tenant_code) for r in rows]
        for r in rows:
            r.callback_next_at = utcnow() + timedelta(seconds=60)
    if not items:
        return 0
    own = client is None
    client = client or httpx.Client(timeout=10, follow_redirects=False)
    try:
        for jid, key_id, url, status, dtype, tenant in items:
            err = None
            secret = signing.get_api_secret(key_id)
            if not secret or not callback_allowed(url, tenant):
                err = "secret/host tidak lagi berlaku"
            else:
                body = json.dumps({"event": "job.finished", "job_id": jid, "status": status,
                                   "document_type": dtype}, separators=(",", ":")).encode()
                ts = str(int(time.time()))
                path = urlsplit(url).path or "/"
                headers = {"Content-Type": "application/json", "User-Agent": "photo-quality-engine",
                           "X-Timestamp": ts, "X-Signature": signing.sign(secret, ts, "POST", path, body)}
                try:
                    r = client.post(url, content=body, headers=headers)
                    if not 200 <= r.status_code < 300:
                        err = f"HTTP {r.status_code}"
                except httpx.HTTPError as e:
                    err = type(e).__name__
            with session_scope() as s:
                j = s.get(DocumentJob, jid)
                j.callback_attempts += 1
                if err is None:
                    j.callback_status, j.callback_error = "done", None
                    metrics.DOCS_CALLBACKS.labels(result="ok").inc()
                else:
                    j.callback_error = err
                    if j.callback_attempts >= CALLBACK_MAX_ATTEMPTS:
                        j.callback_status = "dead"
                    else:
                        j.callback_next_at = utcnow() + timedelta(seconds=min(3600, 30 * 2 ** j.callback_attempts))
                    metrics.DOCS_CALLBACKS.labels(result="error").inc()
    finally:
        if own:
            client.close()
    return len(items)


def retention() -> dict[str, int]:
    st = get_settings()
    files = store.purge_older_than(st.docs_inbox_retention_days)
    with session_scope() as s:
        s.execute(delete(DocumentNonce).where(DocumentNonce.expires_at < utcnow()))
        s.execute(delete(DocumentIdempotency).where(DocumentIdempotency.created_at < utcnow() - timedelta(days=30)))
        r = s.execute(delete(DocumentJob).where(
            DocumentJob.created_at < utcnow() - timedelta(days=st.docs_result_retention_days),
            DocumentJob.status.in_(("succeeded", "failed"))))
        jobs = r.rowcount or 0
        if files or jobs:
            s.add(AuditLog(actor="system:retention", action="document.purge", detail={"files": files, "jobs": jobs}))
    return {"files": files, "jobs": jobs}


class DocumentWorker:
    def __init__(self, concurrency: int | None = None, stop_event: threading.Event | None = None) -> None:
        self.concurrency = concurrency if concurrency is not None else get_settings().docs_worker_concurrency
        self.stop_event = stop_event or threading.Event()

    def _loop(self) -> None:
        idle = get_settings().worker_poll_interval_sec
        while not self.stop_event.is_set():
            try:
                busy = process.run_one()
            except Exception:
                log.exception("worker dokumen error")
                busy = False
            if not busy:
                self.stop_event.wait(idle)

    def _maintenance(self) -> None:
        last_slow = 0.0
        while not self.stop_event.is_set():
            try:
                deliver_callbacks()
                now = time.monotonic()
                if now - last_slow >= 60:
                    last_slow = now
                    with session_scope() as s:
                        n = queue.requeue_stale(s)
                        metrics.DOCS_QUEUE_DEPTH.set(queue.queue_depth(s))
                    if n:
                        log.warning("%d job dokumen macet dikembalikan ke antrean", n)
                    retention()
            except Exception:
                log.exception("maintenance dokumen error")
            self.stop_event.wait(5.0)

    def start(self) -> list[threading.Thread]:
        threads = [threading.Thread(target=self._loop, name=f"document-{i}", daemon=True)
                   for i in range(self.concurrency)]
        threads.append(threading.Thread(target=self._maintenance, name="document-maintenance", daemon=True))
        for t in threads:
            t.start()
        log.info("worker dokumen berjalan (%d thread)", self.concurrency)
        return threads

    def run_until_empty(self, max_jobs: int = 1000) -> int:
        """Proses semua job lalu berhenti (CLI & test)."""
        n = 0
        while n < max_jobs and process.run_one():
            n += 1
        deliver_callbacks()
        return n
