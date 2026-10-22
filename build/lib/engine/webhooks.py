"""Pengiriman webhook ke aplikasi (outbox + retry). Ditandatangani HMAC-SHA256.

Header yang dikirim:
  X-PQE-Event: analysis.completed | review.updated
  X-PQE-Delivery: <id>
  X-PQE-Timestamp: <unix detik>
  X-PQE-Signature: sha256=<hex HMAC(secret, "<timestamp>.<body>")>
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import timedelta

import httpx
from sqlalchemy import select

from engine import metrics
from engine.db import WebhookDelivery, session_scope, utcnow
from engine.tenants import TenantNotFound, registry

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 8


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _secret(sink) -> str | None:
    if sink.secret_env:
        return os.environ.get(sink.secret_env)
    return sink.secret


def deliver_pending(limit: int = 20, client: httpx.Client | None = None) -> int:
    """Kirim webhook yang jatuh tempo. Return jumlah yang dicoba."""
    with session_scope() as s:
        rows = s.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.status == "pending", WebhookDelivery.next_attempt_at <= utcnow())
            .order_by(WebhookDelivery.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        items = [(r.id, r.tenant_code, r.url, r.event, r.payload, r.sink_index) for r in rows]
        for r in rows:  # tunda sementara agar worker lain tidak mengambil yang sama
            r.next_attempt_at = utcnow() + timedelta(seconds=60)
    if not items:
        return 0
    own = client is None
    client = client or httpx.Client(timeout=10)
    try:
        for did, tenant_code, url, event, payload, idx in items:
            ok, err = _send(client, did, tenant_code, url, event, payload, idx)
            with session_scope() as s:
                d = s.get(WebhookDelivery, did)
                d.attempts += 1
                if ok:
                    d.status, d.last_error = "done", None
                    metrics.WEBHOOKS_SENT.labels(result="ok").inc()
                else:
                    d.last_error = err
                    if d.attempts >= MAX_ATTEMPTS:
                        d.status = "dead"
                    else:
                        d.next_attempt_at = utcnow() + timedelta(seconds=min(3600, 15 * 2 ** d.attempts))
                    metrics.WEBHOOKS_SENT.labels(result="error").inc()
    finally:
        if own:
            client.close()
    return len(items)


def _send(client, did, tenant_code, url, event, payload, idx) -> tuple[bool, str | None]:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "photo-quality-engine",
        "X-PQE-Event": event,
        "X-PQE-Delivery": str(did),
        "X-PQE-Timestamp": ts,
    }
    try:
        sinks = registry.get(tenant_code).config.sinks
        secret = _secret(sinks[idx]) if idx < len(sinks) else None
        timeout = sinks[idx].timeout_sec if idx < len(sinks) else 5.0
    except TenantNotFound:
        secret, timeout = None, 5.0
    if secret:
        headers["X-PQE-Signature"] = sign(secret, ts, body)
    try:
        r = client.post(url, content=body, headers=headers, timeout=timeout)
        if 200 <= r.status_code < 300:
            return True, None
        return False, f"HTTP {r.status_code}: {r.text[:300]}"
    except httpx.HTTPError as e:
        return False, f"{type(e).__name__}: {e}"
