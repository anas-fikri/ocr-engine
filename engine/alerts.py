"""Ringkasan harian & alert lonjakan foto ditolak, dikirim ke webhook chat (Slack / Google Chat / Teams / Mattermost)
atau ke endpoint JSON milik tim sendiri.

- Ringkasan harian (jam lokal `daily_hour`): jumlah foto, PASS/WARN/REJECT/error, alasan terbanyak, cabang & kurir
  dengan tolakan tertinggi, waktu proses. Setiap Senin ditambah ringkasan kalibrasi 7 hari (engine/calibration.py).
- Alert lonjakan (dicek tiap 15 menit): tingkat tolak cabang/kurir 24 jam terakhir jauh di atas 7 hari sebelumnya,
  atau banyak foto gagal diproses. Satu subjek paling sering satu alert per 24 jam.

Konfigurasi di Web UI (Laporan & alert) dan disimpan di tabel settings; URL webhook disimpan terenkripsi
(PQE_SECRET_KEY) karena URL webhook chat berfungsi sebagai kunci.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import case, func, select

from engine import secretbox
from engine.db import AuditLog, Photo, Setting, session_scope, utcnow

log = logging.getLogger(__name__)

KEY = "alerts"
STATE_KEY = "alerts_state"
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "format": "text",  # text = {"text": "..."} (Slack/Google Chat/Teams/Mattermost) | json = data lengkap
    "daily_hour": 7,  # jam lokal ringkasan harian dikirim
    "timezone": "Asia/Jakarta",
    "spike_alerts": True,
    "weekly_calibration": True,
}
SPIKE_MIN_PHOTOS = 10  # minimal foto 24 jam terakhir sebelum cabang/kurir bisa dianggap melonjak
SPIKE_FACTOR = 2.0  # tingkat tolak >= 2x rata-rata 7 hari sebelumnya ...
SPIKE_MIN_DIFF = 0.15  # ... dan minimal 15 poin persen lebih tinggi
ERROR_SPIKE = 5  # foto gagal diproses dalam 24 jam
SPIKE_CHECK_SEC = 900
_last_spike_check = 0.0


# ---------------------------------------------------------------- konfigurasi

def _get(key: str) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(Setting, key)
        return dict(row.value or {}) if row else {}


def _put(key: str, value: dict[str, Any], actor: str = "engine") -> int:
    with session_scope() as s:
        row = s.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=value, version=1, updated_by=actor)
            s.add(row)
        else:
            row.value, row.version, row.updated_by = value, row.version + 1, actor
        s.flush()
        return row.version


def load_config() -> dict[str, Any]:
    values = _get(KEY)
    cfg = {**DEFAULTS, **{k: v for k, v in values.items() if k in DEFAULTS}}
    cfg["webhook_url"] = secretbox.decrypt(values["webhook_url_enc"]) if values.get("webhook_url_enc") else None
    return cfg


def validate(values: dict[str, Any]) -> dict[str, Any]:
    unknown = set(values) - set(DEFAULTS) - {"webhook_url"}
    if unknown:
        raise ValueError(f"field tidak dikenal: {sorted(unknown)}")
    out: dict[str, Any] = {}
    for k, v in values.items():
        if k in ("enabled", "spike_alerts", "weekly_calibration"):
            out[k] = bool(v)
        elif k == "daily_hour":
            try:
                out[k] = int(v)
            except (TypeError, ValueError) as e:
                raise ValueError("daily_hour harus angka 0-23") from e
            if not 0 <= out[k] <= 23:
                raise ValueError("daily_hour harus angka 0-23")
        elif k == "format":
            if v not in ("text", "json"):
                raise ValueError("format harus text atau json")
            out[k] = v
        elif k == "timezone":
            try:
                ZoneInfo(str(v))
            except Exception as e:
                raise ValueError(f"timezone tidak dikenal: {v}") from e
            out[k] = str(v)
        elif k == "webhook_url":
            v = (v or "").strip()
            if v and not v.startswith(("https://", "http://")):
                raise ValueError("webhook_url harus diawali https://")
            out[k] = v
    return out


def save(values: dict[str, Any], actor: str) -> int:
    clean = validate(values)
    current = _get(KEY)
    for k, v in clean.items():
        if k == "webhook_url":
            if v:
                current["webhook_url_enc"] = secretbox.encrypt(v)  # raise SecretKeyMissing tanpa PQE_SECRET_KEY
        else:
            current[k] = v
    if current.get("enabled") and not current.get("webhook_url_enc"):
        raise ValueError("isi webhook_url sebelum mengaktifkan laporan & alert")
    version = _put(KEY, current, actor)
    with session_scope() as s:
        s.add(AuditLog(actor=actor, action="settings.alerts.update", target=KEY,
                       detail={"changed": sorted(clean), "version": version}))
    return version


def describe() -> dict[str, Any]:
    cfg = load_config()
    out = {k: v for k, v in cfg.items() if k != "webhook_url"}
    out["webhook_url"] = {"set": bool(cfg["webhook_url"])}
    return {"config": out, "state": _get(STATE_KEY), "secret_key_configured": secretbox.available()}


# ---------------------------------------------------------------- data

def _rate(rejects: int, total: int) -> float:
    return rejects / total if total else 0.0


def summary(hours: int = 24, tenant: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    since = now - timedelta(hours=hours)
    base = [Photo.received_at >= since, Photo.received_at < now]
    if tenant:
        base.append(Photo.tenant_code == tenant)
    is_reject = case((Photo.verdict == "REJECT", 1), else_=0)
    with session_scope() as s:
        by = dict(s.execute(select(Photo.verdict, func.count()).where(*base, Photo.status == "done")
                            .group_by(Photo.verdict)).all())
        errors = s.scalar(select(func.count()).select_from(Photo).where(*base, Photo.status == "error")) or 0
        durations = [d for (d,) in s.execute(select(Photo.duration_ms).where(*base, Photo.duration_ms.is_not(None)))]
        rows = s.execute(select(Photo.reasons).where(*base, Photo.verdict.in_(("WARN", "REJECT")))).all()

        def top(col):
            q = (select(Photo.tenant_code, col, func.count(), func.sum(is_reject))
                 .where(*base, Photo.status == "done", col.is_not(None)).group_by(Photo.tenant_code, col))
            items = [{"tenant": t, "key": k, "photos": n, "rejects": int(r or 0),
                      "reject_rate": round(_rate(r or 0, n), 3)}
                     for t, k, n, r in s.execute(q) if n >= SPIKE_MIN_PHOTOS]
            return sorted(items, key=lambda x: (-x["reject_rate"], -x["photos"]))[:5]

        branches, couriers = top(Photo.branch), top(Photo.courier_id)
    reasons: dict[str, int] = {}
    for (rs,) in rows:
        for x in rs or []:
            if x.get("severity") in ("reject", "warn"):
                reasons[x["code"]] = reasons.get(x["code"], 0) + 1
    total = sum(by.values())
    durations.sort()
    return {
        "from": since.isoformat(), "to": now.isoformat(), "tenant": tenant, "photos": total + errors,
        "verdicts": {v: int(by.get(v, 0)) for v in ("PASS", "WARN", "REJECT")}, "errors": int(errors),
        "reject_rate": round(_rate(by.get("REJECT", 0), total), 3),
        "top_reasons": sorted(({"code": k, "count": v} for k, v in reasons.items()), key=lambda x: -x["count"])[:5],
        "top_branches": branches, "top_couriers": couriers,
        "duration_ms": {"p50": durations[len(durations) // 2] if durations else None,
                        "p95": durations[int(len(durations) * 0.95)] if durations else None},
    }


def spikes(now: datetime | None = None) -> list[dict[str, Any]]:
    """Cabang/kurir yang tingkat tolaknya 24 jam terakhir melonjak dibanding 7 hari sebelumnya, plus error."""
    now = now or utcnow()
    day, week = now - timedelta(hours=24), now - timedelta(days=8)
    is_reject = case((Photo.verdict == "REJECT", 1), else_=0)
    is_recent = case((Photo.received_at >= day, 1), else_=0)
    out: list[dict[str, Any]] = []
    with session_scope() as s:
        for kind, col in (("branch", Photo.branch), ("courier", Photo.courier_id)):
            q = (select(Photo.tenant_code, col, func.sum(is_recent), func.sum(is_recent * is_reject), func.count(),
                        func.sum(is_reject))
                 .where(Photo.received_at >= week, Photo.received_at < now, Photo.status == "done", col.is_not(None))
                 .group_by(Photo.tenant_code, col))
            for t, key, n_day, r_day, n_all, r_all in s.execute(q):
                n_day, r_day = int(n_day or 0), int(r_day or 0)
                n_base, r_base = int(n_all) - n_day, int(r_all or 0) - r_day
                if n_day < SPIKE_MIN_PHOTOS:
                    continue
                rate, base = _rate(r_day, n_day), _rate(r_base, n_base)
                if rate >= SPIKE_FACTOR * base and rate - base >= SPIKE_MIN_DIFF:
                    out.append({"kind": kind, "tenant": t, "key": key, "photos": n_day, "rejects": r_day,
                                "reject_rate": round(rate, 3), "baseline_rate": round(base, 3),
                                "baseline_photos": n_base})
        errs = s.execute(select(Photo.tenant_code, func.count()).where(Photo.received_at >= day,
                                                                       Photo.status == "error")
                         .group_by(Photo.tenant_code)).all()
    out += [{"kind": "errors", "tenant": t, "key": t, "photos": int(n)} for t, n in errs if n >= ERROR_SPIKE]
    return out


# ---------------------------------------------------------------- teks

def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def format_digest(sm: dict[str, Any], sp: list[dict[str, Any]], calibration: dict | None = None) -> str:
    v = sm["verdicts"]
    lines = [f"*Photo Quality: ringkasan 24 jam* ({sm['photos']} foto)",
             f"PASS {v['PASS']} · WARN {v['WARN']} · REJECT {v['REJECT']} ({_pct(sm['reject_rate'])}) · "
             f"error {sm['errors']}"]
    if sm["duration_ms"]["p50"] is not None:
        lines.append(f"Waktu proses: median {sm['duration_ms']['p50']} ms, p95 {sm['duration_ms']['p95']} ms")
    if sm["top_reasons"]:
        lines.append("Alasan terbanyak: " + ", ".join(f"{r['code']} ({r['count']})" for r in sm["top_reasons"]))
    for label, items in (("Cabang", sm["top_branches"]), ("Kurir", sm["top_couriers"])):
        bad = [x for x in items if x["rejects"]]
        if bad:
            lines.append(f"{label} dengan tolakan tertinggi: " + ", ".join(
                f"{x['key']} {_pct(x['reject_rate'])} dari {x['photos']}" for x in bad[:3]))
    if sp:
        lines.append("")
        lines.append(format_spikes(sp))
    if calibration is not None:
        from engine.calibration import format_text

        lines += ["", format_text(calibration)]
    return "\n".join(lines)


def format_spikes(sp: list[dict[str, Any]]) -> str:
    lines = ["*Photo Quality: perlu dicek*"]
    for x in sp:
        if x["kind"] == "errors":
            lines.append(f"- {x['tenant']}: {x['photos']} foto gagal diproses dalam 24 jam (cek log engine)")
        else:
            who = "Cabang" if x["kind"] == "branch" else "Kurir"
            lines.append(f"- {who} {x['key']} ({x['tenant']}): {_pct(x['reject_rate'])} foto ditolak dari "
                         f"{x['photos']} foto 24 jam terakhir, biasanya {_pct(x['baseline_rate'])}")
    return "\n".join(lines)


# ---------------------------------------------------------------- kirim

def send(cfg: dict[str, Any], event: str, text: str, data: dict[str, Any],
         client: httpx.Client | None = None) -> None:
    if not cfg.get("webhook_url"):
        raise ValueError("webhook_url belum diisi")
    body = {"text": text} if cfg.get("format", "text") == "text" else {"event": event, "text": text, "data": data}
    c = client or httpx.Client(timeout=10)
    try:
        r = c.post(cfg["webhook_url"], json=body)
        r.raise_for_status()
    finally:
        if client is None:
            c.close()


def send_test(values: dict[str, Any] | None = None, client: httpx.Client | None = None) -> dict[str, Any]:
    cfg = load_config()
    for k, v in validate(values or {}).items():
        if k != "webhook_url" or v:
            cfg[k] = v
    sm = summary()
    text = "Tes dari Photo Quality Engine.\n\n" + format_digest(sm, spikes())
    send(cfg, "pqe.test", text, {"summary": sm}, client)
    return {"ok": True, "text": text}


def maybe_run(now: datetime | None = None, client: httpx.Client | None = None) -> str | None:
    """Dipanggil maintenance loop worker. Return jenis pesan yang dikirim (untuk test/log)."""
    global _last_spike_check
    cfg = load_config()
    if not cfg["enabled"] or not cfg["webhook_url"]:
        return None
    now = now or utcnow()
    local = now.astimezone(ZoneInfo(cfg["timezone"]))
    state = _get(STATE_KEY)
    before = dict(state)
    sent = None
    if local.hour >= cfg["daily_hour"] and state.get("last_digest_date") != local.date().isoformat():
        sm, sp = summary(now=now), spikes(now)
        calib = None
        if cfg["weekly_calibration"] and local.weekday() == 0:
            from engine.calibration import report

            calib = report(days=7)
        send(cfg, "pqe.digest", format_digest(sm, sp, calib), {"summary": sm, "spikes": sp, "calibration": calib},
             client)
        state["last_digest_date"] = local.date().isoformat()
        state["alerted"] = {**state.get("alerted", {}), **{_subject(x): now.isoformat() for x in sp}}
        sent = "digest"
    elif cfg["spike_alerts"] and (_last_spike_check == 0 or now.timestamp() - _last_spike_check >= SPIKE_CHECK_SEC):
        _last_spike_check = now.timestamp()
        alerted = {k: v for k, v in state.get("alerted", {}).items()
                   if now - datetime.fromisoformat(v) < timedelta(hours=24)}
        fresh = [x for x in spikes(now) if _subject(x) not in alerted]
        if fresh:
            send(cfg, "pqe.alert", format_spikes(fresh), {"spikes": fresh}, client)
            alerted.update({_subject(x): now.isoformat() for x in fresh})
            sent = "alert"
        state["alerted"] = alerted
    if sent:
        state[f"last_{sent}_at"] = now.isoformat()
    if state != before:
        _put(STATE_KEY, state)
    return sent


def safe_error(e: Exception) -> str:
    """Alasan gagal tanpa URL webhook."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    return type(e).__name__ if isinstance(e, httpx.HTTPError) else f"{type(e).__name__}: {e}"


def _subject(x: dict[str, Any]) -> str:
    return f"{x['kind']}:{x['tenant']}:{x['key']}"
