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


def test_v301_bounded_editor_view_probe_evidence_survives_single_terminal_owner():
    observed = _terminal_provenance({"diagnostics": {
        "native_code_editor_state_candidates": 1,
        "native_code_editor_state_resolved": 0,
        "native_code_editor_state_failure": "editor-view-not-found",
        "native_editor_probe_scanned": 261,
        "native_editor_probe_attachments": 0,
        "native_editor_probe_visible_lines": 87,
        "native_editor_probe_retries": 2,
        "native_editor_probe_virtualized": True,
    }})
    assert observed["native_editor_probe_scanned"] == 261
    assert observed["native_editor_probe_attachments"] == 0
    assert observed["native_editor_probe_visible_lines"] == 87
    assert observed["native_editor_probe_retries"] == 2
    assert observed["native_editor_probe_virtualized"] is True
    invalid = _terminal_provenance({"diagnostics": {
        "native_editor_probe_scanned": 99999999,
        "native_editor_probe_attachments": -1,
        "native_editor_probe_visible_lines": "private source",
        "native_editor_probe_retries": 17,
        "native_editor_probe_virtualized": "true",
        "native_canonical_code_integrated": 9,
    }})
    for key in ("native_editor_probe_scanned", "native_editor_probe_attachments",
                "native_editor_probe_visible_lines", "native_editor_probe_retries",
                "native_editor_probe_virtualized", "native_canonical_code_integrated"):
        assert key not in invalid


def test_v301_only_request_owned_mainworld_code_recovers_without_terminal_competition():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    main=(root/"chrome_extension/background_native_editor_state_v297.js").read_text()
    observer=(root/"chrome_extension/content_native_content_v1.js").read_text()
    terminal=(root/"chrome_extension/content_request_v6.js").read_text()
    assert "MAX_EDITOR_SCAN=4096" in main
    assert "editor-state-ambiguous" in main
    assert "element?.shadowRoot" in main
    assert 'reply?.reason!=="editor-view-not-found"' in observer
    assert "editor-owner-scope-disconnected" in observer
    assert "native_editor_probe_scanned:" in terminal
    assert "native_editor_probe_virtualized:" in terminal
    # Observer only returns evidence; request-v6 keeps the sole terminal owner.
    assert "chat.completed" not in main
