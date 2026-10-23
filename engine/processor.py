"""Proses satu job: ambil foto dari SILO -> analisis -> simpan hasil & varian -> antre webhook."""

from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

from sqlalchemy import delete, select

from engine import metrics
from engine.analyzers import AnalysisContext, DuplicateMatch
from engine.analyzers.duplicate import hamming
from engine.db import BackupItem, Job, Photo, WebhookDelivery, as_aware, session_scope, utcnow
from engine.imaging.loader import UnreadableImage
from engine.jobs import apply_mapped, claim_job, fail_job, finish_job
from engine.mapping import map_metadata
from engine.pipeline import analyze
from engine.serializers import photo_to_dict
from engine.settings import get_settings
from engine.storage import ObjectNotFound, Storage, get_storage
from engine.tenants import TenantNotFound, registry

log = logging.getLogger(__name__)


class PhashIndex:
    """Cache hash per tenant di memori, di-update inkremental (cukup untuk jutaan foto)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, dict[str, tuple]] = {}  # tenant -> photo_id -> (awb, key, sha, phash_int, at)
        self._since: dict[str, object] = {}

    def _refresh(self, tenant: str) -> None:
        lookback = utcnow() - timedelta(days=get_settings().duplicate_lookback_days)
        since = self._since.get(tenant) or lookback
        rows = self._rows.setdefault(tenant, {})
        with session_scope() as s:
            q = select(Photo.id, Photo.awb, Photo.object_key, Photo.sha256, Photo.phash, Photo.analyzed_at).where(
                Photo.tenant_code == tenant, Photo.phash.is_not(None), Photo.analyzed_at > since
            )
            latest = since
            for pid, awb, key, sha, ph, at in s.execute(q):
                rows[pid] = (awb, key, sha, int(ph, 16), as_aware(at))
                if as_aware(at) and as_aware(at) > as_aware(latest):
                    latest = at
        # buang yang sudah lewat lookback
        for pid in [k for k, v in rows.items() if v[4] and v[4] < lookback]:
            del rows[pid]
        self._since[tenant] = latest

    def find(self, tenant: str, exclude_id: str, sha: str, ph: int, max_dist: int) -> list[DuplicateMatch]:
        with self._lock:
            self._refresh(tenant)
            out = []
            for pid, (awb, key, sha2, ph2, _) in self._rows.get(tenant, {}).items():
                if pid == exclude_id:
                    continue
                if sha2 == sha:
                    out.append(DuplicateMatch(pid, awb, key, 0, True))
                    continue
                d = hamming(ph, ph2)
                if d <= max_dist:
                    out.append(DuplicateMatch(pid, awb, key, d, False))
            return out

    def reset(self) -> None:
        with self._lock:
            self._rows.clear()
            self._since.clear()


phash_index = PhashIndex()


def _variant_key(p: Photo, ext: str) -> str:
    at = as_aware(p.received_at) or utcnow()
    return f"{p.tenant_code}/{at:%Y/%m/%d}/{p.id}.{ext}"


def _queue_webhooks(s, tenant_cfg, event: str, payload: dict) -> None:
    for i, sink in enumerate(tenant_cfg.sinks):
        if event in sink.events:
            s.add(WebhookDelivery(tenant_code=tenant_cfg.code, url=sink.url, event=event,
                                  payload=payload, sink_index=i))


def queue_photo_event(s, photo: Photo, event: str) -> None:
    try:
        t = registry.get(photo.tenant_code)
    except TenantNotFound:
        return
    payload = {"event": event, "photo": photo_to_dict(photo, t.config.default_lang, detail=False)}
    _queue_webhooks(s, t.config, event, payload)


def process_photo(photo_id: str, storage: Storage | None = None) -> str:
    """Return status akhir foto: done | error. Raise untuk error sementara (akan di-retry)."""
    storage = storage or get_storage()
    st = storage.s  # konfigurasi SILO efektif (env + halaman Pengaturan SILO)
    with session_scope() as s:
        p = s.get(Photo, photo_id)
        if p is None:
            return "error"
        tenant_code, bucket, key, fields = p.tenant_code, p.bucket, p.object_key, dict(p.meta or {})
        ingest_source = p.ingest_source
        received_at = as_aware(p.received_at)
    tenant = registry.get(tenant_code)
    cfg = tenant.config

    try:
        obj = storage.get(bucket, key)
    except ObjectNotFound:
        _mark_error(photo_id, "OBJECT_NOT_FOUND", f"objek {bucket}/{key} tidak ditemukan")
        return "error"

    mapped = map_metadata(cfg.mapping, key, obj.metadata, fields)
    profile_name, profile = cfg.resolve_profile(mapped.get("photo_type"))
    ctx = AnalysisContext(
        profile=profile,
        meta=mapped,
        received_at=received_at,
        timezone=cfg.timezone,
        find_duplicates=lambda sha, ph, dist: phash_index.find(tenant_code, photo_id, sha, ph, dist),
    )
    store = cfg.store_mode == "processed"
    t0 = time.perf_counter()
    try:
        result = analyze(obj.data, ctx, do_compress=store, do_thumbnail=store)
    except UnreadableImage as e:
        _mark_error(photo_id, "UNREADABLE_FILE", str(e), mapped=mapped, profile=profile_name, tz=cfg.timezone)
        return "error"

    processed_key = thumb_key = None
    if store:
        with session_scope() as s:
            p = s.get(Photo, photo_id)
            pkey, tkey = _variant_key(p, "jpg"), _variant_key(p, "jpg")
        comp = result.compression
        accepted = result.verdict.action != "reject"  # ditolak: tidak dibuat versi foto pengiriman
        if accepted and comp is not None and not comp.passthrough and comp.data is not None:
            ext = "webp" if comp.content_type == "image/webp" else "jpg"
            processed_key = pkey.rsplit(".", 1)[0] + f".{ext}"
            storage.put(st.engine_bucket_processed, processed_key, comp.data, comp.content_type,
                        {"photo-id": photo_id, "source-key": key, "sha256-original": result.image.sha256})
        if accepted and processed_key is None and ingest_source == "upload":
            # "scan dulu": foto upload yang diterima selalu punya salinan di bucket hasil, supaya link `stored`
            # tidak pernah menunjuk ke bucket upload (arsip asli + karantina foto yang ditolak)
            ext = key.rsplit(".", 1)[-1] if "." in key else "jpg"
            processed_key = pkey.rsplit(".", 1)[0] + f".{ext}"
            storage.put(st.engine_bucket_processed, processed_key, obj.data, obj.content_type or "image/jpeg",
                        {"photo-id": photo_id, "source-key": key, "sha256-original": result.image.sha256})
        if result.thumbnail:
            thumb_key = tkey
            storage.put(st.engine_bucket_thumb, thumb_key, result.thumbnail, "image/jpeg", {"photo-id": photo_id})

    data = result.to_dict()
    with session_scope() as s:
        p = s.get(Photo, photo_id)
        apply_mapped(p, mapped, cfg.timezone)
        p.profile = profile_name
        p.config_version = tenant.version
        p.sha256 = result.image.sha256
        p.phash = result.phash
        p.verdict = data["verdict"]
        p.action = data["action"]
        p.scores = data["scores"]
        p.reasons = data["reasons"]
        p.metrics = data["metrics"]
        p.exif = data["exif"]
        p.image_info = {**data["image"], "enhancement": data["enhancement"]}
        p.compression = data["compression"] or {}
        p.processed_bucket = st.engine_bucket_processed if processed_key else None
        p.processed_key = processed_key
        p.thumb_bucket = st.engine_bucket_thumb if thumb_key else None
        p.thumb_key = thumb_key
        p.etag = p.etag or obj.etag
        p.status = "done"
        p.error_code = p.error_message = None
        p.analyzed_at = utcnow()
        p.duration_ms = int((time.perf_counter() - t0) * 1000)
        s.flush()
        queue_photo_event(s, p, "analysis.completed")
        s.execute(delete(BackupItem).where(BackupItem.photo_id == p.id))  # hasil baru: salin ulang ke NAS
        metrics.PHOTOS_ANALYZED.labels(tenant=tenant_code, verdict=p.verdict).inc()
        metrics.ANALYSIS_SECONDS.observe(p.duration_ms / 1000)
    return "done"


def _reject_action(tenant_code: str, profile: str | None) -> str:
    """Aksi untuk verdict REJECT sesuai mode profile (sama dengan verdict.decide)."""
    try:
        mode = registry.get(tenant_code).config.resolve_profile(profile)[1].mode
    except Exception:  # tenant/profile tidak valid: jangan menahan kurir
        return "log"
    return {"shadow": "log", "warn": "notify", "enforce": "reject"}[mode]


def _mark_error(photo_id: str, code: str, message: str, *, mapped: dict | None = None,
                profile: str | None = None, tz: str = "Asia/Jakarta") -> None:
    with session_scope() as s:
        p = s.get(Photo, photo_id)
        if p is None:
            return
        if mapped:
            apply_mapped(p, mapped, tz)
        p.profile = profile or p.profile
        p.status = "error"
        p.error_code = code
        p.error_message = message[:2000]
        if code in ("UNREADABLE_FILE",):
            # file rusak = foto tidak layak: terlihat di antrean review ops dan, di mode enforce, menahan status
            p.verdict = "REJECT"
            p.action = _reject_action(p.tenant_code, p.profile)
            p.reasons = [{"code": code, "check": "decode", "severity": "reject", "score": None, "detail": {}}]
        p.analyzed_at = utcnow()
        s.flush()
        queue_photo_event(s, p, "analysis.completed")
        s.execute(delete(BackupItem).where(BackupItem.photo_id == p.id))  # hasil baru: salin ulang ke NAS
        metrics.PHOTOS_FAILED.labels(tenant=p.tenant_code, code=code).inc()


def execute_job(job_id: int, photo_id: str, storage: Storage | None = None) -> str:
    """Kerjakan job yang sudah di-claim. Return status akhir: done | error | retry | dead."""
    try:
        status = process_photo(photo_id, storage)
    except TenantNotFound:
        _mark_error(photo_id, "TENANT_NOT_FOUND", "tenant foto ini tidak ditemukan / dihapus")
        status = "error"
    except Exception as e:  # error sementara: SILO/DB timeout, dsb.
        log.warning("job %s gagal: %s", job_id, e, exc_info=log.isEnabledFor(logging.DEBUG))
        with session_scope() as s:
            dead = fail_job(s, s.get(Job, job_id), f"{type(e).__name__}: {e}")
        if dead:
            _mark_error(photo_id, "PROCESSING_FAILED", f"{type(e).__name__}: {e}")
            metrics.JOBS_DEAD.inc()
            return "dead"
        return "retry"
    with session_scope() as s:
        finish_job(s, s.get(Job, job_id))
    return status


def run_one(storage: Storage | None = None) -> bool:
    """Ambil & kerjakan satu job. Return False jika antrean kosong."""
    with session_scope() as s:
        job = claim_job(s)
        if job is None:
            return False
        job_id, photo_id = job.id, job.photo_id
    execute_job(job_id, photo_id, storage)
    return True
