"""Backup ke NAS: salin foto (asli + versi pengiriman) dan dump database secara berkala.

Tujuan bisa berupa:
- folder: NAS di-mount ke PC/server lewat SMB/NFS lalu dipasang ke container (contoh /backup), atau
- s3: NAS dengan layanan S3-compatible (Synology/QNAP/MinIO di NAS).

Diatur lewat halaman "Backup NAS" di Web UI (tersimpan di tabel settings, secret terenkripsi) dan dijalankan
oleh worker. Backup hanya MENYALIN: foto di NAS tidak pernah dihapus engine (kecuali rotasi dump database).
Tata letak di NAS: photos/<bucket>/<key> dan database/pqe-<waktu>.(dump|sqlite).
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from engine import metrics, secretbox
from engine.db import AuditLog, BackupItem, Photo, Setting, as_aware, session_scope, utcnow
from engine.settings import get_settings

log = logging.getLogger(__name__)

KEY = "backup"
STATE_KEY = "backup_state"
SECRET_FIELDS = {"s3_secret_key"}
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "target": "folder",  # folder | s3
    "folder_path": "/backup",
    "s3_endpoint": "",
    "s3_region": "us-east-1",
    "s3_bucket": "",
    "s3_prefix": "",
    "s3_access_key": "",
    "s3_addressing_style": "path",
    "include_upload_originals": True,  # foto asli dari kurir (bucket upload engine)
    "include_app_originals": False,  # foto asli di bucket aplikasi (biasanya sudah dibackup tim infra)
    "include_processed": True,  # foto pengiriman yang diterima (link `stored`)
    "include_thumbs": False,  # bisa dibuat ulang, biasanya tidak perlu
    "include_database": True,
    "photo_interval_minutes": 15,
    "db_interval_hours": 24,
    "db_keep": 14,
}
BOOL_FIELDS = {k for k, v in DEFAULTS.items() if isinstance(v, bool)}
INT_FIELDS = {"photo_interval_minutes": (1, 1440), "db_interval_hours": (1, 720), "db_keep": (1, 365)}
LEASE = timedelta(minutes=15)
PHOTO_BUDGET_SEC = 50  # satu putaran dibatasi supaya worker tetap responsif; sisanya putaran berikutnya


class BackupError(Exception):
    pass


# ---------------------------------------------------------------- konfigurasi

def _row_values() -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(Setting, KEY)
        return dict(row.value or {}) if row else {}


def load_config() -> dict[str, Any]:
    values = _row_values()
    cfg = {**DEFAULTS, **{k: v for k, v in values.items() if k in DEFAULTS}}
    cfg["s3_secret_key"] = secretbox.decrypt(values["s3_secret_key_enc"]) if values.get("s3_secret_key_enc") else None
    return cfg


def validate(values: dict[str, Any]) -> dict[str, Any]:
    """Normalisasi + validasi nilai dari form. Raise ValueError dengan pesan yang bisa ditampilkan."""
    allowed = set(DEFAULTS) | SECRET_FIELDS
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"field tidak dikenal: {sorted(unknown)}")
    out: dict[str, Any] = {}
    for k, v in values.items():
        if k in BOOL_FIELDS:
            out[k] = bool(v)
        elif k in INT_FIELDS:
            lo, hi = INT_FIELDS[k]
            try:
                n = int(v)
            except (TypeError, ValueError) as e:
                raise ValueError(f"{k} harus angka") from e
            if not lo <= n <= hi:
                raise ValueError(f"{k} harus antara {lo} dan {hi}")
            out[k] = n
        else:
            out[k] = str(v).strip() if v is not None else ""
    if out.get("target", "folder") not in ("folder", "s3"):
        raise ValueError("target harus folder atau s3")
    if out.get("folder_path") and not out["folder_path"].startswith("/"):
        raise ValueError("folder_path harus path absolut di dalam container, contoh /backup")
    if out.get("s3_endpoint") and not out["s3_endpoint"].startswith(("http://", "https://")):
        raise ValueError("s3_endpoint harus diawali http:// atau https://")
    if out.get("s3_addressing_style") and out["s3_addressing_style"] not in ("auto", "path", "virtual"):
        raise ValueError("s3_addressing_style harus auto, path, atau virtual")
    return out


def merged(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Konfigurasi tersimpan + nilai form yang belum disimpan (untuk tombol Test)."""
    cfg = load_config()
    for k, v in validate(overrides or {}).items():
        if k in SECRET_FIELDS and not v:
            continue
        cfg[k] = v
    return cfg


