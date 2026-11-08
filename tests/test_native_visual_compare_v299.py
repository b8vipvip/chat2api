"""No synthetic CI screenshot may claim real deployed native 1:1.

These fixtures check comparison mechanics and strict refusal conditions only.
"""
import io
import json
import zipfile
from PIL import Image

from scripts.native_visual_compare_v299 import load_capture, compare

RID = "req_" + "a" * 32


def _bundle(tmp_path, role, *, color=(200, 200, 200), request=RID,
            verified=True, truncated=False, count=1, size=(64, 64)):
    original = role == "original"
    manifest = {
        "request_id": request,
        "source": "existing-chatgpt-page" if original else "native-public-renderer",
        "request_binding": "live-request-marker" if original else "owner-message-id-verified",
        "native_finality": "verified-final" if verified else "rejected-final",
        "media_assets": 2,
        "media_sha256_status": "verified-sha256" if verified else "unverified",
        "truncated": truncated,
        "full_reply_screenshot": not truncated,
        "screenshot_count": count,
        "capture_rect": {"width": size[0], "height": size[1]*count},
    }
    path = tmp_path / f"{role}_{color}_{request[-4:]}.zip"
    with zipfile.ZipFile(path, "w") as result:
        result.writestr("manifest.json",json.dumps(manifest))
        for index in range(count):
            image = Image.new("RGB", size, color)
            pixels = io.BytesIO()
            image.save(pixels, format="PNG")
            result.writestr(f"screenshots/{'assistant' if original else 'native'}_{index:02d}.png",
                            pixels.getvalue())
    return path


def test_same_request_complete_verified_pixels_pass_only_real_comparison_metric(tmp_path):
    a = load_capture(_bundle(tmp_path, "original"), "original")
    b = load_capture(_bundle(tmp_path, "native"), "native")
    result = compare(a, b)
    assert result["status"] == "passed"
    assert result["strips"][0]["mean_absolute_error"] == 0
    assert result["strips"][0]["changed_pixel_ratio"] == 0
    assert result["decision_owner"] == "native-visual-parity-v299"


def test_wrong_pixels_fail_even_with_matching_dom_and_asset_counts(tmp_path):
    a=load_capture(_bundle(tmp_path,"original"),"original")
    b=load_capture(_bundle(tmp_path,"native",color=(1,20,100)),"native")
    report=compare(a,b)
    assert report["status"] == "rejected"
    assert report["reason"] == "pixel-diff-exceeds-limit"


def test_cross_request_and_viewport_mismatch_do_not_claim_parity(tmp_path):
    a=load_capture(_bundle(tmp_path,"original"),"original")
    b=load_capture(_bundle(tmp_path,"native",request="req_"+"b"*32),"native")
    assert compare(a,b)["reason"] == "request-id-mismatch"
    c=load_capture(_bundle(tmp_path,"native",size=(80,64)),"native")
    assert compare(a,c)["reason"] == "viewport-or-screenshot-geometry-mismatch"


def test_unverified_original_or_native_is_never_a_valid_witness(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="native-final-revision-unverified"):
        load_capture(_bundle(tmp_path,"native",verified=False),"native")
    with pytest.raises(ValueError, match="screenshot-capture-incomplete"):
        load_capture(_bundle(tmp_path,"original",truncated=True),"original")


def test_v300_declared_full_screenshot_cannot_hide_cropped_width_or_height(tmp_path):
    import pytest
    image=_bundle(tmp_path, "original")
    with zipfile.ZipFile(image, "r") as archive:
        items={name: archive.read(name) for name in archive.namelist()}
    manifest=json.loads(items["manifest.json"])
    manifest["capture_rect"]["width"]=80
    items["manifest.json"]=json.dumps(manifest).encode()
    with zipfile.ZipFile(image, "w") as archive:
        for key, value in items.items():
            archive.writestr(key, value)
    with pytest.raises(ValueError, match="screenshot-pixels-do-not-cover-capture-rect"):
        load_capture(image, "original")

    actual=_bundle(tmp_path, "native")
    with zipfile.ZipFile(actual, "r") as archive:
        items={name: archive.read(name) for name in archive.namelist()}
    manifest=json.loads(items["manifest.json"])
    manifest["capture_rect"]["height"]=80
    items["manifest.json"]=json.dumps(manifest).encode()
    with zipfile.ZipFile(actual, "w") as archive:
        for key, value in items.items():
            archive.writestr(key, value)
    with pytest.raises(ValueError, match="screenshot-pixels-do-not-cover-capture-rect"):
        load_capture(actual, "native")
