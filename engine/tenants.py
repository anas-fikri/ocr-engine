"""Penyimpanan & cache config tenant. Database adalah sumber kebenaran; file YAML hanya untuk seed awal."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import select

from engine.db import AuditLog, Tenant, TenantConfigHistory, session_scope
from engine.mapping import validate_rules
from engine.tenant_config import Source, TenantConfig, parse_tenant_yaml

log = logging.getLogger(__name__)
CACHE_TTL_SEC = 5.0


@dataclass
class LoadedTenant:
    config: TenantConfig
    version: int
    enabled: bool


class TenantNotFound(Exception):
    pass


def validate_yaml(text: str) -> TenantConfig:
    cfg = parse_tenant_yaml(text)
    validate_rules(cfg.mapping)
    return cfg


class TenantRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, LoadedTenant] = {}
        self._loaded_at = 0.0

    def _refresh(self, force: bool = False) -> None:
        with self._lock:
            if not force and time.monotonic() - self._loaded_at < CACHE_TTL_SEC:
                return
            fresh: dict[str, LoadedTenant] = {}
            with session_scope() as s:
                for t in s.scalars(select(Tenant)):
                    prev = self._cache.get(t.code)
                    if prev and prev.version == t.version:
                        fresh[t.code] = LoadedTenant(prev.config, t.version, t.enabled)
                        continue
                    try:
                        fresh[t.code] = LoadedTenant(parse_tenant_yaml(t.config_yaml), t.version, t.enabled)
                    except Exception:  # config rusak tidak boleh menjatuhkan engine
                        log.exception("config tenant %s tidak valid, dilewati", t.code)
            self._cache = fresh
            self._loaded_at = time.monotonic()

    def invalidate(self) -> None:
        self._loaded_at = 0.0

    def get(self, code: str) -> LoadedTenant:
        self._refresh()
        t = self._cache.get(code)
        if t is None:
            self._refresh(force=True)
            t = self._cache.get(code)
        if t is None:
            raise TenantNotFound(code)
        return t

    def all(self, enabled_only: bool = True) -> list[LoadedTenant]:
        self._refresh()
        return [t for t in self._cache.values() if t.enabled or not enabled_only]

    def match_object(self, bucket: str, key: str) -> tuple[LoadedTenant, int, Source] | None:
        """Cari tenant + source yang memantau bucket/prefix ini (prefix terpanjang menang)."""
        from engine.settings import get_settings

        default_bucket = get_settings().aws_bucket
        best = None
        for t in self.all():
            for i, src in enumerate(t.config.sources):
                if src.type not in ("s3_poll", "s3_event"):
                    continue
                if (src.bucket or default_bucket) == bucket and key.startswith(src.prefix):
                    if best is None or len(src.prefix) > len(best[2].prefix):
                        best = (t, i, src)
        return best


registry = TenantRegistry()


def save_tenant(text: str, actor: str, *, create_only: bool = False) -> tuple[TenantConfig, int, bool]:
    """Validasi lalu simpan config. Return (config, version, created)."""
    cfg = validate_yaml(text)
    with session_scope() as s:
        t = s.get(Tenant, cfg.code)
        created = t is None
        if t is None:
            t = Tenant(code=cfg.code, name=cfg.name, config_yaml=text, version=1)
            s.add(t)
        elif create_only:
            return cfg, t.version, False
        else:
            if t.config_yaml == text:
                return cfg, t.version, False
            try:
                old_retention = parse_tenant_yaml(t.config_yaml).retention_days
            except Exception:
                old_retention = None
            if old_retention != cfg.retention_days:
                _update_expiry(s, cfg.code, cfg.retention_days)
            t.version += 1
            t.name = cfg.name
            t.config_yaml = text
        s.flush()
        s.add(TenantConfigHistory(tenant_code=cfg.code, version=t.version, config_yaml=text, changed_by=actor))
        s.add(AuditLog(actor=actor, action="tenant.create" if created else "tenant.update", target=cfg.code,
                       detail={"version": t.version}))
        version = t.version
    registry.invalidate()
    return cfg, version, created


def _update_expiry(s, tenant_code: str, days: int) -> None:
    """Hitung ulang expires_at semua foto tenant (received_at + days) langsung di database."""
    from sqlalchemy import text

    if s.get_bind().dialect.name == "postgresql":
        sql = "UPDATE photos SET expires_at = received_at + make_interval(days => :d) WHERE tenant_code = :t"
    else:
        sql = "UPDATE photos SET expires_at = datetime(received_at, '+' || :d || ' days') WHERE tenant_code = :t"
    s.execute(text(sql), {"d": days, "t": tenant_code})


def set_enabled(code: str, enabled: bool, actor: str) -> None:
    with session_scope() as s:
        t = s.get(Tenant, code)
        if t is None:
            raise TenantNotFound(code)
        t.enabled = enabled
        s.add(AuditLog(actor=actor, action="tenant.enable" if enabled else "tenant.disable", target=code))
    registry.invalidate()


def import_dir(path: Path, actor: str = "system:import", force: bool = False) -> list[str]:
    """Import semua *.yaml di folder. Tenant yang sudah ada TIDAK ditimpa kecuali force=True."""
    done = []
    if not path.exists():
        return done
    for f in sorted(path.glob("*.y*ml")):
        text = f.read_text(encoding="utf-8")
        if not isinstance(yaml.safe_load(text), dict):
            continue
        cfg, version, created = save_tenant(text, actor, create_only=not force)
        if created or force:
            done.append(f"{cfg.code} (v{version})")
    return done
