"""Abuse case modul invoice (docs/invoice-ocr-design.md bagian 5.2)."""

from __future__ import annotations

import io
import struct
import time
import zlib

import pytest

from engine.invoice.reader import Options
from engine.invoice.worker import InvoiceWorker


def work() -> int:
    return InvoiceWorker(concurrency=0).run_until_empty()


# ---------------------------------------------------------------------------------------------- A1-A3 tanda tangan
def test_bad_signature_rejected(invoice, invoice_docs):
    other = type(invoice.a)(invoice.client, invoice.a.key, "secret-yang-salah")
    r = other.post_job(invoice_docs["pdf"])
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_SIGNATURE"
    assert other.get("/v1/jobs/" + "a" * 32).json()["error"]["code"] == "INVALID_SIGNATURE"


def test_missing_headers_and_unknown_key(invoice):
    c = invoice.client
    assert c.get("/v1/jobs/" + "a" * 32).json()["error"]["code"] == "UNAUTHORIZED"
    r = c.get("/v1/jobs/" + "a" * 32, headers={"X-Api-Key": invoice.a.key})
    assert r.status_code == 401 and r.json()["error"]["code"] == "SIGNATURE_REQUIRED"
    fake = type(invoice.a)(c, "pqe_tidak_ada", invoice.a.secret)
    assert fake.get("/v1/jobs/" + "a" * 32).json()["error"]["code"] == "UNAUTHORIZED"


def test_tampered_body_rejected(invoice, invoice_docs):
    import httpx

    req = httpx.Request("POST", "http://t/v1/jobs", files={"file": ("a.pdf", invoice_docs["pdf"])},
                        data={"document_type": "invoice"})
    body = req.read()
    h = {"Content-Type": req.headers["content-type"], **invoice.a.headers("POST", "/v1/jobs", body)}
    tampered = body.replace(b"invoice", b"invoicX", 1)
    r = invoice.client.post("/v1/jobs", content=tampered, headers=h)
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_SIGNATURE"
    # path lain dengan tanda tangan yang sama juga ditolak (path ikut ditandatangani)
    h2 = invoice.a.headers("GET", "/v1/jobs/" + "a" * 32, b"")
    assert invoice.client.get("/v1/jobs/" + "b" * 32, headers=h2).json()["error"]["code"] == "INVALID_SIGNATURE"


def test_old_timestamp_rejected(invoice):
    for ts in (int(time.time()) - 301, int(time.time()) + 301):
        path = "/v1/jobs/" + "a" * 32
        r = invoice.client.get(path, headers=invoice.a.headers("GET", path, b"", ts=ts))
        assert r.status_code == 401 and r.json()["error"]["code"] == "TIMESTAMP_OUT_OF_WINDOW"
    path = "/v1/jobs/" + "a" * 32
    h = {**invoice.a.headers("GET", path, b""), "X-Timestamp": "kemarin"}
    assert invoice.client.get(path, headers=h).json()["error"]["code"] == "TIMESTAMP_OUT_OF_WINDOW"


def test_replay_rejected(invoice, invoice_docs):
    """Request POST yang sama persis diulang (replay / retry) tidak membuat job baru: job yang sama dikembalikan."""
    import httpx

    from engine.db import session_scope
    from engine.invoice.models import InvoiceJob

    req = httpx.Request("POST", "http://t/v1/jobs", files={"file": ("a.pdf", invoice_docs["pdf"])},
                        data={"document_type": "invoice"})
    body = req.read()
    h = {"Content-Type": req.headers["content-type"], **invoice.a.headers("POST", "/v1/jobs", body)}
    first = invoice.client.post("/v1/jobs", content=body, headers=h)
    again = invoice.client.post("/v1/jobs", content=body, headers=h)
    assert first.status_code == again.status_code == 202
    assert again.json()["job_id"] == first.json()["job_id"] and again.headers["Idempotent-Replayed"] == "true"
    with session_scope() as s:
        assert s.query(InvoiceJob).count() == 1
    # tanda tangan yang sama dari namespace lain tidak bisa dipakai (key lain → secret lain → tanda tangan lain)
    h_b = {**h, "X-Api-Key": invoice.b.key}
    assert invoice.client.post("/v1/jobs", content=body, headers=h_b).status_code == 401


