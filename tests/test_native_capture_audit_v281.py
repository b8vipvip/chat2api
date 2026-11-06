"""v0.22.267 evidence: 12x12 toolbar image is not a lost assistant raster."""
from __future__ import annotations

from pathlib import Path

from app.native_capture_audit_v281 import capture_comparison

ROOT = Path(__file__).resolve().parents[1]


def forensic(images, *, complete=True, scope="verified_native_assistant_selection"):
    return {
        "schema": "request-native-dom-evidence-v1",
        "request_id": "req_20a00da197294045ac8929dd8559eb9e",
        "complete": complete,
        "scope": scope,
        "root": {"tag": "reply", "children": [
            {"tag": "img", "layout": {"w": w, "h": h},
             "image": {"natural_width": nw, "natural_height": nh}}
            for w, h, nw, nh in images
        ] + [{"tag": "table"}]},
        "counts": {"images": len(images), "tables": 1},
    }


def summary(images=0):
    return {"images": images, "tables": 1, "nodes": 4}


def test_req_20a_size_qualified_filters_12px_icon_not_real_missing_photo():
    # Private forensic image is rendered at 12x12 (intrinsic 32x32),
    # official native serializer deliberately needs >=40x40 displayed.
    # An unrelated 337x450 archived JPEG is not a byte-proofed DOM slot.
    result = capture_comparison(
        forensic([(12, 12, 32, 32)]), summary(images=0),
        archived_media_count=1,
        image_identity={"status": "unavailable"},
    )
    assert result["comparable"] is True
    assert result["status"] == "only-small-decorative-private-images"
    assert result["private_images_total"] == 1
    assert result["private_images_small"] == 1
    assert result["private_images_displayed_large"] == 0
    assert result["native_images"] == 0
    assert result["possible_missing_large_images"] == 0
    assert result["archived_media"] == 1
    assert result["visual_completeness_verified"] is False


def test_visible_337_by_450_img_missing_from_native_is_only_candidate_gap():
    report = forensic([(12, 12, 32, 32), (337, 450, 337, 450)])
    result = capture_comparison(report, summary(images=0), 1)
    assert result["status"] == "possible-large-image-capture-gap"
    assert result["possible_missing_large_images"] == 1
    assert result["private_images_displayed_large"] == 1
    assert result["private_images_small"] == 1
    assert result["byte_identity_status"] == "unavailable"
    assert result["visual_completeness_verified"] is False


def test_dimensions_are_displayed_not_intrinsic_size():
    report = forensic([(12, 12, 4096, 4096), (40, 40, 20, 20)])
    result = capture_comparison(report, summary(images=1), 1)
    assert result["private_images_displayed_large"] == 1
    assert result["private_images_small"] == 1
    assert result["possible_missing_large_images"] == 0


def test_historical_complete_is_not_equivalent_to_pixel_perfect():
    result = capture_comparison(
        forensic([(400, 450, 400, 450)]),
        summary(images=1), 1, {"status": "verified-sha256", "indices": [0]},
    )
    assert result["status"] == "size-qualified-image-counts-agree"
    assert result["byte_identity_status"] == "verified-sha256"
    assert result["visual_completeness_verified"] is False


def test_untrusted_or_partial_private_capture_never_claims_gap():
    for report in (
        None,
        forensic([(300, 300, 300, 300)], complete=False),
        forensic([(300, 300, 300, 300)], scope="candidate_only"),
        {**forensic([(300, 300, 300, 300)]), "root": None},
    ):
        result = capture_comparison(report, summary(), 1)
        assert result["comparable"] is False
        assert result["possible_missing_large_images"] is None
        assert result["visual_completeness_verified"] is False


def test_unavailable_native_snapshot_does_not_claim_loss():
    result = capture_comparison(forensic([(337, 450, 337, 450)]), None, 1)
    assert result["status"] == "native-layout-unavailable"
    assert result["comparable"] is False


def test_unknown_geometry_does_not_get_promoted_to_large_image():
    report = forensic([])
    report["root"]["children"].insert(0, {"tag": "img", "layout": {}})
    result = capture_comparison(report, summary(0), 1)
    assert result["status"] == "count-comparison-has-unknown-geometry"
    assert result["private_images_geometry_unknown"] == 1
    assert result["possible_missing_large_images"] == 0


def test_private_extra_small_icon_cannot_hide_true_large_gap():
    report = forensic([(24, 24, 24, 24), (400, 300, 400, 300)])
    result = capture_comparison(report, summary(0), 1)
    assert result["status"] == "possible-large-image-capture-gap"
    assert result["possible_missing_large_images"] == 1


def test_cycle_or_more_than_30000_private_nodes_fails_closed():
    report = forensic([])
    child = {"tag": "span"}
    child["children"] = [child]
    report["root"]["children"].append(child)
    result = capture_comparison(report, summary(0), 0)
    assert result["status"] == "private-tree-over-cap"
    assert result["comparable"] is False


def test_request_admin_and_final_diagnostics_use_same_owner_scoped_audit():
    server = (ROOT / "app/main.py").read_text("utf8")
    renderer = (ROOT / "app/request_history_v94_patch.py").read_text("utf8")
    assert '"capture_comparison": comparison' in server
    assert 'native_dom_evidence.get(request_id, str(row["api_key_id"]))' in server
    assert 'state.diagnostics["native_capture_comparison"] = capture_comparison(' in server
    assert 'native_dom_evidence.get(state.request_id, principal.key_id)' in server
    assert "const comparison=data?.capture_comparison||{}" in renderer
    assert "only-small-decorative-private-images" in renderer
    assert "possible-large-image-capture-gap" in renderer
    # Neither source changes terminal authority or tries ordinal image binding.
    assert 'image_asset_candidates' in server
    assert "candidate.indices.map(i=>i===null?null:assets[i]||null)" in renderer


def test_audit_contains_only_counts_and_status_not_original_content():
    report = forensic([(50, 50, 50, 50)])
    report["root"]["children"].append({"tag": "p", "text": "PRIVATE PROMPT"})
    result = capture_comparison(report, summary(1), 1)
    assert "PRIVATE PROMPT" not in str(result)
    assert "https://" not in str(result)
