"""Laporan kalibrasi: bandingkan verdict engine dengan keputusan reviewer, lalu sarankan angka batas & mode.

Sumber kebenaran = review manusia di Web UI:
- `approved` (layak tagih) pada foto REJECT  -> salah tolak (engine terlalu ketat)
- `retake_requested` / `fraud` pada foto PASS -> salah terima (engine terlalu longgar)

Saran hanya dibuat bila datanya cukup (lihat konstanta di bawah). Engine TIDAK mengubah config sendiri: saran
ditampilkan di Web UI (Kalibrasi), `pqe calibration`, dan ringkasan mingguan; admin yang memutuskan.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from engine.db import Photo, session_scope, utcnow

MIN_REVIEWED_FOR_THRESHOLD = 10  # minimal foto REJECT (per alasan) yang sudah direview sebelum menyarankan angka
FALSE_REJECT_SUGGEST = 0.30  # >= 30% foto REJECT karena alasan itu disetujui reviewer -> batas terlalu ketat
MIN_FALSE_ACCEPT = 5  # minimal foto PASS yang diminta foto ulang sebelum menyarankan batas lebih ketat
FALSE_ACCEPT_SUGGEST = 0.10
# naik mode: shadow -> warn -> enforce
PROMOTE = {"shadow": {"to": "warn", "photos": 200, "reviewed": 30, "max_false_reject": 0.05},
           "warn": {"to": "enforce", "photos": 500, "reviewed": 50, "max_false_reject": 0.02}}
BAD_REVIEW = ("retake_requested", "fraud")


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))], 1)


def _mode_and_thresholds(tenant: str, profile: str | None) -> tuple[str | None, dict[str, dict]]:
    from engine.tenants import registry

    try:
        _, p = registry.get(tenant).config.resolve_profile(profile)
    except Exception:
        return None, {}
    return p.mode, {k: {"reject": t.reject, "warn": t.warn} for k, t in p.thresholds.items()}


def report(days: int = 7, tenant: str | None = None) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    cols = (Photo.tenant_code, Photo.profile, Photo.status, Photo.verdict, Photo.review_status, Photo.scores,
            Photo.reasons)
    q = select(*cols).where(Photo.received_at >= since)
    if tenant:
        q = q.where(Photo.tenant_code == tenant)
    groups: dict[tuple[str, str], list] = defaultdict(list)
    with session_scope() as s:
        for row in s.execute(q):
            groups[(row.tenant_code, row.profile or "-")].append(row)
    out = [_group(t, p, rows) for (t, p), rows in sorted(groups.items())]
    return {"days": days, "since": since.isoformat(), "tenant": tenant, "groups": out,
            "suggestions": [s for g in out for s in g["suggestions"]]}


def _group(tenant: str, profile: str, rows: list) -> dict[str, Any]:
    mode, thresholds = _mode_and_thresholds(tenant, None if profile == "-" else profile)
    done = [r for r in rows if r.status == "done"]
    counts = {v: sum(1 for r in done if r.verdict == v) for v in ("PASS", "WARN", "REJECT")}
    counts["ERROR"] = sum(1 for r in rows if r.status == "error")
    reviewed = [r for r in done if r.review_status not in (None, "none")]
    false_reject = [r for r in reviewed if r.verdict == "REJECT" and r.review_status == "approved"]
    false_accept = [r for r in reviewed if r.verdict == "PASS" and r.review_status in BAD_REVIEW]
    rate = round(len(false_reject) / len(reviewed), 3) if reviewed else None
    g = {"tenant": tenant, "profile": profile, "mode": mode, "photos": len(rows), "verdicts": counts,
         "reviewed": len(reviewed), "false_reject": len(false_reject), "false_accept": len(false_accept),
         "false_reject_rate": rate, "reasons": [], "suggestions": []}

    # per alasan REJECT/WARN: berapa yang direview, berapa yang ternyata disetujui
    by_code: dict[tuple[str, str], dict] = {}
    for r in done:
        for x in r.reasons or []:
            if x.get("severity") not in ("reject", "warn"):
                continue
            k = (x.get("code"), x.get("check"))
            e = by_code.setdefault(k, {"code": k[0], "check": k[1], "count": 0, "reject": 0, "reviewed": 0,
                                       "approved": 0, "approved_scores": [], "bad_scores": []})
            e["count"] += 1
            e["reject"] += x.get("severity") == "reject"
            if r.review_status not in (None, "none") and x.get("severity") == "reject":
                e["reviewed"] += 1
                score = (r.scores or {}).get(k[1])
                if r.review_status == "approved":
                    e["approved"] += 1
                    if score is not None:
                        e["approved_scores"].append(float(score))
                elif score is not None:
                    e["bad_scores"].append(float(score))
    for e in sorted(by_code.values(), key=lambda x: -x["count"]):
        approved_scores = e.pop("approved_scores")
        bad_scores = e.pop("bad_scores")
        e["approve_rate"] = round(e["approved"] / e["reviewed"], 2) if e["reviewed"] else None
        g["reasons"].append(e)
        th = thresholds.get(e["check"]) or {}
        if (e["reviewed"] >= MIN_REVIEWED_FOR_THRESHOLD and e["approve_rate"] >= FALSE_REJECT_SUGGEST
                and th.get("reject") is not None):
            # batas baru: di bawah sebagian besar foto yang disetujui, tetapi di atas foto yang memang buruk
            new = _pct(approved_scores, 0.2)
            worst_bad = _pct(bad_scores, 0.8)
            if new is not None and worst_bad is not None:
                new = max(new, worst_bad)
            if new is not None and new < th["reject"]:
                g["suggestions"].append({
                    "kind": "threshold", "tenant": tenant, "profile": profile, "check": e["check"],
                    "field": f"thresholds.{e['check']}.reject", "current": th["reject"], "suggested": new,
                    "message": f"{e['approved']} dari {e['reviewed']} foto yang ditolak karena {e['code']} "
                               f"disetujui reviewer. Turunkan {e['check']}.reject dari {th['reject']:g} ke {new:g}."})

    # salah terima: foto PASS yang diminta foto ulang -> check mana yang skornya paling rendah
    passed_reviewed = [r for r in reviewed if r.verdict == "PASS"]
    fa_rate = len(false_accept) / max(1, len(passed_reviewed))
    if len(false_accept) >= MIN_FALSE_ACCEPT and fa_rate >= FALSE_ACCEPT_SUGGEST:
        lows: dict[str, list[float]] = defaultdict(list)
        for r in false_accept:
            sc = {k: v for k, v in (r.scores or {}).items() if k in thresholds and v is not None}
            if sc:
                k = min(sc, key=sc.get)
                lows[k].append(float(sc[k]))
        if lows:
            check = max(lows, key=lambda k: len(lows[k]))
            th = thresholds[check]
            new = _pct(lows[check], 0.8)
            if th.get("warn") is not None and new is not None and new > th["warn"]:
                g["suggestions"].append({
                    "kind": "threshold", "tenant": tenant, "profile": profile, "check": check,
                    "field": f"thresholds.{check}.warn", "current": th["warn"], "suggested": new,
                    "message": f"{len(false_accept)} foto PASS diminta foto ulang oleh reviewer, sebagian besar "
                               f"karena {check}. Naikkan {check}.warn dari {th['warn']:g} ke {new:g}."})

    promo = PROMOTE.get(mode or "")
    if promo:
        missing = []
        if len(rows) < promo["photos"]:
            missing.append(f"{promo['photos'] - len(rows)} foto lagi")
        if len(reviewed) < promo["reviewed"]:
            missing.append(f"{promo['reviewed'] - len(reviewed)} review lagi")
        ok_rate = rate is not None and rate <= promo["max_false_reject"]
        if not missing and ok_rate:
            g["suggestions"].append({
                "kind": "mode", "tenant": tenant, "profile": profile, "field": "mode", "current": mode,
                "suggested": promo["to"],
                "message": f"Salah tolak {rate:.0%} dari {len(reviewed)} foto yang direview (batas "
                           f"{promo['max_false_reject']:.0%}). {tenant}/{profile} siap naik dari mode {mode} ke "
                           f"{promo['to']}."})
        else:
            why = missing + ([] if ok_rate or rate is None else
                             [f"salah tolak {rate:.0%} masih di atas {promo['max_false_reject']:.0%}"])
            g["mode_readiness"] = {"to": promo["to"], "blocked_by": why}
    return g


def format_text(rep: dict[str, Any]) -> str:
    lines = [f"Kalibrasi {rep['days']} hari terakhir"]
    for g in rep["groups"]:
        v = g["verdicts"]
        lines.append(f"- {g['tenant']}/{g['profile']} (mode {g['mode']}): {g['photos']} foto, PASS {v['PASS']}, "
                     f"WARN {v['WARN']}, REJECT {v['REJECT']}, error {v['ERROR']}; direview {g['reviewed']}, "
                     f"salah tolak {g['false_reject']}, salah terima {g['false_accept']}")
        if g.get("mode_readiness"):
            lines.append(f"    belum siap naik ke {g['mode_readiness']['to']}: "
                         + ", ".join(g["mode_readiness"]["blocked_by"]))
    if rep["suggestions"]:
        lines.append("Saran:")
        lines += [f"- {s['message']}" for s in rep["suggestions"]]
    else:
        lines.append("Belum ada saran perubahan (data belum cukup atau hasil sudah sesuai review).")
    return "\n".join(lines)
