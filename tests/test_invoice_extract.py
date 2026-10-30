"""Unit test normalisasi nilai, plugin jenis dokumen, dan layer 2 (QR e-Faktur)."""

from __future__ import annotations

import httpx
import pytest

from engine.documents import parsing as P
from engine.documents.document import Document


@pytest.mark.parametrize("text,expected", [
    ("Tanggal: 15/10/2026", "2026-10-15"),
    ("15-10-26", "2026-10-15"),
    ("15.10.2026", "2026-10-15"),
    ("2026-10-15", "2026-10-15"),
    ("15 Oktober 2026", "2026-10-15"),
    ("15 Okt 2026", "2026-10-15"),
    ("15-Agu-2026", "2026-08-15"),
    ("October 15, 2026", "2026-10-15"),
    ("Oct 15 2026", "2026-10-15"),
    ("12/31/2026", "2026-12-31"),  # bulan/hari hanya jika hari/bulan tidak mungkin
    ("31/02/2026", None),
    ("INV/2026/10/0042", None),
])
def test_normalizers_dates(text, expected):
    assert P.normalize_date(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Rp 1.234.567,89", "1234567.89"),
    ("Rp 1.500.000,-", "1500000"),
    ("1,234,567.89", "1234567.89"),
    ("IDR 2,500,000.00", "2500000"),
    ("260.000", "260000"),
    ("1.000", "1000"),
    ("12,5", "12.50"),
    ("PPN 11% Rp 1.430.000", "1430000"),
    ("-50.000", "-50000"),
    (1500000, "1500000"),
    ("1234567.89", "1234567.89"),
    ("bukan angka", None),
    ("9" * 40, None),
])
def test_normalizers_money(text, expected):
    assert P.normalize_money(text) == expected


def test_normalizers_ids():
    assert P.normalize_npwp("01.234.567.8-901.000") == "012345678901000"
    assert P.normalize_npwp("3171234567890123") == "3171234567890123"  # NPWP 16 digit (NIK)
    assert P.normalize_npwp("12.345") is None
    assert P.normalize_digits("123-456 7890") == "1234567890"
    assert [m.value for m in P.find_npwp("NPWP: 01.234.567.8-901.000 / 02.345.678.9-012.000")] == \
        ["012345678901000", "023456789012000"]
    assert P.shape_of("INV/2026/10/0042") == "AAA/9999/99/9999"
    assert P.shape_regex("AAA/9999/99/9999").search("No: INV/2026/11/0107.").group(0) == "INV/2026/11/0107"
    assert P.find_month_year("Periode Februari 2028") == ("2028-02-01", "2028-02-29")


def _doc(lines: list[tuple[str, list[float]]]) -> Document:
    return Document.from_read({"pages": [{"index": 0, "source": "pdf_text", "lines": [
        {"text": t, "bbox": b, "conf": 0.99} for t, b in lines]}]})


def test_cross_check_flags_inconsistent_total():
    from engine.documents.invoice.extract import InvoiceExtractor

    doc = _doc([("PT Vendor Fiktif", [0.1, 0.05, 0.4, 0.07]),
                ("DPP: Rp 1.000.000", [0.5, 0.5, 0.9, 0.52]),
                ("PPN 11%: Rp 110.000", [0.5, 0.53, 0.9, 0.55]),
                ("Grand Total: Rp 9.999.999", [0.5, 0.56, 0.9, 0.58])])
    ex = InvoiceExtractor()
    fields = ex.extract(doc, None, None)
    total, vat = fields["total"].confidence, fields["vat_amount"].confidence
    issues = ex.validate(fields)
    assert [i["code"] for i in issues] == ["TOTAL_MISMATCH"]
    assert fields["total"].confidence < total and fields["vat_amount"].confidence > vat  # PPN = 11% DPP: konsisten


def test_buyer_npwp_is_not_vendor_npwp():
    from engine.documents.invoice.extract import InvoiceExtractor

    doc = _doc([("Kepada: PT Pembeli", [0.1, 0.05, 0.4, 0.07]),
                ("NPWP Pembeli: 02.345.678.9-012.000", [0.1, 0.08, 0.5, 0.1]),
                ("NPWP: 01.234.567.8-901.000", [0.1, 0.3, 0.5, 0.32])])
    assert InvoiceExtractor().extract(doc, None, None)["vendor_npwp"].value == "012345678901000"


class ReceiptExtractor:
    """Contoh plugin jenis dokumen baru (dimuat lewat PQE_DOCUMENT_PLUGINS)."""


