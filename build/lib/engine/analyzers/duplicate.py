"""Duplikat / pemakaian ulang foto: SHA-256 (file identik) + perceptual hash (visual mirip)."""

from __future__ import annotations

import cv2
import numpy as np

from engine.analyzers.base import AnalysisContext, CheckResult, Finding
from engine.imaging.loader import LoadedImage


def phash(gray: np.ndarray) -> int:
    """pHash 64-bit (DCT 32x32 -> 8x8 frekuensi rendah dibanding median)."""
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(small)[:8, :8].flatten()
    med = np.median(low[1:])
    bits = 0
    for b in low > med:
        bits = (bits << 1) | int(b)
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def phash_hex(value: int) -> str:
    return f"{value:016x}"


class DuplicateAnalyzer:
    name = "duplicate"

    def __call__(self, img: LoadedImage, ctx: AnalysisContext) -> CheckResult:
        h = phash(img.gray)
        result = CheckResult(check=self.name, metrics={"phash": phash_hex(h), "sha256": img.sha256})
        if ctx.find_duplicates is None:
            return result
        max_dist = int(ctx.profile.param("duplicate", "max_distance", 6))
        matches = ctx.find_duplicates(img.sha256, h, max_dist)
        own_awb = ctx.meta.get("awb")
        # foto lain untuk resi yang sama itu wajar (beberapa foto per pengiriman)
        foreign = [m for m in matches if not (own_awb and m.awb == own_awb)]
        result.metrics["matches"] = [
            {"photo_id": m.photo_id, "awb": m.awb, "distance": m.distance, "exact": m.exact}
            for m in sorted(foreign, key=lambda m: m.distance)[:5]
        ]
        exact = [m for m in foreign if m.exact]
        if exact:
            result.findings.append(Finding("DUPLICATE_EXACT", "risk", 70, {"photo_id": exact[0].photo_id}))
        elif foreign:
            best = min(foreign, key=lambda m: m.distance)
            result.findings.append(
                Finding("DUPLICATE_SIMILAR", "risk", 50, {"photo_id": best.photo_id, "distance": best.distance})
            )
        return result
