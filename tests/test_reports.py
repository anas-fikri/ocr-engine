"""Laporan kalibrasi, ringkasan harian, dan alert lonjakan."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx


def _photos(rows):
    """rows: (profile, verdict, review_status, scores, reasons, branch, courier, hours_ago)."""
    from engine.db import Photo, new_id, session_scope, utcnow

    now = utcnow()
    with session_scope() as s:
        for profile, verdict, review, scores, reasons, branch, courier, ago in rows:
            pid = new_id()
            s.add(Photo(id=pid, tenant_code="test-app", bucket="", object_key=f"unstored/{pid}",
                        ingest_source="upload", status="done", profile=profile, verdict=verdict,
                        review_status=review, scores=scores, reasons=reasons, branch=branch, courier_id=courier,
                        duration_ms=500, received_at=now - timedelta(hours=ago)))


BLUR = [{"code": "BLURRY", "check": "sharpness", "severity": "reject"}]


def test_calibration_suggests_threshold_and_mode(client, admin_headers):
    # 12 foto REJECT karena buram yang direview: 8 disetujui (skor 15-22), 4 memang buruk (skor 3-6)
    rows = [("pod_recipient", "REJECT", "approved", {"sharpness": 15 + i}, BLUR, "B1", "K1", 30) for i in range(8)]
    rows += [("pod_recipient", "REJECT", "retake_requested", {"sharpness": 3 + i}, BLUR, "B1", "K1", 30)
             for i in range(4)]
    rows += [("pod_recipient", "PASS", "none", {"sharpness": 90}, [], "B1", "K1", 30)] * 10
    _photos(rows)
    r = client.get("/v1/admin/calibration", params={"days": 7}, headers=admin_headers).json()
    g = next(x for x in r["groups"] if x["profile"] == "pod_recipient")
    assert g["reviewed"] == 12 and g["false_reject"] == 8 and g["verdicts"]["REJECT"] == 12
    blur = next(x for x in g["reasons"] if x["code"] == "BLURRY")
    assert blur["reviewed"] == 12 and blur["approved"] == 8
    s = next(x for x in r["suggestions"] if x["kind"] == "threshold")
    assert s["field"] == "thresholds.sharpness.reject" and s["current"] == 25 and 6 <= s["suggested"] < 25
    # mode tenant test: warn; foto & review belum cukup -> belum siap naik ke enforce, alasan jelas
    assert g["mode"] == "warn" and g["mode_readiness"]["to"] == "enforce"
    assert "foto lagi" in " ".join(g["mode_readiness"]["blocked_by"])
    assert "Turunkan sharpness.reject" in r["text"]


def test_calibration_mode_promotion_when_accurate(client, admin_headers):
    rows = [("pod_document", "PASS", "approved", {"sharpness": 90}, [], "B2", "K2", 10)] * 60
    rows += [("pod_document", "PASS", "none", {"sharpness": 90}, [], "B2", "K2", 10)] * 450
    _photos(rows)
    r = client.get("/v1/admin/calibration", headers=admin_headers).json()
    s = next(x for x in r["suggestions"] if x["kind"] == "mode" and x["profile"] == "pod_document")
    assert s["current"] == "shadow" and s["suggested"] == "warn"


def test_digest_and_spike_alerts(client, admin_headers, monkeypatch):
    from engine import alerts
    from engine.db import utcnow

    # cabang B9: biasanya 10% ditolak, 24 jam terakhir 60%
    rows = [("pod_recipient", "REJECT" if i < 2 else "PASS", "none", {}, BLUR if i < 2 else [], "B9", "K9", 48)
            for i in range(20)]
    rows += [("pod_recipient", "REJECT" if i < 6 else "PASS", "none", {}, BLUR if i < 6 else [], "B9", "K9", 2)
             for i in range(10)]
    _photos(rows)
    d = client.get("/v1/admin/digest", headers=admin_headers).json()
    assert d["summary"]["verdicts"]["REJECT"] == 6 and d["summary"]["top_reasons"][0]["code"] == "BLURRY"
    spike = next(x for x in d["spikes"] if x["kind"] == "branch")
    assert spike["key"] == "B9" and spike["reject_rate"] == 0.6 and spike["baseline_rate"] == 0.1
    assert "Cabang B9" in d["text"]

    # simpan pengaturan (URL terenkripsi, tidak pernah dikembalikan API)
    monkeypatch.setenv("PQE_SECRET_KEY", "test-secret-key-for-alerts-0123456789")
    from engine import settings

    settings.reset_settings_cache()
    url = "https://chat.example/hook/SECRET"
    r = client.put("/v1/admin/settings/alerts", json={"enabled": True, "webhook_url": url, "daily_hour": 0},
                   headers=admin_headers)
    assert r.status_code == 200 and url not in r.text and r.json()["config"]["webhook_url"] == {"set": True}

    sent = []
    client_mock = httpx.Client(transport=httpx.MockTransport(
        lambda req: sent.append(json.loads(req.content)) or httpx.Response(200)))
    # jam ringkasan sudah lewat (daily_hour 0): ringkasan harian sekali per hari, lonjakan ikut di dalamnya
    now = utcnow()
    assert alerts.maybe_run(now=now, client=client_mock) == "digest"
    assert set(sent[0]) == {"text"} and "Cabang B9" in sent[0]["text"]
    alerts._last_spike_check = 0
    # lonjakan yang sama tidak dikirim ulang dalam 24 jam
    assert alerts.maybe_run(now=now + timedelta(minutes=20), client=client_mock) is None
    assert len(sent) == 1

    # webhook gagal: pesan error tanpa URL rahasia
    bad = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(403)))
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: bad)
    r = client.post("/v1/admin/settings/alerts/test", json={}, headers=admin_headers)
    assert r.status_code == 502 and "SECRET" not in r.text and "403" in r.text