def test_key_without_secret_or_wrong_role(invoice, admin_headers):
    from engine.api.auth import create_api_key
    from engine.invoice.signing import set_api_secret

    raw, _ = create_api_key("tanpa-secret", "app", "test-app")
    c = type(invoice.a)(invoice.client, raw, "apa-saja")
    assert c.get("/v1/jobs/" + "a" * 32).json()["error"]["code"] == "SIGNATURE_NOT_ENABLED"
    raw_v, kv = create_api_key("viewer", "viewer", "test-app")
    v = type(invoice.a)(invoice.client, raw_v, set_api_secret(kv.id))
    r = v.get("/v1/jobs/" + "a" * 32)
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


def test_revoked_key_rejected(invoice, admin_headers):
    r = invoice.client.delete(f"/v1/admin/api-keys/{invoice.key_a.id}", headers=admin_headers)
    assert r.status_code in (200, 204), r.text
    assert invoice.a.get("/v1/jobs/" + "a" * 32).json()["error"]["code"] == "UNAUTHORIZED"


def test_signature_compare_is_timing_safe():
    """Perbandingan memakai hmac.compare_digest (bukan ==)."""
    import inspect

    from engine.invoice import signing

    src = inspect.getsource(signing.verify)
    assert "hmac.compare_digest(" in src and "expected ==" not in src and "== expected" not in src


# ---------------------------------------------------------------------------------------------- A4 namespace
def test_namespace_isolation_jobs(invoice, invoice_docs):
    jid = invoice.a.post_job(invoice_docs["pdf"]).json()["job_id"]
    work()
    assert invoice.a.get(f"/v1/jobs/{jid}").json()["status"] == "succeeded"
    r = invoice.b.get(f"/v1/jobs/{jid}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_namespace_isolation_templates(invoice, invoice_docs):
    """Namespace B tidak bisa memakai job A untuk melatih template, dan template A tidak dipakai untuk job B."""
    from tests.test_invoice_api import _learn_custom

    _, payload = _learn_custom(invoice, invoice_docs)
    r = invoice.b.post_json("/v1/templates", payload)
    assert r.status_code == 404 and r.json()["error"]["code"] == "JOB_NOT_FOUND"
    assert invoice.a.post_json("/v1/templates", payload).status_code == 200
    jid = invoice.b.post_job(invoice_docs["custom2_pdf"], template_hint="012345678901000").json()["job_id"]
    work()
    body = invoice.b.get(f"/v1/jobs/{jid}").json()
    assert "template" not in body["layers"] and body["template"] is None
    assert "total" not in body["fields"]  # template namespace A tidak bocor ke B


# ---------------------------------------------------------------------------------------------- A5, A8 file
@pytest.mark.parametrize("data,name", [
    (b"PK\x03\x04" + b"\x00" * 100, "invoice.pdf"),  # ZIP berganti nama
    (b"MZ\x90\x00" + b"\x00" * 100, "invoice.pdf"),  # EXE
    (b"<html><script>alert(1)</script></html>", "invoice.png"),
    (b"GIF89a" + b"\x00" * 50, "invoice.jpg"),
])
def test_unsupported_file_rejected(invoice, data, name):
    r = invoice.a.post_job(data, filename=name)
    assert r.status_code == 415 and r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_filename_is_not_a_path(invoice, invoice_docs, tmp_path, monkeypatch):
    from engine.db import session_scope
    from engine.invoice.models import InvoiceJob

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    r = invoice.a.post_job(invoice_docs["pdf"], filename="../../../../tmp/evil\x00\n.pdf")
    assert r.status_code == 202
    with session_scope() as s:
        job = s.get(InvoiceJob, r.json()["job_id"])
        assert "/" not in job.filename and "\x00" not in job.filename and "\n" not in job.filename
    assert list(cwd.iterdir()) == []  # file tidak pernah ditulis ke disk


# ---------------------------------------------------------------------------------------------- A6, A7 bom
def _pdf_with_pages(n: int) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    for i in range(n):
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, f"Halaman {i + 1} invoice total 1.000")
    return bytes(pdf.output())


