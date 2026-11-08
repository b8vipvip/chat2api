"""Governance: 1 terminal writer, 1 native finality decision, optional layers observational."""
from pathlib import Path
import re

from app.native_snapshot_finality_v282 import (
    native_finality_decision, native_snapshot_finality,
)

ROOT = Path(__file__).resolve().parents[1]


def test_native_finality_compatibility_is_only_a_projection():
    source = (ROOT / "app/native_snapshot_finality_v282.py").read_text("utf-8")
    assert 'def native_finality_decision(' in source
    assert 'return native_finality_decision(row, snapshot)["status"]' in source
    exported = (ROOT / "app/v8_patch.py").read_text("utf-8")
    assert 'verdict = native_finality_decision(row, native_layout)' in exported
    assert '"finality_reason": verdict["reason"]' in exported
    assert '"finality_authority": verdict["authority"]' in exported
    assert '"visual_1to1_verified": verdict["visually_verified"]' in exported


def test_rich_observers_cannot_override_broker_or_worker_terminal():
    for path in (
        "chrome_extension/content_native_content_v1.js",
        "chrome_extension/content_native_dom_evidence_v1.js",
        "chrome_extension/background_native_editor_state_v297.js",
    ):
        src = (ROOT / path).read_text("utf-8")
        # Mentioning a terminal in comments is allowed. Emitting a terminal
        # envelope from an optional native observer is forbidden.
        assert not re.search(r"""type\\s*:\\s*['\\"]chat\\.(?:completed|error|cancelled)['\\"]""", src), (
            f"native observer emits request terminal: {path}"
        )
    for path in (
        "app/native_capture_audit_v281.py",
        "app/native_snapshot_finality_v282.py",
        "app/terminal_archive_singleflight_v277.py",
        "app/mixed_media_archive_v280.py",
    ):
        src = (ROOT / path).read_text("utf-8")
        assert "await broker.publish(" not in src, f"observer publishes terminal: {path}"
        assert "await telemetry.upsert(" not in src, f"observer overwrites request terminal: {path}"
    broker = (ROOT / "app/broker.py").read_text("utf-8")
    assert 'if state.terminal_event_type is not None:' in broker
    assert 'return False' in broker


def test_actual_data_does_not_satisfy_missing_native_evidence():
    row = {"status": "completed", "diagnostics": {
        "native_capture_failure": "structural-code-editor-virtualized",
        "native_final_capture_committed": False,
        "native_snapshot_terminal_complete": False,
        "native_reply_scope_verified": True,
    }}
    result = native_finality_decision(row, None)
    assert result["status"] == "rejected-final"
    assert result["reason"] == "final-native-snapshot-missing"
    assert result["authority"] == "server-native-finality-v298"
    assert native_snapshot_finality(row, None) == result["status"]
    assert row["status"] == "completed"


def test_one_finality_owner_never_implies_pixel_verification():
    row = {"status": "completed", "diagnostics": {
        "native_capture_revision": 3,
        "native_snapshot_terminal_complete": True,
        "native_final_capture_committed": True,
        "native_chunk_complete": True,
        "native_snapshot_persisted": True,
        "native_reply_scope_verified": True,
    }}
    snap = {"version": 1, "revision": 3, "root": {
        "tag": "div", "children": [{"tag": "p", "text": "Complete safe content"}],
    }}
    decision = native_finality_decision(row, snap)
    assert decision["status"] == "verified-final"
    assert decision["visually_verified"] is False
    assert row["status"] == "completed"
