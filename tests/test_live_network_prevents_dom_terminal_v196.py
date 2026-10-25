"""A live ChatGPT response cannot be committed on a DOM pause."""
from pathlib import Path
SOURCE=(Path(__file__).resolve().parents[1]/"chrome_extension/content_request_v6.js").read_text()
def test_network_pending_disables_rendered_dom_completion():
    marker='if (networkTerminalAt && networkTerminalText && !captureMedia'
    assert marker in SOURCE
    fragment=SOURCE[SOURCE.index(marker):SOURCE.index("if (active.networkStreamEndedWithoutTerminalAt",SOURCE.index(marker))]
    assert 'type: "chat.completed"' in fragment
    assert "same-request-rendered-dom-baseline-v191" in fragment
    assert "network_terminal_evidence_source" in fragment
    assert "networkTerminalAt && networkTerminalText" in fragment
def test_dom_only_legacy_is_separate():
    assert "if (responseStarted && lastText && !generating && !captureMedia && !active.networkStreamSeen" in SOURCE
