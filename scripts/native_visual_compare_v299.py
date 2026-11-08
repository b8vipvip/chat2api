"""Fail-closed original ChatGPT vs native public-renderer screenshot comparison.

No claim of 1:1 from DOM counts or CI simulations. Both screenshots must
be independently captured, full length, request-bound and same viewport.
The real browser comparison is performed after deployment and produces a
machine-readable acceptance report without copying user screenshots into logs.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import zipfile
from pathlib import Path

from PIL import Image, ImageChops, ImageStat, UnidentifiedImageError

REQUEST_ID = re.compile(r"^req_[a-f0-9]{32}$")
STRIP = re.compile(r"^screenshots/(?:assistant|native)_[0-9]{2}\.png$")
MAX_SCREENS = 24
MAX_PIXELS = 10_000_000
MAX_COMPRESSED = 40 * 1024 * 1024
MAX_RAW = 120 * 1024 * 1024


def load_capture(path: str | Path, kind: str) -> dict:
    path = Path(path)
    if path.stat().st_size > MAX_COMPRESSED:
        raise ValueError("screenshot-zip-exceeds-limit")
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest, dict):
            raise ValueError("invalid-capture-manifest")
        request_id = manifest.get("request_id")
        if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
            raise ValueError("unbound-request-id")
        if kind == "original":
            if manifest.get("source") != "existing-chatgpt-page":
                raise ValueError("not-chatgpt-original")
            if manifest.get("request_binding") not in {
                "live-request-marker", "chrome-history-content-sha256-verified",
            }:
                raise ValueError("original-message-identity-unverified")
        elif kind == "native":
            if manifest.get("source") != "native-public-renderer":
                raise ValueError("not-public-native-renderer")
            if manifest.get("native_finality") != "verified-final":
                raise ValueError("native-final-revision-unverified")
            if manifest.get("media_assets", 0) and (
                manifest.get("media_sha256_status") != "verified-sha256"):
                raise ValueError("media-byte-identity-unverified")
        else:
            raise ValueError("invalid-capture-role")
        if manifest.get("truncated") or manifest.get("full_reply_screenshot") is not True:
            raise ValueError("screenshot-capture-incomplete")
        wanted = "assistant" if kind == "original" else "native"
        filenames = sorted(n for n in archive.namelist()
                           if n.startswith("screenshots/") and n.endswith(".png"))
        if (not filenames or len(filenames) > MAX_SCREENS or
                any(not STRIP.fullmatch(n) or
                    not n.startswith(f"screenshots/{wanted}_") for n in filenames)):
            raise ValueError("invalid-screenshot-strip-list")
        if manifest.get("screenshot_count") != len(filenames):
            raise ValueError("screenshot-strip-count-conflict")
        images = []
        used = 0
        for filename in filenames:
            item = archive.getinfo(filename)
            used += item.file_size
            if used > MAX_RAW:
                raise ValueError("screenshot-uncompressed-limit")
            try:
                with Image.open(io.BytesIO(archive.read(filename))) as source:
                    source.load()
                    if (source.width < 8 or source.height < 8 or
                            source.width * source.height > MAX_PIXELS):
                        raise ValueError("screenshot-dimensions-untrusted")
                    images.append(source.convert("RGB"))
            except (UnidentifiedImageError, OSError) as exc:
                raise ValueError("screenshot-png-invalid") from exc
        # A self-declared "full_reply_screenshot" is not evidence of
        # geometry. Compare actual decoded PNG coverage with the bounded
        # CSS capture rectangle. Right-edge or bottom truncation must fail
        # even if both images happen to show identical cropped pixels.
        rect = manifest.get("capture_rect")
        if not isinstance(rect, dict):
            raise ValueError("capture-geometry-proof-missing")
        width, height = rect.get("width"), rect.get("height")
        if (type(width) not in (int, float) or type(height) not in (int, float)
                or not math.isfinite(width) or not math.isfinite(height)
                or not 8 <= width <= 3000 or not 8 <= height <= 38400):
            raise ValueError("capture-geometry-untrusted")
        if any(abs(frame.width-width) > 2 for frame in images) or (
                abs(sum(frame.height for frame in images)-height) >
                2*len(images)):
            raise ValueError("screenshot-pixels-do-not-cover-capture-rect")
        return {"manifest": manifest, "images": images}


def compare(original: dict, native: dict, *, max_mae: float = 1.0,
            max_changed: float = 0.01, channel_delta: int = 12) -> dict:
    orig = original["manifest"]
    dst = native["manifest"]
    request_id = orig["request_id"]
    result = {"request_id": request_id, "decision_owner": "native-visual-parity-v299",
              "status": "rejected", "reason": "", "strips": []}
    if request_id != dst["request_id"]:
        result["reason"] = "request-id-mismatch"
        return result
    if len(original["images"]) != len(native["images"]):
        result["reason"] = "screenshot-strip-count-mismatch"
        return result
    for index, (left, right) in enumerate(zip(original["images"], native["images"])):
        if left.size != right.size:
            result["reason"] = "viewport-or-screenshot-geometry-mismatch"
            result["strips"].append({"index": index, "expected": left.size, "actual": right.size})
            return result
        difference = ImageChops.difference(left, right)
        means = ImageStat.Stat(difference).mean
        mae = sum(means) / len(means)
        red, green, blue = difference.split()
        maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        changed = maximum.point(
            lambda x: 255 if x > channel_delta else 0
        ).histogram()[255] / (left.width * left.height)
        result["strips"].append({"index": index, "width": left.width,
                                 "height": left.height,
                                 "mean_absolute_error": round(mae, 4),
                                 "changed_pixel_ratio": round(changed, 6)})
    if any(x["mean_absolute_error"] > max_mae or
           x["changed_pixel_ratio"] > max_changed for x in result["strips"]):
        result["reason"] = "pixel-diff-exceeds-limit"
        return result
    result["status"] = "passed"
    result["reason"] = "same-request-full-screenshot-and-pixels-verified"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original_zip")
    parser.add_argument("native_zip")
    parser.add_argument("--max-mae", type=float, default=1.0)
    parser.add_argument("--max-changed", type=float, default=0.01)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        if not (0 <= args.max_mae <= 20 and 0 <= args.max_changed <= 0.1):
            raise ValueError("unsafe-acceptance-threshold")
        original=load_capture(args.original_zip, "original")
        native=load_capture(args.native_zip, "native")
        outcome=compare(original,native,max_mae=args.max_mae,
                        max_changed=args.max_changed)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        outcome={"status":"rejected","reason":str(error)[:100],
                 "decision_owner":"native-visual-parity-v299"}
    if args.report:
        args.report.write_text(json.dumps(outcome, indent=2), encoding="utf-8")
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["status"]=="passed" else 1)


if __name__ == "__main__":
    main()
