"""Generator sampel sintetis untuk uji & kalibrasi (sebelum sampel foto asli tersedia).

`pqe synth-samples ./samples` menghasilkan foto mirip POD (paket + resi + tekstur) dalam
berbagai kondisi: tajam, buram, pecah, gelap, terang, kontras rendah, kecil, dst.
"""

from __future__ import annotations

import io
import random
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow lama
        return ImageFont.load_default()


def base_scene(width: int = 2400, height: int = 1800, seed: int = 7) -> Image.Image:
    rng = np.random.default_rng(seed)
    random.seed(seed)
    # latar: gradien + tekstur (lantai/dinding)
    y, x = np.mgrid[0:height, 0:width]
    bg = 120 + 40 * np.sin(x / 180.0) + 25 * np.cos(y / 130.0)
    tex = cv2.GaussianBlur(rng.normal(0, 40, (height, width)).astype(np.float32), (0, 0), 2.5)
    rgb = np.stack([bg + tex, bg * 0.95 + tex, bg * 0.85 + tex], axis=-1)
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(img)
    # paket
    px0, py0, px1, py1 = int(width * 0.18), int(height * 0.2), int(width * 0.7), int(height * 0.85)
    d.rectangle([px0, py0, px1, py1], fill=(176, 132, 84), outline=(90, 60, 30), width=8)
    d.line([px0, (py0 + py1) // 2, px1, (py0 + py1) // 2], fill=(150, 110, 70), width=26)
    # label resi
    lx0, ly0 = px0 + 80, py0 + 90
    d.rectangle([lx0, ly0, lx0 + 760, ly0 + 520], fill=(245, 245, 240), outline=(30, 30, 30), width=4)
    f_big, f_small = _font(64), _font(38)
    d.text((lx0 + 30, ly0 + 25), "PCP EXPRESS", fill=(10, 10, 10), font=f_big)
    d.text((lx0 + 30, ly0 + 110), "AWB: 1234 5678 9012", fill=(10, 10, 10), font=f_small)
    d.text((lx0 + 30, ly0 + 165), "Penerima: Budi Santoso", fill=(10, 10, 10), font=f_small)
    d.text((lx0 + 30, ly0 + 220), "Jl. Merdeka No. 10, Jakarta", fill=(10, 10, 10), font=f_small)
    for i in range(28):  # barcode
        w = random.choice((4, 6, 10))
        bx = lx0 + 40 + i * 24
        d.rectangle([bx, ly0 + 300, bx + w, ly0 + 470], fill=(0, 0, 0))
    # objek lain (tangan/penerima disederhanakan)
    d.ellipse([int(width * 0.74), int(height * 0.25), int(width * 0.92), int(height * 0.55)], fill=(210, 170, 140))
    d.rectangle([int(width * 0.73), int(height * 0.55), int(width * 0.93), int(height * 0.95)], fill=(40, 70, 120))
    # noise sensor kamera
    arr = np.asarray(img).astype(np.int16) + rng.normal(0, 4, (height, width, 3)).astype(np.int16)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def to_jpeg(img: Image.Image, quality: int = 92) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def variants(seed: int = 7) -> dict[str, bytes]:
    """Nama sampel -> bytes JPEG. Prefix nama = verdict yang diharapkan (pass_/warn_/reject_)."""
    base = base_scene(seed=seed)
    w, h = base.size
    out = {
        "pass_sharp_large": to_jpeg(base, 92),
        "pass_huge_camera": to_jpeg(base_scene(4000, 3000, seed=seed + 1), 96),
        "pass_small_ok": to_jpeg(base.resize((1280, 960), Image.LANCZOS), 85),
        "warn_blur_mild": to_jpeg(base.filter(ImageFilter.GaussianBlur(6)), 92),
        "reject_blur_severe": to_jpeg(base.filter(ImageFilter.GaussianBlur(10)), 92),
        "warn_pixelated_upscaled": to_jpeg(
            base.resize((w // 16, h // 16), Image.BILINEAR).resize((w, h), Image.NEAREST), 90),
        "reject_upscaled_blurry": to_jpeg(
            base.resize((w // 16, h // 16), Image.BILINEAR).resize((w, h), Image.BILINEAR), 90),
        "reject_tiny": to_jpeg(base.resize((240, 180), Image.LANCZOS), 70),
        "reject_heavy_jpeg": to_jpeg(base.resize((1600, 1200), Image.LANCZOS), 6),
        "reject_dark": to_jpeg(ImageEnhance.Brightness(base).enhance(0.12), 92),
        "reject_overexposed": to_jpeg(ImageEnhance.Brightness(base).enhance(3.2), 92),
        "reject_low_contrast": to_jpeg(ImageEnhance.Contrast(base).enhance(0.18), 92),
    }
    return out


def write_samples(outdir: Path, seed: int = 7) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, data in variants(seed).items():
        p = outdir / f"{name}.jpg"
        p.write_bytes(data)
        paths.append(p)
    return paths
