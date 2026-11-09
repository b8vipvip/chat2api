"""Exact original-byte identity is authoritative over ordinal archive counts.

The request owns every DOM slot; its media archive may deduplicate an
identical original. Full coverage is NOT equivalent to number of files.
These are pure native-sidecar tests, never Broker terminal mutations.
"""

from app.native_content_v1 import (
    native_image_identity_candidates, native_layout_summary,
    validate_native_snapshot,
)
from app.native_snapshot_finality_v282 import native_finality_decision

A = "a" * 64
B = "b" * 64


def _decision(digests, archive):
    """Drive finality with the same validated server-side evidence as main.py."""
    snapshot = validate_native_snapshot({
        "version": 1, "revision": 7,
        "root": {"tag": "div", "children": [
            {"tag": "img", "media_index": i, "source_sha256": digest}
            for i, digest in enumerate(digests)
        ]},
    })
    layout = native_layout_summary(snapshot, len(archive))
    identity = native_image_identity_candidates(snapshot, archive)
    diagnostics = {
        "native_snapshot_terminal_complete": True,
        "native_capture_revision": 7,
        "native_snapshot_received_revision": 7,
        "native_final_capture_committed": True,
        "native_chunk_complete": True,
        "native_snapshot_persisted": True,
        "native_reply_scope_verified": True,
        "native_layout_summary": layout,
        "native_image_identity_status": identity["status"],
        "native_identity_matched_count": (
            len(identity["indices"]) if identity["status"] == "verified-sha256"
            else identity.get("matched_count", 0)
        ),
        "native_identity_dom_image_count": layout["images"],
        "native_identity_archived_count": len(archive),
    }
    row = {"status": "completed", "diagnostics": diagnostics}
    return native_finality_decision(row, snapshot), row, snapshot, layout, identity


def test_one_original_sha_reused_twice_is_fully_mapped():
    decision, row, _snapshot, layout, identity = _decision(
        [A, A], [{"sha256": A}],
    )
    assert layout["images"] == 2
    assert layout["archived_assets"] == 1
    assert layout["media_mapping_complete"] is False  # Ordinals alone cannot prove it
    assert identity == {"status": "verified-sha256", "indices": [0, 0]}
    assert decision["status"] == "verified-final"
    assert decision["visually_verified"] is False
    assert row["status"] == "completed"  # No second terminal authority


def test_distinct_images_still_require_each_distinct_byte_hash():
    decision, _row, _snapshot, _layout, identity = _decision(
        [A, B], [{"sha256": A}],
    )
    assert identity["status"] == "verified-sha256-partial"
    assert decision["status"] == "rejected-final"


def test_extra_unrepresented_original_must_never_be_claimed_complete():
    decision, _row, _snapshot, _layout, identity = _decision(
        [A, A], [{"sha256": A}, {"sha256": B}],
    )
    assert identity["status"] == "verified-sha256-partial"
    assert decision["status"] == "rejected-final"


def test_same_request_source_and_archive_counts_are_cross_checked():
    verdict, row, snapshot, _layout, _identity = _decision(
        [A, A], [{"sha256": A}],
    )
    assert verdict["status"] == "verified-final"
    row["diagnostics"]["native_identity_dom_image_count"] = 1
    assert native_finality_decision(row, snapshot)["reason"] == (
        "unmapped-archived-media"
    )
    row["diagnostics"]["native_identity_dom_image_count"] = 2
    row["diagnostics"]["native_identity_archived_count"] = 2
    assert native_finality_decision(row, snapshot)["reason"] == (
        "unmapped-archived-media"
    )


def test_unverified_dimensions_cannot_override_ordinal_failure():
    verdict, row, snapshot, _layout, _identity = _decision(
        [A, A], [{"sha256": A}],
    )
    assert verdict["status"] == "verified-final"
    row["diagnostics"]["native_image_identity_status"] = (
        "unique-dimensions-unverified"
    )
    assert native_finality_decision(row, snapshot)["reason"] == (
        "unmapped-archived-media"
    )
