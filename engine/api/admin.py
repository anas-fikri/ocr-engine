"""Endpoint admin: tenant/config, API key, antrean job, status source, audit log."""

from __future__ import annotations

from typing import Annotated, Literal

import yaml
from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from engine.api.auth import ROLES, Principal, create_api_key, require
from engine.api.common import api_error
from engine.db import (
    ApiKey,
    AuditLog,
    Job,
    Photo,
    SourceState,
    Tenant,
    TenantConfigHistory,
    WebhookDelivery,
    session_scope,
    utcnow,
)
from engine.jobs import queue_stats
from engine.tenant_config import builtin_profiles
from engine.tenants import TenantNotFound, registry, save_tenant, set_enabled, validate_yaml

router = APIRouter(prefix="/v1/admin", tags=["admin"])
Admin = Annotated[Principal, Depends(require("admin"))]
Staff = Annotated[Principal, Depends(require("admin", "ops", "viewer"))]

YAML_BODY = Body(..., media_type="application/x-yaml", description="Config tenant dalam YAML")


def _config_error(e: Exception):
    if isinstance(e, ValidationError):
        msgs = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
        return api_error(422, "INVALID_CONFIG", "; ".join(msgs))
    if isinstance(e, yaml.YAMLError):
        return api_error(422, "INVALID_YAML", f"YAML tidak valid: {e}")
    return api_error(422, "INVALID_CONFIG", str(e))


@router.get("/tenants", summary="Daftar tenant")
def list_tenants(_: Staff):
    with session_scope() as s:
        rows = s.scalars(select(Tenant).order_by(Tenant.code)).all()
        out = []
        for t in rows:
            try:
                cfg = registry.get(t.code).config
                summary = {"sources": [x.type for x in cfg.sources], "sinks": len(cfg.sinks),
                           "modes": {n: cfg.resolve_profile(n)[1].mode for n in cfg.profile_names()}}
            except TenantNotFound:
                summary = {"error": "config tidak valid"}
            out.append({"code": t.code, "name": t.name, "version": t.version, "enabled": t.enabled,
                        "updated_at": t.updated_at.isoformat(), **summary})
        return out


@router.get("/tenants/{code}", summary="Config tenant (YAML) + profile hasil resolve")
def get_tenant(code: str, _: Staff):
    with session_scope() as s:
        t = s.get(Tenant, code)
        if t is None:
            raise api_error(404, "TENANT_NOT_FOUND", f"tenant {code} tidak ada")
        cfg = validate_yaml(t.config_yaml)
        return {
            "code": t.code, "name": t.name, "version": t.version, "enabled": t.enabled,
            "config_yaml": t.config_yaml,
            "resolved_profiles": {n: cfg.resolve_profile(n)[1].model_dump() for n in cfg.profile_names()},
        }


@router.post("/tenants/validate", summary="Validasi config tanpa menyimpan")
def validate_tenant(_: Admin, body: str = YAML_BODY):
    try:
        cfg = validate_yaml(body)
    except Exception as e:
        raise _config_error(e) from e
    return {"valid": True, "code": cfg.code,
            "resolved_profiles": {n: cfg.resolve_profile(n)[1].model_dump() for n in cfg.profile_names()}}


@router.put("/tenants", summary="Buat / perbarui tenant dari YAML")
def put_tenant(principal: Admin, body: str = YAML_BODY):
    try:
        cfg, version, created = save_tenant(body, principal.actor)
    except Exception as e:
        raise _config_error(e) from e
    return {"code": cfg.code, "version": version, "created": created}


@router.post("/tenants/{code}/enable", summary="Aktifkan tenant")
def enable_tenant(code: str, principal: Admin):
    try:
        set_enabled(code, True, principal.actor)
    except TenantNotFound as e:
        raise api_error(404, "TENANT_NOT_FOUND", code) from e
    return {"code": code, "enabled": True}


@router.post("/tenants/{code}/disable", summary="Nonaktifkan tenant (ingest berhenti, data tetap)")
def disable_tenant(code: str, principal: Admin):
    try:
        set_enabled(code, False, principal.actor)
    except TenantNotFound as e:
        raise api_error(404, "TENANT_NOT_FOUND", code) from e
    return {"code": code, "enabled": False}


