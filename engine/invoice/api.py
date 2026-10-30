"""Endpoint modul invoice: POST /v1/jobs, GET /v1/jobs/{id}, POST /v1/templates, GET /v1/templates/{type}/{npwp}.

Semua endpoint wajib API key + tanda tangan HMAC (signing.py). Body dibaca mentah dengan batas ukuran SEBELUM
diproses (tanda tangan mencakup hash body), lalu multipart di-parse dari byte yang sudah dibatasi.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from engine.api.common import api_error
from engine.db import AuditLog, session_scope
from engine.invoice import parsing as P
from engine.invoice import queue, signing, store
from engine.invoice.document import Document
from engine.invoice.extract import FIELD_KINDS, FIELDS, npwp_candidates
from engine.invoice.models import (
    InvoiceCorrection,
    InvoiceIdempotency,
    InvoiceJob,
    InvoiceNonce,
    InvoiceTemplate,
)
from engine.invoice.plugins import registry
from engine.invoice.reader import detect_media_type
from engine.invoice.templates import learn_rule, normalize_value
from engine.invoice.worker import callback_allowed
from engine.settings import get_settings

router = APIRouter(prefix="/v1", tags=["dokumen (invoice)"])
MB = 1024 * 1024
TEMPLATE_BODY_LIMIT = 256 * 1024
_KEY = re.compile(r"^[A-Za-z0-9_\-:.]{8,128}$")
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
SIGNED = {"description": "Wajib header X-Api-Key, X-Timestamp, X-Signature (HMAC). Lihat docs/api-reference.md"}


async def _read_body(request: Request, limit: int) -> bytes:
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > limit:
        raise api_error(413, "FILE_TOO_LARGE", f"request melebihi {limit // MB or 1} MB")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise api_error(413, "FILE_TOO_LARGE", f"request melebihi {limit // MB or 1} MB")
    return bytes(buf)


async def _multipart(request: Request, body: bytes) -> dict[str, Any]:
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise api_error(415, "UNSUPPORTED_MEDIA_TYPE", "body harus multipart/form-data")

    async def stream():
        yield body

    try:
        form = await MultiPartParser(request.headers, stream(), max_files=1, max_fields=10,
                                     max_part_size=64 * 1024).parse()
    except MultiPartException as e:
        raise api_error(400, "INVALID_REQUEST", f"multipart tidak valid: {e.message}") from e
    out: dict[str, Any] = {}
    for k, v in form.multi_items():
        if isinstance(v, UploadFile):
            out[k] = (v.filename, await v.read())
            await v.close()
        else:
            out[k] = v
    return out


def _job_body(job: InvoiceJob) -> dict[str, Any]:
    body: dict[str, Any] = {"job_id": job.id, "status": job.status, "document_type": job.document_type,
                            "created_at": job.created_at.isoformat() if job.created_at else None,
                            "finished_at": job.finished_at.isoformat() if job.finished_at else None}
    if job.status == "succeeded" and job.result:
        body.update(job.result)
        body["duration_ms"] = job.duration_ms
    elif job.status == "failed":
        body["error"] = {"code": job.error_code or "PROCESSING_ERROR", "message": job.error_message or ""}
    return body


@router.post("/jobs", status_code=202, summary="Kirim dokumen (PDF/JPEG/PNG) untuk OCR async", responses={
    202: {"description": '{"job_id": "...", "status": "queued"}'}, 401: SIGNED, 413: {}, 415: {}, 429: {}},
    openapi_extra={"requestBody": {"content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["file", "document_type"], "properties": {
            "file": {"type": "string", "format": "binary"},
            "document_type": {"type": "string", "example": "invoice"},
            "template_hint": {"type": "string", "description": "NPWP vendor (15/16 digit) jika diketahui"},
            "callback_url": {"type": "string", "description": "Opsional; host harus ada di "
                                                              "PQE_INVOICE_CALLBACK_HOSTS"}}}}}}})
async def create_job(request: Request):
    st = get_settings()
    caller, body = await _auth(request, st.invoice_max_upload_mb * MB + 64 * 1024)
    replay = await run_in_threadpool(_replayed_job, caller)
    if replay is not None:
        return replay
    form = await _multipart(request, body)
    return await run_in_threadpool(_create_job, caller, form)


async def _auth(request: Request, limit: int) -> tuple[signing.SignedCaller, bytes]:
    """API key + timestamp dicek sebelum body dibaca; tanda tangan setelah body (dibatasi) terbaca. Kerja DB di
    threadpool: event loop yang sama melayani analisis sinkron foto kurir."""
    pre = await run_in_threadpool(signing.precheck, request)
    body = await _read_body(request, limit)
    return await run_in_threadpool(signing.verify, request, body, pre), body


def _create_job(caller: signing.SignedCaller, form: dict[str, Any]) -> JSONResponse:
    st = get_settings()
    dtype = str(form.get("document_type") or "").strip()
    if dtype not in registry():
        raise api_error(422, "UNSUPPORTED_DOCUMENT_TYPE",
                        f"document_type harus salah satu dari: {', '.join(sorted(registry()))}")
    hint_raw = str(form.get("template_hint") or "").strip()
    hint = P.normalize_npwp(hint_raw) if hint_raw else None
    if hint_raw and not hint:
        raise api_error(422, "INVALID_TEMPLATE_HINT", "template_hint harus NPWP 15 atau 16 digit")
    callback = str(form.get("callback_url") or "").strip() or None
    if callback and not callback_allowed(callback):
        raise api_error(422, "CALLBACK_NOT_ALLOWED", "host callback_url tidak ada di PQE_INVOICE_CALLBACK_HOSTS")
    f = form.get("file")
    if not isinstance(f, tuple):
        raise api_error(422, "FILE_REQUIRED", "field file wajib (multipart)")
    filename, data = f
    if not data:
        raise api_error(422, "EMPTY_FILE", "file kosong")
    if len(data) > st.invoice_max_upload_mb * MB:
        raise api_error(413, "FILE_TOO_LARGE", f"file melebihi {st.invoice_max_upload_mb} MB")
    media = detect_media_type(data)
    if media is None:
        raise api_error(415, "UNSUPPORTED_MEDIA_TYPE", "hanya PDF, JPEG, dan PNG (dicek dari isi file)")
    try:
        with session_scope() as s:
            if queue.pending_count(s, caller.tenant_code) >= st.invoice_max_pending_per_tenant:
                raise api_error(429, "QUEUE_FULL", "antrean dokumen namespace ini penuh; coba lagi nanti")
            job = InvoiceJob(tenant_code=caller.tenant_code, key_id=caller.principal.key_id, document_type=dtype,
                             template_hint=hint, filename=store.safe_filename(filename), media_type=media,
                             size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), callback_url=callback)
            s.add(job)
            s.flush()
            store.put(s, job.id, data)
            s.add(InvoiceNonce(signature_sha256=caller.signature_digest, tenant_code=caller.tenant_code,
                               job_id=job.id, expires_at=caller.replay_until))
            jid = job.id
    except IntegrityError:  # request kembar yang sama persis datang bersamaan: kembalikan job yang menang
        replay = _replayed_job(caller)
        if replay is None:
            raise
        return replay
    return JSONResponse({"job_id": jid, "status": "queued", "poll_url": f"/v1/jobs/{jid}"}, status_code=202)


def _replayed_job(caller: signing.SignedCaller) -> JSONResponse | None:
    """Request dengan tanda tangan yang sama persis (retry jaringan / replay) → job yang sama, bukan job baru."""
    with session_scope() as s:
        n = s.get(InvoiceNonce, caller.signature_digest)
        if n is None or n.tenant_code != caller.tenant_code:
            return None
        job = s.get(InvoiceJob, n.job_id)
        status = job.status if job else "queued"
    return JSONResponse({"job_id": n.job_id, "status": status, "poll_url": f"/v1/jobs/{n.job_id}"},
                        status_code=202, headers={"Idempotent-Replayed": "true"})


@router.get("/jobs/{job_id}", summary="Status & hasil job dokumen", responses={401: SIGNED, 404: {}})
async def get_job(job_id: str, request: Request):
    caller, _ = await _auth(request, 64 * 1024)
    return await run_in_threadpool(_get_job, caller, job_id)


def _get_job(caller: signing.SignedCaller, job_id: str) -> JSONResponse:
    if not _JOB_ID.match(job_id):
        raise api_error(404, "JOB_NOT_FOUND", "job tidak ditemukan")
    with session_scope() as s:
        job = s.scalar(select(InvoiceJob).where(InvoiceJob.id == job_id, InvoiceJob.tenant_code == caller.tenant_code))
        if job is None:  # job namespace lain = tidak ada (tidak membocorkan keberadaannya)
            raise api_error(404, "JOB_NOT_FOUND", "job tidak ditemukan")
        return JSONResponse(_job_body(job))


class Issuer(BaseModel):
    npwp: str = Field(..., max_length=40)


class Correction(BaseModel):
    field: str = Field(..., max_length=50)
    extracted: Any = None
    corrected: Any = None
    idempotency_key: str = Field(..., pattern=r"^[A-Za-z0-9_\-]{16,128}$")  # kolom DB String(128)


class TemplateRequest(BaseModel):
    document_type: str = Field("invoice", max_length=50)
    issuer: Issuer
    job_id: str = Field(..., pattern=r"^[0-9a-f]{32}$")
    corrections: list[Correction] = Field(..., max_length=100)


def _template_body(t: InvoiceTemplate) -> dict[str, Any]:
    return {"document_type": t.document_type, "issuer": {"npwp": t.issuer_npwp}, "version": t.version,
            "fields": sorted(t.rules or {}), "updated_at": t.updated_at.isoformat() if t.updated_at else None}


def _check_issuer(job: InvoiceJob, doc: Document, npwp: str, req: TemplateRequest) -> None:
    """Template hanya untuk penjual dokumen ini: NPWP issuer harus template_hint job, vendor_npwp hasil job, atau
    vendor_npwp yang dikoreksi di request ini, dan tidak boleh NPWP di blok identitas pembeli."""
    cands = npwp_candidates(doc)
    seller = {v for v, _, _, buyer in cands if not buyer}
    buyer = {v for v, _, _, b in cands if b}
    if npwp in seller:
        return
    if npwp in buyer:
        raise api_error(422, "ISSUER_IS_BUYER", "issuer.npwp hanya muncul di identitas pembeli dokumen ini")
    if npwp == job.template_hint and not seller:  # vendor tanpa NPWP tercetak: percaya hint saat job dikirim
        return
    raise api_error(422, "ISSUER_MISMATCH", "issuer.npwp harus NPWP penjual yang tercetak di dokumen job ini "
                                            "(atau template_hint job bila dokumen tidak mencetak NPWP penjual)")


def _apply_corrections(tenant: str, req: TemplateRequest, npwp: str, actor: str) -> dict[str, Any]:
    with session_scope() as s:
        job = s.scalar(select(InvoiceJob).where(InvoiceJob.id == req.job_id, InvoiceJob.tenant_code == tenant))
        if job is None:
            raise api_error(404, "JOB_NOT_FOUND", "job tidak ditemukan")
        if job.status != "succeeded" or not job.result:
            raise api_error(409, "JOB_NOT_READY", "job belum selesai / gagal: koreksi butuh hasil job")
        if job.document_type != req.document_type:
            raise api_error(422, "DOCUMENT_TYPE_MISMATCH", "document_type berbeda dengan job")
        doc = Document.from_blocks(job.result.get("blocks") or [])
        _check_issuer(job, doc, npwp, req)
        t = s.scalar(select(InvoiceTemplate).where(
            InvoiceTemplate.tenant_code == tenant, InvoiceTemplate.document_type == req.document_type,
            InvoiceTemplate.issuer_npwp == npwp).with_for_update())
        if t is None:
            t = InvoiceTemplate(tenant_code=tenant, document_type=req.document_type, issuer_npwp=npwp, rules={},
                                version=1)
            s.add(t)
            s.flush()
        rules = dict(t.rules or {})
        changed = False
        out, seen = [], set()
        # satu field dipelajari sekali per request (koreksi terakhir menang): biaya belajar terbatas ±15 field
        last_of = {c.field: i for i, c in enumerate(req.corrections)}
        for i, c in enumerate(req.corrections):
            prev = s.scalar(select(InvoiceCorrection).where(
                InvoiceCorrection.tenant_code == tenant, InvoiceCorrection.idempotency_key == c.idempotency_key))
            if c.idempotency_key in seen or prev is not None:
                same = prev is None or (prev.field == c.field and prev.corrected == c.corrected)
                out.append({"idempotency_key": c.idempotency_key, "field": c.field,
                            "status": "duplicate" if same else "conflict"})
                continue
            seen.add(c.idempotency_key)
            learned, reason = False, None
            if c.field not in FIELD_KINDS:
                reason = "FIELD_NOT_LEARNABLE"
            elif last_of[c.field] != i:
                reason = "SUPERSEDED"
            elif c.corrected is None:
                learned, reason = c.field in rules, "RULE_REMOVED"
                rules.pop(c.field, None)
                changed = changed or learned
            else:
                rule, reason = learn_rule(doc, c.field, normalize_value(c.field, c.corrected), c.extracted)
                if rule is not None:
                    rules[c.field], learned, changed = rule, True, True
            s.add(InvoiceCorrection(tenant_code=tenant, idempotency_key=c.idempotency_key, template_id=t.id,
                                    job_id=req.job_id, field=c.field, extracted=c.extracted,
                                    corrected=c.corrected, learned=learned, reason=reason))
            out.append({"idempotency_key": c.idempotency_key, "field": c.field, "status": "applied",
                        "learned": learned, "reason": reason})
        if changed:
            t.rules = rules
            t.version = (t.version or 1) + 1
        s.add(AuditLog(actor=actor, action="invoice.template", target=f"{tenant}:{req.document_type}:{npwp}",
                       detail={"job_id": req.job_id, "applied": sum(o["status"] == "applied" for o in out)}))
        s.flush()
        return {"template": _template_body(t), "corrections": out}


@router.post("/templates", summary="Koreksi pengguna → belajar/update template issuer", responses={
    401: SIGNED, 404: {}, 409: {}, 422: {}})
async def update_template(request: Request):
    caller, body = await _auth(request, TEMPLATE_BODY_LIMIT)
    return await run_in_threadpool(_update_template, request.headers.get("idempotency-key"), caller, body)


def _update_template(idem: str | None, caller: signing.SignedCaller, body: bytes) -> JSONResponse:
    tenant = caller.tenant_code
    if idem is not None and not _KEY.match(idem):
        raise api_error(422, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key harus 8-128 karakter [A-Za-z0-9_-:.]")
    body_hash = hashlib.sha256(body).hexdigest()
    if idem:
        with session_scope() as s:
            prev = s.scalar(select(InvoiceIdempotency).where(InvoiceIdempotency.tenant_code == tenant,
                                                             InvoiceIdempotency.key == idem))
            if prev is not None:
                if prev.body_sha256 != body_hash:
                    raise api_error(422, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key sudah dipakai untuk body lain")
                return JSONResponse(prev.response, headers={"Idempotent-Replayed": "true"})
    try:
        req = TemplateRequest.model_validate(json.loads(body))
    except (ValueError, ValidationError, RecursionError) as e:
        raise api_error(422, "VALIDATION_ERROR", str(e)[:500] if not isinstance(e, RecursionError)
                        else "JSON terlalu dalam") from None
    if req.document_type not in registry():
        raise api_error(422, "UNSUPPORTED_DOCUMENT_TYPE", f"document_type {req.document_type} tidak terdaftar")
    npwp = P.normalize_npwp(req.issuer.npwp)
    if not npwp:
        raise api_error(422, "INVALID_NPWP", "issuer.npwp harus 15 atau 16 digit")
    for c in req.corrections:
        if c.field not in FIELDS:
            raise api_error(422, "UNKNOWN_FIELD", f"field {c.field} tidak dikenal")
        if c.corrected is not None and c.field in FIELD_KINDS and normalize_value(c.field, c.corrected) is None:
            raise api_error(422, "INVALID_VALUE", f"nilai corrected untuk {c.field} tidak valid")
        if len(json.dumps(c.corrected, default=str)) > 20_000:
            raise api_error(422, "INVALID_VALUE", f"nilai corrected untuk {c.field} terlalu besar")
    for attempt in (1, 2):  # request kembar bersamaan: yang kalah membaca ulang (key jadi duplicate)
        try:
            resp = _apply_corrections(tenant, req, npwp, caller.principal.actor)
            break
        except IntegrityError:
            if attempt == 2:
                raise
    if idem:
        try:
            with session_scope() as s:
                s.add(InvoiceIdempotency(tenant_code=tenant, key=idem, body_sha256=body_hash, response=resp))
        except IntegrityError:
            pass
    return JSONResponse(resp)


@router.get("/templates/{document_type}/{npwp}", summary="Lihat template issuer di namespace sendiri",
            responses={401: SIGNED, 404: {}})
async def get_template(document_type: str, npwp: str, request: Request):
    caller, _ = await _auth(request, 64 * 1024)
    return await run_in_threadpool(_get_template, caller, document_type, npwp)


def _get_template(caller: signing.SignedCaller, document_type: str, npwp: str) -> JSONResponse:
    n = P.normalize_npwp(npwp)
    with session_scope() as s:
        t = s.scalar(select(InvoiceTemplate).where(
            InvoiceTemplate.tenant_code == caller.tenant_code, InvoiceTemplate.document_type == document_type,
            InvoiceTemplate.issuer_npwp == (n or "")))
        if t is None:
            raise api_error(404, "TEMPLATE_NOT_FOUND", "template tidak ditemukan")
        return JSONResponse({**_template_body(t), "rules": t.rules})