def _receipt_plugin():
    from engine.documents.plugins import DocumentExtractor, Field

    class Receipt(DocumentExtractor):
        document_type = "receipt"
        version = "7"

        def detect(self, doc):
            return 1.0 if "kwitansi" in doc.text.lower() else 0.0

        def extract(self, doc, template, hint):
            rows = [" ".join(b.text for b in r) for r in doc.rows()]
            ms = [m.value for t in rows if "total" in t.lower() for m in P.find_money(t)]
            return {"total": Field(ms[-1], 0.5, "pdf_text")} if ms else {}

        def validate(self, fields):
            return [] if fields else [{"code": "NO_TOTAL", "fields": ["total"], "message": "-"}]

    return Receipt


def test_plugin_registry(invoice, monkeypatch):
    """AC-12: jenis dokumen baru = plugin detect/extract/validate, tanpa mengubah engine."""
    import sys
    import types

    from engine import settings
    from engine.documents import plugins
    from engine.documents.invoice.synth import make_pdf
    from engine.documents.worker import DocumentWorker

    mod = types.ModuleType("pqe_test_receipt_plugin")
    mod.Receipt = _receipt_plugin()
    monkeypatch.setitem(sys.modules, "pqe_test_receipt_plugin", mod)
    monkeypatch.setenv("PQE_DOCUMENT_PLUGINS", "pqe_test_receipt_plugin:Receipt, modul.tidak.ada:X")
    settings.reset_settings_cache()
    plugins.registry.cache_clear()
    assert set(plugins.registry()) == {"invoice", "receipt"}  # plugin rusak dilewati, tidak menjatuhkan engine
    r = invoice.a.post_job(make_pdf(), document_type="receipt")
    assert r.status_code == 202
    DocumentWorker(concurrency=0).run_until_empty()
    body = invoice.a.get(f"/v1/jobs/{r.json()['job_id']}").json()
    assert body["document_type"] == "receipt" and body["engine"]["extractor.receipt"] == "7"
    assert body["fields"]["total"]["value"] == "14430000"
    assert any(i["code"] == "NOT_LIKELY_DOCUMENT_TYPE" for i in body["validation"])


# ---------------------------------------------------------------------------------------------- layer 2 e-Faktur
QR = "http://svc.efaktur.pajak.go.id/validasi/faktur/012345678901000/0102612345678/3031300d06092a864886f70d"
DJP_XML = b"""<?xml version="1.0" encoding="UTF-8"?><resValidateFakturPm>
<nomorFaktur>0102612345678</nomorFaktur><tanggalFaktur>15/10/2026</tanggalFaktur>
<npwpPenjual>012345678901000</npwpPenjual><namaPenjual>PT CONTOH SINAR ABADI</namaPenjual>
<jumlahDpp>13000000</jumlahDpp><jumlahPpn>1430000</jumlahPpn><statusFaktur>Faktur Pajak Normal</statusFaktur>
</resValidateFakturPm>"""


@pytest.fixture(scope="module")
def efaktur_pdf():
    from engine.documents.invoice.synth import SynthInvoice, make_pdf

    # QR berisi NPWP benar, teks invoice sengaja salah cetak DPP agar terlihat QR yang dipakai
    return make_pdf(SynthInvoice(qr_text=QR, dpp="13.000.001"))


def _set_mode(monkeypatch, mode: str) -> None:
    from engine import settings

    monkeypatch.setenv("PQE_INVOICE_EFAKTUR_QR", mode)
    settings.reset_settings_cache()


def test_efaktur_off_by_default(invoice, efaktur_pdf):
    from engine.documents.worker import DocumentWorker

    jid = invoice.a.post_job(efaktur_pdf).json()["job_id"]
    DocumentWorker(concurrency=0).run_until_empty()
    body = invoice.a.get(f"/v1/jobs/{jid}").json()
    assert "efaktur_qr" not in body["layers"] and "efaktur" not in body
    assert body["fields"]["dpp"]["value"] == "13000001"


def test_efaktur_decode_mode(invoice, efaktur_pdf, monkeypatch):
    from engine.documents.worker import DocumentWorker

    _set_mode(monkeypatch, "decode")
    monkeypatch.setattr("engine.documents.invoice.efaktur.httpx.Client",
                        lambda *a, **k: pytest.fail("decode tanpa jaringan"))
    jid = invoice.a.post_job(efaktur_pdf).json()["job_id"]
    DocumentWorker(concurrency=0).run_until_empty()
    body = invoice.a.get(f"/v1/jobs/{jid}").json()
    assert "efaktur_qr" in body["layers"] and body["efaktur"]["nomor_faktur"] == "0102612345678"
    assert body["fields"]["vendor_npwp"] == {"value": "012345678901000", "confidence": 0.99,
                                             "source": "efaktur_qr", "method": "qr"}


