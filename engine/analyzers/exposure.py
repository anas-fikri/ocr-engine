"""Exposure: terlalu gelap, terlalu terang, glare, kontras rendah (dari histogram)."""

from __future__ import annotations

import numpy as np

from engine.analyzers.base import AnalysisContext, CheckResult, linear_score
from engine.imaging.loader import LoadedImage


class ExposureAnalyzer:
    name = "exposure"

    def __call__(self, img: LoadedImage, ctx: AnalysisContext) -> CheckResult:
        g = img.gray_scaled(512).astype(np.float32)
        mean = float(g.mean())
        std = float(g.std())
        bright_clip = float((g >= 250).mean())
        dark_clip = float((g <= 5).mean())

        subs = {
            "TOO_DARK": linear_score(mean, 20, 75),
            "TOO_BRIGHT": linear_score(mean, 245, 205),
            "GLARE": linear_score(bright_clip, 0.45, 0.12),
            "LOW_CONTRAST": linear_score(std, 6, 30),
        }
        issue = min(subs, key=subs.get)
        return CheckResult(
            check=self.name,
            score=subs[issue],
            issue_code=issue,
            metrics={
                "mean": round(mean, 1),
                "std": round(std, 1),
                "bright_clip": round(bright_clip, 4),
                "dark_clip": round(dark_clip, 4),
                "sub_scores": subs,
            },
        )
