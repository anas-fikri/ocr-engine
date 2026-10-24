"""Evidence penagihan: data & PDF bukti pengiriman untuk pelanggan.

Hanya foto yang layak (PASS atau di-approve ops) yang ditampilkan. Skor internal dan
risk keaslian TIDAK ikut ditampilkan ke pelanggan.
"""

from __future__ import annotations

import io
from datetime import datetime
from zoneinfo import ZoneInfo

from fpdf import FPDF
from sqlalchemy import select

from engine.db import Photo, as_aware
from engine.serializers import evidence_eligible
from engine.settings import get_settings
from engine.storage import Storage

TEXT = {
    "id": {
        "title": "Bukti Pengiriman (Proof of Delivery)",
        "awb": "No. Resi", "time": "Waktu foto", "received": "Diterima sistem", "courier": "Kurir",
        "type": "Jenis foto", "location": "Lokasi", "verify": "Verifikasi keaslian",
        "hash": "SHA-256 file asli", "none": "Belum ada foto bukti yang layak untuk resi ini.",
        "generated": "Dibuat", "page": "Halaman",
    },
    "en": {
        "title": "Proof of Delivery",
        "awb": "AWB", "time": "Photo time", "received": "Received by system", "courier": "Courier",
        "type": "Photo type", "location": "Location", "verify": "Authenticity check",
        "hash": "Original file SHA-256", "none": "No eligible proof photo for this AWB yet.",
        "generated": "Generated", "page": "Page",
    },
}


def eligible_photos(session, tenant_codes: list[str] | None, awbs: list[str],
                    customer_id: str | None = None) -> list[Photo]:
    # hanya foto yang disimpan aplikasi di SILO (bucket "" = tidak disimpan / lokasi belum dilaporkan)
    q = select(Photo).where(Photo.awb.in_(awbs), Photo.status == "done", Photo.bucket != "")
    if tenant_codes is not None:
        q = q.where(Photo.tenant_code.in_(tenant_codes))
    if customer_id is not None:
        q = q.where(Photo.customer_id == customer_id)
    rows = session.scalars(q.order_by(Photo.awb, Photo.captured_at, Photo.received_at)).all()
    return [p for p in rows if evidence_eligible(p)]


def evidence_item(p: Photo, storage: Storage | None, ttl: int) -> dict:
    s = get_settings()
    bucket, key = (p.processed_bucket, p.processed_key) if p.processed_key else (p.bucket, p.object_key)
    return {
        "photo_id": p.id,
        "awb": p.awb,
        "photo_type": p.photo_type,
        "courier_id": p.courier_id,
        "captured_at": (as_aware(p.captured_at) or as_aware(p.received_at)).isoformat(),
        "received_at": as_aware(p.received_at).isoformat(),
        "location": {"lat": p.lat, "lng": p.lng} if p.lat is not None else None,
        "image_url": storage.presign(bucket, key, ttl) if storage else None,
        "sha256": p.sha256,
        "verify_url": f"{s.public_base_url.rstrip('/')}/v1/verify/{p.sha256}",
    }


def _latin1(text: str) -> str:
    return (text or "").encode("latin-1", "replace").decode("latin-1")


class _PDF(FPDF):
    lang = "id"

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", size=8)
        self.set_text_color(120)
        self.cell(0, 6, f"{TEXT[self.lang]['page']} {self.page_no()}/{{nb}}", align="C")


def build_pdf(photos: list[Photo], storage: Storage, lang: str = "id", tz: str = "Asia/Jakarta",
              awbs: list[str] | None = None) -> bytes:
    t = TEXT.get(lang, TEXT["id"])
    zone = ZoneInfo(tz)
    pdf = _PDF(format="A4")
    pdf.lang = lang if lang in TEXT else "id"
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(True, margin=15)

    def fmt(dt) -> str:
        dt = as_aware(dt)
        return dt.astimezone(zone).strftime("%d-%m-%Y %H:%M %Z") if dt else "-"

    if not photos:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, _latin1(t["title"]), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=11)
        pdf.multi_cell(0, 7, _latin1(f"{t['awb']}: {', '.join(awbs or [])}\n{t['none']}"))
        return bytes(pdf.output())

    for p in photos:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, _latin1(t["title"]), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=8)
        pdf.set_text_color(120)
        pdf.cell(0, 5, _latin1(f"{t['generated']}: {fmt(datetime.now(zone))}"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0)
        pdf.ln(2)
        rows = [
            (t["awb"], p.awb or "-"),
            (t["time"], fmt(p.captured_at or p.received_at)),
            (t["received"], fmt(p.received_at)),
            (t["courier"], p.courier_id or "-"),
            (t["type"], p.photo_type or "-"),
            (t["location"], f"{p.lat:.6f}, {p.lng:.6f}" if p.lat is not None and p.lng is not None else "-"),
        ]
        for label, value in rows:
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(45, 7, _latin1(label))
            pdf.set_font("Helvetica", size=10)
            pdf.cell(0, 7, _latin1(str(value)), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
        item = evidence_item(p, None, 0)
        bucket = p.processed_bucket or p.bucket
        key = p.processed_key or p.object_key
        try:
            img = storage.get(bucket, key).data
            max_w, max_h = pdf.w - pdf.l_margin - pdf.r_margin, pdf.h - pdf.get_y() - 40
            from PIL import Image

            with Image.open(io.BytesIO(img)) as im:
                w, h = im.size
            scale = min(max_w / w, max_h / h)
            pdf.image(io.BytesIO(img), x=pdf.l_margin, w=w * scale, h=h * scale)
        except Exception as e:  # gambar gagal dimuat tidak boleh menggagalkan seluruh PDF
            pdf.set_font("Helvetica", "I", 9)
            pdf.cell(0, 7, _latin1(f"[gambar tidak tersedia: {type(e).__name__}]"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
        pdf.set_font("Helvetica", size=8)
        pdf.multi_cell(0, 4.5, _latin1(f"{t['hash']}: {p.sha256}\n{t['verify']}: {item['verify_url']}"))
    return bytes(pdf.output())
