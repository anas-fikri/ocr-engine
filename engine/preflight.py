"""Pemeriksaan sebelum go-live production: `pqe preflight`, GET /v1/admin/preflight, dan panel di Web UI -> Sistem.

Setiap pemeriksaan menghasilkan status ok / warn / fail beserta cara memperbaikinya. "fail" berarti belum aman
untuk production; "warn" boleh jalan untuk uji coba tetapi sebaiknya dibereskan.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from engine.settings import get_settings

DEFAULT_SECRETS = {
    "PQE_BOOTSTRAP_ADMIN_KEY": {"pqe_local_admin_change_me"},
    "PQE_SECRET_KEY": {"local-secret-key-change-me"},
    "PQE_S3_EVENT_TOKEN": {"local-event-token"},
}
DEFAULT_DB_PASSWORDS = ("pqe-local-only",)


def _item(group: str, check: str, status: str, detail: str, fix: str = "") -> dict[str, str]:
    return {"group": group, "check": check, "status": status, "detail": detail, "fix": fix}


def run(check_silo: bool = True) -> dict[str, Any]:
    s = get_settings()
    out: list[dict[str, str]] = []

    # ---- konfigurasi & secret
    out.append(_item("Konfigurasi", "Mode lingkungan (PQE_ENV)",
                     "ok" if s.pqe_env == "production" else "warn", s.pqe_env,
                     "Isi PQE_ENV=production di .env server (pagar pengaman perintah demo)."))
    for name, value in (("PQE_BOOTSTRAP_ADMIN_KEY", s.bootstrap_admin_key), ("PQE_SECRET_KEY", s.secret_key),
                        ("PQE_S3_EVENT_TOKEN", s.s3_event_token)):
        if not value:
            status = "warn" if name == "PQE_S3_EVENT_TOKEN" else "fail"
            out.append(_item("Keamanan", name, status, "belum diisi",
                             "Jalankan scripts/prod-setup.sh atau isi nilai acak panjang."))
        elif value in DEFAULT_SECRETS[name] or len(value) < 24:
            out.append(_item("Keamanan", name, "fail", "masih nilai bawaan/terlalu pendek",
                             "Ganti dengan nilai acak (openssl rand -hex 32) lalu restart."))
        else:
            out.append(_item("Keamanan", name, "ok", "terisi"))
    db_url = s.database_url
    if db_url.startswith("sqlite"):
        out.append(_item("Database", "Jenis database", "warn", "SQLite",
                         "Production memakai PostgreSQL (docker-compose.prod.yml sudah menyiapkannya)."))
    elif any(p in db_url for p in DEFAULT_DB_PASSWORDS):
        out.append(_item("Database", "Password PostgreSQL", "fail", "masih password bawaan",
                         "Isi POSTGRES_PASSWORD acak di .env SEBELUM start pertama (atau ubah password user pqe)."))
    else:
        out.append(_item("Database", "PostgreSQL", "ok", "dipakai, password bukan bawaan"))
    out.append(_item("Konfigurasi", "PUBLIC_BASE_URL", "ok" if s.public_base_url.startswith("https://") else "warn",
                     s.public_base_url,
                     "Pakai alamat https:// yang dibuka aplikasi & pelanggan (docker-compose.prod.yml)."))

    # ---- SILO
    from engine.storage import get_storage

    st = get_storage()
    cfg = st.s
    if not cfg.aws_access_key_id or not cfg.aws_secret_access_key:
        out.append(_item("SILO", "Kredensial", "fail", "access key / secret key belum diisi",
                         "Web UI -> Pengaturan SILO: isi key, Test koneksi, Simpan."))
    elif check_silo:
        def probe(name: str, fn, fix: str) -> None:
            try:
                out.append(_item("SILO", name, "ok", fn()))
            except Exception as e:
                out.append(_item("SILO", name, "fail", f"{type(e).__name__}: {e}"[:250], fix))

        def read_app() -> str:
            first = next(iter(st.list(cfg.aws_bucket)), None)
            return "bisa dibaca" if first is not None else "bisa dibaca (kosong)"

        probe(f"Baca bucket aplikasi '{cfg.aws_bucket}'", read_app, "Cek endpoint, key, dan izin baca bucket aplikasi.")
        for b in (cfg.engine_bucket_upload, cfg.engine_bucket_processed, cfg.engine_bucket_thumb):
            def write(b=b) -> str:
                key = f"_pqe_preflight/{uuid.uuid4().hex}.txt"
                st.put(b, key, b"ok", "text/plain")
                st.delete(b, key)
                return "tulis & hapus berhasil"
            probe(f"Tulis bucket engine '{b}'", write,
                  f"Buat bucket {b} di SILO dan beri key engine izin tulis (docs/operations.md bagian 4).")
    out.append(_item("SILO", "Pagar pengaman bucket aplikasi", "ok",
                     f"engine tidak bisa menulis/menghapus '{cfg.aws_bucket}' "
                     f"(hanya {', '.join(sorted(st.owned_buckets()))})"))

    # ---- tenant
    from engine.tenants import registry

    tenants = registry.all(enabled_only=False)
    if not tenants:
        out.append(_item("Tenant", "Tenant terdaftar", "fail", "belum ada tenant",
                         "Web UI -> Tenant, atau pqe import-tenants."))
    for t in tenants:
        if not t.enabled:
            continue
        modes = {n: t.config.resolve_profile(n)[1].mode for n in t.config.profile_names()}
        enforce = sorted(n for n, m in modes.items() if m == "enforce")
        if t.config.code == "uji-coba":
            out.append(_item("Tenant", "uji-coba", "ok",
                             "untuk halaman Uji Coba Kamera (mode enforce, retensi 30 hari)",
                             "Nonaktifkan lewat Web UI -> Tenant setelah UAT selesai."))
        elif enforce:
            out.append(_item("Tenant", t.config.code, "warn", f"mode enforce pada: {', '.join(enforce)}",
                             "Mulai production dengan mode shadow, naikkan ke warn/enforce setelah kalibrasi."))
        else:
            out.append(_item("Tenant", t.config.code, "ok", "mode " + ", ".join(sorted(set(modes.values())))))

    # ---- backup NAS
    from engine import backup

    b = backup.describe()
    bc, bs = b["config"], b["state"]
    if not bc["enabled"]:
        out.append(_item("Backup NAS", "Jadwal backup", "fail", "belum aktif",
                         "Web UI -> Backup NAS: pilih tujuan, Test NAS, aktifkan, Simpan."))
    elif bs.get("last_error"):
        out.append(_item("Backup NAS", "Backup terakhir", "fail", bs["last_error"][:250],
                         "Lihat Web UI -> Backup NAS."))
    else:
        last = bs.get("last_photo_run")
        stale = not last or datetime.fromisoformat(last) < datetime.now(UTC) - timedelta(
            minutes=bc["photo_interval_minutes"] * 4)
        out.append(_item("Backup NAS", "Backup foto", "warn" if stale else "ok",
                         f"terakhir: {last or 'belum pernah'}, belum tersalin: {b['pending']}",
                         "Pastikan worker berjalan; klik Backup foto sekarang."))
        if bc["include_database"]:
            out.append(_item("Backup NAS", "Backup database", "ok" if bs.get("last_db_run") else "warn",
                             f"terakhir: {bs.get('last_db_run') or 'belum pernah'}", "Klik Backup database sekarang."))
    if bc["target"] == "folder" and bc["folder_path"] == "/backup" and b.get("free_space") is None:
        out.append(_item("Backup NAS", "Folder NAS", "warn", "/backup belum ter-mount",
                         "Mount share NAS di server dan isi NAS_MOUNT di .env."))

    # ---- worker & OCR
    from engine.db import session_scope
    from engine.jobs import queue_stats

    with session_scope() as sess:
        q = queue_stats(sess)
    out.append(_item("Worker", "Antrean", "ok" if q["dead"] == 0 else "warn",
                     f"menunggu {q['queued']}, gagal permanen {q['dead']}", "Web UI -> Sistem -> Job gagal permanen."))
    try:
        from engine.analyzers.ocr import _tesseract

        engine_ocr, lang = _tesseract()
        out.append(_item("Worker", "OCR (Tesseract)", "ok" if engine_ocr else "warn",
                         f"bahasa {lang}" if engine_ocr else "tidak tersedia",
                         "Pakai image engine dengan WITH_OCR=true."))
    except Exception as e:  # OCR opsional
        out.append(_item("Worker", "OCR (Tesseract)", "warn", str(e)[:200]))

    counts = {k: sum(1 for i in out if i["status"] == k) for k in ("ok", "warn", "fail")}
    return {"ready": counts["fail"] == 0, "counts": counts, "checks": out}
