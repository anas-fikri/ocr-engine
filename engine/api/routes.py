"""Endpoint untuk aplikasi, ops, dan pelanggan."""

from __future__ import annotations

import concurrent.futures
import posixpath
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, File, Form, Header, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Text, and_, cast, func, or_, select

from engine.analyzers import AnalysisContext
from engine.api.auth import Principal, check_tenant, current_principal, require
from engine.api.common import api_error, request_lang
from engine.db import AuditLog, Photo, Review, session_scope, utcnow
from engine.evidence import build_pdf, eligible_photos, evidence_item
from engine.i18n import all_reason_codes, localize_reasons, reason_text
from engine.imaging.loader import UnreadableImage
from engine.ingestor import handle_s3_event
from engine.jobs import claim_photo_job, enqueue_object, requeue_photo
from engine.mapping import map_metadata
from engine.pipeline import analyze
from engine.processor import execute_job, queue_photo_event
from engine.serializers import photo_to_dict
from engine.settings import get_settings
from engine.storage import ObjectNotFound, get_storage
from engine.tenant_config import MAPPABLE_FIELDS
from engine.tenants import TenantNotFound, registry

router = APIRouter(prefix="/v1")
_sync_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="sync-analyze")

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}


class PhotoFields(BaseModel):
    """Metadata opsional yang bisa dikirim aplikasi. Semua field boleh kosong."""

    awb: str | None = Field(None, description="Nomor resi", examples=["AWB123"])
    photo_type: str | None = Field(None, description="Jenis foto / nama profile", examples=["pod_recipient"])
    courier_id: str | None = Field(None, examples=["K045"])
    customer_id: str | None = Field(None, description="ID pelanggan (untuk evidence)")
    branch: str | None = Field(None, description="Kode cabang")
    captured_at: str | None = Field(None, description="Waktu foto diambil (ISO-8601 atau epoch)")
    lat: float | None = None
    lng: float | None = None
    source: Literal["camera", "gallery"] | None = Field(None, description="Asal foto di HP")
    external_id: str | None = Field(None, description="ID foto di aplikasi Anda (untuk korelasi)")

    def as_fields(self) -> dict[str, Any]:
        return {k: v for k, v in self.model_dump().items() if v not in (None, "")}


class AnalyzeObjectRequest(PhotoFields):
    object_key: str = Field(..., description="Key objek di SILO, contoh courier-app/pod/2026/10/22/AWB123.jpg")
    bucket: str | None = Field(None, description="Default: AWS_BUCKET")


def _resolve_tenant(p: Principal, tenant: str | None) -> str:
    code = tenant or p.tenant_code
    if not code:
        raise api_error(400, "TENANT_REQUIRED", "parameter tenant wajib untuk API key yang tidak terikat tenant")
    check_tenant(p, code)
    try:
        t = registry.get(code)
    except TenantNotFound as e:
        raise api_error(404, "TENANT_NOT_FOUND", f"tenant {code} tidak terdaftar") from e
    if not t.enabled:
        raise api_error(409, "TENANT_DISABLED", f"tenant {code} sedang dinonaktifkan")
    return code


def _photo_response(photo_id: str, lang: str, status_code: int = 200) -> JSONResponse:
    st = get_settings()
    with session_scope() as s:
        p = s.get(Photo, photo_id)
        body = photo_to_dict(p, lang, get_storage(), st.engine_presign_ttl_ui, detail=False)
    if p.status == "pending":
        status_code = 202
        body["poll_url"] = f"/v1/photos/{photo_id}"
    return JSONResponse(body, status_code=status_code)


def _run_sync(photo_id: str, timeout_ms: int) -> None:
    """Proses foto langsung di request ini. Lewat batas waktu -> tetap jalan di background (fail-open)."""
    deadline = time.monotonic() + timeout_ms / 1000
    with session_scope() as s:
        job = claim_photo_job(s, photo_id)
        job_id = job.id if job else None
    if job_id is not None:
        fut = _sync_pool.submit(execute_job, job_id, photo_id)
        try:
            fut.result(timeout=timeout_ms / 1000)
        except concurrent.futures.TimeoutError:
            pass
        return
    # job sudah diambil worker lain (misal lewat bucket notification): tunggu hasilnya
    while time.monotonic() < deadline:
        with session_scope() as s:
            if s.get(Photo, photo_id).status != "pending":
                return
        time.sleep(0.1)


