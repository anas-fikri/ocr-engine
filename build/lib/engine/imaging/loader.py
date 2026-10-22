"""Decode gambar sekali, siapkan representasi yang dipakai semua analyzer."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from functools import cached_property

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

try:  # HEIC/HEIF dari iPhone
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - opsional
    pass

Image.MAX_IMAGE_PIXELS = 80_000_000  # cegah decompression bomb


class UnreadableImage(Exception):
    """File bukan gambar atau rusak. Error permanen: tidak di-retry."""


@dataclass
class LoadedImage:
    data: bytes
    pil: Image.Image  # sudah dirotasi sesuai EXIF, mode RGB
    format: str  # JPEG / PNG / HEIF / ...
    exif: Image.Exif
    quantization: dict | None
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def width(self) -> int:
        return self.pil.width

    @property
    def height(self) -> int:
        return self.pil.height

    @property
    def size_bytes(self) -> int:
        return len(self.data)

    @cached_property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @cached_property
    def gray(self) -> np.ndarray:
        """Grayscale resolusi penuh (uint8)."""
        return cv2.cvtColor(np.asarray(self.pil), cv2.COLOR_RGB2GRAY)

    def gray_scaled(self, long_side: int) -> np.ndarray:
        """Grayscale yang dinormalisasi ke sisi terpanjang tertentu agar metrik bisa dibandingkan."""
        key = ("gray", long_side)
        if key not in self._cache:
            g = self.gray
            h, w = g.shape
            scale = long_side / max(h, w)
            if abs(scale - 1.0) > 1e-3:
                interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
                g = cv2.resize(g, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=interp)
            self._cache[key] = g
        return self._cache[key]


def load_image(data: bytes) -> LoadedImage:
    if not data:
        raise UnreadableImage("file kosong")
    try:
        img = Image.open(io.BytesIO(data))
        fmt = (img.format or "UNKNOWN").upper()
        quant = getattr(img, "quantization", None) if fmt == "JPEG" else None
        exif = img.getexif()
        img.load()
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
        raise UnreadableImage(str(e)) from e
    if img.width < 8 or img.height < 8:
        raise UnreadableImage(f"gambar terlalu kecil ({img.width}x{img.height})")
    return LoadedImage(data=data, pil=img, format=fmt, exif=exif, quantization=quant)
