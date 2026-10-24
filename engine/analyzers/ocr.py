"""Keterbacaan teks (OCR). Target engine: teks di foto (resi, label, dokumen) harus bisa dibaca.

Cara kerja (tanpa AI generatif; zxing-cpp + Tesseract + OpenCV):
0. Baca barcode/QR (analyzers/barcode.py). Barcode berisi nomor resi terbaca = label tajam dan terbaca: skor
   BARCODE_SCORE, Tesseract dilewati (cepat dan pasti). Jika tidak ada, lanjut ke langkah 1.
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

from engine.analyzers import barcode
from engine.analyzers.base import AnalysisContext, CheckResult, Finding
from engine.imaging.loader import LoadedImage

DETECT_SIDE = 1200  # skala untuk mencari area teks
READ_SIDE = 3000  # resolusi maksimum yang dibaca Tesseract
MAX_REGIONS = 4
MIN_WORDS = 3
GOOD_ENOUGH = 70  # hasil area sudah meyakinkan: tidak perlu membaca seluruh foto
BARCODE_SCORE = 95.0  # skor keterbacaan bila nomor resi terbaca dari barcode/QR
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
        return 0.0, 0.0, -1, ""  # -1 = kehabisan waktu (bukan "tidak ada teks")
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


class _View:
    """Grayscale foto (sudah diluruskan bila miring) pada resolusi baca, plus versi lebih kecil sesuai kebutuhan."""

    def __init__(self, img: LoadedImage, angle: float = 0.0) -> None:
        g = img.gray_scaled(min(READ_SIDE, max(img.width, img.height)))
        if abs(angle) >= 1.5:
            h, w = g.shape
            m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            cos, sin = abs(m[0, 0]), abs(m[0, 1])
            nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
            m[0, 2] += nw / 2 - w / 2
            m[1, 2] += nh / 2 - h / 2
            g = cv2.warpAffine(g, m, (nw, nh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        self.base = g
        self._cache: dict[int, np.ndarray] = {}

    def scaled(self, long_side: int) -> np.ndarray:
        h, w = self.base.shape
        if max(h, w) <= long_side:
            return self.base
        if long_side not in self._cache:
            s = long_side / max(h, w)
            self._cache[long_side] = cv2.resize(self.base, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        return self._cache[long_side]


def estimate_skew(img: LoadedImage) -> float:
    """Kemiringan dominan (derajat) dari garis lurus di foto: baris teks dan tepi kartu/label/resi.

    Tesseract hanya andal sampai kemiringan beberapa derajat; foto resi/kartu yang dipegang miring perlu
    diluruskan dulu. Hanya rotasi (tanpa mengubah isi), dan hanya untuk dibaca, bukan untuk disimpan.
    """
    g = img.gray_scaled(min(DETECT_SIDE, max(img.width, img.height)))
    edges = cv2.Canny(g, 60, 160)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=60, minLineLength=max(40, g.shape[1] // 12),
                            maxLineGap=8)
    if lines is None:
        return 0.0
    angles, weights = [], []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):  # OpenCV 4: (N,1,4), OpenCV 5: (N,4)
        a = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        a = (a + 90) % 180 - 90  # -90..90
        if abs(a) > 45:  # garis tegak: pakai sebagai kemiringan sumbu lain
            a = a - 90 if a > 0 else a + 90
        angles.append(a)
        weights.append(np.hypot(x2 - x1, y2 - y1))
    hist, bins = np.histogram(angles, bins=90, range=(-45, 45), weights=weights)
    i = int(np.argmax(hist))
    sel = [(a, w) for a, w in zip(angles, weights, strict=False) if bins[i] - 1 <= a <= bins[i + 1] + 1]
    angle = sum(a * w for a, w in sel) / max(1e-6, sum(w for _, w in sel))
    return round(angle, 1) if abs(angle) >= 1.5 else 0.0


def find_text_regions(view: _View) -> list[list[float]]:
    """Area yang tampak seperti teks, dalam koordinat relatif [x0, y0, x1, y1] (0-1), terbesar dulu."""
    g = view.scaled(DETECT_SIDE)
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


def _crop(view: _View, region: list[float]) -> np.ndarray:
    full = view.base
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


LINES_PER_STRIP = 4  # area teks padat (struk panjang, halaman penuh) dibaca per kelompok baris
MAX_STRIPS = 3
DENSE_LINES = 8


def _text_lines(crop: np.ndarray) -> list[tuple[int, int]]:
    """Baris teks dari profil tinta per baris piksel (teks gelap di terang maupun sebaliknya)."""
    _, bw = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    if bw.mean() > 127:  # mayoritas "tinta" = latar gelap: balik
        bw = 255 - bw
    ink = (bw > 0).mean(axis=1) > 0.02
    lines, start = [], None
    for y, v in enumerate(ink):
        if v and start is None:
            start = y
        elif not v and start is not None:
            if y - start >= 4:
                lines.append((start, y))
            start = None
    if start is not None and len(ink) - start >= 4:
        lines.append((start, len(ink)))
    return lines


def _strips(crop: np.ndarray) -> list[np.ndarray]:
    """Area teks dengan banyak baris cukup dicontoh: kelompok baris awal, tengah, akhir (dipotong di sela baris,
    tidak pernah membelah huruf). Keterbacaan diukur dari sampel; jauh lebih cepat, tidak kehabisan waktu."""
    lines = _text_lines(crop)
    if len(lines) <= DENSE_LINES:
        return [crop]
    groups = [lines[i:i + LINES_PER_STRIP] for i in range(0, len(lines), LINES_PER_STRIP)]
    pick = sorted({0, len(groups) // 2, len(groups) - 1})[:MAX_STRIPS]
    h = crop.shape[0]
    return [crop[max(0, groups[k][0][0] - 6):min(h, groups[k][-1][1] + 6)] for k in pick]


def _norm(text: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", text.upper())


def readability(img: LoadedImage, awb: str | None = None, regions: list[list[float]] | None = None,
                full_image: bool | None = None, skew: float | None = None,
                budget: float = BUDGET_SEC, scan_codes: bool = True) -> dict[str, Any]:
    """Nilai keterbacaan teks. `regions`/`full_image` diisi untuk memeriksa ulang foto lain (hasil kompresi)
    dengan cara yang sama persis seperti foto aslinya. `budget` hanya untuk Tesseract (barcode jauh lebih cepat)."""
    codes = barcode.read_codes(img) if scan_codes else []
    code = barcode.find_awb(codes, awb) or barcode.find_awb(codes, None)
    if code is not None:
        return {"score": BARCODE_SCORE, "confidence": BARCODE_SCORE, "words": 0,
                "awb_found": bool(awb) and barcode.find_awb([code], awb) is not None, "method": "barcode",
                "code": code, "codes": codes, "regions": regions or [], "skew": skew or 0.0, "timed_out": False}
    if not ocr_available():
        return {"score": 0.0, "confidence": 0.0, "words": 0, "awb_found": False, "method": "unavailable",
                "codes": codes, "regions": [], "skew": 0.0, "timed_out": True}
    deadline = time.monotonic() + budget
    left = lambda: deadline - time.monotonic()  # noqa: E731
    if skew is None:
        skew = estimate_skew(img)
    view = _View(img, skew)
    if regions is None:
        regions = find_text_regions(view)
    total = weight = 0.0
    words, texts, timed_out = 0, [], False
    for r in regions:
        if left() < 0.15:
            break
        crop = _crop(view, r)
        if crop.size == 0:
            continue
        reads = [_read(s, 6, max(0.1, min(0.6, left()))) for s in _strips(crop)]
        first = (sum(x[0] for x in reads), sum(x[1] for x in reads), sum(max(x[2], 0) for x in reads),
                 " ".join(x[3] for x in reads))
        timed_out |= any(x[2] < 0 for x in reads)
        best = first
        if first[2] < MIN_WORDS and crop.shape[0] > 1.5 * crop.shape[1] and left() > 0.3:
            # hampir tidak terbaca dan areanya tegak: mungkin teks diputar 90 derajat
            for rot in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
                x = _read(cv2.rotate(crop, rot), 6, max(0.1, min(0.5, left())))
                timed_out |= x[2] < 0
                if (x[1] and x[0] / x[1]) * min(max(x[2], 0), 10) > (best[1] and best[0] / best[1]) * min(best[2], 10):
                    best = (x[0], x[1], max(x[2], 0), x[3])
        total, weight, words = total + best[0], weight + best[1], words + max(best[2], 0)
        texts.append(best[3])
    region_conf = total / weight if weight else 0.0
    method = "regions"
    want_full = full_image or (full_image is None and (words < 2 * MIN_WORDS or region_conf < GOOD_ENOUGH))
    if want_full and left() <= 0.15:
        timed_out = True
    elif want_full:
        f_total, f_weight, f_words, f_text = _read(view.scaled(2000), 3, left())
        timed_out |= f_words < 0
        f_words = max(f_words, 0)
        f_conf = f_total / f_weight if f_weight else 0.0
        if f_conf * min(f_words, 10) > region_conf * min(words, 10):  # lebih banyak kata yakin = lebih terbaca
            total, weight, words, texts, method = f_total, f_weight, f_words, [f_text], "full"
    conf = total / weight if weight else 0.0
    awb_found = bool(awb and len(_norm(awb)) >= 6 and _norm(awb) in _norm(" ".join(texts)))
    score = conf if words >= MIN_WORDS else 0.0
    if awb_found:
        score = max(score, 90.0)
    return {"score": round(score, 1), "confidence": round(conf, 1), "words": words, "awb_found": awb_found,
            "method": method, "codes": codes, "regions": regions, "skew": skew,
            "timed_out": timed_out and words < MIN_WORDS and not awb_found}


def ocr_confidence(img: LoadedImage) -> tuple[float, int]:
    """Kompatibilitas: rata-rata confidence kata dan jumlah kata terbaca."""
    r = readability(img)
    return r["confidence"], r["words"]


class OcrAnalyzer:
    name = "ocr"

    def __call__(self, img: LoadedImage, ctx: AnalysisContext) -> CheckResult:
        if not ocr_available() and not barcode.barcode_available():
            return CheckResult(check=self.name, findings=[Finding("OCR_UNAVAILABLE", "info")])
        r = readability(img, (ctx.meta or {}).get("awb"))
        if r["method"] == "unavailable":  # tanpa Tesseract dan barcode tidak terbaca
            return CheckResult(check=self.name, metrics=r, findings=[Finding("OCR_UNAVAILABLE", "info")])
        if r["timed_out"]:  # server sibuk / foto sangat buram: jangan menolak hanya karena waktu habis
            return CheckResult(check=self.name, metrics=r, findings=[Finding("OCR_TIMEOUT", "info")])
        code = "NO_TEXT_DETECTED" if r["words"] < MIN_WORDS and not r["awb_found"] and r["method"] != "barcode" \
            else "TEXT_UNREADABLE"
        return CheckResult(check=self.name, score=r["score"], issue_code=code, metrics=r)
