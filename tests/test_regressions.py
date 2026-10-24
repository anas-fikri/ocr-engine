"""Test regresi untuk bug yang ditemukan saat review dokumentasi."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

from PIL import Image

from engine.analyzers import AnalysisContext
from engine.pipeline import analyze
from engine.synth import base_scene
from engine.tenant_config import build_profile, parse_tenant_yaml


def test_single_authenticity_finding_warns():
    img = base_scene(1600, 1200)
    exif = Image.Exif()
    exif[305] = "Snapseed 2.0"
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, exif=exif)
    r = analyze(buf.getvalue(), AnalysisContext(profile=build_profile("pod_recipient"),
                                                received_at=datetime.now(UTC)), do_compress=False)
    assert r.verdict.scores["authenticity"] == 60
    assert r.verdict.verdict == "WARN"
    assert "EDITING_SOFTWARE" in {x.code for x in r.verdict.reasons}


def test_extends_in_tenant_default_profile():
    cfg = parse_tenant_yaml("code: t1\nname: T\nprofiles:\n  default:\n    extends: package\n    mode: warn\n")
    assert cfg.resolve_profile("pod_recipient")[1].mode == "warn"


def test_reason_filter_total_matches(client, admin_headers, samples):
    from tests.test_e2e import ingest_and_process, put_photo

    put_photo("pod/pod_recipient/AWB1.jpg", samples["reject_overexposed"])
    put_photo("pod/pod_recipient/AWB2.jpg", samples["reject_blur_severe"])
    put_photo("pod/pod_recipient/AWB3.jpg", samples["pass_huge_camera"])
    ingest_and_process()
    r = client.get("/v1/photos", params={"reason": "TOO_BRIGHT", "limit": 1}, headers=admin_headers).json()
    assert r["total"] == 1 and r["items"][0]["awb"] == "AWB1"
    assert client.get("/v1/photos", params={"reason": "bukan kode"}, headers=admin_headers).status_code == 422


def test_retention_change_updates_existing_photos(client, admin_headers, samples):
    from engine.db import Photo, as_aware, session_scope
    from tests.conftest import TENANT_YAML
    from tests.test_e2e import ingest_and_process, put_photo

    put_photo("pod/pod_recipient/AWB10.jpg", samples["pass_small_ok"])
    ingest_and_process()
    h = {**admin_headers, "Content-Type": "application/x-yaml"}
    r = client.put("/v1/admin/tenants", content=TENANT_YAML.replace("retention_days: 30", "retention_days: 400"),
                   headers=h)
    assert r.status_code == 200, r.text
    with session_scope() as s:
        p = s.query(Photo).one()
        delta = as_aware(p.expires_at) - as_aware(p.received_at)
    assert abs(delta - timedelta(days=400)) < timedelta(seconds=1)


def test_s3_event_matches_source_without_bucket(client, admin_headers, samples, monkeypatch):
    from tests.conftest import TENANT_YAML
    from tests.test_e2e import put_photo

    h = {**admin_headers, "Content-Type": "application/x-yaml"}
    yaml_no_bucket = TENANT_YAML.replace("    bucket: app-photos\n", "")
    assert client.put("/v1/admin/tenants", content=yaml_no_bucket, headers=h).status_code == 200
    put_photo("pod/pod_recipient/AWB20.jpg", samples["pass_small_ok"])
    event = {"Records": [{"eventName": "s3:ObjectCreated:Put", "s3": {
        "bucket": {"name": "app-photos"}, "object": {"key": "pod/pod_recipient/AWB20.jpg"}}}]}
    r = client.post("/v1/events/s3", json=event, headers={"Authorization": "Bearer event-token"})
    assert r.json() == {"accepted": 1, "ignored": 0}


def test_db_poll_with_integer_checkpoint(client, admin_headers, samples, tmp_path, monkeypatch):
    import sqlalchemy as sa

    from engine.ingestor import run_source
    from engine.storage import get_storage
    from engine.tenants import registry
    from engine.worker import Worker
    from tests.test_e2e import put_photo

    app_db = tmp_path / "app.db"
    eng = sa.create_engine(f"sqlite:///{app_db}")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE delivery_photos (id INTEGER PRIMARY KEY, photo_url TEXT, awb TEXT)"))
        for i in range(1, 11):  # id 1..10: "10" < "9" jika dibandingkan sebagai teks
            put_photo(f"legacy/AWB{i:03d}.jpg", samples["pass_small_ok"] if i % 2 else samples["reject_dark"])
            url = f"{get_storage().s.aws_endpoint}/app-photos/legacy/AWB{i:03d}.jpg"
            row = {"i": i, "u": url, "a": f"AWB{i:03d}"}
            c.execute(sa.text("INSERT INTO delivery_photos VALUES (:i, :u, :a)"), row)
    monkeypatch.setenv("APP_DB_DSN", f"sqlite:///{app_db}")
    yaml_text = """