@router.post(
    "/analyze",
    summary="Analisis foto (upload file ATAU referensi objek SILO)",
    responses={200: {"description": "Selesai (mode sync)"}, 202: {"description": "Diterima, diproses async"}},
)
async def analyze_upload(
    request: Request,
    principal: Annotated[Principal, Depends(require("app", "admin", "ops"))],
    file: Annotated[UploadFile | None, File(description="File foto (multipart)")] = None,
    tenant: Annotated[str | None, Query(description="Kode tenant (wajib jika API key tidak terikat tenant)")] = None,
    sync: Annotated[bool, Query(description="true = tunggu hasil sampai timeout_ms")] = False,
    timeout_ms: Annotated[int | None, Query(ge=100, le=30000)] = None,
    lang: Annotated[str | None, Query(pattern="^(id|en)$")] = None,
    awb: Annotated[str | None, Form()] = None,
    photo_type: Annotated[str | None, Form()] = None,
    courier_id: Annotated[str | None, Form()] = None,
    customer_id: Annotated[str | None, Form()] = None,
    branch: Annotated[str | None, Form()] = None,
    captured_at: Annotated[str | None, Form()] = None,
    lat: Annotated[float | None, Form()] = None,
    lng: Annotated[float | None, Form()] = None,
    source: Annotated[str | None, Form()] = None,
    external_id: Annotated[str | None, Form()] = None,
):
    """Dua cara pemakaian:

    1. **Upload file** (`multipart/form-data`, field `file` + metadata opsional). File disimpan engine ke
       bucket upload tenant lalu dianalisis.
    2. **Referensi objek** (`application/json`, lihat `AnalyzeObjectRequest`): aplikasi sudah upload ke
       SILO, engine cukup diberi `object_key`. Paling hemat kuota data kurir.

    Default async: respons `202` + `poll_url`. Dengan `sync=true`, engine menunggu hasil hingga
    `timeout_ms` (default 3000). Jika lewat, respons tetap `202` (fail-open) dan hasil menyusul.
    """
    st = get_settings()
    code = _resolve_tenant(principal, tenant)
    cfg = registry.get(code).config
    ctype = request.headers.get("content-type", "")
    if file is None and ctype.startswith("application/json"):
        try:
            body = AnalyzeObjectRequest.model_validate(await request.json())
        except Exception as e:
            raise api_error(422, "INVALID_BODY", f"body JSON tidak valid: {e}") from e
        bucket = body.bucket or st.aws_bucket
        fields = body.as_fields()
        fields.pop("object_key", None)
        fields.pop("bucket", None)
        try:
            head = get_storage().head(bucket, body.object_key)
        except ObjectNotFound as e:
            raise api_error(404, "OBJECT_NOT_FOUND", f"objek {bucket}/{body.object_key} tidak ditemukan") from e
        r = enqueue_object(code, bucket, body.object_key, ingest_source="api", fields=fields, etag=head["etag"],
                           mapped=map_metadata(cfg.mapping, body.object_key, head["metadata"], fields),
                           retention_days=cfg.retention_days, tz=cfg.timezone)
    elif file is not None:
        data = await file.read()
        if len(data) > st.max_upload_mb * 1024 * 1024:
            raise api_error(413, "FILE_TOO_LARGE", f"maksimal {st.max_upload_mb} MB")
        if not data:
            raise api_error(422, "EMPTY_FILE", "file kosong")
        ext = posixpath.splitext(file.filename or "")[1].lower()
        ext = ext if ext in ALLOWED_EXT else ".jpg"
        fields = PhotoFields(awb=awb, photo_type=photo_type, courier_id=courier_id, customer_id=customer_id,
                             branch=branch, captured_at=captured_at, lat=lat, lng=lng,
                             source=source if source in ("camera", "gallery") else None,
                             external_id=external_id).as_fields()
        bucket = cfg.upload_bucket or st.engine_bucket_upload
        key = f"{code}/{utcnow():%Y/%m/%d}/{uuid.uuid4().hex}{ext}"
        get_storage().put(bucket, key, data, file.content_type or "application/octet-stream",
                          {k.replace("_", "-"): v for k, v in fields.items()})
        r = enqueue_object(code, bucket, key, ingest_source="upload", fields=fields,
                           mapped=map_metadata(cfg.mapping, key, None, fields),
                           retention_days=cfg.retention_days, tz=cfg.timezone)
    else:
        raise api_error(422, "NO_INPUT", "kirim file (multipart) atau JSON berisi object_key")

    if sync:
        _run_sync(r.photo_id, timeout_ms or st.sync_timeout_ms)
    return _photo_response(r.photo_id, request_lang(request, lang))


