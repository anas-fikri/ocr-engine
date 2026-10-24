"""Exposure: terlalu gelap, terlalu terang, glare, kontras rendah (dari histogram)."""

from __future__ import annotations

import cv2
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
        # Dokumen/resi/layar: latar putih (kertas) itu wajar, bukan silau/terlalu terang. Ciri: banyak putih DAN ada
        # goresan gelap (teks). Silau dihitung hanya dari area putih yang jauh dari teks; "terlalu terang" dari
        # kepekatan teks tergelap (teks pudar = foto kelewat terang).
        dark = g < min(128.0, mean - 50)
        dark_frac = float(dark.mean())
        paper = dark_frac >= 0.005 and bright_clip >= 0.2
        glare_frac = bright_clip
        p5 = float(np.percentile(g, 5))
        if paper:
            near_text = cv2.dilate(dark.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41))) > 0
            glare_frac = float(((g >= 250) & ~near_text).mean())
            subs["GLARE"] = linear_score(glare_frac, 0.45, 0.12)
            subs["TOO_BRIGHT"] = linear_score(p5, 235, 160)
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
                "paper_background": paper,
                "glare_fraction": round(glare_frac, 4),
                "text_darkness_p5": round(p5, 1),
                "sub_scores": subs,
            },
        )
