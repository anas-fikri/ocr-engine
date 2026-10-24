"""Baca barcode/QR resi di foto (zxing-cpp, decoder biasa, bukan AI).

Label resi PCP Express memuat nomor resi sebagai barcode Code 128 dan QR. Barcode yang berhasil dibaca adalah bukti
kuat bahwa label di foto tajam dan terbaca, lebih cepat (puluhan ms) dan lebih pasti daripada Tesseract. Dipakai oleh
check `ocr` (lihat analyzers/ocr.py) sebelum Tesseract.

Jika zxing-cpp tidak terpasang, fungsi di sini mengembalikan daftar kosong dan OCR berjalan seperti biasa.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import numpy as np

from engine.imaging.loader import LoadedImage

SCAN_SIDE = 2000  # dibaca dulu pada skala ini; resolusi asli hanya jika tidak ada yang terbaca
AWB_LIKE = re.compile(r"^[0-9A-Z]{8,40}$")


@lru_cache
def _zxing():
    try:
        import zxingcpp

        return zxingcpp
    except Exception:
        return None


@lru_cache
def _formats():
    """Hanya jenis yang dipakai di label logistik: jauh lebih cepat daripada mencoba semua jenis (EAN, PDF417, ...)."""
    f = _zxing().BarcodeFormat
    return tuple(getattr(f, n) for n in ("Code128", "Code39", "QRCode", "DataMatrix") if hasattr(f, n))


def barcode_available() -> bool:
    return _zxing() is not None


def norm(text: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", text.upper())


def looks_like_awb(text: str) -> bool:
    """Nomor resi: 8-40 huruf/angka, minimal 4 angka, bukan URL."""
    t = text.strip()
    return "://" not in t and bool(AWB_LIKE.match(norm(t))) and sum(c.isdigit() for c in t) >= 4


def _scan(gray: np.ndarray) -> list[dict[str, Any]]:
    z = _zxing()
    out = []
    for r in z.read_barcodes(gray, formats=_formats(), try_invert=False):
        if r.valid and r.text:
            out.append({"format": str(r.format).split(".")[-1], "text": r.text})
    return out


def read_codes(img: LoadedImage) -> list[dict[str, Any]]:
    """Semua barcode/QR yang terbaca, unik per teks."""
    if _zxing() is None:
        return []
    try:
        long_side = max(img.width, img.height)
        codes = _scan(img.gray_scaled(SCAN_SIDE) if long_side > SCAN_SIDE else img.gray)
        if not codes and long_side > SCAN_SIDE:
            codes = _scan(img.gray)
    except Exception:
        return []
    seen, unique = set(), []
    for c in codes:
        if c["text"] not in seen:
            seen.add(c["text"])
            unique.append(c)
    return unique


def find_awb(codes: list[dict[str, Any]], awb: str | None) -> dict[str, Any] | None:
    """Kode yang memuat nomor resi aplikasi; tanpa `awb`, kode pertama yang tampak seperti nomor resi."""
    if awb and len(norm(awb)) >= 6:
        return next((c for c in codes if norm(awb) in norm(c["text"])), None)
    return next((c for c in codes if looks_like_awb(c["text"])), None)
