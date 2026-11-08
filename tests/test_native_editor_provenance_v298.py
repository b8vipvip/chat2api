"""Regression: request-v6 v297 full-editor evidence must reach the request ZIP."""
from app.broker import _terminal_provenance


def test_editor_state_survives_broker_terminal_allowlist_with_safe_bounds():
    data = {"type": "chat.completed", "request_id": "req_"+"b"*32,
            "diagnostics": {
                "native_code_editor_state_candidates": 2,
                "native_code_editor_state_resolved": 1,
                "native_code_editor_captured": 1,
                "native_code_editor_state_failure": "editor-main-world-unavailable",
                "untrusted_raw_page_text": "do not save private document",
            }}
    proof = _terminal_provenance(data)
    assert proof["native_code_editor_state_candidates"] == 2
    assert proof["native_code_editor_state_resolved"] == 1
    assert proof["native_code_editor_captured"] == 1
    assert proof["native_code_editor_state_failure"] == "editor-main-world-unavailable"
    assert "untrusted_raw_page_text" not in proof
    assert "text" not in proof

    invalid = {"diagnostics": {
        "native_code_editor_state_candidates": 200,
        "native_code_editor_state_resolved": -5,
        "native_code_editor_captured": True,
        "native_code_editor_state_failure": "x"*500,
    }}
    safe = _terminal_provenance(invalid)
    assert "native_code_editor_state_candidates" not in safe
    assert "native_code_editor_state_resolved" not in safe
    assert "native_code_editor_captured" not in safe
    assert len(safe["native_code_editor_state_failure"]) <= 100
