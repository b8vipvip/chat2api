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


def test_v299_verified_code_splice_is_request_v6_only_and_observable():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    controller=(root/"chrome_extension/content_request_v6.js").read_text("utf-8")
    rich=(root/"chrome_extension/content_rich_response_v69.js").read_text("utf-8")
    assert 'rich.mergeVerifiedEditorMarkdown(event.text,' in controller
    assert 'active.nativeEditorDocs?.size ===' in controller
    assert 'native_canonical_code_integrated:' in controller
    assert 'native_canonical_code_reason:' in controller
    assert "spliceVerifiedCodeIntoCanonical" in rich
    assert "code-position-unverified" in rich
    evidence=_terminal_provenance({"diagnostics": {
        "native_canonical_code_integrated": 1,
        "native_canonical_code_reason": "verified-editor-anchor-insert",
    }})
    assert evidence["native_canonical_code_integrated"] == 1
    assert evidence["native_canonical_code_reason"] == "verified-editor-anchor-insert"
    invalid=_terminal_provenance({"diagnostics": {
        "native_canonical_code_integrated": 52,
        "native_canonical_code_reason": "x"*500,
    }})
    assert "native_canonical_code_integrated" not in invalid
    assert len(invalid["native_canonical_code_reason"]) <= 100
