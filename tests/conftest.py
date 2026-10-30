"""Fixture test: SQLite sementara + S3 tiruan (moto server) agar test berjalan tanpa SILO/Postgres."""

from __future__ import annotations

import os
import socket

import pytest

TENANT_YAML = """
code: test-app
name: Test App
retention_days: 30
review_required: true
default_profile: pod_recipient
sources:
  - type: s3_poll
    bucket: app-photos
    prefix: "pod/"
    poll_interval_sec: 1
mapping:
  awb: ["meta:awb", "key:regex:(?P<awb>AWB\\\\d+)"]
  photo_type: ["meta:photo-type", "key:segment:1"]
  courier_id: ["meta:courier-id"]
sinks:
  - type: webhook
    url: http://hooks.test/photo
    secret: s3cr3t
    events: [analysis.completed, review.updated]
profiles:
  pod_recipient:
    mode: warn
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def s3_endpoint():
    from moto.server import ThreadedMotoServer

    port = _free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    server.start()
    yield f"http://127.0.0.1:{port}"
    server.stop()


@pytest.fixture()
def env(tmp_path, s3_endpoint, monkeypatch):
    """Lingkungan engine lengkap yang bersih untuk setiap test."""
    from engine import db, settings, storage, tenants
    from engine.processor import phash_index

    tenants_dir = tmp_path / "tenants"
    tenants_dir.mkdir()
    (tenants_dir / "test-app.yaml").write_text(TENANT_YAML)
    values = {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'pqe.db'}",
        "AWS_ENDPOINT": s3_endpoint,
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_BUCKET": "app-photos",
        "ENGINE_CREATE_BUCKETS": "true",
        "PQE_TENANTS_DIR": str(tenants_dir),
        "PQE_BOOTSTRAP_ADMIN_KEY": "pqe_admin_test_key",
        "PQE_S3_EVENT_TOKEN": "event-token",
        "PQE_JOB_MAX_ATTEMPTS": "2",
        "PUBLIC_BASE_URL": "http://pqe.test",
    }
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    settings.reset_settings_cache()
    db.reset_engine()
    storage.reset_storage_cache()
    tenants.registry.invalidate()
    tenants.registry._cache.clear()
    phash_index.reset()
    from engine.bootstrap import bootstrap

    bootstrap()
    st = storage.get_storage()  # bucket moto dipakai bersama antar test: kosongkan
    for b in ("app-photos", "ocr", "photos-processed", "photos-thumb", "photos-upload"):
        for o in list(st.list(b)):
            st.delete(b, o.key, allow_foreign=True)
    yield values
    from engine.processor import wait_for_archives

    wait_for_archives()  # arsip di latar belakang selesai sebelum database test dibuang
    db.reset_engine()
    settings.reset_settings_cache()
    storage.reset_storage_cache()
    os.environ.pop("PQE_BOOTSTRAP_ADMIN_KEY", None)


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from engine.api.app import create_app

    with TestClient(create_app(run_bootstrap=False)) as c:
        yield c


@pytest.fixture()
def admin_headers():
    return {"X-API-Key": "pqe_admin_test_key"}


@pytest.fixture(scope="session")
def samples():
    from engine.synth import variants

    return variants()


# ---------------------------------------------------------------------------------------------- modul invoice
OTHER_TENANT_YAML = """
code: other-app
name: Other App
sources: []
mapping: {}
sinks: []
profiles:
  default:
    mode: shadow
"""


class SignedClient:
    """Klien uji yang menandatangani request seperti client modul dokumen (X-Api-Key + X-Timestamp + X-Signature)."""

    def __init__(self, client, key: str, secret: str) -> None:
        self.c, self.key, self.secret = client, key, secret

    def headers(self, method: str, path: str, body: bytes, ts: int | None = None) -> dict[str, str]:
        import time

        from engine.documents.signing import sign

        ts_s = str(int(time.time()) if ts is None else ts)
        return {"X-Api-Key": self.key, "X-Timestamp": ts_s, "X-Signature": sign(self.secret, ts_s, method, path, body)}

    def post_job(self, data: bytes, filename: str = "invoice.pdf", document_type: str = "invoice", **fields):
        import httpx

        req = httpx.Request("POST", "http://t/v1/jobs", files={"file": (filename, data)},
                            data={"document_type": document_type, **fields})
        body = req.read()
        h = {"Content-Type": req.headers["content-type"], **self.headers("POST", "/v1/jobs", body)}
        return self.c.post("/v1/jobs", content=body, headers=h)

    def get(self, path: str):
        return self.c.get(path, headers=self.headers("GET", path, b""))

    def post_json(self, path: str, payload, extra: dict | None = None):
        import json

        body = json.dumps(payload).encode()
        h = {"Content-Type": "application/json", **self.headers("POST", path, body), **(extra or {})}
        return self.c.post(path, content=body, headers=h)


@pytest.fixture()
def invoice(env, client, monkeypatch):
    """Dua namespace (test-app, other-app), masing-masing API key + api_secret."""
    from types import SimpleNamespace

    from engine import settings
    from engine.api.auth import create_api_key
    from engine.documents import plugins
    from engine.documents.signing import set_api_secret
    from engine.tenants import save_tenant

    monkeypatch.setenv("PQE_SECRET_KEY", "test-secret-key-for-invoice")
    settings.reset_settings_cache()
    plugins.registry.cache_clear()
    save_tenant(OTHER_TENANT_YAML, "test")
    raw_a, ka = create_api_key("app-a", "app", "test-app")
    raw_b, kb = create_api_key("app-b", "app", "other-app")
    a = SignedClient(client, raw_a, set_api_secret(ka.id))
    b = SignedClient(client, raw_b, set_api_secret(kb.id))
    yield SimpleNamespace(a=a, b=b, client=client, key_a=ka, key_b=kb)
    plugins.registry.cache_clear()


@pytest.fixture(scope="session")
def invoice_docs():
    """Invoice sintetis (tanpa data nyata) dipakai bersama antar test."""
    from engine.documents.invoice.synth import Line, SynthInvoice, make_pdf, scan_of

    std = make_pdf(SynthInvoice())
    english = SynthInvoice(layout="english", vendor_name="Contoh Global Ltd", invoice_number="CG-2026-0007",
                           invoice_date="October 15, 2026", due_date="2026-11-14", period="October 2026",
                           bank_account="0987654321",
                           lines=[Line("Cloud hosting", "2", "1,250,000.50", "2,500,001.00")],
                           subtotal="2,500,001.00", discount="1.00", dpp="2,500,000.00", vat="275,000.00",
                           wht="50,000.00", total="2,775,000.00")
    custom = SynthInvoice(layout="custom")
    custom2 = SynthInvoice(layout="custom", invoice_number="INV/2026/11/0107", invoice_date="16/11/2026",
                           due_date="16/12/2026", period="01/11/2026 - 30/11/2026", bank_account="123-456-7890",
                           lines=[Line("Sewa server bulanan", "2", "10.000.000", "20.000.000")],
                           subtotal="20.000.000", discount="", dpp="20.000.000", vat="2.200.000", wht="400.000",
                           total="22.200.000")
    return {
        "pdf": std,
        "jpg": scan_of(std),
        "english_pdf": make_pdf(english),
        "english_png": scan_of(make_pdf(english), fmt="PNG", angle=-3),
        "custom_pdf": make_pdf(custom),
        "custom2_pdf": make_pdf(custom2),
        "custom2_jpg": scan_of(make_pdf(custom2), angle=1.5, seed=11),
    }
