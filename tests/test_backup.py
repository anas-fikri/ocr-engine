"""Pagar pengaman bucket aplikasi dan backup ke NAS (folder & S3-compatible)."""

import pytest


def test_engine_never_writes_or_deletes_app_bucket(env):
    from engine.storage import ForeignBucketWrite, get_storage

    st = get_storage()
    with pytest.raises(ForeignBucketWrite):
        st.put("app-photos", "x.jpg", b"x", "image/jpeg")
    with pytest.raises(ForeignBucketWrite):
        st.delete("app-photos", "x.jpg")
    st.put("photos-processed", "ok.txt", b"x", "text/plain")  # bucket engine tetap boleh
    st.delete("photos-processed", "ok.txt")


def test_demo_upload_refused_in_production(env, monkeypatch):
    from engine.cli import main
    from engine.settings import reset_settings_cache
    from engine.storage import get_storage, reset_storage_cache

    monkeypatch.setenv("PQE_ENV", "production")
    reset_settings_cache()
    reset_storage_cache()
    with pytest.raises(SystemExit) as e:
        main(["demo-upload"])
    assert "Ditolak" in str(e.value)
    assert list(get_storage().list("app-photos")) == []


def _enforce(client, admin_headers):
    from tests.conftest import TENANT_YAML

    h = {**admin_headers, "Content-Type": "application/x-yaml"}
    assert client.put("/v1/admin/tenants", content=TENANT_YAML.replace("mode: warn", "mode: enforce"),
                      headers=h).status_code == 200


def _upload(client, admin_headers, data, awb):
    r = client.post("/v1/analyze", params={"tenant": "test-app", "sync": True}, headers=admin_headers,
                    files={"file": ("f.jpg", data, "image/jpeg")}, data={"awb": awb})
    assert r.status_code == 200, r.text
    return r.json()


def test_backup_to_folder(client, admin_headers, samples, tmp_path):
    from engine import backup

    _enforce(client, admin_headers)
    ok = _upload(client, admin_headers, samples["pass_huge_camera"], "BK1")
    rej = _upload(client, admin_headers, samples["reject_blur_severe"], "BK2")
    assert ok["stored"] and rej["stored"] is None

    nas = tmp_path / "nas"
    nas.mkdir()
    assert client.put("/v1/admin/settings/backup", json={"folder_path": "relatif/salah"},
                      headers=admin_headers).status_code == 422
    r = client.put("/v1/admin/settings/backup", json={"enabled": True, "target": "folder", "folder_path": str(nas)},
                   headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["pending"] == 2
    test = client.post("/v1/admin/settings/backup/test", json={}, headers=admin_headers).json()
    assert test["ok"], test

    res = backup.maybe_run()
    assert res["error"] is None and res["photos"]["photos"] == 2
    files = sorted(p.relative_to(nas).as_posix() for p in nas.rglob("*") if p.is_file())
    assert sum(f.startswith("photos/photos-upload/") for f in files) == 2  # foto asli keduanya
    assert sum(f.startswith("photos/photos-processed/") for f in files) == 1  # hanya foto yang diterima
    assert any(f.startswith("database/pqe-") and f.endswith(".sqlite") for f in files)
    stored = ok["stored"]
    assert (nas / "photos" / stored["bucket"] / stored["key"]).exists()

    assert backup.maybe_run() is None  # belum waktunya (jadwal 15 menit), tidak ada permintaan
    assert client.post("/v1/admin/backup/run", json={"kind": "photos"}, headers=admin_headers).status_code == 200
    again = backup.maybe_run()
    assert again["photos"] == {"photos": 0, "files": 0} and "database" not in again

    # rotasi dump database: hanya `db_keep` dump terbaru yang disimpan
    (nas / "database" / "pqe-20200101-000000.sqlite").write_bytes(b"lama")
    client.put("/v1/admin/settings/backup", json={"db_keep": 1}, headers=admin_headers)
    client.post("/v1/admin/backup/run", json={"kind": "database"}, headers=admin_headers)
    res = backup.maybe_run()
    assert res["database"]["rotated"] >= 1
    dumps = sorted(p.name for p in (nas / "database").iterdir())
    assert len(dumps) == 1 and dumps[0] != "pqe-20200101-000000.sqlite"

    status = client.get("/v1/admin/settings/backup", headers=admin_headers).json()
    assert status["pending"] == 0 and status["backed_up"] == 2 and status["state"]["last_error"] is None


def test_backup_to_s3_nas_and_errors(client, admin_headers, samples, env, monkeypatch):
    import boto3

    from engine import backup
    from engine.settings import reset_settings_cache

    s3 = boto3.client("s3", endpoint_url=env["AWS_ENDPOINT"], region_name="us-east-1",
                      aws_access_key_id="test", aws_secret_access_key="test")
    try:
        s3.create_bucket(Bucket="nas-backup")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        pass
    _upload(client, admin_headers, samples["pass_huge_camera"], "BK3")
    body = {"enabled": True, "target": "s3", "s3_endpoint": env["AWS_ENDPOINT"], "s3_bucket": "nas-backup",
            "s3_prefix": "pqe", "s3_access_key": "test", "s3_secret_key": "rahasia-nas", "include_database": False}
    r = client.put("/v1/admin/settings/backup", json=body, headers=admin_headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SECRET_KEY_MISSING"

    monkeypatch.setenv("PQE_SECRET_KEY", "kunci-test")
    reset_settings_cache()
    r = client.put("/v1/admin/settings/backup", json=body, headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["config"]["s3_secret_key"] == {"set": True} and "rahasia-nas" not in r.text
    res = backup.maybe_run()
    assert res["error"] is None and res["photos"]["files"] == 2  # asli + versi pengiriman
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="nas-backup", Prefix="pqe/photos/").get("Contents", [])]
    assert len(keys) == 2

    # NAS tidak bisa dijangkau -> dicatat sebagai error, foto tetap menunggu (tidak dianggap tersalin)
    _upload(client, admin_headers, samples["pass_small_ok"], "BK4")
    client.put("/v1/admin/settings/backup", json={"s3_bucket": "tidak-ada"}, headers=admin_headers)
    client.post("/v1/admin/backup/run", json={"kind": "photos"}, headers=admin_headers)
    res = backup.maybe_run()
    assert res["error"]
    status = client.get("/v1/admin/settings/backup", headers=admin_headers).json()
    assert status["pending"] == 1 and status["state"]["last_error"]
