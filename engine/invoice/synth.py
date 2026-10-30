"""Invoice sintetis untuk test & demo (tanpa dokumen nyata atau data pribadi).

Semua nama, NPWP, dan nomor rekening fiktif. `make_pdf` membuat PDF digital (text layer), `scan_of` mengubahnya menjadi
"hasil scan" (JPEG/PNG miring + noise) untuk menguji jalur OCR.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np
from PIL import Image


@dataclass
class Line:
    description: str
    quantity: str
    unit_price: str
    amount: str


@dataclass
class SynthInvoice:
    vendor_name: str = "PT Contoh Sinar Abadi"
    vendor_npwp: str = "01.234.567.8-901.000"
    buyer_name: str = "PT Pelanggan Fiktif"
    buyer_npwp: str = "02.345.678.9-012.000"
    invoice_number: str = "INV/2026/10/0042"
    invoice_date: str = "15/10/2026"
    due_date: str = "14/11/2026"
    period: str = "01/10/2026 - 31/10/2026"
    bank_account: str = "123-456-7890"
    bank_name: str = "Bank Contoh"
    lines: list[Line] = field(default_factory=lambda: [
        Line("Sewa server bulanan", "1", "10.000.000", "10.000.000"),
        Line("Lisensi aplikasi", "5", "500.000", "2.500.000"),
        Line("Dukungan teknis", "2", "750.000", "1.500.000"),
    ])
    subtotal: str = "14.000.000"
    discount: str = "1.000.000"
    dpp: str = "13.000.000"
    vat: str = "1.430.000"
    wht: str = "260.000"
    total: str = "14.430.000"
    layout: str = "standard"  # standard | custom (label tidak umum: untuk menguji template)
    qr_text: str | None = None


def _labels(inv: SynthInvoice) -> dict[str, str]:
    if inv.layout == "custom":
        return {"no": "Ref. Dokumen", "date": "Tgl. Cetak", "due": "Batas Bayar", "period": "Masa Layanan",
                "bank": "Transfer ke", "total": "Wajib Dibayar"}
    return {"no": "No. Invoice", "date": "Tanggal Invoice", "due": "Jatuh Tempo", "period": "Periode",
            "bank": "No. Rekening", "total": "Grand Total"}


def make_pdf(inv: SynthInvoice | None = None) -> bytes:
    from fpdf import FPDF

    inv = inv or SynthInvoice()
    lb = _labels(inv)
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_xy(15, 15)
    pdf.cell(0, 8, inv.vendor_name)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(15, 24)
    pdf.cell(0, 5, "Jl. Contoh Raya No. 1, Jakarta")
    pdf.set_xy(15, 30)
    pdf.cell(0, 5, f"NPWP: {inv.vendor_npwp}")
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_xy(140, 15)
    pdf.cell(0, 8, "INVOICE")
    pdf.set_font("Helvetica", "", 10)
    rows = [(lb["no"], inv.invoice_number), (lb["date"], inv.invoice_date), (lb["due"], inv.due_date),
            (lb["period"], inv.period)]
    y = 45
    for label, value in rows:
        pdf.set_xy(15, y)
        pdf.cell(40, 6, label)
        pdf.cell(4, 6, ":")
        pdf.cell(80, 6, value)
        y += 7
    pdf.set_xy(120, 45)
    pdf.cell(0, 6, "Kepada:")
    pdf.set_xy(120, 52)
    pdf.cell(0, 6, inv.buyer_name)
    pdf.set_xy(120, 59)
    pdf.cell(0, 6, f"NPWP Pembeli: {inv.buyer_npwp}")

    y = 85
    pdf.set_font("Helvetica", "B", 10)
    for x, w, t in ((15, 85, "Deskripsi"), (100, 20, "Qty"), (120, 35, "Harga Satuan"), (155, 40, "Jumlah")):
        pdf.set_xy(x, y)
        pdf.cell(w, 7, t, align="R" if x >= 100 else "L")
    pdf.set_font("Helvetica", "", 10)
    y += 9
    for ln in inv.lines:
        for x, w, t in ((15, 85, ln.description), (100, 20, ln.quantity), (120, 35, ln.unit_price),
                        (155, 40, ln.amount)):
            pdf.set_xy(x, y)
            pdf.cell(w, 7, t, align="R" if x >= 100 else "L")
        y += 8
    y += 6
    totals = [("Subtotal", inv.subtotal), ("Diskon", inv.discount), ("DPP", inv.dpp), ("PPN 11%", inv.vat),
              ("PPh 23", inv.wht), (lb["total"], inv.total)]
    for label, value in totals:
        if not value:
            continue
        pdf.set_xy(110, y)
        pdf.cell(40, 7, label)
        pdf.cell(5, 7, "Rp")
        pdf.cell(40, 7, value, align="R")
        y += 8
    y += 8
    pdf.set_xy(15, y)
    pdf.cell(0, 6, f"Pembayaran: {inv.bank_name}")
    pdf.set_xy(15, y + 7)
    pdf.cell(0, 6, f"{lb['bank']}: {inv.bank_account}")
    if inv.qr_text:
        import zxingcpp

        qr = zxingcpp.create_barcode(inv.qr_text, zxingcpp.BarcodeFormat.QRCode)
        img = Image.fromarray(np.asarray(qr.to_image(scale=6))).convert("L")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        pdf.image(buf, x=160, y=250, w=35, h=35)
    return bytes(pdf.output())


def scan_of(pdf: bytes, *, angle: float = 2.0, noise: float = 6.0, dpi: int = 200, fmt: str = "JPEG",
            seed: int = 7) -> bytes:
    """Tiruan hasil scan/foto: render halaman 1, miringkan, beri noise, simpan JPEG/PNG (tanpa text layer)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    try:
        img = doc[0].render(scale=dpi / 72, grayscale=True).to_pil().convert("L")
    finally:
        doc.close()
    img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    arr = np.asarray(img).astype(np.float32)
    rng = np.random.default_rng(seed)
    arr = arr * 0.85 + 20 + rng.normal(0, noise, arr.shape)  # kertas agak abu + noise sensor
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    out.save(buf, fmt, **({"quality": 85} if fmt == "JPEG" else {}))
    return buf.getvalue()
