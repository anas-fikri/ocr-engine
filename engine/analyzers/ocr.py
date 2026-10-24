"""Keterbacaan teks (OCR). Target engine: teks di foto (resi, label, dokumen) harus bisa dibaca.

Cara kerja (tanpa AI generatif; Tesseract + OpenCV):
1. Cari area yang tampak seperti teks (gradien + morfologi), ambil beberapa area terbesar.
2. Baca setiap area dengan Tesseract pada resolusi asli (diperbesar bila teks kecil; area tegak dicoba diputar).
3. Jika hasilnya belum meyakinkan, baca seluruh foto sebagai pembanding; ambil yang terbaik.
4. Jika aplikasi mengirim nomor resi (`awb`) dan nomor itu terbaca di foto, teks dianggap terbaca.

Skor = rata-rata confidence kata (berbobot panjang kata). Area teks yang ditemukan disimpan di metrik supaya
foto hasil kompresi bisa diperiksa di area yang sama (lihat pipeline: penjaga keterbacaan teks).

Jika Tesseract tidak terpasang, check ini dilewati (info OCR_UNAVAILABLE) dan tidak memengaruhi verdict.
Docker image engine sudah menyertakan Tesseract (bahasa ind + eng).
"""

from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Any

import cv2
import numpy as np

from engine.analyzers.base import AnalysisContext, CheckResult, Finding
from engine.imaging.loader import LoadedImage

DETECT_SIDE = 1200  # skala untuk mencari area teks
READ_SIDE = 3000  # resolusi maksimum yang dibaca Tesseract
MAX_REGIONS = 4
MIN_WORDS = 3
GOOD_ENOUGH = 70  # hasil area sudah meyakinkan: tidak perlu membaca seluruh foto
BUDGET_SEC = 1.2  # batas total waktu Tesseract per foto (kurir menunggu maks. 3 detik untuk semua pemeriksaan)


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


def _read(gray: np.ndarray, psm: int, timeout: float = 0.8) -> tuple[float, float, int, str]:
    """Return (jumlah conf*panjang, jumlah panjang, jumlah kata, teks).

    Tesseract bisa sangat lama pada foto buram tanpa hasil apa pun; lewat `timeout` dianggap tidak terbaca
    supaya kurir tidak menunggu (foto buram sudah ditolak oleh check ketajaman)."""
    tess, lang = _tesseract()
    try:
        data = tess.image_to_data(gray, lang=lang, config=f"--psm {psm}", timeout=timeout,  # type: ignore[attr-defined]
                                  output_type=tess.Output.DICT)  # type: ignore[attr-defined]
    except RuntimeError:  # timeout
        return 0.0, 0.0, 0, ""
    total = weight = 0.0
    words, texts = 0, []
    for text, conf in zip(data["text"], data["conf"], strict=False):
        text = (text or "").strip()
        conf = float(conf)
        if conf < 0 or sum(c.isalnum() for c in text) < 2:
            continue
        words += 1
        total += conf * len(text)
        weight += len(text)
        texts.append(text)
    return total, weight, words, " ".join(texts)


def find_text_regions(img: LoadedImage) -> list[list[float]]:
    """Area yang tampak seperti teks, dalam koordinat relatif [x0, y0, x1, y1] (0-1), terbesar dulu."""
    g = img.gray_scaled(min(DETECT_SIDE, max(img.width, img.height)))
    h, w = g.shape
    grad = cv2.morphologyEx(g, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    boxes = []
    for kernel in ((17, 3), (3, 17)):  # baris teks mendatar dan tegak
        closed = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, kernel))
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            x, y, bw_, bh = cv2.boundingRect(c)
            area = bw_ * bh
            if bw_ < 20 or bh < 8 or area < 400 or area > 0.6 * w * h:
                continue
            fill = cv2.countNonZero(bw[y:y + bh, x:x + bw_]) / area
            if 0.12 < fill < 0.85:
                boxes.append((x, y, x + bw_, y + bh))
    merged = _merge(boxes, pad=max(4, int(0.01 * max(w, h))))
    merged.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    return [[round(b[0] / w, 4), round(b[1] / h, 4), round(b[2] / w, 4), round(b[3] / h, 4)]
            for b in merged[:MAX_REGIONS]]