code: legacy-app
name: Legacy
sources:
  - type: db_poll
    dsn_env: APP_DB_DSN
    query: "SELECT id, photo_url, awb FROM delivery_photos WHERE id > :checkpoint ORDER BY id LIMIT 4"
    checkpoint_column: id
    lookback_days: 0
"""
    h = {**admin_headers, "Content-Type": "application/x-yaml"}
    assert client.put("/v1/admin/tenants", content=yaml_text, headers=h).status_code == 200
    from engine.db import SourceState, session_scope

    with session_scope() as s:
        s.add(SourceState(id="legacy-app:0:db_poll", checkpoint="0"))
    t = registry.get("legacy-app")
    found = [run_source(t, 0, t.config.sources[0], get_storage()) for _ in range(4)]
    assert found == [4, 4, 2, 0]
    with session_scope() as s:
        assert s.get(SourceState, "legacy-app:0:db_poll").checkpoint == "10"
    Worker().run_until_empty()
    items = client.get("/v1/photos", params={"tenant": "legacy-app", "limit": 200}, headers=admin_headers).json()
    assert items["total"] == 10
    assert {p["awb"] for p in items["items"]} == {f"AWB{i:03d}" for i in range(1, 11)}


def test_review_optional_by_default_warn_goes_to_evidence(client, admin_headers, samples):
    """review_required: false (bawaan) -> foto WARN langsung masuk evidence, REJECT tidak."""
    from tests.conftest import TENANT_YAML
    from tests.test_e2e import ingest_and_process, put_photo

    h = {**admin_headers, "Content-Type": "application/x-yaml"}
    no_review = TENANT_YAML.replace("review_required: true\n", "")
    assert client.put("/v1/admin/tenants", content=no_review, headers=h).status_code == 200
    put_photo("pod/pod_recipient/AWB30_a.jpg", samples["warn_blur_mild"])
    put_photo("pod/pod_recipient/AWB30_b.jpg", samples["reject_blur_severe"])
    ingest_and_process()
    ev = client.get("/v1/evidence/AWB30", headers=admin_headers).json()
    assert ev["count"] == 1
    listing = client.get("/v1/photos", params={"awb": "AWB30"}, headers=admin_headers).json()["items"]
    items = {p["verdict"]: p for p in listing}
    assert items["WARN"]["evidence_eligible"] is True and items["WARN"]["retake_required"] is False
    assert items["REJECT"]["evidence_eligible"] is False and items["REJECT"]["retake_required"] is True


def test_storage_settings_page_api(client, admin_headers, env, monkeypatch):
    """Konfigurasi SILO lewat API/Web UI: format mc, enkripsi secret, test koneksi, berlaku tanpa restart."""
    from engine.db import Setting, session_scope
    from engine.settings import reset_settings_cache
    from engine.storage import get_storage

    # tanpa PQE_SECRET_KEY secret tidak boleh disimpan
    mc = {"url": env["AWS_ENDPOINT"], "accessKey": "test", "secretKey": "rahasia-baru", "api": "s3v4", "path": "on"}
    r = client.put("/v1/admin/settings/storage", json={"mc_config": mc}, headers=admin_headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SECRET_KEY_MISSING"

    monkeypatch.setenv("PQE_SECRET_KEY", "kunci-enkripsi-test")
    reset_settings_cache()
    r = client.put("/v1/admin/settings/storage", json={"mc_config": mc, "app_bucket": "app-photos"},
                   headers=admin_headers)
    assert r.status_code == 200, r.text
    f = r.json()["fields"]
    assert f["endpoint"]["source"] == "database" and f["addressing_style"]["value"] == "path"
    assert f["secret_key"]["value"] == {"set": True}
    assert "rahasia-baru" not in r.text
    with session_scope() as s:  # tersimpan terenkripsi, bukan teks asli
        stored = s.get(Setting, "storage").value
    assert "rahasia-baru" not in str(stored) and stored["secret_key_enc"]
    assert get_storage().s.aws_secret_access_key == "rahasia-baru"  # berlaku tanpa restart

    t = client.post("/v1/admin/settings/storage/test", json={}, headers=admin_headers).json()
    assert t["ok"] is True and len(t["checks"]) == 3  # baca bucket aplikasi + tulis bucket arsip
    bad = client.post("/v1/admin/settings/storage/test", json={"endpoint": "http://127.0.0.1:9"},
                      headers=admin_headers).json()
    assert bad["ok"] is False
    assert client.put("/v1/admin/settings/storage", json={"addressing_style": "aneh"},
                      headers=admin_headers).status_code == 422

    r = client.put("/v1/admin/settings/storage", json={"clear": ["addressing_style", "secret_key"]},
                   headers=admin_headers)
    assert r.json()["fields"]["addressing_style"]["source"] != "database"
    assert get_storage().s.aws_secret_access_key == "test"  # kembali ke env
    app_key = client.post("/v1/admin/api-keys", json={"name": "aplikasi", "role": "app", "tenant": "test-app"},
                          headers=admin_headers).json()["api_key"]
    assert client.get("/v1/admin/settings/storage", headers={"X-API-Key": app_key}).status_code == 403


def test_trial_page_endpoints(client, admin_headers, samples):
    """Halaman uji coba: preview mengembalikan foto hasil engine, file diambil lewat engine, daftar profile."""
    import base64
    import io

    from PIL import Image

    # preview: foto besar dikompres, hasilnya ikut dikirim sebagai data URL (tidak disimpan)
    big = samples["pass_huge_camera"]
    r = client.post("/v1/analyze/preview", params={"tenant": "test-app", "include_output": True},
                    headers=admin_headers, files={"file": ("besar.jpg", big, "image/jpeg")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["compression"]["passthrough"] is False and body["allow_status_update"] is True
    head, data = body["output_image"].split(",", 1)
    out = base64.b64decode(data)
    assert head.startswith("data:image/") and len(out) <= 1024 * 1024 and Image.open(io.BytesIO(out)).size[0] > 0
    small = client.post("/v1/analyze/preview", params={"include_output": True}, headers=admin_headers,
                        files={"file": ("kecil.jpg", samples["pass_small_ok"], "image/jpeg")}).json()
    assert small["output_image"] is None  # foto kecil tidak disentuh
    plain = client.post("/v1/analyze/preview", headers=admin_headers,
                        files={"file": ("a.jpg", big, "image/jpeg")}).json()
    assert "output_image" not in plain

    # simulasi aplikasi kurir: engine mengembalikan foto, aplikasi menyimpannya ke SILO (object_key), lalu Web UI
    # mengambil file lewat engine (HP tidak perlu akses ke SILO)
    from engine.storage import get_storage

    r = client.post("/v1/analyze", params={"tenant": "test-app"}, headers=admin_headers,
                    files={"file": ("besar.jpg", big, "image/jpeg")},
                    data={"awb": "UJI1", "object_key": "pod/UJI1.jpg"})
    body = r.json()
    pid, out = body["id"], body["output_image"]
    assert out["optimized"] and base64.b64decode(out["base64"])[:2] == b"\xff\xd8" and out["bytes"] < len(big)
    assert body["object"] == {"bucket": "app-photos", "key": "pod/UJI1.jpg"}
    get_storage().put("app-photos", "pod/UJI1.jpg", base64.b64decode(out["base64"]), out["content_type"],
                      allow_foreign=True)
    for variant in ("processed", "original", "thumbnail"):
        f = client.get(f"/v1/photos/{pid}/file", params={"variant": variant}, headers=admin_headers)
        assert f.status_code == 200, (variant, f.text)
        assert f.headers["content-type"].startswith("image/")
        Image.open(io.BytesIO(f.content)).verify()
    assert len(client.get(f"/v1/photos/{pid}/file", headers=admin_headers).content) == out["bytes"]
    assert client.get(f"/v1/photos/{pid}/file", params={"variant": "lain"}, headers=admin_headers).status_code == 422
    assert client.get("/v1/photos/tidak-ada/file", headers=admin_headers).status_code == 404

    other = client.post("/v1/admin/api-keys", json={"name": "lain", "role": "app", "tenant": "tenant-lain"},
                        headers=admin_headers)
    if other.status_code == 200:  # key tenant lain tidak boleh melihat file tenant ini
        assert client.get(f"/v1/photos/{pid}/file", headers={"X-API-Key": other.json()["api_key"]}).status_code == 404

    # daftar profile + mode
    builtin = client.get("/v1/meta/profiles", headers=admin_headers).json()
    assert builtin["tenant"] is None and "pod_recipient" in [p["name"] for p in builtin["profiles"]]
    t = client.get("/v1/meta/profiles", params={"tenant": "test-app"}, headers=admin_headers).json()
    assert t["tenant"] == "test-app" and all(p["mode"] in ("shadow", "warn", "enforce") for p in t["profiles"])
    assert client.get("/v1/meta/profiles", params={"tenant": "tidak-ada"}, headers=admin_headers).status_code == 404


def test_shipped_tenant_configs_are_valid():
    """Config contoh di repo (termasuk tenant uji-coba) harus selalu valid."""
    from pathlib import Path

    import yaml

    from engine.tenant_config import TenantConfig

    files = sorted(Path("config/tenants").glob("*.yaml"))
    assert {f.stem for f in files} >= {"courier-app", "uji-coba"}
    for f in files:
        cfg = TenantConfig.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")))
        cfg.validate_all_profiles()
        assert cfg.code == f.stem
    uji = TenantConfig.model_validate(yaml.safe_load(Path("config/tenants/uji-coba.yaml").read_text()))
    assert uji.sources == [] and uji.retention_days == 30
    assert uji.resolve_profile("pod_document")[1].mode == "enforce"


def test_reject_blocks_status_update_and_enhance_is_opt_in(client, admin_headers, samples):
    """Mode enforce + REJECT -> allow_status_update false. Engine tidak memanipulasi gambar kecuali diaktifkan."""
    from tests.conftest import TENANT_YAML

    h = {**admin_headers, "Content-Type": "application/x-yaml"}
    enforce = TENANT_YAML.replace("mode: warn", "mode: enforce")
    assert client.put("/v1/admin/tenants", content=enforce, headers=h).status_code == 200

    def analyze(sample, **data):
        r = client.post("/v1/analyze", params={"tenant": "test-app", "sync": True, "lang": "id"}, headers=admin_headers,
                        files={"file": ("f.jpg", samples[sample], "image/jpeg")}, data=data)
        assert r.status_code == 200, r.text
        return r.json()

    ok = analyze("pass_sharp_large", awb="AWB901")  # pertama, sebelum ada foto mirip (cek duplikat)
    assert ok["verdict"] == "PASS" and ok["allow_status_update"] is True and ok["courier_message"] is None
    # foto diterima -> engine mengembalikan foto untuk disimpan APLIKASI (foto kecil: apa adanya, tidak dikompres)
    assert ok["output_image"]["optimized"] is False and ok["output_image"]["bytes"] == len(samples["pass_sharp_large"])
    rej = analyze("reject_blur_severe", awb="AWB900")
    assert rej["action"] == "reject" and rej["allow_status_update"] is False and rej["retake_required"] is True
    assert rej["output_image"] is None  # ditolak: tidak ada foto untuk disimpan sebagai foto pengiriman
    assert rej["courier_message"] == ("Wajib foto ulang. Foto buram. "
                                      "Pegang HP dengan stabil, ketuk layar untuk fokus, lalu foto ulang.")
    warn = analyze("warn_blur_mild", awb="AWB902")
    assert warn["action"] == "notify" and warn["allow_status_update"] is True
    assert warn["courier_message"] == "Foto buram. Pegang HP dengan stabil, ketuk layar untuk fokus, lalu foto ulang."

    # foto BESAR yang ditolak: tidak ada foto hasil, dan engine tidak menulis apa pun ke SILO
    import io

    import numpy as np
    from PIL import Image

    from engine.storage import get_storage

    px = np.asarray(Image.open(io.BytesIO(samples["pass_huge_camera"])).convert("RGB")).astype(np.float32)
    noisy_dark = np.clip(px * 0.06 + np.random.default_rng(1).normal(0, 3, px.shape), 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(noisy_dark).save(buf, "JPEG", quality=95)
    r = client.post("/v1/analyze", params={"tenant": "test-app", "sync": True}, headers=admin_headers,
                    files={"file": ("gelap-besar.jpg", buf.getvalue(), "image/jpeg")}, data={"awb": "AWB906"})
    big_rej = r.json()
    assert big_rej["verdict"] == "REJECT" and big_rej["allow_status_update"] is False
    assert big_rej["output_image"] is None
    detail = client.get(f"/v1/photos/{big_rej['id']}", headers=admin_headers).json()
    assert detail["object"] is None and detail["urls"]["processed"] is None
    assert not any(list(get_storage().list(b)) for b in ("photos-processed", "photos-thumb", "photos-upload"))

    # file rusak = foto tidak layak: di mode enforce juga menahan status
    r = client.post("/v1/analyze", params={"tenant": "test-app", "sync": True}, headers=admin_headers,
                    files={"file": ("rusak.jpg", b"bukan gambar", "image/jpeg")}, data={"awb": "AWB905"})
    broken = r.json()
    assert broken["status"] == "error" and broken["allow_status_update"] is False and broken["retake_required"] is True
    assert broken["courier_message"].startswith("Wajib foto ulang. File tidak bisa dibuka sebagai gambar.")

    # ops menyetujui foto yang ditolak -> status boleh diupdate, tidak ada pesan
    r = client.post(f"/v1/photos/{rej['id']}/review", json={"action": "approve"}, headers=admin_headers)
    assert r.status_code == 200
    approved = client.get(f"/v1/photos/{rej['id']}", headers=admin_headers).json()
    assert approved["allow_status_update"] is True and approved["courier_message"] is None

    # preview mengikuti aturan yang sama
    pv = client.post("/v1/analyze/preview", params={"tenant": "test-app"}, headers=admin_headers,
                     files={"file": ("f.jpg", samples["reject_blur_severe"], "image/jpeg")}).json()
    assert pv["allow_status_update"] is False and pv["courier_message"].startswith("Wajib foto ulang.")

    # bawaan: foto gelap tidak dikoreksi (tanpa manipulasi)
    dark = analyze("reject_dark", awb="AWB903")
    assert dark["verdict"] == "REJECT" and dark["allow_status_update"] is False
    # koreksi pencahayaan hanya jika diaktifkan eksplisit per profile
    opt_in = enforce.replace("    mode: enforce\n", "    mode: enforce\n    enhance:\n      enabled: true\n")
    assert client.put("/v1/admin/tenants", content=opt_in, headers=h).status_code == 200
    fixed = analyze("reject_dark", awb="AWB904")
    detail = client.get(f"/v1/photos/{fixed['id']}", headers=admin_headers).json()
    assert detail["enhancement"]["applied"] is True and fixed["allow_status_update"] is True


def test_version_is_visible(client, admin_headers):
    """Versi engine terlihat di healthz, /v1/me, header setiap respons, dan CLI."""
    import subprocess
    import sys

    from engine import __version__

    assert client.get("/healthz").json()["version"] == __version__
    r = client.get("/v1/me", headers=admin_headers)
    assert r.json()["version"] == __version__ and r.headers["X-PQE-Version"] == __version__
    assert client.get("/v1/photos/tidak-ada", headers=admin_headers).headers["X-PQE-Version"] == __version__
    out = subprocess.run([sys.executable, "-m", "engine.cli", "--version"], capture_output=True, text=True)
    assert out.stdout.strip() == f"pqe {__version__}"


def test_engine_archives_to_ocr_bucket(client, admin_headers, samples):
    """ADR 0007: engine mengarsipkan foto di bucket `ocr` per tenant; aplikasi tetap menyimpan salinannya sendiri.

    Diterima -> arsip = foto yang sama dengan output_image. Ditolak -> arsip = foto asli (review & kalibrasi).
    Lewat archive_retention_days (bawaan 520) file arsip dihapus, catatan analisis tetap.
    """
    import base64
    import hashlib
    from datetime import timedelta

    from engine.db import Photo, session_scope, utcnow
    from engine.processor import wait_for_archives
    from engine.storage import get_storage
    from engine.worker import archive_sweep
    from tests.conftest import TENANT_YAML

    h = {**admin_headers, "Content-Type": "application/x-yaml"}
    assert client.put("/v1/admin/tenants", content=TENANT_YAML.replace("mode: warn", "mode: enforce"),
                      headers=h).status_code == 200

    def send(sample, awb):
        r = client.post("/v1/analyze", params={"tenant": "test-app"}, headers=admin_headers,
                        files={"file": ("f.jpg", samples[sample], "image/jpeg")}, data={"awb": awb})
        assert r.status_code == 200, r.text
        return r.json()

    ok, rej = send("pass_huge_camera", "AR1"), send("reject_blur_severe", "AR2")
    wait_for_archives()
    st = get_storage()
    a_ok = client.get(f"/v1/photos/{ok['id']}", headers=admin_headers).json()["archive"]
    a_rej = client.get(f"/v1/photos/{rej['id']}", headers=admin_headers).json()["archive"]
    assert a_ok["bucket"] == "ocr" and a_ok["key"].startswith("test-app/") and a_ok["key"].endswith(f"{ok['id']}.jpg")
    assert st.get("ocr", a_ok["key"]).data == base64.b64decode(ok["output_image"]["base64"])
    assert hashlib.sha256(st.get("ocr", a_rej["key"]).data).hexdigest() == rej["sha256"]  # foto asli
    assert rej["output_image"] is None and rej["allow_status_update"] is False
    # Web UI & evidence memakai arsip walau aplikasi tidak melaporkan lokasinya
    assert client.get(f"/v1/photos/{rej['id']}/file", params={"variant": "thumbnail"},
                      headers=admin_headers).status_code == 200
    assert client.get("/v1/evidence/AR1", headers=admin_headers).json()["count"] == 1
    # bucket aplikasi tetap tidak tersentuh
    assert not list(st.list("app-photos"))

    with session_scope() as s:
        s.get(Photo, ok["id"]).received_at = utcnow() - timedelta(days=521)
    assert archive_sweep(st) == 1
    assert client.get(f"/v1/photos/{ok['id']}", headers=admin_headers).json()["archive"] is None
    assert not any(o.key == a_ok["key"] for o in st.list("ocr"))
    assert client.get(f"/v1/photos/{rej['id']}", headers=admin_headers).json()["archive"] is not None


def test_archive_bucket_editable_via_storage_settings(client, admin_headers, monkeypatch):
    from engine.settings import reset_settings_cache

    monkeypatch.setenv("PQE_SECRET_KEY", "kunci-test")
    reset_settings_cache()
    r = client.put("/v1/admin/settings/storage", json={"archive_bucket": "ocr-lain"}, headers=admin_headers)
    assert r.status_code == 200, r.text
    cfg = client.get("/v1/admin/settings/storage", headers=admin_headers).json()
    field = cfg["fields"]["archive_bucket"]
    assert field["value"] == "ocr-lain" and field["source"] == "database"
