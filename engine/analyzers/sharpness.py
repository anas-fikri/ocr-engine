"""Ketajaman / blur: variance of Laplacian per tile pada resolusi yang dinormalisasi.

Dihitung per tile (4x4) lalu diambil rata-rata 25% tile paling tajam, sehingga foto yang
objeknya tajam tetapi latar belakangnya polos/bokeh tetap lolos.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from engine.analyzers.base import AnalysisContext, CheckResult, linear_score
from engine.imaging.loader import LoadedImage

GRID = 4


def laplacian_variance_tiles(gray: np.ndarray) -> np.ndarray:
    # blur ringan dulu supaya noise sensor (foto gelap) tidak terbaca sebagai "tajam"
    smooth = cv2.GaussianBlur(gray, (3, 3), 0)
    lap = cv2.Laplacian(smooth, cv2.CV_64F)
    h, w = lap.shape
    values = []
    for i in range(GRID):
        for j in range(GRID):
            tile = lap[i * h // GRID:(i + 1) * h // GRID, j * w // GRID:(j + 1) * w // GRID]
            if tile.size:
                values.append(float(tile.var()))
    return np.array(values)


def contrast_gain(gray: np.ndarray, target_std: float = 50.0, max_gain: float = 4.0) -> float:
    """Faktor penguat kontras (1-4x) agar foto gelap/pudar tidak ikut dianggap buram.

    Masalah cahaya sudah ditangani analyzer exposure; sharpness hanya menilai fokus.
    """
    std = float(gray.std())
    return min(max_gain, max(1.0, target_std / max(std, 1e-3)))


def sharpness_metric(gray: np.ndarray) -> tuple[float, float]:
    tiles = laplacian_variance_tiles(gray) * contrast_gain(gray) ** 2
    top = np.sort(tiles)[-max(1, len(tiles) // 4):]
    return float(top.mean()), float(np.median(tiles))


def score_from_metric(metric: float, low: float, high: float) -> float:
    return linear_score(math.log10(metric + 1), math.log10(low + 1), math.log10(high + 1))


class SharpnessAnalyzer:
    name = "sharpness"

    def __call__(self, img: LoadedImage, ctx: AnalysisContext) -> CheckResult:
        p = ctx.profile
        long_side = int(p.param("sharpness", "analysis_long_side_px", 1024))
        low = float(p.param("sharpness", "low", 4))
        high = float(p.param("sharpness", "high", 120))
        top, median = sharpness_metric(img.gray_scaled(long_side))
        return CheckResult(
            check=self.name,
            score=score_from_metric(top, low, high),
            issue_code="BLURRY",
            metrics={"laplacian_var_top": round(top, 2), "laplacian_var_median": round(median, 2)},
        )
