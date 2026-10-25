"""Metadata EXIF: kamera, waktu ambil, GPS, jejak software editing.

Catatan penting: EXIF sering dihapus oleh aplikasi/platform (kompresi, WhatsApp, upload web).
Karena itu EXIF kosong hanya dicatat sebagai info, TIDAK PERNAH menjadi alasan reject.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from engine.analyzers.base import AnalysisContext, CheckResult, Finding
from engine.imaging.loader import LoadedImage

EDITING_SOFTWARE = re.compile(
    r"photoshop|lightroom|gimp|snapseed|picsart|canva|facetune|meitu|pixlr|affinity|vsco|photodirector|airbrush",
    re.I,
)

TAG_MAKE, TAG_MODEL, TAG_SOFTWARE, TAG_DATETIME = 271, 272, 305, 306
IFD_EXIF, IFD_GPS = 0x8769, 0x8825
TAG_DATETIME_ORIGINAL, TAG_OFFSET_ORIGINAL = 36867, 36881


def _clean(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode("utf-8", "ignore")
    s = str(v).strip("\x00 ").strip()
    return s or None


def _parse_dt(value: str | None, offset: str | None, tz: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.strptime(value[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    if offset and re.fullmatch(r"[+-]\d{2}:\d{2}", offset):
        sign = 1 if offset[0] == "+" else -1
        h, m = int(offset[1:3]), int(offset[4:6])
        return dt.replace(tzinfo=timezone(sign * timedelta(hours=h, minutes=m)))
    return dt.replace(tzinfo=ZoneInfo(tz))


def _gps_to_deg(values, ref) -> float | None:
    try:
        d, m, s = (float(x) for x in values)
    except (TypeError, ValueError):
        return None
    deg = d + m / 60 + s / 3600
    if _clean(ref) in ("S", "W"):
        deg = -deg
    return round(deg, 6)


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_meta_datetime(value, tz: str) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if s.isdigit():  # unix epoch (detik / milidetik)
            n = int(s)
            return datetime.fromtimestamp(n / 1000 if n > 10**11 else n, tz=UTC)
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(tz))


def extract_exif(img: LoadedImage, tz: str) -> dict:
    ex = img.exif
    info: dict = {}
    if not ex:
        return info
    info["make"] = _clean(ex.get(TAG_MAKE))
    info["model"] = _clean(ex.get(TAG_MODEL))
    info["software"] = _clean(ex.get(TAG_SOFTWARE))
    sub = ex.get_ifd(IFD_EXIF) or {}
    dt_raw = _clean(sub.get(TAG_DATETIME_ORIGINAL)) or _clean(ex.get(TAG_DATETIME))
    dt = _parse_dt(dt_raw, _clean(sub.get(TAG_OFFSET_ORIGINAL)), tz)
    info["datetime_original"] = dt.isoformat() if dt else None
    gps = ex.get_ifd(IFD_GPS) or {}
    if gps.get(2) and gps.get(4):
        info["gps_lat"] = _gps_to_deg(gps.get(2), gps.get(1))
        info["gps_lng"] = _gps_to_deg(gps.get(4), gps.get(3))
    return {k: v for k, v in info.items() if v is not None}


class ExifAnalyzer:
    name = "exif"

    def __call__(self, img: LoadedImage, ctx: AnalysisContext) -> CheckResult:
        p = ctx.profile
        info = extract_exif(img, ctx.timezone)
        findings: list[Finding] = []

        if not any(k in info for k in ("make", "model", "datetime_original")):
            findings.append(Finding("EXIF_MISSING", "info"))

        if info.get("software") and EDITING_SOFTWARE.search(info["software"]):
            findings.append(Finding("EDITING_SOFTWARE", "risk", 40, {"software": info["software"]}))

        shot = datetime.fromisoformat(info["datetime_original"]) if "datetime_original" in info else None
        if shot and ctx.received_at:
            age_h = (ctx.received_at - shot).total_seconds() / 3600
            if age_h > float(p.param("exif", "max_age_hours", 48)):
                findings.append(Finding("OLD_PHOTO", "risk", 30, {"age_hours": round(age_h, 1)}))

        claimed = parse_meta_datetime(ctx.meta.get("captured_at"), ctx.timezone)
        if shot and claimed:
            diff_h = abs((claimed - shot).total_seconds()) / 3600
            if diff_h > float(p.param("exif", "captured_at_tolerance_hours", 6)):
                findings.append(Finding("CAPTURE_TIME_MISMATCH", "risk", 30, {"diff_hours": round(diff_h, 1)}))

        try:
            lat, lng = float(ctx.meta["lat"]), float(ctx.meta["lng"])
        except (KeyError, TypeError, ValueError):
            lat = lng = None
        if lat is not None and "gps_lat" in info and "gps_lng" in info:
            dist = haversine_km(lat, lng, info["gps_lat"], info["gps_lng"])
            if dist > float(p.param("exif", "gps_tolerance_km", 2)):
                findings.append(Finding("GPS_MISMATCH", "risk", 30, {"distance_km": round(dist, 2)}))

        # Jarak dari alamat tujuan (aplikasi mengirim dest_lat/dest_lng). Posisi foto: GPS EXIF, jika tidak ada
        # posisi HP saat memotret (lat/lng). Tanpa salah satunya, tidak dinilai.
        try:
            dlat, dlng = float(ctx.meta["dest_lat"]), float(ctx.meta["dest_lng"])
        except (KeyError, TypeError, ValueError):
            dlat = dlng = None
        plat, plng = (info.get("gps_lat"), info.get("gps_lng")) if "gps_lat" in info else (lat, lng)
        if dlat is not None and plat is not None and plng is not None:
            dist = haversine_km(dlat, dlng, plat, plng)
            info["dest_distance_km"] = round(dist, 3)
            if dist > float(p.param("exif", "dest_radius_km", 1.0)):
                findings.append(Finding("FAR_FROM_DESTINATION", "risk", 30, {
                    "distance_km": round(dist, 2), "source": "exif" if "gps_lat" in info else "device"}))

        return CheckResult(check=self.name, metrics=info, findings=findings)
