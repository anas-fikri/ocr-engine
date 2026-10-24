"""Kompresi dinamis: turunkan ukuran file ke bawah target tanpa mengorbankan keterbacaan.

Aturan:
1. File sudah <= skip_if_below_bytes dan resolusi wajar -> tidak dikompres (passthrough).
2. Downscale ke max_long_side_px, lalu binary search quality tertinggi yang muat target.
3. Guard: SSIM terhadap original (pada skala analisis) harus >= min_ssim.
   Kalau target dan guard tidak bisa dipenuhi bersamaan, keterbacaan yang dimenangkan.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from engine.imaging.loader import LoadedImage
from engine.tenant_config import Compression, Thumbnail

SSIM_SIDE = 1024
SCALE_STEPS = (1.0, 0.85, 0.72, 0.6, 0.5)


@dataclass
class CompressionResult:
    data: bytes | None  # None = passthrough (pakai original)
    content_type: str
    width: int
    height: int
    quality: int | None
    ssim: float | None
    target_met: bool
    passthrough: bool
    original_bytes: int
    text_guard: dict | None = None  # penjaga keterbacaan teks (pipeline): skor sebelum/sesudah, langkah

    @property
    def size_bytes(self) -> int:
        return len(self.data) if self.data is not None else self.original_bytes

    def summary(self) -> dict:
        return {
            "passthrough": self.passthrough,
            "original_bytes": self.original_bytes,
            "output_bytes": self.size_bytes,
            "ratio": round(self.size_bytes / self.original_bytes, 3) if self.original_bytes else None,
            "width": self.width,
            "height": self.height,
            "quality": self.quality,
            "ssim": self.ssim,
            "target_met": self.target_met,
            "text_guard": self.text_guard,
        }


def _encode(img: Image.Image, fmt: str, quality: int, fast: bool = False) -> bytes:
    """fast=True untuk pencarian quality (tanpa optimasi Huffman/progressive: ±2x lebih cepat, file sedikit lebih
    besar, jadi aman: yang muat dalam mode cepat pasti muat setelah dioptimasi)."""
    buf = io.BytesIO()
    if fmt == "webp":
        img.save(buf, "WEBP", quality=quality, method=2 if fast else 4)
    elif fast:
        img.save(buf, "JPEG", quality=quality, subsampling="4:2:0")
    else:
        img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True, subsampling="4:2:0")
    return buf.getvalue()


def _gray_for_ssim(img: Image.Image) -> np.ndarray:
    g = np.asarray(img.convert("L"), dtype=np.float32)
    h, w = g.shape
    scale = SSIM_SIDE / max(h, w)
    if scale < 1:
        g = cv2.resize(g, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    return g


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a, mu_b = cv2.GaussianBlur(a, (11, 11), 1.5), cv2.GaussianBlur(b, (11, 11), 1.5)
    s_a = cv2.GaussianBlur(a * a, (11, 11), 1.5) - mu_a**2
    s_b = cv2.GaussianBlur(b * b, (11, 11), 1.5) - mu_b**2
    s_ab = cv2.GaussianBlur(a * b, (11, 11), 1.5) - mu_a * mu_b
    m = ((2 * mu_a * mu_b + c1) * (2 * s_ab + c2)) / ((mu_a**2 + mu_b**2 + c1) * (s_a + s_b + c2))
    return float(m.mean())


def _resize(img: Image.Image, long_side: int) -> Image.Image:
    w, h = img.size
    if max(w, h) <= long_side:
        return img
    s = long_side / max(w, h)
    return img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)


def compress(src: LoadedImage, cfg: Compression) -> CompressionResult:
    content_type = "image/webp" if cfg.format == "webp" else "image/jpeg"
    long_side = max(src.width, src.height)
    small_enough = src.size_bytes <= cfg.skip_if_below_bytes
    browser_ok = src.format in ("JPEG", "PNG", "WEBP")
    if not cfg.enabled or (small_enough and browser_ok and long_side <= cfg.max_long_side_px * 1.5):
        return CompressionResult(None, f"image/{src.format.lower()}", src.width, src.height, None, None,
                                 src.size_bytes <= cfg.target_max_bytes, True, src.size_bytes)

    ref = _gray_for_ssim(src.pil)
    base_long = min(long_side, cfg.max_long_side_px)
    best_guard_ok: tuple[bytes, Image.Image, int, float] | None = None  # lolos guard, tapi di atas target

    for step in SCALE_STEPS:
        target_long = max(int(base_long * step), min(cfg.min_long_side_px, long_side))
        img = _resize(src.pil, target_long)
        lo, hi, found = cfg.min_quality, cfg.max_quality, None
        while lo <= hi:  # quality tertinggi yang ukurannya muat target
            q = (lo + hi) // 2
            data = _encode(img, cfg.format, q, fast=True)
            if len(data) <= cfg.target_max_bytes:
                found, lo = (data, q), q + 1
            else:
                hi = q - 1
        if found is not None:
            found = (_encode(img, cfg.format, found[1]), found[1])  # versi final teroptimasi (lebih kecil)
        if found is None:
            data = _encode(img, cfg.format, cfg.min_quality)
            s = ssim(ref, _gray_for_ssim(img_from(data)))
            if s >= cfg.guard.min_ssim and (best_guard_ok is None or len(data) < len(best_guard_ok[0])):
                best_guard_ok = (data, img, cfg.min_quality, s)
            if target_long <= cfg.min_long_side_px:
                break
            continue
        data, q = found
        s = ssim(ref, _gray_for_ssim(img_from(data)))
        if s >= cfg.guard.min_ssim:
            return CompressionResult(data, content_type, img.width, img.height, q, round(s, 4), True, False,
                                     src.size_bytes)
        if target_long <= cfg.min_long_side_px:
            break

    # target & guard tidak bisa dipenuhi bersamaan -> utamakan keterbacaan
    if best_guard_ok is None:
        img = _resize(src.pil, base_long)
        data = _encode(img, cfg.format, cfg.max_quality)
        best_guard_ok = (data, img, cfg.max_quality, ssim(ref, _gray_for_ssim(img_from(data))))
    data, img, q, s = best_guard_ok
    if len(data) >= src.size_bytes and browser_ok:
        return CompressionResult(None, f"image/{src.format.lower()}", src.width, src.height, None, None,
                                 False, True, src.size_bytes)
    return CompressionResult(data, content_type, img.width, img.height, q, round(s, 4),
                             len(data) <= cfg.target_max_bytes, False, src.size_bytes)


def passthrough_of(src: LoadedImage, cfg: Compression) -> CompressionResult:
    """Pakai foto asli apa adanya (format yang bisa dibuka browser), walau di atas target ukuran."""
    if src.format in ("JPEG", "PNG", "WEBP"):
        return CompressionResult(None, f"image/{src.format.lower()}", src.width, src.height, None, None,
                                 src.size_bytes <= cfg.target_max_bytes, True, src.size_bytes)
    return encode_at(src, cfg, max(src.width, src.height), 95)


def encode_fit(src: LoadedImage, cfg: Compression, long_side: int, max_bytes: int, min_q: int, max_q: int
               ) -> CompressionResult | None:
    """Quality tertinggi (min_q..max_q) pada ukuran tertentu yang muat max_bytes. None jika tidak ada."""
    img = _resize(src.pil, long_side)
    lo, hi, found = min_q, max_q, None
    while lo <= hi:
        q = (lo + hi) // 2
        data = _encode(img, cfg.format, q, fast=True)
        if len(data) <= max_bytes:
            found, lo = (data, q), q + 1
        else:
            hi = q - 1
    if found is None:
        return None
    q = found[1]
    data = _encode(img, cfg.format, q)
    s = ssim(_gray_for_ssim(src.pil), _gray_for_ssim(img_from(data)))
    return CompressionResult(data, "image/webp" if cfg.format == "webp" else "image/jpeg", img.width, img.height, q,
                             round(s, 4), len(data) <= cfg.target_max_bytes, False, src.size_bytes)


def encode_at(src: LoadedImage, cfg: Compression, long_side: int, quality: int) -> CompressionResult:
    """Encode dengan ukuran & quality tertentu (dipakai penjaga keterbacaan teks untuk menaikkan kualitas)."""
    content_type = "image/webp" if cfg.format == "webp" else "image/jpeg"
    img = _resize(src.pil, long_side)
    data = _encode(img, cfg.format, quality)
    s = ssim(_gray_for_ssim(src.pil), _gray_for_ssim(img_from(data)))
    return CompressionResult(data, content_type, img.width, img.height, quality, round(s, 4),
                             len(data) <= cfg.target_max_bytes, False, src.size_bytes)


def img_from(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def thumbnail(src: LoadedImage, cfg: Thumbnail) -> bytes:
    img = _resize(src.pil, cfg.long_side_px)
    return _encode(img, "jpeg", cfg.quality)