def save(values: dict[str, Any], actor: str) -> int:
    clean = validate(values)
    cfg_after = {**load_config(), **{k: v for k, v in clean.items() if k not in SECRET_FIELDS or v}}
    if cfg_after.get("target") == "s3" and cfg_after.get("enabled"):
        if not cfg_after.get("s3_endpoint") or not cfg_after.get("s3_bucket"):
            raise ValueError("tujuan s3 butuh s3_endpoint dan s3_bucket")
    with session_scope() as s:
        row = s.get(Setting, KEY)
        current = dict(row.value or {}) if row else {}
        for k, v in clean.items():
            if k in SECRET_FIELDS:
                if v:
                    current["s3_secret_key_enc"] = secretbox.encrypt(v)
            else:
                current[k] = v
        if row is None:
            row = Setting(key=KEY, value=current, version=1, updated_by=actor)
            s.add(row)
        else:
            row.value = current
            row.version += 1
            row.updated_by = actor
        s.flush()
        s.add(AuditLog(actor=actor, action="settings.backup.update", target=KEY,
                       detail={"changed": sorted(k for k in clean if k not in SECRET_FIELDS or clean[k]),
                               "version": row.version}))
        return row.version


# ---------------------------------------------------------------- tujuan (NAS)

class FolderTarget:
    """NAS yang di-mount (SMB/NFS) sebagai folder di dalam container."""

    def __init__(self, path: str) -> None:
        self.root = Path(path)
        self.label = f"folder {path}"

    def _path(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise BackupError(f"path di luar folder backup: {rel}")
        return p

    def check(self) -> str:
        if not self.root.is_dir():
            raise BackupError(f"folder {self.root} tidak ada di dalam container. Mount NAS ke folder ini "
                              "(docker-compose: NAS_MOUNT) lalu restart engine")
        return "folder ada"

    def size(self, rel: str) -> int | None:
        try:
            return self._path(rel).stat().st_size
        except FileNotFoundError:
            return None

    def put(self, rel: str, data: bytes, content_type: str | None = None) -> None:
        p = self._path(rel)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex[:8]}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)  # atomik: file di NAS tidak pernah setengah jadi
        except PermissionError as e:
            raise BackupError(f"folder {self.root} tidak bisa ditulis engine (user uid {os.getuid()}). Di server: "
                              f"sudo chown -R {os.getuid()} <folder NAS_MOUNT>, atau atur izin share NAS") from e

    def put_file(self, rel: str, local: str) -> None:
        p = self._path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex[:8]}.tmp")
        shutil.copyfile(local, tmp)
        os.replace(tmp, p)

    def read(self, rel: str) -> bytes:
        return self._path(rel).read_bytes()

    def delete(self, rel: str) -> None:
        self._path(rel).unlink(missing_ok=True)

    def list(self, prefix: str) -> list[str]:
        d = self._path(prefix)
        if not d.is_dir():
            return []
        base = prefix.rstrip("/")
        return sorted(f"{base}/{f.name}" for f in d.iterdir() if f.is_file() and not f.name.startswith("."))

    def free_space(self) -> dict[str, int] | None:
        try:
            u = shutil.disk_usage(self.root)
            return {"total": u.total, "free": u.free}
        except OSError:
            return None


