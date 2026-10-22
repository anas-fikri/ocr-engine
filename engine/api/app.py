"""Aplikasi FastAPI: API v1, Web UI (/ui), health check, metrik, dokumentasi OpenAPI (/docs)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from importlib import resources

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from engine import __version__
from engine.api import admin, routes
from engine.bootstrap import bootstrap
from engine.db import session_scope
from engine.settings import get_settings
from engine.storage import get_storage

log = logging.getLogger(__name__)

DESCRIPTION = """
API Photo Quality Engine: deteksi foto buram/pecah/gelap, kompresi dinamis, validasi keaslian,
dan bukti pengiriman (evidence) untuk penagihan.

**Autentikasi:** kirim header `X-API-Key: pqe_xxx` (atau `Authorization: Bearer pqe_xxx`).

**Bahasa pesan:** header `Accept-Language: id` / `en`, atau query `?lang=en`.

Panduan integrasi lengkap: `docs/integration-guide.md` di repository.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    if app.state.run_bootstrap:
        bootstrap()
    yield


def create_app(run_bootstrap: bool = True) -> FastAPI:
    app = FastAPI(
        title="Photo Quality Engine",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.state.run_bootstrap = run_bootstrap

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "ERROR", "message": str(exc.detail)}
        return JSONResponse({"error": detail}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        msgs = [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()]
        return JSONResponse({"error": {"code": "VALIDATION_ERROR", "message": "; ".join(msgs)}}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse({"error": {"code": "INTERNAL_ERROR", "message": "terjadi kesalahan di server"}},
                            status_code=500)

    app.include_router(routes.router, tags=["v1"])
    app.include_router(admin.router)

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/ui/")

    @app.get("/healthz", tags=["ops"], summary="Liveness: proses hidup")
    def healthz():
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", tags=["ops"], summary="Readiness: DB & SILO bisa diakses")
    def readyz():
        checks = {}
        try:
            with session_scope() as s:
                s.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as e:
            checks["database"] = f"error: {type(e).__name__}"
        try:
            get_storage().ping(get_settings().engine_bucket_processed)
            checks["storage"] = "ok"
        except Exception as e:
            checks["storage"] = f"error: {type(e).__name__}"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)

    @app.get("/metrics", tags=["ops"], summary="Metrik Prometheus")
    def prom():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    ui_dir = resources.files("engine").joinpath("webui")
    app.mount("/ui", StaticFiles(directory=str(ui_dir), html=True), name="ui")
    return app