def _merge(boxes: list[tuple[int, int, int, int]], pad: int) -> list[tuple[int, int, int, int]]:
    """Gabungkan kotak yang berdekatan/bertumpuk (baris-baris satu label menjadi satu blok)."""
    out: list[list[int]] = []
    for b in sorted(boxes):
        for o in out:
            if b[0] <= o[2] + pad and b[2] >= o[0] - pad and b[1] <= o[3] + pad and b[3] >= o[1] - pad:
                o[0], o[1], o[2], o[3] = min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])
                break
        else:
            out.append(list(b))
    return [tuple(o) for o in out]  # type: ignore[misc]


def _crop(img: LoadedImage, region: list[float]) -> np.ndarray:
    full = img.gray_scaled(min(READ_SIDE, max(img.width, img.height)))
    h, w = full.shape
    x0, y0, x1, y1 = region
    px, py = int(0.02 * w), int(0.02 * h)
    c = full[max(0, int(y0 * h) - py):min(h, int(y1 * h) + py), max(0, int(x0 * w) - px):min(w, int(x1 * w) + px)]
    short, long_ = (min(c.shape), max(c.shape)) if c.size else (0, 0)
    if short and short < 64:  # teks kecil: perbesar supaya Tesseract bisa membaca
        s = min(4.0, 64 / short)
        c = cv2.resize(c, (int(c.shape[1] * s), int(c.shape[0] * s)), interpolation=cv2.INTER_CUBIC)
    elif long_ > 1600:  # area besar: cukup 1600 px (teks tetap terbaca, jauh lebih cepat)
        s = 1600 / long_
        c = cv2.resize(c, (int(c.shape[1] * s), int(c.shape[0] * s)), interpolation=cv2.INTER_AREA)
    return c


def _norm(text: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", text.upper())


def readability(img: LoadedImage, awb: str | None = None, regions: list[list[float]] | None = None,
                full_image: bool | None = None) -> dict[str, Any]:
    """Nilai keterbacaan teks. `regions`/`full_image` diisi untuk memeriksa ulang foto lain (hasil kompresi)
    dengan cara yang sama persis seperti foto aslinya."""
    deadline = time.monotonic() + BUDGET_SEC
    left = lambda: deadline - time.monotonic()  # noqa: E731
    if regions is None:
        regions = find_text_regions(img)
    total = weight = 0.0
    words, texts = 0, []
    for r in regions:
        if left() < 0.15:
            break
        crop = _crop(img, r)
        if crop.size == 0:
            continue
        tries = [crop]
        if crop.shape[0] > 1.5 * crop.shape[1]:  # area tegak: kemungkinan teks diputar
            tries += [cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE), cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)]
        best = max((_read(t, 6, max(0.1, min(0.6, left()))) for t in tries),
                   key=lambda x: (x[1] and x[0] / x[1]) * min(x[2], 10))
        total, weight, words = total + best[0], weight + best[1], words + best[2]
        texts.append(best[3])
    region_conf = total / weight if weight else 0.0
    method = "regions"
    if (full_image or (full_image is None and (words < MIN_WORDS or region_conf < GOOD_ENOUGH))) and left() > 0.15:
        f_total, f_weight, f_words, f_text = _read(img.gray_scaled(min(2000, max(img.width, img.height))), 3, left())
        f_conf = f_total / f_weight if f_weight else 0.0
        if (f_words >= MIN_WORDS and f_conf > region_conf) or words < MIN_WORDS:
            total, weight, words, texts, method = f_total, f_weight, f_words, [f_text], "full"
    conf = total / weight if weight else 0.0
    awb_found = bool(awb and len(_norm(awb)) >= 6 and _norm(awb) in _norm(" ".join(texts)))
    score = conf if words >= MIN_WORDS else 0.0
    if awb_found:
        score = max(score, 90.0)
    return {"score": round(score, 1), "confidence": round(conf, 1), "words": words, "awb_found": awb_found,
            "method": method, "regions": regions}


def ocr_confidence(img: LoadedImage) -> tuple[float, int]:
    """Kompatibilitas: rata-rata confidence kata dan jumlah kata terbaca."""
    r = readability(img)
    return r["confidence"], r["words"]


class OcrAnalyzer:
    name = "ocr"

    def __call__(self, img: LoadedImage, ctx: AnalysisContext) -> CheckResult:
        if not ocr_available():
            return CheckResult(check=self.name, findings=[Finding("OCR_UNAVAILABLE", "info")])
        r = readability(img, (ctx.meta or {}).get("awb"))
        code = "NO_TEXT_DETECTED" if r["words"] < MIN_WORDS and not r["awb_found"] else "TEXT_UNREADABLE"
        return CheckResult(check=self.name, score=r["score"], issue_code=code, metrics=r)
