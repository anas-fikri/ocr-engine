"""Persiapan saat start: skema DB, kunci admin awal, import config tenant, bucket engine."""

from __future__ import annotations

import logging

from engine.db import init_db
from engine.settings import get_settings
from engine.storage import get_storage
from engine.tenants import import_dir

log = logging.getLogger(__name__)


def bootstrap() -> None:
    s = get_settings()
    init_db()
    if s.bootstrap_admin_key:
        from engine.api.auth import ensure_bootstrap_key

        ensure_bootstrap_key(s.bootstrap_admin_key)
    if not s.s3_event_token:
        log.warning("PQE_S3_EVENT_TOKEN kosong: endpoint /v1/events/s3 menerima request tanpa token. "
                    "Isi nilai acak di produksi.")
    imported = import_dir(s.tenants_dir)
    if imported:
        log.info("tenant di-import dari %s: %s", s.tenants_dir, ", ".join(imported))
    if s.engine_create_buckets:
        st = get_storage()
        es = st.s
        for b in {es.engine_bucket_processed, es.engine_bucket_thumb, es.engine_bucket_upload, es.aws_bucket}:
            try:
                st.ensure_bucket(b)
            except Exception as e:  # SILO belum siap tidak boleh menggagalkan start
                log.warning("tidak bisa memastikan bucket %s: %s", b, e)
