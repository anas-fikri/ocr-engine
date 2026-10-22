"""Pipeline murni (tanpa DB/S3): bytes gambar + profile -> hasil analisis, kompresi, thumbnail.

Dipakai oleh worker, endpoint API sinkron/preview, dan CLI `pqe analyze`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from engine.analyzers import REGISTRY, AnalysisContext, CheckResult
from engine.imaging.compressor import CompressionResult, compress, thumbnail
from engine.imaging.loader import LoadedImage, load_image
from engine.verdict import Reason, Verdict, decide


@dataclass
class PipelineResult:
    image: LoadedImage
    verdict: Verdict
    checks: list[CheckResult]
    compression: CompressionResult | None = None
    thumbnail: bytes | None = None
    timings_ms: dict[str, int] = field(default_factory=dict)

    @property
    def phash(self) -> str | None:
        for c in self.checks:
            if c.check == "duplicate":
                return c.metrics.get("phash")
        return None

    @property
    def exif(self) -> dict:
        for c in self.checks:
            if c.check == "exif":
                return c.metrics
        return {}

    def reasons(self) -> list[dict]:
        out = [r.to_dict() for r in self.verdict.reasons]
        if self.compression and not self.compression.target_met and not self.compression.passthrough:
            out.append(Reason("COMPRESSION_TARGET_NOT_MET", "compression", "info").to_dict())
        return out

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.verdict,
            "action": self.verdict.action,
            "scores": self.verdict.scores,
            "reasons": self.reasons(),
            "image": {
                "format": self.image.format,
                "width": self.image.width,
                "height": self.image.height,
                "bytes": self.image.size_bytes,
                "sha256": self.image.sha256,
                "phash": self.phash,
            },
            "metrics": {c.check: c.metrics for c in self.checks},
            "exif": self.exif,
            "compression": self.compression.summary() if self.compression else None,
            "timings_ms": self.timings_ms,
        }


def run_checks(img: LoadedImage, ctx: AnalysisContext) -> tuple[list[CheckResult], dict[str, int]]:
    results, timings = [], {}
    for name in ctx.profile.checks:
        t0 = time.perf_counter()
        r = REGISTRY[name](img, ctx)
        r.duration_ms = int((time.perf_counter() - t0) * 1000)
        timings[name] = r.duration_ms
        results.append(r)
    return results, timings


def analyze(
    data: bytes | LoadedImage,
    ctx: AnalysisContext,
    *,
    do_compress: bool = True,
    do_thumbnail: bool = True,
) -> PipelineResult:
    """Raise `UnreadableImage` jika file bukan gambar."""
    t0 = time.perf_counter()
    img = data if isinstance(data, LoadedImage) else load_image(data)
    timings = {"decode": int((time.perf_counter() - t0) * 1000)}
    checks, check_t = run_checks(img, ctx)
    timings.update(check_t)
    result = PipelineResult(image=img, verdict=decide(ctx.profile, checks), checks=checks, timings_ms=timings)
    if do_compress:
        t1 = time.perf_counter()
        result.compression = compress(img, ctx.profile.compression)
        timings["compress"] = int((time.perf_counter() - t1) * 1000)
    if do_thumbnail:
        t2 = time.perf_counter()
        result.thumbnail = thumbnail(img, ctx.profile.thumbnail)
        timings["thumbnail"] = int((time.perf_counter() - t2) * 1000)
    timings["total"] = int((time.perf_counter() - t0) * 1000)
    return result
