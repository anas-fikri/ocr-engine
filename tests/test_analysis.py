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


def ctx(profile="pod_recipient", enhance=False, text_first=True, sharpen=False, **kw):
    """Default tanpa perbaikan otomatis agar deteksi masalah asli bisa diuji; perbaikan diuji terpisah."""
    p = build_profile(profile)
    p = p.model_copy(update={"enhance": p.enhance.model_copy(update={"enabled": enhance, "sharpen_text": sharpen}),
                             "text_first": text_first})
    return AnalysisContext(profile=p, received_at=datetime.now(UTC), **kw)


@pytest.mark.parametrize("name", [
    "pass_sharp_large", "pass_huge_camera", "pass_small_ok", "warn_blur_mild", "reject_blur_severe",
    "warn_pixelated_upscaled", "reject_upscaled_blurry", "reject_tiny", "reject_heavy_jpeg", "reject_dark",
    "reject_overexposed", "reject_low_contrast",
])
def test_synthetic_verdicts(samples, name):
    from engine.analyzers.ocr import ocr_available

    expected = name.split("_")[0].upper()
    if ocr_available():
        if name == "warn_pixelated_upscaled":
            expected = "REJECT"  # teks wajib terbaca: foto pecah yang teksnya tidak terbaca ditolak
        if name in ("reject_dark", "reject_overexposed", "reject_low_contrast", "reject_heavy_jpeg"):
            expected = "WARN"  # teks masih terbaca: masalah kejelasan foto cukup diberi tahu
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


# ---------------- perbaikan otomatis (kurva tonal global, tanpa AI) ----------------

@pytest.mark.parametrize("name", ["reject_dark", "reject_low_contrast"])
def test_enhancement_fixes_lighting(samples, name):
    r = analyze(samples[name], ctx(enhance=True, text_first=False))
    assert r.enhancement["applied"] is True
    assert r.enhancement["verified"] == "global_monotonic_curve"
    assert r.enhancement["before"]["verdict"] == "REJECT"
    assert r.verdict.verdict in ("PASS", "WARN") and not r.retake_required
    # versi kompres dibuat dari hasil perbaikan; hash tetap milik foto original
    assert r.compression.data is not None and not r.compression.passthrough
    assert r.image.sha256 == load_image(samples[name]).sha256


@pytest.mark.parametrize("name", ["reject_blur_severe", "reject_heavy_jpeg", "reject_tiny", "reject_overexposed"])
def test_unfixable_photos_require_retake(samples, name):
    r = analyze(samples[name], ctx(enhance=True, text_first=False))
    assert r.verdict.verdict == "REJECT"
    assert r.retake_required is True
    assert r.to_dict()["retake_required"] is True


def test_enhancement_never_changes_sharpness_score(samples):
    """Koreksi kontras tidak boleh membuat skor ketajaman 'naik palsu'."""
    dark_blur = samples["warn_blur_mild"]
    before = analyze(dark_blur, ctx(enhance=False), do_compress=False, do_thumbnail=False)
    after = analyze(dark_blur, ctx(enhance=True), do_compress=False, do_thumbnail=False)
    assert after.verdict.scores["sharpness"] == before.verdict.scores["sharpness"]


def test_curve_verification_detects_any_content_change(samples):
    import numpy as np
    from PIL import ImageDraw

    from engine.imaging.enhance import build_curve, verify_tonal_only
    from engine.synth import _font

    src = np.asarray(load_image(samples["reject_dark"]).pil).copy()
    lut, ops = build_curve(src)
    assert ops
    out = lut[src]
    assert verify_tonal_only(src, out)
    edited = Image.fromarray(out.copy())
    ImageDraw.Draw(edited).text((900, 1300), "Rp 1.250.000", fill=(250, 250, 250), font=_font(20))
    assert not verify_tonal_only(src, np.asarray(edited))  # teks ditambahkan -> ditolak
    one = out.copy()
    one[100, 100] = 255 - one[100, 100]
    assert not verify_tonal_only(src, one)  # satu piksel diubah -> ditolak
    assert not verify_tonal_only(src, out[:, ::-1].copy())  # geometri berubah -> ditolak


ocr_only = pytest.mark.skipif("not __import__('engine.analyzers.ocr', fromlist=['x']).ocr_available()",
                              reason="Tesseract tidak terpasang")


@ocr_only
@pytest.mark.parametrize("profile", ["pod_recipient", "pod_document", "package", "goods", "location", "default"])
def test_text_must_be_readable_in_every_profile(samples, profile):
    """Target engine: teks di foto harus terbaca, di semua jenis foto."""
    assert "ocr" in build_profile(profile).checks
    good = analyze(samples["pass_sharp_large"], ctx(profile), do_compress=False, do_thumbnail=False)
    ocr = next(c for c in good.checks if c.check == "ocr")
    assert ocr.score >= 65 and ocr.metrics["words"] >= 3
    bad = analyze(samples["reject_blur_severe"], ctx(profile), do_compress=False, do_thumbnail=False)
    # foto sangat buram: teks tidak terbaca (atau Tesseract kehabisan waktu -> OCR_TIMEOUT, tidak menghukum);
    # foto tetap ditolak lewat ketajaman
    assert bad.verdict.verdict == "REJECT"
    assert any(r.code in ("NO_TEXT_DETECTED", "TEXT_UNREADABLE", "OCR_TIMEOUT") for r in bad.verdict.reasons)


