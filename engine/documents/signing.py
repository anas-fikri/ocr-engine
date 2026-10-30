"""Tanda tangan HMAC request modul dokumen (FR-OCR-008) dan api_secret per API key.

Kanonik (sama untuk request ke engine dan callback dari engine):

    timestamp + "\\n" + METHOD + "\\n" + path + "\\n" + hex(sha256(body))

X-Signature = hex(HMAC-SHA256(api_secret, kanonik)). `path` tanpa query string.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Request

from engine import metrics, secretbox
from engine.api.auth import Principal, _lookup
from engine.api.common import api_error
from engine.db import ApiKey, AuditLog, session_scope, utcnow
from engine.documents.models import ApiKeySecret
from engine.settings import get_settings
from engine.tenants import TenantNotFound, registry


def canonical(timestamp: str, method: str, path: str, body: bytes) -> bytes:
    return f"{timestamp}\n{method.upper()}\n{path}\n{hashlib.sha256(body).hexdigest()}".encode()


def sign(secret: str, timestamp: str, method: str, path: str, body: bytes) -> str:
    return hmac.new(secret.encode(), canonical(timestamp, method, path, body), hashlib.sha256).hexdigest()


def set_api_secret(key_id: str, actor: str = "system") -> str:
    """Buat (atau ganti) api_secret untuk API key. Nilai mentah hanya dikembalikan SEKALI."""
    if not secretbox.available():
        raise secretbox.SecretKeyMissing("PQE_SECRET_KEY belum diisi: api_secret tidak bisa disimpan terenkripsi")
    raw = secrets.token_hex(32)
    with session_scope() as s:
        if s.get(ApiKey, key_id) is None:
            raise KeyError(key_id)
        row = s.get(ApiKeySecret, key_id)
        if row is None:
            s.add(ApiKeySecret(key_id=key_id, secret_enc=secretbox.encrypt(raw)))
        else:
            row.secret_enc, row.created_at = secretbox.encrypt(raw), utcnow()
        s.add(AuditLog(actor=actor, action="apikey.secret", target=key_id))
    return raw


def get_api_secret(key_id: str) -> str | None:
    """Secret milik key yang masih aktif (key dicabut → None: request & callback berhenti)."""
    with session_scope() as s:
        row = s.get(ApiKeySecret, key_id)
        key = s.get(ApiKey, key_id)
        if row is None or key is None or key.revoked_at is not None:
            return None
        return secretbox.decrypt(row.secret_enc)


@dataclass
class SignedCaller:
    principal: Principal
    tenant_code: str
    secret: str
    signature_digest: str  # sha256(X-Signature): kunci anti-replay POST /v1/jobs
    replay_until: datetime


def _deny(status: int, code: str, message: str):
    metrics.DOCS_AUTH_FAILURES.labels(code=code).inc()
    return api_error(status, code, message)


_TS = re.compile(r"^\d{1,12}$")
_SIG = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass
class Precheck:
    principal: Principal
    secret: str
    timestamp: str
    signature: str


def signed_path(request: Request) -> str:
    """Path persis seperti dikirim klien (belum di-decode, tanpa query): yang ditandatangani klien."""
    raw = request.scope.get("raw_path")
    return raw.decode("latin-1") if raw else request.url.path


def precheck(request: Request) -> Precheck:
    """Cek header, timestamp, API key, dan api_secret SEBELUM body dibaca: request tanpa key valid tidak bisa membuat
    server membaca body 20 MB."""
    h = request.headers
    raw_key, ts, sig = h.get("x-api-key"), h.get("x-timestamp"), h.get("x-signature")
    if not raw_key:
        raise _deny(401, "UNAUTHORIZED", "API key wajib (header X-Api-Key)")
    if not ts or not sig:
        raise _deny(401, "SIGNATURE_REQUIRED", "header X-Timestamp dan X-Signature wajib")
    if not _TS.match(ts):
        raise _deny(401, "TIMESTAMP_OUT_OF_WINDOW", "X-Timestamp harus Unix detik (angka saja)")
    window = get_settings().docs_signature_window_sec
    if abs(time.time() - int(ts)) > window:
        raise _deny(401, "TIMESTAMP_OUT_OF_WINDOW", f"X-Timestamp di luar ±{window} detik dari jam server")
    if not _SIG.match(sig):
        raise _deny(401, "INVALID_SIGNATURE", "X-Signature harus hex HMAC-SHA256 (64 karakter)")
    p = _lookup(raw_key)
    if p is None:
        raise _deny(401, "UNAUTHORIZED", "API key tidak valid atau sudah dicabut")
    secret = get_api_secret(p.key_id)
    if not secret:
        raise _deny(403, "SIGNATURE_NOT_ENABLED",
                    "API key ini belum punya api_secret (buat dengan: pqe create-key ... --with-secret)")
    return Precheck(p, secret, ts, sig.lower())


def verify(request: Request, body: bytes, pre: Precheck | None = None) -> SignedCaller:
    """Verifikasi tanda tangan atas body yang sudah dibaca (dibatasi), lalu role & tenant."""
    pre = pre or precheck(request)
    p, secret, ts = pre.principal, pre.secret, pre.timestamp
    expected = sign(secret, ts, request.method, signed_path(request), body)
    if not hmac.compare_digest(expected, pre.signature):
        raise _deny(401, "INVALID_SIGNATURE", "X-Signature tidak cocok")
    if p.role != "app" or not p.tenant_code:
        raise _deny(403, "FORBIDDEN", "endpoint dokumen hanya untuk API key role app yang terikat tenant")
    try:
        t = registry.get(p.tenant_code)
    except TenantNotFound:
        raise _deny(403, "FORBIDDEN", f"tenant {p.tenant_code} tidak terdaftar") from None
    if not t.enabled:
        raise _deny(409, "TENANT_DISABLED", f"tenant {p.tenant_code} sedang dinonaktifkan")
    request.state.principal = p
    window = get_settings().docs_signature_window_sec
    return SignedCaller(p, p.tenant_code, secret, hashlib.sha256(expected.encode()).hexdigest(),
                        datetime.fromtimestamp(int(ts) + window + 5, UTC))
