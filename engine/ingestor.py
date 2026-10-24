"""Ingestor: menemukan foto baru dari SILO (polling / bucket event) dan DB aplikasi (read-only).

Semua jalur bermuara ke `enqueue_object` yang idempotent, jadi polling, event, dan
reconciliation boleh tumpang tindih tanpa menghasilkan proses ganda.
"""

from __future__ import annotations

import logging
import os
import posixpath
import threading
from datetime import datetime, timedelta
from functools import lru_cache
from urllib.parse import unquote_plus
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select, text

from engine import metrics
from engine.analyzers.exif import parse_meta_datetime
from engine.db import Photo, SourceState, session_scope, utcnow
from engine.jobs import enqueue_object
from engine.mapping import map_metadata
from engine.settings import get_settings
from engine.storage import Storage, get_storage, split_url
from engine.tenant_config import MAPPABLE_FIELDS, Source
from engine.tenants import LoadedTenant, registry

log = logging.getLogger(__name__)


def state_id(tenant: str, idx: int, src: Source) -> str:
    return f"{tenant}:{idx}:{src.type}"


def _prefixes(src: Source, tz: str, days: int) -> list[str]:
    if not src.date_prefix_format:
        return [src.prefix]
    today = datetime.now(ZoneInfo(tz)).date()
    seen, out = set(), []
    for d in range(days + 1):
        p = src.prefix + (today - timedelta(days=d)).strftime(src.date_prefix_format)
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _wanted(key: str, src: Source) -> bool:
    if key.endswith("/"):
        return False
    return posixpath.splitext(key)[1].lower() in {s.lower() for s in src.suffixes}


def poll_s3(t: LoadedTenant, idx: int, src: Source, storage: Storage, *, reconcile: bool = False) -> int:
    """List bucket/prefix, catat objek yang belum dikenal. Return jumlah foto baru."""
    cfg = t.config
    bucket = src.bucket or storage.s.aws_bucket
    days = max(src.lookback_days, 7) if reconcile else src.lookback_days
    found = 0
    for prefix in _prefixes(src, cfg.timezone, days):
        batch: list = []
        for obj in storage.list(bucket, prefix):
            if _wanted(obj.key, src):
                batch.append(obj)
            if len(batch) >= 500:
                found += _enqueue_batch(t, bucket, batch, "s3_poll")
                batch = []
        if batch:
            found += _enqueue_batch(t, bucket, batch, "s3_poll")
    return found


def _enqueue_batch(t: LoadedTenant, bucket: str, objs: list, source: str) -> int:
    cfg = t.config
    keys = [o.key for o in objs]
    with session_scope() as s:
        known = dict(s.execute(
            select(Photo.object_key, Photo.etag).where(Photo.bucket == bucket, Photo.object_key.in_(keys))
        ).all())
        n = 0
        for o in objs:
            if o.key in known and (not o.etag or not known[o.key] or known[o.key] == o.etag):
                continue
            r = enqueue_object(cfg.code, bucket, o.key, ingest_source=source, etag=o.etag,
                               mapped=map_metadata(cfg.mapping, o.key), retention_days=cfg.retention_days,
                               tz=cfg.timezone, session=s)
            if r.created or r.requeued:
                n += 1
                metrics.PHOTOS_INGESTED.labels(tenant=cfg.code, source=source).inc()
    return n


def handle_s3_event(payload: dict) -> dict:
    """Terima notifikasi bucket SILO/MinIO (format S3 event). Return ringkasan."""
    accepted, ignored = 0, 0
    records = payload.get("Records") or []
    for rec in records:
        name = rec.get("eventName", "")
        s3 = rec.get("s3") or {}
        bucket = (s3.get("bucket") or {}).get("name")
        obj = s3.get("object") or {}
        key = unquote_plus(obj.get("key", ""))
        if not name.startswith("s3:ObjectCreated") or not bucket or not key:
            ignored += 1
            continue
        match = registry.match_object(bucket, key)
        if match is None or not _wanted(key, match[2]):
            ignored += 1
            continue
        t, _, _ = match
        cfg = t.config
        r = enqueue_object(cfg.code, bucket, key, ingest_source="s3_event", etag=obj.get("eTag"),
                           mapped=map_metadata(cfg.mapping, key), retention_days=cfg.retention_days,
                           tz=cfg.timezone)
        if r.created or r.requeued:
            metrics.PHOTOS_INGESTED.labels(tenant=cfg.code, source="s3_event").inc()
        accepted += 1
    return {"accepted": accepted, "ignored": ignored}


@lru_cache(maxsize=16)
def _external_engine(dsn: str):
    return create_engine(dsn, pool_pre_ping=True, pool_size=2, max_overflow=0)