@ocr_only
def test_awb_on_photo_counts_as_readable(samples):
    """Nomor resi yang dikirim aplikasi dan terbaca di foto = teks terbaca."""
    r = analyze(samples["pass_small_ok"], ctx(meta={"awb": "AWB 1234 5678 9012"}), do_compress=False,
                do_thumbnail=False)
    m = next(c for c in r.checks if c.check == "ocr").metrics
    assert m["awb_found"] is True
    r = analyze(samples["pass_small_ok"], ctx(meta={"awb": "XYZ999888777"}), do_compress=False, do_thumbnail=False)
    assert next(c for c in r.checks if c.check == "ocr").metrics["awb_found"] is False


@ocr_only
def test_compression_keeps_text_readable(samples, monkeypatch):
    """Foto hasil kompres wajib tetap terbaca; jika turun, kualitas dinaikkan (boleh di atas 1 MB)."""
    from engine.analyzers import ocr as ocr_mod

    r = analyze(samples["pass_huge_camera"], ctx())
    tg = r.compression.text_guard
    assert tg and tg["after"] >= tg["before"] - 8 and tg["raised"] is False

    # paksa hasil kompres pertama "tidak terbaca": penjaga harus menaikkan kualitas/resolusi
    real = ocr_mod.readability
    calls = {"n": 0}

    def fake(img, awb=None, regions=None, full_image=None, **kw):
        if regions is not None:  # pemeriksaan ulang foto hasil kompres
            calls["n"] += 1
            if calls["n"] == 1:
                return {**real(img, awb, regions, full_image, **kw), "score": 10.0}
        return real(img, awb, regions, full_image, **kw)

    monkeypatch.setattr(ocr_mod, "readability", fake)
    r = analyze(samples["pass_huge_camera"], ctx())
    tg = r.compression.text_guard
    assert tg["raised"] is True and tg["steps"][0]["score"] == 10.0 and tg["after"] >= tg["before"] - 8
    assert r.compression.quality is None or r.compression.quality > tg["steps"][0]["quality"] \
        or max(r.compression.width, r.compression.height) > tg["steps"][0]["long_side"]
    # storage tetap terkendali: tidak pernah melewati text_guard_max_bytes (bawaan 1,5 MB)
    assert r.compression.size_bytes <= 1_500_000 and tg["max_bytes"] == 1_500_000


@ocr_only
def test_text_first_softens_other_rejects(samples):
    """Teks terbaca -> gelap/terang/pudar/pecah cukup WARN (kurir diberi tahu, status boleh diupdate).
    Teks tidak terbaca -> tetap REJECT. text_first: false -> aturan lama."""
    r = analyze(samples["reject_dark"], ctx(), do_compress=False, do_thumbnail=False)
    dark = next(x for x in r.verdict.reasons if x.code == "TOO_DARK")
    assert r.verdict.verdict == "WARN" and dark.severity == "warn" and dark.detail["softened"] == "text_readable"
    assert analyze(samples["reject_blur_severe"], ctx(), do_compress=False, do_thumbnail=False).verdict.verdict \
        == "REJECT"
    assert analyze(samples["reject_dark"], ctx(text_first=False), do_compress=False,
                   do_thumbnail=False).verdict.verdict == "REJECT"


def _tilted_card(angle: float) -> bytes:
    """Foto besar kartu/resi yang dipegang miring (kasus nyata dari uji coba)."""
    import numpy as np
    from PIL import ImageDraw, ImageFilter

    from engine.synth import _font

    rng = np.random.default_rng(3)
    img = Image.fromarray(rng.normal(90, 25, (3000, 2250, 3)).clip(0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(6))
    card = Image.new("RGB", (1100, 1700), (245, 245, 245))
    d = ImageDraw.Draw(card)
    d.rectangle([0, 0, 1100, 330], fill=(200, 20, 40))
    d.text((80, 100), "PCP EXPRESS", font=_font(140), fill=(255, 255, 255))
    d.text((90, 1080), "ANAS FIKRI MASYKURI", font=_font(72), fill=(20, 20, 20))
    d.text((90, 1190), "PT PCP EXPRESS INDONESIA", font=_font(52), fill=(60, 60, 60))
    card = card.rotate(angle, expand=True, fillcolor=(0, 0, 0))
    img.paste(card, (350, 450), card.convert("L").point(lambda v: 255 if v > 5 else 0))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


@ocr_only
@pytest.mark.parametrize("angle", [-12, 20])
def test_tilted_text_is_straightened_before_reading(angle):
    from engine.analyzers.ocr import readability

    r = readability(load_image(_tilted_card(angle)))
    assert r["score"] >= 65 and r["words"] >= 5 and abs(abs(r["skew"]) - abs(angle)) <= 3


@ocr_only
def test_text_sharpener_only_when_it_helps(samples):
    """Penajam teks (unsharp mask, tanpa AI) dipakai hanya jika keterbacaan naik; hash foto asli tetap."""
    r = analyze(samples["warn_pixelated_upscaled"], ctx(sharpen=True))
    info = r.enhancement["sharpen_text"]
    assert info["attempted"] is True and info["operation"].startswith("unsharp_mask")
    if info["applied"]:
        assert info["after"] >= info["before"] + 5 and r.output_image is not r.image
    assert r.image.sha256 == load_image(samples["warn_pixelated_upscaled"]).sha256
    clear = analyze(samples["pass_sharp_large"], ctx(sharpen=True), do_compress=False, do_thumbnail=False)
    assert "sharpen_text" not in clear.enhancement  # teks sudah terbaca: tidak disentuh