@router.post("/analyze/preview", summary="Coba analisis tanpa menyimpan apa pun (kalibrasi & onboarding)")
async def analyze_preview(
    request: Request,
    principal: Annotated[Principal, Depends(require("app", "admin", "ops", "viewer"))],
    file: Annotated[UploadFile, File()],
    tenant: Annotated[str | None, Query()] = None,
    photo_type: Annotated[str | None, Query(description="Profile yang dipakai")] = None,
    lang: Annotated[str | None, Query(pattern="^(id|en)$")] = None,
):
    code = tenant or principal.tenant_code
    if code:
        check_tenant(principal, code)
        try:
            cfg = registry.get(code).config
        except TenantNotFound as e:
            raise api_error(404, "TENANT_NOT_FOUND", f"tenant {code} tidak terdaftar") from e
        profile_name, profile = cfg.resolve_profile(photo_type)
        tz = cfg.timezone
    else:
        from engine.tenant_config import build_profile, builtin_profiles

        profile_name = photo_type if photo_type in builtin_profiles() else "default"
        profile, tz = build_profile(profile_name), "Asia/Jakarta"
    data = await file.read()
    try:
        result = analyze(data, AnalysisContext(profile=profile, received_at=utcnow(), timezone=tz),
                         do_thumbnail=False)
    except UnreadableImage as e:
        raise api_error(422, "UNREADABLE_FILE", f"file tidak bisa dibuka sebagai gambar: {e}") from e
    out = result.to_dict()
    out["profile"] = profile_name
    out["mode"] = profile.mode
    out["reasons"] = localize_reasons(out["reasons"], request_lang(request, lang))
    return out


@router.get("/photos", summary="Cari foto")
def list_photos(
    request: Request,
    principal: Annotated[Principal, Depends(require("admin", "ops", "viewer", "app"))],
    tenant: str | None = None,
    awb: str | None = None,
    courier_id: str | None = None,
    branch: str | None = None,
    customer_id: str | None = None,
    external_id: str | None = None,
    verdict: Annotated[str | None, Query(pattern="^(PASS|WARN|REJECT)$")] = None,
    status: Annotated[str | None, Query(pattern="^(pending|done|error)$")] = None,
    review_status: Annotated[str | None, Query(pattern="^(none|approved|retake_requested|fraud)$")] = None,
    needs_review: Annotated[bool, Query(description="Hanya WARN/REJECT/error yang belum direview")] = False,
    reason: Annotated[str | None, Query(pattern="^[A-Z_]{3,40}$", description="Kode alasan, contoh BLURRY")] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    lang: str | None = None,
):
    q = select(Photo)
    if principal.tenant_code:
        q = q.where(Photo.tenant_code == principal.tenant_code)
    if tenant:
        check_tenant(principal, tenant)
        q = q.where(Photo.tenant_code == tenant)
    for col, val in ((Photo.awb, awb), (Photo.courier_id, courier_id), (Photo.branch, branch),
                     (Photo.customer_id, customer_id), (Photo.external_id, external_id),
                     (Photo.verdict, verdict), (Photo.status, status), (Photo.review_status, review_status)):
        if val:
            q = q.where(col == val)
    if needs_review:
        q = q.where(Photo.review_status == "none",
                    or_(Photo.verdict.in_(("WARN", "REJECT")), Photo.status == "error"))
    if reason:  # cocok di PostgreSQL (JSONB) maupun SQLite (JSON teks)
        q = q.where(cast(Photo.reasons, Text).like(f'%"code": "{reason}"%'))
    if date_from:
        q = q.where(Photo.received_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.where(Photo.received_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    with session_scope() as s:
        total = s.scalar(select(func.count()).select_from(q.subquery()))
        rows = s.scalars(q.order_by(Photo.received_at.desc()).offset(offset).limit(limit)).all()
        storage = get_storage()
        ttl = get_settings().engine_presign_ttl_ui
        lg = request_lang(request, lang)
        items = [photo_to_dict(p, lg, storage, ttl, detail=False) for p in rows]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def _get_photo_for(principal: Principal, photo_id: str, s) -> Photo:
    p = s.get(Photo, photo_id)
    if p is None or not principal.can_tenant(p.tenant_code):
        raise api_error(404, "PHOTO_NOT_FOUND", "foto tidak ditemukan")
    return p


@router.get("/photos/{photo_id}", summary="Detail foto + hasil analisis")
def get_photo(
    photo_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require("admin", "ops", "viewer", "app"))],
    lang: str | None = None,
):
    with session_scope() as s:
        p = _get_photo_for(principal, photo_id, s)
        return photo_to_dict(p, request_lang(request, lang), get_storage(), get_settings().engine_presign_ttl_ui)


