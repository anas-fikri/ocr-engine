"""Golden test ekstraksi invoice sintetis (tanpa dokumen nyata): PDF digital (layer 1), scan miring/bernoise/berbayang
(layer 3), label bahasa Inggris, dan template issuer (layer 4)."""

from __future__ import annotations

import pytest

from engine.invoice import sandbox
from engine.invoice.document import Document
from engine.invoice.extract import InvoiceExtractor
from engine.invoice.reader import Options, detect_media_type
from engine.invoice.templates import learn_rule, normalize_value

STANDARD = {
    "vendor_npwp": "012345678901000", "vendor_name": "PT Contoh Sinar Abadi", "bank_account": "1234567890",
    "invoice_number": "INV/2026/10/0042", "invoice_date": "2026-10-15", "due_date": "2026-11-14",
    "period_start": "2026-10-01", "period_end": "2026-10-31", "subtotal": "14000000", "discount": "1000000",
    "dpp": "13000000", "vat_amount": "1430000", "wht_amount": "260000", "total": "14430000",
    "lines": [
        {"description": "Sewa server bulanan", "quantity": "1", "unit_price": "10000000", "amount": "10000000"},
        {"description": "Lisensi aplikasi", "quantity": "5", "unit_price": "500000", "amount": "2500000"},
        {"description": "Dukungan teknis", "quantity": "2", "unit_price": "750000", "amount": "1500000"},
    ],
}
ENGLISH = {
    "vendor_npwp": "012345678901000", "vendor_name": "Contoh Global Ltd", "bank_account": "0987654321",
    "invoice_number": "CG-2026-0007", "invoice_date": "2026-10-15", "due_date": "2026-11-14",
    "period_start": "2026-10-01", "period_end": "2026-10-31", "currency": "IDR", "subtotal": "2500001",
    "discount": "1", "dpp": "2500000", "vat_amount": "275000", "wht_amount": "50000", "total": "2775000",
    "lines": [{"description": "Cloud hosting", "quantity": "2", "unit_price": "1250000.50", "amount": "2500001"}],
}


def read(data: bytes) -> Document:
    raw = sandbox.run(data, Options(media_type=detect_media_type(data)), memory_mb=1536, timeout_sec=120)
    return Document.from_read(raw)


def extract(doc: Document, template=None, hint=None) -> dict:
    ex = InvoiceExtractor()
    fields = ex.extract(doc, template, hint)
    issues = ex.validate(fields)
    return {"fields": fields, "issues": issues}


def check(fields: dict, expected: dict) -> None:
    got = {k: fields[k].value for k in expected if k in fields}
    assert got == expected
    for k, f in fields.items():
        assert 0 <= f.confidence <= 1, k


def test_digital_pdf_golden(invoice_docs):
    """AC-03: PDF digital dibaca tanpa OCR, semua field benar."""
    doc = read(invoice_docs["pdf"])
    assert {p["source"] for p in doc.pages} == {"pdf_text"}
    out = extract(doc)
    check(out["fields"], STANDARD)
    assert out["issues"] == []
    assert all(out["fields"][k].confidence >= 0.8 for k in ("total", "dpp", "invoice_number", "invoice_date"))


@pytest.mark.parametrize("variant", ["jpg", "shaded"])
def test_scanned_image_golden(invoice_docs, variant):
    """AC-05: scan miring + noise (+ bayangan foto HP) → deskew, ratakan, binarize, Tesseract ind+eng."""
    from engine.invoice.synth import scan_of

    data = invoice_docs["jpg"] if variant == "jpg" else scan_of(invoice_docs["pdf"], angle=-4, shade=True, seed=3)
    doc = read(data)
    assert doc.pages[0]["source"] == "ocr" and doc.pages[0]["deskew_deg"] != 0
    out = extract(doc)
    check(out["fields"], STANDARD)
    assert out["issues"] == []


@pytest.mark.parametrize("doc_key,layer", [("english_pdf", "pdf_text"), ("english_png", "ocr")])
def test_english_invoice_golden(invoice_docs, doc_key, layer):
    doc = read(invoice_docs[doc_key])
    assert doc.pages[0]["source"] == layer
    out = extract(doc)
    check(out["fields"], ENGLISH)
    assert out["issues"] == []


def test_template_learning_fixes_field(invoice_docs):
    """AC-06: layout tidak umum → koreksi sekali → invoice berikutnya (PDF dan scan) terbaca via template."""
    first = read(invoice_docs["custom_pdf"])
    generic = extract(first)["fields"]
    missing = {"invoice_number": "INV/2026/10/0042", "due_date": "2026-11-14", "total": "14430000",
               "bank_account": "1234567890", "invoice_date": "2026-10-15"}  # "Tgl. Cetak" bukan label tanggal invoice
    assert not set(missing) & set(generic)
    rules = {}
    for field, value in missing.items():
        rule, reason = learn_rule(first, field, normalize_value(field, value))
        assert rule is not None, (field, reason)
        assert rule["anchor"]  # label dokumen ("ref dokumen", "batas bayar", ...) menjadi anchor
        rules[field] = rule
    template = {"issuer_npwp": "012345678901000", "version": 2, "rules": rules}
    expected = {"invoice_number": "INV/2026/11/0107", "due_date": "2026-12-16", "total": "22200000",
                "bank_account": "1234567890", "dpp": "20000000", "vat_amount": "2200000", "wht_amount": "400000",
                "invoice_date": "2026-11-16", "period_start": "2026-11-01", "period_end": "2026-11-30"}
    for key in ("custom2_pdf", "custom2_jpg"):
        out = extract(read(invoice_docs[key]), template, "012345678901000")
        check(out["fields"], expected)
        assert all(out["fields"][f].method == "template" for f in missing), key
        assert out["issues"] == [], key
