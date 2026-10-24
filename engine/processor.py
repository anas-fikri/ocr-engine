"""Analisis foto: dari file upload aplikasi atau dari SILO (hanya baca). Engine tidak pernah menulis ke SILO."""

from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, select

from engine import metrics
from engine.analyzers import AnalysisContext, DuplicateMatch
from engine.analyzers.duplicate import hamming
from engine.db import BackupItem, Job, Photo, WebhookDelivery, as_aware, session_scope, utcnow
from engine.imaging.loader import UnreadableImage
from engine.jobs import UNSTORED_BUCKET, apply_mapped, claim_job, fail_job, finish_job
from engine.mapping import map_metadata
from engine.pipeline import analyze
from engine.serializers import allow_status_update, photo_to_dict
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


@dataclass
class OutputImage:
    """Foto yang disimpan APLIKASI ke SILO (engine tidak menyimpan apa pun ke SILO)."""

    data: bytes
    content_type: str
    optimized: bool  # False = foto asli sudah memenuhi syarat ukuran, dikembalikan apa adanya

    def to_dict(self) -> dict:
        return {
            "content_type": self.content_type,
            "bytes": len(self.data),
            "optimized": self.optimized,
            "sha256": hashlib.sha256(self.data).hexdigest(),
            "base64": base64.b64encode(self.data).decode(),
        }


def process_photo(photo_id: str, storage: Storage | None = None) -> str:
    """Analisis foto yang sudah ada di SILO (polling / event / object_key / analisis ulang). Hanya MEMBACA.

    Foto upload yang tidak disimpan aplikasi dianalisis ulang dari arsip engine. Return status akhir foto:
    done | error. Raise untuk error sementara (akan di-retry).
    """
    storage = storage or get_storage()
    with session_scope() as s:
        p = s.get(Photo, photo_id)
        if p is None:
            return "error"
        bucket, key = (p.bucket, p.object_key) if p.bucket != UNSTORED_BUCKET else (p.processed_bucket, p.processed_key)
    try:
        obj = storage.get(bucket, key)
    except ObjectNotFound:
        _mark_error(photo_id, "OBJECT_NOT_FOUND", f"objek {bucket}/{key} tidak ditemukan")
        return "error"
    status, _ = analyze_and_record(photo_id, obj.data, obj.content_type, obj.metadata, obj.etag)
    return status


def process_upload(photo_id: str, data: bytes, content_type: str | None) -> tuple[str, OutputImage | None]:
    """Analisis file yang dikirim aplikasi (tanpa menyentuh SILO). Return (status, foto untuk disimpan aplikasi).

    Foto untuk disimpan hanya dikembalikan jika foto diterima (`allow_status_update`): versi optimasi, atau foto
    asli jika sudah memenuhi syarat ukuran. Foto ditolak -> None (aplikasi tidak menyimpannya sebagai foto
    pengiriman dan meminta kurir foto ulang).

    Engine juga mengarsipkan salinannya sendiri di bucket arsip (ADR 0007), di belakang layar setelah keputusan
    keluar: foto diterima = foto yang sama dengan `output_image`; foto ditolak = salinan review berkualitas tinggi
    (maks. `archive_rejected_max_bytes`, bawaan 1,5 MB; 0 = file asli) untuk review & kalibrasi.
    """
    status, output = analyze_and_record(photo_id, data, content_type, None, None, want_output=True)
    if status == "done":
        if output is not None:
            schedule_archive(photo_id, output.data, output.content_type)
        else:
            schedule_archive(photo_id, data, content_type or "image/jpeg", review=True)
    return status, output


_archive_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="archive")
ARCHIVE_RETRY_SEC = (1, 4, 15)
_EXT = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic",
        "image/heif": "heif"}


def archive_bucket(tenant_code: str) -> str:
    try:
        b = registry.get(tenant_code).config.archive_bucket
    except TenantNotFound:
        b = None
    return b or get_storage().s.engine_bucket_archive


