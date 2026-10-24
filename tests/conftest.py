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