class ReviewRequest(BaseModel):
    action: Literal["approve", "request_retake", "mark_fraud", "reset"]
    note: str | None = Field(None, max_length=2000)


REVIEW_STATUS = {"approve": "approved", "request_retake": "retake_requested", "mark_fraud": "fraud", "reset": "none"}


@router.post("/photos/{photo_id}/review", summary="Keputusan ops atas foto")
def review_photo(
    photo_id: str,
    body: ReviewRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require("admin", "ops"))],
):
    with session_scope() as s:
        p = _get_photo_for(principal, photo_id, s)
        p.review_status = REVIEW_STATUS[body.action]
        p.reviewed_by = principal.actor
        p.reviewed_at = utcnow()
        p.review_note = body.note
        s.add(Review(photo_id=p.id, action=body.action, note=body.note, actor=principal.actor))
        s.add(AuditLog(actor=principal.actor, action=f"photo.{body.action}", target=p.id))
        s.flush()
        queue_photo_event(s, p, "review.updated")
        return photo_to_dict(p, request_lang(request), None, detail=False)


@router.post("/photos/{photo_id}/reprocess", summary="Analisis ulang (misal setelah threshold diubah)")
def reprocess_photo(photo_id: str, principal: Annotated[Principal, Depends(require("admin", "ops"))]):
    with session_scope() as s:
        p = _get_photo_for(principal, photo_id, s)
        requeue_photo(p.id, s)
        s.add(AuditLog(actor=principal.actor, action="photo.reprocess", target=p.id))
    return {"id": photo_id, "status": "pending"}


@router.get("/photos/{photo_id}/reviews", summary="Riwayat review foto")
def photo_reviews(photo_id: str, principal: Annotated[Principal, Depends(require("admin", "ops", "viewer"))]):
    with session_scope() as s:
        _get_photo_for(principal, photo_id, s)
        rows = s.scalars(select(Review).where(Review.photo_id == photo_id).order_by(Review.id)).all()
        return [{"action": r.action, "note": r.note, "actor": r.actor, "at": r.created_at.isoformat()} for r in rows]


def _evidence_scope(p: Principal) -> tuple[list[str] | None, str | None]:
    if p.role == "customer":
        return ([p.tenant_code] if p.tenant_code else None), p.customer_id
    if p.role == "app":
        return [p.tenant_code], None
    return None, None


@router.get("/evidence/{awb}", summary="Bukti pengiriman untuk pelanggan (JSON)")
def evidence_json(
    awb: str,
    request: Request,
    principal: Annotated[Principal, Depends(require("customer", "app", "admin", "ops"))],
    lang: str | None = None,
):
    tenants, customer = _evidence_scope(principal)
    ttl = get_settings().engine_presign_ttl_customer
    with session_scope() as s:
        photos = eligible_photos(s, tenants, [awb], customer)
        items = [evidence_item(p, get_storage(), ttl) for p in photos]
    return {"awb": awb, "count": len(items), "photos": items, "lang": request_lang(request, lang)}


def _pdf_response(awbs: list[str], principal: Principal, lang: str, filename: str) -> Response:
    tenants, customer = _evidence_scope(principal)
    with session_scope() as s:
        photos = eligible_photos(s, tenants, awbs, customer)
        tz = "Asia/Jakarta"
        if photos:
            try:
                tz = registry.get(photos[0].tenant_code).config.timezone
            except TenantNotFound:
                pass
        pdf = build_pdf(photos, get_storage(), lang, tz, awbs)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})


@router.get("/evidence/{awb}/pdf", summary="Bukti pengiriman (PDF) per resi")
def evidence_pdf(
    awb: str,
    request: Request,
    principal: Annotated[Principal, Depends(require("customer", "app", "admin", "ops"))],
    lang: str | None = None,
):
    return _pdf_response([awb], principal, request_lang(request, lang), f"pod-{awb}.pdf")


class EvidenceBatch(BaseModel):
    awbs: list[str] = Field(..., min_length=1, max_length=500)


@router.post("/evidence/batch", summary="Bukti pengiriman (PDF) banyak resi, misal per periode invoice")
def evidence_batch(
    body: EvidenceBatch,
    request: Request,
    principal: Annotated[Principal, Depends(require("customer", "app", "admin", "ops"))],
    lang: str | None = None,
):
    return _pdf_response(body.awbs, principal, request_lang(request, lang), "pod-batch.pdf")


