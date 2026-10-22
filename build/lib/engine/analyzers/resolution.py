"""Resolusi & "pecah": ukuran piksel, estimasi kualitas JPEG, dan blocking artifact 8x8."""

from __future__ import annotations

import numpy as np

from engine.analyzers.base import AnalysisContext, CheckResult, linear_score
from engine.imaging.loader import LoadedImage

# Tabel kuantisasi luminance standar JPEG (IJG). Dipakai sebagai acuan quality 50.
_STD_LUMA_SUM = sum([
    16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99,
])


def estimate_jpeg_quality(quantization: dict | None) -> int | None:
    """Estimasi quality JPEG (1-100) dari tabel kuantisasi luminance. None jika bukan JPEG."""
    if not quantization:
        return None
    table = quantization.get(0)
    if not table or len(table) != 64:
        return None
    scale = sum(table) * 100.0 / _STD_LUMA_SUM
    quality = (200 - scale) / 2 if scale <= 100 else 5000 / scale
    return int(max(1, min(100, round(quality))))


def _crop(gray: np.ndarray, size: int = 2048) -> np.ndarray:
    h, w = gray.shape
    cy, cx = max(0, (h - size) // 2), max(0, (w - size) // 2)
    return gray[cy:cy + size, cx:cx + size].astype(np.int16)


def _profiles(g: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rata-rata beda piksel bertetangga per kolom dan per baris."""
    return np.abs(np.diff(g, axis=1)).mean(axis=0), np.abs(np.diff(g, axis=0)).mean(axis=1)


def blockiness(gray: np.ndarray) -> float:
    """Blocking artifact JPEG: energi tepi di grid 8 piksel vs posisi lain.

    ~1.0-1.5 = normal, > 3 = kotak-kotak jelas. Tidak bergantung posisi awal grid.
    """
    ratios = []
    for diff in _profiles(_crop(gray)):
        if diff.size < 64:
            continue
        energy = np.array([diff[k::8].mean() for k in range(8)])
        base = float(np.median(energy))
        if base < 0.5:  # area polos, tidak informatif
            continue
        ratios.append(float(energy.max()) / base)
    return round(float(np.mean(ratios)), 3) if ratios else 1.0


def mosaic(gray: np.ndarray) -> float:
    """Indikasi foto kecil yang diperbesar (kotak-kotak besar, ukuran bebas).

    Tepi hanya muncul di batas kotak, sehingga profil beda-piksel sangat "berduri" di
    kedua sumbu. Foto normal ~1.5-8 (dokumen/garis lurus bisa ~12), foto kecil yang diperbesar parah > 22.
    """
    ratios = []
    for prof in _profiles(_crop(gray)):
        if prof.size < 64:
            continue
        top = np.sort(prof)[-max(1, prof.size // 32):].mean()
        ratios.append(float(top) / max(float(np.median(prof)), 0.3))
    return round(min(ratios), 3) if ratios else 1.0


class ResolutionAnalyzer:
    name = "resolution"

    def __call__(self, img: LoadedImage, ctx: AnalysisContext) -> CheckResult:
        ideal = float(ctx.profile.param("resolution", "ideal_short_side_px", 1080))
        short = min(img.width, img.height)
        quality = estimate_jpeg_quality(img.quantization)
        block = blockiness(img.gray)
        mos = mosaic(img.gray)

        subs = {
            "LOW_RESOLUTION": linear_score(short, 0, ideal),
            # mosaic hanya bisa menurunkan skor sampai 40 (WARN): foto dengan area polos besar yang
            # dikompres WebP/HEIC bisa terlihat mirip, jadi sinyal ini tidak boleh langsung REJECT
            "PIXELATED": min(linear_score(block, 3.5, 2.2), 40 + 0.6 * linear_score(mos, 22.0, 12.0)),
        }
        if quality is not None:
            subs["HEAVY_COMPRESSION"] = linear_score(quality, 10, 60)
        issue = min(subs, key=subs.get)
        return CheckResult(
            check=self.name,
            score=subs[issue],
            issue_code=issue,
            metrics={
                "width": img.width,
                "height": img.height,
                "short_side_px": short,
                "jpeg_quality_est": quality,
                "blockiness": block,
                "mosaic": mos,
                "sub_scores": subs,
            },
        )