@router.get("/tenants/{code}/history", summary="Riwayat versi config")
def tenant_history(code: str, _: Staff):
    with session_scope() as s:
        rows = s.scalars(select(TenantConfigHistory).where(TenantConfigHistory.tenant_code == code)
                         .order_by(TenantConfigHistory.version.desc())).all()
        return [{"version": r.version, "changed_by": r.changed_by, "changed_at": r.changed_at.isoformat(),
                 "config_yaml": r.config_yaml} for r in rows]


@router.get("/profiles/builtin", summary="Profile bawaan engine")
def builtin(_: Staff):
    return builtin_profiles()


class NewKey(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    role: str = Field(..., description=f"salah satu: {', '.join(ROLES)}")
    tenant: str | None = None
    customer_id: str | None = None


@router.get("/api-keys", summary="Daftar API key (tanpa nilai rahasia)")
def list_keys(_: Admin):
    with session_scope() as s:
        rows = s.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
        return [{"id": k.id, "name": k.name, "prefix": k.prefix, "role": k.role, "tenant": k.tenant_code,
                 "customer_id": k.customer_id, "created_at": k.created_at.isoformat(),
                 "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                 "revoked": k.revoked_at is not None} for k in rows]


@router.post("/api-keys", summary="Buat API key. Nilai key hanya ditampilkan sekali!")
def new_key(body: NewKey, principal: Admin):
    if body.tenant:
        try:
            registry.get(body.tenant)
        except TenantNotFound as e:
            raise api_error(404, "TENANT_NOT_FOUND", body.tenant) from e
    try:
        raw, k = create_api_key(body.name, body.role, body.tenant, body.customer_id, actor=principal.actor)
    except ValueError as e:
        raise api_error(422, "INVALID_KEY_REQUEST", str(e)) from e
    return {"id": k.id, "name": k.name, "role": k.role, "tenant": k.tenant_code, "customer_id": k.customer_id,
            "api_key": raw, "warning": "Simpan sekarang. Nilai ini tidak bisa dilihat lagi."}


@router.delete("/api-keys/{key_id}", summary="Cabut API key")
def revoke_key(key_id: str, principal: Admin):
    with session_scope() as s:
        k = s.get(ApiKey, key_id)
        if k is None:
            raise api_error(404, "KEY_NOT_FOUND", key_id)
        k.revoked_at = utcnow()
        s.add(AuditLog(actor=principal.actor, action="apikey.revoke", target=k.name))
    return {"id": key_id, "revoked": True}


@router.get("/jobs", summary="Status antrean + daftar job (default: dead)")
def jobs(_: Staff, status: Annotated[str, Query(pattern="^(queued|running|done|dead)$")] = "dead",
         limit: Annotated[int, Query(ge=1, le=500)] = 100):
    with session_scope() as s:
        rows = s.execute(select(Job, Photo.object_key, Photo.tenant_code).join(Photo, Photo.id == Job.photo_id)
                         .where(Job.status == status).order_by(Job.id.desc()).limit(limit)).all()
        return {"stats": queue_stats(s), "items": [
            {"id": j.id, "photo_id": j.photo_id, "tenant": t, "object_key": k, "attempts": j.attempts,
             "last_error": j.last_error, "updated_at": j.updated_at.isoformat()} for j, k, t in rows]}


@router.post("/jobs/{job_id}/retry", summary="Ulangi job dead")
def retry_job(job_id: int, principal: Admin):
    with session_scope() as s:
        j = s.get(Job, job_id)
        if j is None:
            raise api_error(404, "JOB_NOT_FOUND", str(job_id))
        j.status, j.attempts, j.run_after, j.last_error = "queued", 0, utcnow(), None
        s.add(AuditLog(actor=principal.actor, action="job.retry", target=str(job_id)))
    return {"id": job_id, "status": "queued"}


@router.post("/jobs/retry-dead", summary="Ulangi semua job dead")
def retry_dead(principal: Admin):
    with session_scope() as s:
        rows = s.scalars(select(Job).where(Job.status == "dead")).all()
        for j in rows:
            j.status, j.attempts, j.run_after, j.last_error = "queued", 0, utcnow(), None
        s.add(AuditLog(actor=principal.actor, action="job.retry_dead", detail={"count": len(rows)}))
    return {"requeued": len(rows)}


@router.get("/webhooks", summary="Pengiriman webhook (default: dead)")
def webhooks(_: Staff, status: Annotated[str, Query(pattern="^(pending|done|dead)$")] = "dead",
             limit: Annotated[int, Query(ge=1, le=500)] = 100):
    with session_scope() as s:
        rows = s.scalars(select(WebhookDelivery).where(WebhookDelivery.status == status)
                         .order_by(WebhookDelivery.id.desc()).limit(limit)).all()
        return [{"id": d.id, "tenant": d.tenant_code, "url": d.url, "event": d.event, "attempts": d.attempts,
                 "last_error": d.last_error, "photo_id": (d.payload.get("photo") or {}).get("id"),
                 "created_at": d.created_at.isoformat()} for d in rows]


@router.post("/webhooks/retry-dead", summary="Kirim ulang semua webhook dead")
def retry_dead_webhooks(principal: Admin):
    with session_scope() as s:
        rows = s.scalars(select(WebhookDelivery).where(WebhookDelivery.status == "dead")).all()
        for d in rows:
            d.status, d.attempts, d.next_attempt_at, d.last_error = "pending", 0, utcnow(), None
        s.add(AuditLog(actor=principal.actor, action="webhook.retry_dead", detail={"count": len(rows)}))
    return {"requeued": len(rows)}


@router.get("/sources", summary="Status setiap source ingest")
def sources(_: Staff):
    with session_scope() as s:
        rows = s.scalars(select(SourceState).order_by(SourceState.id)).all()
        return [{"id": r.id, "checkpoint": r.checkpoint, "last_found": r.last_found, "last_error": r.last_error,
                 "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
                 "last_reconcile_at": r.last_reconcile_at.isoformat() if r.last_reconcile_at else None}
                for r in rows]


@router.get("/audit", summary="Audit log")
def audit(_: Admin, limit: Annotated[int, Query(ge=1, le=500)] = 100):
    with session_scope() as s:
        rows = s.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()
        return [{"actor": r.actor, "action": r.action, "target": r.target, "detail": r.detail,
                 "at": r.created_at.isoformat()} for r in rows]


# ---------------- Pengaturan SILO ----------------

class StorageSettingsIn(BaseModel):
    """Semua field opsional. Field kosong = tidak diubah. `mc_config` = JSON format mc/MinIO client."""

    endpoint: str | None = Field(None, examples=["https://s3.pcpexpress.com"])
    public_endpoint: str | None = Field(None, description="Host untuk link foto di browser/pelanggan")
    region: str | None = None
    access_key: str | None = None
    secret_key: str | None = Field(None, description="Disimpan terenkripsi, tidak pernah ditampilkan lagi")
    addressing_style: str | None = Field(None, description="auto | path | virtual")
    signature_version: str | None = Field(None, description="s3v4 | s3")
    app_bucket: str | None = Field(None, description="Bucket foto aplikasi (engine hanya membaca)")
    processed_bucket: str | None = None
    thumb_bucket: str | None = None
    upload_bucket: str | None = None
    mc_config: dict | None = Field(None, examples=[{"url": "http://127.0.0.1:9000", "accessKey": "...",
                                                   "secretKey": "...", "api": "s3v4", "path": "auto"}])
    clear: list[str] = Field(default_factory=list, description="Field yang dikembalikan ke nilai env")

    def values(self) -> dict:
        from engine.storage_config import from_mc_config

        data = self.model_dump(exclude={"mc_config", "clear"})
        if self.mc_config:
            data.update({k: v for k, v in from_mc_config(self.mc_config).items() if not data.get(k)})
        return {k: v.strip() if isinstance(v, str) else v for k, v in data.items() if v not in (None, "")}


class BackupSettingsIn(BaseModel):
    enabled: bool | None = None
    target: Literal["folder", "s3"] | None = None
    folder_path: str | None = None
    s3_endpoint: str | None = None
    s3_region: str | None = None
    s3_bucket: str | None = None
    s3_prefix: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_addressing_style: Literal["auto", "path", "virtual"] | None = None
    include_photos: bool | None = None
    include_upload_originals: bool | None = None
    include_processed: bool | None = None
    include_thumbs: bool | None = None
    include_database: bool | None = None
    photo_interval_minutes: int | None = None
    db_interval_hours: int | None = None
    db_keep: int | None = None

    def values(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


@router.get("/settings/backup", summary="Konfigurasi & status backup NAS (rahasia disamarkan)")
def get_backup_settings(_: Admin):
    from engine import backup

    return backup.describe()


@router.put("/settings/backup", summary="Simpan konfigurasi backup NAS (berlaku di putaran worker berikutnya)")
def put_backup_settings(body: BackupSettingsIn, principal: Admin):
    from engine import backup
    from engine.secretbox import SecretKeyMissing

    try:
        backup.save(body.values(), principal.actor)
    except SecretKeyMissing as e:
        raise api_error(409, "SECRET_KEY_MISSING",
                        "PQE_SECRET_KEY belum diisi di server, secret key NAS tidak bisa disimpan terenkripsi.") from e
    except ValueError as e:
        raise api_error(422, "INVALID_SETTINGS", str(e)) from e
    return backup.describe()


@router.post("/settings/backup/test", summary="Uji tujuan backup NAS (dengan nilai form, sebelum disimpan)")
def test_backup_settings(body: BackupSettingsIn, _: Admin):
    from engine import backup

    try:
        return backup.test(body.values())
    except ValueError as e:
        raise api_error(422, "INVALID_SETTINGS", str(e)) from e


class BackupRunIn(BaseModel):
    kind: Literal["photos", "database", "all"] = "all"


@router.post("/backup/run", summary="Jalankan backup NAS sekarang (dikerjakan worker dalam ±10 detik)")
def run_backup(body: BackupRunIn, principal: Admin):
    from engine import backup

    backup.request_run(body.kind, principal.actor)
    return backup.describe()


@router.get("/preflight", summary="Pemeriksaan kesiapan production (secret, SILO, bucket, NAS, tenant, worker)")
def preflight(_: Admin, check_silo: bool = True):
    from engine.preflight import run

    return run(check_silo=check_silo)


@router.get("/settings/storage", summary="Konfigurasi SILO efektif (rahasia disamarkan)")
def get_storage_settings(_: Admin):
    from engine.storage_config import describe

    return describe()


@router.put("/settings/storage", summary="Simpan konfigurasi SILO (berlaku tanpa restart, maks. 10 detik)")
def put_storage_settings(body: StorageSettingsIn, principal: Admin):
    from engine.secretbox import SecretKeyMissing
    from engine.storage import reset_storage_cache
    from engine.storage_config import describe, save

    try:
        version = save(body.values(), principal.actor, body.clear)
    except SecretKeyMissing as e:
        raise api_error(409, "SECRET_KEY_MISSING",
                        "PQE_SECRET_KEY belum diisi di server, secret key SILO tidak bisa disimpan terenkripsi. "
                        "Isi PQE_SECRET_KEY lalu restart, atau isi AWS_SECRET_ACCESS_KEY lewat env.") from e
    except ValueError as e:
        raise api_error(422, "INVALID_SETTINGS", str(e)) from e
    reset_storage_cache()
    return {"version": version, **describe()}


@router.post("/settings/storage/test", summary="Uji koneksi SILO (dengan nilai form, sebelum disimpan)")
def test_storage_settings(body: StorageSettingsIn, _: Admin):

    from botocore.exceptions import ClientError

    from engine.storage import Storage
    from engine.storage_config import effective_settings, validate

    values = body.values()
    try:
        validate(values)
    except ValueError as e:
        raise api_error(422, "INVALID_SETTINGS", str(e)) from e
    settings, _version = effective_settings(values)
    checks = []

    def run(name: str, fn):
        try:
            detail = fn()
            checks.append({"check": name, "ok": True, "detail": detail})
        except Exception as e:  # semua error koneksi ditampilkan ke admin apa adanya
            code = getattr(e, "response", {}).get("Error", {}).get("Code") if isinstance(e, ClientError) else None
            checks.append({"check": name, "ok": False, "detail": f"{code or type(e).__name__}: {e}"[:300]})

    st = Storage(settings)
    def read_check():
        sample = [o for _, o in zip(range(5), st.list(settings.aws_bucket), strict=False)]
        return f"bisa di-list ({len(sample)} objek contoh)"

    run(f"Baca bucket aplikasi '{settings.aws_bucket}'", read_check)

    def get_check():
        first = next(iter(st.list(settings.aws_bucket)), None)
        if first is None:
            return "bucket kosong, tidak ada foto untuk dicoba"
        st.head(settings.aws_bucket, first.key)
        return "bisa diunduh (Web UI, evidence, backup NAS)"

    run("Baca isi foto", get_check)
    return {"ok": all(c["ok"] for c in checks), "endpoint": settings.aws_endpoint, "checks": checks}
