"""Pipeline murni (tanpa DB/S3): bytes gambar + profile -> hasil analisis, kompresi, thumbnail.

Dipakai oleh worker, endpoint API sinkron/preview, dan CLI `pqe analyze`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from engine.analyzers import REGISTRY, AnalysisContext, CheckResult
from engine.imaging.compressor import CompressionResult, compress, thumbnail
from engine.imaging.enhance import FIXABLE_CODES, enhance
from engine.imaging.loader import LoadedImage, load_image
from engine.verdict import _RANK, REJECT, Reason, Verdict, decide

# Setelah perbaikan pencahayaan, HANYA check ini yang dinilai ulang. Ketajaman & resolusi tetap
# memakai nilai foto asli: koreksi tonal tidak bisa memperbaiki fokus/detail, dan kontras yang
# dinaikkan tidak boleh membuat skor ketajaman "naik palsu". EXIF/duplikat membaca file asli.
RECHECK_AFTER_ENHANCE = {"exposure", "ocr"}


@dataclass
class PipelineResult:
    image: LoadedImage  # foto ORIGINAL (hash, EXIF, ukuran asli)
    verdict: Verdict
    checks: list[CheckResult]
    compression: CompressionResult | None = None
    thumbnail: bytes | None = None
    timings_ms: dict[str, int] = field(default_factory=dict)
    output_image: LoadedImage | None = None  # foto yang dipakai untuk versi kompres (original / hasil perbaikan)
    enhancement: dict = field(default_factory=lambda: {"attempted": False, "applied": False})

    @property
    def retake_required(self) -> bool:
        """Kualitas tidak bisa dimaksimalkan: kurir wajib mengambil foto ulang."""
        return self.verdict.verdict == REJECT

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
            "retake_required": self.retake_required,
            "enhancement": self.enhancement,
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
    result = PipelineResult(image=img, verdict=decide(ctx.profile, checks), checks=checks, timings_ms=timings,
                            output_image=img)
    if ctx.profile.enhance.enabled and any(
        r.code in FIXABLE_CODES and r.severity != "info" for r in result.verdict.reasons
    ):
        t_e = time.perf_counter()
        _try_enhance(result, ctx)
        timings["enhance"] = int((time.perf_counter() - t_e) * 1000)
    out = result.output_image
    if do_compress:
        t1 = time.perf_counter()
        result.compression = compress(out, ctx.profile.compression)
        if out is not img and result.compression.passthrough:  # hasil perbaikan wajib disimpan sebagai file baru
            c = result.compression
            c.data, c.passthrough, c.content_type = out.data, False, "image/jpeg"
            c.width, c.height = out.width, out.height
        timings["compress"] = int((time.perf_counter() - t1) * 1000)
    if do_thumbnail:
        t2 = time.perf_counter()
        result.thumbnail = thumbnail(out, ctx.profile.thumbnail)
        timings["thumbnail"] = int((time.perf_counter() - t2) * 1000)
    timings["total"] = int((time.perf_counter() - t0) * 1000)
    return result


def _try_enhance(result: PipelineResult, ctx: AnalysisContext) -> None:
    """Coba perbaiki pencahayaan. Dipakai hanya jika verdict membaik DAN struktur tidak berubah."""
    enhanced, info = enhance(result.image)
    result.enhancement = info
    if enhanced is None:
        return
    before = result.verdict
    recheck = [c for c in ctx.profile.checks if c in RECHECK_AFTER_ENHANCE]
    sub_ctx = AnalysisContext(profile=ctx.profile.model_copy(update={"checks": recheck}), meta=ctx.meta,
                              received_at=ctx.received_at, timezone=ctx.timezone)
    new_results, _ = run_checks(enhanced.image, sub_ctx)
    by_name = {c.check: c for c in new_results}
    merged = [by_name.get(c.check, c) for c in result.checks]
    after = decide(ctx.profile, merged)
    info["before"] = {"verdict": before.verdict, "scores": before.scores,
                      "codes": [r.code for r in before.reasons if r.severity != "info"]}
    if _RANK[after.verdict] < _RANK[before.verdict]:
        result.verdict, result.checks, result.output_image = after, merged, enhanced.image
    else:
        info.update(applied=False, reason="no_improvement")
