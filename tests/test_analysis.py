"""Test analyzer, verdict, dan kompresi pada sampel sintetis (tanpa DB/S3)."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from engine.analyzers import AnalysisContext, DuplicateMatch
from engine.imaging.loader import UnreadableImage, load_image
from engine.pipeline import analyze
from engine.synth import base_scene, to_jpeg
from engine.tenant_config import build_profile


def ctx(profile="pod_recipient", **kw):
    return AnalysisContext(profile=build_profile(profile), received_at=datetime.now(UTC), **kw)


@pytest.mark.parametrize("name", [
    "pass_sharp_large", "pass_huge_camera", "pass_small_ok", "warn_blur_mild", "reject_blur_severe",
    "warn_pixelated_upscaled", "reject_upscaled_blurry", "reject_tiny", "reject_heavy_jpeg", "reject_dark",
    "reject_overexposed", "reject_low_contrast",
])
def test_synthetic_verdicts(samples, name):
    expected = name.split("_")[0].upper()
    r = analyze(samples[name], ctx(), do_compress=False, do_thumbnail=False)
    assert r.verdict.verdict == expected, (name, r.verdict.scores, [x.code for x in r.verdict.reasons])


@pytest.mark.parametrize("name,code", [
    ("reject_blur_severe", "BLURRY"),
    ("reject_tiny", "LOW_RESOLUTION"),
    ("reject_heavy_jpeg", "HEAVY_COMPRESSION"),
    ("warn_pixelated_upscaled", "PIXELATED"),
    ("reject_dark", "TOO_DARK"),
    ("reject_overexposed", "TOO_BRIGHT"),
    ("reject_low_contrast", "LOW_CONTRAST"),
])
def test_reason_codes(samples, name, code):
    r = analyze(samples[name], ctx(), do_compress=False, do_thumbnail=False)
    codes = {x.code for x in r.verdict.reasons}
    resolution = r.checks[1].metrics["sub_scores"]
    # heavy jpeg boleh terbaca sebagai PIXELATED maupun HEAVY_COMPRESSION (keduanya rusak)
    if code == "HEAVY_COMPRESSION":
        assert resolution["HEAVY_COMPRESSION"] < 30 and codes & {"HEAVY_COMPRESSION", "PIXELATED"}
    else:
        assert code in codes, codes


def test_dark_photo_is_not_also_blurry(samples):
    r = analyze(samples["reject_dark"], ctx(), do_compress=False, do_thumbnail=False)
    assert "BLURRY" not in {x.code for x in r.verdict.reasons}


def test_large_photo_compressed_below_target(samples):
    r = analyze(samples["pass_huge_camera"], ctx())
    c = r.compression
    assert len(samples["pass_huge_camera"]) > 1_000_000
    assert not c.passthrough and c.target_met
    assert c.size_bytes <= 1_000_000
    assert c.ssim >= 0.9
    assert max(c.width, c.height) <= 2000
    assert r.thumbnail and len(r.thumbnail) < 60_000


def test_small_photo_not_compressed(samples):
    r = analyze(samples["pass_small_ok"], ctx())
    assert r.compression.passthrough
    assert r.compression.size_bytes == len(samples["pass_small_ok"])


def test_document_profile_keeps_more_detail(samples):
    doc = analyze(samples["pass_huge_camera"], ctx("pod_document"))
    assert doc.compression.size_bytes <= 1_000_000
    assert max(doc.compression.width, doc.compression.height) <= 2400
    assert doc.compression.ssim >= 0.94


def test_png_and_webp_inputs():
    img = base_scene(1600, 1200)
    for fmt in ("PNG", "WEBP"):
        buf = io.BytesIO()
        img.save(buf, fmt, quality=95)
        r = analyze(buf.getvalue(), ctx())
        assert r.image.format == fmt
        assert r.verdict.verdict == "PASS"
        assert r.compression.size_bytes <= 1_000_000


def test_unreadable_file():
    with pytest.raises(UnreadableImage):
        load_image(b"bukan gambar")
    with pytest.raises(UnreadableImage):
        load_image(b"")


def _jpeg_with_exif(software=None, taken: datetime | None = None, gps=None) -> bytes:
    img = base_scene(1600, 1200)
    exif = Image.Exif()
    exif[271] = "Samsung"
    exif[272] = "SM-A155F"
    if software:
        exif[305] = software
    if taken:
        exif.get_ifd(0x8769)[36867] = taken.strftime("%Y:%m:%d %H:%M:%S")
    if gps:
        lat, lng = gps
        exif.get_ifd(0x8825).update({
            1: "S" if lat < 0 else "N", 2: (abs(lat), 0.0, 0.0),
            3: "W" if lng < 0 else "E", 4: (abs(lng), 0.0, 0.0),
        })
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, exif=exif)
    return buf.getvalue()


def test_exif_editing_software_and_old_photo():
    from zoneinfo import ZoneInfo

    old = datetime.now(ZoneInfo("Asia/Jakarta")) - timedelta(days=10)
    data = _jpeg_with_exif(software="Adobe Photoshop 25.0", taken=old)
    r = analyze(data, ctx(), do_compress=False, do_thumbnail=False)
    codes = {x.code for x in r.verdict.reasons}
    assert {"EDITING_SOFTWARE", "OLD_PHOTO"} <= codes
    assert r.verdict.verdict == "WARN"  # keaslian tidak pernah langsung REJECT
    assert r.verdict.scores["authenticity"] == 30
    assert r.exif["make"] == "Samsung"


def test_exif_missing_is_info_only(samples):
    r = analyze(samples["pass_sharp_large"], ctx(), do_compress=False, do_thumbnail=False)
    info = [x for x in r.verdict.reasons if x.code == "EXIF_MISSING"]
    assert info and info[0].severity == "info"
    assert r.verdict.verdict == "PASS"


def test_gps_and_capture_time_mismatch():
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    data = _jpeg_with_exif(taken=now - timedelta(hours=1), gps=(-6.2, 106.8))
    meta = {"lat": -7.25, "lng": 112.75, "captured_at": (now - timedelta(hours=20)).isoformat()}
    r = analyze(data, ctx(meta=meta), do_compress=False, do_thumbnail=False)
    codes = {x.code for x in r.verdict.reasons}
    assert {"GPS_MISMATCH", "CAPTURE_TIME_MISMATCH"} <= codes


def test_duplicate_detection_uses_lookup(samples):
    data = samples["pass_sharp_large"]
    sha = load_image(data).sha256

    def lookup(s, ph, dist):
        return [DuplicateMatch("other", "AWB999", "k", 0, s == sha)]

    r = analyze(data, ctx(meta={"awb": "AWB1"}, find_duplicates=lookup), do_compress=False, do_thumbnail=False)
    assert "DUPLICATE_EXACT" in {x.code for x in r.verdict.reasons}
    assert r.verdict.verdict == "WARN"

    # foto lain untuk resi yang sama bukan masalah
    r2 = analyze(data, ctx(meta={"awb": "AWB999"}, find_duplicates=lookup), do_compress=False, do_thumbnail=False)
    assert "DUPLICATE_EXACT" not in {x.code for x in r2.verdict.reasons}


def test_mode_controls_action(samples):
    from engine.tenant_config import Profile

    base = build_profile("pod_recipient").model_dump()
    for mode, action in (("shadow", "log"), ("warn", "notify"), ("enforce", "reject")):
        p = Profile.model_validate({**base, "mode": mode})
        r = analyze(samples["reject_blur_severe"], AnalysisContext(profile=p), do_compress=False,
                    do_thumbnail=False)
        assert r.verdict.action == action
    p = Profile.model_validate({**base, "mode": "enforce"})
    r = analyze(samples["pass_sharp_large"], AnalysisContext(profile=p), do_compress=False, do_thumbnail=False)
    assert r.verdict.action == "log"


def test_exif_orientation_applied():
    img = base_scene(1600, 1200)
    exif = Image.Exif()
    exif[0x0112] = 6  # rotasi 90 derajat
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    loaded = load_image(buf.getvalue())
    assert (loaded.width, loaded.height) == (1200, 1600)


def test_jpeg_with_tiny_pixel_dimensions_rejected():
    tiny = to_jpeg(base_scene(320, 240), 85)
    r = analyze(tiny, ctx(), do_compress=False, do_thumbnail=False)
    assert r.verdict.verdict == "REJECT"
