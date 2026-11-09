"""Do not certify 1:1 native layouts when the scoped Worker dropped charts.

The sole native-sidecar finality authority consumes the existing same-request
Worker counters. Its decision must never mutate a completed Broker request.
"""
from app.native_snapshot_finality_v282 import native_finality_decision


PROOF = {
    "native_snapshot_terminal_complete": True,
    "native_capture_revision": 11,
    "native_snapshot_received_revision": 11,
    "native_final_capture_committed": True,
    "native_chunk_complete": True,
    "native_snapshot_persisted": True,
    "native_reply_scope_verified": True,
}
TEXT_SNAPSHOT = {"version": 1, "revision": 11, "root": {
    "tag": "div", "children": [{"tag": "p", "text": "项目说明"}],
}}
SVG_SNAPSHOT = {"version": 1, "revision": 11, "root": {
    "tag": "div", "children": [
        {"tag": "p", "text": "可视化流程"},
        {"tag": "svg", "children": [{"tag": "text", "text": "A"}]},
    ],
}}


def _decide(snapshot=TEXT_SNAPSHOT, **extra):
    row = {"status": "completed", "diagnostics": {**PROOF, **extra}}
    result = native_finality_decision(row, snapshot)
    assert row["status"] == "completed"
    assert result["scope"] == "native-sidecar-only"
    assert result["visually_verified"] is False
    return result


def test_sanitizer_skipped_a_canvas_so_ack_does_not_prove_full_reply():
    decision = _decide(
        native_canvas_omitted=1,
        native_graphics_unsupported=1,
        native_svg_charts_captured=0,
    )
    assert decision["status"] == "rejected-final"
    assert decision["reason"] == "canvas-source-omitted"


def test_unsupported_original_graphic_does_not_pass_even_without_canvas():
    decision = _decide(
        native_canvas_omitted=0,
        native_graphics_unsupported=2,
        native_svg_charts_captured=0,
    )
    assert decision["reason"] == "native-graphics-unsupported"


def test_svg_observed_by_worker_but_not_retained_in_native_tree_rejects():
    assert _decide(native_canvas_omitted=0, native_graphics_unsupported=0,
                   native_svg_charts_captured=1)["reason"] == "svg-chart-capture-gap"


def test_svg_root_persisted_in_same_reply_does_not_falsely_reject():
    assert _decide(SVG_SNAPSHOT,
                   native_canvas_omitted=0,
                   native_graphics_unsupported=0,
                   native_svg_charts_captured=1)["status"] == "verified-final"
    assert _decide(SVG_SNAPSHOT,
                   native_canvas_omitted=0,
                   native_graphics_unsupported=0,
                   native_svg_charts_captured=0)["status"] == "verified-final"


def test_clean_legacy_or_zero_graphics_diagnostics_stay_compatible():
    assert _decide()["status"] == "verified-final"
    assert _decide(native_canvas_omitted=0,
                   native_graphics_unsupported=0,
                   native_svg_charts_captured=0)["status"] == "verified-final"


def test_invalid_or_untrusted_graphic_counters_cannot_fake_success():
    for key in ("native_canvas_omitted", "native_graphics_unsupported",
                "native_svg_charts_captured"):
        for invalid in (True, False, -1, "0", "1", 30001):
            assert _decide(**{key: invalid})["reason"] == (
                "invalid-graphics-coverage-counter"
            )