def poll_db(t: LoadedTenant, idx: int, src: Source) -> int:
    """Baca tabel link foto aplikasi (read-only). Query wajib memakai parameter :checkpoint."""
    cfg = t.config
    dsn = os.environ.get(src.dsn_env or "")
    if not dsn or not src.query:
        raise RuntimeError(f"db_poll {cfg.code}: env {src.dsn_env!r} atau query belum diisi")
    sid = state_id(cfg.code, idx, src)
    with session_scope() as s:
        st = s.get(SourceState, sid)
        checkpoint = st.checkpoint if st and st.checkpoint else None
    if checkpoint and checkpoint.lstrip("-").isdigit():  # kolom ID auto-increment
        cp_value = int(checkpoint)
    else:
        cp_value = parse_meta_datetime(checkpoint, cfg.timezone) if checkpoint else None
        if cp_value is None:
            cp_value = checkpoint or (datetime.now(ZoneInfo(cfg.timezone)) - timedelta(days=src.lookback_days))
    if isinstance(cp_value, datetime):
        cp_value = cp_value.replace(tzinfo=None)  # kolom DB aplikasi umumnya tanpa zona waktu
    with _external_engine(dsn).connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(text(src.query), {"checkpoint": cp_value})]
    found, newest = 0, checkpoint
    for row in rows:
        url = row.get(src.url_column)
        if not url:
            continue
        loc = split_url(str(url)) if "://" in str(url) else ((src.bucket or get_storage().s.aws_bucket), str(url))
        if loc is None:
            log.warning("db_poll %s: link tidak dikenali %s", cfg.code, url)
            continue
        bucket, key = loc
        fields = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()
                  if k in MAPPABLE_FIELDS and v is not None}
        r = enqueue_object(cfg.code, bucket, key, ingest_source="db_poll", fields=fields,
                           mapped=map_metadata(cfg.mapping, key, None, fields), retention_days=cfg.retention_days,
                           tz=cfg.timezone)
        if r.created:
            found += 1
            metrics.PHOTOS_INGESTED.labels(tenant=cfg.code, source="db_poll").inc()
        cp = row.get(src.checkpoint_column)
        if cp is not None:
            cp_s = cp.isoformat() if isinstance(cp, datetime) else str(cp)
            if newest is None or _cp_key(cp_s) > _cp_key(newest):
                newest = cp_s
    with session_scope() as s:
        st = s.get(SourceState, sid) or SourceState(id=sid)
        st.checkpoint = newest
        s.add(st)
    return found


def _cp_key(value: str) -> tuple:
    """Urutan checkpoint: angka dibandingkan sebagai angka, selain itu sebagai teks (ISO-8601)."""
    return (0, int(value), "") if value.lstrip("-").isdigit() else (1, 0, value)


def run_source(t: LoadedTenant, idx: int, src: Source, storage: Storage, *, reconcile: bool = False) -> int:
    sid = state_id(t.config.code, idx, src)
    try:
        if src.type in ("s3_poll", "s3_event"):
            n = poll_s3(t, idx, src, storage, reconcile=reconcile)
        else:
            n = poll_db(t, idx, src)
        err = None
    except Exception as e:  # source bermasalah tidak boleh menghentikan source lain
        log.warning("source %s gagal: %s", sid, e)
        metrics.SOURCE_ERRORS.labels(tenant=t.config.code, type=src.type).inc()
        n, err = 0, f"{type(e).__name__}: {e}"
    with session_scope() as s:
        st = s.get(SourceState, sid) or SourceState(id=sid)
        st.last_run_at = utcnow()
        st.last_found = n
        st.last_error = err
        if reconcile:
            st.last_reconcile_at = utcnow()
        s.add(st)
    return n


class Ingestor:
    """Loop penjadwal source. Satu instance aktif cukup (idempotent jika lebih dari satu)."""

    def __init__(self, storage: Storage | None = None) -> None:
        self.storage = storage
        self._last: dict[str, float] = {}
        self._last_reconcile: dict[str, float] = {}
        self.stop_event = threading.Event()

    def tick(self, now: float) -> int:
        storage = self.storage or get_storage()
        total = 0
        reconcile_every = get_settings().reconcile_interval_sec
        for t in registry.all():
            for idx, src in enumerate(t.config.sources):
                sid = state_id(t.config.code, idx, src)
                # s3_event: event adalah jalur utama, polling hanya jaring pengaman (reconcile)
                interval = reconcile_every if src.type == "s3_event" else src.poll_interval_sec
                due_rec = now - self._last_reconcile.get(sid, 0) >= reconcile_every
                if now - self._last.get(sid, 0) >= interval or due_rec:
                    total += run_source(t, idx, src, storage, reconcile=due_rec)
                    self._last[sid] = now
                    if due_rec:
                        self._last_reconcile[sid] = now
        return total

    def run_forever(self) -> None:
        import time

        log.info("ingestor berjalan")
        while not self.stop_event.is_set():
            try:
                self.tick(time.monotonic())
            except Exception:
                log.exception("ingestor tick gagal")
            self.stop_event.wait(1.0)
