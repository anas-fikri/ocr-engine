"""Test end-to-end: S3 (moto) -> ingestor -> antrean -> worker -> API -> webhook -> review -> evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx
import pytest

from engine.storage import get_storage


def put_photo(key: str, data: bytes, **meta) -> None:
    get_storage().put("app-photos", key, data, "image/jpeg", {k.replace("_", "-"): v for k, v in meta.items()})


def ingest_and_process() -> int:
    from engine.ingestor import Ingestor
    from engine.worker import Worker

    Ingestor().tick(time.monotonic())
    return Worker().run_until_empty()


class Hooks:
    def __init__(self):
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(200)


@pytest.fixture()
def hooks(monkeypatch):
    h = Hooks()
    real_client = httpx.Client

    def fake_client(*a, **kw):
        kw["transport"] = httpx.MockTransport(h)
        return real_client(*a, **kw)

    monkeypatch.setattr("engine.webhooks.httpx.Client", fake_client)
    return h


def test_polling_flow_end_to_end(client, admin_headers, samples, hooks):
    put_photo("pod/pod_recipient/AWB100_good.jpg", samples["pass_huge_camera"], courier_id="K1")
    put_photo("pod/pod_recipient/AWB200_blur.jpg", samples["reject_blur_severe"], courier_id="K2")
    put_photo("pod/pod_recipient/readme.txt", b"bukan foto")  # diabaikan (bukan ekstensi foto)
    assert ingest_and_process() == 2

    r = client.get("/v1/photos", params={"awb": "AWB100"}, headers=admin_headers)
    assert r.status_code == 200, r.text
    good = r.json()["items"][0]
    assert good["verdict"] == "PASS" and good["courier_id"] == "K1" and good["photo_type"] == "pod_recipient"
    assert good["urls"]["processed"] and good["urls"]["thumbnail"]

    detail = client.get(f"/v1/photos/{good['id']}", headers=admin_headers).json()
    assert detail["compression"]["output_bytes"] <= 1_000_000 < detail["compression"]["original_bytes"]
    processed = httpx.get(detail["urls"]["processed"])
    assert processed.status_code == 200 and len(processed.content) == detail["compression"]["output_bytes"]

    bad = client.get("/v1/photos", params={"awb": "AWB200"}, headers={**admin_headers, "Accept-Language": "en"})
    bad = bad.json()["items"][0]
    assert bad["verdict"] == "REJECT"
    assert bad["action"] == "notify"  # profile pod_recipient tenant ini mode warn
    assert bad["reasons"][0]["code"] == "BLURRY" and bad["reasons"][0]["message"] == "The photo is blurry"

    # polling ulang tidak membuat proses ganda
    assert ingest_and_process() == 0

    # webhook terkirim + tanda tangan valid
    assert len(hooks.calls) == 2
    req = hooks.calls[0]
    ts = req.headers["X-PQE-Timestamp"]
    expected = hmac.new(b"s3cr3t", ts.encode() + b"." + req.content, hashlib.sha256).hexdigest()
    assert req.headers["X-PQE-Signature"] == f"sha256={expected}"
    assert json.loads(req.content)["event"] == "analysis.completed"

    # antrean review ops hanya berisi foto bermasalah
    q = client.get("/v1/photos", params={"needs_review": True}, headers=admin_headers).json()
    assert [p["awb"] for p in q["items"]] == ["AWB200"]

    stats = client.get("/v1/stats", headers=admin_headers).json()
    assert stats["by_verdict"] == {"PASS": 1, "REJECT": 1}
    assert stats["top_reasons"][0]["code"] == "BLURRY"


def test_review_and_evidence(client, admin_headers, samples, hooks):
    put_photo("pod/pod_recipient/AWB300_a.jpg", samples["pass_sharp_large"], customer_id="CUST1")
    put_photo("pod/pod_recipient/AWB300_b.jpg", samples["warn_blur_mild"], customer_id="CUST1")
    put_photo("pod/pod_recipient/AWB400_a.jpg", samples["pass_sharp_large"], customer_id="CUST2")
    ingest_and_process()

    ev = client.get("/v1/evidence/AWB300", headers=admin_headers).json()
    assert ev["count"] == 1  # foto WARN belum tampil sebelum di-approve

    warn = client.get("/v1/photos", params={"awb": "AWB300", "verdict": "WARN"}, headers=admin_headers).json()
    pid = warn["items"][0]["id"]
    r = client.post(f"/v1/photos/{pid}/review", json={"action": "approve", "note": "masih terbaca"},
                    headers=admin_headers)
    assert r.status_code == 200 and r.json()["review"]["status"] == "approved"
    assert client.get("/v1/evidence/AWB300", headers=admin_headers).json()["count"] == 2

    # API key pelanggan hanya bisa melihat resi miliknya
    key = client.post("/v1/admin/api-keys", json={"name": "pelanggan 1", "role": "customer",
                                                  "customer_id": "CUST1"}, headers=admin_headers).json()
    cust = {"X-API-Key": key["api_key"]}
    assert client.get("/v1/evidence/AWB300", headers=cust).json()["count"] == 2
    assert client.get("/v1/evidence/AWB400", headers=cust).json()["count"] == 0
    assert client.get("/v1/photos", headers=cust).status_code == 403

    pdf = client.get("/v1/evidence/AWB300/pdf", headers=cust)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and len(pdf.content) > 20_000
    batch = client.post("/v1/evidence/batch", json={"awbs": ["AWB300", "AWB999"]}, headers=admin_headers)
    assert batch.status_code == 200 and batch.content.startswith(b"%PDF")

    sha = ev["photos"][0]["sha256"]
    v = client.get(f"/v1/verify/{sha}").json()
    assert v["found"] is True and v["awb_suffix"] == "B300"
    assert client.get("/v1/verify/" + "0" * 64).json() == {"found": False}

    from engine.webhooks import deliver_pending

    deliver_pending()  # di produksi dikirim oleh loop maintenance worker
    assert any(json.loads(c.content)["event"] == "review.updated" for c in hooks.calls)


def test_duplicate_reuse_across_deliveries(client, admin_headers, samples):
    put_photo("pod/pod_recipient/AWB500_x.jpg", samples["pass_sharp_large"])
    ingest_and_process()
    put_photo("pod/pod_recipient/AWB501_x.jpg", samples["pass_sharp_large"])
    ingest_and_process()
    dup = client.get("/v1/photos", params={"awb": "AWB501"}, headers=admin_headers).json()["items"][0]
    assert dup["verdict"] == "WARN"
    assert "DUPLICATE_EXACT" in [r["code"] for r in dup["reasons"]]
    assert dup["scores"]["authenticity_risk"] == 70


def test_api_upload_sync_and_object_reference(client, admin_headers, samples):
    key = client.post("/v1/admin/api-keys", json={"name": "aplikasi test", "role": "app", "tenant": "test-app"},
                      headers=admin_headers).json()["api_key"]
    app_h = {"X-API-Key": key}

    r = client.post("/v1/analyze", params={"sync": True}, headers=app_h,
                    files={"file": ("foto.jpg", samples["reject_blur_severe"], "image/jpeg")},
                    data={"awb": "AWB600", "photo_type": "pod_recipient", "courier_id": "K9"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "REJECT" and body["awb"] == "AWB600" and body["reasons"][0]["code"] == "BLURRY"
    assert body["reasons"][0]["suggestion"]
    assert body["retake_required"] is True and "foto ulang" in body["retake_message"]

    # foto gelap diperbaiki otomatis: tidak perlu foto ulang, versi kompres = hasil perbaikan
    r = client.post("/v1/analyze", params={"sync": True}, headers=app_h,
                    files={"file": ("gelap.jpg", samples["reject_dark"], "image/jpeg")}, data={"awb": "AWB601"})
    dark = r.json()
    assert dark["verdict"] in ("PASS", "WARN") and dark["retake_required"] is False and dark["retake_message"] is None
    detail = client.get(f"/v1/photos/{dark['id']}", headers=admin_headers).json()
    assert detail["enhancement"]["applied"] is True and detail["enhancement"]["before"]["verdict"] == "REJECT"
    assert detail["urls"]["processed"] != detail["urls"]["original"]

    put_photo("pod/pod_document/AWB700_resi.jpg", samples["pass_sharp_large"])
    r = client.post("/v1/analyze?sync=true", headers=app_h,
                    json={"object_key": "pod/pod_document/AWB700_resi.jpg", "external_id": "foto-77"})
    assert r.status_code == 200, r.text
    assert r.json()["photo_type"] == "pod_document" and r.json()["external_id"] == "foto-77"

    # async default -> 202 + poll_url
    r = client.post("/v1/analyze", headers=app_h, files={"file": ("a.jpg", samples["pass_small_ok"], "image/jpeg")})
    assert r.status_code == 202 and r.json()["poll_url"].startswith("/v1/photos/")

    # app key tidak bisa mengakses tenant lain / endpoint admin
    assert client.post("/v1/analyze", params={"tenant": "lain"}, headers=app_h,
                       files={"file": ("a.jpg", b"x", "image/jpeg")}).status_code == 403
    assert client.get("/v1/admin/tenants", headers=app_h).status_code == 403
    assert client.post("/v1/analyze", headers=app_h, json={"object_key": "tidak/ada.jpg"}).status_code == 404


def test_preview_does_not_store(client, admin_headers, samples):
    r = client.post("/v1/analyze/preview", params={"photo_type": "pod_document"}, headers=admin_headers,
                    files={"file": ("a.jpg", samples["pass_huge_camera"], "image/jpeg")})
    assert r.status_code == 200, r.text
    assert r.json()["profile"] == "pod_document" and r.json()["compression"]["target_met"]
    assert client.get("/v1/photos", headers=admin_headers).json()["total"] == 0


def test_s3_event_webhook(client, admin_headers, samples):
    put_photo("pod/pod_recipient/AWB800.jpg", samples["pass_sharp_large"])
    event = {"EventName": "s3:ObjectCreated:Put", "Records": [{
        "eventName": "s3:ObjectCreated:Put",
        "s3": {"bucket": {"name": "app-photos"}, "object": {"key": "pod/pod_recipient/AWB800.jpg", "eTag": "x"}},
    }, {
        "eventName": "s3:ObjectCreated:Put",
        "s3": {"bucket": {"name": "bucket-lain"}, "object": {"key": "a.jpg"}},
    }]}
    assert client.post("/v1/events/s3", json=event).status_code == 401
    r = client.post("/v1/events/s3", json=event, headers={"Authorization": "Bearer event-token"})
    assert r.json() == {"accepted": 1, "ignored": 1}
    from engine.worker import Worker

    assert Worker().run_until_empty() == 1
    items = client.get("/v1/photos", params={"awb": "AWB800"}, headers=admin_headers).json()["items"]
    assert items[0]["verdict"] == "PASS"


def test_unreadable_file_goes_to_review(client, admin_headers):
    put_photo("pod/pod_recipient/AWB900.jpg", b"\xff\xd8\xff rusak")
    ingest_and_process()
    p = client.get("/v1/photos", params={"awb": "AWB900"}, headers=admin_headers).json()["items"][0]
    assert p["status"] == "error" and p["verdict"] == "REJECT" and p["error"]["code"] == "UNREADABLE_FILE"
    assert client.get("/v1/photos", params={"needs_review": True}, headers=admin_headers).json()["total"] == 1


def test_transient_storage_error_retries_then_dead(client, admin_headers, samples, monkeypatch):
    put_photo("pod/pod_recipient/AWB950.jpg", samples["pass_sharp_large"])
    from engine.ingestor import Ingestor
    from engine.processor import run_one
    from engine.storage import Storage

    Ingestor().tick(time.monotonic())

    def boom(self, bucket, key):
        raise ConnectionError("SILO timeout")

    monkeypatch.setattr(Storage, "get", boom)
    assert run_one() is True
    jobs = client.get("/v1/admin/jobs", params={"status": "queued"}, headers=admin_headers).json()
    assert jobs["items"][0]["attempts"] == 1 and "SILO timeout" in jobs["items"][0]["last_error"]

    # jatuh tempo retry dipercepat untuk test
    from engine.db import Job, session_scope, utcnow

    with session_scope() as s:
        for j in s.query(Job).all():
            j.run_after = utcnow()
    assert run_one() is True  # percobaan ke-2 = batas -> dead
    dead = client.get("/v1/admin/jobs", headers=admin_headers).json()
    assert dead["stats"]["dead"] == 1
    p = client.get("/v1/photos", params={"awb": "AWB950"}, headers=admin_headers).json()["items"][0]
    assert p["error"]["code"] == "PROCESSING_FAILED"

    monkeypatch.undo()
    assert client.post("/v1/admin/jobs/retry-dead", headers=admin_headers).json() == {"requeued": 1}
    from engine.worker import Worker

    Worker().run_until_empty()
    p = client.get("/v1/photos", params={"awb": "AWB950"}, headers=admin_headers).json()["items"][0]
    assert p["status"] == "done" and p["verdict"] == "PASS"


def test_admin_tenant_config_lifecycle(client, admin_headers):
    h = {**admin_headers, "Content-Type": "application/x-yaml"}
    bad = client.post("/v1/admin/tenants/validate", content="code: X\nname: y\n", headers=h)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_CONFIG"
    bad_rule = "code: app-b\nname: B\nmapping:\n  awb: ['key:regex:(']\n"
    assert client.post("/v1/admin/tenants/validate", content=bad_rule, headers=h).status_code == 422

    yaml_text = "code: app-b\nname: Aplikasi B\nprofiles:\n  package:\n    mode: enforce\n"
    r = client.put("/v1/admin/tenants", content=yaml_text, headers=h)
    assert r.status_code == 200 and r.json() == {"code": "app-b", "version": 1, "created": True}
    r = client.put("/v1/admin/tenants", content=yaml_text.replace("Aplikasi B", "Aplikasi B2"), headers=h)
    assert r.json()["version"] == 2
    t = client.get("/v1/admin/tenants/app-b", headers=admin_headers).json()
    assert t["resolved_profiles"]["package"]["mode"] == "enforce"
    assert t["resolved_profiles"]["pod_recipient"]["mode"] == "shadow"
    assert len(client.get("/v1/admin/tenants/app-b/history", headers=admin_headers).json()) == 2
    assert client.post("/v1/admin/tenants/app-b/disable", headers=admin_headers).json()["enabled"] is False
    codes = {x["code"]: x for x in client.get("/v1/admin/tenants", headers=admin_headers).json()}
    assert codes["app-b"]["enabled"] is False and "test-app" in codes
    audit = client.get("/v1/admin/audit", headers=admin_headers).json()
    assert {"tenant.create", "tenant.update", "tenant.disable"} <= {a["action"] for a in audit}


def test_auth_and_health(client, admin_headers):
    assert client.get("/v1/photos").status_code == 401
    assert client.get("/v1/photos", headers={"X-API-Key": "salah"}).json()["error"]["code"] == "UNAUTHORIZED"
    assert client.get("/v1/me", headers={"Authorization": "Bearer pqe_admin_test_key"}).json()["role"] == "admin"
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/readyz").json()["checks"] == {"database": "ok", "storage": "ok"}
    assert b"pqe_queue_depth" in client.get("/metrics").content
    assert client.get("/ui/").status_code == 200
    assert client.get("/docs").status_code == 200
    codes = client.get("/v1/meta/reason-codes?lang=en").json()
    assert {"code": "BLURRY", "message": "The photo is blurry"}.items() <= next(
        c for c in codes if c["code"] == "BLURRY").items()


def test_cors_disabled_by_default_and_configurable(env, monkeypatch):
    from fastapi.testclient import TestClient

    from engine.api.app import create_app
    from engine.settings import reset_settings_cache

    with TestClient(create_app(run_bootstrap=False)) as c:
        r = c.options("/v1/photos", headers={"Origin": "https://portal.test", "Access-Control-Request-Method": "GET"})
        assert "access-control-allow-origin" not in r.headers
    monkeypatch.setenv("PQE_CORS_ORIGINS", "https://portal.test")
    reset_settings_cache()
    with TestClient(create_app(run_bootstrap=False)) as c:
        r = c.options("/v1/photos", headers={"Origin": "https://portal.test", "Access-Control-Request-Method": "GET"})
        assert r.headers["access-control-allow-origin"] == "https://portal.test"


def test_dead_webhook_can_be_retried(client, admin_headers, samples, monkeypatch):
    import engine.webhooks as wh

    put_photo("pod/pod_recipient/AWB990.jpg", samples["pass_sharp_large"])
    monkeypatch.setattr(wh, "MAX_ATTEMPTS", 1)
    real_client = httpx.Client
    failing = httpx.MockTransport(lambda r: httpx.Response(500))
    monkeypatch.setattr("engine.webhooks.httpx.Client", lambda *a, **kw: real_client(*a, transport=failing, **kw))
    ingest_and_process()
    dead = client.get("/v1/admin/webhooks", headers=admin_headers).json()
    assert len(dead) == 1 and dead[0]["last_error"].startswith("HTTP 500")
    assert client.post("/v1/admin/webhooks/retry-dead", headers=admin_headers).json() == {"requeued": 1}
    pending = client.get("/v1/admin/webhooks", params={"status": "pending"}, headers=admin_headers).json()
    assert pending[0]["id"] == dead[0]["id"]
    metrics = client.get("/metrics").text
    assert "pqe_pending_webhooks 1.0" in metrics
