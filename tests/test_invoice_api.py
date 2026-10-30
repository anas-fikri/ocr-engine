"""Modul invoice lewat API: kontrak ICTNotes (TBD-OCR-1..9), template + idempotency, callback, retensi, health."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

import httpx
import pytest

from engine.invoice.worker import InvoiceWorker

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONEY = re.compile(r"^-?\d+(\.\d{1,2})?$")


def work() -> int:
    return InvoiceWorker(concurrency=0).run_until_empty()


def corr_key(job_id: str, field: str, extracted, corrected) -> str:
    """Rumus idempotency_key per koreksi milik klien ICTNotes."""
    raw = f"{job_id}\n{field}\n{json.dumps(extracted)}\n{json.dumps(corrected)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def request_key(keys: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(keys)).encode()).hexdigest()


def assert_contract(body: dict) -> None:
    """Bentuk respons yang diasumsikan klien ICTNotes."""
    assert set(body) >= {"job_id", "status", "layer", "text", "blocks", "fields", "engine"}
    assert body["status"] == "succeeded" and body["layer"] in ("pdf_text", "ocr")
    assert isinstance(body["engine"], dict) and all(isinstance(v, str) for v in body["engine"].values())
    for b in body["blocks"]:
        assert len(b["bbox"]) == 4 and all(0 <= v <= 1 for v in b["bbox"]) and 0 <= b["confidence"] <= 1
    for name, f in body["fields"].items():
        assert set(f) >= {"value", "confidence"} and 0 <= f["confidence"] <= 1, name
        v = f["value"]
        assert not isinstance(v, float), name  # uang tidak pernah float
        if name in ("invoice_date", "due_date", "period_start", "period_end"):
            assert DATE.match(v), (name, v)
        if name in ("subtotal", "discount", "dpp", "vat_amount", "wht_amount", "total"):
            assert isinstance(v, int) or MONEY.match(v), (name, v)
        if name in ("vendor_npwp", "bank_account"):
            assert v.isdigit(), (name, v)
    for ln in body["fields"].get("lines", {}).get("value", []):
        assert set(ln) == {"description", "quantity", "unit_price", "amount"}
    assert len(json.dumps(body).encode()) < 1024 * 1024


def test_create_job_and_poll(invoice, invoice_docs):
    """AC-01, AC-02, AC-14: job async, polling, bentuk hasil sesuai kontrak."""
    r = invoice.a.post_job(invoice_docs["pdf"], document_type="invoice", template_hint="01.234.567.8-901.000")
    assert r.status_code == 202, r.text
    jid = r.json()["job_id"]
    assert r.json()["status"] == "queued"
    q = invoice.a.get(f"/v1/jobs/{jid}")
    assert q.status_code == 200 and q.json()["status"] == "queued" and "fields" not in q.json()
    assert work() == 1
    body = invoice.a.get(f"/v1/jobs/{jid}").json()
    assert_contract(body)
    f = body["fields"]
    assert body["layer"] == "pdf_text"
    assert f["vendor_npwp"]["value"] == "012345678901000" and f["vendor_npwp"]["method"] == "hint"
    assert f["total"]["value"] == "14430000" and f["invoice_number"]["value"] == "INV/2026/10/0042"
    assert "INV/2026/10/0042" in body["text"]
    assert body["engine"]["pqe"] and body["engine"]["tesseract"] and body["engine"]["pdfium"]


def test_job_validation_errors(invoice, invoice_docs):
    assert invoice.a.post_job(invoice_docs["pdf"], document_type="kontrak").json()["error"]["code"] == \
        "UNSUPPORTED_DOCUMENT_TYPE"
    r = invoice.a.post_job(invoice_docs["pdf"], template_hint="12345")
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_TEMPLATE_HINT"
    r = invoice.a.post_job(b"", filename="kosong.pdf")
    assert r.status_code == 422 and r.json()["error"]["code"] == "EMPTY_FILE"
    assert invoice.a.get("/v1/jobs/" + "0" * 32).status_code == 404
    assert invoice.a.get("/v1/jobs/bukan-id").json()["error"]["code"] == "JOB_NOT_FOUND"


def test_failed_job_reports_error_code(invoice, monkeypatch):
    """PDF rusak → job failed dengan error.code (tidak di-retry: error permanen)."""
    r = invoice.a.post_job(b"%PDF-1.7\nini bukan pdf yang valid", filename="rusak.pdf")
    jid = r.json()["job_id"]
    work()
    body = invoice.a.get(f"/v1/jobs/{jid}").json()
    assert body["status"] == "failed" and body["error"]["code"] == "UNREADABLE_FILE"
    assert "fields" not in body


def _learn_custom(invoice, docs) -> tuple[str, dict]:
    """Invoice layout tidak umum: aturan umum tidak menemukan nomor/total/jatuh tempo/rekening → koreksi pengguna."""
    jid = invoice.a.post_job(docs["custom_pdf"], template_hint="012345678901000").json()["job_id"]
    work()
    body = invoice.a.get(f"/v1/jobs/{jid}").json()
    for missing in ("invoice_number", "due_date", "total", "bank_account"):
        assert missing not in body["fields"], missing
    corrections = []
    for field, value in (("invoice_number", "INV/2026/10/0042"), ("due_date", "2026-11-14"), ("total", "14430000"),
                         ("bank_account", "1234567890"), ("invoice_date", "2026-10-15")):
        extracted = body["fields"].get(field, {}).get("value")
        corrections.append({"field": field, "extracted": extracted, "corrected": value,
                            "idempotency_key": corr_key(jid, field, extracted, value)})
    payload = {"document_type": "invoice", "issuer": {"npwp": "012345678901000"}, "job_id": jid,
               "corrections": corrections}
    return jid, payload


def test_template_learning_and_reuse(invoice, invoice_docs):
    """AC-06, AC-07: koreksi → template issuer → invoice berikutnya dari vendor yang sama terbaca lewat template."""
    _, payload = _learn_custom(invoice, invoice_docs)
    rk = request_key([c["idempotency_key"] for c in payload["corrections"]])
    r = invoice.a.post_json("/v1/templates", payload, {"Idempotency-Key": rk})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["template"]["issuer"]["npwp"] == "012345678901000" and out["template"]["version"] == 2
    assert all(c["status"] == "applied" and c["learned"] for c in out["corrections"]), out["corrections"]
    assert set(out["template"]["fields"]) >= {"invoice_number", "due_date", "total", "bank_account"}

    # invoice lain dari vendor yang sama (nilai berbeda), PDF digital dan hasil scan
    for doc, layer in (("custom2_pdf", "pdf_text"), ("custom2_jpg", "ocr")):
        jid = invoice.a.post_job(invoice_docs[doc], template_hint="012345678901000").json()["job_id"]
        work()
        body = invoice.a.get(f"/v1/jobs/{jid}").json()
        assert_contract(body)
        f = body["fields"]
        assert body["layer"] == layer and "template" in body["layers"], doc
        assert body["template"] == {"issuer_npwp": "012345678901000", "version": 2}
        assert f["invoice_number"]["value"] == "INV/2026/11/0107", (doc, f["invoice_number"])
        assert f["due_date"]["value"] == "2026-12-16", doc
        assert f["total"]["value"] == "22200000", doc
        assert f["bank_account"]["value"] == "1234567890", doc
        assert f["invoice_number"]["method"] == "template" and f["invoice_number"]["confidence"] >= 0.7


def test_template_without_hint_uses_detected_npwp(invoice, invoice_docs):
    _, payload = _learn_custom(invoice, invoice_docs)
    assert invoice.a.post_json("/v1/templates", payload).status_code == 200
    jid = invoice.a.post_job(invoice_docs["custom2_pdf"]).json()["job_id"]  # tanpa template_hint
    work()
    body = invoice.a.get(f"/v1/jobs/{jid}").json()
    assert "template" in body["layers"] and body["fields"]["total"]["value"] == "22200000"


def test_template_idempotency(invoice, invoice_docs):
    """Key per koreksi diterapkan paling banyak sekali; Idempotency-Key request diulang → respons sama."""
    _, payload = _learn_custom(invoice, invoice_docs)
    rk = request_key([c["idempotency_key"] for c in payload["corrections"]])
    first = invoice.a.post_json("/v1/templates", payload, {"Idempotency-Key": rk})
    again = invoice.a.post_json("/v1/templates", payload, {"Idempotency-Key": rk})
    assert again.status_code == 200 and again.json() == first.json()
    assert again.headers.get("Idempotent-Replayed") == "true"
    # body lain dengan Idempotency-Key yang sama → ditolak
    other = dict(payload, corrections=payload["corrections"][:1])
    r = invoice.a.post_json("/v1/templates", other, {"Idempotency-Key": rk})
    assert r.status_code == 422 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    # tanpa header: koreksi yang sama tidak diterapkan ulang, versi template tidak naik
    r = invoice.a.post_json("/v1/templates", payload)
    assert [c["status"] for c in r.json()["corrections"]] == ["duplicate"] * len(payload["corrections"])
    assert r.json()["template"]["version"] == first.json()["template"]["version"]


def test_template_validation(invoice, invoice_docs):
    jid, payload = _learn_custom(invoice, invoice_docs)
    bad = dict(payload, corrections=[dict(payload["corrections"][0], field="tidak_ada")])
    assert invoice.a.post_json("/v1/templates", bad).json()["error"]["code"] == "UNKNOWN_FIELD"
    bad = dict(payload, corrections=[dict(payload["corrections"][1], corrected="bukan tanggal")])
    assert invoice.a.post_json("/v1/templates", bad).json()["error"]["code"] == "INVALID_VALUE"
    bad = dict(payload, issuer={"npwp": "123"})
    assert invoice.a.post_json("/v1/templates", bad).json()["error"]["code"] == "INVALID_NPWP"
    bad = dict(payload, job_id="f" * 32)
    assert invoice.a.post_json("/v1/templates", bad).status_code == 404
    r = invoice.a.post_json("/v1/templates", {"issuer": {}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_template_value_not_in_document(invoice, invoice_docs):
    jid, payload = _learn_custom(invoice, invoice_docs)
    c = {"field": "invoice_number", "extracted": None, "corrected": "TIDAK-ADA-999",
         "idempotency_key": corr_key(jid, "invoice_number", None, "TIDAK-ADA-999")}
    r = invoice.a.post_json("/v1/templates", dict(payload, corrections=[c])).json()
    assert r["corrections"][0]["learned"] is False
    assert r["corrections"][0]["reason"] == "VALUE_NOT_FOUND_IN_DOCUMENT"
    assert r["template"]["version"] == 1  # tidak ada aturan baru


def test_get_template(invoice, invoice_docs):
    _, payload = _learn_custom(invoice, invoice_docs)
    invoice.a.post_json("/v1/templates", payload)
    r = invoice.a.get("/v1/templates/invoice/012345678901000")
    assert r.status_code == 200 and "total" in r.json()["rules"]
    assert invoice.b.get("/v1/templates/invoice/012345678901000").status_code == 404


def test_callback_signed(invoice, invoice_docs, monkeypatch):
    """AC-09: callback ber-HMAC bila didaftarkan; host di luar allowlist ditolak."""
    from engine import settings
    from engine.invoice import signing

    r = invoice.a.post_job(invoice_docs["pdf"], callback_url="https://ictnotes.test/ocr/callback")
    assert r.status_code == 422 and r.json()["error"]["code"] == "CALLBACK_NOT_ALLOWED"
    monkeypatch.setenv("PQE_INVOICE_CALLBACK_HOSTS", "ictnotes.test")
    settings.reset_settings_cache()
    for bad in ("https://evil.test/x", "https://user:pw@ictnotes.test/x", "file:///etc/passwd",
                "http://ictnotes.test:99999/x"):
        assert invoice.a.post_job(invoice_docs["pdf"], callback_url=bad).status_code == 422, bad
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200)

    real = httpx.Client
    monkeypatch.setattr("engine.invoice.worker.httpx.Client",
                        lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(handler)}))
    jid = invoice.a.post_job(invoice_docs["pdf"], callback_url="https://ictnotes.test/ocr/callback").json()["job_id"]
    work()
    assert len(calls) == 1
    req = calls[0]
    body = req.read()
    assert json.loads(body) == {"event": "job.finished", "job_id": jid, "status": "succeeded",
                                "document_type": "invoice"}
    expected = signing.sign(invoice.a.secret, req.headers["X-Timestamp"], "POST", "/ocr/callback", body)
    assert req.headers["X-Signature"] == expected


def test_inbox_retention(invoice, invoice_docs):
    """AC-10: file inbox lebih tua dari retensi dihapus; hasil job tetap; job yang filenya hilang gagal jelas."""
    from engine.db import session_scope, utcnow
    from engine.invoice import store
    from engine.invoice.models import InvoiceFile
    from engine.invoice.worker import retention

    done = invoice.a.post_job(invoice_docs["pdf"]).json()["job_id"]
    work()
    late = invoice.a.post_job(invoice_docs["pdf"]).json()["job_id"]
    with session_scope() as s:
        for f in s.query(InvoiceFile).all():
            f.created_at = utcnow() - timedelta(days=31)
    assert retention()["files"] == 2
    assert store.get(done) is None and store.get(late) is None
    assert invoice.a.get(f"/v1/jobs/{done}").json()["fields"]["total"]["value"] == "14430000"
    work()
    body = invoice.a.get(f"/v1/jobs/{late}").json()
    assert body["status"] == "failed" and body["error"]["code"] == "FILE_EXPIRED"


def test_healthz_versions(client):
    """AC-11: versi engine + komponen OCR."""
    h = client.get("/healthz").json()
    assert h["status"] == "ok" and h["invoice"]["enabled"] is True and h["invoice"]["efaktur_qr"] == "off"
    assert h["engines"]["pqe"] == h["version"] and h["engines"]["tesserocr"] and h["engines"]["pdfium"]


def test_engine_versions_do_not_import_tesserocr_in_threads():
    """/healthz dijalankan di thread: tidak boleh meng-import tesserocr (cysignals gagal di thread non-main dan import
    yang gagal mengubah jalur OCR foto ke CLI)."""
    import subprocess
    import sys

    code = ("import sys, threading\nfrom engine.invoice.process import engine_versions\n"
            "t = threading.Thread(target=engine_versions); t.start(); t.join()\n"
            "print('tesserocr' in sys.modules, sorted(engine_versions()))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.startswith("False") and "pdfium" in out


def test_photo_endpoints_unchanged_by_invoice_keys(invoice, admin_headers):
    """API key invoice tetap API key biasa: endpoint foto tidak butuh tanda tangan, admin tidak butuh secret."""
    assert invoice.client.get("/v1/me", headers={"X-API-Key": invoice.a.key}).json()["tenant"] == "test-app"
    assert invoice.client.get("/v1/me", headers=admin_headers).status_code == 200


@pytest.mark.parametrize("disabled", [True])
def test_invoice_module_can_be_disabled(env, monkeypatch, disabled):
    from fastapi.testclient import TestClient

    from engine import settings
    from engine.api.app import create_app

    monkeypatch.setenv("PQE_INVOICE_ENABLED", "false")
    settings.reset_settings_cache()
    with TestClient(create_app(run_bootstrap=False)) as c:
        assert c.post("/v1/jobs").status_code == 404
        assert c.get("/healthz").json()["invoice"]["enabled"] is False
