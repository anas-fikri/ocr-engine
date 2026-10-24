"""Pipeline murni (tanpa DB/S3): bytes gambar + profile -> hasil analisis, kompresi, thumbnail.

Dipakai oleh worker, endpoint API sinkron/preview, dan CLI `pqe analyze`.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
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
    # kompres & thumbnail foto asli dikerjakan bersamaan dengan pemeriksaan (encode JPEG & Tesseract melepas GIL);
    # hasilnya dipakai jika foto asli memang yang disimpan (tanpa perbaikan otomatis), selain itu dibuat ulang
    spec_c = _SPEC_POOL.submit(_timed, compress, img, ctx.profile.compression) if do_compress else None
    spec_t = _SPEC_POOL.submit(_timed, thumbnail, img, ctx.profile.thumbnail) if do_thumbnail else None
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
    if ctx.profile.enhance.sharpen_text and "ocr" in ctx.profile.checks:
        t_s = time.perf_counter()
        if time.perf_counter() - t0 < SHARPEN_MAX_ELAPSED and _try_sharpen_text(result, ctx):
            timings["sharpen_text"] = int((time.perf_counter() - t_s) * 1000)
    out = result.output_image
    if do_compress:
        if out is img:
            result.compression, timings["compress"] = spec_c.result()
        else:
            result.compression, timings["compress"] = _timed(compress, out, ctx.profile.compression)
            if result.compression.passthrough:  # hasil perbaikan wajib disimpan sebagai file baru
                c = result.compression
                c.data, c.passthrough, c.content_type = out.data, False, "image/jpeg"
                c.width, c.height = out.width, out.height
        t3 = time.perf_counter()
        if _guard_text(result, out, ctx):
            timings["text_guard"] = int((time.perf_counter() - t3) * 1000)
    if do_thumbnail:
        result.thumbnail, timings["thumbnail"] = spec_t.result() if out is img else _timed(
            thumbnail, out, ctx.profile.thumbnail)
    timings["total"] = int((time.perf_counter() - t0) * 1000)
    return result


_SPEC_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pipeline")


def _timed(fn, *args):
    t = time.perf_counter()
    return fn(*args), int((time.perf_counter() - t) * 1000)


SHARPEN_MIN_GAIN = 5  # penajam teks dipakai hanya jika skor keterbacaan naik minimal sekian poin
SHARPEN_MAX_ELAPSED = 1.6  # detik; lewat ini penajam dilewati supaya kurir tetap dijawab < 3 detik


def _try_sharpen_text(result: PipelineResult, ctx: AnalysisContext) -> bool:
    """Teks sulit dibaca -> coba penajam teks (unsharp mask, tanpa AI). Dipakai hanya jika keterbacaan teks naik
    dan verdict tidak memburuk. Skor ketajaman TIDAK dinilai ulang (penajaman tidak boleh membuat skor itu naik palsu).
    Return True jika dicoba."""
    from engine.analyzers.ocr import readability
    from engine.imaging.enhance import SHARPEN_AMOUNT, SHARPEN_SIGMA, sharpen_text

    ocr = next((c for c in result.checks if c.check == "ocr" and c.score is not None), None)
    th = ctx.profile.thresholds.get("ocr")
    if ocr is None or th is None or th.warn is None or ocr.score >= th.warn:
        return False
    if ocr.score == 0 and not ocr.metrics.get("words"):
        return False  # tidak ada teks sama sekali: penajam tidak akan membantu
    src = result.output_image or result.image
    sharp = sharpen_text(src)
    r = readability(sharp, (ctx.meta or {}).get("awb"), skew=ocr.metrics.get("skew"), budget=0.7,
                    scan_codes=False)
    info = {"attempted": True, "before": ocr.score, "after": r["score"],
            "operation": f"unsharp_mask:sigma={SHARPEN_SIGMA},amount={SHARPEN_AMOUNT}"}
    if r["score"] >= ocr.score + SHARPEN_MIN_GAIN and not r["timed_out"]:
        code = "NO_TEXT_DETECTED" if r["words"] < 3 and not r["awb_found"] else "TEXT_UNREADABLE"
        new = CheckResult(check="ocr", score=r["score"], issue_code=code, metrics=r)
        merged = [new if c.check == "ocr" else c for c in result.checks]
        after = decide(ctx.profile, merged)
        if _RANK[after.verdict] <= _RANK[result.verdict.verdict]:
            before = {"verdict": result.verdict.verdict, "scores": result.verdict.scores,
                      "codes": [x.code for x in result.verdict.reasons if x.severity != "info"]}
            result.verdict, result.checks, result.output_image = after, merged, sharp
            info["applied"] = True
            e = result.enhancement
            e["applied"] = True
            e["operations"] = [*e.get("operations", []), info["operation"]]
            e.setdefault("before", before)
    info.setdefault("applied", False)
    result.enhancement["sharpen_text"] = info
    return True


TEXT_DROP_TOLERANCE = 8  # skor keterbacaan teks hasil kompres boleh turun maksimal sekian poin


def _guard_text(result: PipelineResult, src: LoadedImage, ctx: AnalysisContext) -> bool:
    """Foto hasil kompres wajib tetap terbaca teksnya. Jika keterbacaan turun, naikkan kualitas lalu resolusi,
    tetapi tidak pernah melewati `compression.text_guard_max_bytes` (bawaan 1,5 MB) supaya storage terkendali.

    Hanya untuk foto yang teksnya terbaca di foto asli (skor OCR >= warn). Return True jika penjaga dijalankan.
    """
    from engine.analyzers.ocr import readability
    from engine.imaging.compressor import encode_fit, passthrough_of

    comp = result.compression
    ocr = next((c for c in result.checks if c.check == "ocr" and c.score is not None), None)
    th = ctx.profile.thresholds.get("ocr")
    if comp is None or comp.passthrough or comp.data is None or ocr is None or th is None:
        return False
    before = ocr.score
    if th.warn is not None and before < th.warn:
        return False  # teks memang belum terbaca di foto asli: sudah dinilai oleh check ocr
    m = ocr.metrics
    floor = max(th.warn or 0, before - TEXT_DROP_TOLERANCE)

    def measure(c) -> dict:
        if c.passthrough:
            return {"score": before, "awb_found": m.get("awb_found", False)}
        if m.get("method") == "barcode":  # cukup barcode yang sama masih terbaca; jika tidak, Tesseract otomatis
            return readability(load_image(c.data), (ctx.meta or {}).get("awb"), budget=0.6)
        return readability(load_image(c.data), (ctx.meta or {}).get("awb"), m.get("regions") or [],
                           full_image=m.get("method") == "full", skew=m.get("skew", 0.0), budget=0.6,
                           scan_codes=False)

    def ok(r: dict) -> bool:
        return r["score"] >= floor and (r["awb_found"] or not m.get("awb_found"))

    cfg, long_side = ctx.profile.compression, max(src.width, src.height)
    cap = max(cfg.text_guard_max_bytes, cfg.target_max_bytes)
    q0, side0 = comp.quality or 80, max(comp.width, comp.height)
    ladder = [  # makin besar; semuanya <= cap
        lambda: encode_fit(src, cfg, side0, cap, q0 + 1, 95),
        lambda: encode_fit(src, cfg, min(long_side, int(side0 * 1.4)), cap, cfg.min_quality, 92),
        lambda: encode_fit(src, cfg, long_side, cap, cfg.min_quality, 92),
        lambda: passthrough_of(src, cfg) if src.size_bytes <= cap and src.format in ("JPEG", "PNG", "WEBP") else None,
    ]
    best, best_r = comp, measure(comp)
    current, r, steps = best, best_r, []
    while not ok(r) and ladder:
        steps.append({"quality": current.quality, "long_side": max(current.width, current.height),
                      "bytes": current.size_bytes, "score": r["score"]})
        cand = ladder.pop(0)()
        if cand is None:
            continue
        current, r = cand, measure(cand)
        if r["score"] > best_r["score"]:
            best, best_r = current, r
    chosen, cr = (current, r) if ok(r) else (best, best_r)  # tidak lolos: pakai yang paling terbaca dalam batas
    chosen.text_guard = {"before": before, "after": cr["score"], "awb_found": cr.get("awb_found", False),
                         "raised": chosen is not comp, "ok": ok(cr), "max_bytes": cap, "steps": steps}
    result.compression = chosen
    return True


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