@router.get("/verify/{sha256}", summary="Cek keaslian file (publik, tanpa API key)")
def verify(sha256: str):
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256.lower()):
        raise api_error(422, "INVALID_HASH", "sha256 harus 64 karakter hex")
    with session_scope() as s:
        p = s.scalar(select(Photo).where(Photo.sha256 == sha256.lower()).order_by(Photo.received_at).limit(1))
        if p is None:
            return {"found": False}
        return {"found": True, "received_at": p.received_at.isoformat(), "awb_suffix": (p.awb or "")[-4:] or None,
                "message": "File ini identik dengan foto yang diterima sistem pada waktu tersebut."}


@router.post("/events/s3", summary="Penerima bucket notification SILO (webhook)", include_in_schema=True)
def s3_events(payload: dict = Body(...), authorization: str | None = Header(None)):
    token = get_settings().s3_event_token
    if token:
        given = (authorization or "").removeprefix("Bearer ").strip()
        if given != token:
            raise api_error(401, "UNAUTHORIZED", "token event tidak valid")
    return handle_s3_event(payload)


@router.get("/stats", summary="Ringkasan kualitas untuk dashboard")
def stats(
    principal: Annotated[Principal, Depends(require("admin", "ops", "viewer", "app"))],
    tenant: str | None = None,
    days: Annotated[int, Query(ge=1, le=365)] = 14,
):
    since = utcnow() - timedelta(days=days)
    cond = [Photo.received_at >= since]
    if principal.tenant_code:
        cond.append(Photo.tenant_code == principal.tenant_code)
    if tenant:
        check_tenant(principal, tenant)
        cond.append(Photo.tenant_code == tenant)
    where = and_(*cond)
    with session_scope() as s:
        by_verdict = dict(s.execute(select(Photo.verdict, func.count()).where(where).group_by(Photo.verdict)).all())
        by_status = dict(s.execute(select(Photo.status, func.count()).where(where).group_by(Photo.status)).all())
        day = func.date(Photo.received_at)
        daily = s.execute(select(day, Photo.verdict, func.count()).where(where).group_by(day, Photo.verdict)
                          .order_by(day)).all()
        bad = and_(where, Photo.verdict.in_(("WARN", "REJECT")))
        top_couriers = s.execute(select(Photo.courier_id, func.count()).where(bad, Photo.courier_id.is_not(None))
                                 .group_by(Photo.courier_id).order_by(func.count().desc()).limit(10)).all()
        top_branches = s.execute(select(Photo.branch, func.count()).where(bad, Photo.branch.is_not(None))
                                 .group_by(Photo.branch).order_by(func.count().desc()).limit(10)).all()
        review_open = s.scalar(select(func.count()).where(where, Photo.review_status == "none",
                                                          or_(Photo.verdict.in_(("WARN", "REJECT")),
                                                              Photo.status == "error")))
        reason_counts: dict[str, int] = {}
        for (reasons,) in s.execute(select(Photo.reasons).where(bad).limit(20000)):
            for r in reasons or []:
                if r.get("severity") in ("reject", "warn"):
                    reason_counts[r["code"]] = reason_counts.get(r["code"], 0) + 1
    series: dict[str, dict[str, int]] = {}
    for d, v, n in daily:
        series.setdefault(str(d), {"PASS": 0, "WARN": 0, "REJECT": 0, "pending": 0})[v or "pending"] = n
    return {
        "days": days,
        "total": sum(by_verdict.values()),
        "by_verdict": {k or "pending": v for k, v in by_verdict.items()},
        "by_status": by_status,
        "review_open": review_open,
        "daily": [{"date": d, **v} for d, v in sorted(series.items())],
        "top_couriers": [{"courier_id": c, "count": n} for c, n in top_couriers],
        "top_branches": [{"branch": b, "count": n} for b, n in top_branches],
        "top_reasons": sorted(({"code": k, "count": v} for k, v in reason_counts.items()),
                              key=lambda x: -x["count"])[:10],
    }


@router.get("/meta/reason-codes", summary="Daftar kode alasan + pesan")
def reason_codes(request: Request, lang: str | None = None):
    lg = request_lang(request, lang)
    return [{"code": c, **reason_text(c, lg)} for c in all_reason_codes()]


@router.get("/meta/fields", summary="Daftar field metadata yang dikenali engine")
def meta_fields():
    return list(MAPPABLE_FIELDS)


@router.get("/me", summary="Info API key yang sedang dipakai")
def me(principal: Annotated[Principal, Depends(current_principal)]):
    return {"name": principal.name, "role": principal.role, "tenant": principal.tenant_code,
            "customer_id": principal.customer_id}