def schedule_archive(photo_id: str, data: bytes, content_type: str, review: bool = False) -> None:
    """Simpan ke arsip tanpa menahan respons ke aplikasi. Gagal -> dicoba ulang beberapa kali, lalu dicatat.
    review=True: foto ditolak, dikompres dulu jadi salinan review (di thread arsip, kurir tidak menunggu)."""
    fut = _archive_pool.submit(_archive_with_retry, photo_id, data, content_type, review)
    _pending_archives.add(fut)
    fut.add_done_callback(_pending_archives.discard)


_pending_archives: set = set()


def wait_for_archives(timeout: float = 30) -> None:
    """Tunggu penyimpanan arsip yang masih berjalan (test, CLI, dan saat proses berhenti)."""
    from concurrent.futures import wait

    wait(list(_pending_archives), timeout=timeout)


def _review_copy(photo_id: str, data: bytes, content_type: str) -> tuple[bytes, str, dict | None]:
    from engine.imaging.compressor import review_copy

    with session_scope() as s:
        p = s.get(Photo, photo_id)
        tenant_code = p.tenant_code if p is not None else None
    try:
        cap = registry.get(tenant_code).config.archive_rejected_max_bytes if tenant_code else 0
    except TenantNotFound:
        cap = 1_500_000
    if not cap:
        return data, content_type, None
    try:
        return review_copy(data, cap)
    except Exception as e:  # file aneh: arsipkan apa adanya daripada kehilangan bukti
        log.warning("salinan review foto %s gagal, arsip file asli: %s", photo_id, e)
        return data, content_type, None


def _archive_with_retry(photo_id: str, data: bytes, content_type: str, review: bool = False) -> None:
    info = None
    if review:
        data, content_type, info = _review_copy(photo_id, data, content_type)
    for i, wait in enumerate((0, *ARCHIVE_RETRY_SEC)):
        if wait:
            time.sleep(wait)
        try:
            archive_photo(photo_id, data, content_type, info=info)
            metrics.ARCHIVE.labels(result="ok").inc()
            return
        except Exception as e:  # SILO lambat/mati: kurir tidak terpengaruh, coba lagi
            last = i == len(ARCHIVE_RETRY_SEC)
            metrics.ARCHIVE.labels(result="failed" if last else "retry").inc()
            log.warning("arsip foto %s gagal (percobaan %s): %s", photo_id, i + 1, e)


def archive_photo(photo_id: str, data: bytes, content_type: str, storage: Storage | None = None,
                  info: dict | None = None) -> None:
    storage = storage or get_storage()
    with session_scope() as s:
        p = s.get(Photo, photo_id)
        if p is None or p.processed_key:
            return
        tenant_code, verdict, sha = p.tenant_code, p.verdict, p.sha256
        at = as_aware(p.received_at) or utcnow()
    bucket = archive_bucket(tenant_code)
    key = f"{tenant_code}/{at:%Y/%m/%d}/{photo_id}.{_EXT.get(content_type.lower(), 'jpg')}"
    meta = {"photo-id": photo_id, "verdict": verdict, "sha256-original": sha}
    if info and info.get("compressed"):
        meta["review-copy"] = f"q{info['quality']} {info['width']}x{info['height']} dari {info['original_bytes']} byte"
    storage.put(bucket, key, data, content_type, meta)
    with session_scope() as s:
        p = s.get(Photo, photo_id)
        if p is not None:
            p.processed_bucket, p.processed_key = bucket, key
            if info is not None:  # salinan review foto ditolak: tampil di Web UI (baris Arsip engine)
                p.image_info = {**(p.image_info or {}), "archive": info}
            s.execute(delete(BackupItem).where(BackupItem.photo_id == photo_id))  # ada file baru: salin ke NAS


