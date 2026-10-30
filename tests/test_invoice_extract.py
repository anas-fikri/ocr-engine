"""Unit test normalisasi nilai, plugin jenis dokumen, dan layer 2 (QR e-Faktur)."""

from __future__ import annotations

import httpx
import pytest

from engine.invoice import parsing as P
from engine.invoice.document import Document


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
    from engine.invoice.extract import InvoiceExtractor

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
    from engine.invoice.extract import InvoiceExtractor

    doc = _doc([("Kepada: PT Pembeli", [0.1, 0.05, 0.4, 0.07]),
                ("NPWP Pembeli: 02.345.678.9-012.000", [0.1, 0.08, 0.5, 0.1]),
                ("NPWP: 01.234.567.8-901.000", [0.1, 0.3, 0.5, 0.32])])
    assert InvoiceExtractor().extract(doc, None, None)["vendor_npwp"].value == "012345678901000"


class ReceiptExtractor:
    """Contoh plugin jenis dokumen baru (dimuat lewat PQE_DOCUMENT_PLUGINS)."""


def _receipt_plugin():
    from engine.invoice.plugins import DocumentExtractor, Field

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
    from engine.invoice import plugins
    from engine.invoice.synth import make_pdf
    from engine.invoice.worker import InvoiceWorker

    mod = types.ModuleType("pqe_test_receipt_plugin")
    mod.Receipt = _receipt_plugin()
    monkeypatch.setitem(sys.modules, "pqe_test_receipt_plugin", mod)
    monkeypatch.setenv("PQE_DOCUMENT_PLUGINS", "pqe_test_receipt_plugin:Receipt, modul.tidak.ada:X")
    settings.reset_settings_cache()
    plugins.registry.cache_clear()
    assert set(plugins.registry()) == {"invoice", "receipt"}  # plugin rusak dilewati, tidak menjatuhkan engine
    r = invoice.a.post_job(make_pdf(), document_type="receipt")
    assert r.status_code == 202
    InvoiceWorker(concurrency=0).run_until_empty()
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
    from engine.invoice.synth import SynthInvoice, make_pdf

    # QR berisi NPWP benar, teks invoice sengaja salah cetak DPP agar terlihat QR yang dipakai
    return make_pdf(SynthInvoice(qr_text=QR, dpp="13.000.001"))


def _set_mode(monkeypatch, mode: str) -> None:
    from engine import settings

    monkeypatch.setenv("PQE_INVOICE_EFAKTUR_QR", mode)
    settings.reset_settings_cache()


def test_efaktur_off_by_default(invoice, efaktur_pdf):
    from engine.invoice.worker import InvoiceWorker

    jid = invoice.a.post_job(efaktur_pdf).json()["job_id"]
    InvoiceWorker(concurrency=0).run_until_empty()
    body = invoice.a.get(f"/v1/jobs/{jid}").json()
    assert "efaktur_qr" not in body["layers"] and "efaktur" not in body
    assert body["fields"]["dpp"]["value"] == "13000001"


def test_efaktur_decode_mode(invoice, efaktur_pdf, monkeypatch):
    from engine.invoice.worker import InvoiceWorker

    _set_mode(monkeypatch, "decode")
    monkeypatch.setattr("engine.invoice.efaktur.httpx.Client", lambda *a, **k: pytest.fail("decode tanpa jaringan"))
    jid = invoice.a.post_job(efaktur_pdf).json()["job_id"]
    InvoiceWorker(concurrency=0).run_until_empty()
    body = invoice.a.get(f"/v1/jobs/{jid}").json()
    assert "efaktur_qr" in body["layers"] and body["efaktur"]["nomor_faktur"] == "0102612345678"
    assert body["fields"]["vendor_npwp"] == {"value": "012345678901000", "confidence": 0.99,
                                             "source": "efaktur_qr", "method": "qr"}


def test_efaktur_lookup_mode(invoice, efaktur_pdf, monkeypatch):
    from engine.invoice.worker import InvoiceWorker

    _set_mode(monkeypatch, "lookup")
    seen = []

    def djp(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(200, content=DJP_XML)

    real = httpx.Client
    monkeypatch.setattr("engine.invoice.efaktur.httpx.Client",
                        lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(djp)}))
    jid = invoice.a.post_job(efaktur_pdf).json()["job_id"]
    InvoiceWorker(concurrency=0).run_until_empty()
    body = invoice.a.get(f"/v1/jobs/{jid}").json()
    f = body["fields"]
    assert seen == [QR.replace("http://", "https://")]  # selalu HTTPS
    assert f["dpp"]["value"] == "13000000" and f["dpp"]["source"] == "efaktur_qr"
    assert f["vat_amount"]["value"] == "1430000" and f["invoice_date"]["value"] == "2026-10-15"
    assert f["vendor_name"]["value"] == "PT CONTOH SINAR ABADI"
    assert body["efaktur"]["status"] == "Faktur Pajak Normal"


def test_efaktur_lookup_failure_falls_back(invoice, efaktur_pdf, monkeypatch):
    from engine.invoice.worker import InvoiceWorker

    _set_mode(monkeypatch, "lookup")
    real = httpx.Client

    def down(req):
        raise httpx.ConnectError("DJP tidak bisa dihubungi")

    monkeypatch.setattr("engine.invoice.efaktur.httpx.Client",
                        lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(down)}))
    jid = invoice.a.post_job(efaktur_pdf).json()["job_id"]
    InvoiceWorker(concurrency=0).run_until_empty()
    body = invoice.a.get(f"/v1/jobs/{jid}").json()
    assert body["status"] == "succeeded" and body["efaktur"]["lookup_error"] == "ConnectError"
    assert body["fields"]["vendor_npwp"]["source"] == "efaktur_qr" and body["fields"]["dpp"]["value"] == "13000001"
