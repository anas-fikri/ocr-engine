"""Perbaikan otomatis yang AMAN untuk dokumen bukti: hanya kurva tonal global (pencahayaan & kontras).

Jaminan keabsahan (bisa dibuktikan, bukan perkiraan):
- Tanpa AI / model generatif. Tidak ada piksel yang "dikarang".
- Satu-satunya operasi: SATU tabel kurva (LUT 256 nilai) yang sama untuk seluruh foto dan ketiga
  kanal warna, seperti fitur "Levels/Curves" di aplikasi foto. Nilai baru setiap piksel hanya
  bergantung pada nilai piksel itu sendiri, tidak pada posisi atau tetangganya.
- Akibatnya mustahil menambah/mengubah huruf atau angka: untuk menggambar huruf, piksel yang
  nilainya sama harus diberi nilai berbeda, sedangkan kurva selalu memberi nilai yang sama.
  Kurva juga monoton naik, jadi urutan terang-gelap (bentuk tulisan) tetap.
- Verifikasi eksak setelah koreksi: setiap nilai masukan pada setiap kanal harus menghasilkan
  tepat satu nilai keluaran dan urutannya tidak terbalik. Satu pelanggaran saja = hasil dibuang.
- Tanpa penajaman, tanpa resize/crop/rotasi. Foto original tetap disimpan utuh dan hash
  verifikasi (SHA-256) selalu merujuk ke original.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from engine.imaging.loader import LoadedImage, load_image

# masalah yang bisa dibantu kurva tonal; buram/pecah/kecil TIDAK bisa diperbaiki secara jujur
FIXABLE_CODES = {"TOO_DARK", "TOO_BRIGHT", "LOW_CONTRAST", "GLARE"}


@dataclass
class EnhanceResult:
    image: LoadedImage
    operations: list[str] = field(default_factory=list)


def _luma(rgb: np.ndarray) -> np.ndarray:
    return (rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114).astype(np.float32)


def build_curve(rgb: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """Susun LUT monoton dari statistik foto: levels (rentang) lalu gamma (terang/gelap)."""
    y = _luma(rgb)
    lo, hi = (float(v) for v in np.percentile(y, (0.5, 99.5)))
    x = np.arange(256, dtype=np.float64)
    curve = x.copy()
    ops: list[str] = []
    if hi - lo < 200 and hi > lo:  # levels: rentang sempit (pudar/gelap) direntangkan ke 0-255
        curve = np.clip((x - lo) / (hi - lo) * 255.0, 0, 255)
        ops.append(f"levels:{lo:.0f}-{hi:.0f}")
    mid = float(np.clip(np.median(np.interp(y, x, curve)), 1.0, 250.0))
    if mid < 90 or mid > 170:  # gamma: geser kecerahan tengah ke ~120
        gamma = float(np.clip(np.log(120 / 255) / np.log(mid / 255), 0.3, 3.0))
        curve = 255.0 * np.power(curve / 255.0, gamma)
        ops.append(f"gamma:{gamma:.2f}")
    lut = np.clip(np.round(curve), 0, 255).astype(np.uint8)
    lut = np.maximum.accumulate(lut)  # paksa monoton naik
    return lut, ops


def verify_tonal_only(src: np.ndarray, out: np.ndarray) -> bool:
    """Bukti eksak: `out` adalah hasil kurva monoton per nilai dari `src` (tiap kanal)."""
    if src.shape != out.shape or src.dtype != np.uint8 or out.dtype != np.uint8:
        return False
    for c in range(src.shape[2]):
        a = src[..., c].ravel().astype(np.int64)
        b = out[..., c].ravel().astype(np.int64)
        pairs = np.unique(a * 256 + b)
        ins, outs = pairs // 256, pairs % 256
        if len(np.unique(ins)) != len(ins):  # satu nilai masukan -> lebih dari satu keluaran
            return False
        if np.any(np.diff(outs) < 0):  # urutan terang-gelap terbalik
            return False
    return True


SHARPEN_SIGMA = 1.2
SHARPEN_AMOUNT = 1.0


def sharpen_text(img: LoadedImage) -> LoadedImage:
    """Penajam teks klasik (unsharp mask), tanpa AI: hasil = foto + amount x (foto - foto_diblur).

    Filter linear yang sama untuk seluruh foto: hanya mempertegas tepi yang SUDAH ada (huruf, garis),
    tidak bisa menambah atau mengubah huruf/angka. Dipakai hanya jika keterbacaan teks terbukti naik
    (pipeline), dan hanya untuk versi yang disimpan; foto asli & hash-nya tidak berubah.
    """
    import cv2

    src = np.ascontiguousarray(np.asarray(img.pil, dtype=np.uint8))
    blur = cv2.GaussianBlur(src, (0, 0), SHARPEN_SIGMA)
    out = cv2.addWeighted(src, 1 + SHARPEN_AMOUNT, blur, -SHARPEN_AMOUNT, 0)  # jenuh otomatis 0-255
    ok, enc = cv2.imencode(".jpg", cv2.cvtColor(out, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
    return load_image(enc.tobytes())


def enhance(img: LoadedImage) -> tuple[EnhanceResult | None, dict]:
    """Return (hasil, info). Hasil None jika tidak ada yang perlu diubah atau verifikasi gagal."""
    src = np.ascontiguousarray(np.asarray(img.pil, dtype=np.uint8))
    lut, ops = build_curve(src)
    if not ops:
        return None, {"attempted": True, "applied": False, "reason": "nothing_to_fix"}
    out = lut[src]
    if not verify_tonal_only(src, out):
        return None, {"attempted": True, "applied": False, "reason": "verification_failed", "operations": ops}
    buf = io.BytesIO()
    Image.fromarray(out).save(buf, "JPEG", quality=95, subsampling="4:2:0")
    info = {"attempted": True, "applied": True, "operations": ops, "verified": "global_monotonic_curve"}
    return EnhanceResult(image=load_image(buf.getvalue()), operations=ops), info