def analyze_and_record(photo_id: str, data: bytes, content_type: str | None, obj_metadata: dict | None,
                       etag: str | None, *, want_output: bool = False) -> tuple[str, OutputImage | None]:
    with session_scope() as s:
        p = s.get(Photo, photo_id)
        if p is None:
            return "error", None
        tenant_code, key, fields = p.tenant_code, p.object_key, dict(p.meta or {})
        received_at = as_aware(p.received_at)
    tenant = registry.get(tenant_code)
    cfg = tenant.config

    mapped = map_metadata(cfg.mapping, key, obj_metadata, fields)
    profile_name, profile = cfg.resolve_profile(mapped.get("photo_type"))
    ctx = AnalysisContext(
        profile=profile,
        meta=mapped,
        received_at=received_at,
        timezone=cfg.timezone,
        find_duplicates=lambda sha, ph, dist: phash_index.find(tenant_code, photo_id, sha, ph, dist),
    )
    t0 = time.perf_counter()
    try:
        result = analyze(data, ctx, do_compress=want_output, do_thumbnail=False)
    except UnreadableImage as e:
        _mark_error(photo_id, "UNREADABLE_FILE", str(e), mapped=mapped, profile=profile_name, tz=cfg.timezone)
        return "error", None

    data_out = result.to_dict()
    with session_scope() as s:
        p = s.get(Photo, photo_id)
        apply_mapped(p, mapped, cfg.timezone)
        p.profile = profile_name
        p.config_version = tenant.version
        p.sha256 = result.image.sha256
        p.phash = result.phash
        p.verdict = data_out["verdict"]
        p.action = data_out["action"]
        p.scores = data_out["scores"]
        p.reasons = data_out["reasons"]
        p.metrics = data_out["metrics"]
        p.exif = data_out["exif"]
        p.image_info = {**data_out["image"], "enhancement": data_out["enhancement"]}
        p.compression = data_out["compression"] or {}
        p.etag = p.etag or etag
        p.status = "done"
        p.error_code = p.error_message = None
        p.analyzed_at = utcnow()
        p.duration_ms = int((time.perf_counter() - t0) * 1000)
        if want_output and allow_status_update(p):
            _bind_location(s, p)  # lokasi yang dikirim aplikasi (object_key) dicatat hanya untuk foto diterima
        s.flush()
        accepted = allow_status_update(p)
        queue_photo_event(s, p, "analysis.completed")
        s.execute(delete(BackupItem).where(BackupItem.photo_id == p.id))  # hasil baru: salin ulang ke NAS
        metrics.PHOTOS_ANALYZED.labels(tenant=tenant_code, verdict=p.verdict).inc()
        metrics.ANALYSIS_SECONDS.observe(p.duration_ms / 1000)

    output = None
    if want_output and accepted:
        c = result.compression
        if c is not None and not c.passthrough and c.data is not None:
            output = OutputImage(c.data, c.content_type, True)
        else:
            output = OutputImage(data, content_type or "image/jpeg", False)
    return "done", output


def _bind_location(s, p: Photo) -> None:
    """Pindahkan foto upload dari lokasi sementara ke bucket/key yang dikirim aplikasi (meta object_key)."""
    key = (p.meta or {}).get("object_key")
    if not key or p.bucket != UNSTORED_BUCKET:
        return
    bind_location(s, p, (p.meta or {}).get("bucket") or get_storage().s.aws_bucket, key)


def bind_location(s, p: Photo, bucket: str, key: str) -> None:
    """Catat di mana aplikasi menyimpan foto ini di SILO. Foto lain yang tercatat di lokasi yang sama (misal foto
    lama yang ditimpa, atau salinan dari polling) dilepas dari lokasi itu."""
    other = s.scalar(select(Photo).where(Photo.bucket == bucket, Photo.object_key == key, Photo.id != p.id))
    if other is not None:
        if other.ingest_source in ("s3_poll", "s3_event"):
            s.delete(other)  # polling sempat mencatat file yang sama (foto hasil engine): cukup satu catatan
        else:
            other.bucket, other.object_key = UNSTORED_BUCKET, f"replaced/{other.id}"
        s.flush()
    p.bucket, p.object_key = bucket, key
    s.execute(delete(BackupItem).where(BackupItem.photo_id == p.id))  # lokasi baru: salin ke NAS


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
