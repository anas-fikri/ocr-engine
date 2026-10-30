"""Membaca dokumen menjadi halaman + baris teks ber-koordinat. Dijalankan HANYA di proses anak (sandbox.py).

Layer 1: PDF digital → teks + kotak dari PDFium (tanpa OCR).
Layer 3: gambar / halaman PDF hasil scan → preprocess (deskew, kontras, binarize) → Tesseract ind+eng.
QR (untuk layer 2 e-Faktur) dibaca di sini juga bila diminta; tafsirannya di efaktur.py (proses induk).

Koordinat blok selalu relatif terhadap halaman: [x0, y0, x1, y1] dalam 0-1, titik (0,0) di kiri atas.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from PIL import Image

MAX_PAGE_POINTS = 5000  # sisi halaman PDF maks. (pt, 1/72 inci): ±176 cm
MAX_RENDER_PIXELS = 40_000_000
MIN_TEXT_CHARS = 20
MIN_ALNUM_RATIO = 0.5
OCR_MIN_SIDE = 1600  # teks kecil: perbesar dulu supaya tinggi huruf cukup untuk Tesseract
OCR_MAX_SIDE = 4200
QR_SCAN_DPI = 150


class ReadError(Exception):
    """Error permanen (dokumen tidak bisa / tidak boleh dibaca). Tidak di-retry."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Options:
    media_type: str
    max_pages: int = 20
    render_dpi: int = 300
    max_image_pixels: int = 50_000_000
    ocr_page_timeout_sec: int = 30
    read_qr: bool = False


def detect_media_type(data: bytes) -> str | None:
    """Tipe file dari magic bytes (bukan nama file / Content-Type yang bisa dipalsukan)."""
    head = data[:1024]
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if b"%PDF-" in head:  # spesifikasi PDF mengizinkan sampah sebelum header di 1 KB pertama
        return "application/pdf"
    return None


# ---------------------------------------------------------------------------------------------- versi engine OCR
def versions() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        import tesserocr

        out["tesseract"] = tesserocr.tesseract_version().split()[1]
    except Exception:
        pass
    try:
        import pypdfium2

        out["pdfium"] = str(pypdfium2.version.PDFIUM_INFO).split()[0]
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------------------------- Tesseract
_api = None


def _tesseract_api():
    global _api
    if _api is None:
        from engine.analyzers.ocr import _tesserocr

        tess, path, lang = _tesserocr()
        if tess is None:
            raise ReadError("OCR_UNAVAILABLE", "Tesseract tidak terpasang di server engine")
        _api = tess.PyTessBaseAPI(path=path, lang=lang)  # type: ignore[attr-defined]
    return _api


def _deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    from engine.analyzers.ocr import estimate_skew
    from engine.imaging.loader import LoadedImage

    rgb = Image.fromarray(gray).convert("RGB")
    angle = estimate_skew(LoadedImage(data=b"", pil=rgb, format="PNG", exif=Image.Exif(), quantization=None))
    if abs(angle) < 1.5:
        return gray, 0.0
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(gray, m, (nw, nh), flags=cv2.INTER_CUBIC, borderValue=255), angle


