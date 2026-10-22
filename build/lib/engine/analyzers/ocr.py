"""Keterbacaan teks (OCR) untuk foto resi/dokumen. Opsional: butuh Tesseract.

Jika Tesseract tidak terpasang, check ini dilewati (info OCR_UNAVAILABLE) dan tidak memengaruhi verdict.
Docker image engine sudah menyertakan Tesseract (bahasa ind + eng).
"""

from __future__ import annotations

from functools import lru_cache

from engine.analyzers.base import AnalysisContext, CheckResult, Finding
from engine.imaging.loader import LoadedImage


@lru_cache
def _tesseract() -> tuple[object | None, str]:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        langs = set(pytesseract.get_languages(config=""))
        lang = "+".join(x for x in ("ind", "eng") if x in langs) or "eng"
        return pytesseract, lang
    except Exception:
        return None, ""


def ocr_available() -> bool:
    return _tesseract()[0] is not None


def ocr_confidence(img: LoadedImage) -> tuple[float, int]:
    """Rata-rata confidence kata (berbobot panjang kata) dan jumlah kata terbaca."""
    tess, lang = _tesseract()
    gray = img.gray_scaled(min(2000, max(img.width, img.height)))
    data = tess.image_to_data(gray, lang=lang, output_type=tess.Output.DICT)  # type: ignore[attr-defined]
    total = weight = 0.0
    words = 0
    for text, conf in zip(data["text"], data["conf"], strict=False):
        text = (text or "").strip()
        conf = float(conf)
        if conf < 0 or sum(c.isalnum() for c in text) < 2:
            continue
        words += 1
        total += conf * len(text)
        weight += len(text)
    return (total / weight if weight else 0.0), words


class OcrAnalyzer:
    name = "ocr"

    def __call__(self, img: LoadedImage, ctx: AnalysisContext) -> CheckResult:
        if not ocr_available():
            return CheckResult(check=self.name, findings=[Finding("OCR_UNAVAILABLE", "info")])
        conf, words = ocr_confidence(img)
        if words < 3:
            return CheckResult(check=self.name, score=0.0, issue_code="NO_TEXT_DETECTED",
                               metrics={"confidence": round(conf, 1), "words": words})
        return CheckResult(check=self.name, score=round(conf, 1), issue_code="TEXT_UNREADABLE",
                           metrics={"confidence": round(conf, 1), "words": words})
