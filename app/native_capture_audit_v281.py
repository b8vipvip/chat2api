"""Private, size-qualified comparison of the two request-owned native captures.

A v0.22.267 production ZIP reported private DOM images=1 vs native images=0;
its sole private <img> was a 12x12 icon (naturalWidth=32). The production
native serializer deliberately filters displayed images smaller than 40x40.
Raw image counts alone are therefore NOT evidence that an assistant picture
was lost. Compare only images whose *displayed* dimensions qualify under the
same filter, and never infer byte identity from dimensions or ordinal order.

This is strictly observational: it cannot change Broker terminal, media
archival, published content, or the independent Worker evidence.
"""
from __future__ import annotations

from typing import Any

TRUSTED_SCOPES = frozenset({
    "roleless_reply_siblings",
    "verified_reply_container",
    "verified_native_assistant_selection",
})
MAX_VISIT = 30000
MIN_IMAGE_WIDTH = 40
MIN_IMAGE_HEIGHT = 40


def _nonnegative_int(value: Any, *, upper: int = MAX_VISIT) -> int | None:
    return value if type(value) is int and 0 <= value <= upper else None


def capture_comparison(
    private_dom: dict | None,
    native_summary: dict | None,
    archived_media_count: int,
    image_identity: dict | None = None,
) -> dict[str, Any]:
    """Summarize same-request evidence without copying text, URLs or DOM HTML.

    A count discrepancy is a *candidate* requiring snapshot timing/byte proof,
    not sufficient evidence that the underlying model omitted an image.
    Incomplete/untrusted forensic scopes never become affirmative comparisons.
    """
    archived = _nonnegative_int(archived_media_count, upper=40)
    if archived is None:
        archived = 0
    result: dict[str, Any] = {
        "status": "unavailable",
        "comparable": False,
        "private_capture_complete": False,
        "private_images_total": None,
        "private_images_displayed_large": None,
        "private_images_small": None,
        "private_images_geometry_unknown": None,
        "native_images": None,
        "native_tables": None,
        "private_tables": None,
        "possible_missing_large_images": None,
        "archived_media": archived,
        "byte_identity_status": (
            str(image_identity.get("status") or "unavailable")[:60]
            if isinstance(image_identity, dict) else "unavailable"
        ),
        "visual_completeness_verified": False,
    }
    if not isinstance(private_dom, dict):
        return result
    result["private_capture_complete"] = bool(
        private_dom.get("complete") is True
        and private_dom.get("scope") in TRUSTED_SCOPES
        and isinstance(private_dom.get("root"), dict)
    )
    if not result["private_capture_complete"]:
        result["status"] = "private-capture-incomplete"
        return result
    if not isinstance(native_summary, dict):
        result["status"] = "native-layout-unavailable"
        return result

    native_images = _nonnegative_int(native_summary.get("images"))
    native_tables = _nonnegative_int(native_summary.get("tables"))
    if native_images is None or native_tables is None:
        result["status"] = "native-layout-unavailable"
        return result
    root = private_dom["root"]
    pending = [root]
    seen = 0
    small = large = unknown = total = 0
    private_tables = 0
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        seen += 1
        if seen > MAX_VISIT:
            result["status"] = "private-tree-over-cap"
            return result
        tag = node.get("tag")
        if tag == "img":
            total += 1
            layout = node.get("layout")
            w = layout.get("w") if isinstance(layout, dict) else None
            h = layout.get("h") if isinstance(layout, dict) else None
            # Forensic DOM report already validates the bounded layout.
            # bool, missing and malformed dimensions are not comparable.
            if (type(w) in (int, float) and type(h) in (int, float)
                    and w >= 0 and h >= 0):
                if w >= MIN_IMAGE_WIDTH and h >= MIN_IMAGE_HEIGHT:
                    large += 1
                else:
                    small += 1
            else:
                unknown += 1
        if tag == "table":
            private_tables += 1
        children = node.get("children")
        if isinstance(children, list):
            pending.extend(children)

    result.update({
        "comparable": True,
        "private_images_total": total,
        "private_images_displayed_large": large,
        "private_images_small": small,
        "private_images_geometry_unknown": unknown,
        "native_images": native_images,
        "native_tables": native_tables,
        "private_tables": private_tables,
        # Only count a candidate disparity from complete, trusted captures.
        # This is NOT an asset map or a hash verification.
        "possible_missing_large_images": max(0, large - native_images),
    })
    if large > native_images:
        result["status"] = "possible-large-image-capture-gap"
    elif unknown:
        result["status"] = "count-comparison-has-unknown-geometry"
    elif native_images > large:
        result["status"] = "native-capture-has-more-image-nodes"
    elif small and not large:
        result["status"] = "only-small-decorative-private-images"
    else:
        result["status"] = "size-qualified-image-counts-agree"
    return result
