"""Bentuk JSON foto yang dipakai API, webhook, dan Web UI (satu sumber, supaya konsisten)."""

from __future__ import annotations

from typing import Any

from engine.db import Photo, as_aware
from engine.i18n import courier_message, localize_reasons, retake_text, verdict_text
from engine.storage import Storage


def _iso(dt) -> str | None:
    dt = as_aware(dt)
    return dt.isoformat() if dt else None


def review_required_for(tenant_code: str) -> bool:
    from engine.tenants import TenantNotFound, registry

    try:
        return registry.get(tenant_code).config.review_required
    except TenantNotFound:
        return False


def evidence_eligible(p: Photo, review_required: bool | None = None) -> bool:
    """Foto boleh tampil di evidence pelanggan.

    - review_required = false (bawaan): PASS dan WARN langsung masuk, REJECT tidak.
    - review_required = true: hanya PASS, atau WARN/REJECT yang sudah di-approve ops.
    Foto yang ditandai ops "minta foto ulang" atau "kecurangan" tidak pernah masuk.
    """
    if p.review_status in ("retake_requested", "fraud"):
        return False
    if p.status != "done":
        return False
    if p.review_status == "approved" or p.verdict == "PASS":
        return True
    if review_required is None:
        review_required = review_required_for(p.tenant_code)
    return p.verdict == "WARN" and not review_required


def retake_required(p: Photo) -> bool:
    """Kualitas tidak bisa dimaksimalkan (termasuk setelah perbaikan otomatis): wajib foto ulang."""
    if p.review_status == "approved":
        return False
    if p.review_status == "retake_requested":
        return True
    return p.verdict == "REJECT" and p.status in ("done", "error")


def allow_status_update(p: Photo) -> bool:
    """Boleh aplikasi pengiriman mengupdate status pengiriman? Status itu milik aplikasi pengiriman, bukan engine.

    False hanya jika foto ditolak (aksi `reject`, yaitu verdict REJECT di mode enforce) atau ops meminta foto ulang /
    menandai kecurangan. Belum ada hasil (pending) atau engine gagal memproses -> True (fail-open).
    """
    if p.review_status in ("retake_requested", "fraud"):
        return False
    if p.review_status == "approved":
        return True
    return p.action != "reject"


def photo_to_dict(
    p: Photo,
    lang: str,
    storage: Storage | None = None,
    ttl: int = 900,
    *,
    detail: bool = True,
) -> dict[str, Any]:
    scores = dict(p.scores or {})
    reasons = localize_reasons(p.reasons or [], lang)
    retake = retake_required(p)
    action = {"retake_requested": "reject", "approved": "log"}.get(p.review_status, p.action)  # untuk pesan kurir
    if "authenticity" in scores:
        scores["authenticity_risk"] = round(100 - scores["authenticity"], 1)
    out: dict[str, Any] = {
        "id": p.id,
        "tenant": p.tenant_code,
        "status": p.status,
        "verdict": p.verdict,
        "verdict_text": verdict_text(p.verdict, lang) if p.verdict else None,
        "action": p.action,
        "allow_status_update": allow_status_update(p),
        "courier_message": courier_message(action, retake, reasons, lang),
        "retake_required": retake,
        "retake_message": retake_text(lang) if retake else None,
        "photo_type": p.photo_type,
        "profile": p.profile,
        "awb": p.awb,
        "courier_id": p.courier_id,
        "customer_id": p.customer_id,
        "branch": p.branch,
        "external_id": p.external_id,
        "captured_at": _iso(p.captured_at),
        "received_at": _iso(p.received_at),
        "analyzed_at": _iso(p.analyzed_at),
        "scores": scores,
        "reasons": reasons,
        "review": {
            "status": p.review_status,
            "by": p.reviewed_by,
            "at": _iso(p.reviewed_at),
            "note": p.review_note,
        },
        "evidence_eligible": evidence_eligible(p),
        # lokasi foto di SILO aplikasi (dilaporkan aplikasi); null = tidak disimpan / belum dilaporkan
        "object": {"bucket": p.bucket, "key": p.object_key} if p.bucket else None,
        "sha256": p.sha256,
        "error": {"code": p.error_code, "message": p.error_message} if p.error_code else None,
    }
    if storage is not None:
        original = storage.presign(p.bucket, p.object_key, ttl) if p.bucket else None
        out["urls"] = {
            "original": original,
            "processed": storage.presign(p.processed_bucket, p.processed_key, ttl) if p.processed_key else original,
            # engine tidak menyimpan thumbnail; ambil lewat GET /v1/photos/{id}/file?variant=thumbnail
            "thumbnail": storage.presign(p.thumb_bucket, p.thumb_key, ttl) if p.thumb_key else None,
            "expires_in": ttl,
        }
    if detail:
        out.update({
            "ingest_source": p.ingest_source,
            "config_version": p.config_version,
            "image": p.image_info or {},
            "compression": p.compression or {},
            "enhancement": (p.image_info or {}).get("enhancement", {"attempted": False, "applied": False}),
            "exif": p.exif or {},
            "metrics": p.metrics or {},
            "location": {"lat": p.lat, "lng": p.lng} if p.lat is not None else None,
            "capture_source": p.capture_source,
            "input_fields": p.meta or {},
            "duration_ms": p.duration_ms,
            "phash": p.phash,
        })
    return out
