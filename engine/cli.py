"""CLI `pqe`. Jalankan `pqe --help` atau `pqe <perintah> --help`."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import mimetypes
import signal
import sys
import threading
import time
from pathlib import Path

from engine.settings import get_settings


def _setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, get_settings().log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _serve_api(host: str, port: int, run_bootstrap: bool = True) -> None:
    import uvicorn

    from engine.api.app import create_app

    uvicorn.run(create_app(run_bootstrap=run_bootstrap), host=host, port=port, proxy_headers=True,
                forwarded_allow_ips="*", log_level=get_settings().log_level.lower())


def _start_metrics_server() -> None:
    """Endpoint metrik + health untuk proses tanpa API (worker/ingestor)."""
    from prometheus_client import start_http_server

    port = get_settings().metrics_port
    start_http_server(port)
    logging.getLogger("pqe").info("metrik Prometheus di :%d/metrics", port)


def _wait_forever(stop: threading.Event) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        stop.wait(1)


def cmd_run(a) -> None:
    """API + worker + ingestor dalam satu proses (paling mudah untuk PC / uji coba)."""
    from engine.bootstrap import bootstrap
    from engine.ingestor import Ingestor
    from engine.worker import Worker

    bootstrap()
    Worker(concurrency=a.concurrency).start()
    ing = Ingestor()
    threading.Thread(target=ing.run_forever, name="ingestor", daemon=True).start()
    _serve_api(a.host, a.port, run_bootstrap=False)


def cmd_api(a) -> None:
    _serve_api(a.host, a.port)


def cmd_worker(a) -> None:
    from engine.bootstrap import bootstrap
    from engine.worker import Worker

    bootstrap()
    _start_metrics_server()
    w = Worker(concurrency=a.concurrency)
    w.start()
    _wait_forever(w.stop_event)


def cmd_ingestor(a) -> None:
    from engine.bootstrap import bootstrap
    from engine.ingestor import Ingestor

    bootstrap()
    _start_metrics_server()
    ing = Ingestor()
    t = threading.Thread(target=ing.run_forever, daemon=True)
    t.start()
    _wait_forever(ing.stop_event)


def cmd_init(a) -> None:
    from engine.bootstrap import bootstrap

    bootstrap()
    print("database siap, tenant dari", get_settings().tenants_dir, "sudah di-import")


def cmd_create_key(a) -> None:
    from engine.api.auth import create_api_key
    from engine.db import init_db

    init_db()
    raw, k = create_api_key(a.name, a.role, a.tenant, a.customer_id, actor="cli")
    print(f"API key dibuat untuk '{k.name}' (role={k.role}, tenant={k.tenant_code or '-'}).")
    print("Simpan sekarang, nilai ini tidak bisa dilihat lagi:\n")
    print(raw)


def cmd_import_tenants(a) -> None:
    from engine.db import init_db
    from engine.tenants import import_dir

    init_db()
    done = import_dir(Path(a.dir or get_settings().tenants_dir), actor="cli", force=a.force)
    print("di-import:", ", ".join(done) if done else "(tidak ada perubahan; pakai --force untuk menimpa)")


def cmd_validate_tenant(a) -> None:
    from engine.tenants import validate_yaml

    ok = True
    for f in a.files:
        try:
            cfg = validate_yaml(Path(f).read_text(encoding="utf-8"))
            print(f"OK   {f} -> tenant '{cfg.code}', profile: {', '.join(cfg.profile_names())}")
        except Exception as e:
            ok = False
            print(f"FAIL {f}: {e}")
    sys.exit(0 if ok else 1)


def cmd_analyze(a) -> None:
    """Analisis file lokal tanpa server/DB. Cocok untuk mencoba & kalibrasi threshold."""
    from engine.analyzers import AnalysisContext
    from engine.db import utcnow
    from engine.i18n import localize_reasons
    from engine.imaging.loader import UnreadableImage
    from engine.pipeline import analyze
    from engine.tenant_config import build_profile, parse_tenant_yaml

    if a.tenant_file:
        cfg = parse_tenant_yaml(Path(a.tenant_file).read_text(encoding="utf-8"))
        name, profile = cfg.resolve_profile(a.profile)
    else:
        name, profile = a.profile or "default", build_profile(a.profile or "default")
    out_dir = Path(a.out_dir) if a.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for f in a.files:
        p = Path(f)
        try:
            r = analyze(p.read_bytes(), AnalysisContext(profile=profile, received_at=utcnow()))
        except UnreadableImage as e:
            results.append({"file": str(p), "error": f"UNREADABLE_FILE: {e}"})
            continue
        d = r.to_dict()
        d["reasons"] = localize_reasons(d["reasons"], a.lang)
        d["file"], d["profile"] = str(p), name
        if out_dir and r.compression and r.compression.data:
            ext = "webp" if r.compression.content_type == "image/webp" else "jpg"
            (out_dir / f"{p.stem}.compressed.{ext}").write_bytes(r.compression.data)
        results.append(d)
    if a.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return
    for d in results:
        if "error" in d:
            print(f"\n{d['file']}\n  ERROR {d['error']}")
            continue
        c = d["compression"] or {}
        print(f"\n{d['file']}  [profile {d['profile']}]")
        print(f"  verdict : {d['verdict']}")
        print("  skor    : " + ", ".join(f"{k}={v:g}" for k, v in d["scores"].items()))
        for r in d["reasons"]:
            print(f"  - [{r['severity']}] {r['code']}: {r['message']}" + (f" -> {r['suggestion']}" if
                                                                        r['severity'] != 'info' else ""))
        if c:
            note = "tidak dikompres" if c["passthrough"] else f"quality {c['quality']}, SSIM {c['ssim']}"
            print(f"  ukuran  : {c['original_bytes'] / 1024:.0f} KB -> {c['output_bytes'] / 1024:.0f} KB ({note})")
        print(f"  waktu   : {d['timings_ms']['total']} ms")


def cmd_synth(a) -> None:
    from engine.synth import write_samples

    for p in write_samples(Path(a.out_dir)):
        print(p)


def cmd_demo_upload(a) -> None:
    """Upload sampel sintetis ke bucket (dengan metadata) untuk mencoba alur end-to-end."""
    import random

    from engine.storage import get_storage
    from engine.synth import variants

    st = get_storage()
    bucket = a.bucket or st.s.aws_bucket
    if st.s.pqe_env == "production" and not a.force:
        raise SystemExit(f"Ditolak: PQE_ENV=production. demo-upload menulis {len(variants())} foto sintetis ke bucket "
                         f"'{bucket}'. Jangan jalankan di SILO produksi. Untuk uji coba lokal set PQE_ENV=development "
                         "(sudah diatur di docker-compose.yml), atau tambahkan --force jika Anda yakin.")
    samples = variants()
    rnd = random.Random(42)
    for i, (name, data) in enumerate(samples.items()):
        awb = f"DEMO{int(time.time()) % 100000:05d}{i:02d}"
        key = f"{a.prefix}{time.strftime('%Y/%m/%d')}/{awb}_{name}.jpg"
        st.put(bucket, key, data, "image/jpeg", allow_foreign=True, metadata={
            "awb": awb, "photo-type": "pod_document" if "huge" in name else "pod_recipient",
            "courier-id": rnd.choice(["K001", "K002", "K003"]), "branch": rnd.choice(["JKT01", "BDG02"]),
            "customer-id": "DEMO-CUST",
        })
        print(f"s3://{bucket}/{key}")
    print(f"{len(samples)} foto diupload. Engine akan memprosesnya dalam beberapa detik; buka Web UI -> Review.")


def cmd_reconcile(a) -> None:
    from engine.bootstrap import bootstrap
    from engine.ingestor import run_source
    from engine.storage import get_storage
    from engine.tenants import registry

    bootstrap()
    for t in registry.all():
        if a.tenant and t.config.code != a.tenant:
            continue
        for i, src in enumerate(t.config.sources):
            n = run_source(t, i, src, get_storage(), reconcile=True)
            print(f"{t.config.code} source#{i} ({src.type}): {n} foto baru")


def cmd_process(a) -> None:
    from engine.bootstrap import bootstrap
    from engine.worker import Worker

    bootstrap()
    n = Worker().run_until_empty()
    print(f"{n} job diproses")


def cmd_migrate_legacy(a) -> None:
    """Upload folder foto lama ke SILO, pertahankan struktur folder, verifikasi checksum."""
    from engine.storage import get_storage

    src = Path(a.src_dir)
    st = get_storage()
    ok = skipped = failed = 0
    files = [p for p in sorted(src.rglob("*")) if p.is_file()]
    for i, p in enumerate(files, 1):
        rel = p.relative_to(src).as_posix()
        key = f"{a.prefix.rstrip('/') + '/' if a.prefix else ''}{rel}"
        data = p.read_bytes()
        md5 = hashlib.md5(data).hexdigest()
        try:
            try:
                head = st.head(a.bucket, key)
                if head["etag"] == md5:
                    skipped += 1
                    continue
            except Exception:
                pass
            if a.dry_run:
                print(f"[dry-run] {p} -> s3://{a.bucket}/{key}")
                ok += 1
                continue
            st.put(a.bucket, key, data, mimetypes.guess_type(p.name)[0] or "application/octet-stream",
                   {"migrated-from": str(p)}, allow_foreign=True)  # tujuan dipilih eksplisit lewat --bucket
            if st.head(a.bucket, key)["etag"] != md5:
                raise RuntimeError("checksum tidak cocok setelah upload")
            ok += 1
        except Exception as e:
            failed += 1
            print(f"GAGAL {p}: {e}", file=sys.stderr)
        if a.sleep_ms:
            time.sleep(a.sleep_ms / 1000)
        if i % 100 == 0:
            print(f"{i}/{len(files)} ...")
    print(f"selesai: {ok} di-upload, {skipped} sudah ada (checksum sama), {failed} gagal")
    sys.exit(1 if failed else 0)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="pqe", description="Photo Quality Engine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_, description=help_)
        p.set_defaults(fn=fn)
        return p

    p = add("run", cmd_run, "Jalankan API + worker + ingestor dalam satu proses (mode PC/uji coba)")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--concurrency", type=int, default=None, help="Jumlah thread analisis")

    p = add("api", cmd_api, "Jalankan API + Web UI saja")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)

    p = add("worker", cmd_worker, "Jalankan worker analisis saja")
    p.add_argument("--concurrency", type=int, default=None)

    add("ingestor", cmd_ingestor, "Jalankan ingestor (polling SILO / DB) saja")
    add("init", cmd_init, "Siapkan database & import config tenant")

    p = add("create-key", cmd_create_key, "Buat API key")
    p.add_argument("--name", required=True)
    p.add_argument("--role", required=True, choices=["admin", "ops", "viewer", "app", "customer"])
    p.add_argument("--tenant")
    p.add_argument("--customer-id")

    p = add("import-tenants", cmd_import_tenants, "Import config tenant dari folder YAML")
    p.add_argument("--dir")
    p.add_argument("--force", action="store_true", help="Timpa tenant yang sudah ada")

    p = add("validate-tenant", cmd_validate_tenant, "Validasi file config tenant (YAML)")
    p.add_argument("files", nargs="+")

    p = add("analyze", cmd_analyze, "Analisis file foto lokal (tanpa server/DB)")
    p.add_argument("files", nargs="+")
    p.add_argument("--profile", help="Nama profile, contoh pod_recipient / pod_document")
    p.add_argument("--tenant-file", help="Pakai profile dari file config tenant")
    p.add_argument("--lang", default="id", choices=["id", "en"])
    p.add_argument("--json", action="store_true", help="Output JSON lengkap")
    p.add_argument("--out-dir", help="Simpan hasil kompres ke folder ini")

    p = add("synth-samples", cmd_synth, "Buat sampel foto sintetis (buram, pecah, gelap, ...)")
    p.add_argument("out_dir")

    p = add("demo-upload", cmd_demo_upload, "Upload foto contoh ke bucket untuk mencoba alur end-to-end")
    p.add_argument("--bucket", help="Default: AWS_BUCKET")
    p.add_argument("--prefix", default="demo/")
    p.add_argument("--force", action="store_true", help="Izinkan walaupun PQE_ENV=production")

    p = add("reconcile", cmd_reconcile, "Jalankan semua source sekali dengan lookback panjang")
    p.add_argument("--tenant")

    add("process", cmd_process, "Proses semua job di antrean lalu berhenti")

    p = add("migrate-legacy", cmd_migrate_legacy, "Upload folder foto lama ke SILO (dengan verifikasi)")
    p.add_argument("src_dir")
    p.add_argument("--bucket", required=True)
    p.add_argument("--prefix", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--sleep-ms", type=int, default=0, help="Jeda antar file (batasi beban)")

    a = ap.parse_args(argv)
    _setup_logging()
    a.fn(a)


if __name__ == "__main__":
    main()