def preprocess(img: Image.Image) -> tuple[np.ndarray, dict[str, Any]]:
    """Grayscale → skala baca → deskew → ratakan bayangan → kontras (CLAHE) → binarize (Otsu).

    Hanya untuk dibaca, tidak disimpan. Bayangan/pencahayaan tidak rata (foto HP) diratakan dengan membagi gambar dengan
    estimasi latar (closing morfologi + blur); tanpa langkah ini Otsu menghitamkan sisi yang gelap. Binarize adaptif
    lokal sempat dicoba dan justru memperburuk scan bernoise (confidence Tesseract turun dari ±93 ke ±45).
    """
    gray = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    long_side = max(h, w)
    scale = 1.0
    if long_side < OCR_MIN_SIDE:
        scale = OCR_MIN_SIDE / long_side
    elif long_side > OCR_MAX_SIDE:
        scale = OCR_MAX_SIDE / long_side
    if abs(scale - 1) > 1e-3:
        gray = cv2.resize(gray, (round(w * scale), round(h * scale)),
                          interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    gray, angle = _deskew(gray)
    k = max(15, max(gray.shape) // 100) | 1
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    gray = cv2.divide(gray, cv2.GaussianBlur(bg, (0, 0), k / 2), scale=255)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    return bw, {"scale": round(scale, 3), "deskew_deg": angle}


def ocr_page(img: Image.Image, timeout_sec: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import tesserocr

    bw, info = preprocess(img)
    h, w = bw.shape
    api = _tesseract_api()
    api.SetPageSegMode(tesserocr.PSM.AUTO)
    api.SetImage(Image.fromarray(bw))
    if not api.Recognize(timeout=max(1000, timeout_sec * 1000)):
        raise ReadError("PROCESSING_TIMEOUT", "OCR satu halaman melewati batas waktu")
    lines: list[dict[str, Any]] = []
    it = api.GetIterator()
    if it is not None:
        lvl = tesserocr.RIL.TEXTLINE
        for line in tesserocr.iterate_level(it, lvl):
            try:
                text = (line.GetUTF8Text(lvl) or "").strip()
                conf = float(line.Confidence(lvl))
                box = line.BoundingBox(lvl)
            except RuntimeError:
                continue
            if not text or box is None:
                continue
            x0, y0, x1, y1 = box
            lines.append({"text": " ".join(text.split()), "bbox": [x0 / w, y0 / h, x1 / w, y1 / h],
                          "conf": round(max(0.0, min(conf, 100.0)) / 100, 3)})
    info["mean_conf"] = round(sum(x["conf"] for x in lines) / len(lines), 3) if lines else 0.0
    return lines, info


# ---------------------------------------------------------------------------------------------- QR
def read_qr(img: Image.Image) -> list[str]:
    try:
        import zxingcpp
    except Exception:
        return []
    gray = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    try:
        found = zxingcpp.read_barcodes(gray, formats=zxingcpp.BarcodeFormat.QRCode)
    except Exception:
        return []
    return [r.text for r in found if r.text][:5]


# ---------------------------------------------------------------------------------------------- PDF
def _group_lines(segs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gabungkan potongan teks PDF menjadi baris; potongan yang berjauhan di baris yang sama tetap blok terpisah
    (kolom tabel, label vs nilai di kanan halaman)."""
    segs = sorted(segs, key=lambda s: ((s["bbox"][1] + s["bbox"][3]) / 2, s["bbox"][0]))
    rows: list[list[dict[str, Any]]] = []
    for s in segs:
        cy = (s["bbox"][1] + s["bbox"][3]) / 2
        if rows:
            r = rows[-1][0]["bbox"]
            if r[1] <= cy <= r[3]:
                rows[-1].append(s)
                continue
        rows.append([s])
    out = []
    for row in rows:
        row.sort(key=lambda s: s["bbox"][0])
        cur = None
        for s in row:
            height = s["bbox"][3] - s["bbox"][1]
            if cur is not None and s["bbox"][0] - cur["bbox"][2] <= 1.2 * height:
                cur["text"] += " " + s["text"]
                cur["bbox"] = [min(cur["bbox"][0], s["bbox"][0]), min(cur["bbox"][1], s["bbox"][1]),
                               max(cur["bbox"][2], s["bbox"][2]), max(cur["bbox"][3], s["bbox"][3])]
            else:
                if cur is not None:
                    out.append(cur)
                cur = {"text": s["text"], "bbox": list(s["bbox"]), "conf": 0.99}
        if cur is not None:
            out.append(cur)
    return out


def _pdf_text_lines(page) -> tuple[list[dict[str, Any]], str]:
    # get_bbox = kotak efektif halaman (crop box, termasuk yang diwarisi dari /Pages); get_cropbox tidak mewarisi
    left, bottom, right, top = page.get_bbox()
    pw, ph = max(1e-6, right - left), max(1e-6, top - bottom)
    tp = page.get_textpage()
    try:
        segs = []
        for i in range(min(tp.count_rects(), 20_000)):
            x0, b, x1, t = tp.get_rect(i)
            text = " ".join((tp.get_text_bounded(x0, b, x1, t) or "").split())
            if not text:
                continue
            segs.append({"text": text, "bbox": [max(0.0, (x0 - left) / pw), max(0.0, (top - t) / ph),
                                                min(1.0, (x1 - left) / pw), min(1.0, (top - b) / ph)]})
        full = tp.get_text_range() or ""
    finally:
        tp.close()
    return _group_lines(segs), full


def _usable_text(text: str) -> bool:
    chars = [c for c in text if not c.isspace()]
    if len(chars) < MIN_TEXT_CHARS:
        return False
    return sum(c.isalnum() for c in chars) / len(chars) >= MIN_ALNUM_RATIO


def _render(page, dpi: int) -> Image.Image:
    w, h = page.get_size()
    scale = dpi / 72
    if w * h * scale * scale > MAX_RENDER_PIXELS:
        scale = (MAX_RENDER_PIXELS / (w * h)) ** 0.5
    return page.render(scale=scale, grayscale=True).to_pil()


def read_pdf(data: bytes, opt: Options) -> dict[str, Any]:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as e:
        msg = str(e).lower()
        if "password" in msg:
            raise ReadError("PDF_ENCRYPTED", "PDF dilindungi password") from e
        raise ReadError("UNREADABLE_FILE", "PDF rusak atau tidak bisa dibuka") from e
    try:
        n = len(pdf)
        if n == 0:
            raise ReadError("UNREADABLE_FILE", "PDF tanpa halaman")
        if n > opt.max_pages:
            raise ReadError("TOO_MANY_PAGES", f"PDF berisi {n} halaman (maks. {opt.max_pages})")
        pages, qr = [], []
        for i in range(n):
            page = pdf[i]
            try:
                w, h = page.get_size()
                if w <= 0 or h <= 0 or w > MAX_PAGE_POINTS or h > MAX_PAGE_POINTS:
                    raise ReadError("PAGE_TOO_LARGE", f"ukuran halaman {i + 1} tidak wajar ({w:.0f}x{h:.0f} pt)")
                lines, full = _pdf_text_lines(page)
                if _usable_text(full):
                    pages.append({"index": i, "source": "pdf_text", "width": round(w, 1), "height": round(h, 1),
                                  "lines": lines, "mean_conf": 0.99})
                    if opt.read_qr and i == 0:
                        qr += read_qr(_render(page, QR_SCAN_DPI))
                else:
                    img = _render(page, opt.render_dpi)
                    if opt.read_qr and i == 0:
                        qr += read_qr(img)
                    ocr_lines, info = ocr_page(img, opt.ocr_page_timeout_sec)
                    pages.append({"index": i, "source": "ocr", "width": img.width, "height": img.height,
                                  "lines": ocr_lines, **info})
            finally:
                page.close()
        return {"kind": "pdf", "pages": pages, "qr": qr}
    finally:
        pdf.close()


# ---------------------------------------------------------------------------------------------- gambar
def read_image(data: bytes, opt: Options) -> dict[str, Any]:
    from PIL import ImageOps, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = opt.max_image_pixels
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ("JPEG", "PNG"):
            raise ReadError("UNSUPPORTED_MEDIA_TYPE", "hanya PDF, JPEG, dan PNG")
        if img.width * img.height > opt.max_image_pixels:  # dicek dari header, sebelum decode
            raise ReadError("IMAGE_TOO_LARGE", f"gambar {img.width}x{img.height} melebihi batas piksel")
        img.load()
        img = ImageOps.exif_transpose(img)
    except ReadError:
        raise
    except Image.DecompressionBombError as e:  # PIL menolak dari header (> 2x batas) sebelum decode
        raise ReadError("IMAGE_TOO_LARGE", "gambar melebihi batas piksel") from e
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ReadError("UNREADABLE_FILE", f"gambar rusak atau tidak bisa dibaca: {type(e).__name__}") from e
    if img.width < 32 or img.height < 32:
        raise ReadError("UNREADABLE_FILE", "gambar terlalu kecil")
    qr = read_qr(img) if opt.read_qr else []
    lines, info = ocr_page(img, opt.ocr_page_timeout_sec)
    return {"kind": "image", "pages": [{"index": 0, "source": "ocr", "width": img.width, "height": img.height,
                                        "lines": lines, **info}], "qr": qr}


def read_document(data: bytes, opt: Options) -> dict[str, Any]:
    kind = detect_media_type(data)
    if kind is None or kind != opt.media_type:
        raise ReadError("UNSUPPORTED_MEDIA_TYPE", "hanya PDF, JPEG, dan PNG")
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    out = read_pdf(data, opt) if kind == "application/pdf" else read_image(data, opt)
    out["versions"] = versions()
    return out