def test_too_many_pages(invoice, monkeypatch):
    from engine import settings

    monkeypatch.setenv("PQE_INVOICE_MAX_PAGES", "2")
    settings.reset_settings_cache()
    jid = invoice.a.post_job(_pdf_with_pages(3)).json()["job_id"]
    work()
    body = invoice.a.get(f"/v1/jobs/{jid}").json()
    assert body["status"] == "failed" and body["error"]["code"] == "TOO_MANY_PAGES"


def test_sandbox_memory_bomb():
    """Batas memori proses anak berlaku: dokumen yang butuh memori lebih dari batas → RESOURCE_LIMIT, induk hidup."""
    from engine.invoice import sandbox

    with pytest.raises(sandbox.SandboxError) as e:
        sandbox.run(_pdf_with_pages(1), Options(media_type="application/pdf"), memory_mb=64, timeout_sec=60)
    assert e.value.code in ("RESOURCE_LIMIT", "PROCESSING_ERROR")


def test_sandbox_flate_bomb():
    """PDF bom: content stream kecil yang mengembang menjadi ±1 GB. Ditolak/dimatikan, tidak membuat worker jatuh."""
    from engine.invoice import sandbox

    payload = zlib.compress(b" " * (1024 * 1024 * 1024), 9)  # ±1 MB terkompres
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R >>",
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(payload) + payload + b"\nendstream"]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    t0 = time.monotonic()
    try:
        doc = sandbox.run(out.getvalue(), Options(media_type="application/pdf"), memory_mb=512, timeout_sec=60)
    except sandbox.SandboxError as e:  # dimatikan batas memori/waktu
        assert e.code in ("RESOURCE_LIMIT", "PROCESSING_TIMEOUT", "PROCESSING_ERROR")
    else:  # PDFium membatasi sendiri: halaman tanpa teks, tetap selesai
        assert doc["pages"][0]["lines"] == [] or doc["pages"]
    assert time.monotonic() - t0 < 90


def _png_header_only(w: int, h: int) -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" * 64)) + chunk(b"IEND", b""))


@pytest.mark.parametrize("w,h", [(60_000, 60_000), (9_000, 9_000)])
def test_image_pixel_bomb(w, h):
    """Dimensi dicek dari header sebelum decode (PNG 3,6 GP ditolak tanpa dialokasikan)."""
    from engine.invoice import sandbox

    with pytest.raises(sandbox.SandboxError) as e:
        sandbox.run(_png_header_only(w, h), Options(media_type="image/png", max_image_pixels=50_000_000),
                    memory_mb=1536, timeout_sec=60)
    assert e.value.code == "IMAGE_TOO_LARGE"


# ---------------------------------------------------------------------------------------------- A9 ReDoS
def test_shape_regex_is_linear():
    from engine.invoice import parsing as P
    from engine.invoice.extract import anchor_regex

    shape = P.shape_of("A" * 30 + "/" + "9" * 30)
    rx = P.shape_regex(shape)
    assert "+" not in rx.pattern and "*" not in rx.pattern  # hanya quantifier tetap {n}
    evil = ("A" * 29 + "/" + "9" * 29 + "!") * 20
    t0 = time.perf_counter()
    for _ in range(200):
        rx.search(P._cut(evil))
        anchor_regex("a " * 6).search(P._cut("a" * 5000))
        P.find_money("1" * 5000 + ",")
        P.find_dates("1/" * 3000)
        P.find_doc_numbers("A/" * 3000)
    assert time.perf_counter() - t0 < 2.0


def test_template_rules_contain_no_client_regex(invoice, invoice_docs):
    """Isi template berasal dari dokumen + jenis pola tetap, bukan dari teks koreksi klien."""
    from tests.test_invoice_api import _learn_custom, corr_key

    jid, payload = _learn_custom(invoice, invoice_docs)
    evil = "(a+)+$"
    c = {"field": "vendor_name", "extracted": None, "corrected": evil,
         "idempotency_key": corr_key(jid, "vendor_name", None, evil)}
    r = invoice.a.post_json("/v1/templates", dict(payload, corrections=[c])).json()
    assert r["corrections"][0]["learned"] is False
    rules = invoice.a.get("/v1/templates/invoice/012345678901000").json()["rules"]
    assert "vendor_name" not in rules and evil not in str(rules)