def test_efaktur_lookup_mode(invoice, efaktur_pdf, monkeypatch):
    from engine.documents.worker import DocumentWorker

    _set_mode(monkeypatch, "lookup")
    seen = []

    def djp(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(200, content=DJP_XML)

    real = httpx.Client
    monkeypatch.setattr("engine.documents.invoice.efaktur.httpx.Client",
                        lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(djp)}))
    jid = invoice.a.post_job(efaktur_pdf).json()["job_id"]
    DocumentWorker(concurrency=0).run_until_empty()
    body = invoice.a.get(f"/v1/jobs/{jid}").json()
    f = body["fields"]
    assert seen == [QR.replace("http://", "https://")]  # selalu HTTPS
    # nilai QR yang sama dengan cetakan → confidence 0,99 dari QR
    assert f["vat_amount"] == {"value": "1430000", "confidence": 0.99, "source": "efaktur_qr", "method": "qr"}
    assert f["invoice_date"]["value"] == "2026-10-15" and f["invoice_date"]["method"] == "qr"
    # QR berbeda dengan cetakan → cetakan dipertahankan, confidence turun, dilaporkan (QR bisa milik faktur lain)
    assert f["dpp"]["value"] == "13000001" and f["dpp"]["confidence"] < 0.6
    assert {"code": "EFAKTUR_MISMATCH", "fields": ["dpp"]}.items() <= next(
        i for i in body["validation"] if i["code"] == "EFAKTUR_MISMATCH").items()
    assert body["efaktur"]["status"] == "Faktur Pajak Normal"


def test_efaktur_qr_cannot_override_printed_npwp(invoice, monkeypatch):
    """QR dikendalikan pembuat dokumen: NPWP di QR yang berbeda dengan NPWP tercetak tidak dipercaya 0,99."""
    from engine.documents.invoice.synth import SynthInvoice, make_pdf
    from engine.documents.worker import DocumentWorker

    _set_mode(monkeypatch, "decode")
    fake = QR.replace("012345678901000", "099999999999000")
    jid = invoice.a.post_job(make_pdf(SynthInvoice(qr_text=fake))).json()["job_id"]
    DocumentWorker(concurrency=0).run_until_empty()
    body = invoice.a.get(f"/v1/jobs/{jid}").json()
    assert body["fields"]["vendor_npwp"]["value"] == "012345678901000"
    assert body["fields"]["vendor_npwp"]["confidence"] < 0.6
    assert any(i["code"] == "EFAKTUR_MISMATCH" for i in body["validation"])


def test_efaktur_lookup_failure_falls_back(invoice, efaktur_pdf, monkeypatch):
    from engine.documents.worker import DocumentWorker

    _set_mode(monkeypatch, "lookup")
    real = httpx.Client

    def down(req):
        raise httpx.ConnectError("DJP tidak bisa dihubungi")

    monkeypatch.setattr("engine.documents.invoice.efaktur.httpx.Client",
                        lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(down)}))
    jid = invoice.a.post_job(efaktur_pdf).json()["job_id"]
    DocumentWorker(concurrency=0).run_until_empty()
    body = invoice.a.get(f"/v1/jobs/{jid}").json()
    assert body["status"] == "succeeded" and body["efaktur"]["lookup_error"] == "ConnectError"
    assert body["fields"]["vendor_npwp"]["source"] == "efaktur_qr" and body["fields"]["dpp"]["value"] == "13000001"


# ---------------------------------------------------------------------------------------------- temuan audit independen
def _pdf_rows(rows) -> Document:
    from fpdf import FPDF

    from engine.documents import sandbox
    from engine.documents.reader import Options

    p = FPDF(format="A4", unit="mm")
    p.add_page()
    p.set_auto_page_break(False)
    p.set_font("Helvetica", "", 10)
    for x, y, t in rows:
        p.set_xy(x, y)
        p.cell(0, 6, t)
    raw = sandbox.run(bytes(p.output()), Options(media_type="application/pdf"), memory_mb=1536, timeout_sec=60)
    return Document.from_read(raw)


