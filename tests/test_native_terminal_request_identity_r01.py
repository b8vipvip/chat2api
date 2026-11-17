"""R-01: native finality must bind the same Broker request, not just Worker."""
from __future__ import annotations

from types import SimpleNamespace

from app.native_snapshot_finality_v282 import native_terminal_view


def test_same_worker_different_request_terminal_cannot_verify_old_native_reply() -> None:
    row = {
        "request_id": "req_r01_old", "client_id": "ext_shared",
        "status": "running",
        "diagnostics": {"native_final_capture_committed": False},
    }
    newer = SimpleNamespace(
        request_id="req_r01_new", client_id="ext_shared",
        terminal_event_type="chat.completed",
        diagnostics={"native_final_capture_committed": True},
    )
    assert native_terminal_view(row, newer) is row
    assert row["status"] == "running"
    assert row["diagnostics"]["native_final_capture_committed"] is False


def test_missing_request_identity_cannot_look_like_matching_worker_proof() -> None:
    row = {
        "request_id": "req_r01", "client_id": "ext_shared",
        "status": "running", "diagnostics": {},
    }
    missing_broker_id = SimpleNamespace(
        client_id="ext_shared", terminal_event_type="chat.completed", diagnostics={},
    )
    assert native_terminal_view(row, missing_broker_id) is row

    broker = SimpleNamespace(
        request_id="req_r01", client_id="ext_shared",
        terminal_event_type="chat.completed", diagnostics={},
    )
    assert native_terminal_view({**row, "request_id": ""}, broker)["status"] == "running"
    assert native_terminal_view({**row, "request_id": None}, broker)["status"] == "running"


def test_matching_request_and_worker_are_both_required_for_native_terminal_projection() -> None:
    row = {
        "request_id": "req_r01", "client_id": "ext_shared",
        "status": "running", "diagnostics": {"previous": "safe"},
    }
    broker = SimpleNamespace(
        request_id="req_r01", client_id="ext_shared",
        terminal_event_type="chat.completed", diagnostics={"native_capture_revision": 3},
    )
    merged = native_terminal_view(row, broker)
    assert merged is not row
    assert merged["status"] == "completed"
    assert merged["diagnostics"] == {
        "previous": "safe", "native_capture_revision": 3,
    }
    assert row["status"] == "running"
    assert row["diagnostics"] == {"previous": "safe"}
