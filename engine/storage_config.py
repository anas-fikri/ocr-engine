"""Konfigurasi koneksi SILO yang bisa diubah lewat Web UI (menimpa nilai env).

Urutan prioritas per field: database (halaman "Pengaturan SILO") > environment variable > bawaan.
Secret key disimpan terenkripsi (lihat secretbox.py). Semua proses (API, worker, ingestor)
membaca perubahan paling lambat REFRESH_SEC detik, tanpa restart.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from engine import secretbox
from engine.db import AuditLog, Setting, session_scope
from engine.settings import Settings, get_settings

KEY = "storage"
REFRESH_SEC = 10.0

# nama field di form/API -> field Settings
FIELDS: dict[str, str] = {
    "endpoint": "aws_endpoint",
    "public_endpoint": "aws_public_endpoint",
    "region": "aws_default_region",
    "access_key": "aws_access_key_id",
    "secret_key": "aws_secret_access_key",
    "addressing_style": "aws_addressing_style",
    "signature_version": "aws_signature_version",
    "app_bucket": "aws_bucket",
    "processed_bucket": "engine_bucket_processed",
    "thumb_bucket": "engine_bucket_thumb",
    "upload_bucket": "engine_bucket_upload",
}
SECRET_FIELDS = {"secret_key"}
ADDRESSING = {"auto", "path", "virtual"}
SIGNATURES = {"s3v4", "s3"}

_lock = threading.Lock()
_cache: dict[str, Any] = {"checked": 0.0, "version": None, "values": {}}


def from_mc_config(cfg: dict) -> dict[str, str]:
    """Konversi format konfigurasi `mc`/MinIO client:
    {"url": ..., "accessKey": ..., "secretKey": ..., "api": "s3v4", "path": "auto"}.
    """
    out: dict[str, str] = {}
    if cfg.get("url"):
        out["endpoint"] = str(cfg["url"]).rstrip("/")
    if cfg.get("accessKey"):
        out["access_key"] = str(cfg["accessKey"])
    if cfg.get("secretKey"):
        out["secret_key"] = str(cfg["secretKey"])
    api = str(cfg.get("api", "")).lower()
    if api:
        out["signature_version"] = "s3" if api in ("s3v2", "s3") else "s3v4"
    path = str(cfg.get("path", "")).lower()
    if path:
        out["addressing_style"] = {"on": "path", "off": "virtual", "dns": "virtual"}.get(path, "auto")
    return out


def validate(values: dict[str, Any]) -> None:
    unknown = set(values) - set(FIELDS)
    if unknown:
        raise ValueError(f"field tidak dikenal: {sorted(unknown)}")
    if values.get("addressing_style") not in (None, "", *ADDRESSING):
        raise ValueError("addressing_style harus auto, path, atau virtual")
    if values.get("signature_version") not in (None, "", *SIGNATURES):
        raise ValueError("signature_version harus s3v4 atau s3")
    for k in ("endpoint", "public_endpoint"):
        v = values.get(k)
        if v and not str(v).startswith(("http://", "https://")):
            raise ValueError(f"{k} harus diawali http:// atau https://")


def _load_db() -> tuple[int | None, dict[str, Any]]:
    with session_scope() as s:
        row = s.get(Setting, KEY)
        if row is None:
            return None, {}
        values = dict(row.value or {})
        version = row.version
    if "secret_key_enc" in values:
        secret = secretbox.decrypt(values.pop("secret_key_enc"))
        if secret:
            values["secret_key"] = secret
    return version, values


def db_values(force: bool = False) -> tuple[int | None, dict[str, Any]]:
    with _lock:
        now = time.monotonic()
        if force or now - _cache["checked"] >= REFRESH_SEC:
            try:
                _cache["version"], _cache["values"] = _load_db()
            except Exception:  # DB belum siap (misal saat start): pakai env saja
                pass
            _cache["checked"] = now
        return _cache["version"], dict(_cache["values"])


def invalidate() -> None:
    with _lock:
        _cache["checked"] = 0.0


def effective_settings(overrides: dict[str, Any] | None = None) -> tuple[Settings, int | None]:
    """Settings dengan nilai SILO dari database (dan `overrides` opsional, untuk tombol Test)."""
    base = get_settings()
    version, values = db_values()
    merged = {k: v for k, v in values.items() if v not in (None, "")}
    merged.update({k: v for k, v in (overrides or {}).items() if v not in (None, "")})
    update = {FIELDS[k]: v for k, v in merged.items() if k in FIELDS}
    return base.model_copy(update=update), version


def describe() -> dict[str, Any]:
    """Nilai efektif untuk ditampilkan di Web UI (rahasia disamarkan) + asal setiap nilai."""
    base = get_settings()
    _, values = db_values(force=True)
    eff, version = effective_settings()
    out: dict[str, Any] = {"version": version, "fields": {}, "secret_key_configured": secretbox.available()}
    for k, attr in FIELDS.items():
        v = getattr(eff, attr)
        source = "database" if values.get(k) not in (None, "") else ("env" if getattr(base, attr) else "default")
        if k in SECRET_FIELDS:
            v = {"set": bool(v)}
        elif k == "access_key" and v:
            v = v[:3] + "…" + v[-2:] if len(v) > 6 else "…"
        out["fields"][k] = {"value": v, "source": source}
    return out


def save(values: dict[str, Any], actor: str, clear: list[str] | None = None) -> int:
    """Simpan perubahan (field kosong diabaikan; `clear` = kembalikan field ke nilai env)."""
    validate(values)
    for k in clear or []:
        if k not in FIELDS:
            raise ValueError(f"field tidak dikenal: {k}")
    with session_scope() as s:
        row = s.get(Setting, KEY)
        current = dict(row.value or {}) if row else {}
        for k, v in values.items():
            if v in (None, ""):
                continue
            if k in SECRET_FIELDS:
                current["secret_key_enc"] = secretbox.encrypt(str(v))
            else:
                current[k] = str(v)
        for k in clear or []:
            current.pop("secret_key_enc" if k in SECRET_FIELDS else k, None)
        if row is None:
            row = Setting(key=KEY, value=current, version=1, updated_by=actor)
            s.add(row)
        else:
            row.value = current
            row.version += 1
            row.updated_by = actor
        s.flush()
        changed = sorted(k for k, v in values.items() if v not in (None, ""))
        s.add(AuditLog(actor=actor, action="settings.storage.update", target=KEY,
                       detail={"changed": changed, "cleared": clear or [], "version": row.version}))
        version = row.version
    invalidate()
    return version
