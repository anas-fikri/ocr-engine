"""Kontrak analyzer plugin. Lihat docs/development.md untuk cara menambah analyzer baru."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from engine.imaging.loader import LoadedImage
from engine.tenant_config import Profile


@dataclass
class Finding:
    """Temuan tambahan di luar skor utama.

    severity:
      - "info": hanya informasi (contoh EXIF_MISSING), tidak memengaruhi verdict
      - "risk": menambah risiko keaslian sebanyak `points`
    """

    code: str
    severity: str
    points: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckResult:
    check: str
    score: float | None = None  # 0-100, makin tinggi makin baik. None = tidak dinilai
    issue_code: str | None = None  # kode alasan jika skor di bawah threshold
    metrics: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    duration_ms: int = 0


@dataclass
class DuplicateMatch:
    photo_id: str
    awb: str | None
    object_key: str | None
    distance: int
    exact: bool


DuplicateLookup = Callable[[str, int, int], list[DuplicateMatch]]


@dataclass
class AnalysisContext:
    profile: Profile
    meta: dict[str, Any] = field(default_factory=dict)  # field hasil mapping: awb, captured_at, lat, ...
    received_at: datetime | None = None  # timezone-aware
    timezone: str = "Asia/Jakarta"
    # (sha256, phash_int, max_distance) -> daftar foto lain yang mirip. None = cek duplikat dilewati
    find_duplicates: DuplicateLookup | None = None


class Analyzer(Protocol):
    name: str

    def __call__(self, img: LoadedImage, ctx: AnalysisContext) -> CheckResult: ...


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def linear_score(value: float, bad: float, good: float) -> float:
    """Skor 0-100 linear: `bad` -> 0, `good` -> 100 (arah bebas)."""
    if good == bad:
        return 100.0
    return round(100.0 * clamp01((value - bad) / (good - bad)), 1)
