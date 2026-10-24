"""Autentikasi API key + role.

Role:
  admin    : semua akses, termasuk config & API key
  ops      : review foto, lihat semua tenant, reprocess, evidence
  viewer   : hanya baca (dashboard, foto)
  app      : aplikasi sumber; hanya tenant miliknya (analyze, baca foto, evidence)
  customer : pelanggan; hanya evidence untuk customer_id miliknya
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Query, Request
from sqlalchemy import select

from engine.db import ApiKey, AuditLog, as_aware, session_scope, utcnow

ROLES = ("admin", "ops", "viewer", "app", "customer")
KEY_PREFIX = "pqe_"


@dataclass
class Principal:
    key_id: str
    name: str
    role: str
    tenant_code: str | None
    customer_id: str | None

    @property
    def actor(self) -> str:
        return f"{self.role}:{self.name}"

    def can_tenant(self, tenant: str) -> bool:
        return self.tenant_code is None or self.tenant_code == tenant


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_api_key(name: str, role: str, tenant_code: str | None = None, customer_id: str | None = None,
                   actor: str = "system", raw: str | None = None) -> tuple[str, ApiKey]:
    """Buat API key baru. Nilai mentah hanya dikembalikan SEKALI (yang disimpan hanya hash)."""
    if role not in ROLES:
        raise ValueError(f"role harus salah satu dari {ROLES}")
    if role == "app" and not tenant_code:
        raise ValueError("role app wajib punya tenant")
    if role == "customer" and not customer_id:
        raise ValueError("role customer wajib punya customer_id")
    raw = raw or KEY_PREFIX + secrets.token_urlsafe(32)
    with session_scope() as s:
        k = ApiKey(name=name, prefix=raw[:12], key_hash=hash_key(raw), role=role,
                   tenant_code=tenant_code, customer_id=customer_id)
        s.add(k)
        s.add(AuditLog(actor=actor, action="apikey.create", target=name,
                       detail={"role": role, "tenant": tenant_code, "customer_id": customer_id}))
        s.flush()
        s.expunge(k)
    return raw, k


def ensure_bootstrap_key(raw: str) -> None:
    """Kunci admin awal dari env PQE_BOOTSTRAP_ADMIN_KEY (dibuat sekali)."""
    with session_scope() as s:
        if s.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(raw))):
            return
    create_api_key("bootstrap-admin", "admin", raw=raw, actor="system:bootstrap")


def _lookup(raw: str) -> Principal | None:
    with session_scope() as s:
        k = s.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(raw), ApiKey.revoked_at.is_(None)))
        if k is None:
            return None
        now = utcnow()
        if k.last_used_at is None or (now - as_aware(k.last_used_at)).total_seconds() > 60:
            k.last_used_at = now
        return Principal(k.id, k.name, k.role, k.tenant_code, k.customer_id)


def current_principal(
    request: Request,
    x_api_key: str | None = Header(None, description="API key, contoh: pqe_xxx"),
    authorization: str | None = Header(None),
    api_key: str | None = Query(None, include_in_schema=False),
) -> Principal:
    raw = x_api_key
    if not raw and authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    raw = raw or api_key
    if not raw:
        raise HTTPException(401, {"code": "UNAUTHORIZED", "message": "API key wajib (header X-API-Key)"})
    p = _lookup(raw)
    if p is None:
        raise HTTPException(401, {"code": "UNAUTHORIZED", "message": "API key tidak valid atau sudah dicabut"})
    request.state.principal = p
    return p


def require(*roles: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if p.role not in roles:
            raise HTTPException(403, {"code": "FORBIDDEN",
                                      "message": f"role {p.role} tidak boleh mengakses endpoint ini"})
        return p

    return dep


def check_tenant(p: Principal, tenant: str) -> None:
    if not p.can_tenant(tenant):
        raise HTTPException(403, {"code": "FORBIDDEN", "message": f"API key tidak punya akses ke tenant {tenant}"})
