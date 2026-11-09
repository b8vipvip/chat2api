"""Truthful, single-owner native finality and Broker status regressions.

Even an ACKed final DOM cannot prove archived image bytes that do not exist.
The read-only Broker projection must not rewrite cancellation as error.
"""
from types import SimpleNamespace

from app.native_snapshot_finality_v282 import native_finality_decision, native_terminal_view

ACK = {
    "native_snapshot_terminal_complete": True,
    "native_capture_revision": 3,
    "native_snapshot_received_revision": 3,
    "native_chunk_complete": True,
    "native_final_capture_committed": True,
    "native_snapshot_persisted": True,
    "native_reply_scope_verified": True,
}
IMAGE_SNAPSHOT = {
    "version": 1,
    "revision": 3,
    "root": {"tag": "div", "children": [
        {"tag": "p", "text": "图表与正文"},
        {"tag": "img", "media_index": 0},
    ]},
}
TEXT_SNAPSHOT = {"version": 1, "revision": 3,
                 "root": {"tag": "p", "text": "正文"}}


def test_native_image_without_any_original_archive_cannot_pass_finality():
    row = {"status": "completed", "diagnostics": {**ACK,
        "native_layout_summary": {
            "revision": 3, "images": 1, "archived_assets": 0,
            "media_mapping_complete": False,
        },
    }}
    assert native_finality_decision(row, IMAGE_SNAPSHOT)["reason"] == (
        "native-images-without-archived-originals"
    )
    assert row["status"] == "completed"  # Native finality never owns this fact.


def test_empty_text_only_archive_may_verify_when_other_proofs_are_complete():
    row = {"status": "completed", "diagnostics": {**ACK,
        "native_layout_summary": {
            "revision": 3, "images": 0, "archived_assets": 0,
            "media_mapping_complete": True,
        },
    }}
    decision = native_finality_decision(row, TEXT_SNAPSHOT)
    assert decision["status"] == "verified-final"
    assert decision["visually_verified"] is False


def test_layout_revision_must_match_actual_native_snapshot():
    row = {"status": "completed", "diagnostics": {**ACK,
        "native_layout_summary": {
            "revision": 2, "images": 0, "archived_assets": 0,
            "media_mapping_complete": True,
        },
    }}
    assert native_finality_decision(row, TEXT_SNAPSHOT)["reason"] == (
        "revision-conflict-native-layout-summary"
    )
    row["diagnostics"]["native_layout_summary"]["revision"] = "3"
    assert native_finality_decision(row, TEXT_SNAPSHOT)["reason"] == (
        "revision-conflict-native-layout-summary"
    )
    row["diagnostics"]["native_layout_summary"]["revision"] = 3
    assert native_finality_decision(row, TEXT_SNAPSHOT)["status"] == "verified-final"


def test_cancelled_broker_terminal_is_not_mislabeled_error():
    original = {"status": "running", "client_id": "ext_lease_v316",
                "diagnostics": {"native_snapshot_terminal_complete": False}}
    for terminal, expected in (
        ("chat.cancelled", "cancelled"),
        ("chat.error", "error"),
        ("chat.completed", "completed"),
    ):
        broker = SimpleNamespace(
            terminal_event_type=terminal, client_id="ext_lease_v316",
            diagnostics={"some_terminal_observation": True},
        )
        projected = native_terminal_view(original, broker)
        assert projected["status"] == expected
        assert projected["diagnostics"]["some_terminal_observation"] is True
        assert original["status"] == "running"
        if expected != "completed":
            assert native_finality_decision(
                projected, TEXT_SNAPSHOT,
            )["reason"] == "broker-request-not-completed"


def test_broker_terminal_of_other_client_cannot_change_status():
    row = {"status": "running", "client_id": "ext_a",
           "diagnostics": {}}
    another = SimpleNamespace(
        terminal_event_type="chat.cancelled",
        client_id="ext_b", diagnostics={},
    )
    assert native_terminal_view(row, another) is row