# ---------------------------------------------------------------------------------------------- A10, A11 DoS
def test_upload_too_large(invoice, monkeypatch):
    from engine import settings

    monkeypatch.setenv("PQE_INVOICE_MAX_UPLOAD_MB", "1")
    settings.reset_settings_cache()
    big = b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 100 * 1024)
    r = invoice.a.post_job(big)
    assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_queue_full(invoice, invoice_docs, monkeypatch):
    from engine import settings

    monkeypatch.setenv("PQE_INVOICE_MAX_PENDING_PER_TENANT", "1")
    settings.reset_settings_cache()
    assert invoice.a.post_job(invoice_docs["pdf"]).status_code == 202
    r = invoice.a.post_job(invoice_docs["jpg"], filename="b.jpg")
    assert r.status_code == 429 and r.json()["error"]["code"] == "QUEUE_FULL"
    assert invoice.b.post_job(invoice_docs["pdf"]).status_code == 202  # kuota per namespace


def test_response_size_capped():
    from engine.invoice.process import MAX_RESULT_BYTES, cap_result

    blocks = [{"page": 1, "text": "x" * 200, "bbox": [0, 0, 1, 1], "confidence": 0.9, "source": "ocr"}] * 20_000
    res = cap_result({"text": "y" * 3_000_000, "blocks": blocks, "fields": {"total": {"value": "1"}}})
    import json

    assert len(json.dumps(res).encode()) <= MAX_RESULT_BYTES and res["truncated"] is True
    assert res["fields"] == {"total": {"value": "1"}}


# ---------------------------------------------------------------------------------------------- A12-A14
def test_efaktur_url_allowlist():
    from engine.invoice.efaktur import parse_qr_url

    ok = parse_qr_url("http://svc.efaktur.pajak.go.id/validasi/faktur/012345678901000/0102612345678/abc123")
    assert ok == {"url": "https://svc.efaktur.pajak.go.id/validasi/faktur/012345678901000/0102612345678/abc123",
                  "npwp": "012345678901000", "nomor_faktur": "0102612345678"}
    for bad in ("http://evil.test/validasi/faktur/012345678901000/0102612345678/abc",
                "http://svc.efaktur.pajak.go.id.evil.test/validasi/faktur/012345678901000/0102612345678/a",
                "http://user@svc.efaktur.pajak.go.id/validasi/faktur/012345678901000/0102612345678/a",
                "http://svc.efaktur.pajak.go.id:8080/validasi/faktur/012345678901000/0102612345678/a",
                "http://svc.efaktur.pajak.go.id/validasi/faktur/012345678901000/0102612345678/a?x=1",
                "http://svc.efaktur.pajak.go.id/../admin", "javascript:alert(1)", "file:///etc/passwd"):
        assert parse_qr_url(bad) is None, bad


def test_efaktur_xml_entities_rejected():
    from engine.invoice.efaktur import _xml_fields

    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><r><npwpPenjual>&b;' \
           b'</npwpPenjual></r>'
    with pytest.raises(Exception):  # noqa: B017 - defusedxml: DTDForbidden / EntitiesForbidden
        _xml_fields(bomb)
    xxe = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
    with pytest.raises(Exception):  # noqa: B017
        _xml_fields(xxe)


def test_sandbox_env_has_no_secrets(monkeypatch):
    from engine.invoice.sandbox import child_env

    for k in ("DATABASE_URL", "AWS_SECRET_ACCESS_KEY", "PQE_SECRET_KEY", "PQE_BOOTSTRAP_ADMIN_KEY",
              "POSTGRES_PASSWORD"):
        monkeypatch.setenv(k, "rahasia")
    env = child_env()
    assert "rahasia" not in env.values()
    assert set(env) <= {"PATH", "HOME", "LANG", "LC_ALL", "TESSDATA_PREFIX", "PYTHONPATH", "OMP_THREAD_LIMIT",
                        "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED"}