class S3Target:
    """NAS dengan layanan S3-compatible."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = cfg["s3_bucket"]
        self.prefix = (cfg.get("s3_prefix") or "").strip("/")
        self.label = f"s3 {cfg['s3_endpoint']}/{self.bucket}" + (f"/{self.prefix}" if self.prefix else "")
        self.client = boto3.client(
            "s3", endpoint_url=cfg["s3_endpoint"], region_name=cfg.get("s3_region") or "us-east-1",
            aws_access_key_id=cfg.get("s3_access_key") or None, aws_secret_access_key=cfg.get("s3_secret_key") or None,
            config=Config(s3={"addressing_style": cfg.get("s3_addressing_style") or "path"},
                          retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=5, read_timeout=60),
        )

    def _key(self, rel: str) -> str:
        return f"{self.prefix}/{rel}" if self.prefix else rel

    def check(self) -> str:
        self.client.head_bucket(Bucket=self.bucket)
        return "bucket ada"

    def size(self, rel: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            return int(self.client.head_object(Bucket=self.bucket, Key=self._key(rel))["ContentLength"])
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def put(self, rel: str, data: bytes, content_type: str | None = None) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._key(rel), Body=data,
                               ContentType=content_type or "application/octet-stream")

    def put_file(self, rel: str, local: str) -> None:
        self.client.upload_file(local, self.bucket, self._key(rel))

    def read(self, rel: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._key(rel))["Body"].read()

    def delete(self, rel: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(rel))

    def list(self, prefix: str) -> list[str]:
        out = []
        base = self._key(prefix.rstrip("/") + "/")
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=base):
            for o in page.get("Contents", []) or []:
                out.append(o["Key"][len(self.prefix) + 1:] if self.prefix else o["Key"])
        return sorted(out)

    def free_space(self) -> dict[str, int] | None:
        return None


def make_target(cfg: dict[str, Any]) -> FolderTarget | S3Target:
    if cfg.get("target") == "s3":
        if not cfg.get("s3_endpoint") or not cfg.get("s3_bucket"):
            raise BackupError("tujuan s3 butuh endpoint dan bucket")
        return S3Target(cfg)
    return FolderTarget(cfg.get("folder_path") or "/backup")


# ---------------------------------------------------------------- pekerjaan backup

def _pending_query():
    return (select(Photo).outerjoin(BackupItem, BackupItem.photo_id == Photo.id)
            .where(BackupItem.photo_id.is_(None), Photo.status.in_(("done", "error"))))


def pending_count() -> int:
    with session_scope() as s:
        q = _pending_query().with_only_columns(func.count(Photo.id))
        return int(s.scalar(q) or 0)


def _files_for(row: dict, cfg: dict, owned: set[str]) -> list[tuple[str, str, str]]:
    files = []
    original_owned = row["bucket"] in owned
    if (original_owned and cfg["include_upload_originals"]) or (not original_owned and cfg["include_app_originals"]):
        files.append(("original", row["bucket"], row["object_key"]))
    if cfg["include_processed"] and row["processed_key"]:
        files.append(("processed", row["processed_bucket"], row["processed_key"]))
    if cfg["include_thumbs"] and row["thumb_key"]:
        files.append(("thumbnail", row["thumb_bucket"], row["thumb_key"]))
    return files


def run_photos(storage, cfg: dict, target, budget_sec: float = PHOTO_BUDGET_SEC, batch: int = 100) -> dict:
    """Salin file foto yang belum ada di NAS. Return {"photos": n, "files": n}."""
    import time

    from engine.storage import ObjectNotFound

    owned = storage.owned_buckets()
    started, photos, files = time.monotonic(), 0, 0
    while time.monotonic() - started < budget_sec:
        with session_scope() as s:
            rows = [{"id": p.id, "bucket": p.bucket, "object_key": p.object_key,
                     "processed_bucket": p.processed_bucket, "processed_key": p.processed_key,
                     "thumb_bucket": p.thumb_bucket, "thumb_key": p.thumb_key}
                    for p in s.scalars(_pending_query().order_by(Photo.received_at).limit(batch))]
        if not rows:
            break
        for row in rows:
            copied = 0
            for kind, bucket, key in _files_for(row, cfg, owned):
                rel = f"photos/{bucket}/{key}"
                try:
                    obj = storage.get(bucket, key)
                except ObjectNotFound:
                    continue  # sudah dihapus (retensi/lifecycle): tidak ada yang bisa disalin
                try:
                    if target.size(rel) == len(obj.data):
                        continue
                    target.put(rel, obj.data, obj.content_type)
                except Exception as e:
                    metrics.BACKUP_ERRORS.labels(kind="photos").inc()
                    raise BackupError(f"gagal menulis ke NAS ({target.label}): {type(e).__name__}: {e}") from e
                metrics.BACKUP_FILES.labels(kind=kind).inc()
                copied += 1
            with session_scope() as s:
                s.merge(BackupItem(photo_id=row["id"], files=copied, backed_up_at=utcnow()))
            photos += 1
            files += copied
            if time.monotonic() - started >= budget_sec:
                break
    return {"photos": photos, "files": files}


def dump_database(dest_dir: str) -> str:
    """Buat dump database ke folder lokal sementara. Return path file."""
    from sqlalchemy.engine import make_url

    url = make_url(get_settings().database_url)
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    if url.get_backend_name() == "sqlite":
        out = os.path.join(dest_dir, f"pqe-{stamp}.sqlite")
        src = sqlite3.connect(url.database)
        try:
            with sqlite3.connect(out) as dst:
                src.backup(dst)  # salinan konsisten walau database sedang dipakai
        finally:
            src.close()
        return out
    if url.get_backend_name() == "postgresql":
        if shutil.which("pg_dump") is None:
            raise BackupError("pg_dump tidak ada di image engine")
        out = os.path.join(dest_dir, f"pqe-{stamp}.dump")
        cmd = ["pg_dump", "--format=custom", "--no-owner", "--file", out, "--dbname", url.database or ""]
        if url.host:
            cmd += ["--host", url.host]
        if url.port:
            cmd += ["--port", str(url.port)]
        if url.username:
            cmd += ["--username", url.username]
        env = {**os.environ, "PGPASSWORD": url.password or ""}  # password tidak terlihat di daftar proses
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            raise BackupError(f"pg_dump gagal: {r.stderr.strip()[:300]}")
        return out
    raise BackupError(f"database {url.get_backend_name()} tidak didukung untuk backup")


def run_database(cfg: dict, target) -> dict:
    with tempfile.TemporaryDirectory(prefix="pqe-dump-") as tmp:
        try:
            path = dump_database(tmp)
            name = os.path.basename(path)
            target.put_file(f"database/{name}", path)
            size = os.path.getsize(path)
        except BackupError:
            metrics.BACKUP_ERRORS.labels(kind="database").inc()
            raise
        except Exception as e:
            metrics.BACKUP_ERRORS.labels(kind="database").inc()
            raise BackupError(f"gagal menyimpan dump ke NAS ({target.label}): {type(e).__name__}: {e}") from e
    metrics.BACKUP_FILES.labels(kind="database").inc()
    dumps = [d for d in target.list("database") if os.path.basename(d).startswith("pqe-")]
    removed = 0
    for old in dumps[: max(0, len(dumps) - int(cfg["db_keep"]))]:  # nama berisi waktu: urut = urut waktu
        target.delete(old)
        removed += 1
    return {"file": name, "bytes": size, "rotated": removed}


# ---------------------------------------------------------------- penjadwalan (dipanggil worker)

def _due(last: str | None, every: timedelta, now) -> bool:
    if not last:
        return True
    from datetime import datetime

    return now - as_aware(datetime.fromisoformat(last)) >= every


def request_run(kind: str, actor: str) -> None:
    """Minta worker menjalankan backup secepatnya (tombol "Jalankan sekarang")."""
    if kind not in ("photos", "database", "all"):
        raise ValueError("kind harus photos, database, atau all")
    with session_scope() as s:
        row = s.get(Setting, STATE_KEY)
        state = dict(row.value or {}) if row else {}
        state["requested"] = kind
        if row is None:
            s.add(Setting(key=STATE_KEY, value=state, updated_by=actor))
        else:
            row.value = state
            row.updated_by = actor
        s.add(AuditLog(actor=actor, action="backup.run_requested", target=KEY, detail={"kind": kind}))


def maybe_run(storage=None) -> dict | None:
    """Jalankan backup jika terjadwal atau diminta. Aman dipanggil berkali-kali dari beberapa worker."""
    from engine.jobs import WORKER_ID
    from engine.storage import get_storage

    cfg = load_config()
    now = utcnow()
    with session_scope() as s:
        row = s.get(Setting, STATE_KEY, with_for_update=True)
        state = dict(row.value or {}) if row else {}
        requested = state.get("requested")
        if not cfg["enabled"] and not requested:
            return None
        lease = state.get("lease_until")
        if lease and lease > now.isoformat() and state.get("lease_owner") != WORKER_ID:
            return None  # worker lain sedang menjalankan backup
        do_photos = requested in ("photos", "all") or (
            cfg["enabled"] and _due(state.get("last_photo_run"), timedelta(minutes=cfg["photo_interval_minutes"]), now))
        do_db = cfg["include_database"] and (requested in ("database", "all") or (
            cfg["enabled"] and _due(state.get("last_db_run"), timedelta(hours=cfg["db_interval_hours"]), now)))
        if not do_photos and not do_db:
            if requested:
                state["requested"] = None
                row.value = state
            return None
        state.update(requested=None, lease_until=(now + LEASE).isoformat(), lease_owner=WORKER_ID)
        if row is None:
            s.add(Setting(key=STATE_KEY, value=state, updated_by="system:backup"))
        else:
            row.value = state

    result: dict[str, Any] = {"started_at": now.isoformat()}
    error = None
    try:
        target = make_target(cfg)
        target.check()
        if do_photos:
            result["photos"] = run_photos(storage or get_storage(), cfg, target)
        if do_db:
            result["database"] = run_database(cfg, target)
    except Exception as e:
        error = str(e)[:500] if isinstance(e, BackupError) else f"{type(e).__name__}: {e}"[:500]
        log.warning("backup NAS gagal: %s", error)
    finally:
        with session_scope() as s:
            row = s.get(Setting, STATE_KEY)
            state = dict(row.value or {})
            done_at = utcnow().isoformat()
            if do_photos and "photos" in result:
                state["last_photo_run"] = done_at
                state["last_photo_result"] = result["photos"]
                state["files_total"] = int(state.get("files_total", 0)) + result["photos"]["files"]
            if do_db and "database" in result:
                state["last_db_run"] = done_at
                state["last_db_result"] = result["database"]
            state["last_error"] = error
            state["last_error_at"] = done_at if error else state.get("last_error_at")
            state["last_run_at"] = done_at
            state.update(lease_until=None, lease_owner=None)
            row.value = state
    try:
        metrics.BACKUP_PENDING.set(pending_count())
    except Exception:  # metrik tidak boleh menggagalkan backup
        pass
    result["error"] = error
    return result


# ---------------------------------------------------------------- untuk Web UI / API

def test(overrides: dict[str, Any] | None = None, storage=None) -> dict:
    """Uji tujuan backup (dengan nilai form yang belum disimpan) dan akses ke bucket sumber."""
    from engine.storage import get_storage

    cfg = merged(overrides)
    checks: list[dict] = []

    def run(name: str, fn) -> None:
        try:
            checks.append({"check": name, "ok": True, "detail": fn()})
        except Exception as e:
            checks.append({"check": name, "ok": False, "detail": f"{type(e).__name__}: {e}"[:300]})

    try:
        target = make_target(cfg)
    except Exception as e:
        return {"ok": False, "target": cfg.get("target"), "checks": [{"check": "Konfigurasi tujuan", "ok": False,
                                                                       "detail": str(e)}]}
    run(f"Tujuan backup ({target.label})", target.check)
    probe = f"_pqe_backup_test/{uuid.uuid4().hex}.txt"

    def write_read_delete() -> str:
        target.put(probe, b"ok", "text/plain")
        if target.read(probe) != b"ok":
            raise BackupError("isi file uji berbeda setelah dibaca ulang")
        target.delete(probe)
        return "tulis, baca, hapus berhasil"

    run("Tulis ke NAS", write_read_delete)
    if isinstance(target, FolderTarget):
        free = target.free_space()
        if free:
            checks.append({"check": "Ruang kosong NAS", "ok": free["free"] > 1024**3,
                           "detail": f"{free['free'] / 1024**3:.1f} GB kosong dari {free['total'] / 1024**3:.1f} GB"})
    st = storage or get_storage()
    sources = (("foto asli (upload)", st.s.engine_bucket_upload), ("foto pengiriman", st.s.engine_bucket_processed))
    for label, b in sources:
        run(f"Baca bucket {label} '{b}'", lambda b=b: st.ping(b) and "bisa diakses")
    if cfg.get("include_database"):
        def db_check() -> str:
            from sqlalchemy.engine import make_url

            backend = make_url(get_settings().database_url).get_backend_name()
            if backend == "postgresql" and shutil.which("pg_dump") is None:
                raise BackupError("pg_dump tidak ada di image engine")
            return f"dump {backend} siap"
        run("Dump database", db_check)
    return {"ok": all(c["ok"] for c in checks), "target": target.label, "checks": checks}


def describe() -> dict:
    cfg = load_config()
    values = _row_values()
    out_cfg = {k: v for k, v in cfg.items() if k not in SECRET_FIELDS}
    out_cfg["s3_secret_key"] = {"set": bool(values.get("s3_secret_key_enc"))}
    with session_scope() as s:
        row = s.get(Setting, STATE_KEY)
        state = dict(row.value or {}) if row else {}
        backed = int(s.scalar(select(func.count()).select_from(BackupItem)) or 0)
    free = None
    if cfg["target"] == "folder":
        free = FolderTarget(cfg["folder_path"]).free_space() if Path(cfg["folder_path"]).is_dir() else None
    state.pop("lease_owner", None)
    return {"config": out_cfg, "state": state, "pending": pending_count(), "backed_up": backed,
            "free_space": free, "secret_key_configured": secretbox.available()}
