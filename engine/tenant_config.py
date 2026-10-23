"""Skema config tenant & profile, validasi, dan resolusi profile (bawaan + override tenant)."""

from __future__ import annotations

import copy
from functools import lru_cache
from importlib import resources
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

CHECKS = ("sharpness", "resolution", "exposure", "exif", "duplicate", "ocr")
SCORED_CHECKS = ("sharpness", "resolution", "exposure", "ocr", "authenticity")
MAPPABLE_FIELDS = (
    "awb", "courier_id", "photo_type", "customer_id", "branch",
    "captured_at", "lat", "lng", "source", "external_id",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Threshold(_Strict):
    reject: float | None = None
    warn: float | None = None


class Guard(_Strict):
    min_ssim: float = 0.90


class Compression(_Strict):
    enabled: bool = True
    format: Literal["jpeg", "webp"] = "jpeg"
    target_max_bytes: int = 1_000_000
    skip_if_below_bytes: int = 1_000_000
    max_long_side_px: int = 2000
    min_long_side_px: int = 1024
    min_quality: int = Field(55, ge=10, le=95)
    max_quality: int = Field(90, ge=10, le=98)
    guard: Guard = Guard()


class Thumbnail(_Strict):
    long_side_px: int = 320
    quality: int = 70


class Enhance(_Strict):
    """Perbaikan pencahayaan otomatis (tanpa AI). Lihat engine/imaging/enhance.py."""

    enabled: bool = True


class Profile(_Strict):
    mode: Literal["shadow", "warn", "enforce"] = "shadow"
    checks: list[str]
    thresholds: dict[str, Threshold]
    params: dict[str, dict[str, Any]] = {}
    compression: Compression = Compression()
    thumbnail: Thumbnail = Thumbnail()
    enhance: Enhance = Enhance()

    @field_validator("checks")
    @classmethod
    def _known_checks(cls, v: list[str]) -> list[str]:
        unknown = set(v) - set(CHECKS)
        if unknown:
            raise ValueError(f"check tidak dikenal: {sorted(unknown)}. Pilihan: {list(CHECKS)}")
        return v

    @field_validator("thresholds")
    @classmethod
    def _known_thresholds(cls, v: dict[str, Threshold]) -> dict[str, Threshold]:
        unknown = set(v) - set(SCORED_CHECKS)
        if unknown:
            raise ValueError(f"threshold tidak dikenal: {sorted(unknown)}. Pilihan: {list(SCORED_CHECKS)}")
        return v

    def param(self, check: str, name: str, default: Any = None) -> Any:
        return self.params.get(check, {}).get(name, default)


class Source(_Strict):
    type: Literal["s3_poll", "s3_event", "db_poll"]
    bucket: str | None = None
    prefix: str = ""
    # contoh "%Y/%m/%d/": hanya prefix tanggal terbaru yang di-list, jauh lebih ringan
    date_prefix_format: str | None = None
    lookback_days: int = 2
    poll_interval_sec: int = 15
    suffixes: list[str] = [".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"]
    # db_poll
    dsn_env: str | None = None
    query: str | None = None
    url_column: str = "photo_url"
    checkpoint_column: str = "created_at"


class Sink(_Strict):
    type: Literal["webhook"] = "webhook"
    url: str
    secret_env: str | None = None
    secret: str | None = None
    events: list[Literal["analysis.completed", "review.updated"]] = ["analysis.completed"]
    timeout_sec: float = 5.0


class TenantConfig(_Strict):
    code: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str
    default_lang: Literal["id", "en"] = "id"
    timezone: str = "Asia/Jakarta"
    retention_days: int = 1095
    store_mode: Literal["processed", "none"] = "processed"
    # false (default): foto PASS & WARN langsung masuk evidence, review ops opsional.
    # true: foto WARN baru masuk evidence setelah di-approve ops.
    review_required: bool = False
    upload_bucket: str | None = None
    default_profile: str = "default"
    sources: list[Source] = []
    mapping: dict[str, list[str]] = {}
    sinks: list[Sink] = []
    profiles: dict[str, dict[str, Any]] = {}

    @field_validator("mapping")
    @classmethod
    def _known_fields(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        unknown = set(v) - set(MAPPABLE_FIELDS)
        if unknown:
            raise ValueError(f"field mapping tidak dikenal: {sorted(unknown)}. Pilihan: {list(MAPPABLE_FIELDS)}")
        return v

    def profile_names(self) -> list[str]:
        return sorted(set(builtin_profiles()) | set(self.profiles))

    def resolve_profile(self, photo_type: str | None) -> tuple[str, Profile]:
        """Kembalikan (nama_profile, Profile) untuk jenis foto. Fallback ke default_profile."""
        names = set(builtin_profiles()) | set(self.profiles)
        name = photo_type if photo_type in names else self.default_profile
        return name, build_profile(name, self.profiles)

    def validate_all_profiles(self) -> None:
        for name in self.profile_names():
            build_profile(name, self.profiles)


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


@lru_cache
def builtin_profiles() -> dict[str, dict]:
    text = resources.files("engine").joinpath("defaults/profiles.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def build_profile(name: str, tenant_profiles: dict[str, dict] | None = None) -> Profile:
    """default bawaan <- profile bawaan bernama sama <- `extends` <- override tenant."""
    builtins = builtin_profiles()
    tenant_profiles = tenant_profiles or {}
    override = dict(tenant_profiles.get(name, {}))
    parent = override.pop("extends", None)
    data = deep_merge({}, builtins["default"])
    if name != "default":
        data = deep_merge(data, {k: v for k, v in tenant_profiles.get("default", {}).items() if k != "extends"})
    if parent and parent != name:
        data = deep_merge(data, builtins.get(parent, {}))
        data = deep_merge(data, {k: v for k, v in tenant_profiles.get(parent, {}).items() if k != "extends"})
    data = deep_merge(data, builtins.get(name, {}) if name != "default" else {})
    data = deep_merge(data, override)
    return Profile.model_validate(data)


def parse_tenant_yaml(text: str) -> TenantConfig:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("config tenant harus berupa objek YAML")
    cfg = TenantConfig.model_validate(data)
    cfg.validate_all_profiles()
    return cfg