@pytest.mark.parametrize("name,rows,expect", [
    ("buyer block on top", [(15, 10, "Kepada Yth."), (15, 16, "PT Pembeli Besar"),
                            (15, 22, "NPWP: 02.345.678.9-012.000"), (15, 40, "PT Penjual Kecil"),
                            (15, 46, "NPWP: 01.234.567.8-901.000"), (140, 40, "INVOICE")],
     {"vendor_npwp": "012345678901000", "vendor_name": "PT Penjual Kecil"}),
    ("16-digit VA is not NPWP", [(15, 10, "CV Kecil"), (120, 40, "Kepada: PT Pembeli"),
                                 (120, 46, "NPWP Pembeli: 02.345.678.9-012.000"),
                                 (15, 120, "Virtual Account BCA: 8808123456789012")],
     {"vendor_npwp": None}),
    ("PO date is not invoice date", [(15, 30, "Tanggal PO: 01/09/2026"), (15, 36, "No. Invoice: INV-1"),
                                     (15, 42, "Tanggal: 15/10/2026")], {"invoice_date": "2026-10-15"}),
    ("date-like invoice number", [(15, 30, "No. Invoice: INV-15-10-26"), (15, 36, "PO No: PO-998877")],
     {"invoice_number": "INV-15-10-26"}),
    ("discount percent without amount", [(120, 72, "Subtotal 1.000.000"), (120, 78, "Diskon 10%"),
                                         (120, 84, "DPP 900.000")], {"discount": None}),
    ("phone after account label", [(15, 40, "No. Rekening: a.n. PT Penjual, hubungi 021-5551234"),
                                   (15, 46, "BCA 123-456-7890")], {"bank_account": None}),
    ("balance due 0 after payment", [(120, 80, "Subtotal 1.000.000"), (120, 86, "VAT 11% 110.000"),
                                     (120, 92, "Total 1.110.000"), (120, 98, "Amount Paid 1.110.000"),
                                     (120, 104, "Balance Due 0")], {"total": "1110000"}),
    ("total VAT row last", [(120, 80, "Subtotal 1.000.000"), (120, 86, "Total 1.110.000"),
                            (120, 92, "Total VAT 110.000")], {"total": "1110000"}),
    ("malformed thousands", [(120, 80, "Subtotal: 1.234.56")], {"subtotal": "1234.56"}),
    ("Nop month", [(15, 36, "Due Date: 14 Nop 2026")], {"due_date": "2026-11-14"}),
])
def test_layouts_never_return_wrong_value(name, rows, expect):
    """Lebih baik field kosong daripada salah dengan confidence tinggi (temuan H5 audit)."""
    from engine.documents.invoice.extract import InvoiceExtractor

    fields = InvoiceExtractor().extract(_pdf_rows(rows), None, None)
    for k, v in expect.items():
        assert (fields[k].value if k in fields else None) == v, (name, k)


def test_extraction_is_not_quadratic():
    """PDF kecil berisi ribuan label "Total" tidak boleh menahan worker (temuan H3 audit: 8k blok = 34 dtk)."""
    import time

    from engine.documents.invoice.extract import InvoiceExtractor

    lines = [{"text": "Total", "bbox": [(i % 1000) / 1000, 0.5, (i % 1000) / 1000 + 0.0005, 0.51], "conf": 0.99}
             for i in range(20_000)]
    doc = Document.from_read({"pages": [{"index": 0, "source": "pdf_text", "lines": lines}]})
    assert doc.truncated and len(doc.blocks) == 4000
    t0 = time.perf_counter()
    InvoiceExtractor().extract(doc, None, None)
    assert time.perf_counter() - t0 < 2.0


def test_template_anchor_is_a_real_label(invoice_docs):
    """Sel "Rp" di sebelah nilai bukan anchor (temuan H1: template memilih baris uang yang salah)."""
    from engine.documents import sandbox
    from engine.documents.invoice.extract import InvoiceExtractor
    from engine.documents.invoice.synth import Line, SynthInvoice, make_pdf
    from engine.documents.reader import Options
    from engine.documents.templates import learn_rule

    def read(pdf):
        return Document.from_read(sandbox.run(pdf, Options(media_type="application/pdf"), memory_mb=1536,
                                              timeout_sec=60))

    first = read(invoice_docs["custom_pdf"])
    from engine.documents.invoice.extract import FIELD_KINDS

    rules = {f: learn_rule(first, FIELD_KINDS[f], v)[0] for f, v in (("total", "14430000"), ("wht_amount", "260000"),
                                                        ("vat_amount", "1430000"))}
    assert rules["total"]["anchor"] == "wajib dibayar" and rules["wht_amount"]["anchor"] == "pph"
    lines = [Line(f"Item {k}", "1", "1.000.000", "1.000.000") for k in range(5)]  # layout bergeser 2 baris
    other = SynthInvoice(layout="custom", lines=lines, subtotal="5.000.000", discount="", dpp="5.000.000",
                         vat="550.000", wht="100.000", total="5.550.000")
    f = InvoiceExtractor().extract(read(make_pdf(other)), {"rules": rules}, None)
    assert (f["total"].value, f["vat_amount"].value, f["wht_amount"].value) == ("5550000", "550000", "100000")
