from pathlib import Path

from app.broker import _terminal_text


ROOT = Path(__file__).resolve().parents[1]


def test_terminal_reconciliation_prefers_longer_accumulated_prefix_extension():
    full = '<<<CHAT2API_RESPONSES_TOOL_V109>>>\n{"kind":"final","text":"FDEX_CODEX_SMOKE_cdac2df519f84e20_WIRE"}\n<<<END_CHAT2API_RESPONSES_TOOL_V109>>>'
    partial = '<<<CHAT2API_RESPONSES_TOOL_V109>>>'
    assert _terminal_text(full, partial) == (full, "accumulated-prefix-extension")


def test_terminal_reconciliation_never_replaces_with_unrelated_stream_text():
    assert _terminal_text("different network observation", "controller final") == ("controller final", "terminal")


def test_worker_manifest_has_one_terminal_completion_owner():
    manifest = (ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8")
    assert '"content_request_v6.js"' in manifest
    assert '"content_network_stream_recovery_v55.js"' in manifest
    assert '"content_response_stream_recovery_v49.js"' not in manifest
    assert '"content_response_stream_recovery_v69.js"' not in manifest
    assert '"content_terminal_integrity_v89.js"' not in manifest


def test_network_observer_is_evidence_only():
    source = (ROOT / "chrome_extension" / "content_network_stream_recovery_v55.js").read_text(encoding="utf-8")
    assert 'network-stream-evidence-v56' in source
    assert 'network_terminal_authority: "request-v6"' in source
    assert 'type: "chat.completed"' not in source
    assert 'active.cancelled = true' not in source


def test_prompt_overlay_does_not_arbitrate_terminal_events():
    source = (ROOT / "chrome_extension" / "content_request_terminal_prompt_v88.js").read_text(encoding="utf-8")
    assert 'role: "prompt-insertion-only"' in source
    assert 'chrome.runtime.sendMessage = function' not in source
    assert 'network-success-is-terminal' not in source
